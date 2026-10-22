#!/usr/bin/env python3
"""Instrument only the pinned DarwinBoot handoff, preserving the zero-tail RAM transport."""

import argparse
import gzip
import hashlib
import json
import lzma
import struct
import subprocess
import tempfile
import uuid
import zlib
from pathlib import Path

from build_chiron_entry_probe import ROOT, read_aarch64_object, sha256
from inspect_chiron_boot import inspect
from make_chiron_firmware_control import read_inspected

SOURCE_IMAGE_SHA = "14c51d4478e7e5e7437f5d444f919b3ec23a337d10d6259dd185e08c9d28aaa9"
SOURCE_PE_SHA = "a74961df659b638b0ea458e44314ab2ca84b27074c29c56ccbdb9c433fb20edb"
DARWIN_GUID = uuid.UUID("1b64af87-8415-4b57-9b16-680d70913b9f").bytes_le
LZMA_GUID = uuid.UUID("ee4e5898-3914-4259-9d6e-dc7bd79403cf").bytes_le


def align(value, boundary):
    return (value + boundary - 1) // boundary * boundary


def compile_handoff(directory, clang="clang"):
    obj = directory / "handoff.o"
    subprocess.run([clang, "--target=aarch64-none-elf", "-c",
                    str(ROOT / "scripts/chiron-handoff-stages.S"), "-o", str(obj)], check=True)
    code, symbols = read_aarch64_object(obj.read_bytes())
    if symbols.get("DarwinEnter") != 0:
        raise ValueError("handoff does not begin at .text offset zero")
    stages = {name: symbols[name] for name in (
        "ChironHandoffCacheLoop", "ChironHandoffCacheDone",
        "ChironHandoffMmuOff", "ChironHandoffKernelBranch",
    )}
    if any(offset % 4 or not 0 <= offset < len(code) for offset in stages.values()):
        raise ValueError("invalid stage symbol")
    return code, stages


def patch_pe(data, code, expected_sha=SOURCE_PE_SHA):
    if sha256(data) != expected_sha or len(data) < 256 or data[:2] != b"MZ":
        raise ValueError("unexpected DarwinBoot PE fingerprint")
    pe = struct.unpack_from("<I", data, 60)[0]
    if pe + 24 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("invalid PE header")
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    opt_bytes = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    if machine != 0xaa64 or opt_bytes < 152 or opt + opt_bytes + count * 40 > len(data):
        raise ValueError("unexpected PE architecture or dimensions")
    if struct.unpack_from("<H", data, opt)[0] != 0x20b:
        raise ValueError("expected PE32+")
    if struct.unpack_from("<I", data, opt + 64)[0]:
        raise ValueError("nonzero optional PE checksum is unsupported")
    if any(struct.unpack_from("<II", data, opt + 112 + 4 * 8)):
        raise ValueError("signed PE security directory is unsupported")
    sections = []
    for index in range(count):
        header = opt + opt_bytes + index * 40
        name = data[header:header + 8].rstrip(b"\0")
        virtual_size, rva, raw_size, raw = struct.unpack_from("<4I", data, header + 8)
        if raw + raw_size > len(data):
            raise ValueError("PE section outside file")
        sections.append((name, header, virtual_size, rva, raw_size, raw))
    matches = [section for section in sections if section[0] == b".text"]
    if len(matches) != 1:
        raise ValueError("expected one PE code section")
    _, header, virtual_size, rva, raw_size, raw = matches[0]
    if virtual_size < 148 or virtual_size > raw_size:
        raise ValueError("unexpected PE code bounds")
    start = raw + virtual_size - 148
    original = data[start:start + 148]
    if original[:4] != bytes.fromhex("df4f03d5") or original[-4:] != bytes.fromhex("20001fd6"):
        raise ValueError("pinned DarwinEnter is not the final code function")
    padding = data[raw + virtual_size:raw + raw_size]
    if padding not in (b"\0" * len(padding), b"\xcc" * len(padding)):
        raise ValueError("code section tail is not uniform linker padding")
    new_size = virtual_size - 148 + len(code)
    if len(code) < 148 or len(code) % 4 or new_size > raw_size:
        raise ValueError("handoff does not fit the existing code section")
    next_rvas = [section[3] for section in sections if section[3] > rva]
    if next_rvas and rva + new_size > min(next_rvas):
        raise ValueError("expanded code would overlap the next section")
    result = bytearray(data)
    result[start:start + len(code)] = code
    struct.pack_into("<I", result, header + 8, new_size)
    allowed = set(range(start, start + len(code))) | set(range(header + 8, header + 12))
    if any(index not in allowed for index, (a, b) in enumerate(zip(data, result)) if a != b):
        raise ValueError("PE patch exceeded its declared ranges")
    return bytes(result), {
        "source_pe_sha256": expected_sha, "patched_pe_sha256": sha256(result),
        "function_file_offset": start, "function_rva": rva + start - raw,
        "old_function_bytes": 148, "new_function_bytes": len(code),
        "old_text_virtual_size": virtual_size, "new_text_virtual_size": new_size,
        "pe_file_length_unchanged": True, "section_raw_sizes_unchanged": True,
    }


