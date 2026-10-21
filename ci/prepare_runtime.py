#!/usr/bin/env python3
"""Adapt pinned upstream recipes to the current bootstrap's recorded paths."""

import argparse
import json
from pathlib import Path


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"upstream recipe changed: {path}: {old[:80]}")
    path.write_text(text.replace(old, new, 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path)
    args = parser.parse_args()
    scripts = args.tree / "scripts"
    replace_once(scripts / "build-corecrypto-14.py",
                 "SOURCE = ROOT / 'refer/corecrypto-2023'",
                 "SOURCE = (ROOT / 'refer/corecrypto-2023').resolve()")
    kernel = "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
    replace_once(scripts / "build-libpthread-kernel.py",
                 "build/xnu/checkpoints/native-4k-user-abi/kernel.macho", kernel)
    for filename in ("build-corecrypto-14.py", "build-libpthread-kernel.py"):
        replace_once(scripts / filename, "'-target', 'arm64-apple-ios17.0',",
                     "'-target', 'arm64-apple-ios17.0', '-march=armv8-a', '-mno-outline-atomics',")
    probe = scripts / "probe-darwinboot-xnu.py"
    replace_once(probe,
                 "user_inspections = [('_results', 32), ('_user_page_size', 8), ('_old_length', 8)]",
                 "user_inspections = [('_results', 32), ('_user_page_size', 8), ('_old_length', 8), "
                 "('_chiron_heartbeats', 8), ('_chiron_init_status', 8)]")
    hashes = {}
    import hashlib
    for name in ("build-corecrypto-14.py", "build-libpthread-kernel.py", "probe-darwinboot-xnu.py"):
        hashes[name] = hashlib.sha256((scripts / name).read_bytes()).hexdigest()
    (args.tree / "build/chiron-recipe-overlays.json").write_text(json.dumps(hashes, indent=2) + "\n")


if __name__ == "__main__":
    main()
