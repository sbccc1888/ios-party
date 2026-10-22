#!/usr/bin/env python3
"""Build inspected firmware-only or legacy-firmware RAM transport controls."""

import argparse
import hashlib
import json
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

from inspect_chiron_boot import inspect


def read_inspected(source, directory):
    inspect(source, directory)
    report = json.loads((directory / "inspection.json").read_text())
    if not report["legacy_sha1_matches"] or report["trailing_bytes"]:
        raise ValueError("input boot image integrity check failed")
    if report["header"]["second_size"]:
        raise ValueError("second-stage payloads are not supported")
    if len(report["appended_dtbs"]) != 1:
        raise ValueError("expected one chiron DTB")
    blob = source.read_bytes()
    if hashlib.sha256(blob).hexdigest() != report["source_sha256"]:
        raise ValueError("input changed during inspection")
    return blob, report


def make_control(source, output, firmware_source=None, uncompressed=False, ramdisk_prefix_bytes=None):
    if output.exists():
        raise ValueError("output directory already exists")
    if ramdisk_prefix_bytes is not None and firmware_source is None:
        raise ValueError("RAM prefix controls require legacy firmware without the RAM-volume driver")
    with tempfile.TemporaryDirectory(prefix="chiron-firmware-control-") as temporary:
        work = Path(temporary)
        blob, report = read_inspected(source, work / "input")
        if report["header"]["ramdisk_size"] == 0:
            raise ValueError("input has no RAM transport")
        firmware_blob, firmware_report = blob, report
        ramdisk = b""
        name = "chiron-same-firmware-no-ram.img"
        purpose = "Firmware-only control; contains no XNU/init RAM transport."
        limits = [
            "Removing initrd changes bootloader placement and the early reserve path.",
            "A successful control boot does not identify which of those caused the failure.",
        ]
        if firmware_source:
            firmware_blob, firmware_report = read_inspected(firmware_source, work / "firmware")
            if firmware_report["header"]["ramdisk_size"]:
                raise ValueError("firmware source must have no RAM transport")
            for field in ("kernel_addr", "ramdisk_addr", "tags_addr", "page_size"):
                if firmware_report["header"][field] != report["header"][field]:
                    raise ValueError(f"firmware source has incompatible {field}")
            if firmware_report["appended_dtbs"][0]["sha256"] != report["appended_dtbs"][0]["sha256"]:
                raise ValueError("firmware source has a different DTB")
            offset = report["payload_offsets"]["ramdisk"]
            ramdisk = blob[offset:offset + report["header"]["ramdisk_size"]]
            name = "chiron-legacy-firmware-same-ram.img"
            purpose = "Legacy firmware with unchanged RAM transport; no RAM-volume driver."
            limits = [
                "Legacy firmware does not expose the RAM volume or launch its XNU payload.",
                "Boot success narrows investigation to firmware initrd handling; it is not an XNU boot.",
            ]
        offset = firmware_report["payload_offsets"]["kernel"]
        kernel = firmware_blob[offset:offset + firmware_report["header"]["kernel_size"]]
        expected_kernel_sha256 = hashlib.sha256(kernel).hexdigest()
        if uncompressed:
            if firmware_report["kernel_compression"] != "gzip":
                raise ValueError("uncompressed control requires a gzip firmware source")
            decompressor = zlib.decompressobj(31)
            firmware = decompressor.decompress(kernel, 0x600001)
            if (
                not decompressor.eof or len(firmware) != 0x600000
                or firmware[56:60] != b"ARM\x64"
                or struct.unpack_from("<Q", firmware, 16)[0] != len(firmware)
            ):
                raise ValueError("expected a complete 6 MiB ARM64 firmware volume")
            kernel = firmware + decompressor.unused_data
            expected_kernel_sha256 = hashlib.sha256(kernel).hexdigest()
            offset = report["payload_offsets"]["ramdisk"]
            ramdisk = blob[offset:offset + report["header"]["ramdisk_size"]]
            name = ("chiron-legacy-raw-firmware-same-ram.img" if firmware_source
                    else "chiron-raw-firmware-same-ram.img")
            purpose = "Uncompressed firmware with unchanged RAM transport; skips bootloader gzip."
            limits = [
                "This changes kernel encoding, size, and subsequent container offsets.",
                "Success supports a gzip/placement interaction but does not prove a specific overwrite.",
            ]
            if firmware_source:
                limits.append("Legacy firmware cannot expose the RAM volume or start its XNU payload.")
        if ramdisk_prefix_bytes is not None:
            if (
                ramdisk_prefix_bytes < 4096 or ramdisk_prefix_bytes % 4096
                or ramdisk_prefix_bytes > len(ramdisk)
            ):
                raise ValueError("RAM prefix must be page aligned, at least 4096 bytes, and within the source")
            ramdisk = ramdisk[:ramdisk_prefix_bytes]
            encoding = "raw-" if uncompressed else ""
            name = f"chiron-legacy-{encoding}ram-{ramdisk_prefix_bytes // 1024}k.img"
            purpose = "Legacy firmware with a small nonempty prefix of the original RAM transport."
            limits = [
                "This truncated transport is not a valid mountable FAT volume and contains no complete XNU payload.",
                "Legacy firmware does not mount it; this only tests bootloader and common early initrd handling.",
                "Success distinguishes this prefix from the full transport, not a specific maximum supported size.",
            ]
        page_size = firmware_report["header"]["page_size"]
        data = bytearray(firmware_blob[:page_size])
        data.extend(kernel)
        data.extend(bytes(-len(kernel) % page_size))
        data.extend(ramdisk)
        data.extend(bytes(-len(ramdisk) % page_size))
        struct.pack_into("<I", data, 8, len(kernel))
        struct.pack_into("<I", data, 16, len(ramdisk))
        digest = hashlib.sha1()
        for payload in (kernel, ramdisk, b""):
            digest.update(payload)
            digest.update(struct.pack("<I", len(payload)))
        data[576:608] = digest.digest().ljust(32, b"\0")
        image = work / "control.img"
        image.write_bytes(data)
        verified = work / "verified"
        inspect(image, verified)
        control = json.loads((verified / "inspection.json").read_text())
        if (
            not control["legacy_sha1_matches"]
            or control["header"]["ramdisk_size"] != len(ramdisk)
            or control["payload_sha256"]["kernel"] != expected_kernel_sha256
            or control["payload_sha256"]["ramdisk"] != hashlib.sha256(ramdisk).hexdigest()
            or control["kernel_image_sha256"] != firmware_report["kernel_image_sha256"]
            or control["appended_dtbs"][0]["sha256"] != firmware_report["appended_dtbs"][0]["sha256"]
        ):
            raise ValueError("control payloads failed integrity validation")
        output.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(image, output / name)
        (output / "control-report.json").write_text(json.dumps({
            "source_sha256": report["source_sha256"],
            "firmware_source_sha256": firmware_report["source_sha256"],
            "image": name,
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "bytes": image.stat().st_size,
            "same_kernel_payload": control["payload_sha256"]["kernel"] == report["payload_sha256"]["kernel"],
            "kernel_payload_sha256": control["payload_sha256"]["kernel"],
            "kernel_compression": control["kernel_compression"],
            "firmware_content_sha256": control["kernel_image_sha256"],
            "same_firmware_content": True,
            "ramdisk_bytes": len(ramdisk),
            "ramdisk_sha256": control["payload_sha256"]["ramdisk"],
            "source_ramdisk_bytes": report["header"]["ramdisk_size"],
            "ramdisk_prefix_bytes": ramdisk_prefix_bytes,
            "legacy_sha1_matches": True,
            "hardware_executed": False,
            "purpose": purpose,
            "limits": limits,
        }, indent=2) + "\n")
    return output / name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--firmware-source", type=Path,
                        help="Use a legacy no-initrd boot image and retain the source RAM transport")
    parser.add_argument("--uncompressed-firmware", action="store_true",
                        help="Keep the RAM transport and store the same firmware as a raw ARM64 Image")
    parser.add_argument("--ramdisk-prefix-bytes", type=lambda value: int(value, 0),
                        help="Keep only this many RAM transport bytes; requires legacy firmware")
    args = parser.parse_args()
    print(make_control(args.source, args.output, args.firmware_source,
                       args.uncompressed_firmware, args.ramdisk_prefix_bytes))


if __name__ == "__main__":
    main()
