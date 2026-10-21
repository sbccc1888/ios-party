#!/usr/bin/env python3
"""Pack a compile-only chiron UEFI image using the measured boot v0 layout."""

import argparse
import gzip
import hashlib
import json
import struct
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--dtb", type=Path, help="Explicit DTB from a verified firmware artifact")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ramdisk", type=Path, help="Verified raw CHIRONESP FAT32 transport")
    args = parser.parse_args()
    fd = (args.output / "chiron-unixnu.fd").read_bytes()
    if args.dtb:
        dtb = args.dtb.read_bytes()
    elif args.source:
        dtb = (args.source / "Platforms/Msm8998Pkg/Device/xiaomi/chiron/DeviceTreeBlob/Android/Android-chiron.dtb").read_bytes()
    else:
        parser.error("--source or --dtb is required")
    if len(fd) != 0x600000 or fd[56:60] != b"ARM\x64":
        raise SystemExit("unexpected firmware size or ARM64 header")
    if struct.unpack_from("<Q", fd, 16)[0] != len(fd):
        raise SystemExit("ARM64 Image size does not cover the firmware volume")
    if dtb[:4] != b"\xd0\x0d\xfe\xed":
        raise SystemExit("unexpected Android device tree")
    ramdisk = args.ramdisk.read_bytes() if args.ramdisk else b""
    if args.ramdisk and not ramdisk:
        raise SystemExit("empty RAM transport")
    if ramdisk:
        from build_ram_payload import validate_fat_header
        if len(ramdisk) > 0x8000000 or len(ramdisk) & 4095:
            raise SystemExit("RAM transport is too large or not page aligned")
        validate_fat_header(ramdisk[:512], len(ramdisk))
    kernel = gzip.compress(fd, mtime=0) + dtb
    page = bytearray(4096)
    page[:8] = b"ANDROID!"
    # Addresses match the supplied boot v0 container; execution is unqualified.
    version = (15 << 25) | (26 << 4) | 8
    struct.pack_into("<10I", page, 8, len(kernel), 0x8000, len(ramdisk), 0x1000000, 0, 0, 0x100, 4096, 0, version)
    command = b"androidboot.hardware=qcom console=null"
    page[64:64 + len(command)] = command
    digest = hashlib.sha1()
    for data in (kernel, ramdisk, b""):
        digest.update(data)
        digest.update(struct.pack("<I", len(data)))
    page[576:608] = digest.digest().ljust(32, b"\0")
    image = bytes(page) + kernel + bytes(-len(kernel) % 4096) + ramdisk + bytes(-len(ramdisk) % 4096)
    path = args.output / ("chiron-ram-init-experimental.img" if ramdisk else "chiron-uefi-experimental.img")
    path.write_bytes(image)
    (args.output / "firmware-report.json").write_text(json.dumps({
        "image": path.name, "sha256": hashlib.sha256(image).hexdigest(),
        "bytes": len(image), "header_version": 0,
        "firmware_sha256": hashlib.sha256(fd).hexdigest(),
        "arm64_image_size": len(fd),
        "dtb_sha256": hashlib.sha256(dtb).hexdigest(),
        "hardware_executed": False, "contains_xnu_payload": bool(ramdisk),
        "ramdisk_bytes": len(ramdisk),
        "status": "experimental compiled container; no fastboot command is run",
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
