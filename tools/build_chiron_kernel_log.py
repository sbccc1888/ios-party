#!/usr/bin/env python3
"""Add a kernel-file entry witness and enable the source kernel's existing text logs."""

import argparse
import hashlib
import json
import struct
import tempfile
from pathlib import Path

from build_chiron_entry_probe import sha256
from build_chiron_zero_tail_boot import PHYSICAL_BYTES, LOGICAL_BYTES, RAM_VOLUME_SHA256, require_zero_tail
from chiron_kernel_log import SOURCE_KERNEL_SHA, MachKernel, console_config, patch_kernel
from chiron_ram_fat import RamFat32
from inspect_chiron_boot import inspect
from make_chiron_firmware_control import read_inspected

SOURCE_IMAGE_SHA = "17d0c5b0e09daa37916f9c20129dcd89cd558aca0e3ed6257c54fc914214f6a5"
KERNEL = "EFI/UniXNU/Payloads/kernel.macho"
CONFIG = "EFI/UniXNU/unixnu.toml"
UNCHANGED = ("EFI/UniXNU/Payloads/launchd.macho", "EFI/UniXNU/Payloads/kernel.macho.adt",
             "EFI/UniXNU/Payloads/kernel.macho.trustcache")


def build(source, output, clang="clang", console_only=False, stages=False, vm_detail=False, post_vm=False):
    stages = stages or vm_detail or post_vm
    if console_only and stages:
        raise ValueError("console-only and startup stages are mutually exclusive")
    if output.exists():
        raise ValueError("output directory already exists")
    with tempfile.TemporaryDirectory(prefix="chiron-kernel-log-") as temporary:
        work = Path(temporary)
        data, before = read_inspected(source, work / "input")
        if before["source_sha256"] != SOURCE_IMAGE_SHA or before["header"]["ramdisk_size"] != PHYSICAL_BYTES:
            raise ValueError("use the verified three-stripe zero-tail input")
        ram_offset = before["payload_offsets"]["ramdisk"]
        original_volume = data[ram_offset:ram_offset + PHYSICAL_BYTES] + bytes(LOGICAL_BYTES - PHYSICAL_BYTES)
        if sha256(original_volume) != RAM_VOLUME_SHA256:
            raise ValueError("original logical volume differs from the validated source")
        original = RamFat32(original_volume)
        old_kernel = original.read(KERNEL)
        if sha256(old_kernel) != SOURCE_KERNEL_SHA:
            raise ValueError("original kernel identity mismatch")
        old_model = MachKernel(old_kernel)
        old_model.integrity()
        volume = original
        witness = None
        stage_report = None
        if stages:
            from chiron_kernel_stages import patch_stages
            kernel, stage_report = patch_stages(old_kernel, work, clang, vm_detail, post_vm)
            volume = volume.replace_same_size(KERNEL, kernel)
        elif not console_only:
            kernel, witness = patch_kernel(old_kernel, work, clang)
            volume = volume.replace_same_size(KERNEL, kernel)
        config, console = console_config(original.read(CONFIG))
        volume = volume.replace_same_size(CONFIG, config)
        for path in UNCHANGED:
            if volume.read(path) != original.read(path):
                raise ValueError("non-diagnostic payload changed: " + path)
        if console_only and volume.read(KERNEL) != old_kernel:
            raise ValueError("console-only mode changed the kernel")
        transported = require_zero_tail(volume.data)
        result = bytearray(data)
        result[ram_offset:ram_offset + PHYSICAL_BYTES] = transported
        h = before["header"]
        kernel_offset = before["payload_offsets"]["kernel"]
        encoded_firmware = data[kernel_offset:kernel_offset + h["kernel_size"]]
        digest = hashlib.sha1()
        for payload in (encoded_firmware, transported, b""):
            digest.update(payload)
            digest.update(struct.pack("<I", len(payload)))
        result[576:608] = digest.digest().ljust(32, b"\0")
        name = ("chiron-kernel-post-vm.img" if post_vm else
                "chiron-kernel-vm-stages.img" if vm_detail else
                "chiron-kernel-stages.img" if stages else
                "chiron-kernel-console-only.img" if console_only else "chiron-kernel-entry-log.img")
        image = work / name
        image.write_bytes(result)
        inspect(image, work / "verified")
        after = json.loads((work / "verified/inspection.json").read_text())
        if (
            not after["legacy_sha1_matches"] or len(result) != len(data)
            or after["payload_offsets"]["ramdisk"] != ram_offset
            or after["payload_sha256"]["kernel"] != before["payload_sha256"]["kernel"]
            or after["payload_sha256"]["ramdisk"] != sha256(transported)
        ):
            raise ValueError("diagnostic container layout or integrity changed")
        output.mkdir(parents=True, exist_ok=False)
        (output / name).write_bytes(result)
        (output / "logical-volume.fat").write_bytes(volume.data)
        (output / "kernel.macho").write_bytes(volume.read(KERNEL))
        (output / "kernel-log-report.json").write_text(json.dumps({
            "image": name, "sha256": sha256(result), "bytes": len(result),
            "source_image_sha256": before["source_sha256"], "console_only": console_only,
            "firmware_and_handoff_stripes_unchanged": True, "container_layout_unchanged": True,
            "physical_ramdisk_bytes": PHYSICAL_BYTES, "logical_ramdisk_bytes": LOGICAL_BYTES,
            "logical_volume_sha256": sha256(volume.data), "transport_sha256": sha256(transported),
            "zero_tail_still_all_zero": True, "fat_allocation_and_file_sizes_unchanged": True,
            "init_adt_trustcache_unchanged": True, "legacy_sha1_matches": True,
            "console": console, "witness": witness, "stages": stage_report, "hardware_executed": False,
            "limits": stage_report["limits"] if stages else [
                "The console flags request existing XNU text output; screen-console readiness is unobserved.",
                "The witness proves kernel-image diagnostic entry, not original XNU initialization or PID 1.",
                "The instrumented image has not passed the original full QEMU runtime qualification.",
                "Original text and POST HMAC are unchanged; the POST does not qualify the added witness.",
            ],
        }, indent=2) + "\n")
    return output / name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clang", default="clang")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--console-only", action="store_true",
                       help="Only request native console verbosity; keep the entire kernel file unchanged")
    modes.add_argument("--stages", action="store_true",
                       help="Instrument the pinned cold-start path through arm_vm_init entry")
    modes.add_argument("--vm-stages", action="store_true",
                       help="Also trace arm_vm_init up to the new kernel page-table switch")
    modes.add_argument("--post-vm", action="store_true",
                       help="Use writable scratch and bounded late drawing through arm_vm_init return")
    args = parser.parse_args()
    print(build(args.source, args.output, args.clang, args.console_only,
                args.stages, args.vm_stages, args.post_vm))


if __name__ == "__main__":
    main()
