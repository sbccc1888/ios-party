#!/usr/bin/env python3
"""Read-only, fail-closed inventory of explicitly supplied iOS userspace inputs.

Only Python's standard library is used. No target is executed and no network,
subprocess, dependency installation, recursive search, or output-file write occurs.
The source definitions and deliberately unsupported formats are documented in
research/CHIRON_USERSPACE_INPUTS.zh-CN.md.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import mmap
import os
from pathlib import Path
import plistlib
import re
import stat
import struct
import sys
import uuid
from xml.parsers.expat import ExpatError


SCHEMA_VERSION = 1
MAX_FILES = 256
MAX_METADATA = 2 * 1024 * 1024
MAX_SIGNATURE = 64 * 1024 * 1024
MAX_COMMANDS = 65536
MAX_FILE_BYTES = 8 * 1024**3
MAX_TOTAL_BYTES = 16 * 1024**3
ARM64 = 0x0100000C
DYLIBS = {
    0xC: "LC_LOAD_DYLIB", 0x80000018: "LC_LOAD_WEAK_DYLIB",
    0x8000001F: "LC_REEXPORT_DYLIB", 0x20: "LC_LAZY_LOAD_DYLIB",
    0x80000023: "LC_LOAD_UPWARD_DYLIB",
}
FAT = {
    b"\xca\xfe\xba\xbe": (">", False), b"\xbe\xba\xfe\xca": ("<", False),
    b"\xca\xfe\xba\xbf": (">", True), b"\xbf\xba\xfe\xca": ("<", True),
}
# XNU osfmk/kern/debug.h: struct _dyld_cache_header, through uuid.
CACHE_PREFIX = struct.Struct("<16s4I7Q16s")
KINDS = {"auto", "macho", "cache", "system_version", "data"}
ROLES = {"dyld", "launchd", "libsystem", "cache", "cache_subcache",
         "system_version", "support"}


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def issue(code, detail, category="invalid", path=None):
    result = {"code": code, "category": category, "detail": detail}
    if path is not None:
        result["path"] = path
    return result


def extent(total, offset, size, label):
    require(0 <= offset <= total and 0 <= size <= total - offset,
            label + " outside file/container")


def cstring(data, offset, end, label, minimum=0):
    require(minimum <= offset < end <= len(data), label + " offset outside bounds")
    stop = data.find(b"\0", offset, end)
    require(stop >= 0, label + " is not NUL terminated")
    try:
        return data[offset:stop].decode("utf-8")
    except UnicodeError as error:
        raise AuditError(label + " is not UTF-8") from error


def digest(data, start=0, size=None, algorithm="sha256"):
    size = len(data) - start if size is None else size
    extent(len(data), start, size, "hash range")
    result = hashlib.new(algorithm)
    for at in range(start, start + size, 1024 * 1024):
        result.update(data[at:min(at + 1024 * 1024, start + size)])
    return result.digest()


def parse_code_directory(cd, data, base, image_size, signature_offset, slot):
    require(len(cd) >= 44, "truncated CodeDirectory")
    magic, length, version, flags, hashoff, identoff, special, slots, limit = (
        struct.unpack_from(">9I", cd))
    require(magic == 0xFADE0C02 and length == len(cd), "invalid CodeDirectory length/magic")
    hashsize, hashtype, platform, shift = struct.unpack_from("4B", cd, 36)
    result = {
        "slot": slot, "version": hex(version), "flags": hex(flags),
        "hash_type": hashtype, "hash_size": hashsize, "platform": platform,
        "page_shift": shift, "page_bytes": (1 << shift) if shift <= 30 else None,
        "special_slots": special, "code_slots": slots, "code_limit": limit,
        "code_hashes_verified": False, "special_hashes_verified": False,
        "issues": [],
    }
    problems = result["issues"]
    if not 0x20001 <= version <= 0x20600:
        problems.append(issue("code_directory_version", "unsupported CodeDirectory version",
                              "unsupported"))
        return result
    minimum = 44
    for threshold, size in ((0x20100, 48), (0x20200, 52), (0x20300, 64),
                            (0x20400, 88), (0x20500, 96), (0x20600, 108)):
        if version >= threshold:
            minimum = size
    require(len(cd) >= minimum, "truncated versioned CodeDirectory")
    require(struct.unpack_from(">I", cd, 40)[0] == 0, "nonzero CodeDirectory spare2")
    result["identifier"] = cstring(cd, identoff, len(cd), "identifier", minimum)
    if version >= 0x20200:
        team = struct.unpack_from(">I", cd, 48)[0]
        result["team_id"] = cstring(cd, team, len(cd), "team", minimum) if team else None
    if version >= 0x20300:
        spare, limit64 = struct.unpack_from(">IQ", cd, 52)
        require(spare == 0, "nonzero CodeDirectory spare3")
        if limit64:
            # Avoid accepting ambiguous alternate coverage encodings.
            if limit not in (0, 0xFFFFFFFF, limit64):
                problems.append(issue("ambiguous_code_limit", "codeLimit/codeLimit64 disagree"))
            limit = limit64
    result["code_limit"] = limit
    result["coverage"] = {
        "start": 0, "end": limit, "signature_offset": signature_offset,
        "covers_all_bytes_before_signature": limit == signature_offset,
    }
    require(limit <= image_size and limit <= signature_offset,
            "CodeDirectory covers bytes outside signed image or into signature")
    if limit != signature_offset:
        problems.append(issue("incomplete_coverage", "codeLimit differs from signature offset"))
    if version >= 0x20100 and struct.unpack_from(">I", cd, 44)[0]:
        problems.append(issue("scatter_signature", "scatter CodeDirectory is not supported",
                              "unsupported"))
    if version >= 0x20500 and struct.unpack_from(">I", cd, 92)[0]:
        problems.append(issue("pre_encryption_signature", "pre-encryption hashes not supported",
                              "unsupported"))
    if version >= 0x20600 and any(cd[96:108]):
        problems.append(issue("linkage_signature", "linkage CodeDirectory not supported",
                              "unsupported"))
    algorithms = {1: ("sha1", 20), 2: ("sha256", 32),
                  3: ("sha256", 20), 4: ("sha384", 48)}
    if hashtype not in algorithms or hashsize != algorithms[hashtype][1]:
        problems.append(issue("signature_hash", "unsupported hash type/size", "unsupported"))
        return result
    algorithm, _ = algorithms[hashtype]
    result["cdhash"] = hashlib.new(algorithm, cd).digest()[:20].hex()
    require(hashoff - special * hashsize >= minimum, "special slots overlap fixed header")
    extent(len(cd), hashoff, slots * hashsize, "CodeDirectory hash slots")
    special_start = hashoff - special * hashsize
    for offset in (identoff, struct.unpack_from(">I", cd, 48)[0] if version >= 0x20200 else 0):
        if offset:
            require(offset < special_start and cd.find(b"\0", offset, special_start) >= 0,
                    "CodeDirectory string overlaps hash table")
    if shift not in (0, 12, 14):
        problems.append(issue("signature_page_shift", "only infinite, 4K and 16K hashes parsed",
                              "unsupported"))
        return result
    result["page_bytes"] = None if shift == 0 else 1 << shift
    block = limit if shift == 0 else 1 << shift
    expected_slots = (limit + block - 1) // block if block else 0
    require(slots == expected_slots, "CodeDirectory slot count does not cover codeLimit")
    if shift != 12:
        problems.append(issue("native_4k_signature", "not the current native 4K signing path",
                              "unsupported"))
    if any(p["code"] in {"scatter_signature", "pre_encryption_signature",
                         "linkage_signature", "ambiguous_code_limit"} for p in problems):
        return result
    for index in range(slots):
        actual = digest(data, base + index * block, min(block, limit - index * block),
                        algorithm)[:hashsize]
        if actual != cd[hashoff + index * hashsize:hashoff + (index + 1) * hashsize]:
            problems.append(issue("code_hash_mismatch", "code slot %d mismatched" % index))
            return result
    result["code_hashes_verified"] = True
    return result


def parse_signature(data, base, image_size, offset, size):
    extent(image_size, offset, size, "signature")
    require(12 <= size <= MAX_SIGNATURE, "signature size outside supported limit")
    signature = data[base + offset:base + offset + size]
    magic, length, count = struct.unpack_from(">III", signature)
    require(magic == 0xFADE0CC0 and 12 <= length <= size,
            "unsupported or invalid signature SuperBlob")
    require(count <= 1024 and 12 + count * 8 <= length, "invalid SuperBlob index count")
    blobs, ranges = {}, []
    for index in range(count):
        slot, at = struct.unpack_from(">II", signature, 12 + index * 8)
        require(slot not in blobs, "duplicate SuperBlob slot")
        require(at >= 12 + count * 8, "blob overlaps SuperBlob index")
        extent(length, at, 8, "blob header")
        kind, blobsize = struct.unpack_from(">II", signature, at)
        require(blobsize >= 8, "invalid blob size")
        extent(length, at, blobsize, "blob")
        require(all(at + blobsize <= left or right <= at for left, right in ranges),
                "overlapping signature blobs")
        ranges.append((at, at + blobsize))
        blobs[slot] = (kind, signature[at:at + blobsize])
    require(0 in blobs and blobs[0][0] == 0xFADE0C02, "primary CodeDirectory missing")
    result = {"offset": offset, "size": size, "code_directories": [],
              "entitlements": {"xml": False, "der": False},
              "cms_present": 0x10000 in blobs, "apple_trust_verified": False, "issues": []}
    for slot, (kind, blob) in blobs.items():
        if kind == 0xFADE0C02:
            require(slot == 0 or 0x1000 <= slot < 0x1005, "CodeDirectory in unknown slot")
            cd = parse_code_directory(blob, data, base, image_size, offset, slot)
            result["code_directories"].append(cd)
            result["issues"].extend(cd["issues"])
        elif slot in (0, *range(0x1000, 0x1005)):
            raise AuditError("CodeDirectory slot has wrong blob magic")
        if slot in (5, 7):
            expected = 0xFADE7171 if slot == 5 else 0xFADE7172
            require(kind == expected, "entitlements slot/magic mismatch")
            result["entitlements"]["xml" if slot == 5 else "der"] = True
        if slot == 0x10000:
            require(kind == 0xFADE0B01, "CMS slot has wrong blob magic")
    # Special slots can include external Info.plist/resources: presence is
    # inventoried, never confused with validating those files or Apple trust.
    if any(cd["special_slots"] for cd in result["code_directories"]):
        result["issues"].append(issue("special_slot_validation",
                                     "special-slot hashes not validated", "unverified"))
    if any(result["entitlements"].values()):
        result["issues"].append(issue("entitlement_policy",
                                     "entitlement semantics/AMFI not validated", "unverified"))
    return result


def parse_thin(data, base, size):
    extent(len(data), base, size, "Mach-O slice")
    require(size >= 4, "truncated Mach-O magic")
    magic = data[base:base + 4]
    if magic not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
        raise AuditError("unsupported Mach-O format (require 64-bit thin)")
    endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
    require(size >= 32, "truncated Mach-O header")
    _, cpu, subtype, kind, count, command_bytes, flags, reserved = (
        struct.unpack_from(endian + "8I", data, base))
    require(reserved == 0, "nonzero Mach-O reserved field")
    require(count <= MAX_COMMANDS and command_bytes <= 16 * 1024**2,
            "Mach-O load command limit exceeded")
    extent(size, 32, command_bytes, "load commands")
    result = {
        "offset": base, "size": size, "endianness": endian, "cpu_type": cpu,
        "cpu_subtype": subtype, "cpu_subtype_base": subtype & 0xFFFFFF,
        "cpu_subtype_capabilities": hex(subtype & 0xFF000000),
        "architecture": ("arm64e" if subtype & 0xFFFFFF == 2 else "arm64")
        if cpu == ARM64 else hex(cpu),
        "file_type": kind, "flags": hex(flags), "uuid": None,
        "load_dylinker": [], "load_dylib": [], "rpaths": [], "segments": [],
        "build_versions": [], "unparsed_load_commands": [], "issues": [],
        "signature": None,
    }
    problems = result["issues"]
    if cpu != ARM64 or subtype not in (0, 1) or endian != "<":
        problems.append(issue("architecture", "require little-endian arm64 ALL/V8 without flags",
                              "unsupported"))
    if kind not in (2, 6, 7, 8):
        problems.append(issue("mach_file_type", "unsupported userspace Mach-O file type",
                              "unsupported"))
    cursor, end, signature = base + 32, base + 32 + command_bytes, None
    for _ in range(count):
        extent(end, cursor, 8, "load command header")
        cmd, length = struct.unpack_from(endian + "II", data, cursor)
        require(length >= 8 and length % 8 == 0, "invalid load command size/alignment")
        extent(end, cursor, length, "load command")
        if cmd == 0x1B:
            require(length == 24 and result["uuid"] is None, "invalid/duplicate LC_UUID")
            result["uuid"] = str(uuid.UUID(bytes=bytes(data[cursor + 8:cursor + 24])))
        elif cmd in DYLIBS or cmd in (0xD, 0xE, 0xF, 0x8000001C):
            minimum = 24 if cmd in DYLIBS or cmd == 0xD else 12
            require(length >= minimum, "truncated load command string")
            at = struct.unpack_from(endian + "I", data, cursor + 8)[0]
            name = cstring(data, cursor + at, cursor + length, "load command string",
                           cursor + minimum)
            if cmd in DYLIBS:
                result["load_dylib"].append({"command": DYLIBS[cmd], "path": name})
            elif cmd == 0xE:
                result["load_dylinker"].append(name)
            elif cmd == 0x8000001C:
                result["rpaths"].append(name)
            else:
                result["install_name"] = name
        elif cmd == 0x19:
            require(length >= 72, "truncated LC_SEGMENT_64")
            name, va, vmsize, fileoff, filesize, maxprot, initprot, sections, segflags = (
                struct.unpack_from(endian + "16s4Q4I", data, cursor + 8))
            require(length == 72 + sections * 80, "segment section count/size mismatch")
            extent(size, fileoff, filesize, "segment")
            require(filesize <= vmsize and va + vmsize <= 1 << 64, "invalid segment extent")
            result["segments"].append({
                "name": name.rstrip(b"\0").decode("ascii", "replace"),
                "address": va, "vm_size": vmsize, "file_offset": fileoff,
                "file_size": filesize, "max_prot": maxprot, "init_prot": initprot,
                "flags": segflags, "section_count": sections,
            })
            if (va | fileoff) & 4095:
                problems.append(issue("segment_alignment", "segment address/offset not 4K aligned"))
        elif cmd == 0x1D:
            require(length == 16 and signature is None, "invalid/duplicate LC_CODE_SIGNATURE")
            signature = struct.unpack_from(endian + "II", data, cursor + 8)
        elif cmd == 0x32:
            require(length >= 24, "truncated LC_BUILD_VERSION")
            platform, minimum, sdk, tools = struct.unpack_from(endian + "4I", data, cursor + 8)
            require(length == 24 + tools * 8, "LC_BUILD_VERSION tool count mismatch")
            result["build_versions"].append({"platform": platform, "minos": minimum, "sdk": sdk})
            if platform != 2:
                problems.append(issue("platform", "LC_BUILD_VERSION is not iOS", "unsupported"))
        elif cmd in (0x21, 0x2C):
            require(length == (24 if cmd == 0x2C else 20), "invalid encryption command")
            cryptoff, cryptsize, cryptid = struct.unpack_from(endian + "3I", data, cursor + 8)
            extent(size, cryptoff, cryptsize, "encrypted region")
            result["encryption"] = {"offset": cryptoff, "size": cryptsize, "cryptid": cryptid}
            if cryptid:
                problems.append(issue("encrypted_image", "encrypted target is unsupported",
                                      "unsupported"))
        elif cmd == 0x80000034:
            require(length == 16, "invalid chained fixups command")
            at, length2 = struct.unpack_from(endian + "II", data, cursor + 8)
            extent(size, at, length2, "chained fixups")
            problems.append(issue("chained_fixups", "chained fixup formats not inspected",
                                  "unsupported"))
        else:
            result["unparsed_load_commands"].append(hex(cmd))
        cursor += length
    require(cursor == end, "Mach-O load command count/size mismatch")
    require(len(result["load_dylinker"]) <= 1, "duplicate LC_LOAD_DYLINKER")
    if result["uuid"] is None:
        problems.append(issue("missing_uuid", "LC_UUID absent", "missing"))
    if signature is None:
        problems.append(issue("missing_signature", "LC_CODE_SIGNATURE absent", "missing"))
    else:
        try:
            result["signature"] = parse_signature(data, base, size, *signature)
            problems.extend(result["signature"]["issues"])
        except AuditError as error:
            problems.append(issue("signature_rejected", str(error)))
    return result


def parse_macho(data):
    require(len(data) >= 4, "truncated file")
    magic = bytes(data[:4])
    if magic not in FAT:
        return {"format": "Mach-O64", "slices": [parse_thin(data, 0, len(data))]}
    endian, wide = FAT[magic]
    require(len(data) >= 8, "truncated FAT header")
    count = struct.unpack_from(endian + "I", data, 4)[0]
    require(0 < count <= 64, "FAT architecture count outside supported limit")
    entry_size = 32 if wide else 20
    table_end = 8 + count * entry_size
    extent(len(data), 0, table_end, "FAT architecture table")
    result, ranges, identities = [], [], set()
    for index in range(count):
        fields = struct.unpack_from(endian + ("IIQQII" if wide else "5I"),
                                    data, 8 + index * entry_size)
        cpu, subtype, offset, size, alignment = fields[:5]
        require(not wide or fields[5] == 0, "nonzero FAT64 reserved field")
        require(alignment <= 63 and offset >= table_end and size >= 32,
                "invalid FAT slice offset/size/alignment")
        require(offset % (1 << alignment) == 0, "misaligned FAT slice")
        extent(len(data), offset, size, "FAT slice")
        require((cpu, subtype) not in identities, "duplicate FAT architecture")
        require(all(offset + size <= left or right <= offset for left, right in ranges),
                "overlapping FAT slices")
        identities.add((cpu, subtype))
        ranges.append((offset, offset + size))
        image = parse_thin(data, offset, size)
        require((cpu, subtype) == (image["cpu_type"], image["cpu_subtype"]),
                "FAT architecture disagrees with Mach-O slice")
        result.append(image)
    return {"format": "FAT64" if wide else "FAT32", "slices": result}


def parse_cache(data):
    require(len(data) >= CACHE_PREFIX.size, "truncated dyld cache identity prefix")
    fields = CACHE_PREFIX.unpack_from(data)
    magic, mapping_offset, mapping_count, images_offset, images_count = fields[:5]
    require(re.fullmatch(rb"dyld_v1 +[A-Za-z0-9_]+", magic.rstrip(b"\0")) is not None,
            "unsupported dyld cache magic/version")
    dyldbase, signature_offset, signature_size, slideoff, slidesize, localoff, localsize = fields[5:12]
    require(mapping_offset >= CACHE_PREFIX.size, "cache mappings overlap identity prefix")
    extent(len(data), mapping_offset, 0, "cache mapping offset")
    result = {
        "format": "dyld-cache-prefix-only",
        "magic": magic.rstrip(b"\0").decode("ascii", "replace"),
        "architecture": magic.rstrip(b"\0").split()[-1].decode("ascii"),
        "uuid": str(uuid.UUID(bytes=fields[12])),
        "mapping_offset": mapping_offset, "mapping_count": mapping_count,
        "legacy_images_offset": images_offset, "legacy_images_count": images_count,
        "dyld_base_address": dyldbase, "signature": None,
        "subcache_discovery_verified": False, "subcache_identity_verified": False,
        "issues": [issue("cache_layout_unsupported",
                         "only fixed XNU mirrored identity/signature prefix is parsed; "
                         "mapping, slide, image index and subcache layouts are not supported",
                         "unsupported")],
    }
    extent(len(data), slideoff, slidesize, "legacy slide info")
    extent(len(data), localoff, localsize, "local symbols")
    if signature_offset:
        try:
            result["signature"] = parse_signature(
                data, 0, len(data), signature_offset,
                signature_size or len(data) - signature_offset)
            result["issues"].extend(result["signature"]["issues"])
        except AuditError as error:
            result["issues"].append(issue("signature_rejected", str(error)))
    else:
        result["issues"].append(issue("missing_signature", "cache signature absent", "missing"))
    return result


def relative_path(value):
    require(isinstance(value, str) and value and "\0" not in value and "\\" not in value,
            "file path must be a nonempty POSIX relative path")
    require(not value.startswith("/") and all(p not in ("", ".", "..") for p in value.split("/")),
            "absolute, empty, dot and parent path components are forbidden")
    return value


@contextmanager
def open_input(root, name, directory=False):
    """Walk relative components using dir_fd and O_NOFOLLOW, including parents."""
    parts = relative_path(name).split("/")
    current = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parts[:-1]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=current)
            os.close(current)
            current = following
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        final = os.open(parts[-1], flags, dir_fd=current)
        try:
            info = os.fstat(final)
            require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
                    "input must be an ordinary directory/file")
            yield final, info
        finally:
            os.close(final)
    finally:
        os.close(current)


def read_metadata(path):
    root = path.parent.resolve()
    with open_input(root, path.name) as (fd, info):
        require(info.st_size <= MAX_METADATA, "metadata exceeds 2 MiB")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(MAX_METADATA + 1)
        require(len(data) <= MAX_METADATA, "metadata exceeds 2 MiB")
        return data


def load_manifest(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate manifest key: " + key)
            result[key] = value
        return result
    manifest = json.loads(read_metadata(path), object_pairs_hook=unique_pairs)
    require(isinstance(manifest, dict), "manifest must be an object")
    require(set(manifest) <= {"schema_version", "ProductVersion", "BuildVersion", "source", "files"},
            "unknown manifest fields")
    require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
            "require manifest schema_version 1")
    require(isinstance(manifest.get("files"), list) and len(manifest["files"]) <= MAX_FILES,
            "manifest files must be an array of at most 256 entries")
    for field in ("ProductVersion", "BuildVersion", "source"):
        require(field not in manifest or isinstance(manifest[field], str),
                field + " must be a string")
    targets, paths = set(), set()
    for entry in manifest["files"]:
        require(isinstance(entry, dict), "file entry must be an object")
        require(set(entry) <= {"path", "target_path", "kind", "role", "sha256", "uuid"},
                "unknown file entry fields")
        path = relative_path(entry.get("path"))
        require(path not in paths, "duplicate input path")
        paths.add(path)
        target = entry.get("target_path", "/" + path)
        require(isinstance(target, str) and target.startswith("/") and target != "/",
                "target_path must be absolute")
        relative_path(target[1:])
        require(target not in targets, "duplicate target_path")
        targets.add(target)
        require(isinstance(entry.get("kind", "auto"), str) and
                entry.get("kind", "auto") in KINDS, "unsupported manifest kind")
        require(isinstance(entry.get("role", "support"), str) and
                entry.get("role", "support") in ROLES, "unsupported manifest role")
        if "sha256" in entry:
            require(isinstance(entry["sha256"], str) and
                    re.fullmatch("[0-9a-fA-F]{64}", entry["sha256"]), "invalid expected SHA-256")
        if "uuid" in entry:
            require(isinstance(entry["uuid"], str), "UUID must be a string")
            uuid.UUID(entry["uuid"])
    return manifest


def directory_entries(root):
    entries = [
        {"path": "System/Library/CoreServices/SystemVersion.plist", "kind": "system_version",
         "role": "system_version"},
        {"path": "usr/lib/dyld", "kind": "macho", "role": "dyld"},
        {"path": "sbin/launchd", "kind": "macho", "role": "launchd"},
    ]
    library = "usr/lib/libSystem.B.dylib"
    try:
        with open_input(root, library):
            entries.append({"path": library, "kind": "macho", "role": "libsystem"})
    except FileNotFoundError:
        pass
    cache_dir = "System/Library/Caches/com.apple.dyld"
    try:
        with open_input(root, cache_dir, directory=True) as (fd, _):
            names = []
            with os.scandir(fd) as scan:
                for index, entry in enumerate(scan):
                    require(index < 1024, "cache directory entry limit exceeded; use explicit manifest")
                    if entry.name.startswith("dyld_shared_cache_"):
                        names.append(entry.name)
            require(len(names) <= MAX_FILES - len(entries), "too many cache candidates")
            for name in sorted(names):
                entries.append({"path": cache_dir + "/" + name, "kind": "cache", "role": "cache"})
    except FileNotFoundError:
        pass
    return entries


def inspect_file(root, entry, remaining):
    name, kind = entry["path"], entry.get("kind", "auto")
    record = {"path": name, "target_path": entry.get("target_path", "/" + name),
              "role": entry.get("role", "support"), "issues": [], "status": "unverified"}
    size = 0
    try:
        with open_input(root, name) as (fd, before):
            size = before.st_size
            require(size <= MAX_FILE_BYTES and size <= remaining, "input byte budget exceeded")
            record["size"] = size
            require(size > 0, "empty input")
            with mmap.mmap(fd, 0, access=mmap.ACCESS_READ) as data:
                record["sha256"] = digest(data).hex()
                if "sha256" in entry and record["sha256"] != entry["sha256"].lower():
                    record["issues"].append(issue("sha256_mismatch", "manifest SHA-256 mismatched"))
                if kind == "auto":
                    kind = "cache" if data[:7] == b"dyld_v1" else "macho"
                record["kind"] = kind
                if kind == "macho":
                    record["macho"] = parse_macho(data)
                    for image in record["macho"]["slices"]:
                        record["issues"].extend(image["issues"])
                elif kind == "cache":
                    record["cache"] = parse_cache(data)
                    record["issues"].extend(record["cache"]["issues"])
                elif kind == "system_version":
                    require(size <= MAX_METADATA, "SystemVersion.plist exceeds metadata limit")
                    value = plistlib.loads(data[:])
                    require(isinstance(value, dict), "SystemVersion.plist is not a dictionary")
                    record["system_version"] = {
                        "ProductVersion": value.get("ProductVersion"),
                        "BuildVersion": value.get("ProductBuildVersion"),
                    }
                elif kind == "data":
                    record["issues"].append(issue("opaque_data", "data inventoried without format audit",
                                                  "unverified"))
                if "uuid" in entry:
                    expected = str(uuid.UUID(entry["uuid"]))
                    actual = ([s["uuid"] for s in record["macho"]["slices"]]
                              if "macho" in record else [record.get("cache", {}).get("uuid")])
                    if expected not in actual:
                        record["issues"].append(issue("uuid_mismatch", "manifest UUID mismatched"))
                record["status"] = "parsed" if not record["issues"] else "blocked"
            after = os.fstat(fd)
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                    "input changed during audit")
    except FileNotFoundError:
        record["issues"].append(issue("missing_file", "explicit input file is absent", "missing"))
        record["status"] = "missing"
    except (OSError, ValueError, struct.error, OverflowError, ExpatError, RecursionError) as error:
        record["issues"].append(issue("input_rejected", str(error)))
        record["status"] = "rejected"
    return record, size


def audit(root=None, manifest=None, product_version=None, build_version=None):
    report = {
        "schema_version": SCHEMA_VERSION, "tool": "audit_chiron_userspace",
        "target": {"ProductVersion": product_version, "BuildVersion": build_version,
                   "identity_verified": False},
        "files": [], "issues": [], "dependencies": [],
        "compatibility_verified": False,
        "structural_audit_complete": False,
        "admission": {"eligible": False, "status": "blocked"},
        "unverified": [
            "Apple provenance/CMS trust chain, AMFI, entitlement semantics and security components",
            "instruction reachability and target CPU ABI",
            "complete dynamic dependency closure and cache/subcache membership",
            "cache mappings, slide chains, authenticated pointers and cache image extraction",
            "4K runtime mapping/page-in, dyld execution, launchd, services and hardware",
        ],
    }
    problems = report["issues"]
    manifest_value = {}
    if manifest is not None:
        manifest_value = load_manifest(Path(manifest))
        if root is None:
            root = Path(manifest).parent
    for field, explicit in (("ProductVersion", product_version), ("BuildVersion", build_version)):
        declared = manifest_value.get(field)
        if explicit and declared and explicit != declared:
            problems.append(issue("target_identity_conflict", field + " CLI/manifest disagree"))
        report["target"][field] = explicit or declared
    product, build = report["target"]["ProductVersion"], report["target"]["BuildVersion"]
    if not isinstance(product, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", product):
        problems.append(issue("missing_product_version", "provide a concrete ProductVersion", "missing"))
    if not isinstance(build, str) or not re.fullmatch(r"\d+[A-Za-z]\d+[A-Za-z]?", build):
        problems.append(issue("missing_build_version", "provide a concrete BuildVersion", "missing"))
    report["target"]["source_declaration"] = manifest_value.get("source")
    if root is None:
        problems.append(issue("no_target_inputs", "no target directory or manifest provided", "missing"))
        return report
    root = Path(root).resolve(strict=True)
    require(root.is_dir(), "input root must be a directory")
    report["input_root"] = str(root)
    entries = manifest_value["files"] if manifest is not None else directory_entries(root)
    if not entries:
        problems.append(issue("no_target_inputs", "input inventory is empty", "missing"))
    remaining = MAX_TOTAL_BYTES
    for entry in entries:
        record, size = inspect_file(root, entry, remaining)
        remaining -= min(size, remaining)
        report["files"].append(record)
        problems.extend(dict(item, path=record["path"]) for item in record["issues"])
    by_role = {}
    for record in report["files"]:
        by_role.setdefault(record["role"], []).append(record)
    for role in ("dyld", "launchd", "system_version"):
        if not by_role.get(role):
            problems.append(issue("missing_role", "required input role absent: " + role, "missing"))
        elif len(by_role[role]) != 1:
            problems.append(issue("ambiguous_role", "require exactly one input for role: " + role))
    for role, records in by_role.items():
        for record in records:
            expected = ("system_version" if role == "system_version" else
                        "cache" if role in ("cache", "cache_subcache") else
                        "macho" if role in ("dyld", "launchd", "libsystem") else None)
            if expected and expected not in record:
                problems.append(issue("role_format_mismatch",
                                      "required role does not have parsed " + expected,
                                      path=record["path"]))
    if not (by_role.get("libsystem") or by_role.get("cache")):
        problems.append(issue("missing_runtime_libraries",
                              "provide libSystem or the explicitly inventoried shared-cache set",
                              "missing"))
    versions = [r["system_version"] for r in by_role.get("system_version", [])
                if "system_version" in r]
    if len(versions) == 1 and versions[0] == {"ProductVersion": product, "BuildVersion": build}:
        report["target"]["identity_verified"] = bool(product and build)
    else:
        problems.append(issue("system_version_unverified",
                              "SystemVersion.plist missing or differs from explicit target",
                              "unverified"))
    present = {r["target_path"]: r for r in report["files"] if r["status"] not in ("missing", "rejected")}
    for record in report["files"]:
        for image in record.get("macho", {}).get("slices", []):
            paths = ([{"command": "LC_LOAD_DYLINKER", "path": p}
                      for p in image["load_dylinker"]] + image["load_dylib"])
            for dependency in paths:
                path = dependency["path"]
                state = "present_unverified" if path in present else (
                    "missing" if path.startswith("/") else "unresolved")
                report["dependencies"].append(dict(dependency, source=record["path"], status=state))
                if state != "present_unverified":
                    problems.append(issue("dependency_" + state,
                                          path + "; cache index/runpaths not resolved",
                                          "missing" if state == "missing" else "unverified",
                                          record["path"]))
    if by_role.get("cache_subcache") and not by_role.get("cache"):
        problems.append(issue("missing_cache_main", "subcache supplied without a main cache", "missing"))
    if by_role.get("cache") or by_role.get("cache_subcache"):
        problems.append(issue("cache_companions_unverified",
                              "cache/subcache UUID pairing and missing companion discovery unsupported; "
                              "manifest labels do not prove membership", "unsupported"))
    report["structural_audit_complete"] = not problems
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="explicit ordinary directory; never searched recursively")
    parser.add_argument("--manifest", type=Path, help="schema v1 inventory; default root is its parent")
    parser.add_argument("--product-version", help="exact ProductVersion, e.g. the supplied plist value")
    parser.add_argument("--build-version", help="exact BuildVersion, e.g. ProductBuildVersion")
    args = parser.parse_args(argv)
    try:
        report = audit(args.root, args.manifest, args.product_version, args.build_version)
        exit_code = 1
    except (OSError, ValueError, struct.error, OverflowError, ExpatError, RecursionError) as error:
        report = {
            "schema_version": SCHEMA_VERSION, "tool": "audit_chiron_userspace",
            "files": [], "issues": [issue("invalid_input", str(error))],
            "compatibility_verified": False,
            "admission": {"eligible": False, "status": "blocked"},
        }
        exit_code = 2
    json.dump(report, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
