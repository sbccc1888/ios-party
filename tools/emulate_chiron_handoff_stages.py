#!/usr/bin/env python3
"""Execute the patched PE's handoff code in an identity-mapped Unicorn model."""

import argparse
import json
import struct
from pathlib import Path

from build_chiron_entry_probe import sha256
from emulate_chiron_entry_probe import write_png


def run_case(pe_data, metadata, case):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import (
        UC_ARM64_REG_PC, UC_ARM64_REG_PSTATE, UC_ARM64_REG_SP, UC_ARM64_REG_X0,
        UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4,
        UC_ARM64_REG_X5, UC_ARM64_REG_X30,
    )

    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    image_base = 0x1b9000000
    pe = struct.unpack_from("<I", pe_data, 60)[0]
    opt = pe + 24
    opt_size = struct.unpack_from("<H", pe_data, pe + 20)[0]
    image_size = struct.unpack_from("<I", pe_data, opt + 56)[0]
    machine.mem_map(image_base, (image_size + 4095) & ~4095)
    for index in range(struct.unpack_from("<H", pe_data, pe + 6)[0]):
        section = opt + opt_size + index * 40
        _, rva, size, offset = struct.unpack_from("<4I", pe_data, section + 8)
        machine.mem_write(image_base + rva, pe_data[offset:offset + size])
    fb_base = 0 if case.get("no_framebuffer") else 0x9d400000
    fb_size = 0 if case.get("zero_framebuffer_size") else 4320 * 2160
    machine.mem_map(0x9d400000, 0xa00000)
    owned = case.get("owned", 0x40004000)
    owned_size = case.get("owned_size", 0x10000)
    mapping = owned & ~4095
    machine.mem_map(mapping, ((owned - mapping + owned_size + 4095) // 4096) * 4096)
    kernel = 0x403f9488
    machine.mem_map(kernel & ~4095, 4096)
    machine.reg_write(UC_ARM64_REG_PSTATE, 0x3c5)
    machine.cpr_write(3, 0, 1, 0, 0, 0x1004)
    machine.reg_write(UC_ARM64_REG_SP, 0x70001000)
    args = [0x41540000, kernel, owned, owned_size, fb_base, fb_size]
    regs = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
            UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5)
    for reg, value in zip(regs, args):
        machine.reg_write(reg, value)
    machine.reg_write(UC_ARM64_REG_X30, 0x12345678)
    start = image_base + metadata["function_rva"]
    offsets = metadata["stage_offsets"]
    cleaned = []
    stages = []

    def cache_line(uc, address, size, user_data):
        cleaned.append(uc.reg_read(UC_ARM64_REG_X4))

    def checkpoint(uc, address, size, name):
        stages.append({"stage": name, "sctlr_el1": hex(uc.cpr_read(3, 0, 1, 0, 0))})

    address = start + offsets["ChironHandoffCacheLoop"]
    machine.hook_add(UC_HOOK_CODE, cache_line, begin=address, end=address)
    for name in ("ChironHandoffCacheDone", "ChironHandoffMmuOff", "ChironHandoffKernelBranch"):
        address = start + offsets[name]
        machine.hook_add(UC_HOOK_CODE, checkpoint, user_data=name, begin=address, end=address)
    machine.emu_start(start, kernel, count=6_000_000)
    line_bytes = 4 << ((machine.cpr_read(3, 3, 0, 0, 1) >> 16) & 15)
    expected = list(range(owned & ~(line_bytes - 1), owned + owned_size, line_bytes))
    if cleaned != expected or machine.reg_read(UC_ARM64_REG_PC) != kernel:
        raise ValueError(f"{case['name']}: wrong cache-clean bounds or kernel target")
    expected_stages = ["ChironHandoffCacheDone", "ChironHandoffMmuOff", "ChironHandoffKernelBranch"]
    if [stage["stage"] for stage in stages] != expected_stages:
        raise ValueError(f"{case['name']}: stage order changed")
    if any(int(stage["sctlr_el1"], 16) & 0x1005 for stage in stages[1:]):
        raise ValueError(f"{case['name']}: MMU/cache control bits not cleared")
    if (
        [machine.reg_read(reg) for reg in regs[:4]] != args[:4]
        or machine.reg_read(UC_ARM64_REG_SP) != 0x70001000
        or machine.reg_read(UC_ARM64_REG_X30) != 0x12345678
    ):
        raise ValueError(f"{case['name']}: boot arguments, SP or LR changed")
    stripe_bytes = (fb_size >> 4) & ~7
    colors = [0xff00ff00, 0xff0000ff, 0xffff0000]
    if fb_base and stripe_bytes:
        for index, color in enumerate(colors):
            actual = bytes(machine.mem_read(fb_base + index * stripe_bytes, stripe_bytes))
            if actual != struct.pack("<I", color) * (stripe_bytes // 4):
                raise ValueError(f"{case['name']}: wrong stripe {index}")
    elif any(machine.mem_read(0x9d400000, 4096)):
        raise ValueError(f"{case['name']}: painted an absent framebuffer")
    frame = bytes(machine.mem_read(0x9d400000, 4320 * 432))
    return {
        "name": case["name"], "cache_lines": len(cleaned), "cache_line_bytes": line_bytes,
        "cache_bounds_match_original_algorithm": True, "stage_order": stages,
        "kernel_branch_target_observed": hex(kernel), "kernel_instruction_executed": False,
        "boot_args_sp_lr_preserved": True,
        "stripes_verified": bool(fb_base and stripe_bytes),
    }, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    metadata = json.loads((args.boot_directory / "handoff-report.json").read_text())
    pe = (args.boot_directory / "DarwinBoot-stages.efi").read_bytes()
    if sha256(pe) != metadata["patched_pe_sha256"]:
        parser.error("patched PE does not match the report")
    cases = [
        {"name": "aligned owned window"},
        {"name": "unaligned owned window", "owned": 0x40004003, "owned_size": 0x1001},
        {"name": "no GOP framebuffer", "no_framebuffer": True},
        {"name": "empty GOP framebuffer", "zero_framebuffer_size": True},
    ]
    results = []
    preview = None
    for case in cases:
        result, frame = run_case(pe, metadata, case)
        results.append(result)
        if preview is None:
            preview = frame
    args.output.mkdir(parents=True, exist_ok=False)
    write_png(args.output / "stripe-preview.png", preview, height=432)
    report = {
        "all_cases_passed": True, "patched_pe_sha256": sha256(pe), "cases": results,
        "limits": [
            "Identity-mapped emulator RAM; real UEFI page tables and cache behavior are not modeled.",
            "Test windows are reduced, not the phone's full 1 GiB cache-clean run.",
            "Initial MMU is off in the model; C/I control transitions are checked.",
            "Execution stops at the kernel target before XNU executes.",
        ],
    }
    (args.output / "emulation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
