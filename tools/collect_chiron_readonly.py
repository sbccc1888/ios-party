#!/usr/bin/env python3
"""Collect device metadata over an explicitly selected ADB transport."""

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path


READS = {
    "identity.txt": (
        "getprop ro.product.device; getprop ro.product.model; "
        "getprop ro.build.version.release; getprop ro.build.version.security_patch; "
        "getprop ro.build.display.id; getprop ro.boot.flash.locked; "
        "getprop ro.boot.verifiedbootstate; getprop ro.boot.vbmeta.device_state; "
        "uname -a; getconf PAGE_SIZE"
    ),
    "cpuinfo.txt": "cat /proc/cpuinfo",
    "meminfo.txt": "cat /proc/meminfo",
    "iomem.txt": "cat /proc/iomem",
    "cmdline.txt": "cat /proc/cmdline",
    "input-devices.txt": "cat /proc/bus/input/devices",
    "dmesg.txt": "dmesg",
    "runtime-fdt.dtb": "cat /sys/firmware/fdt",
    "runtime-tree.tar": "tar -chf - -C /sys/firmware/devicetree/base .",
    "kernel-config.gz": "cat /proc/config.gz",
    "soc.txt": (
        "for field in soc_id revision machine family platform_version; do "
        'printf "%s=" "$field"; cat "/sys/devices/soc0/$field"; done'
    ),
    "cpu-registers.txt": (
        "for cpu in 0 4; do "
        "for reg in midr_el1 revidr_el1 id_aa64mmfr0_el1 id_aa64mmfr1_el1 id_aa64isar0_el1; do "
        'p="/sys/devices/system/cpu/cpu$cpu/regs/identification/$reg"; '
        'printf "%s\\n" "$p"; cat "$p"; done; done'
    ),
    "i2c-bindings.txt": (
        "for node in /sys/bus/i2c/devices/*; do "
        '[ -f "$node/name" ] || continue; printf "%s\\n" "$node"; '
        'cat "$node/name"; readlink -f "$node/driver"; done'
    ),
    "framebuffer.txt": (
        "for field in name virtual_size stride bits_per_pixel modes mode; do "
        'printf "%s=" "$field"; cat "/sys/class/graphics/fb0/$field"; done; '
        "ls -l /sys/class/graphics/fb0; "
        "ls -l /sys/kernel/debug/mdp"
    ),
    "block-devices.txt": (
        "cat /proc/partitions; ls -l /dev/block/by-name; "
        "for disk in /sys/class/block/sd?; do "
        '[ -d "$disk" ] || continue; printf "%s\\n" "$disk"; '
        'cat "$disk/size" "$disk/queue/logical_block_size" '
        '"$disk/queue/physical_block_size" "$disk/device/model"; done'
    ),
    "partition-layout.txt": (
        "for part in /sys/class/block/sd*; do "
        '[ -f "$part/partition" ] || continue; printf "%s\\n" "$part"; '
        'cat "$part/start" "$part/size" "$part/uevent"; done'
    ),
    "memblock-memory.txt": "cat /sys/kernel/debug/memblock/memory",
    "memblock-reserved.txt": "cat /sys/kernel/debug/memblock/reserved",
    "panel-info.txt": "cat /sys/class/graphics/fb0/msm_fb_panel_info",
    "touch-identification.txt": (
        "for field in name productinfo buildid; do "
        'printf "%s=" "$field"; cat "/sys/class/input/input1/$field"; done; '
        "readlink -f /sys/bus/i2c/devices/5-0020/driver; "
        "readlink /sys/bus/i2c/devices/5-0038/driver"
    ),
    "sda-lun.txt": (
        "readlink -f /sys/class/block/sda/device; "
        "cat /sys/class/block/sda/queue/logical_block_size "
        "/sys/class/block/sda/queue/physical_block_size "
        "/sys/class/block/sda/size"
    ),
    "sda-primary-gpt.bin": "dd if=/dev/block/sda bs=4096 count=6 2>/dev/null",
}


def run(adb, serial, arguments):
    return subprocess.run(
        [adb, "-s", serial, *arguments],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="/usr/local/bin/adb")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--only", nargs="+", choices=sorted(READS))
    args = parser.parse_args()
    identity = run(args.adb, args.serial, ["shell", "getprop", "ro.product.device"])
    if identity.returncode or identity.stdout.strip() != b"chiron":
        parser.exit(1, "refusing collection: selected device is not confirmed chiron\n")
    privilege = run(args.adb, args.serial, ["shell", "su", "-c", "id"])
    if privilege.returncode or not privilege.stdout.startswith(b"uid=0("):
        parser.exit(1, "root access unavailable\n")
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "device": "chiron",
        "mode": "read-only device commands",
        "limits": ["exec-out return codes describe ADB transport; inspect and validate each payload for remote read errors."],
        "files": {},
    }
    for name, command in READS.items():
        if args.only and name not in args.only:
            continue
        try:
            result = run(args.adb, args.serial, ["exec-out", "su -c " + shlex.quote(command)])
        except subprocess.TimeoutExpired:
            manifest["files"][name] = {"command": command, "timeout": True}
            print(f"{name}: timed out", flush=True)
            continue
        (args.output / name).write_bytes(result.stdout)
        if result.stderr:
            (args.output / f"{name}.stderr").write_bytes(result.stderr)
        manifest["files"][name] = {
            "command": command,
            "returncode": result.returncode,
            "bytes": len(result.stdout),
            "sha256": hashlib.sha256(result.stdout).hexdigest(),
            "stderr": result.stderr.decode("utf-8", "replace"),
        }
        print(f"{name}: rc={result.returncode} bytes={len(result.stdout)}", flush=True)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
