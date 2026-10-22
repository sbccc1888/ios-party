#!/usr/bin/env python3
"""Build a separate signed CHRAMFS ABI fixture; no kernel build or runtime claim."""

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ramfs_probe_abi_builder", ROOT / "ci/build_chiron_abi_probe.py")
ABI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ABI)
RAM = ABI.RAM
sys.path.insert(0, str(ROOT / "tools"))
import chiron_ramfs as RAMFS

REPORT = ABI.REPORT
PURPOSE = "bounded independent CHRAMFS static ARM64 system interface probe"
WRAPPER = ROOT / "scripts/chiron-ramfs-probe.S"
CHILD_PATH = "/usr/lib/chiron/probe.bin"
DECOY = b"CHRAMFS PATH DECOY: /probe.bin IS NOT THE MACH-O CHILD.\n"
DIRECTORIES = ("/", "/dev", "/sbin", "/usr", "/usr/lib", "/usr/lib/chiron")
ROOT_PATHS = {
    "/sbin/launchd": "launchd.macho",
    CHILD_PATH: "child.macho",
    "/probe.bin": "probe-decoy.txt",
}
COMPAT_SYMBOLS = {
    "_start", "_checkpoint", "_results", "_user_page_size", "_old_length",
    "_chiron_heartbeats", "_chiron_init_status",
    "_chiron_abi_results", "_chiron_abi_results_end",
}


def require_ramfs_macro(source=None):
    """Fail closed if the coordinated assembly extension is not available yet."""
    source = source or ROOT / "scripts/chiron-abi-probe.S"
    text = source.read_text()
    if (not all(token in text for token in (
            "CHIRON_ABI_RAMFS", "CHIRON_ABI_FILE_SUM", CHILD_PATH, "Lfile_sum:")) or
            not re.search(r"\bSTAGE\s+65\b", text)):
        raise ValueError("D's RAMFS path/whole-file checksum macro is not ready")


def check_destination(output, tree=None):
    output = Path(output).resolve()
    if tree:
        baseline = (Path(tree) / "build/native-user-fixture").resolve()
        if output == baseline or baseline in output.parents or output in baseline.parents:
            raise ValueError("RAMFS probe requires a separate output from the normal fixture")
    existing = output / REPORT
    if existing.exists():
        previous = json.loads(existing.read_text())
        if previous.get("purpose") != PURPOSE or previous.get("root_format") != "CHRAMFS":
            raise ValueError("RAMFS probe requires a separate output from an existing ABI fixture")


def make_root(launchd, child):
    if not launchd or not child or child == DECOY:
        raise ValueError("RAMFS fixture requires nonempty distinct executable inputs")
    entries = [RAMFS.Entry(path, "dir", mode=0o755) for path in DIRECTORIES]
    entries += [
        RAMFS.Entry("/sbin/launchd", "file", launchd, mode=0o555),
        RAMFS.Entry(CHILD_PATH, "file", child, mode=0o555),
        RAMFS.Entry("/probe.bin", "file", DECOY, mode=0o444),
    ]
    image = RAMFS.build(entries)
    validate_root(image, launchd, child)
    return image


def validate_root(image, launchd, child):
    records = RAMFS.parse(image)
    paths = {entry["path"]: entry for entry in records}
    if set(paths) != set(DIRECTORIES) | set(ROOT_PATHS):
        raise ValueError("RAMFS fixture path set differs from the exact experiment")
    for path in DIRECTORIES:
        entry = paths[path]
        if (entry["kind"], entry["mode"], entry["uid"], entry["gid"]) != ("dir", 0o755, 0, 0):
            raise ValueError("RAMFS fixture directory metadata mismatch")
    contents = {"/sbin/launchd": launchd, CHILD_PATH: child, "/probe.bin": DECOY}
    for path, data in contents.items():
        entry = paths[path]
        mode = 0o444 if path == "/probe.bin" else 0o555
        if ((entry["kind"], entry["mode"], entry["uid"], entry["gid"], entry["size"]) !=
                ("file", mode, 0, 0, len(data)) or RAMFS.read_file(image, path) != data):
            raise ValueError("RAMFS fixture file metadata/readback mismatch: " + path)
    if RAMFS.read_file(image, CHILD_PATH) == RAMFS.read_file(image, "/probe.bin"):
        raise ValueError("RAMFS path alias trap is not distinct")
    return records


