#!/usr/bin/env python3
"""Execute the kernel-file witness and verify rejoining the unchanged original entry."""

import argparse
import json
import struct
from pathlib import Path

from build_chiron_entry_probe import sha256
from chiron_kernel_log import MachKernel
from emulate_chiron_entry_probe import write_png


def run_case(kernel, metadata, case):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    import unicorn.arm64_const as reg

    witness = metadata["witness"]
    model = MachKernel(kernel)
    physical_base = case.get("physical_base", 0x40004000)
    original = physical_base + witness["original_entry"] - model.base
    target = physical_base + witness["original_branch_target"] - model.base
    start = physical_base + model.entry - model.base
    if (start + witness["witness_stack_top_offset"]) % 16:
        raise ValueError("witness stack violates AArch64 SP alignment")
    pages = {original & ~4095, target & ~4095}
    pages.update(range(start & ~4095, (start + witness["witness_bytes"] + 4095) & ~4095, 4096))
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for page in sorted(pages):
        machine.mem_map(page, 4096)
        virtual = model.base + page - physical_base
        offset = model.file_offset(virtual, executable=True)
        machine.mem_write(page, kernel[offset:offset + 4096])
    args = 0x60000000
    machine.mem_map(args, 4096)
    framebuffer = 0 if case.get("no_gop") else 0x9d400000
    video = [framebuffer, 1, case.get("row_bytes", 4320), 1080, 2160, 32]
    machine.mem_write(args + 40, struct.pack("<6Q", *video))
    machine.mem_map(0x9d400000, 0xa00000)
    machine.reg_write(reg.UC_ARM64_REG_PSTATE, case.get("pstate", 0x3c5))
    machine.cpr_write(3, 0, 1, 0, 0, 0xc00808 | case.get("cache", 0))
    registers = [getattr(reg, "UC_ARM64_REG_X" + str(index)) for index in range(31)]
    before = [0x1122000000000000 + index for index in range(31)]
    before[0], before[1] = args, start
    for register, value in zip(registers, before):
        machine.reg_write(register, value)
    machine.reg_write(reg.UC_ARM64_REG_SP, 0x70001000)
    machine.reg_write(reg.UC_ARM64_REG_NZCV, 0xa0000000)
    seen = []

    def original_entry(uc, address, size, user_data):
        seen.append(address)

    machine.hook_add(UC_HOOK_CODE, original_entry, begin=original, end=original)
    machine.emu_start(start, target, count=4_000_000)
    after = [machine.reg_read(register) for register in registers]
    expected = list(before)
    expected[1] = original
    if (
        machine.reg_read(reg.UC_ARM64_REG_PC) != target or seen != [original]
        or after != expected or machine.reg_read(reg.UC_ARM64_REG_SP) != 0x70001000
        or machine.reg_read(reg.UC_ARM64_REG_NZCV) != 0xa0000000
    ):
        raise ValueError(f"{case['name']}: original entry/control/register continuation mismatch")
    frame = bytes(machine.mem_read(0x9d400000, 4320 * 552))
    nonzero = sum(value != 0 for value in frame)
    should_render = not (case.get("no_gop") or case.get("row_bytes") or case.get("cache"))
    if should_render != (nonzero > 1000):
        raise ValueError(f"{case['name']}: rendering guard failed")
    return {
        "name": case["name"], "witness_pa": hex(start), "original_entry_pa": hex(original),
        "original_first_branch_executed": True, "original_branch_target_pa": hex(target),
        "all_normal_entry_gprs_restored": True, "sp_and_nzcv_restored": True,
        "witness_stack_16byte_aligned": True,
        "screen_marker_observed_in_model": should_render,
        "xnu_initialization_executed": False,
    }, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    metadata = json.loads((args.boot_directory / "kernel-log-report.json").read_text())
    if not metadata["witness"]:
        parser.error("this is a console-only image")
    kernel = (args.boot_directory / "kernel.macho").read_bytes()
    if sha256(kernel) != metadata["witness"]["diagnostic_kernel_sha256"]:
        parser.error("kernel does not match the report")
    cases = [
        {"name": "observed physical load base"},
        {"name": "different physical load base", "physical_base": 0x50004000},
        {"name": "EL1 SP0 handoff", "pstate": 0x3c4},
        {"name": "no GOP safely skipped", "no_gop": True},
        {"name": "unexpected row bytes safely skipped", "row_bytes": 4352},
        {"name": "enabled data cache safely skipped", "cache": 4},
    ]
    results = []
    preview = None
    for case in cases:
        result, frame = run_case(kernel, metadata, case)
        results.append(result)
        if preview is None:
            preview = frame
    args.output.mkdir(parents=True, exist_ok=False)
    write_png(args.output / "entry-preview.png", preview, width=640, height=552)
    report = {
        "all_cases_passed": True, "kernel_sha256": sha256(kernel), "cases": results,
        "limits": [
            "Identity/flat RAM model with synthetic boot_args, not the phone or its page tables.",
            "Execution stops at the first original branch target; original XNU initialization is not emulated.",
            "This does not qualify native console readiness, source crypto POST execution or PID 1.",
        ],
    }
    (args.output / "emulation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
