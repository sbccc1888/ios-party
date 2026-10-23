#!/usr/bin/env python3
"""Reserve the measured MIX2 high bank before DXE memory fragmentation."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from apply_chiron import replace_once


ROOT = Path(__file__).resolve().parents[2]


def apply(source):
    package = ROOT / "package/ChironCiPkg"
    memory = source / "Platforms/Msm8998FamilyPkg/MemoryInitPei"
    dsc = source / "Platforms/Msm8998Pkg/Msm8998.dsc"
    old = "gArmTokenSpaceGuid.PcdSystemMemorySize|0xF0000000"
    dsc.write_text(replace_once(dsc.read_text(), old,
                               "gArmTokenSpaceGuid.PcdSystemMemorySize|0x60000000"))
    for name in ("ChironMemoryWindow.h", "ChironReserveWindow.inc"):
        shutil.copy2(package / name, memory / name)
    entry = memory / "MemoryInitPeiLib.c"
    text = replace_once(
        entry.read_text(), '#include "ChironReservePayload.inc"',
        '#include "ChironReservePayload.inc"\n#include "ChironReserveWindow.inc"',
    )
    text = replace_once(
        text, "  ChironReservePayload(Fdt, UefiMemoryBase, UefiMemorySize);",
        "  ChironReservePayload(Fdt, UefiMemoryBase, UefiMemorySize);\n"
        "  ChironReserveKernelWindow(Fdt, UefiMemoryBase, UefiMemorySize);",
    )
    entry.write_text(text)
    files = [dsc, entry, memory / "ChironMemoryWindow.h", memory / "ChironReserveWindow.inc"]
    return {
        "schema": "chiron-owned-memory-window-v1",
        "early_system_memory_base": "0x80000000",
        "early_system_memory_size": "0x60000000",
        "early_uefi_heap_base": "0xde000000",
        "early_uefi_heap_bytes": "0x2000000",
        "kernel_window_base": "0x100000000",
        "kernel_window_bytes": "0xbc000000",
        "allocation_type": "EfiLoaderData; PEI allocation HOB before DXE",
        "hardware_qualified": False,
        "files": {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in files},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = apply(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
