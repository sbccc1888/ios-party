#!/usr/bin/env python3
"""Compile experimental RAMFS units with actual XNU flags, outside the runtime tree."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ramfs_overlay", ROOT / "ci/apply_chiron_ramfs.py")
OVERLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OVERLAY)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_arguments(record, source, obj, includes):
    original = iter(record["arguments"])
    command = []
    replaced = False
    for value in original:
        if value == record["file"]:
            command.append(str(source))
            replaced = True
        elif value in ("-o", "-MF", "-MJ", "-serialize-diagnostics", "--serialize-diagnostics"):
            next(original)
            if value == "-o":
                command.extend(["-o", str(obj)])
        elif value not in ("-MD", "-MP") and not value.startswith(("-DMOCKFS=", "-UMOCKFS")):
            command.append(value)
    if not replaced or "-o" not in command:
        raise ValueError("XNU compile record lacks source or output")
    command[1:1] = ["-I", str(includes)]
    command.append("-DMOCKFS=1")
    return command


def build(tree, output):
    tree, output = tree.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_root = output / "source"
    destination = source_root / OVERLAY.RELATIVE
    destination.mkdir(parents=True)
    original = tree / ".cache/xnu-work" / OVERLAY.RELATIVE
    for name in OVERLAY.BASELINE:
        shutil.copyfile(original / name, destination / name)
    overlay = OVERLAY.apply(source_root, enable_experimental=True)
    (output / "overlay.json").write_text(json.dumps(overlay, indent=2) + "\n")
    recipe = tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/vfs_conf.o.json"
    record = json.loads(recipe.read_text().strip().rstrip(","))
    objects = []
    for name in ("mockfs_fsnode", "mockfs_vfsops", "mockfs_vnops"):
        source, obj = destination / (name + ".c"), output / (name + ".o")
        command = compile_arguments(record, source, obj, source_root / "bsd")
        result = subprocess.run(command, cwd=record["directory"], capture_output=True)
        (output / (name + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"{name} XNU compilation failed; see {output / (name + '.log')}")
        if struct.unpack_from("<4I", obj.read_bytes()) != (0xFEEDFACF, 0x100000C, 0, 1):
            raise ValueError("RAMFS unit is not an ordinary ARM64 Mach-O object")
        objects.append(obj)
    archive = output / "libChironRamfs.a"
    subprocess.run(["xcrun", "ar", "rcs", str(archive), *map(str, objects)], check=True)
    report = {
        "schema": 1, "format": "CHRAMFS", "version": 1,
        "compile_record": str(recipe.relative_to(tree)), "compile_record_sha256": sha(recipe),
        "overlay": OVERLAY.verify(source_root),
        "objects": {obj.name: sha(obj) for obj in objects},
        "archive": archive.name, "archive_sha256": sha(archive),
        "real_xnu_units_compiled": True,
        "linked_into_kernel": False, "qemu_mounted": False, "hardware_tested": False,
        "normal_runtime_tree_modified": False,
    }
    (output / "compile-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.tree, args.output), indent=2))


if __name__ == "__main__":
    main()
