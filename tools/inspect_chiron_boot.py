#!/usr/bin/env python3
"""Inspect legacy boot v0 and appended FDTs without running target code."""

import argparse
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path


def align(value, boundary):
    return (value + boundary - 1) // boundary * boundary


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def cstring(data, start, end):
    stop = data.find(b"\0", start, end)
    if stop < 0:
        raise ValueError("unterminated FDT string")
    return data[start:stop].decode("ascii")


def parse_fdt(blob):
    if len(blob) < 40:
        raise ValueError("truncated FDT header")
    magic, total, off_struct, off_strings, off_reserve, version, compatible, _, size_strings, size_struct = (
        struct.unpack_from(">10I", blob)
    )
    if magic != 0xD00DFEED or total != len(blob) or version < 17 or compatible > 17:
        raise ValueError("unsupported FDT header")
    for offset, size in ((off_struct, size_struct), (off_strings, size_strings)):
        if offset < 40 or offset + size > total:
            raise ValueError("FDT block outside image")
    position, struct_end = off_struct, off_struct + size_struct
    stack, nodes = [], {}
    while position + 4 <= struct_end:
        token = struct.unpack_from(">I", blob, position)[0]
        position += 4
        if token == 1:  # FDT_BEGIN_NODE
            name = cstring(blob, position, struct_end)
            position = align(position + len(name) + 1, 4)
            stack.append(name)
            path = "/" + "/".join(stack[1:])
            if path in nodes:
                raise ValueError("duplicate FDT node")
            nodes[path] = {}
        elif token == 2:  # FDT_END_NODE
            if not stack:
                raise ValueError("unbalanced FDT nodes")
            stack.pop()
        elif token == 3:  # FDT_PROP
            if not stack or position + 8 > struct_end:
                raise ValueError("invalid FDT property")
            length, name_offset = struct.unpack_from(">II", blob, position)
            position += 8
            if position + length > struct_end or name_offset >= size_strings:
                raise ValueError("FDT property outside block")
            name = cstring(blob, off_strings + name_offset, off_strings + size_strings)
            path = "/" + "/".join(stack[1:])
            if name in nodes[path]:
                raise ValueError("duplicate FDT property")
            nodes[path][name] = blob[position:position + length]
            position = align(position + length, 4)
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            if stack:
                raise ValueError("unclosed FDT nodes")
            break
        else:
            raise ValueError(f"unknown FDT token {token}")
    else:
        raise ValueError("missing FDT_END")

    reservations = []
    position = off_reserve
    while 40 <= position and position + 16 <= total:
        address, size = struct.unpack_from(">QQ", blob, position)
        position += 16
        if not address and not size:
            break
        reservations.append({"address": hex(address), "size": hex(size)})
    else:
        raise ValueError("unterminated FDT reservation map")
    return nodes, reservations


def describe(value):
    result = {"bytes": len(value)}
    if value and len(value) <= 128 and len(value) % 4 == 0:
        result["cells"] = [hex(item[0]) for item in struct.iter_unpack(">I", value)]
    if not value:
        result["boolean"] = True
    elif (
        value[0] != 0
        and value.endswith(b"\0")
        and all(byte == 0 or 32 <= byte < 127 for byte in value)
        and all(value[:-1].split(b"\0"))
    ):
        result["strings"] = value[:-1].decode("ascii").split("\0")
    elif "cells" in result:
        pass
    elif len(value) <= 128:
        result["hex"] = value.hex()
    else:
        result["sha256"] = sha256(value)
        result["preview_hex"] = value[:64].hex()
    return result


def selected(path, properties):
    return (
        path in ("/", "/aliases", "/chosen", "/cpus", "/reserved-memory")
        or path.startswith(("/cpus/", "/memory", "/reserved-memory/", "/psci"))
        or re.search(
            r"(interrupt-controller@|/timer$|ufshc@|ufsphy@|i2c@c179000|"
            r"synaptics|ft5x|splash|/gpio-keys|mdss_mdp@|spmi@)", path
        )
        or "qcom,mdss-dsi-panel-name" in properties
    )


