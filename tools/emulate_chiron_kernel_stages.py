#!/usr/bin/env python3
"""Check actual startup-hook bytes, continuation, bounded mapping and fatal output."""

import argparse
import json
import struct
from pathlib import Path

from build_chiron_entry_probe import sha256
from chiron_kernel_log import MachKernel
from emulate_chiron_entry_probe import write_png

PHYSICAL_BASE = 0x40004000
ARGS = 0x41540000
GOP = 0x9d400000
ROOT = 0x4017a000


def machine_for(kernel, metadata, sp0=False, aliases=False):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    import unicorn.arm64_const as reg

    model = MachKernel(kernel)
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    if aliases:
        import ctypes
        backing = ctypes.create_string_buffer(0x2000000)
        machine._chiron_backing = backing
        machine.mem_map_ptr(0x40000000, 0x2000000, 7, ctypes.addressof(backing))
        machine.mem_map_ptr(model.base - 0x4000, 0x2000000, 7, ctypes.addressof(backing))
    else:
        machine.mem_map(0x40000000, 0x2000000)
    machine.mem_map(GOP, 0xa00000)
    for segment in model.segments:
        if segment["file_bytes"]:
            machine.mem_write(
                PHYSICAL_BASE + segment["address"] - model.base,
                kernel[segment["file_offset"]:segment["file_offset"] + segment["file_bytes"]],
            )
    machine.mem_write(ARGS + 8, struct.pack("<3Q", model.base, PHYSICAL_BASE, 0x40000000))
    machine.mem_write(ARGS + 40, struct.pack("<6Q", GOP, 1, 4320, 1080, 2160, 32))
    machine.reg_write(reg.UC_ARM64_REG_PSTATE, 0x3c4 if sp0 else 0x3c5)
    machine.cpr_write(3, 0, 1, 0, 0, 0xc00808)
    # Deliberately unmapped SP: no hook may borrow an EFI or pre-MMU KVA stack.
    machine.reg_write(reg.UC_ARM64_REG_SP, 0xfffffff008001000)
    registers = [getattr(reg, f"UC_ARM64_REG_X{index}") for index in range(31)]
    for index, register in enumerate(registers):
        machine.reg_write(register, 0x1122000000000000 + index)
    machine.reg_write(registers[0], ARGS)
    machine.reg_write(registers[1], PHYSICAL_BASE + model.entry - model.base)
    machine.reg_write(registers[20], ARGS)
    machine.reg_write(reg.UC_ARM64_REG_NZCV, 0xa0000000)
    return machine, model, registers


def pa(model, virtual):
    return PHYSICAL_BASE + virtual - model.base


def snapshot(machine, registers):
    import unicorn.arm64_const as reg
    return {
        "gprs": [machine.reg_read(item) for item in registers],
        "sp": machine.reg_read(reg.UC_ARM64_REG_SP),
        "nzcv": machine.reg_read(reg.UC_ARM64_REG_NZCV),
        "daif": machine.reg_read(reg.UC_ARM64_REG_PSTATE) & 0x3c0,
        "tpidr_el0": machine.cpr_read(3, 3, 13, 0, 2),
    }


