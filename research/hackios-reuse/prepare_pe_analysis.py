#!/usr/bin/env python3
"""Prepare an offline-only vendor PE copy for LLVM's stricter header reader."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = bytearray(args.source.read_bytes())
    if data[:2] != b"MZ":
        raise ValueError("expected PE DOS header")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("expected PE signature")
    machine, sections = struct.unpack_from("<HH", data, pe + 4)
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    if machine != 0xAA64 or struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise ValueError("expected AArch64 PE32+")
    if optional_size < 168 or optional + optional_size + sections * 40 > len(data):
        raise ValueError("invalid PE header bounds")
    debug = optional + 112 + 6 * 8
    old = bytes(data[debug:debug + 8])
    # Only the analysis copy drops the malformed debug directory reference.
    data[debug:debug + 8] = bytes(8)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(data)
    print(json.dumps({
        "source": str(args.source.resolve()),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "analysis_copy": str(args.output.resolve()),
        "analysis_sha256": hashlib.sha256(data).hexdigest(),
        "debug_directory_offset": debug,
        "original_debug_directory": old.hex(),
        "scope": "offline disassembly only; never a boot or packaging input",
    }, indent=2))


if __name__ == "__main__":
    main()
