#!/usr/bin/env python3
"""Execute the actual pinned ARM64 relocation helper in Unicorn, without device access."""

import argparse
import json
import struct
from pathlib import Path

from patch_chiron_fdt_store import COPY_BODY, NOP, sha256


def emulate(firmware, entry, source_base):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_X0, UC_ARM64_REG_X30

    destination = 0xd0000000
    store = 0x83300000
    fdt = 0x43000000
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    machine.mem_map(source_base, len(firmware))
    machine.mem_write(source_base, firmware)
    machine.mem_map(destination, len(firmware))
    if not source_base <= store < source_base + len(firmware):
        machine.mem_map(store, 4096)
    machine.reg_write(UC_ARM64_REG_X0, fdt)
    machine.reg_write(UC_ARM64_REG_X30, source_base + 0x1000)
    end = destination + 0x1000
    machine.emu_start(source_base + entry, end, count=2_000_000)
    if machine.reg_read(UC_ARM64_REG_PC) != end:
        raise ValueError("relocation did not return to its relocated caller")
    copied = bytes(machine.mem_read(destination, len(firmware)))
    differences = [index for index, (left, right) in enumerate(zip(firmware, copied)) if left != right]
    return {
        "source_base": hex(source_base),
        "source_overlaps_fdt_store": source_base <= store < source_base + len(firmware),
        "copied_fd_matches_input": not differences,
        "changed_bytes_in_copied_fd": len(differences),
        "first_changed_fd_offset": hex(differences[0]) if differences else None,
        "fdt_pointer_stored_correctly": bytes(machine.mem_read(store, 8)) == struct.pack("<Q", fdt),
        "relocated_return_observed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-firmware", type=Path, required=True)
    parser.add_argument("--patched-firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output file already exists")
    original = args.source_firmware.read_bytes()
    patched = args.patched_firmware.read_bytes()
    if len(original) != 0x600000 or len(patched) != len(original) or original.count(COPY_BODY) != 1:
        parser.error("expected original and patched 6 MiB pinned firmware volumes")
    start = original.index(COPY_BODY)
    if patched[start + 4:start + 8] != NOP:
        parser.error("patched firmware has no deferred store")
    # The 20 bytes before COPY_BODY are the actual ADR/LDR/CMP/B.NE/RET
    # prologue, including its original linked-address comparison.
    entry = start - 20
    cases = []
    for source_base in (0x80080000, 0x83200000):
        for name, firmware in (("original", original), ("patched", patched)):
            cases.append({"variant": name, **emulate(firmware, entry, source_base)})
    baseline, fixed_normal, corrupted, fixed_overlap = cases
    passed = (
        baseline["copied_fd_matches_input"] and fixed_normal["copied_fd_matches_input"]
        and not corrupted["copied_fd_matches_input"] and fixed_overlap["copied_fd_matches_input"]
        and all(case["fdt_pointer_stored_correctly"] for case in cases)
    )
    report = {
        "source_sha256": sha256(original), "patched_sha256": sha256(patched),
        "all_expected_outcomes_observed": passed,
        "instruction_source": "Actual SEC relocation helper bytes from the supplied firmware volumes",
        "cases": cases,
        "limits": [
            "Synthetic physical addresses and flat mapped RAM; not an emulation of the MIX 2 bootloader.",
            "No actual failing phone load address was observed.",
            "This does not execute DXE, XNU, or iOS.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
