#!/usr/bin/env python3
"""Decode the versioned source-observer wire buffer without interpreting its text."""

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

MAGIC = 0x434849524f4e5431
SIZE = 65536
TEXT_OFFSET, TEXT_BYTES = 280, 32768
EVENT_OFFSET, EVENT_BYTES, EVENT_COUNT = 33048, 96, 256


def decode(data, symbols=None):
    if len(data) != SIZE:
        raise ValueError("source trace must be exactly 65536 bytes")
    magic, version, size = struct.unpack_from("<QII", data)
    if (magic, version, size) != (MAGIC, 1, SIZE):
        raise ValueError("unknown or uninitialized source trace")
    sequence, early, boot_args, root, virtual, physical = struct.unpack_from("<6Q", data, 16)
    fault, reentries = struct.unpack_from("<II", data, 96)
    pc, esr, far, sp, spsr, sctlr, tcr = struct.unpack_from("<7Q", data, 104)
    mode, dropped, write, used = struct.unpack_from("<4I", data, 168)
    if write >= TEXT_BYTES or used > TEXT_BYTES:
        raise ValueError("invalid text ring bounds")
    build = data[184:280].split(b"\0", 1)[0].decode("ascii", errors="strict")
    if not build.startswith("CT-"):
        raise ValueError("missing numbered source build identity")
    ring = data[TEXT_OFFSET:TEXT_OFFSET + TEXT_BYTES]
    start = (write - used) % TEXT_BYTES
    text = (ring[start:] + ring[:start])[:used].decode("utf-8", errors="replace")
    count, event_dropped = struct.unpack_from("<II", data, 57624)
    panic_caller, exceptions, panics = struct.unpack_from("<QII", data, 57696)
    paints, native_paints, refused = struct.unpack_from("<3Q", data, 57712)
    native_vectors, early_depth, early_slot, nested_pc, nested_esr, nested_far = struct.unpack_from("<QII3Q", data, 57736)
    events = []
    first = max(0, count - EVENT_COUNT)
    for index in range(first, count):
        offset = EVENT_OFFSET + index % EVENT_COUNT * EVENT_BYTES
        number = struct.unpack_from("<Q", data, offset)[0]
        message = data[offset + 8:offset + EVENT_BYTES].split(b"\0", 1)[0].decode(errors="replace")
        events.append({"sequence": number, "text": message})
    if any(a["sequence"] >= b["sequence"] for a, b in zip(events, events[1:])):
        raise ValueError("nonmonotonic source event journal")
    if events and events[-1]["sequence"] != sequence:
        raise ValueError("source sequence does not match the event journal")
    result = {
        "schema": 1, "wire_sha256": hashlib.sha256(data).hexdigest(), "build_id": build,
        "sequence": sequence, "early_stage": early,
        "early_pc": [hex(value) for value in struct.unpack_from("<8Q", data, 57632)],
        "boot_args": hex(boot_args), "bootstrap_root": hex(root),
        "kernel_virtual_base": hex(virtual), "kernel_physical_base": hex(physical),
        "mapping_mode": mode, "text_dropped": dropped, "events_dropped": event_dropped,
        "reentries": reentries, "events": events, "console": text,
        "exception_count": exceptions, "panic_count": panics,
        "panic_caller": hex(panic_caller),
        "paints": paints, "native_paints": native_paints, "render_refused": refused,
        "native_vectors": hex(native_vectors),
        "early_fault_depth": early_depth, "early_fault_slot": early_slot,
        "nested_fault": {"pc": hex(nested_pc), "esr": hex(nested_esr), "far": hex(nested_far)},
        "fault": {"kind": fault, "pc": hex(pc), "esr": hex(esr), "far": hex(far),
                  "sp": hex(sp), "spsr": hex(spsr), "sctlr": hex(sctlr), "tcr": hex(tcr)},
    }
    result["fault"]["registers_valid"] = exceptions > 0
    if symbols and (pc or panic_caller):
        entries = []
        for line in Path(symbols).read_text().splitlines():
            match = re.fullmatch(r"([0-9a-fA-F]+)\s+\S\s+(\S+)", line)
            if match:
                entries.append((int(match[1], 16), match[2]))
        lookup = pc
        if physical <= pc < physical + 0x40000000 and (spsr & 15) in (4, 5):
            lookup = pc - physical + virtual
            result["fault"]["pc_normalized_from_physical"] = hex(lookup)
        prior = [entry for entry in entries if entry[0] <= lookup]
        if prior:
            address, name = max(prior)
            if lookup - address < 0x100000:
                result["fault"]["nearest_symbol"] = name
                result["fault"]["symbol_offset"] = hex(lookup - address)
        if panic_caller:
            prior = [entry for entry in entries if entry[0] <= panic_caller]
            if prior:
                address, name = max(prior)
                if panic_caller - address < 0x100000:
                    result["panic_symbol"] = name
                    result["panic_symbol_offset"] = hex(panic_caller - address)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", type=Path)
    args = parser.parse_args()
    result = decode(args.input.read_bytes(), args.symbols)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    args.output.with_suffix(".txt").write_text(
        result["build_id"] + "\n" + result["console"] + "\n" +
        json.dumps(result["fault"], indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
