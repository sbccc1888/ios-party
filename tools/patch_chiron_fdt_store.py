#!/usr/bin/env python3
"""Make an offline, pinned-firmware control that saves the FDT pointer after FD copying."""

import argparse
import gzip
import hashlib
import json
import struct
import tempfile
import zlib
from pathlib import Path

from inspect_chiron_boot import inspect
from make_chiron_firmware_control import read_inspected

SUPPORTED_FDS = {
    "7513f28ad5e8a44f750d775c2175b206a9a569e1691a239d82a5037cc27d09d1": "legacy",
    "d264707aad247c2be824970bbb802a2c58dc8463d8a602b5a113abda75374963": "ram-volume",
}
COPY_BODY = bytes.fromhex(
    "0a66b0d2 400100f9 a6010058 a50006cb 840005cb de0304cb de03068b"
    "45010058 820cc1a8 c20c81a8 a54000f1 a1ffff54 c0035fd6"
)
NOP = bytes.fromhex("1f2003d5")
STORE_AND_RETURN = bytes.fromhex("400100f9 c0035fd6")
CAVE = 0x40


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def patch_firmware(firmware, expected_sha256):
    if sha256(firmware) != expected_sha256:
        raise ValueError("firmware fingerprint mismatch")
    if (
        len(firmware) != 0x600000 or firmware[56:60] != b"ARM\x64"
        or struct.unpack_from("<Q", firmware, 16)[0] != len(firmware)
        or firmware[:8] != bytes.fromhex("01000010 ff1f0014")
    ):
        raise ValueError("unexpected chiron FD layout")
    if firmware[CAVE:CAVE + 8] != b"\xff" * 8:
        raise ValueError("entry padding is not unused erased space")
    if firmware.count(COPY_BODY) != 1:
        raise ValueError("expected exactly one pinned relocation sequence")
    start = firmware.index(COPY_BODY)
    # The outer FV header and SEC FFS header stay unchanged. This pinned SEC
    # uses the fixed 0xaa data-checksum marker, rather than a content checksum.
    fv = 0x8000
    if firmware[fv + 40:fv + 44] != b"_FVH":
        raise ValueError("missing outer firmware volume")
    fv_length = struct.unpack_from("<Q", firmware, fv + 32)[0]
    header_length = struct.unpack_from("<H", firmware, fv + 48)[0]
    if fv + fv_length != len(firmware) or header_length < 56 or header_length % 2:
        raise ValueError("invalid firmware volume dimensions")
    header = firmware[fv:fv + header_length]
    if len(header) != header_length or sum(struct.unpack(f"<{header_length // 2}H", header)) & 0xffff:
        raise ValueError("invalid firmware volume header checksum")
    offset = (fv + header_length + 7) & ~7
    while offset + 24 <= len(firmware):
        ffs = firmware[offset:offset + 24]
        size = int.from_bytes(ffs[20:23], "little")
        if size < 24 or offset + size > len(firmware) or ffs[19] & 1:
            raise ValueError("invalid or unsupported FFS layout")
        if offset + 24 <= start and start + len(COPY_BODY) <= offset + size:
            if ffs[18] != 3 or ffs[19] & 0x40 or ffs[17] != 0xaa:
                raise ValueError("relocation is not in the expected fixed-checksum SEC file")
            break
        offset = (offset + size + 7) & ~7
    else:
        raise ValueError("relocation is outside a SEC file")
    tail = start + len(COPY_BODY) - 4
    branch = 0x14000000 | (((CAVE - tail) // 4) & 0x03ffffff)
    patched = bytearray(firmware)
    patched[start + 4:start + 8] = NOP
    patched[tail:tail + 4] = struct.pack("<I", branch)
    patched[CAVE:CAVE + 8] = STORE_AND_RETURN
    return bytes(patched), {
        "copy_body_offset": hex(start),
        "deferred_store_offset": hex(CAVE),
        "return_branch_offset": hex(tail),
        "sec_ffs_offset": hex(offset),
        "sec_ffs_header_unchanged": patched[offset:offset + 24] == firmware[offset:offset + 24],
        "fv_header_unchanged": patched[fv:fv + header_length] == header,
        "fdt_store_address": "0x83300000",
        "source_firmware_sha256": expected_sha256,
        "patched_firmware_sha256": sha256(patched),
    }


def build_control(source, output):
    if output.exists():
        raise ValueError("output directory already exists")
    with tempfile.TemporaryDirectory(prefix="chiron-fdt-control-") as temporary:
        work = Path(temporary)
        data, source_report = read_inspected(source, work / "input")
        header = source_report["header"]
        if not header["ramdisk_size"]:
            raise ValueError("use a RAM-bearing input for this control")
        offset = source_report["payload_offsets"]["kernel"]
        encoded = data[offset:offset + header["kernel_size"]]
        compressed = source_report["kernel_compression"] == "gzip"
        if compressed:
            decoder = zlib.decompressobj(31)
            firmware = decoder.decompress(encoded, 0x600001)
            if not decoder.eof:
                raise ValueError("incomplete or oversized firmware")
            suffix = decoder.unused_data
        else:
            image_size = source_report["arm64_image_header"]["image_size"]
            firmware, suffix = encoded[:image_size], encoded[image_size:]
        fingerprint = sha256(firmware)
        if fingerprint not in SUPPORTED_FDS:
            raise ValueError("firmware is not one of the two verified pinned inputs")
        patched, patch_report = patch_firmware(firmware, fingerprint)
        kernel = (gzip.compress(patched, mtime=0) if compressed else patched) + suffix
        page = header["page_size"]
        old_padded = (len(encoded) + page - 1) // page * page
        new_padded = (len(kernel) + page - 1) // page * page
        if old_padded != new_padded:
            raise ValueError("patch would change the RAM transport container offset")
        ram_offset = source_report["payload_offsets"]["ramdisk"]
        ramdisk = data[ram_offset:ram_offset + header["ramdisk_size"]]
        boot_header = bytearray(data[:page])
        struct.pack_into("<I", boot_header, 8, len(kernel))
        digest = hashlib.sha1()
        for payload in (kernel, ramdisk, b""):
            digest.update(payload)
            digest.update(struct.pack("<I", len(payload)))
        boot_header[576:608] = digest.digest().ljust(32, b"\0")
        result = bytes(boot_header) + kernel + bytes(-len(kernel) % page) + data[ram_offset:]
        image = work / "chiron-fdt-after-copy.img"
        image.write_bytes(result)
        inspect(image, work / "verified")
        verified = json.loads((work / "verified/inspection.json").read_text())
        if (
            not verified["legacy_sha1_matches"] or len(result) != len(data)
            or verified["payload_offsets"]["ramdisk"] != ram_offset
            or verified["payload_sha256"]["ramdisk"] != source_report["payload_sha256"]["ramdisk"]
            or verified["appended_dtbs"][0]["sha256"] != source_report["appended_dtbs"][0]["sha256"]
            or verified["kernel_image_sha256"] != sha256(patched)
        ):
            raise ValueError("patched container integrity or layout changed unexpectedly")
        output.mkdir(parents=True, exist_ok=False)
        (output / image.name).write_bytes(result)
        (output / "source-firmware.fd").write_bytes(firmware)
        (output / "firmware.fd").write_bytes(patched)
        (output / "patch-report.json").write_text(json.dumps({
            **patch_report,
            "source_image_sha256": source_report["source_sha256"],
            "image": image.name, "sha256": sha256(result), "bytes": len(result),
            "firmware_generation": SUPPORTED_FDS[fingerprint],
            "kernel_compression": verified["kernel_compression"],
            "ramdisk_bytes": len(ramdisk), "ramdisk_sha256": sha256(ramdisk),
            "ramdisk_container_offset_unchanged": True,
            "container_length_unchanged": True, "appended_dtb_unchanged": True,
            "legacy_sha1_matches": True, "hardware_executed": False,
            "limits": [
                "The device's actual failing source FD address is still unobserved.",
                "This tests a source-overlap mechanism; it does not establish an iOS or XNU boot.",
            ],
        }, indent=2) + "\n")
    return output / image.name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_control(args.source, args.output))


if __name__ == "__main__":
    main()