def inspect(source, output):
    data = source.read_bytes()
    if len(data) < 1632 or data[:8] != b"ANDROID!":
        raise ValueError("not a legacy Android boot image")
    names = (
        "kernel_size kernel_addr ramdisk_size ramdisk_addr second_size "
        "second_addr tags_addr page_size header_version os_version"
    ).split()
    header = dict(zip(names, struct.unpack_from("<10I", data, 8)))
    page = header["page_size"]
    if header["header_version"] != 0 or page not in (2048, 4096, 8192, 16384):
        raise ValueError("only boot header v0 with a valid page size is supported")
    offsets, payloads, position = {}, {}, page
    digest = hashlib.sha1()
    for name in ("kernel", "ramdisk", "second"):
        size = header[f"{name}_size"]
        if position + size > len(data):
            raise ValueError(f"truncated {name} payload")
        offsets[name] = position
        payloads[name] = data[position:position + size]
        digest.update(payloads[name])
        digest.update(struct.pack("<I", size))
        position = align(position + size, page)
    if position > len(data):
        raise ValueError("truncated payload alignment padding")
    if payloads["kernel"][:2] != b"\x1f\x8b":
        raise ValueError("expected gzip kernel")
    decompressor = zlib.decompressobj(31)
    kernel = decompressor.decompress(payloads["kernel"], 256 * 1024 * 1024)
    if not decompressor.eof:
        raise ValueError("kernel exceeds limit or has an incomplete gzip stream")
    suffix = decompressor.unused_data
    gzip_size = len(payloads["kernel"]) - len(suffix)
    banner = re.search(rb"Linux version [^\0\r\n]{1,1024}", kernel)
    arm_header = None
    if len(kernel) >= 64 and kernel[56:60] == b"ARM\x64":
        text_offset, image_size, flags = struct.unpack_from("<QQQ", kernel, 8)
        # Linux Image flags describe this build, not every CPU capability.
        arm_header = {
            "text_offset": hex(text_offset),
            "image_size": image_size,
            "flags": hex(flags),
            "kernel_page_size": {0: "unspecified", 1: 4096, 2: 16384, 3: 65536}[
                (flags >> 1) & 3
            ],
        }
    dtbs, extracted = [], []
    cursor = 0
    while cursor < len(suffix):
        if suffix[cursor:cursor + 4] != b"\xd0\x0d\xfe\xed":
            if suffix[cursor] == 0:
                cursor += 1
                continue
            raise ValueError(f"unexpected appended data at suffix offset {cursor:#x}")
        if cursor + 8 > len(suffix):
            raise ValueError("truncated appended FDT")
        size = struct.unpack_from(">I", suffix, cursor + 4)[0]
        if size < 40 or cursor + size > len(suffix):
            raise ValueError("invalid appended FDT size")
        blob = suffix[cursor:cursor + size]
        nodes, reservations = parse_fdt(blob)
        filename = f"dtb-{len(dtbs):02d}.dtb"
        dtbs.append({
            "file": filename,
            "boot_image_offset": page + gzip_size + cursor,
            "size": size,
            "sha256": sha256(blob),
            "node_count": len(nodes),
            "memory_reservations": reservations,
            "nodes": {
                path: {name: describe(value) for name, value in props.items()}
                for path, props in nodes.items() if selected(path, props)
            },
        })
        extracted.append((filename, blob))
        cursor += size
    if not dtbs:
        raise ValueError("no appended FDT found")
    os_version = header["os_version"]
    cmdline = data[64:576].split(b"\0", 1)[0] + data[608:1632].split(b"\0", 1)[0]
    report = {
        "source": str(source.resolve()),
        "source_bytes": len(data),
        "source_sha256": sha256(data),
        "header": header,
        "header_os_version": ".".join(str((os_version >> shift) & 127) for shift in (25, 18, 11)),
        "header_patch_level": f"{2000 + ((os_version >> 4) & 127):04d}-{os_version & 15:02d}",
        "board_name": data[48:64].split(b"\0", 1)[0].decode("ascii", "replace"),
        "cmdline": cmdline.decode("ascii", "replace"),
        "payload_offsets": offsets,
        "payload_sha256": {name: sha256(blob) for name, blob in payloads.items()},
        "legacy_sha1_matches": data[576:608] == digest.digest().ljust(32, b"\0"),
        "legacy_sha1_stored": data[576:608].hex(),
        "legacy_sha1_computed": digest.hexdigest(),
        "trailing_bytes": len(data) - position,
        "trailing_sha256": sha256(data[position:]),
        "kernel_gzip_bytes": gzip_size,
        "kernel_decompressed_bytes": len(kernel),
        "kernel_banner": banner.group().decode("ascii", "replace") if banner else None,
        "arm64_image_header": arm_header,
        "appended_dtbs": dtbs,
        "limits": [
            "Static input only; no target execution or live FDT was observed.",
            "Header addresses are encoded load parameters, not a proven free-memory map.",
            "Linux page-size flags do not prove support or absence of other CPU granules.",
            "Bundled DTBs do not establish which variant the bootloader selected.",
        ],
    }
    # Refuse an existing directory so prior evidence cannot be silently replaced.
    output.mkdir(parents=True, exist_ok=False)
    for filename, blob in extracted:
        (output / filename).write_bytes(blob)
    (output / "inspection.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "source_sha256", "header", "header_os_version", "header_patch_level",
        "legacy_sha1_matches", "kernel_banner", "arm64_image_header",
    )}, indent=2))
    for dtb in dtbs:
        root = dtb["nodes"]["/"]
        print(json.dumps({
            "dtb": dtb["file"], "sha256": dtb["sha256"],
            "bytes": dtb["size"], "model": root.get("model"),
            "msm_id": root.get("qcom,msm-id"), "board_id": root.get("qcom,board-id"),
        }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        inspect(args.source, args.output)
    except (ValueError, OSError, zlib.error, struct.error) as exc:
        parser.exit(1, f"inspection failed: {exc}\n")


if __name__ == "__main__":
    main()