def ffs_header(payload, original):
    header = bytearray(original[:24])
    total = len(payload) + 24
    if total >= 0xffffff or header[19] & 1:
        raise ValueError("extended FFS layout is unsupported")
    header[20:23] = total.to_bytes(3, "little")
    header[17] = (-sum(payload)) & 255 if header[19] & 0x40 else 0xaa
    header[16] = 0
    header[16] = (-(sum(header) - header[17] - header[23])) & 255
    return bytes(header) + payload


def fv_files(data, base):
    if base + 56 > len(data) or data[base + 40:base + 44] != b"_FVH":
        raise ValueError("invalid FV signature")
    length = struct.unpack_from("<Q", data, base + 32)[0]
    header_size = struct.unpack_from("<H", data, base + 48)[0]
    end = base + length
    if header_size < 56 or header_size % 2 or end > len(data) or header_size > length:
        raise ValueError("invalid FV dimensions")
    header = data[base:base + header_size]
    if sum(struct.unpack(f"<{header_size // 2}H", header)) & 0xffff:
        raise ValueError("invalid FV header checksum")
    files = []
    offset = align(base + header_size, 8)
    while offset + 24 <= end and data[offset:offset + 24] != b"\xff" * 24:
        h = data[offset:offset + 24]
        size = int.from_bytes(h[20:23], "little")
        if h[19] & 1 or size < 24 or offset + size > end:
            raise ValueError("invalid FFS dimensions")
        if (sum(h) - h[17] - h[23]) & 255:
            raise ValueError("invalid FFS header checksum")
        payload = data[offset + 24:offset + size]
        if h[19] & 0x40:
            if (sum(payload) + h[17]) & 255:
                raise ValueError("invalid FFS data checksum")
        elif h[17] != 0xaa:
            raise ValueError("invalid fixed FFS checksum marker")
        files.append({"offset": offset, "size": size, "guid": h[:16], "type": h[18]})
        offset = align(offset + size, 8)
    return files, end


def sections(data, start, end):
    result = []
    offset = start
    while offset + 4 <= end:
        size = int.from_bytes(data[offset:offset + 3], "little")
        if size < 4 or size == 0xffffff or offset + size > end:
            raise ValueError("invalid or extended section")
        result.append({"offset": offset, "size": size, "type": data[offset + 3]})
        offset = start + align(offset - start + size, 4)
    return result


def decode_lzma(data):
    if len(data) < 13:
        raise ValueError("truncated LZMA header")
    size = struct.unpack_from("<Q", data, 5)[0]
    dictionary = struct.unpack_from("<I", data, 1)[0]
    if not 0 < size <= 32 * 1048576 or not 4096 <= dictionary <= 32 * 1048576:
        raise ValueError("unexpected LZMA allocation size")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=64 * 1048576)
    result = decoder.decompress(data, max_length=size + 1)
    if not decoder.eof or len(result) != size or decoder.unused_data:
        raise ValueError("LZMA stream dimensions mismatch")
    return result


