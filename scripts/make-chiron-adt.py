#!/usr/bin/env python3
"""Generate a minimal chiron Apple device tree from measured board inputs."""

import argparse
import hashlib
import json
import os
import struct
import tomllib
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def u32(value):
    return struct.pack("<I", value)


def u64(*values):
    return struct.pack("<" + "Q" * len(values), *values)


def node(name, properties=(), children=()):
    properties = [("name", name.encode("ascii") + b"\0"), *properties]
    output = struct.pack("<II", len(properties), len(children))
    for key, value in properties:
        if not key.isascii() or len(key) > 31:
            raise ValueError("invalid ADT property name")
        output += struct.pack("<32sI", key.encode("ascii"), len(value))
        output += value + bytes(-len(value) % 4)
    return output + b"".join(children)


def nvram_proxy():
    def header(name, size):
        data = bytearray(struct.pack("<BBH12s", 0x70, 0, size // 16, name))
        checksum = sum(data)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        data[1] = checksum
        return data

    bank = bytearray(8192)
    bank[:16] = header(b"nvram", 32)
    bank[32:48] = header(b"common", 2064)
    free = 32 + 2064
    bank[free:free + 16] = header(b"free", len(bank) - free)
    struct.pack_into("<I", bank, 16, zlib.adler32(bank[20:]))
    return [
        ("nvram-bank-size", u32(len(bank))),
        ("nvram-bank-count", u32(1)),
        ("nvram-current-bank", u32(0)),
        ("nvram-proxy-data", bytes(bank)),
    ]


def generate(board, profile, seed):
    spec = board["bringup"]
    if profile["device"] != "chiron" or profile["soc_revision"] != "2.1":
        raise ValueError("unqualified device profile")
    if spec["cpu_count"] != 1 or profile["psci_method"] != "smc":
        raise ValueError("only the measured single-CPU SMC fixture is supported")
    if profile["kernel_page_bytes"] != 4096 or spec["virtual_timer_intid"] != 19:
        raise ValueError("unexpected chiron page size or timer")
    if len(seed) != 64:
        raise ValueError("the boot entropy seed must contain 64 bytes")
    allocation_size = profile["candidate_owned_memory"]["size"]
    banks = profile["memory_banks"]
    cpu = node("cpu0", [
        ("device_type", b"cpu\0"),
        ("compatible", b"qcom,kryo280\0arm,armv8\0"),
        ("reg", u32(profile["cpu_affinities"][0])),
        ("state", b"running\0"),
        ("timebase-frequency", u32(spec["timer_frequency"])),
    ])
    intc = node("intc", [
        ("compatible", b"arm,gic-v3\0"),
        ("reg", u64(
            spec["gicd"] - spec["arm_io_base"], spec["gicd_size"],
            spec["gicr"] - spec["arm_io_base"], spec["gicr_size"],
        )),
        ("redistributor-stride", u64(spec["redistributor_stride"])),
        ("virtual-timer-intid", u32(spec["virtual_timer_intid"])),
    ])
    memory_nodes = [node(f"memory{index}", [
        ("device_type", b"memory\0"), ("reg", u64(bank["base"], bank["size"])),
    ]) for index, bank in enumerate(banks)]
    return node("device-tree", [
        ("model", b"Xiaomi Mi MIX 2\0"), ("compatible", b"xiaomi,chiron\0"),
    ], [
        node("chosen", [
            ("random-seed", seed),
            ("firmware-version", b"chiron-source-bringup\0"),
            ("dram-base", u64(min(bank["base"] for bank in banks))),
            ("dram-size", u64(sum(bank["size"] for bank in banks))),
            ("darwinboot-memory-size", u64(allocation_size)),
            *nvram_proxy(),
        ], [node("memory-map")]),
        node("cpus", children=[cpu]),
        node("psci", [("compatible", b"arm,psci-1.0\0"), ("method", b"smc\0")]),
        node("defaults"),
        node("options"),
        node("arm-io", [
            ("device_type", b"chiron-io\0"),
            ("ranges", u64(0, spec["arm_io_base"], spec["arm_io_size"])),
        ], [intc]),
        *memory_nodes,
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, default=ROOT / "board.toml")
    parser.add_argument("--profile", type=Path, default=ROOT / "research/chiron-live-profile.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    board = tomllib.loads(args.board.read_text())
    profile = json.loads(args.profile.read_text())
    data = generate(board, profile, os.urandom(64))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    report = {
        "device": "chiron", "cpus": 1, "timer_intid": 19,
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "hardware_boot_qualified": False,
        "storage_nodes_included": False, "apple_identity_included": False,
        "memory_policy": "DarwinBoot must allocate from its actual UEFI free-memory map.",
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
