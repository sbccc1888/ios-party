#!/usr/bin/env python3
"""Validate captured FDT/GPT metadata and write a measured chiron profile."""

import argparse
import gzip
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

from inspect_chiron_boot import parse_fdt


def cells(data):
    if len(data) % 4:
        raise ValueError("invalid FDT cells")
    return [item[0] for item in struct.iter_unpack(">I", data)]


def strings(data):
    return data.rstrip(b"\0").decode("ascii").split("\0")


def ranges(data, address_cells, size_cells):
    values = cells(data)
    width = address_cells + size_cells
    if width <= 0 or len(values) % width:
        raise ValueError("invalid FDT range width")
    result = []
    for offset in range(0, len(values), width):
        address, size = 0, 0
        for value in values[offset:offset + address_cells]:
            address = (address << 32) | value
        for value in values[offset + address_cells:offset + width]:
            size = (size << 32) | value
        result.append({"base": address, "size": size, "end_exclusive": address + size})
    return result


def memblock_ranges(text):
    result = []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*\d+:\s*(0x[\da-f]+)\.\.(0x[\da-f]+)\s*", line)
        if not match:
            raise ValueError(f"invalid memblock line: {line!r}")
        start, last = (int(value, 16) for value in match.groups())
        result.append({"base": start, "size": last - start + 1, "end_exclusive": last + 1})
    return result


def overlaps(a, b):
    return a["base"] < b["end_exclusive"] and b["base"] < a["end_exclusive"]


