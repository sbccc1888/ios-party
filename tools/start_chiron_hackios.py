#!/usr/bin/env python3
"""Upload a hash-checked APFS root into an already running MIX2 HackiOS loader."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def fastboot(serial, *args):
    command = ["fastboot", "-s", serial, *args]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout)
    return result.stdout


def getvar(serial, name):
    text = fastboot(serial, "getvar", name)
    found = re.search(rf"(?:\(bootloader\)\s*)?{re.escape(name)}:\s*([^\r\n]+)", text)
    if not found:
        raise RuntimeError(f"missing loader variable {name}: {text}")
    return found[1].strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--system", type=Path, required=True)
    ap.add_argument("--serial")
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text())
    expected = manifest["phone_inputs"]["system_root"]
    if args.system.stat().st_size != expected["bytes"]:
        raise RuntimeError("system volume length differs from this candidate")
    digest = hashlib.sha256()
    with args.system.open("rb") as stream:
        while block := stream.read(4 << 20):
            digest.update(block)
    if digest.hexdigest() != expected["sha256"]:
        raise RuntimeError("system volume SHA-256 differs from this candidate")
    if args.serial:
        serial = args.serial
    else:
        devices = subprocess.check_output(["fastboot", "devices"], text=True)
        candidates = [line.split()[0] for line in devices.splitlines() if line.strip()]
        if not candidates:
            raise RuntimeError(
                "no fastboot device is visible; the HackiOS USB loader has not enumerated. "
                "Check the phone's EFI/loader screen. --serial cannot fix zero devices."
            )
        if len(candidates) > 1:
            raise RuntimeError("multiple fastboot devices are visible; specify --serial from: " + ", ".join(candidates))
        serial = candidates[0]
    if getvar(serial, "product") != "xnuloader" or getvar(serial, "board") != "chiron":
        raise RuntimeError("device is not the running chiron HackiOS loader")
    if getvar(serial, "screen") != "1080 2160 4320":
        raise RuntimeError("loader panel differs from the measured MIX2")
    fastboot(serial, "oem", "ramdisk")
    limit = int(getvar(serial, "max-download-size"), 0)
    if expected["bytes"] > limit:
        raise RuntimeError(f"UEFI resident root capacity {limit} is below the required {expected['bytes']}")
    print("Uploading the exact APFS system into allocated RAM.")
    print(fastboot(serial, "stage", str(args.system)))
    print(fastboot(serial, "oem", "run"))
    print("Kernel start requested; inspect the phone. No device boot, flash or reboot command was issued.")


if __name__ == "__main__":
    main()
