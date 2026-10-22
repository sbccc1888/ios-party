#!/usr/bin/env python3
"""Require actual QEMU panic/data-abort snapshots from the numbered source kernel."""

import argparse
import importlib.util
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def decode_module(root):
    spec = importlib.util.spec_from_file_location("source_trace_decoder", root / "tools/decode_source_trace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    decoder = decode_module(root)
    args.output.mkdir(parents=True, exist_ok=True)
    symbols = root / "dist/ram-init/kernel-symbols.txt"
    build = json.loads((root / "dist/ram-init/source-trace-build.json").read_text())
    normal = decoder.decode((args.tree / "build/chiron-qemu-init/chiron-trace.bin").read_bytes(), symbols)
    if normal["build_id"] != build["build_id"] or normal["early_stage"] != 4:
        raise ValueError("normal QEMU run did not execute all four source assembly stages")
    if not all(int(value, 16) for value in normal["early_pc"][:4]):
        raise ValueError("normal source entry/MMU PCs are missing")
    if normal["panic_count"]:
        raise ValueError("normal init run contains a panic")
    if normal["mapping_mode"] != 2 or not normal["native_paints"] or not any(
        "load_init_program" in event["text"] for event in normal["events"]
    ):
        raise ValueError("native source mapping/init path was not observed")
    (args.output / "normal.json").write_text(json.dumps(normal, indent=2) + "\n")
    (args.output / "normal.txt").write_text(normal["console"])
    shutil.copyfile(args.tree / "build/chiron-qemu-init/screen.png", args.output / "normal.png")
    kernel = args.tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
    kernel_sha256 = hashlib.sha256(kernel.read_bytes()).hexdigest()
    qualification = json.loads((root / "dist/ram-init/runtime-test.json").read_text())
    if qualification["kernel_sha256"] != kernel_sha256:
        raise ValueError("source observer tests must qualify the same normal runtime kernel")
    scenarios = (
        ("panic", args.tree / "build/corecrypto-2023/negative-hmac/kernel.macho", ""),
        ("data-abort", kernel, "chiron-trace-test=1"),
    )
    reports = {}
    for name, image, extra in scenarios:
        destination = args.tree / ("build/chiron-source-" + name)
        subprocess.run([
            sys.executable, args.tree / "scripts/probe-darwinboot-xnu.py",
            "--kernel", image, "--output", destination, "--timeout", "180",
            "--break-symbol", "_chiron_trace_fault_checkpoint", "--kernel-extra-args", extra,
            "--qemu-ramfb", "--capture-qemu-screen",
        ], cwd=args.tree, check=True)
        observed = decoder.decode((destination / "chiron-trace.bin").read_bytes(), symbols)
        if observed["build_id"] != build["build_id"]:
            raise ValueError("fault capture belongs to another source build")
        if name == "panic":
            if (observed["fault"]["kind"] != 2 or not int(observed["panic_caller"], 16)
                    or "FIPS Kernel POST Failed" not in observed["console"]):
                raise ValueError("real POST panic was not captured before native debugger handling")
        else:
            if observed["fault"]["kind"] != 1 or int(observed["fault"]["far"], 16) != 0x1000:
                raise ValueError("real data-abort FAR was not captured")
            if not int(observed["fault"]["pc"], 16) or int(observed["fault"]["esr"], 16) >> 26 not in (0x24, 0x25):
                raise ValueError("data-abort PC/ESR is missing or wrong")
        reports[name] = observed["fault"]
        (args.output / (name + ".json")).write_text(json.dumps(observed, indent=2) + "\n")
        (args.output / (name + ".txt")).write_text(observed["console"])
        shutil.copyfile(destination / "screen.png", args.output / (name + ".png"))
    (args.output / "verification.json").write_text(json.dumps({
        "schema": 1, "build_id": build["build_id"], "qemu_only": True,
        "kernel_sha256": kernel_sha256,
        "source_entry_and_mmu_observed": True, "native_framebuffer_mapping_observed": True,
        "panic_reason_observed": True, "data_abort_pc_esr_far_observed": True,
        "faults": reports, "hardware_observed": False,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
