#!/usr/bin/env python3
"""Build two signed static ARM64 programs and the pinned N4KROOT RAM fixture."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
PAGE = 4096
MAX_ROOT = 32 * 1024 * 1024
MARKER = b"CHIRON-SELFTEST-INDEPENDENT-CHILD-v1\n"
MOCKFS_HASHES = {
    "mockfs_fsnode.h": "cbe32910e41513fa6aacd1a23226755c7f234f175f3b26dfaae99ddb1d2830c3",
    "mockfs_fsnode.c": "0947375cc875a38d32dae87dce5759281376464c883f0a70c09c22cebcd70c3b",
    "mockfs_vnops.c": "3b4237530d426258221307b525d8dc386047589f707cc9ace0ac3d35f7677b19",
    "mockfs_vfsops.c": "3eb58dae4dd332a44a9940270104807ba130e7fc40996a8b12b664624ef76a15",
}
RESULT_FIELDS = (
    "version", "required_mask", "passed_mask", "failure_stage",
    "child_pid", "wait_pid", "wait_status", "reserved",
)
DIAGNOSTIC_FIELDS = ("last_syscall", "last_return", "last_nzcv", "file_marker", "mmap_base")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(value):
    return (value + PAGE - 1) & -PAGE


def load_commands(data):
    if len(data) < 32 or struct.unpack_from("<4I", data) != (0xFEEDFACF, 0x100000C, 0, 2):
        raise ValueError("expected an ordinary ARM64 executable")
    ncmds, sizeofcmds = struct.unpack_from("<II", data, 16)
    end = 32 + sizeofcmds
    if end > len(data):
        raise ValueError("Mach-O commands exceed file")
    cursor = 32
    for _ in range(ncmds):
        if cursor + 8 > end:
            raise ValueError("truncated Mach-O load command")
        cmd, size = struct.unpack_from("<II", data, cursor)
        if size < 8 or size % 8 or cursor + size > end:
            raise ValueError("invalid Mach-O load command size")
        yield cmd, cursor, size
        cursor += size
    if cursor != end:
        raise ValueError("Mach-O command count/size mismatch")


def marker_offset(data):
    found = []
    for cmd, at, size in load_commands(data):
        if cmd != 0x19:
            continue
        if size < 72:
            raise ValueError("truncated segment")
        count = struct.unpack_from("<I", data, at + 64)[0]
        if 72 + count * 80 != size:
            raise ValueError("invalid section count")
        for index in range(count):
            sec = at + 72 + index * 80
            name, segment = struct.unpack_from("<16s16s", data, sec)
            if name.rstrip(b"\0") == b"__st_marker" and segment.rstrip(b"\0") == b"__TEXT":
                length, offset = struct.unpack_from("<QI", data, sec + 40)
                if length != len(MARKER) or data[offset:offset + length] != MARKER:
                    raise ValueError("child marker section does not match")
                found.append(offset)
    if len(found) != 1:
        raise ValueError("require one independent child marker section")
    return found[0]


def audit_static(data):
    commands = list(load_commands(data))
    dyld_commands = {0xC, 0xE, 0x10, 0x80000018, 0x8000001F, 0x20, 0x22,
                     0x80000022, 0x80000023, 0x27, 0x80000028, 0x80000034}
    if any(cmd in dyld_commands for cmd, _, _ in commands):
        raise ValueError("selftest programs must be static without dyld dependencies")
    deployments = [struct.unpack_from("<II", data, at + 8)
                   for cmd, at, size in commands if cmd == 0x32 and size >= 24]
    if deployments != [(2, 0x110000)]:
        raise ValueError("require ordinary iOS 17 deployment")
    if not any(cmd == 5 for cmd, _, _ in commands):
        raise ValueError("static executable requires LC_UNIXTHREAD")


def signature_info(data):
    """Verify SHA-256 CodeDirectory slots and derive the cache's 20-byte cdhash."""
    commands = list(load_commands(data))
    signatures = [(at, size) for cmd, at, size in commands if cmd == 0x1D]
    if len(signatures) != 1 or signatures[0][1] != 16:
        raise ValueError("require one LC_CODE_SIGNATURE")
    offset, length = struct.unpack_from("<II", data, signatures[0][0] + 8)
    signature = data[offset:offset + length]
    if len(signature) != length or length < 12:
        raise ValueError("truncated signature")
    magic, total, count = struct.unpack_from(">III", signature)
    if magic != 0xFADE0CC0 or total > length or 12 + count * 8 > total:
        raise ValueError("invalid signature SuperBlob")
    directories = []
    for index in range(count):
        slot, at = struct.unpack_from(">II", signature, 12 + index * 8)
        if at + 8 > total:
            raise ValueError("signature blob exceeds SuperBlob")
        blob_magic, size = struct.unpack_from(">II", signature, at)
        if size < 8 or at + size > total:
            raise ValueError("invalid signature blob length")
        if blob_magic == 0xFADE0C02:
            directories.append((slot, signature[at:at + size]))
    if len(directories) != 1 or directories[0][0] != 0:
        raise ValueError("require one primary CodeDirectory")
    cd = directories[0][1]
    if len(cd) < 44:
        raise ValueError("truncated CodeDirectory")
    hashoff, _, special, slots, limit = struct.unpack_from(">5I", cd, 16)
    hashsize, hashtype, _, pageshift = struct.unpack_from("4B", cd, 36)
    if (hashsize, hashtype, pageshift) != (32, 2, 12):
        raise ValueError("require SHA-256 and 4096-byte CodeDirectory slots")
    if limit != offset or slots != (limit + PAGE - 1) // PAGE:
        raise ValueError("code coverage differs from LC_CODE_SIGNATURE")
    if hashoff < 44 + special * 32 or hashoff + slots * 32 > len(cd):
        raise ValueError("CodeDirectory slots exceed bounds")
    for index in range(slots):
        expected = hashlib.sha256(data[index * PAGE:min((index + 1) * PAGE, limit)]).digest()
        if cd[hashoff + index * 32:hashoff + (index + 1) * 32] != expected:
            raise ValueError("CodeDirectory page hash mismatch")
    return {
        "cdhash": hashlib.sha256(cd).digest()[:20].hex(),
        "signature_hash_page_bytes": PAGE, "code_limit": limit, "code_slots": slots,
    }


