#!/usr/bin/env python3
"""Compile the phone-only EFI USB adapter while retaining the qualified kernel."""

import argparse
import json
from pathlib import Path
import shutil

import apply_chiron
import apply_chiron_usb
import cloud


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--phone", type=Path, required=True)
    args = ap.parse_args()
    cloud.require_runner()
    sources = json.loads(cloud.SOURCES.read_text())
    cloud.fetch_git(args.work, sources["kernel"]["url"], sources["kernel"]["commit"])
    patch = cloud.ROOT / "ci/hackios/patches/0001-wait-for-ramfb-dma.patch"
    cloud.command(["git", "-C", args.work, "apply", patch])
    profile = json.loads(Path(__file__).with_name("chiron-profile.json").read_text())
    apply_chiron.loader_overlay(args.work, profile)
    apply_chiron_usb.apply(args.work)
    cloud.command(["bash", "xnuloader/build.sh"], cwd=args.work)
    shutil.copy2(args.work / "xnuloader/loader.efi", args.phone / "loader.efi")
    inputs = json.loads((args.phone / "inputs.json").read_text())
    inputs["loader"] = cloud.digest(args.phone / "loader.efi")
    inputs["usb_backend"] = "native Qualcomm EFI_USB_DEVICE_PROTOCOL; upstream polled USB"
    inputs["usb_transport_hardware_qualified"] = False
    inputs["phone_loader_source_sha256"] = cloud.digest(args.work / "xnuloader/loader.c")["sha256"]
    cloud.save(args.phone / "inputs.json", inputs)


if __name__ == "__main__":
    main()
