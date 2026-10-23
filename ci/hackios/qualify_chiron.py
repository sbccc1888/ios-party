#!/usr/bin/env python3
"""Qualify saved native runtime evidence without rerunning or rebuilding anything."""

import argparse
import json
import os
from pathlib import Path
import re

import chiron
import cloud


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime", type=Path, required=True)
    args = ap.parse_args()
    cloud.require_runner()
    log = (args.runtime / "qemu-serial.log").read_text(errors="replace")
    if re.search(r"panic\(cpu|initproc failed|Undefined kernel instruction", log):
        raise RuntimeError("saved runtime has a real fatal exception")
    found = re.search(r"CurrentEL=1 MIDR=([0-9a-f]+) MMFR0=([0-9a-f]+)", log)
    if not found:
        raise RuntimeError("missing actual loader CPU identity")
    midr = int(found[1], 16)
    if midr >> 24 != 0x41 or (midr >> 4) & 0xfff != 0xd03:
        raise RuntimeError("saved runtime is not the real Cortex-A53 model")
    regs = json.loads((args.runtime / "actual-cpu-registers.json").read_text())["system_registers"]
    if int(regs["ID_AA64MMFR0_EL1"], 16) != int(found[2], 16):
        raise RuntimeError("GDB and loader CPU feature observations disagree")
    # MIDR is already an actual MRS observation in the loader log. The
    # independent TCR/SCTLR/MMFR/ISAR reads remain mandatory in summarize().
    chiron.summarize(Path("."), args.runtime)
    cloud.save(args.runtime / "qualification-provenance.json", {
        "runtime_run": "39918716948",
        "qualification_run": os.environ["GITHUB_RUN_ID"],
        "midr": hex(midr), "midr_source": "real loader MRS log",
        "mmu_and_instruction_features_source": "real GDB register reads",
        "new_guest_execution": False, "new_binary_build": False,
        "hardware_boot_qualified": False,
    })


if __name__ == "__main__":
    main()
