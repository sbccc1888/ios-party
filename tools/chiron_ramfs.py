#!/usr/bin/env python3
"""Build/inspect the opt-in CHRAMFS v1 read-only root experiment (not N4KROOT)."""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
import struct

PAGE = 4096
MAX_IMAGE = 32 * 1024 * 1024
MAX_NODES, MAX_NAME, MAX_DEPTH, MAX_PATH = 128, 79, 16, 1023
MAGIC = b"CHRAMFS\0"
HEADER = struct.Struct("<8s4I3Q16s")
RECORD = struct.Struct("<8I2Q80s")
DIR, FILE = 1, 2


def align(value):
    return (value + PAGE - 1) & -PAGE


def components(path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("path must be absolute")
    if path == "/":
        return ()
    names = path[1:].split("/")
    if len(path) > MAX_PATH or len(names) > MAX_DEPTH:
        raise ValueError("path/depth exceeds format bound")
    for name in names:
        if (not name or name in (".", "..") or len(name) > MAX_NAME or
                any(ord(c) < 32 or ord(c) > 126 or c == "\\" for c in name)):
            raise ValueError("invalid path component")
    return tuple(names)


def integer(value, maximum, what):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"invalid {what}")
    return value


@dataclass(frozen=True)
class Entry:
    path: str
    kind: str
    data: bytes = b""
    mode: int = 0o555
    uid: int = 0
    gid: int = 0


def build(entries):
    """Build explicit entries; root and every parent directory must be supplied."""
    entries = list(entries)
    if not 1 <= len(entries) <= MAX_NODES:
        raise ValueError("node count exceeds format bound")
    by_path = {}
    for e in entries:
        names = components(e.path)
        if e.path in by_path:
            raise ValueError("duplicate path")
        integer(e.mode, 0o777, "mode (only permission bits supported)")
        integer(e.uid, 0xFFFFFFFF, "uid")
        integer(e.gid, 0xFFFFFFFF, "gid")
        if e.kind not in ("dir", "file") or not isinstance(e.data, bytes):
            raise ValueError("expected directory or regular file bytes")
        if e.kind == "dir" and e.data:
            raise ValueError("directory has file data")
        if len(e.data) > MAX_IMAGE:
            raise ValueError("file exceeds image limit")
        by_path[e.path] = (names, e)
    if "/" not in by_path or by_path["/"][1].kind != "dir":
        raise ValueError("explicit root directory required")
    ordered = sorted(by_path.values(), key=lambda item: item[0])
    ids = {e.path: i for i, (_, e) in enumerate(ordered)}
    cursor = data_offset = align(HEADER.size + len(ordered) * RECORD.size)
    records, extents = [], []
    for names, e in ordered:
        parent_path = "/" + "/".join(names[:-1])
        if names and (parent_path not in ids or by_path[parent_path][1].kind != "dir"):
            raise ValueError("missing or non-directory parent")
        name = names[-1].encode("ascii") if names else b""
        offset, size = 0, 0
        if e.kind == "file":
            offset, size = cursor, len(e.data)
            cursor = align(cursor + size)
            if cursor > MAX_IMAGE:
                raise ValueError("image exceeds 32 MiB experiment limit")
            extents.append((offset, e.data))
        records.append(RECORD.pack(ids[parent_path] if names else 0,
                                   DIR if e.kind == "dir" else FILE, e.mode, e.uid, e.gid,
                                   0, len(name), 0, offset, size, name))
    image = bytearray(cursor)
    HEADER.pack_into(image, 0, MAGIC, 1, HEADER.size, RECORD.size, len(records),
                     cursor, HEADER.size, data_offset, bytes(16))
    image[HEADER.size:HEADER.size + len(records) * RECORD.size] = b"".join(records)
    for offset, data in extents:
        image[offset:offset + len(data)] = data
    parse(image)
    return bytes(image)


