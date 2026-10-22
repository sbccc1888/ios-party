#!/usr/bin/env python3
"""Inspect the source kernel and add an explicit, integrity-preserving entry witness."""

import hashlib
import hmac
import json
import shlex
import struct
import subprocess
import tomllib
from pathlib import Path

from build_chiron_entry_probe import ROOT, read_aarch64_object, sha256

SOURCE_KERNEL_SHA = "812eb05da0c93556674489ba706868fc2b5208fd66b4a74cd27f282cb4f34de2"


class MachKernel:
    def __init__(self, data):
        self.data = bytes(data)
        if len(data) < 32 or struct.unpack_from("<4I", data) != (0xfeedfacf, 0x100000c, 0, 2):
            raise ValueError("expected an ordinary ARM64 MH_EXECUTE kernel")
        count, size = struct.unpack_from("<II", data, 16)
        end = 32 + size
        if end > len(data) or not 0 < count <= 512:
            raise ValueError("invalid Mach-O command table")
        self.segments, self.sections = [], []
        self.entry_field = None
        offset = 32
        for _ in range(count):
            if offset + 8 > end:
                raise ValueError("truncated Mach-O command")
            cmd, length = struct.unpack_from("<II", data, offset)
            if length < 8 or length % 8 or offset + length > end:
                raise ValueError("invalid Mach-O command length")
            if cmd == 0x1d:
                raise ValueError("signed kernel modification is unsupported")
            if cmd == 0x19:
                if length < 72:
                    raise ValueError("truncated segment")
                name = data[offset + 8:offset + 24].rstrip(b"\0").decode("ascii")
                va, vs, fo, fs = struct.unpack_from("<4Q", data, offset + 24)
                prot, nsects = struct.unpack_from("<II", data, offset + 60)
                if nsects > (length - 72) // 80 or fs > vs or fo + fs > len(data):
                    raise ValueError("invalid segment dimensions")
                segment = {"name": name, "address": va, "size": vs,
                           "file_offset": fo, "file_bytes": fs, "protection": prot, "sections": []}
                self.segments.append(segment)
                for index in range(nsects):
                    s = offset + 72 + index * 80
                    section_name = data[s:s + 16].rstrip(b"\0").decode("ascii")
                    address, nbytes, position = struct.unpack_from("<QQI", data, s + 32)
                    flags = struct.unpack_from("<I", data, s + 64)[0]
                    if flags & 255 not in (1, 12, 18) and nbytes and position + nbytes > len(data):
                        raise ValueError("section outside kernel file")
                    item = {"name": section_name, "segment": name, "address": address,
                            "bytes": nbytes, "offset": position, "flags": flags}
                    segment["sections"].append(item)
                    self.sections.append(item)
            elif cmd == 5:
                if self.entry_field is not None or length != 288 or struct.unpack_from("<II", data, offset + 8) != (6, 68):
                    raise ValueError("unexpected kernel thread-state entry")
                self.entry_field = offset + 272
            offset += length
        if offset != end or self.entry_field is None:
            raise ValueError("missing kernel entry or incomplete load commands")
        loaded = [s for s in self.segments if s["size"]]
        self.base = min(s["address"] for s in loaded)
        self.entry = struct.unpack_from("<Q", data, self.entry_field)[0]
        self.file_offset(self.entry, executable=True)

    def file_offset(self, address, executable=False):
        matches = [s for s in self.segments if s["address"] <= address < s["address"] + s["file_bytes"]
                   and (not executable or s["protection"] & 4)]
        if len(matches) != 1:
            raise ValueError("address is not in a unique file-backed segment")
        s = matches[0]
        return s["file_offset"] + address - s["address"]

    def integrity(self):
        code = next((s for s in self.sections if s["name"] == "__text"
                     and s["segment"] in ("__TEXT", "__TEXT_EXEC")), None)
        fields = [s for s in self.sections if s["name"] == "__fips_hmacs"]
        if code is None or len(fields) != 1 or fields[0]["bytes"] != 32:
            raise ValueError("missing source-provider POST fields")
        if code["address"] - self.base != code["offset"]:
            raise ValueError("POST runtime/file text mapping differs")
        field = fields[0]
        if code["offset"] <= field["offset"] < code["offset"] + code["bytes"]:
            raise ValueError("POST HMAC field overlaps its code")
        stored = self.data[field["offset"]:field["offset"] + 32]
        computed = hmac.digest(b"\0", self.data[code["offset"]:code["offset"] + code["bytes"]], "sha256")
        if not hmac.compare_digest(stored, computed):
            raise ValueError("source-provider POST integrity does not match the text")
        return code, field, stored

    def padding(self, minimum):
        for segment in self.segments:
            if segment["name"] != "__TEXT_EXEC" or not segment["protection"] & 4:
                continue
            start = segment["file_offset"]
            for section in segment["sections"]:
                if section["bytes"] and section["flags"] & 255 not in (1, 12, 18):
                    start = max(start, section["offset"] + section["bytes"])
            start = (start + 15) & ~15
            end = segment["file_offset"] + segment["file_bytes"]
            if end - start >= minimum and not any(self.data[start:end]):
                return start, end, segment["address"] + start - segment["file_offset"]
        raise ValueError("no unused zero-filled executable padding")


