#!/usr/bin/env python3
"""Validate the actual entry probe instructions and render an emulated display preview."""

import argparse
import json
import struct
import zlib
from pathlib import Path

from build_chiron_entry_probe import sha256


def make_fdt(start, end, width=8, chosen=True, omit=False):
    strings = b"linux,initrd-start\0linux,initrd-end\0"
    end_name = strings.index(b"linux,initrd-end")

    def node(name):
        value = name.encode() + b"\0"
        return struct.pack(">I", 1) + value + bytes(-len(value) % 4)

    def prop(offset, value):
        return struct.pack(">III", 3, len(value), offset) + value + bytes(-len(value) % 4)

    tree = node("") + node("chosen" if chosen else "other")
    if not omit:
        tree += prop(0, start.to_bytes(width, "big"))
        tree += prop(end_name, end.to_bytes(width, "big"))
    tree += struct.pack(">III", 2, 2, 9)
    off_struct = 56
    off_strings = off_struct + len(tree)
    header = struct.pack(">10I", 0xd00dfeed, off_strings + len(strings),
                         off_struct, off_strings, 40, 17, 16, 0, len(strings), len(tree))
    return header + bytes(16) + tree + strings


def run_case(firmware, halt_offset, case):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import (
        UC_ARM64_REG_PC, UC_ARM64_REG_PSTATE, UC_ARM64_REG_X0,
        UC_ARM64_REG_X21, UC_ARM64_REG_X22, UC_ARM64_REG_X23, UC_ARM64_REG_X24,
    )

    image_base = case.get("image_base", 0x80080000)
    fdt_base = case.get("fdt_base", 0x43000000)
    fdt = case["fdt"]
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    machine.mem_map(image_base, len(firmware))
    machine.mem_write(image_base, firmware)
    machine.mem_map(fdt_base & ~4095, (len(fdt) + 8191) & ~4095)
    machine.mem_write(fdt_base, fdt)
    machine.mem_map(0x9d400000, 0xa00000)
    machine.reg_write(UC_ARM64_REG_PSTATE, 0x3c5)
    machine.reg_write(UC_ARM64_REG_X0, fdt_base)
    halt = image_base + halt_offset
    machine.emu_start(image_base, halt, count=10_000_000)
    if machine.reg_read(UC_ARM64_REG_PC) != halt:
        raise ValueError(f"{case['name']}: probe did not reach its diagnostic halt")
    actual = {
        "state": machine.reg_read(UC_ARM64_REG_X21),
        "initrd_start": machine.reg_read(UC_ARM64_REG_X22),
        "initrd_end": machine.reg_read(UC_ARM64_REG_X23),
        "overlap": machine.reg_read(UC_ARM64_REG_X24),
    }
    for key, expected in case["expected"].items():
        if actual[key] != expected:
            raise ValueError(f"{case['name']}: {key} is {actual[key]:#x}, expected {expected:#x}")
    frame = bytes(machine.mem_read(0x9d400000, 4320 * 272))
    candidates = ((0x9d400000, 4320, 0), (0x9d400000, 4352, 350),
                  (0x9d600000, 4320, 850), (0x9d600000, 4352, 1200))
    for base, stride, y in candidates:
        display = bytes(machine.mem_read(base + stride * y, stride * 272))
        if sum(value != 0 for value in display) < 1000:
            raise ValueError(f"{case['name']}: a display candidate was blank")
    return {
        "name": case["name"], "image_base": hex(image_base), "fdt_base": hex(fdt_base),
        **{key: hex(value) for key, value in actual.items()},
        "diagnostic_halt_observed": True, "all_display_candidates_nonblank": True,
    }, frame


def write_png(path, frame, width=512, height=272):
    rows = []
    for y in range(height):
        row = bytearray(b"\0")
        for x in range(width):
            offset = y * 4320 + x * 4
            row.extend((frame[offset + 2], frame[offset + 1], frame[offset]))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    firmware = (args.probe_directory / "diagnostic-firmware.fd").read_bytes()
    metadata = json.loads((args.probe_directory / "probe-report.json").read_text())
    if sha256(firmware) != metadata["diagnostic_firmware_sha256"]:
        parser.error("diagnostic firmware does not match its build report")
    valid = make_fdt(0x43400000, 0x46400000)
    bad_magic = bytearray(valid)
    bad_magic[0] = 0
    bad_structure = bytearray(valid)
    struct.pack_into(">I", bad_structure, 56, 0xdeadbeef)
    cases = [
        {"name": "64-bit fields", "fdt": valid,
         "expected": {"state": 0, "initrd_start": 0x43400000, "initrd_end": 0x46400000, "overlap": 0}},
        {"name": "32-bit fields and initrd overlap", "fdt": make_fdt(0x81000000, 0x84000000, 4),
         "fdt_base": 0x82300000,
         "expected": {"state": 0, "initrd_start": 0x81000000, "initrd_end": 0x84000000, "overlap": 10}},
        {"name": "Image overlaps fixed store", "fdt": valid, "image_base": 0x83200000,
         "expected": {"state": 0, "overlap": 1}},
        {"name": "high initrd addresses", "fdt": make_fdt(0x100000000, 0x103000000),
         "expected": {"state": 0, "initrd_start": 0x100000000, "initrd_end": 0x103000000}},
        {"name": "missing initrd", "fdt": make_fdt(0, 0, omit=True), "expected": {"state": 3}},
        {"name": "properties outside chosen ignored", "fdt": make_fdt(1, 2, chosen=False),
         "expected": {"state": 3}},
        {"name": "bad header", "fdt": bytes(bad_magic), "expected": {"state": 1}},
        {"name": "bad structure", "fdt": bytes(bad_structure), "expected": {"state": 2}},
        {"name": "bad address width", "fdt": make_fdt(1, 2, 2), "expected": {"state": 4}},
        {"name": "bad range", "fdt": make_fdt(0x46400000, 0x43400000), "expected": {"state": 5}},
    ]
    results = []
    preview = None
    for case in cases:
        result, frame = run_case(firmware, metadata["program_halt_offset"], case)
        results.append(result)
        if preview is None:
            preview = frame
    args.output.mkdir(parents=True, exist_ok=False)
    write_png(args.output / "display-preview.png", preview)
    report = {
        "diagnostic_firmware_sha256": sha256(firmware),
        "all_cases_passed": True, "cases": results,
        "limits": [
            "Flat emulated RAM and synthetic FDT; not the MIX 2 bootloader.",
            "Rendering to emulated memory does not prove physical scanout on the phone.",
            "The probe deliberately halts and does not execute UEFI or XNU.",
        ],
    }
    (args.output / "emulation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