def hook_case(kernel, metadata, hook, sp0=False):
    import unicorn.arm64_const as reg
    actual, model, registers = machine_for(kernel, metadata, sp0)
    expected, _, _ = machine_for(kernel, metadata, sp0)
    report = metadata["stages"]
    stage = hook["stage"]
    site = pa(model, hook["site_va"])
    initial_tpidr = 0x12345678 if stage <= 3 else 0
    for machine in (actual, expected):
        machine.cpr_write(3, 3, 13, 0, 2, initial_tpidr)
        machine.mem_write(
            pa(model, report["diagnostic_entry"] + report["symbols"]["ChironStageInitialTpidr"]),
            struct.pack("<Q", initial_tpidr),
        )
        if stage == 5:
            machine.reg_write(registers[0], PHYSICAL_BASE)
        if stage == 6:
            machine.reg_write(registers[25], ROOT)
            machine.reg_write(registers[2], ROOT + 0x3000)
    expected.mem_write(site, struct.pack("<I", hook["displaced_word"]))
    expected.emu_start(site, site + 4, count=10)
    actual.emu_start(site, site + 4, count=2_000_000)
    if actual.reg_read(reg.UC_ARM64_REG_PC) != site + 4:
        raise ValueError(f"stage {stage}: continuation not reached")
    if snapshot(actual, registers) != snapshot(expected, registers):
        raise ValueError(f"stage {stage}: normal instruction/register contract changed")
    record = pa(model, report["early_record_va"])
    if struct.unpack("<Q", actual.mem_read(record, 8))[0] != stage:
        raise ValueError(f"stage {stage}: breadcrumb missing")
    if stage == 6:
        descriptor = struct.unpack("<Q", actual.mem_read(ROOT + 16, 8))[0]
        if descriptor != ROOT + 0x4000 + 3:
            raise ValueError("temporary framebuffer table pointer differs")
        page = actual.mem_read(ROOT + 0x4000, 4096)
        wanted = bytearray(4096)
        for index in range(5):
            struct.pack_into("<Q", wanted, (234 + index) * 8,
                             0x006000009d40060d + index * 0x200000)
        if page != wanted:
            raise ValueError("framebuffer table exceeds five NX device blocks")
    if stage == 9:
        if actual.cpr_read(3, 0, 12, 0, 0) != 0:
            raise ValueError("stage 09 installed the premature native VBAR")
    frame = bytes(actual.mem_read(GOP + 540 * 4320, 510 * 4320))
    return {"name": f"stage {stage:02X}, SP_EL{0 if sp0 else 1}",
            "register_continuation_preserved": True}, frame


def guard_case(kernel, metadata, kind):
    import unicorn.arm64_const as reg
    machine, model, registers = machine_for(kernel, metadata)
    report = metadata["stages"]
    halt = pa(model, report["diagnostic_entry"] + report["symbols"]["ChironStageHalt"])
    entry = pa(model, report["diagnostic_entry"])
    if kind == "unexpected GOP pitch":
        machine.mem_write(ARGS + 56, struct.pack("<Q", 4352))
    elif kind == "unexpected owned memory size":
        machine.mem_write(ARGS + 24, struct.pack("<Q", 0x80000000))
    elif kind == "enabled incoming data cache":
        machine.cpr_write(3, 0, 1, 0, 0, 0xc0080c)
    elif kind == "unexpected physical base":
        machine.mem_write(ARGS + 16, struct.pack("<Q", 0x50004000))
    else:
        raise ValueError(kind)
    machine.emu_start(entry, halt, count=2_000_000)
    if machine.reg_read(reg.UC_ARM64_REG_PC) != halt:
        raise ValueError(kind + ": failed to halt")
    if any(machine.mem_read(GOP, 0xa00000)):
        raise ValueError(kind + ": wrote GOP despite a failed guard")
    return {"name": kind, "halted_before_gop_write": True}


def mapping_guard_case(kernel, metadata, kind):
    import unicorn.arm64_const as reg
    machine, model, registers = machine_for(kernel, metadata)
    report = metadata["stages"]
    machine.reg_write(registers[25], ROOT)
    machine.reg_write(registers[2], ROOT + 0x3000)
    if kind == "occupied root slot":
        machine.mem_write(ROOT + 16, struct.pack("<Q", 0x12345673))
    elif kind == "occupied free page":
        machine.mem_write(ROOT + 0x4000, struct.pack("<Q", 0x12345678))
    elif kind == "reservation exhausted":
        machine.reg_write(registers[2], ROOT + 0xf000)
    elif kind == "unaligned free page":
        machine.reg_write(registers[2], ROOT + 0x3008)
    else:
        raise ValueError(kind)
    before = bytes(machine.mem_read(ROOT, 0x10000))
    halt = pa(model, report["diagnostic_entry"] + report["symbols"]["ChironStageHalt"])
    site = pa(model, report["hooks"][5]["site_va"])
    machine.emu_start(site, halt, count=2_000_000)
    if machine.reg_read(reg.UC_ARM64_REG_PC) != halt:
        raise ValueError(kind + ": failed to halt")
    if bytes(machine.mem_read(ROOT, 0x10000)) != before:
        raise ValueError(kind + ": modified occupied page tables")
    if struct.unpack("<Q", machine.mem_read(pa(model, report["early_record_va"]), 8))[0] != 0xe1:
        raise ValueError(kind + ": missing failure code E1")
    return {"name": kind, "rejected_without_page_table_write": True}