def encode_lzma(data, reference):
    properties = reference[0]
    lc = properties % 9
    rest = properties // 9
    lp, pb = rest % 5, rest // 5
    compressed = lzma.compress(data, format=lzma.FORMAT_ALONE, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": struct.unpack_from("<I", reference, 1)[0],
        "lc": lc, "lp": lp, "pb": pb, "mode": lzma.MODE_NORMAL,
        "mf": lzma.MF_BT4, "nice_len": 128,
    }])
    # EDK2's decoder allocates from this known-length field, not the EOS marker.
    result = compressed[:5] + struct.pack("<Q", len(data)) + compressed[13:]
    if result[:5] != reference[:5] or decode_lzma(result) != data:
        raise ValueError("LZMA round-trip or properties changed")
    return result


def patch_firmware(data, code, expected_pe_sha=SOURCE_PE_SHA):
    outer, end = fv_files(data, 0x8000)
    candidates = [file for file in outer if file["type"] == 11]
    if len(candidates) != 1:
        raise ValueError("expected one compressed FV image file")
    container = candidates[0]
    off, size = container["offset"], container["size"]
    outer_sections = sections(data, off + 24, off + size)
    if len(outer_sections) != 1 or outer_sections[0]["type"] != 2:
        raise ValueError("unexpected FV guided section")
    section = outer_sections[0]
    s = section["offset"]
    if data[s + 4:s + 20] != LZMA_GUID or struct.unpack_from("<HH", data, s + 20) != (24, 1):
        raise ValueError("expected standard LZMA processing-required section")
    packed = data[s + 24:s + section["size"]]
    unpacked = decode_lzma(packed)
    nested = [part for part in sections(unpacked, 0, len(unpacked)) if part["type"] == 23]
    if len(nested) != 1:
        raise ValueError("expected one inner FV section")
    inner_base = nested[0]["offset"] + 4
    files, inner_end = fv_files(unpacked, inner_base)
    matches = [file for file in files if file["guid"] == DARWIN_GUID]
    if len(matches) != 1:
        raise ValueError("missing or duplicate DarwinBoot FFS")
    module = matches[0]
    m, msize = module["offset"], module["size"]
    pe_sections = [part for part in sections(unpacked, m + 24, m + msize) if part["type"] == 16]
    if len(pe_sections) != 1:
        raise ValueError("expected one DarwinBoot PE32 section")
    pe_section = pe_sections[0]
    pe_start = pe_section["offset"] + 4
    pe_end = pe_section["offset"] + pe_section["size"]
    pe, report = patch_pe(unpacked[pe_start:pe_end], code, expected_pe_sha)
    changed = bytearray(unpacked)
    changed[pe_start:pe_end] = pe
    changed[m:m + msize] = ffs_header(bytes(changed[m + 24:m + msize]), unpacked[m:m + 24])
    after_files, after_end = fv_files(changed, inner_base)
    if len(after_files) != len(files) or after_end != inner_end:
        raise ValueError("inner FV file layout changed")
    for before, after in zip(files, after_files):
        if before != after:
            raise ValueError("inner FFS metadata changed unexpectedly")
        p, n = before["offset"], before["size"]
        if p != m and unpacked[p:p + n] != changed[p:p + n]:
            raise ValueError("a non-DarwinBoot FFS changed")
    new_packed = encode_lzma(bytes(changed), packed)
    guided = bytearray(data[s:s + 24])
    new_section_size = len(guided) + len(new_packed)
    if new_section_size >= 0xffffff:
        raise ValueError("guided section is too large")
    guided[:3] = new_section_size.to_bytes(3, "little")
    new_file = ffs_header(bytes(guided) + new_packed, data[off:off + 24])
    if any(value != 0xff for value in data[off + size:end]) or off + len(new_file) > end:
        raise ValueError("compressed FV is not the final file or no longer fits")
    result = data[:off] + new_file + b"\xff" * (end - off - len(new_file)) + data[end:]
    fv_files(result, 0x8000)
    if len(result) != len(data) or result[:0x8000] != data[:0x8000]:
        raise ValueError("FD dimensions or zero-tail stub changed")
    return result, pe, {
        **report, "outer_ffs_offset": off, "inner_ffs_offset": m,
        "inner_file_count": len(files), "other_inner_files_unchanged": True,
        "source_lzma_bytes": len(packed), "patched_lzma_bytes": len(new_packed),
        "lzma_properties_preserved": True, "lzma_known_length_preserved": True,
        "zero_tail_stub_unchanged": True,
    }


