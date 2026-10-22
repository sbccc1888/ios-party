#!/usr/bin/env python3
"""Transport a verified zero-tailed 48 MiB FAT volume as 24 MiB and restore it at entry."""

import argparse
import gzip
import hashlib
import importlib.util
import json
import struct
import tempfile
import zlib
from pathlib import Path

from build_chiron_entry_probe import ROOT, compile_probe, install_probe, sha256
from inspect_chiron_boot import inspect
from make_chiron_firmware_control import read_inspected
from chiron_ram_fat import RamFat32

PHYSICAL_BYTES = 24 * 1048576
LOGICAL_BYTES = 48 * 1048576
RAM_FIRMWARE_SHA256 = "d264707aad247c2be824970bbb802a2c58dc8463d8a602b5a113abda75374963"
RAM_VOLUME_SHA256 = "8be640543dec47458edf6cc8c8c4204a6857e2d5223e442b57704d9337c04a48"


def require_measured_range(profile):
    start, end = 0x44c00000, 0x46400000
    if profile.get("device") != "chiron":
        raise ValueError("zero-tail address guards are specific to the measured chiron")
    if not any(bank["base"] <= start and end <= bank["end_exclusive"]
               for bank in profile["memblock_memory"]):
        raise ValueError("zero-fill range is outside the measured RAM banks")
    reserved = list(profile["memblock_reserved"])
    for ranges in profile["static_reserved_regions"].values():
        reserved.extend(ranges)
    if any(region["base"] < end and start < region["end_exclusive"] for region in reserved):
        raise ValueError("zero-fill range intersects a measured reserved region")


def require_zero_tail(volume):
    if len(volume) != LOGICAL_BYTES:
        raise ValueError("expected a 48 MiB logical RAM volume")
    if any(volume[PHYSICAL_BYTES:]):
        raise ValueError("omitted RAM tail contains nonzero data")
    if (
        volume[510:512] != b"\x55\xaa" or volume[11:13] != b"\x00\x02"
        or volume[16] != 2 or volume[17:19] != b"\0\0" or volume[22:24] != b"\0\0"
        or volume[71:82] != b"CHIRONESP  " or volume[82:90] != b"FAT32   "
        or struct.unpack_from("<I", volume, 32)[0] * 512 != LOGICAL_BYTES
    ):
        raise ValueError("unexpected logical FAT32 geometry")
    return volume[:PHYSICAL_BYTES]