def exception_case(kernel, metadata, slot, sp0=False):
    import unicorn.arm64_const as reg
    machine, model, _ = machine_for(kernel, metadata, sp0)
    report = metadata["stages"]
    values = (0x02000000, 0x403fb05c, 0x12345000, 0x600003c5)
    for encoding, value in zip(
        ((3, 0, 5, 2, 0), (3, 0, 4, 0, 1), (3, 0, 6, 0, 0), (3, 0, 4, 0, 0)),
        values,
    ):
        machine.cpr_write(*encoding, value)
    record = pa(model, report["early_record_va"])
    machine.mem_write(record, struct.pack("<Q", 1))
    halt = pa(model, report["diagnostic_entry"] + report["symbols"]["ChironStageHalt"])
    machine.emu_start(pa(model, report["low_vectors_va"]) + slot * 128, halt, count=3_000_000)
    recorded = struct.unpack("<7Q", machine.mem_read(record, 56))
    if machine.reg_read(reg.UC_ARM64_REG_PC) != halt or recorded[1:5] != values:
        raise ValueError("fatal vector lost exception registers")
    if recorded[5] != 0xfffffff008001000 or recorded[6] != 0xc00808:
        raise ValueError("fatal vector lost original SP/SCTLR")
    frame = bytes(machine.mem_read(GOP + 540 * 4320, 510 * 4320))
    if sum(value != 0 for value in frame) < 1000:
        raise ValueError("fatal exception text is blank")
    return {"name": f"fatal vector {slot}, SP_EL{0 if sp0 else 1}",
            "synthetic_exception_registers_and_text_verified": True}, frame


def cold_start_case(kernel, metadata):
    import unicorn.arm64_const as reg
    machine, model, _ = machine_for(kernel, metadata)
    # Stop BEFORE the original SCTLR write, not at an emulated MMU fault.
    stop = pa(model, 0xfffffff0073fb350)
    machine.emu_start(pa(model, model.entry), stop, count=15_000_000)
    if machine.reg_read(reg.UC_ARM64_REG_PC) != stop:
        raise ValueError("cold startup did not reach the pre-MMU boundary")
    if machine.cpr_read(3, 0, 1, 0, 0) & 1:
        raise ValueError("cold startup test unexpectedly enabled the MMU")
    if machine.cpr_read(3, 0, 2, 0, 2) != 0x22a51a251a:
        raise ValueError("cold startup TCR differs from native 4K baseline")
    if machine.cpr_read(3, 0, 2, 0, 0) != ROOT:
        raise ValueError("cold startup TTBR0 differs")
    if machine.cpr_read(3, 0, 2, 0, 1) != ROOT + 4096:
        raise ValueError("cold startup TTBR1 differs")

    def translate(root, virtual):
        # This pinned baseline uses 38-bit VAs, an L1 root and 2 MiB blocks.
        virtual &= (1 << 38) - 1
        first = struct.unpack("<Q", machine.mem_read(root + (virtual >> 30) * 8, 8))[0]
        if first & 3 != 3:
            raise ValueError("cold startup root is not a table descriptor")
        table = first & 0xfffffff000
        second = struct.unpack(
            "<Q", machine.mem_read(table + ((virtual >> 21) & 511) * 8, 8)
        )[0]
        if second & 3 != 1:
            raise ValueError("cold startup mapping is not an L2 block")
        return (second & 0xffffe00000) | (virtual & 0x1fffff)

    if translate(ROOT, stop) != stop:
        raise ValueError("cold startup identity code mapping differs")
    arm_init_va = metadata["stages"]["hooks"][10]["site_va"]
    if translate(ROOT + 4096, arm_init_va) != pa(model, arm_init_va):
        raise ValueError("cold startup arm_init virtual mapping differs")
    for position in (GOP, GOP + 4320 * 2160 - 1):
        if translate(ROOT, position) != position:
            raise ValueError("cold startup framebuffer mapping differs")
    record = pa(model, metadata["stages"]["early_record_va"])
    if struct.unpack("<Q", machine.mem_read(record, 8))[0] != 9:
        raise ValueError("cold startup stage sequence differs")
    frame = bytes(machine.mem_read(GOP + 540 * 4320, 510 * 4320))
    return {
        "name": "actual cold startup through pre-SCTLR boundary",
        "original_bootstrap_table_generation_executed": True,
        "original_tcr_ttbr_mair_setup_executed": True,
        "table_bytes_decode_to_expected_code_arm_init_and_gop_mappings": True,
        "mmu_transition_executed": False,
    }, frame


