#!/usr/bin/env python3
"""Number a qualified cloud-built source runtime using the frozen RAM transport."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = args.output / "full-ram"
    transport = args.output / "transport"
    if args.output.exists():
        raise ValueError("output directory already exists")
    args.output.mkdir(parents=True)
    subprocess.run([
        sys.executable, root / "ci/build_ram_boot.py", "--runtime", args.runtime,
        "--firmware", args.firmware, "--output", source,
    ], check=True)
    subprocess.run([
        sys.executable, root / "tools/build_chiron_zero_tail_boot.py",
        "--source", source / "chiron-ram-init-experimental.img",
        "--output", transport, "--source-runtime", args.runtime,
    ], check=True)
    build = json.loads((args.runtime / "source-trace-build.json").read_text())
    proof = json.loads((args.runtime / "source-trace-tests/verification.json").read_text())
    image_name = build["build_id"] + ".img"
    shutil.copyfile(transport / "chiron-ram-init-zero-tail.img", args.output / image_name)
    symbols = args.runtime / "kernel-symbols.txt"
    shutil.copyfile(symbols, args.output / symbols.name)
    for origin, report_name in (
        (transport / "zero-tail-report.json", "transport-verification.json"),
        (source / "ram-transport-report.json", "fat-verification.json"),
    ):
        shutil.copyfile(origin, args.output / report_name)
    manifest = {
        "schema": 1, "build_id": build["build_id"],
        "run_id": build["run_id"], "source_commit": build["source_commit"],
        "packaging_run_id": os.environ.get("GITHUB_RUN_ID"),
        "packaging_source_commit": os.environ.get("GITHUB_SHA"),
        "efi_run_id": os.environ.get("CHIRON_EFI_RUN"),
        "firmware_run_id": os.environ.get("CHIRON_FIRMWARE_RUN"),
        "image": image_name,
        "image_sha256": hashlib.sha256((args.output / image_name).read_bytes()).hexdigest(),
        "image_bytes": (args.output / image_name).stat().st_size,
        "kernel_binary_instruction_patching": False, "qemu_source_trace_proof": proof,
        "baseline": json.loads((root / "diagnostics/baseline.json").read_text()),
        "upstream_sources": json.loads((root / "ci/sources.json").read_text()),
        "source_observer": build,
        "overlay": json.loads((args.runtime / "chiron-source-overlay.json").read_text()),
        "hardware_tested": False, "failure_cause_identified": False,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copytree(args.runtime / "source-trace-tests", args.output / "qemu-logs")
    shutil.copyfile(args.runtime / "chiron-trace.bin", args.output / "qemu-logs/normal.bin")
    shutil.copytree(args.runtime / "source-trace-captures", args.output / "qemu-logs/preflight-captures")
    shutil.copytree(args.runtime / "kernel.dSYM", args.output / "kernel.dSYM")
    shutil.copytree(args.runtime / "observer-source", args.output / "observer-source")
    print(json.dumps({"image": image_name, "sha256": manifest["image_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
