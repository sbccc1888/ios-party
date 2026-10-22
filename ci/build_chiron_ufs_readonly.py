#!/usr/bin/env python3
"""Compile only the opt-in Chiron UFS archive using an existing kernel compile record."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "drivers/ChironUfsReadonly"


def compile_arguments(record, source, obj):
    arguments = []
    replaced = False
    original = iter(record["arguments"])
    for item in original:
        if item == record["file"]:
            arguments.append(str(source))
            replaced = True
        elif item in ("-o", "-MJ", "-MF", "-serialize-diagnostics", "--serialize-diagnostics"):
            next(original)
            if item in ("-o", "-MJ"):
                arguments.extend([item, str(obj if item == "-o" else obj.with_suffix(".compile.json"))])
        else:
            arguments.append(item)
    if not replaced or "-o" not in arguments:
        raise ValueError("compile record lacks source or output")
    return arguments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--upstream", type=Path, help="pinned source checkout if drivers are not in the assembled tree")
    args = parser.parse_args()
    tree = args.tree.resolve()
    upstream = (args.upstream or tree).resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "driver-report.json").unlink(missing_ok=True)
    pin = json.loads((DRIVER / "upstream.json").read_text())
    for relative, digest in pin["files"].items():
        # prepare_runtime.py legitimately adapts the build recipe. Pin the
        # inspected driver, without rejecting the parent's integration changes.
        if not relative.startswith("drivers/NativeUfs/"):
            continue
        if hashlib.sha256((upstream / relative).read_bytes()).hexdigest() != digest:
            raise SystemExit("unexpected upstream source: " + relative)
    record_file = tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOCPU.cpo.json"
    record = json.loads(record_file.read_text().strip().rstrip(","))
    source = DRIVER / "ChironUfsReadonly.cpp"
    obj = output / "ChironUfsReadonly.o"
    arguments = compile_arguments(record, source, obj)
    result = subprocess.run(arguments, cwd=record["directory"], capture_output=True)
    (output / "compile.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit((result.stdout + result.stderr).decode("utf-8", "replace"))
    if struct.unpack_from("<4I", obj.read_bytes()) != (0xFEEDFACF, 0x100000C, 0, 1):
        raise SystemExit("expected ordinary ARM64 Mach-O object")
    archive = output / "libChironUfsReadonly.a"
    subprocess.run(["xcrun", "ar", "rcs", str(archive), str(obj)], check=True)
    symbols = subprocess.check_output(["xcrun", "nm", "-g", str(obj)], text=True)
    if not any(line.split()[-2:] == ["T", "_chiron_ufs_readonly_probe"]
               for line in symbols.splitlines()):
        raise SystemExit("probe entry not defined by driver object")
    report = {
        "archive": archive.name, "entry": "chiron_ufs_readonly_probe",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(DRIVER.iterdir()) if p.is_file()
        },
        "compile_record": str(record_file.relative_to(tree)),
        "upstream_commit": pin["unixnu_commit"],
        "linked_into_kernel": False, "hardware_executed": False,
        "board_handoff_qualified": False,
    }
    (output / "driver-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
