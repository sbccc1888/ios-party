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
    if normal["console"].count("CHIRON RAM init: PID 1, 4K ABI, heartbeat") < 2:
        raise ValueError("the actual PID 1 console writes were not captured twice")
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
        ("console", kernel, "", "_chiron_trace_console_checkpoint"),
        ("panic", args.tree / "build/corecrypto-2023/negative-hmac/kernel.macho",
         "", "_chiron_trace_fault_checkpoint"),
        ("data-abort", kernel, "chiron-trace-test=1", "_chiron_trace_fault_checkpoint"),
    )
    reports = {}
    console_proof = None
    for name, image, extra, checkpoint in scenarios:
        destination = args.tree / ("build/chiron-source-" + name)
        completed = subprocess.run([
            sys.executable, args.tree / "scripts/probe-darwinboot-xnu.py",
            "--kernel", image, "--output", destination, "--timeout", "180",
            "--break-symbol", checkpoint, "--kernel-extra-args", extra,
            "--qemu-ramfb", "--capture-qemu-screen",
        ], cwd=args.tree, check=False)
        capture = args.output / (name + "-capture")
        capture.mkdir(parents=True, exist_ok=True)
        for filename in ("chiron-trace.bin", "gdb-stop.json", "gdb-failure.json",
                         "gdb-stderr.log", "gdb-uart.log", "screen.png"):
            path = destination / filename
            if path.exists():
                shutil.copyfile(path, capture / filename)
        if completed.returncode:
            if (capture / "chiron-trace.bin").exists():
                try:
                    partial = decoder.decode((capture / "chiron-trace.bin").read_bytes(), symbols)
                    (capture / "decoded.json").write_text(json.dumps(partial, indent=2) + "\n")
                    (capture / "console.txt").write_text(partial["console"])
                except ValueError as error:
                    (capture / "decode-error.txt").write_text(str(error) + "\n")
            raise RuntimeError(f"{name} observation failed; raw evidence preserved in {capture}")
        observed = decoder.decode((destination / "chiron-trace.bin").read_bytes(), symbols)
        if observed["build_id"] != build["build_id"]:
            raise ValueError("fault capture belongs to another source build")
        if name == "console":
            console_proof = verify_console_capture(observed)
        elif name == "panic":
            if (observed["fault"]["kind"] != 2 or not int(observed["panic_caller"], 16)
                    or "FIPS Kernel POST Failed" not in observed["console"]):
                raise ValueError("real POST panic was not captured before native debugger handling")
        else:
            if observed["fault"]["kind"] != 1 or int(observed["fault"]["far"], 16) != 0x1000:
                raise ValueError("real data-abort FAR was not captured")
            if (observed["fault"].get("nearest_symbol") != "_chiron_trace_test"
                    or not any("explicit diagnostic data-abort injection requested" in event["text"]
                               for event in observed["events"])):
                raise ValueError("data abort must originate from the requested source injection")
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
        "console_initialization": console_proof,
        "faults": reports, "hardware_observed": False,
    }, indent=2) + "\n")


def verify_console_capture(observed):
    console = observed["console"]
    required = (
        "panic region uses device tree",
        "panic DT region absent; no memory accessed",
        "check_for_panic_log returned",
        "screen map enter",
        "screen map returned",
        "gc_initialize returned",
        "screen init returned",
        "PE_create_console() returned",
    )
    if observed["panic_count"] or "panic fixed board region" in console:
        raise ValueError("console must not panic or access a foreign board's fixed panic region")
    position = -1
    for message in required:
        position = console.find(message, position + 1)
        if position < 0:
            raise ValueError(f"console initialization evidence missing or out of order: {message}")
    return {"device_tree_panic_path": True, "absent_region_skipped": True,
            "framebuffer_map_returned": True, "console_returned": True}


if __name__ == "__main__":
    main()
