#!/usr/bin/env python3
"""Compile native drivers using the exact bootstrap kernel compile records."""

import argparse
import hashlib
import json
import struct
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tree = args.tree.resolve()
    record_file = tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOCPU.cpo.json"
    record = json.loads(record_file.read_text().strip().rstrip(","))
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for name in ("ArmDTPlatform", "ArmCpu"):
        source = tree / f"drivers/{name}/{name}.cpp"
        obj = args.output.resolve() / (name + ".o")
        arguments, replaced = [], False
        original = iter(record["arguments"])
        for item in original:
            if item == record["file"]:
                arguments.append(str(source))
                replaced = True
            elif item == "-o":
                next(original)
                arguments.extend(["-o", str(obj)])
            elif item in ("-MF", "-serialize-diagnostics", "--serialize-diagnostics"):
                next(original)
            else:
                arguments.append(item)
        if not replaced:
            raise SystemExit("compile record does not identify its source argument")
        result = subprocess.run(arguments, cwd=record["directory"], capture_output=True)
        (args.output / f"{name}.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise SystemExit((result.stdout + result.stderr).decode("utf-8", "replace"))
        if struct.unpack_from("<4I", obj.read_bytes()) != (0xFEEDFACF, 0x100000C, 0, 1):
            raise SystemExit("driver object is not ordinary arm64 Mach-O")
        archive = args.output.resolve() / f"lib{name}.a"
        subprocess.run(["xcrun", "ar", "rcs", str(archive), str(obj)], check=True)
        reports.append({
            "driver": name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "compile_record": str(record_file.relative_to(tree)),
            "linked_into_kernel": False, "hardware_executed": False,
        })
    (args.output / "driver-report.json").write_text(json.dumps(reports, indent=2) + "\n")


if __name__ == "__main__":
    main()