def late_window_case(kernel, metadata, hook, failure=None):
    """Exercise actual instructions with modeled SCTLR/AT, not hardware MMU walks."""
    from unicorn import UC_HOOK_CODE, UC_PROT_READ, UC_PROT_EXEC, UC_PROT_WRITE
    import unicorn.arm64_const as reg

    machine, model, registers = machine_for(kernel, metadata, aliases=True)
    report = metadata["stages"]
    diagnostic = report["diagnostic_entry"]
    record = report["early_record_va"]
    descriptor = ROOT + 0x4000 + 3
    machine.mem_write(ROOT + 16, struct.pack("<Q", descriptor))
    machine.mem_write(diagnostic + report["symbols"]["ChironWindowDescriptor"],
                      struct.pack("<Q", descriptor))
    for index in range(5):
        machine.mem_write(ROOT + 0x4000 + (234 + index) * 8,
                          struct.pack("<Q", 0x006000009d40060d + index * 0x200000))
    if failure == "changed root":
        machine.mem_write(ROOT + 16, struct.pack("<Q", descriptor + 4096))
    if failure == "changed video block":
        machine.mem_write(ROOT + 0x4000 + 236 * 8, struct.pack("<Q", 0))
    # Original mappings are independently selected and must be restored exactly.
    machine.cpr_write(3, 0, 2, 0, 0, 0x123400004017d000)
    machine.cpr_write(3, 0, 2, 0, 1, 0x40180000)
    machine.cpr_write(3, 0, 2, 0, 2, 0x22a51a251a)
    machine.cpr_write(3, 0, 7, 4, 0, 0x88776000)
    original_ttbr0 = machine.cpr_read(3, 0, 2, 0, 0)
    original_ttbr1 = machine.cpr_read(3, 0, 2, 0, 1)
    original_tcr = machine.cpr_read(3, 0, 2, 0, 2)
    original_par = machine.cpr_read(3, 0, 7, 4, 0)
    events = []
    for segment in model.segments:
        if segment["name"] in ("__TEXT_EXEC", "__DATA"):
            permissions = (UC_PROT_READ | UC_PROT_EXEC if segment["name"] == "__TEXT_EXEC"
                           else UC_PROT_READ | UC_PROT_WRITE)
            for base in (segment["address"], pa(model, segment["address"])):
                machine.mem_protect(base, segment["size"], permissions)
    expected = snapshot(machine, registers)
    word = hook["displaced_word"]
    if hook["stage"] == 18:
        expected["gprs"][19] = (expected["gprs"][19] + 0xbd0) & ((1 << 64) - 1)
    elif hook["stage"] == 19:
        expected["gprs"][27] = expected["gprs"][22]
    elif hook["stage"] == 20:
        expected["gprs"][0] = expected["gprs"][20]
    elif hook["stage"] == 21:
        expected["gprs"][23] = expected["gprs"][9] * expected["gprs"][8] & ((1 << 64) - 1)
    elif hook["stage"] == 22:
        expected["sp"] += 0x160
    elif hook["stage"] != 23 or word != 0xd503201f:
        raise ValueError("late hook instruction expectation changed")

    def modeled_registers(uc, address, size, unused):
        instruction = struct.unpack("<I", uc.mem_read(address, 4))[0]
        if instruction & ~31 == 0xd5381000:  # MRS SCTLR_EL1
            uc.reg_write(registers[instruction & 31], 0x3454593d)
            uc.reg_write(reg.UC_ARM64_REG_PC, address + 4)
        elif instruction & ~31 == 0xd5087820:  # AT S1E1W
            target = uc.reg_read(registers[instruction & 31])
            root = uc.cpr_read(3, 0, 2, 0, 0) & 0xfffffff000
            valid = root == ROOT and GOP <= target < GOP + 0xa00000
            value = target & 0xfffffff000 if valid and failure != "translation refused" else 1
            uc.cpr_write(3, 0, 7, 4, 0, value)
            uc.reg_write(reg.UC_ARM64_REG_PC, address + 4)
        elif instruction & ~31 == 0xd5182000:  # MSR TTBR0_EL1
            events.append(uc.reg_read(registers[instruction & 31]))

    machine.hook_add(UC_HOOK_CODE, modeled_registers)
    stop = (diagnostic + report["symbols"]["ChironStageHalt"]
            if failure in ("changed root", "changed video block") else hook["site_va"] + 4)
    machine.emu_start(hook["site_va"], stop, count=3_000_000)
    if machine.reg_read(reg.UC_ARM64_REG_PC) != stop:
        raise ValueError("late window did not reach expected continuation/halt")
    if machine.cpr_read(3, 0, 2, 0, 0) != original_ttbr0:
        raise ValueError("late window lost original TTBR0")
    if (machine.cpr_read(3, 0, 2, 0, 1), machine.cpr_read(3, 0, 2, 0, 2),
        machine.cpr_read(3, 0, 7, 4, 0)) != (original_ttbr1, original_tcr, original_par):
        raise ValueError("late window changed TTBR1, TCR or PAR")
    actual_stage = struct.unpack("<Q", machine.mem_read(record, 8))[0]
    if failure in ("changed root", "changed video block"):
        if events or actual_stage != 0xe2:
            raise ValueError("late mapping guard failed before TTBR0 modification")
    else:
        if events != [ROOT, original_ttbr0] or actual_stage != hook["stage"]:
            raise ValueError("late drawing did not bracket TTBR0 changes")
        if snapshot(machine, registers) != expected:
            raise ValueError("late drawing lost original instruction/register semantics")
    frame = bytes(machine.mem_read(GOP + 540 * 4320, 510 * 4320))
    painted = any(frame)
    if painted != (failure is None):
        raise ValueError("late framebuffer guard or rendering failed")
    return {
        "name": f"late stage {hook['stage']:02X}, {failure or 'read-only text / writable data'}",
        "original_ttbr0_ttbr1_tcr_par_preserved": True,
        "ttbr0_restore_on_failed_translation": failure == "translation refused",
        "sctlr_and_at_are_modeled": True,
    }, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    metadata = json.loads((args.boot_directory / "kernel-log-report.json").read_text())
    kernel = (args.boot_directory / "kernel.macho").read_bytes()
    if sha256(kernel) != metadata["stages"]["diagnostic_kernel_sha256"]:
        parser.error("kernel fingerprint differs")
    results = []
    for hook in metadata["stages"]["hooks"]:
        result, frame = hook_case(kernel, metadata, hook)
        results.append(result)
    result, _ = hook_case(kernel, metadata, metadata["stages"]["hooks"][3], sp0=True)
    results.append(result)
    for kind in ("unexpected GOP pitch", "unexpected owned memory size",
                 "enabled incoming data cache", "unexpected physical base"):
        results.append(guard_case(kernel, metadata, kind))
    for kind in ("occupied root slot", "occupied free page", "reservation exhausted",
                 "unaligned free page"):
        results.append(mapping_guard_case(kernel, metadata, kind))
    previews = []
    for slot, sp0 in ((0, True), (4, False), (8, False)):
        result, frame = exception_case(kernel, metadata, slot, sp0)
        results.append(result)
        previews.append(frame)
    result, startup_frame = cold_start_case(kernel, metadata)
    results.append(result)
    late_preview = None
    if metadata["stages"].get("post_vm"):
        for hook in metadata["stages"]["hooks"]:
            if hook["stage"] >= 18:
                result, late_preview = late_window_case(kernel, metadata, hook)
                results.append(result)
        for failure in ("changed root", "changed video block", "translation refused"):
            result, _ = late_window_case(kernel, metadata, metadata["stages"]["hooks"][-1], failure)
            results.append(result)
    args.output.mkdir(parents=True, exist_ok=False)
    write_png(args.output / "exception-preview.png", previews[0], width=1080, height=510)
    write_png(args.output / "stage-preview.png", startup_frame, width=1080, height=510)
    if late_preview is not None:
        write_png(args.output / "post-vm-preview.png", late_preview, width=1080, height=510)
    output = {
        "all_cases_passed": True, "cases": results,
        "kernel_sha256": sha256(kernel),
        "limits": [
            "Individual hook paths run with MMU off in flat synthetic RAM.",
            "Cold startup executes table generation/TCR/TTBR/MAIR setup but stops before SCTLR.",
            "Table decoding checks bytes, not hardware table walks or the native MMU transition.",
            "Exception registers are injected; no claim of hardware exception delivery.",
            "Original arm_init/arm_vm_init bodies, crypto POST and PID 1 are not executed.",
            "Late cases model SCTLR/AT with shared physical/KVA RAM aliases; no actual MMU transition.",
        ],
    }
    (args.output / "emulation-report.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"all_cases_passed": True, "cases": len(results)}, indent=2))


if __name__ == "__main__":
    main()