def build(source, output, clang="clang"):
    if output.exists():
        raise ValueError("output directory already exists")
    with tempfile.TemporaryDirectory(prefix="chiron-handoff-stages-") as temporary:
        work = Path(temporary)
        data, before = read_inspected(source, work / "input")
        if before["source_sha256"] != SOURCE_IMAGE_SHA:
            raise ValueError("use the verified working zero-tail image")
        h = before["header"]
        p = before["payload_offsets"]["kernel"]
        decoder = zlib.decompressobj(31)
        firmware = decoder.decompress(data[p:p + h["kernel_size"]], 0x600001)
        if not decoder.eof or len(firmware) != 0x600000:
            raise ValueError("invalid source firmware")
        code, stages = compile_handoff(work, clang)
        modified, pe, details = patch_firmware(firmware, code)
        kernel = gzip.compress(modified, mtime=0) + decoder.unused_data
        ram_offset = before["payload_offsets"]["ramdisk"]
        ramdisk = data[ram_offset:ram_offset + h["ramdisk_size"]]
        header = bytearray(data[:h["page_size"]])
        struct.pack_into("<I", header, 8, len(kernel))
        digest = hashlib.sha1()
        for payload in (kernel, ramdisk, b""):
            digest.update(payload)
            digest.update(struct.pack("<I", len(payload)))
        header[576:608] = digest.digest().ljust(32, b"\0")
        result = bytes(header) + kernel + bytes(-len(kernel) % h["page_size"]) + data[ram_offset:]
        image = work / "chiron-ram-init-handoff-stages.img"
        image.write_bytes(result)
        inspect(image, work / "verified")
        after = json.loads((work / "verified/inspection.json").read_text())
        if (
            not after["legacy_sha1_matches"]
            or after["payload_sha256"]["ramdisk"] != before["payload_sha256"]["ramdisk"]
            or after["appended_dtbs"][0]["sha256"] != before["appended_dtbs"][0]["sha256"]
            or after["kernel_image_sha256"] != sha256(modified)
        ):
            raise ValueError("diagnostic boot integrity failed")
        output.mkdir(parents=True, exist_ok=False)
        (output / image.name).write_bytes(result)
        (output / "DarwinBoot-stages.efi").write_bytes(pe)
        (output / "handoff.bin").write_bytes(code)
        (output / "handoff-report.json").write_text(json.dumps({
            **details, "image": image.name, "sha256": sha256(result), "bytes": len(result),
            "source_image_sha256": before["source_sha256"], "program_bytes": len(code),
            "stage_offsets": stages, "ramdisk_bytes": len(ramdisk), "ramdisk_sha256": sha256(ramdisk),
            "kernel_and_init_files_unchanged": True, "legacy_sha1_matches": True,
            "stripes": [
                {"index": 0, "color": "green", "meaning": "DarwinEnter reached after ExitBootServices"},
                {"index": 1, "color": "blue", "meaning": "owned-window cache clean completed"},
                {"index": 2, "color": "red", "meaning": "MMU/cache disable and TLB maintenance completed"},
            ],
            "hardware_executed": False,
            "limits": [
                "Red precedes BR to XNU; it does not prove XNU's first instruction executed.",
                "Cache-clean bounds and all MMU/TLB operations retain the original semantics.",
                "The FV is recompressed with the original LZMA properties and a known-length header.",
                "This is a diagnostic loader; phone execution remains to be observed.",
            ],
        }, indent=2) + "\n")
    return output / image.name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clang", default="clang")
    args = parser.parse_args()
    print(build(args.source, args.output, args.clang))


if __name__ == "__main__":
    main()