def make_root(launchd, child):
    offset = align(PAGE + len(launchd))
    length = align(offset + len(child))
    if not launchd or not child or length > MAX_ROOT:
        raise ValueError("empty or oversized N4KROOT file")
    image = bytearray(length)
    struct.pack_into("<8sII4Q", image, 0, b"N4KROOT\0", 1, 2,
                     PAGE, len(launchd), offset, len(child))
    image[PAGE:PAGE + len(launchd)] = launchd
    image[offset:offset + len(child)] = child
    validate_root(image, launchd, child)
    return bytes(image)


def validate_root(image, launchd, child):
    if len(image) < PAGE or len(image) > MAX_ROOT or len(image) % PAGE:
        raise ValueError("invalid N4KROOT geometry")
    magic, version, count, first, first_size, second, second_size = struct.unpack_from("<8sII4Q", image)
    if (magic, version, count, first, first_size, second, second_size) != (
        b"N4KROOT\0", 1, 2, PAGE, len(launchd), align(PAGE + len(launchd)), len(child)
    ):
        raise ValueError("N4KROOT header differs from exact two-file layout")
    if len(image) != align(second + second_size):
        raise ValueError("N4KROOT has truncated or trailing blocks")
    if image[first:first + first_size] != launchd or image[second:second + second_size] != child:
        raise ValueError("N4KROOT file read-back mismatch")
    gaps = (image[48:first], image[first + first_size:second], image[second + second_size:])
    if any(any(gap) for gap in gaps):
        raise ValueError("N4KROOT padding is not zero")


def make_trustcache(cdhashes):
    hashes = sorted(bytes.fromhex(value) for value in cdhashes)
    if len(hashes) != 2 or len(set(hashes)) != 2 or any(len(h) != 20 for h in hashes):
        raise ValueError("require two distinct own-program cdhashes")
    identity = uuid.uuid5(uuid.NAMESPACE_URL, "org.ios-party.ram-selftest:" + "".join(h.hex() for h in hashes))
    module = struct.pack("<I16sI", 2, identity.bytes, len(hashes))
    module += b"".join(h + bytes((2, 0, 0, 0)) for h in hashes)
    return struct.pack("<II", 1, 8) + module


def verify_mockfs(tree):
    directory = tree / ".cache/xnu-work/bsd/miscfs/mockfs"
    actual = {name: sha((directory / name).read_bytes()) for name in MOCKFS_HASHES}
    if actual != MOCKFS_HASHES:
        raise ValueError("mockfs differs from the audited pinned two-file implementation")
    return actual


def symbols(path):
    output = subprocess.check_output(["xcrun", "nm", "-U", str(path)], text=True)
    return {name: int(at, 16) for at, name in re.findall(r"^([0-9a-fA-F]+)\s+\S\s+(_\S+)$", output, re.M)}


