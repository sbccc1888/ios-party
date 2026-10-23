#!/usr/bin/env python3
"""Build the MIX2 port, verify its constrained A53 desktop, and emit phone inputs."""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import apply_chiron
import cloud


ROOT = Path(__file__).resolve().parents[2]
PROFILE = Path(__file__).with_name("chiron-profile.json")


def prepare(work, evidence):
    cloud.prepare(work, evidence)
    p = json.loads(PROFILE.read_text())
    sha = apply_chiron.loader_overlay(work / "kernel", p)
    cloud.save(evidence / "chiron-loader-overlay.json", {"profile": p, "loader_source_sha256": sha})
    shutil.copy2(PROFILE, evidence / PROFILE.name)


def build_fetch(work, evidence):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(cloud.command, ["bash", ROOT / "ci/hackios/build-chiron-kernel.sh", work, evidence],
                        log=evidence / "kernel-build.log"),
            pool.submit(cloud.fetch_apple, work, evidence),
        ]
        failures = []
        for task in concurrent.futures.as_completed(tasks):
            try:
                task.result()
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError("; ".join(failures))


def images(work, evidence):
    cloud.images(work, evidence)
    kernel = work / "kernel"
    # The phone starts only this small image. The original big APFS root is
    # transferred afterwards through the resident loader's existing USB API.
    phone = evidence / "phone"
    phone.mkdir(exist_ok=True)
    cmdline = f"cpus=1 epoch={int(time.time())}"
    cloud.command([
        "python3", kernel / "xnuloader/mkpayload.py", "--usb",
        "-o", phone / "payload.bin",
        f"kernel={evidence / 'kernel.development.hackios'}",
        f"ramdisk={kernel / 'userland/root.apfs'}", "--cmdline", cmdline,
    ], cwd=kernel)
    shutil.copy2(kernel / "xnuloader/loader.efi", phone / "loader.efi")
    shutil.copy2(kernel / "userland/root.apfs", phone / "root.apfs")
    cloud.save(phone / "inputs.json", {
        "profile": json.loads(PROFILE.read_text()),
        "payload": cloud.digest(phone / "payload.bin"),
        "loader": cloud.digest(phone / "loader.efi"),
        "small_root": cloud.digest(phone / "root.apfs"),
        "system_root": cloud.digest(kernel / "userland/system.apfs"),
        "hardware_boot_qualified": False,
    })


def qemu(work, evidence):
    kernel = work / "kernel"
    run = kernel / "qemu/run.sh"
    run.write_text(apply_chiron.replace_once(
        run.read_text(), "-cpu cortex-a76", "-cpu cortex-a53"))
    ios = kernel / "qemu/run-ios.sh"
    text = apply_chiron.replace_once(ios.read_text(), "RD_ADDR=0xFC000000", "RD_ADDR=0x108000000")
    text = apply_chiron.replace_once(text, '--rdres "$(printf', '--physbase 100000000 --rdres "$(printf')
    ios.write_text(text)
    try:
        cloud.qemu(work, evidence)
    finally:
        result = evidence / "qemu-run.json"
        if result.exists():
            data = json.loads(result.read_text())
            data.update({
                "cpu": "cortex-a53", "physical_base": "0x100000000",
                "kernel_window_limit": "0xbc000000", "native_granule_required": 4096,
                "pan_required": False, "lse_required": False,
                "chiron_cpu_qualified": False, "hardware_observed": False,
            })
            cloud.save(result, data)


def summarize(work, evidence):
    cloud.summarize(work, evidence)
    text = (evidence / "qemu-serial.log").read_text(errors="replace")
    found = re.search(r"chiron: TCR=([0-9a-f]+) SCTLR=([0-9a-f]+) MMFR0=([0-9a-f]+) "
                      r"MMFR1=([0-9a-f]+) ISAR0=([0-9a-f]+) MPIDR=([0-9a-f]+) hwshift=(\d+) timer=(\d+)", text)
    if not found:
        raise RuntimeError("missing real kernel CPU/MMU registers")
    tcr, sctlr, mmfr0, mmfr1, isar0, mpidr = [int(v, 16) for v in found.groups()[:6]]
    if (tcr >> 14) & 3 or (tcr >> 30) & 3 != 2 or int(found[7]) != 12:
        raise RuntimeError("kernel did not use 4K hardware translation tables")
    if (mmfr1 >> 20) & 15 or (isar0 >> 20) & 15:
        raise RuntimeError("CPU model unexpectedly supplies PAN or LSE")
    if "phys window 100000000 size bc000000, claim status 0" not in text:
        raise RuntimeError("constrained high-bank memory allocation was not observed")
    cloud.save(evidence / "chiron-qualification.json", {
        "cortex_a53_no_pan_lse_desktop": True,
        "hardware_granule": 4096, "high_memory_base": "0x100000000",
        "kernel_window_bytes": 0xbc000000,
        "registers": dict(tcr=hex(tcr), sctlr=hex(sctlr), mmfr0=hex(mmfr0),
                          mmfr1=hex(mmfr1), isar0=hex(isar0), mpidr=hex(mpidr)),
        "phone_timer_intid": 19, "qemu_timer_intid_observed": int(found[8]),
        "hardware_boot_qualified": False,
        "scope": "A53 4K/no-PAN/no-LSE high-bank simulation, not physical Kryo qualification",
    })


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=("prepare", "build-fetch", "images", "qemu", "summarize"))
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--evidence", type=Path, required=True)
    args = ap.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    cloud.require_runner()
    try:
        {"prepare": prepare, "build-fetch": build_fetch, "images": images,
         "qemu": qemu, "summarize": summarize}[args.stage](args.work.resolve(), args.evidence.resolve())
    except Exception as error:
        cloud.save(args.evidence / f"failure-{args.stage}.json", {"error": str(error), "stage": args.stage})
        raise


if __name__ == "__main__":
    main()
