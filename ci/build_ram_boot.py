#!/usr/bin/env python3
"""Combine verified runtime and RAM-volume-capable firmware without disk flashing."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ci = Path(__file__).resolve().parent
    report = json.loads((args.firmware / "firmware-report.json").read_text())
    integration = json.loads((args.firmware / "integration-report.json").read_text())
    if not integration.get("ram_payload_hob") or not integration.get("ram_payload_read_only_block_io"):
        raise ValueError("firmware has no reserved read-only RAM volume support")
    fd = args.firmware / "chiron-unixnu.fd"
    dtb = args.firmware / "container-inspection/dtb-00.dtb"
    for path, key in ((fd, "firmware_sha256"), (dtb, "dtb_sha256")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != report[key]:
            raise ValueError("firmware artifact integrity mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="chiron-ram-boot-") as temporary:
        work = Path(temporary)
        shutil.copyfile(fd, work / fd.name)
        subprocess.run([sys.executable, ci / "build_ram_payload.py", "--runtime", args.runtime,
                        "--output", work / "transport.fat"], check=True)
        subprocess.run([sys.executable, ci / "pack_firmware.py", "--dtb", dtb,
                        "--output", work, "--ramdisk", work / "transport.fat"], check=True)
        for name in ("chiron-ram-init-experimental.img", "firmware-report.json"):
            shutil.copyfile(work / name, args.output / name)
        shutil.copyfile(work / "transport.json", args.output / "ram-transport-report.json")
    shutil.copyfile(args.runtime / "runtime-test.json", args.output / "qemu-runtime-test.json")
    subprocess.run([sys.executable, ci.parent / "tools/inspect_chiron_boot.py",
                    args.output / "chiron-ram-init-experimental.img",
                    "--output", args.output / "container-inspection"], check=True)


if __name__ == "__main__":
    main()