def build_program(source, name, output, sdk, defines=()):
    obj = output / (name + ".o")
    intermediate = output / (name + "-static.macho")
    path = output / (name + ".macho")
    subprocess.run(["xcrun", "clang", "-target", "arm64-apple-ios17.0", "-march=armv8-a",
                    "-isysroot", sdk, "-I", str(ROOT / "scripts"), *defines,
                    "-c", str(source), "-o", str(obj)], check=True)
    subprocess.run(["xcrun", "ld", "-arch", "arm64", "-static", "-e", "_start",
                    "-platform_version", "ios", "17.0", "17.0", str(obj), "-o", str(intermediate)], check=True)
    subprocess.run(["xcrun", "vtool", "-set-build-version", "ios", "17.0", "17.0",
                    "-replace", "-output", str(path), str(intermediate)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", "--pagesize", "4096",
                    "--identifier", "org.ios-party.ram-selftest." + name, str(path)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(path)], check=True)
    data = path.read_bytes()
    audit_static(data)
    signature = signature_info(data)
    details = subprocess.run(["codesign", "--display", "--verbose=4", str(path)],
                             capture_output=True, text=True, check=True).stderr
    if not re.search(r"^CDHash=" + signature["cdhash"] + r"$", details, re.M):
        raise ValueError("codesign and parsed CodeDirectory disagree")
    return data, signature, symbols(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, help="prepared pinned UniXNU tree; verify mockfs source hashes")
    parser.add_argument("--output", type=Path, help="default: <tree>/build/native-user-fixture")
    args = parser.parse_args()
    if args.output is None and args.tree is None:
        parser.error("provide --tree or --output")
    mockfs = verify_mockfs(args.tree.resolve()) if args.tree else None
    output = (args.output or args.tree / "build/native-user-fixture").resolve()
    output.mkdir(parents=True, exist_ok=True)
    # A failed rebuild must not leave an old report that appears qualified.
    (output / "selftest-build.json").unlink(missing_ok=True)
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
    child, child_signature, child_symbols = build_program(
        ROOT / "scripts/chiron-selftest-child.S", "child", output, sdk)
    offset = marker_offset(child)
    launchd, launch_signature, launch_symbols = build_program(
        ROOT / "scripts/chiron-init.S", "launchd", output, sdk,
        ("-DCHIRON_RAM_SELFTEST=1", f"-DCHIRON_SELFTEST_MARKER_OFFSET={offset}",
         f"-DCHIRON_SELFTEST_CHILD_SIZE={len(child)}"))
    required = {"_start", "_checkpoint", "_results", "_user_page_size", "_old_length",
                "_chiron_heartbeats", "_chiron_init_status", "_chiron_selftest_results",
                "_chiron_selftest_diagnostics", "_chiron_selftest_failure_checkpoint"}
    if not required <= launch_symbols.keys() or launchd == child:
        raise ValueError("missing compatibility/selftest symbols or duplicate child image")
    if "_chiron_selftest_child_entry" not in child_symbols:
        raise ValueError("independent child entry missing")
    root = make_root(launchd, child)
    trust = make_trustcache((launch_signature["cdhash"], child_signature["cdhash"]))
    (output / "root.img").write_bytes(root)
    (output / "trustcache.bundle").write_bytes(trust)
    (output / "addresses.json").write_text(json.dumps(launch_symbols, indent=2) + "\n")
    (output / "child-addresses.json").write_text(json.dumps(child_symbols, indent=2) + "\n")
    sources = ["scripts/chiron-init.S", "scripts/chiron-selftest-main.S",
               "scripts/chiron-selftest-child.S", "ci/build_ram_selftest.py"]
    artifacts = ("launchd.macho", "child.macho", "root.img", "trustcache.bundle",
                 "addresses.json", "child-addresses.json")
    report = {
        "schema_version": 1, "purpose": "own static RAM user selftest",
        "source_sha256": {name: sha((ROOT / name).read_bytes()) for name in sources},
        "artifacts": {name: {"sha256": sha((output / name).read_bytes()),
                             "bytes": (output / name).stat().st_size} for name in artifacts},
        "mockfs_source_sha256": mockfs, "mockfs_source_verified": mockfs is not None,
        "expected_mockfs_source_sha256": MOCKFS_HASHES,
        "root_format": "N4KROOT", "root_version": 1, "root_file_count": 2,
        "root_paths": {"/sbin/launchd": "launchd.macho", "/probe.bin": "child.macho"},
        "child_marker_offset": offset, "child_marker_hex": MARKER.hex(),
        "signatures": {"launchd": launch_signature, "child": child_signature},
        "trustcache_entries": 2,
        "result_abi": {"symbol": "_chiron_selftest_results", "format": "<8Q",
                       "fields": RESULT_FIELDS, "version": 1, "required_mask": 15,
                       "bits": {"file": 1, "mmap": 2, "independent_exec": 4, "wait_reap": 8},
                       "child_exit_status": 37, "wait_status": 37 << 8, "reserved": 0},
        "diagnostics_abi": {"symbol": "_chiron_selftest_diagnostics", "format": "<5Q",
                            "fields": DIAGNOSTIC_FIELDS},
        "kernel_compiled": False, "qemu_runtime_qualified": False,
        "hardware_tested": False, "apple_trust_chain_verified": False,
    }
    (output / "selftest-build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), "root_sha256": sha(root),
                      "static_build_verified": True, "runtime_qualified": False}, indent=2))


if __name__ == "__main__":
    main()