def parse(image):
    """Strict independent Python decoder; kernel acceptance is tested using C too."""
    if len(image) < PAGE or len(image) > MAX_IMAGE or len(image) % PAGE:
        raise ValueError("invalid image geometry")
    magic, version, hs, rs, count, total, table, start, reserved = HEADER.unpack_from(image)
    if (magic != MAGIC or version != 1 or hs != HEADER.size or rs != RECORD.size or
            not 1 <= count <= MAX_NODES or total != len(image) or table != HEADER.size or
            start != align(table + count * rs) or start > total or any(reserved)):
        raise ValueError("invalid header")
    cursor, decoded, names_seen = start, [], set()
    for i in range(count):
        parent, kind, mode, uid, gid, r0, length, r1, offset, size, name_field = RECORD.unpack_from(
            image, table + i * rs)
        if (kind not in (DIR, FILE) or mode & ~0o777 or r0 or r1 or
                length > MAX_NAME or any(name_field[length:])):
            raise ValueError("invalid record")
        try:
            name = name_field[:length].decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("invalid name") from error
        if i == 0:
            if parent or kind != DIR or length:
                raise ValueError("invalid root")
            path, depth = "/", 0
        else:
            if parent >= i or decoded[parent]["kind"] != "dir":
                raise ValueError("invalid parent")
            base = decoded[parent]["path"]
            path = (base.rstrip("/") + "/" + name)
            # Validate the component separately: an embedded slash cannot become a path.
            if len(components("/" + name)) != 1:
                raise ValueError("invalid name")
            components(path)
            depth = decoded[parent]["depth"] + 1
            if (parent, name) in names_seen:
                raise ValueError("duplicate name")
            names_seen.add((parent, name))
        if kind == DIR:
            if offset or size:
                raise ValueError("directory has extent")
        else:
            if offset != cursor or size > total - cursor:
                raise ValueError("invalid file extent")
            end = cursor + size
            cursor = align(end)
            if cursor > total or any(image[end:cursor]):
                raise ValueError("invalid file padding")
        decoded.append({"path": path, "parent": parent, "kind": "dir" if kind == DIR else "file",
                        "mode": mode, "uid": uid, "gid": gid, "offset": offset, "size": size,
                        "depth": depth})
    if cursor != total or any(image[table + count * rs:start]):
        raise ValueError("invalid metadata padding or trailing pages")
    return decoded


def read_file(image, path):
    components(path)
    for e in parse(image):
        if e["path"] == path:
            if e["kind"] != "file":
                raise ValueError("path is a directory")
            return bytes(image[e["offset"]:e["offset"] + e["size"]])
    raise FileNotFoundError(path)


def from_manifest(manifest_path):
    """JSON schema 1: entries with path/type/mode/uid/gid and file source."""
    manifest_path = Path(manifest_path)
    document = json.loads(manifest_path.read_text())
    if set(document) != {"schema_version", "entries"} or document["schema_version"] != 1:
        raise ValueError("expected manifest schema_version 1 and entries")
    if not isinstance(document["entries"], list) or len(document["entries"]) > MAX_NODES:
        raise ValueError("invalid manifest entries")
    entries = []
    for item in document["entries"]:
        if not isinstance(item, dict) or set(item) - {"path", "type", "mode", "uid", "gid", "source"}:
            raise ValueError("unknown manifest entry field")
        kind = item["type"]
        data = b""
        if kind == "file":
            path = manifest_path.parent / item["source"]
            # Reject device/FIFO/symlink inputs; do not silently read host device nodes.
            if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > MAX_IMAGE:
                raise ValueError("source must be a bounded regular file")
            with path.open("rb") as stream:
                data = stream.read(MAX_IMAGE + 1)
            if len(data) > MAX_IMAGE:
                raise ValueError("source grew beyond image limit")
        elif "source" in item:
            raise ValueError("directory must not have a source")
        mode = item.get("mode", "0555" if kind == "dir" else "0444")
        if isinstance(mode, str):
            mode = int(mode, 8)
        entries.append(Entry(item["path"], kind, data, mode, item.get("uid", 0), item.get("gid", 0)))
    return build(entries)


def inspect(image):
    entries = parse(image)
    for e in entries:
        if e["kind"] == "file":
            e["sha256"] = hashlib.sha256(image[e["offset"]:e["offset"] + e["size"]]).hexdigest()
    return {"format": "CHRAMFS", "version": 1, "bytes": len(image),
            "sha256": hashlib.sha256(image).hexdigest(), "entries": entries,
            "qualification": {"host_python_parse": True, "kernel_compiled": False,
                              "qemu_tested": False, "hardware_tested": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--manifest", type=Path, required=True)
    make.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("inspect")
    check.add_argument("image", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        image = from_manifest(args.manifest)
        args.output.write_bytes(image)
    else:
        if not stat.S_ISREG(args.image.lstat().st_mode) or args.image.stat().st_size > MAX_IMAGE:
            parser.error("image must be a bounded regular file")
        image = args.image.read_bytes()
    print(json.dumps(inspect(image), indent=2))


if __name__ == "__main__":
    main()