def patch_kernel(data, directory, clang="clang", expected_sha=SOURCE_KERNEL_SHA):
    if sha256(data) != expected_sha:
        raise ValueError("unexpected source kernel fingerprint")
    before = MachKernel(data)
    code, field, stored = before.integrity()
    entry_offset = before.file_offset(before.entry, executable=True)
    instruction = struct.unpack_from("<I", data, entry_offset)[0]
    if instruction & 0xfc000000 != 0x14000000:
        raise ValueError("original kernel entry does not begin with the expected branch")
    immediate = instruction & 0x03ffffff
    if immediate & (1 << 25):
        immediate -= 1 << 26
    original_target = before.entry + immediate * 4
    before.file_offset(original_target, executable=True)
    start, end, address = before.padding(6144)
    obj = directory / "kernel-witness.o"
    delta = before.entry - address
    subprocess.run([clang, "--target=aarch64-none-elf", "-DCHIRON_KERNEL_WITNESS",
                    "-DCHIRON_ENTRY_DELTA=" + str(delta), "-c",
                    str(ROOT / "scripts/chiron-entry-probe.S"), "-o", str(obj)], check=True)
    program, symbols = read_aarch64_object(obj.read_bytes())
    if symbols.get("chiron_entry_probe") != 0 or "ChironWitnessOriginalBranch" not in symbols:
        raise ValueError("unexpected witness entry or continuation")
    stack_top = symbols.get("ChironWitnessStackTop")
    if stack_top is None or (address + stack_top) % 16:
        raise ValueError("kernel witness stack is not 16-byte aligned")
    if len(program) > end - start:
        raise ValueError("kernel witness does not fit unused padding")
    modified = bytearray(data)
    modified[start:start + len(program)] = program
    struct.pack_into("<Q", modified, before.entry_field, address)
    after = MachKernel(modified)
    _, _, new_stored = after.integrity()
    if (
        modified[code["offset"]:code["offset"] + code["bytes"]]
        != data[code["offset"]:code["offset"] + code["bytes"]]
        or new_stored != stored
        or modified[entry_offset:entry_offset + 16] != data[entry_offset:entry_offset + 16]
    ):
        raise ValueError("original code, entry words or POST HMAC changed")
    allowed = set(range(start, start + len(program))) | set(range(before.entry_field, before.entry_field + 8))
    if any(index not in allowed for index, (a, b) in enumerate(zip(data, modified)) if a != b):
        raise ValueError("kernel patch exceeded declared metadata/padding ranges")
    return bytes(modified), {
        "source_kernel_sha256": expected_sha, "diagnostic_kernel_sha256": sha256(modified),
        "kernel_file_length_unchanged": len(modified) == len(data),
        "kernel_base": before.base, "original_entry": before.entry, "original_branch_target": original_target,
        "witness_entry": address, "witness_file_offset": start, "witness_bytes": len(program),
        "witness_resume_offset": symbols["ChironWitnessOriginalBranch"],
        "witness_stack_top_offset": stack_top, "witness_stack_16byte_aligned": True,
        "load_command_entry_field": before.entry_field,
        "original_entry_words_unchanged": True, "original_text_unchanged": True,
        "post_hmac_field_unchanged": True, "post_hmac_matches_text": True,
        "post_hmac": stored.hex(), "post_code_file_offset": code["offset"],
        "post_code_bytes": code["bytes"], "post_hmac_file_offset": field["offset"],
        "limits": [
            "Witness code lies outside the original POST text range; that POST does not qualify the witness.",
            "White/text proves diagnostic kernel-image entry, not original XNU initialization or PID 1.",
            "x1 is restored to the normal original-entry PA expected by the unmodified handoff.",
            "Rendering is guarded to the observed EL1, MMU/cache-off, 1080x2160 GOP layout.",
        ],
    }


def console_config(data):
    original = tomllib.loads(data.decode("utf-8"))
    if set(original) != {"version", "boot", "kits", "entry"}:
        raise ValueError("unexpected RAM boot configuration structure")
    expected = tomllib.loads(data.decode("utf-8"))
    entries = [entry for entry in expected["entry"] if entry.get("type") == "xnu"]
    if len(entries) != 1:
        raise ValueError("expected one XNU boot entry")
    entry = entries[0]
    tokens = shlex.split(entry["args"])
    if tokens.count("serial=3") != 1 or any(token.startswith("startup_debug=") for token in tokens):
        raise ValueError("unexpected source console arguments")
    tokens[tokens.index("serial=3")] = "serial=0"
    for token in ("-enable_kprintf_spam", "startup_debug=1"):
        if token not in tokens:
            tokens.append(token)
    if any(any(char.isspace() for char in token) for token in tokens):
        raise ValueError("quoted boot arguments are unsupported")
    entry["args"] = " ".join(tokens)
    entry["title"] = "Chiron XNU log"

    def scalar(value):
        if not isinstance(value, (str, int, bool)):
            raise ValueError("unsupported RAM config value")
        return json.dumps(value, ensure_ascii=True)

    lines = ["version=" + scalar(expected["version"])]
    for name in ("boot", "kits"):
        lines.append("[" + name + "]")
        lines.extend(key + "=" + scalar(value) for key, value in expected[name].items())
    for entry in expected["entry"]:
        lines.append("[[entry]]")
        lines.extend(key + "=" + scalar(value) for key, value in entry.items())
    result = ("\n".join(lines) + "\n").encode()
    if len(result) > len(data):
        raise ValueError("verbose config will not fit its existing file allocation")
    result = result.ljust(len(data), b" ")
    if tomllib.loads(result.decode()) != expected:
        raise ValueError("console config did not round-trip")
    old_entry = next(item for item in original["entry"] if item.get("type") == "xnu")
    return result, {"old_arguments": old_entry["args"], "new_arguments": entries[0]["args"],
                    "file_bytes": len(result), "title": entries[0]["title"]}
