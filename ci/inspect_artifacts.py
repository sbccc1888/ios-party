#!/usr/bin/env python3
"""Validate produced AArch64 EFI/Mach-O files before retaining build artifacts."""

import argparse
import hashlib
import json
import os
import struct
from pathlib import Path


def inspect(path):
    data = path.read_bytes()
    result = {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if path.suffix == ".efi":
        if len(data) < 64 or data[:2] != b"MZ":
            raise ValueError(f"{path}: missing DOS header")
        pe = struct.unpack_from("<I", data, 60)[0]
        if pe + 94 > len(data) or data[pe:pe + 4] != b"PE\0\0":
            raise ValueError(f"{path}: invalid PE header")
        machine = struct.unpack_from("<H", data, pe + 4)[0]
        kind = struct.unpack_from("<H", data, pe + 24 + 68)[0]
        if machine != 0xAA64 or kind not in (10, 11, 12):
            raise ValueError(f"{path}: wrong UEFI machine or subsystem")
        result.update(format="PE32+", machine="AArch64", subsystem=kind)
    elif path.name == "kernel.source.unstripped":
        if len(data) < 32 or struct.unpack_from("<4I", data) != (0xFEEDFACF, 0x100000C, 0, 2):
            raise ValueError(f"{path}: expected ordinary ARM64 executable Mach-O")
        result.update(format="Mach-O", architecture="arm64", prelinked=False, hardware_boot_qualified=False)
    else:
        return None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    files = [result for path in sorted(args.directory.iterdir()) if path.is_file() and (result := inspect(path))]
    if not files:
        raise SystemExit("no EFI or source kernel artifacts found")
    (args.directory / "artifact-report.json").write_text(json.dumps({
        "artifacts": files, "hardware_executed": False,
        "device_commit": os.environ.get("GITHUB_SHA"),
        "stage": "compiler validation; not a complete XNU boot payload",
    }, indent=2) + "\n")
    print(json.dumps(files, indent=2))


if __name__ == "__main__":
    main()
