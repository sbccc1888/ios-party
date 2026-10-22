#!/usr/bin/env python3
"""Build a diagnostic-only Image that shows live handoff addresses before UEFI."""

import argparse
import gzip
import hashlib
import json
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

from inspect_chiron_boot import inspect
from make_chiron_firmware_control import read_inspected

ROOT = Path(__file__).resolve().parents[1]
LEGACY_FD_SHA256 = "7513f28ad5e8a44f750d775c2175b206a9a569e1691a239d82a5037cc27d09d1"
PREFIX_OFFSET = 0x40


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_aarch64_object(data):
    if data[:7] != b"\x7fELF\x02\x01\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
        raise ValueError("expected a little-endian AArch64 ELF64 object")
    shoff = struct.unpack_from("<Q", data, 40)[0]
    entry_bytes, count, names_index = struct.unpack_from("<3H", data, 58)
    if entry_bytes != 64 or count == 0 or names_index >= count or shoff + count * 64 > len(data):
        raise ValueError("invalid ELF section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * 64) for index in range(count)]

    def section_bytes(index):
        section = sections[index]
        offset, length = section[4:6]
        if offset + length > len(data):
            raise ValueError("section outside ELF object")
        return data[offset:offset + length]

    def name(strings, offset):
        end = strings.find(b"\0", offset)
        if offset >= len(strings) or end < 0:
            raise ValueError("invalid ELF string")
        return strings[offset:end].decode("ascii")

    names = section_bytes(names_index)
    text_index = next(index for index, section in enumerate(sections)
                      if name(names, section[0]) == ".text")
    if any(section[1] in (4, 9) and section[5] for section in sections):
        raise ValueError("probe contains unresolved ELF relocations")
    text = section_bytes(text_index)
    if not text or len(text) + PREFIX_OFFSET > 0x4000:
        raise ValueError("probe must fit below the reserved prefix stack")
    symbols = {}
    for index, section in enumerate(sections):
        if section[1] != 2:
            continue
        if section[9] != 24 or section[6] >= count or section[5] % 24:
            raise ValueError("invalid ELF symbol table")
        strings = section_bytes(section[6])
        table = section_bytes(index)
        for offset in range(0, len(table), 24):
            symbol = struct.unpack_from("<IBBHQQ", table, offset)
            if symbol[3] == text_index:
                symbols[name(strings, symbol[0])] = symbol[4]
    return text, symbols


def read_probe_object(data):
    text, symbols = read_aarch64_object(data)
    if symbols.get("chiron_entry_probe") != 0:
        raise ValueError("probe entry is not at the start of .text")
    halt = symbols.get("chiron_entry_probe_halt", -1)
    if not 0 <= halt < len(text) or halt % 4:
        raise ValueError("probe has no valid halt address")
    return text, halt


def compile_probe(directory, clang="clang", defines=()):
    source = ROOT / "scripts/chiron-entry-probe.S"
    obj = directory / "entry-probe.o"
    subprocess.run([clang, "--target=aarch64-none-elf", *["-D" + value for value in defines],
                    "-c", str(source), "-o", str(obj)], check=True)
    return read_probe_object(obj.read_bytes())


def install_probe(firmware, program):
    if (
        len(firmware) != 0x600000 or firmware[:8] != bytes.fromhex("01000010 ff1f0014")
        or firmware[56:60] != b"ARM\x64"
        or struct.unpack_from("<Q", firmware, 16)[0] != len(firmware)
    ):
        raise ValueError("unexpected Image prefix")
    if firmware[64:0x8000] != b"\xff" * (0x8000 - 64):
        raise ValueError("Image prefix is not unused erased space")
    if not program or len(program) + PREFIX_OFFSET > 0x4000:
        raise ValueError("probe overlaps its reserved stack area")
    result = bytearray(firmware)
    struct.pack_into("<I", result, 4, 0x14000000 | ((PREFIX_OFFSET - 4) // 4))
    result[PREFIX_OFFSET:PREFIX_OFFSET + len(program)] = program
    return bytes(result)


def build(source, output, ramdisk_bytes=None, clang="clang"):
    if output.exists():
        raise ValueError("output directory already exists")
    with tempfile.TemporaryDirectory(prefix="chiron-entry-probe-") as temporary:
        work = Path(temporary)
        data, report = read_inspected(source, work / "input")
        offset = report["payload_offsets"]["kernel"]
        encoded = data[offset:offset + report["header"]["kernel_size"]]
        compressed = report["kernel_compression"] == "gzip"
        if compressed:
            decoder = zlib.decompressobj(31)
            firmware = decoder.decompress(encoded, 0x600001)
            if not decoder.eof:
                raise ValueError("incomplete or oversized input firmware")
            suffix = decoder.unused_data
        else:
            firmware, suffix = encoded[:0x600000], encoded[0x600000:]
        if sha256(firmware) != LEGACY_FD_SHA256:
            raise ValueError("input does not carry the verified original legacy firmware")
        count = report["header"]["ramdisk_size"] if ramdisk_bytes is None else ramdisk_bytes
        if count < 4096 or count % 4096 or count > report["header"]["ramdisk_size"]:
            raise ValueError("RAM size must be a nonempty, aligned source prefix")
        program, halt = compile_probe(work, clang)
        diagnostic = install_probe(firmware, program)
        kernel = (gzip.compress(diagnostic, mtime=0) if compressed else diagnostic) + suffix
        page = report["header"]["page_size"]
        ram_offset = report["payload_offsets"]["ramdisk"]
        ramdisk = data[ram_offset:ram_offset + count]
        header = bytearray(data[:page])
        struct.pack_into("<I", header, 8, len(kernel))
        struct.pack_into("<I", header, 16, count)
        digest = hashlib.sha1()
        for payload in (kernel, ramdisk, b""):
            digest.update(payload)
            digest.update(struct.pack("<I", len(payload)))
        header[576:608] = digest.digest().ljust(32, b"\0")
        result = bytes(header) + kernel + bytes(-len(kernel) % page) + ramdisk
        name = f"chiron-entry-probe-{count // 1048576}m.img"
        image = work / name
        image.write_bytes(result)
        inspect(image, work / "verified")
        verified = json.loads((work / "verified/inspection.json").read_text())
        if (
            not verified["legacy_sha1_matches"]
            or verified["payload_offsets"]["ramdisk"] != page + (len(kernel) + page - 1) // page * page
            or verified["payload_sha256"]["ramdisk"] != sha256(ramdisk)
            or verified["appended_dtbs"][0]["sha256"] != report["appended_dtbs"][0]["sha256"]
            or verified["kernel_image_sha256"] != sha256(diagnostic)
        ):
            raise ValueError("diagnostic Image or transport integrity mismatch")
        output.mkdir(parents=True, exist_ok=False)
        (output / name).write_bytes(result)
        (output / "diagnostic-firmware.fd").write_bytes(diagnostic)
        (output / "probe.bin").write_bytes(program)
        (output / "probe-report.json").write_text(json.dumps({
            "image": name, "sha256": sha256(result), "bytes": len(result),
            "source_image_sha256": report["source_sha256"],
            "kernel_compression": verified["kernel_compression"],
            "input_ramdisk_offset": ram_offset,
            "ramdisk_offset": verified["payload_offsets"]["ramdisk"],
            "diagnostic_firmware_sha256": sha256(diagnostic),
            "program_bytes": len(program), "program_sha256": sha256(program),
            "program_entry_offset": PREFIX_OFFSET,
            "program_halt_offset": PREFIX_OFFSET + halt,
            "ramdisk_bytes": count, "ramdisk_sha256": sha256(ramdisk),
            "legacy_sha1_matches": True, "diagnostic_only": True,
            "enters_uefi": False, "enters_xnu": False, "hardware_executed": False,
            "display_candidates": [
                {"base": "0x9d400000", "stride": 4320, "y_offset": 0},
                {"base": "0x9d400000", "stride": 4352, "y_offset": 350},
                {"base": "0x9d600000", "stride": 4320, "y_offset": 850},
                {"base": "0x9d600000", "stride": 4352, "y_offset": 1200},
            ],
            "state_codes": {
                "0": "valid initrd fields", "1": "invalid FDT pointer/header",
                "2": "invalid FDT structure", "3": "initrd fields absent",
                "4": "unsupported address field size", "5": "invalid initrd range",
            },
            "overlap_bits": {
                "1": "fixed FDT store falls inside the Image",
                "2": "fixed FDT store falls inside initrd",
                "4": "FDT pointer falls inside the Image",
                "8": "FDT pointer falls inside initrd",
            },
            "limits": [
                "The entry deliberately halts after displaying addresses; UEFI and XNU are not attempted.",
                "Display candidates come from prior measurements; pre-UEFI scanout is not verified.",
                "No visible text alone does not prove that the entry was never reached.",
                "These are diagnostic-image addresses; gzip repacking can change the transport placement.",
            ],
        }, indent=2) + "\n")
    return output / name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ramdisk-bytes", type=lambda value: int(value, 0))
    parser.add_argument("--clang", default="clang")
    args = parser.parse_args()
    print(build(args.source, args.output, args.ramdisk_bytes, args.clang))


if __name__ == "__main__":
    main()
