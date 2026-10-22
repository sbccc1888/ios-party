#!/usr/bin/env python3
"""Execute the zero-tail stub and verify byte-exact volume restoration before UEFI."""

import argparse
import json
from pathlib import Path

from build_chiron_entry_probe import sha256
from build_chiron_zero_tail_boot import PHYSICAL_BYTES, LOGICAL_BYTES, require_zero_tail
from emulate_chiron_entry_probe import make_fdt, write_png


def run_case(firmware, volume, metadata, case):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import (
        UC_ARM64_REG_PC, UC_ARM64_REG_PSTATE, UC_ARM64_REG_SP,
        UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
        UC_ARM64_REG_X28,
    )

    image_base = case.get("image_base", 0x40080000)
    fdt_base = case.get("fdt_base", 0x43200000)
    width = case.get("width", 8)
    start = case.get("initrd_start", 0x43400000)
    end = case.get("initrd_end", 0x44c00000)
    fdt = make_fdt(start, end, width, omit=case.get("omit", False))
    physical = bytearray(volume[:PHYSICAL_BYTES])
    if case.get("bad_fat"):
        physical[510] = 0
    before = bytes(physical) + b"\xa5" * (LOGICAL_BYTES - PHYSICAL_BYTES)
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    machine.mem_map(image_base, len(firmware))
    machine.mem_write(image_base, firmware)
    machine.mem_map(fdt_base, 4096)
    machine.mem_write(fdt_base, fdt)
    machine.mem_map(0x43400000, LOGICAL_BYTES)
    machine.mem_write(0x43400000, before)
    machine.mem_map(0x9d400000, 0xa00000)
    level = case.get("el", 1)
    machine.reg_write(UC_ARM64_REG_PSTATE, 0x3c1 + level * 4)
    machine.cpr_write(3, 0, 1, 0, 0, case.get("cache", 0))
    machine.cpr_write(3, 4, 1, 0, 0, case.get("cache", 0))
    original_sp = 0x70001000
    machine.reg_write(UC_ARM64_REG_SP, original_sp)
    machine.reg_write(UC_ARM64_REG_X0, fdt_base)
    machine.reg_write(UC_ARM64_REG_X1, 0)
    machine.reg_write(UC_ARM64_REG_X2, 0x1111222233334444)
    machine.reg_write(UC_ARM64_REG_X3, 0x5555666677778888)
    failure = case.get("failure")
    stop = image_base + (metadata["failure_halt_offset"] if failure else metadata["uefi_entry_offset"])
    machine.emu_start(image_base, stop, count=20_000_000)
    if machine.reg_read(UC_ARM64_REG_PC) != stop:
        raise ValueError(f"{case['name']}: did not reach expected handoff/halt")
    after_volume = bytes(machine.mem_read(0x43400000, LOGICAL_BYTES))
    after_fdt = bytes(machine.mem_read(fdt_base, len(fdt)))
    body = bytes(machine.mem_read(image_base + 0x8000, len(firmware) - 0x8000))
    if body != firmware[0x8000:]:
        raise ValueError(f"{case['name']}: firmware body changed")
    result = {"name": case["name"], "exception_level": level, "firmware_body_unchanged": True}
    if failure:
        actual = machine.reg_read(UC_ARM64_REG_X28)
        if actual != failure or after_volume != before or after_fdt != fdt:
            raise ValueError(f"{case['name']}: unsafe rejection or wrong FAIL code {actual:#x}")
        result.update({
            "failure_halt_observed": True, "failure_code": hex(actual),
            "ram_and_fdt_unchanged": True,
        })
    else:
        expected_fdt = make_fdt(start, 0x46400000, width)
        regs = [machine.reg_read(reg) for reg in (
            UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
        expected_regs = [fdt_base, image_base, 0x1111222233334444, 0x5555666677778888]
        if (
            after_volume != volume or after_fdt != expected_fdt or regs != expected_regs
            or machine.reg_read(UC_ARM64_REG_SP) != original_sp
        ):
            raise ValueError(f"{case['name']}: restoration, FDT, or handoff register mismatch")
        result.update({
            "original_prepi_entry_reached": True,
            "logical_volume_byte_exact": True, "logical_volume_sha256": sha256(after_volume),
            "fdt_end_updated_to": "0x46400000",
            "boot_registers_and_sp_restored": True,
        })
    frame = bytes(machine.mem_read(0x9d400000, 4320 * 272))
    if sum(value != 0 for value in frame) < 1000:
        raise ValueError(f"{case['name']}: status display was blank")
    result["status_display_nonblank"] = True
    return result, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    metadata = json.loads((args.boot_directory / "zero-tail-report.json").read_text())
    firmware = (args.boot_directory / "firmware.fd").read_bytes()
    volume = (args.boot_directory / "logical-volume.fat").read_bytes()
    if sha256(firmware) != metadata["firmware_sha256"] or sha256(volume) != metadata["logical_volume_sha256"]:
        parser.error("inputs do not match the build report")
    require_zero_tail(volume)
    cases = [
        {"name": "EL1, 64-bit FDT fields"},
        {"name": "EL1, 32-bit FDT fields", "width": 4},
        {"name": "EL2, 64-bit FDT fields", "el": 2},
        {"name": "enabled data cache rejected", "cache": 4, "failure": 0x10},
        {"name": "different Image address rejected", "image_base": 0x40280000, "failure": 0x11},
        {"name": "different FDT address rejected", "fdt_base": 0x43000000, "failure": 0x11},
        {"name": "different initrd start rejected", "initrd_start": 0x43500000, "failure": 0x12},
        {"name": "different physical length rejected", "initrd_end": 0x44b00000, "failure": 0x12},
        {"name": "invalid FAT header rejected", "bad_fat": True, "failure": 0x13},
        {"name": "missing initrd rejected", "omit": True, "failure": 3},
    ]
    results = []
    preview = None
    for case in cases:
        result, frame = run_case(firmware, volume, metadata, case)
        results.append(result)
        if preview is None:
            preview = frame
    args.output.mkdir(parents=True, exist_ok=False)
    write_png(args.output / "success-preview.png", preview)
    report = {
        "all_cases_passed": True, "firmware_sha256": sha256(firmware),
        "logical_volume_sha256": sha256(volume), "cases": results,
        "limits": [
            "Synthetic RAM/FDT and architectural control registers; not an emulation of the phone bootloader.",
            "The successful cases stop before original PrePi executes; UEFI and XNU are not tested here.",
            "Historical RAM/reserved measurements do not replace a live allocation map.",
        ],
    }
    (args.output / "emulation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