def source_runtime_proof(directory, volume):
    spec = importlib.util.spec_from_file_location("trace_ram_payload", ROOT / "ci/build_ram_payload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = module.require_runtime(directory)
    proof = json.loads((directory / "source-trace-tests/verification.json").read_text())
    build = json.loads((directory / "source-trace-build.json").read_text())
    if (
        proof.get("build_id") != build.get("build_id")
        or proof.get("kernel_sha256") != runtime["kernel_sha256"]
        or not proof.get("source_entry_and_mmu_observed")
        or not proof.get("native_framebuffer_mapping_observed")
        or not proof.get("panic_reason_observed")
        or not proof.get("data_abort_pc_esr_far_observed")
        or build.get("kernel_binary_patching") is not False
    ):
        raise ValueError("source trace runtime/fault qualification is missing or mismatched")
    if runtime.get("user_selftest") and proof.get("user_selftest") != runtime["user_selftest"]:
        raise ValueError("source trace qualification does not match the observed user selftest")
    fat = RamFat32(volume)
    payloads = [
        ("kernel.macho", "kernel.macho"), ("kernel.macho.adt", "kernel.macho.adt"),
        ("launchd.macho", "launchd.macho"), ("trustcache.bundle", "kernel.macho.trustcache"),
    ]
    if runtime.get("user_selftest"):
        payloads.append(("root.img", "root.img"))
    for filename, target in payloads:
        if fat.read("EFI/UniXNU/Payloads/" + target) != (directory / filename).read_bytes():
            raise ValueError("source runtime FAT payload mismatch: " + filename)
    config = fat.read("EFI/UniXNU/unixnu.toml")
    if runtime.get("user_selftest") and b'initrd = "Payloads/root.img"' not in config:
        raise ValueError("selftest phone image must use the qualified multi-file RAM root")
    if b"chiron-trace-test" in config:
        raise ValueError("fault-injection arguments must never enter the phone image")
    return {"build_id": build["build_id"], "runtime": runtime, "source_trace_tests": proof}


def build(source, output, clang="clang", runtime=None):
    if output.exists():
        raise ValueError("output directory already exists")
    require_measured_range(json.loads((ROOT / "research/chiron-live-profile.json").read_text()))
    with tempfile.TemporaryDirectory(prefix="chiron-zero-tail-") as temporary:
        work = Path(temporary)
        data, report = read_inspected(source, work / "input")
        if report["kernel_compression"] != "gzip":
            raise ValueError("expected the original gzip RAM-init boot image")
        offset = report["payload_offsets"]["kernel"]
        encoded = data[offset:offset + report["header"]["kernel_size"]]
        decoder = zlib.decompressobj(31)
        firmware = decoder.decompress(encoded, 0x600001)
        if not decoder.eof or sha256(firmware) != RAM_FIRMWARE_SHA256:
            raise ValueError("expected the pinned RAM-volume-capable firmware")
        ram_offset = report["payload_offsets"]["ramdisk"]
        volume = data[ram_offset:ram_offset + report["header"]["ramdisk_size"]]
        transported = require_zero_tail(volume)
        qualified_source = source_runtime_proof(runtime, volume) if runtime else None
        if not qualified_source and sha256(volume) != RAM_VOLUME_SHA256:
            raise ValueError("logical volume differs from the verified original payload")
        reconstructed = transported + bytes(LOGICAL_BYTES - PHYSICAL_BYTES)
        if reconstructed != volume:
            raise ValueError("zero-tail reconstruction is not byte exact")
        program, halt = compile_probe(work, clang, ("CHIRON_ZERO_TAIL",))
        modified = install_probe(firmware, program)
        kernel = gzip.compress(modified, mtime=0) + decoder.unused_data
        page = report["header"]["page_size"]
        if page + (len(kernel) + page - 1) // page * page != ram_offset:
            raise ValueError("entry stub would change the matched RAM transport file offset")
        header = bytearray(data[:page])
        struct.pack_into("<I", header, 8, len(kernel))
        struct.pack_into("<I", header, 16, len(transported))
        digest = hashlib.sha1()
        for payload in (kernel, transported, b""):
            digest.update(payload)
            digest.update(struct.pack("<I", len(payload)))
        header[576:608] = digest.digest().ljust(32, b"\0")
        image = work / "chiron-ram-init-zero-tail.img"
        result = bytes(header) + kernel + bytes(-len(kernel) % page) + transported
        if len(result) != len(data) - (LOGICAL_BYTES - PHYSICAL_BYTES):
            raise ValueError("unexpected physical transport layout")
        image.write_bytes(result)
        inspect(image, work / "verified")
        verified = json.loads((work / "verified/inspection.json").read_text())
        if (
            not verified["legacy_sha1_matches"]
            or verified["payload_sha256"]["ramdisk"] != sha256(transported)
            or verified["kernel_image_sha256"] != sha256(modified)
            or verified["appended_dtbs"][0]["sha256"] != report["appended_dtbs"][0]["sha256"]
            or modified[0x8000:] != firmware[0x8000:]
        ):
            raise ValueError("zero-tail boot image integrity failed")
        output.mkdir(parents=True, exist_ok=False)
        (output / image.name).write_bytes(result)
        (output / "firmware.fd").write_bytes(modified)
        (output / "logical-volume.fat").write_bytes(volume)
        metadata = {
            "image": image.name, "sha256": sha256(result), "bytes": len(result),
            "source_image_sha256": report["source_sha256"],
            "firmware_sha256": sha256(modified), "firmware_body_unchanged": True,
            "program_bytes": len(program), "program_sha256": sha256(program),
            "failure_halt_offset": 64 + halt, "uefi_entry_offset": 0x8000,
            "physical_ramdisk_bytes": PHYSICAL_BYTES, "logical_ramdisk_bytes": LOGICAL_BYTES,
            "transport_sha256": sha256(transported), "logical_volume_sha256": sha256(volume),
            "omitted_tail_is_all_zero": True, "host_reconstruction_byte_exact": True,
            "recorded_ram_and_reserved_range_check": True,
            "legacy_sha1_matches": True, "hardware_executed": False,
            "qualified_source_runtime": qualified_source,
            "continues_to_uefi_on_success": True,
            "handoff_guards": {
                "image_base": "0x40080000", "fdt_base": "0x43200000",
                "initrd_start": "0x43400000", "physical_end": "0x44c00000",
                "logical_end": "0x46400000", "mmu_off": True, "data_cache_off": True,
                "exception_levels": [1, 2],
            },
            "failure_codes": {
                "0x10": "unsupported EL or enabled MMU/data cache",
                "0x11": "Image/FDT address differs from the observed handoff",
                "0x12": "physical initrd range or end field differs from the expected 24 MiB",
                "0x13": "logical FAT32 header geometry is not the verified 48 MiB form",
                "1..5": "FDT parser error, as in the entry probe",
            },
            "limits": [
                "The 48 MiB bootloader failure mechanism remains unproven; this is a transport workaround.",
                "Only the observed 24 MiB handoff addresses are accepted; other handoffs halt with FAIL.",
                "The zero-fill range uses the previously measured low RAM bank, not a new allocation.",
                "A successful stub handoff does not prove UEFI, XNU or iOS ran.",
            ],
        }
        (output / "zero-tail-report.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return output / image.name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--source-runtime", type=Path,
                        help="Require matching source-observer QEMU, panic, fault and FAT proofs")
    args = parser.parse_args()
    print(build(args.source, args.output, args.clang, args.source_runtime))


if __name__ == "__main__":
    main()
