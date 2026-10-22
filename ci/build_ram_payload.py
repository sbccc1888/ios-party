#!/usr/bin/env python3
"""Build a read-only FAT32 transport from the actually observed RAM-init inputs."""

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

FILES = {
    "kernel.macho": "kernel_sha256",
    "launchd.macho": "launchd.macho_sha256",
    "trustcache.bundle": "trustcache.bundle_sha256",
    "kernel.macho.adt": "kernel.macho.adt_sha256",
}


def require_runtime(directory):
    report = json.loads((directory / "runtime-test.json").read_text())
    if (
        report.get("init_pid") != 1 or report.get("init_heartbeats", 0) < 2
        or not report.get("native_4k_mmu_observed")
        or not report.get("real_crypto_post_and_corruption_refusal")
        or not report.get("source_pthread_registered")
    ):
        raise ValueError("the real QEMU runtime checkpoints have not passed")
    if (int(report["initial_tcr"], 16) >> 32) & 7 != 2:
        raise ValueError("kernel boot address size does not match the measured 40-bit CPU")
    for filename, key in FILES.items():
        digest = hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        if digest != report.get(key):
            raise ValueError(f"runtime artifact hash mismatch: {filename}")
    selftest = report.get("user_selftest")
    has_selftest = (directory / "selftest-build.json").exists() or "root.img_sha256" in report
    if selftest or has_selftest:
        if not isinstance(selftest, dict) or (
            selftest.get("schema") != 1 or selftest.get("required_mask") != 15
            or selftest.get("passed_mask") != 15
            or not all(selftest.get(name) is True for name in (
                "file_read_passed", "anonymous_mmap_passed", "independent_exec_passed", "child_reaped"))
            or selftest.get("child_pid", 0) <= 1
            or selftest.get("wait_pid") != selftest.get("child_pid")
            or selftest.get("wait_status") != 37 << 8
        ):
            raise ValueError("the complete user selftest was not observed")
        for filename in ("root.img", "child.macho"):
            data = (directory / filename).read_bytes()
            if hashlib.sha256(data).hexdigest() != report.get(filename + "_sha256"):
                raise ValueError(f"selftest artifact hash mismatch: {filename}")
    return report


def validate_fat_header(header, size):
    if (
        len(header) != 512 or header[510:512] != b"\x55\xaa"
        or struct.unpack_from("<H", header, 11)[0] != 512
        or header[71:82] != b"CHIRONESP  " or header[82:90] != b"FAT32   "
        or struct.unpack_from("<I", header, 32)[0] * 512 != size
    ):
        raise ValueError("unexpected FAT32 transport geometry or identity")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    observed = require_runtime(args.runtime)
    for name in ("mformat", "mmd", "mcopy", "mdir"):
        if not shutil.which(name):
            parser.error(f"missing tool: {name}")
    image = args.output
    image.parent.mkdir(parents=True, exist_ok=True)
    with image.open("xb") as stream:
        stream.truncate(48 * 1024 * 1024)
    subprocess.run(["mformat", "-i", str(image), "-F", "-v", "CHIRONESP", "::"], check=True)
    subprocess.run(["mmd", "-i", str(image), "::/EFI", "::/EFI/UniXNU", "::/EFI/UniXNU/Payloads"], check=True)
    mapping = {
        "kernel.macho": "kernel.macho", "kernel.macho.adt": "kernel.macho.adt",
        "launchd.macho": "launchd.macho", "trustcache.bundle": "kernel.macho.trustcache",
    }
    root_name = "launchd.macho"
    if observed.get("user_selftest"):
        mapping["root.img"] = "root.img"
        root_name = "root.img"
    for source, target in mapping.items():
        subprocess.run(["mcopy", "-i", str(image), str(args.runtime / source),
                        "::/EFI/UniXNU/Payloads/" + target], check=True)
    title = "Chiron native 4K XNU / RAM init"
    trace_build = args.runtime / "source-trace-build.json"
    if trace_build.exists():
        title = json.loads(trace_build.read_text())["build_id"]
    config = f'''version = 1
[boot]
timeout = 5
default = "chiron-ram-init"
[kits]
autoload = false
[[entry]]
id = "chiron-ram-init"
title = {json.dumps(title)}
type = "xnu"
path = "Payloads/kernel.macho"
initrd = "Payloads/{root_name}"
args = "-v serial=3 debug=0x14e cpus=1 rd=md0 keepsyms=1 kt_fixed_zones=120"
[[entry]]
id = "firmware"
title = "Return to firmware"
type = "action"
action = "exit"
'''
    with tempfile.TemporaryDirectory(prefix="chiron-volume-check-") as temporary:
        root = Path(temporary)
        cfg = root / "unixnu.toml"
        cfg.write_text(config)
        subprocess.run(["mcopy", "-i", str(image), str(cfg), "::/EFI/UniXNU/unixnu.toml"], check=True)
        for source, target in mapping.items():
            recovered = root / target
            subprocess.run(["mcopy", "-i", str(image), "::/EFI/UniXNU/Payloads/" + target, str(recovered)], check=True)
            if hashlib.sha256(recovered.read_bytes()).digest() != hashlib.sha256((args.runtime / source).read_bytes()).digest():
                raise ValueError("FAT read-back mismatch")
    with image.open("rb") as stream:
        validate_fat_header(stream.read(512), image.stat().st_size)
    subprocess.run(["mdir", "-i", str(image), "::/EFI/UniXNU/Payloads"], check=True)
    subprocess.run(["fsck.fat", "-n", str(image)], check=True)
    image.with_suffix(".json").write_text(json.dumps({
        "volume": "CHIRONESP", "bytes": image.stat().st_size,
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "source_runtime": observed, "fat_read_back_verified": True,
        "hardware_boot_qualified": False, "transport": "bootloader RAM disk; readonly EFI block I/O",
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