def inspect_gpt(data, sector_bytes):
    if len(data) < sector_bytes * 2:
        raise ValueError("truncated GPT capture")
    header = data[sector_bytes:sector_bytes * 2]
    signature, revision, header_size, crc, reserved, current, backup, first, last, _, table_lba, count, entry_size, table_crc = (
        struct.unpack_from("<8sIIIIQQQQ16sQIII", header)
    )
    if (
        signature != b"EFI PART" or revision != 0x10000
        or not 92 <= header_size <= sector_bytes or reserved != 0
        or current != 1 or not 128 <= entry_size <= 4096
        or not 1 <= count <= 4096
    ):
        raise ValueError("invalid GPT header")
    checked_header = bytearray(header[:header_size])
    struct.pack_into("<I", checked_header, 16, 0)
    if zlib.crc32(checked_header) != crc:
        raise ValueError("GPT header CRC mismatch")
    offset = table_lba * sector_bytes
    table = data[offset:offset + count * entry_size]
    if len(table) != count * entry_size or zlib.crc32(table) != table_crc:
        raise ValueError("GPT entries missing or CRC mismatch")
    partitions = []
    for index in range(count):
        entry = table[index * entry_size:(index + 1) * entry_size]
        if entry[:16] == bytes(16):
            continue
        start, end, _ = struct.unpack_from("<QQQ", entry, 32)
        if not first <= start <= end <= last:
            raise ValueError("partition outside GPT usable region")
        partitions.append({
            "number": index + 1,
            "name": entry[56:128].decode("utf-16-le").split("\0", 1)[0],
            "first_lba": start, "last_lba": end,
            "bytes": (end - start + 1) * sector_bytes,
        })
    ordered = sorted(partitions, key=lambda part: part["first_lba"])
    if any(a["last_lba"] >= b["first_lba"] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("overlapping GPT partitions")
    return {
        "logical_block_bytes": sector_bytes,
        "primary_header_crc_valid": True, "primary_entries_crc_valid": True,
        "backup_header_validated": False,
        "backup_header_lba": backup,
        "entry_count": count, "entry_bytes": entry_size,
        "partitions": partitions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--details", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    capture, details = args.capture, args.details
    nodes, _ = parse_fdt((capture / "runtime-fdt.dtb").read_bytes())
    if cells(nodes["/"]["qcom,msm-id"]) != [0x124, 0x20001]:
        raise ValueError("capture is not the observed MSM8998 v2.1")
    ac = cells(nodes["/"]["#address-cells"])[0]
    sc = cells(nodes["/"]["#size-cells"])[0]
    banks = ranges(nodes["/memory"]["reg"], ac, sc)
    reserved = {}
    parent = nodes["/reserved-memory"]
    for path, props in nodes.items():
        if path.startswith("/reserved-memory/") and "reg" in props:
            reserved[path] = ranges(
                props["reg"], cells(parent["#address-cells"])[0],
                cells(parent["#size-cells"])[0],
            )
    boot_reservations = memblock_ranges((details / "memblock-reserved.txt").read_text())
    candidate = {"base": 0x100000000, "size": 0x40000000, "end_exclusive": 0x140000000}
    in_bank = any(bank["base"] <= candidate["base"] and candidate["end_exclusive"] <= bank["end_exclusive"] for bank in banks)
    in_memblock = any(
        bank["base"] <= candidate["base"] and candidate["end_exclusive"] <= bank["end_exclusive"]
        for bank in memblock_ranges((details / "memblock-memory.txt").read_text())
    )
    no_overlap = not any(overlaps(candidate, item) for item in boot_reservations)
    if not (in_bank and in_memblock and no_overlap):
        raise ValueError("candidate memory window does not pass captured Android map checks")
    lun_lines = (details / "sda-lun.txt").read_text().splitlines()
    logical_block = int(lun_lines[1])
    gpt = inspect_gpt((details / "sda-primary-gpt.bin").read_bytes(), logical_block)
    if (gpt["backup_header_lba"] + 1) * logical_block != int(lun_lines[3]) * 512:
        raise ValueError("GPT disk size disagrees with sysfs")
    userdata_entries = [part for part in gpt["partitions"] if part["name"] == "userdata"]
    if len(userdata_entries) != 1:
        raise ValueError("userdata entry is absent or ambiguous")
    userdata = userdata_entries[0]
    partition_text = (capture / "partition-layout.txt").read_text()
    match = re.search(r"^/sys/class/block/sda17\n(\d+)\n(\d+)\n", partition_text, re.MULTILINE)
    if (
        userdata["number"] != 17 or not match
        or int(match[1]) * 512 != userdata["first_lba"] * logical_block
        or int(match[2]) * 512 != userdata["bytes"]
    ):
        raise ValueError("userdata GPT and sysfs disagree")
    identity = (capture / "identity.txt").read_text().splitlines()
    config = gzip.decompress((capture / "kernel-config.gz").read_bytes()).decode()
    fb = dict(
        line.split("=", 1) for line in (capture / "framebuffer.txt").read_text().splitlines()
        if "=" in line and line.split("=", 1)[0] in ("name", "virtual_size", "stride", "bits_per_pixel", "modes")
    )
    stride, bpp = int(fb["stride"]), int(fb["bits_per_pixel"])
    if int(identity[-1]) != 4096 or "CONFIG_ARM64_4K_PAGES=y\n" not in config:
        raise ValueError("runtime kernel page-size evidence disagrees")
    input_text = (capture / "input-devices.txt").read_text()
    touch_text = (details / "touch-identification.txt").read_text()
    if 'Name="synaptics_dsx"' not in input_text or "/sys/bus/i2c/drivers/synaptics_dsi_force" not in touch_text:
        raise ValueError("Synaptics binding is not confirmed")
    cmdline = (capture / "cmdline.txt").read_text().split()
    profile = {
        "schema": "chiron-measured-profile-v1",
        "capture_date": "2026-10-20",
        "user_reported": {"ram_gb": 6, "storage_gb": 128},
        "device": "chiron", "soc_id": 292, "soc_revision": "2.1",
        "android_release": identity[2], "security_patch": identity[3],
        "kernel_page_bytes": 4096,
        "psci_method": strings(nodes["/psci"]["method"])[0],
        "cpu_affinities": [
            cells(props["reg"])[-1] for path, props in nodes.items()
            if re.fullmatch(r"/cpus/cpu@[^/]+", path)
        ],
        "cpu_16k_granule_supported": None,
        "raw_cpu_registers_read": False,
        "memory_banks": banks,
        "static_reserved_regions": reserved,
        "memblock_memory": memblock_ranges((details / "memblock-memory.txt").read_text()),
        "memblock_reserved": boot_reservations,
        "candidate_owned_memory": {
            **candidate,
            "passes_captured_android_map_checks": True,
            "qualified_for_boot_image": False,
            "remaining_checks": ["UEFI final memory map", "payload placement and lifetime", "early-stage access and exceptions"],
        },
        "display": {
            "width": 1080, "height": 2160,
            "virtual_size": [int(value) for value in fb["virtual_size"].split(",")],
            "stride_bytes": stride, "bits_per_pixel": bpp,
            "pixels_per_scanline": stride // (bpp // 8),
            "panel": "jdi fhd video dsi panel",
            "boot_panel_selector": next(
                (item.split("=", 1)[1] for item in cmdline if item.startswith("mdss_mdp.panel=")), None,
            ),
            "splash_region_is_not_proof_of_scanout_base": True,
        },
        "touch": {
            "driver": "synaptics_dsi_force", "input_name": "synaptics_dsx",
            "bus": 5, "i2c_address": 0x20,
            "qup_mmio": 0x0C179000, "reset_gpio": 89, "irq_gpio": 125,
            "firmware_build_id": int(re.search(r"buildid=(\d+)", touch_text)[1]),
            "exact_ic_model": None,
        },
        "sda": {"scsi_path": lun_lines[0], **gpt, "userdata": userdata, "userdata_matches_sysfs": True},
        "boot_status": {
            "reported_flash_locked_property": identity[5],
            "reported_verified_boot_property": identity[6],
            "reported_vbmeta_device_state_property": identity[7],
            "cmdline_verified_boot_state": next(
                (item.split("=", 1)[1] for item in cmdline if item.startswith("androidboot.verifiedbootstate=")), None,
            ),
            "fastboot_acceptance_tested": False,
        },
        "limits": [
            "Android runtime data does not qualify a future UEFI/XNU handoff.",
            "No flash, reboot, register write, kernel module, or persistent setting change was performed.",
            "Raw captures may contain identifiers; they remain in the Git-ignored artifacts directory.",
        ],
        "evidence": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                capture / "runtime-fdt.dtb", capture / "kernel-config.gz",
                capture / "input-devices.txt", details / "memblock-memory.txt",
                details / "memblock-reserved.txt", details / "sda-primary-gpt.bin",
            )
        },
    }
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(profile, indent=2) + "\n")
    print(json.dumps({
        "memory_banks": banks, "display": profile["display"],
        "userdata": userdata, "gpt_primary_crc_valid": True,
        "candidate_memory_qualified_for_boot": False,
    }, indent=2))


if __name__ == "__main__":
    main()
