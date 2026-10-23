#!/usr/bin/env python3
"""Reuse the physical-qualified boot container; replace only its read-only FAT prefix."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from inspect_chiron_boot import inspect
from chiron_ram_fat import RamFat32

TEMPLATE_SHA = "f0425c454eaab78ffe5f74084ed89adca0457fdf5c49d7fb0839db77d266006b"
PHYSICAL = 24 << 20
LOGICAL = 48 << 20


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    subprocess.run([str(v) for v in args], check=True)


def package(template, phone, entry, qualification, output):
    proof = json.loads(qualification.read_text())
    if not proof.get("cortex_a53_no_pan_lse_desktop"):
        raise ValueError("the real constrained A53 desktop has not passed")
    original = template.read_bytes()
    if sha(original) != TEMPLATE_SHA:
        raise ValueError("boot template differs from the physical-qualified source-trace image")
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="chiron-hackios-package-") as temporary:
        work = Path(temporary)
        inspect(template, work / "original")
        report = json.loads((work / "original/inspection.json").read_text())
        if report["header"]["ramdisk_size"] != PHYSICAL or not report["legacy_sha1_matches"]:
            raise ValueError("unexpected template transport")
        fat = work / "transport.fat"
        with fat.open("xb") as stream:
            stream.truncate(LOGICAL)
        run(["mformat", "-i", fat, "-F", "-v", "CHIRONESP", "::"])
        run(["mmd", "-i", fat, "::/EFI", "::/EFI/UniXNU", "::/EFI/HackiOS"])
        config = work / "unixnu.toml"
        config.write_text('''version = 1
[boot]
timeout = 3
default = "chiron-hackios"
[kits]
autoload = false
[[entry]]
id = "chiron-hackios"
title = "HK5"
type = "efi"
path = "/EFI/HackiOS/ChironHackios.efi"
[[entry]]
id = "firmware"
title = "Return to firmware"
type = "action"
action = "exit"
''')
        mapping = {
            "EFI/UniXNU/unixnu.toml": config,
            "EFI/HackiOS/ChironHackios.efi": entry,
            "EFI/HackiOS/loader.efi": phone / "loader.efi",
            "EFI/HackiOS/payload.bin": phone / "payload.bin",
        }
        for target, source in mapping.items():
            run(["mcopy", "-i", fat, source, "::/" + target])
        run(["fsck.fat", "-n", fat])
        volume = fat.read_bytes()
        if any(volume[PHYSICAL:]):
            raise ValueError("nonzero data beyond the physical 24MiB prefix")
        reader = RamFat32(volume)
        for target, source in mapping.items():
            if reader.read(target) != source.read_bytes():
                raise ValueError("FAT readback differs: " + target)
        header = bytearray(original[:report["header"]["page_size"]])
        ko = report["payload_offsets"]["kernel"]
        kernel = original[ko:ko + report["header"]["kernel_size"]]
        transported = volume[:PHYSICAL]
        check = hashlib.sha1()
        for payload in (kernel, transported, b""):
            check.update(payload)
            check.update(struct.pack("<I", len(payload)))
        header[576:608] = check.digest().ljust(32, b"\0")
        result = bytearray(original)
        result[:len(header)] = header
        ro = report["payload_offsets"]["ramdisk"]
        result[ro:ro + PHYSICAL] = transported
        image = output / "MIX2-HackiOS-8796-usb.img"
        image.write_bytes(result)
        inspect(image, output / "inspection")
        after = json.loads((output / "inspection/inspection.json").read_text())
        if not after["legacy_sha1_matches"] or after["kernel_image_sha256"] != report["kernel_image_sha256"]:
            raise ValueError("template firmware bytes changed")
        manifest = {
            "image": image.name, "bytes": len(result), "sha256": sha(result),
            "template_sha256": TEMPLATE_SHA, "firmware_bytes_unchanged": True,
            "kernel_binary_instruction_patching": False,
            "transport_readback_verified": True, "omitted_tail_zero": True,
            "physical_transport_bytes": PHYSICAL, "logical_transport_bytes": LOGICAL,
            "qualification": proof, "phone_inputs": json.loads((phone / "inputs.json").read_text()),
            "entry_sha256": sha(entry.read_bytes()),
            "phone_loader_sha256": sha((phone / "loader.efi").read_bytes()),
            "hardware_boot_qualified": False,
            "next_action": "User boots the candidate, then uploads the exact system.apfs to the resident xnuloader.",
        }
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return image


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", type=Path, required=True)
    ap.add_argument("--phone", type=Path, required=True)
    ap.add_argument("--entry", type=Path, required=True)
    ap.add_argument("--qualification", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    print(package(args.template, args.phone, args.entry, args.qualification, args.output))


if __name__ == "__main__":
    main()
