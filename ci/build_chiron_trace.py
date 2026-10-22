#!/usr/bin/env python3
"""Build the source observer archive with a numbered identity and a code audit."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "build/boot-progress"
    output.mkdir(parents=True, exist_ok=True)
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
    clang = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--find", "clang"], text=True).strip()
    identity = ("CT-" + os.environ.get("GITHUB_RUN_ID", "LOCAL") + "-" +
                os.environ.get("GITHUB_RUN_ATTEMPT", "1") + "-" +
                os.environ.get("GITHUB_SHA", "unversioned")[:12])
    objects = []
    for name in ("chiron-trace.c", "chiron-trace-entry.S"):
        source = root / "diagnostics" / name
        obj = output / (name.replace(".", "-") + ".o")
        command = [clang, "-target", "arm64-apple-ios17.0", "-isysroot", sdk,
                   "-march=armv8-a", "-mgeneral-regs-only", "-mno-outline-atomics",
                   "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-mkernel",
                   "-fno-pic", "-fno-common", "-fvisibility=hidden", "-fno-jump-tables",
                   "-O2", "-g", '-DCHIRON_TRACE_BUILD_ID="' + identity + '"',
                   "-c", str(source), "-o", str(obj)]
        subprocess.run(command, check=True)
        assembly = subprocess.check_output(["xcrun", "llvm-objdump", "-d", str(obj)], text=True)
        relocations = subprocess.check_output(["xcrun", "llvm-objdump", "-r", str(obj)], text=True)
        if re.search(r"\b(?:q[0-9]+|v[0-9]+\.[0-9a-z]+)\b", assembly):
            raise ValueError("observer contains FP/SIMD instructions")
        if "GOT_LOAD" in relocations:
            raise ValueError("early observer must use direct PC-relative references")
        if re.search(r"\bmsr\s+(?:TTBR1_EL1|TCR_EL1|SCTLR_EL1)\b", assembly, re.I):
            raise ValueError("observer may not change kernel translation controls")
        (output / (obj.stem + ".asm")).write_text(assembly)
        objects.append(obj)
    archive = output / "libBootProgress.a"
    subprocess.run(["xcrun", "ar", "rcs", str(archive), *map(str, objects)], check=True)
    (output / "source-trace-build.json").write_text(json.dumps({
        "schema": 1, "build_id": identity, "kernel_binary_patching": False,
        "source_commit": os.environ.get("GITHUB_SHA", "unversioned"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "LOCAL"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "compiler": subprocess.check_output([clang, "--version"], text=True).strip(),
        "sdk_version": subprocess.check_output(
            ["xcrun", "--sdk", "iphoneos", "--show-sdk-version"], text=True).strip(),
        "wire_version": 1, "wire_bytes": 65536,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (root / "diagnostics").iterdir() if p.is_file()},
        "fp_simd_absent": True, "got_relocations_absent": True,
        "efi_inputs": {
            name: hashlib.sha256((Path(os.environ["GITHUB_WORKSPACE"]) / "dist/efi" / name).read_bytes()).hexdigest()
            for name in ("UniXNU.efi", "DarwinBoot.efi")
        } if os.environ.get("GITHUB_WORKSPACE") else {},
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