def build(output, tree=None, abi_source=None):
    output = Path(output).resolve()
    tree = Path(tree).resolve() if tree is not None else None
    abi_source = Path(abi_source).resolve() if abi_source is not None else None
    check_destination(output, tree)
    (output / REPORT).unlink(missing_ok=True)
    require_ramfs_macro()
    inputs = (
        "scripts/chiron-abi-probe.S", "scripts/chiron-selftest-child.S",
        "scripts/chiron-ramfs-probe.S", "ci/build_chiron_abi_probe.py",
        "ci/build_ram_selftest.py", "ci/validate_chiron_abi_probe.py",
        "ci/build_chiron_ramfs_probe.py", "tools/chiron_ramfs.py",
    )
    source_snapshot = {name: RAM.sha((ROOT / name).read_bytes()) for name in inputs}
    # Retain D's exact default source, dispatch and normal-mockfs checks.
    # This is an unmodified normal tree, never the RAMFS kernel overlay tree.
    base = ABI.build(output, tree=tree, abi_source=abi_source)
    # The intermediate N4KROOT report must not survive a failed wrapper rebuild.
    (output / REPORT).unlink()
    child = (output / "child.macho").read_bytes()
    child_signature = RAM.signature_info(child)
    if child_signature != base["signatures"]["child"]:
        raise ValueError("signed child changed after the ABI seed build")
    marker = RAM.marker_offset(child)
    byte_sum = sum(child) & ((1 << 64) - 1)
    defines = (
        f"-DCHIRON_ABI_CHILD_SIZE={len(child)}",
        f"-DCHIRON_ABI_CHILD_MAP_SIZE={RAM.align(len(child))}",
        f"-DCHIRON_ABI_MARKER_OFFSET={marker}",
        f"-DCHIRON_ABI_FILE_SUM=0x{byte_sum:016x}",
    )
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
    launchd, signature, addresses = RAM.build_program(WRAPPER, "launchd", output, sdk, defines)
    if (not COMPAT_SYMBOLS <= addresses.keys() or
            addresses["_chiron_abi_results_end"] - addresses["_chiron_abi_results"] !=
            ABI.RESULT_ABI["bytes"]):
        raise ValueError("RAMFS wrapper changed the compatibility/result ABI")
    if (CHILD_PATH.encode() + b"\0") not in launchd:
        raise ValueError("RAMFS wrapper did not compile the nested child path")
    image = make_root(launchd, child)
    trust = RAM.make_trustcache([signature["cdhash"], child_signature["cdhash"]])
    (output / "root.img").write_bytes(image)
    (output / "trustcache.bundle").write_bytes(trust)
    (output / "addresses.json").write_text(json.dumps(addresses, indent=2) + "\n")
    (output / "probe-decoy.txt").write_bytes(DECOY)
    report = copy.deepcopy(base)
    report.update({
        "purpose": PURPOSE,
        "root_format": "CHRAMFS", "root_version": 1,
        "root_file_count": 3, "root_node_count": 9,
        "root_paths": dict(ROOT_PATHS), "root_directories": list(DIRECTORIES),
        "child_marker_offset": marker,
        "file_map_bytes": RAM.align(len(child)), "file_byte_sum": byte_sum,
        "file_byte_sum_algorithm": "uint64 unsigned-byte sum modulo 2^64",
        "whole_file_readback_requested": True, "whole_file_readback_failure_stage": 65,
        "whole_file_readback_runtime_verified": False,
        "wrapper_source": str(WRAPPER.relative_to(ROOT)),
        "wrapper_defines": list(defines),
        "mockfs_source_role": "unmodified normal-tree source check only; not RAMFS overlay qualification",
        "signatures": {"launchd": signature, "child": child_signature},
        "kernel_compiled": False, "ramfs_mount_verified": False, "file_pagein_verified": False,
        "qemu_runtime_qualified": False, "hardware_tested": False,
    })
    if any(RAM.sha((ROOT / name).read_bytes()) != digest for name, digest in source_snapshot.items()):
        raise ValueError("RAMFS fixture source changed during the coordinated build")
    report["source_sha256"].update(source_snapshot)
    names = tuple(base["artifacts"]) + ("probe-decoy.txt",)
    report["artifacts"] = {
        name: {"sha256": RAM.sha((output / name).read_bytes()),
               "bytes": (output / name).stat().st_size} for name in names
    }
    # Revalidate final root and signatures before publishing the only final report.
    validate_root((output / "root.img").read_bytes(), launchd, child)
    for name in ("launchd", "child"):
        data = (output / (name + ".macho")).read_bytes()
        RAM.audit_static(data)
        if RAM.signature_info(data) != report["signatures"][name]:
            raise ValueError("final RAMFS fixture signature changed: " + name)
    report["static_build_verified"] = True
    (output / REPORT).write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, help="unmodified prepared tree; D's default source checks")
    parser.add_argument("--output", type=Path, required=True, help="separate RAMFS probe output")
    parser.add_argument("--abi-source", type=Path, help="D's exact pinned ABI mirror override")
    args = parser.parse_args()
    report = build(args.output, args.tree, args.abi_source)
    print(json.dumps({
        "output": str(args.output.resolve()), "report": REPORT,
        "root_format": report["root_format"], "root_version": report["root_version"],
        "result_bytes": report["result_abi"]["bytes"], "file_byte_sum": report["file_byte_sum"],
        "static_build_verified": True, "runtime_qualified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
