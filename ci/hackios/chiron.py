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
import threading
import time

import apply_chiron
import cloud
import observe_chiron


ROOT = Path(__file__).resolve().parents[2]
PROFILE = Path(__file__).with_name("chiron-profile.json")


def prepare(work, evidence):
    cloud.prepare(work, evidence)
    p = json.loads(PROFILE.read_text())
    sha = apply_chiron.loader_overlay(work / "kernel", p)
    cloud.save(evidence / "chiron-loader-overlay.json", {"profile": p, "loader_source_sha256": sha})
    shutil.copy2(PROFILE, evidence / PROFILE.name)
    pinned = json.loads((ROOT / "ci/sources.json").read_text())["unixnu"]
    cloud.fetch_git(work / "unixnu-observer", "https://github.com/" + pinned["repository"] + ".git", pinned["commit"])


def reuse_kernel(work, evidence):
    source = Path(os.environ["HACKIOS_CHIRON_KERNEL_DIR"])
    lock = json.loads(Path(__file__).with_name("chiron-kernel.lock.json").read_text())
    if json.loads((source / "runner.json").read_text())["run_id"] != lock["run_id"]:
        raise RuntimeError("wrong native source-kernel build")
    k = source / "kernel.development.hackios"
    u = source / "kernel.development.hackios.unstripped"
    if cloud.digest(k)["sha256"] != lock["kernel_sha256"] or cloud.digest(u)["sha256"] != lock["unstripped_sha256"]:
        raise RuntimeError("native source-kernel hash mismatch")
    target = work / "kernel/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(k, target)
    shutil.copy2(u, target.with_name(target.name + ".unstripped"))
    cloud.save(evidence / "chiron-kernel-provenance.json", {
        "source_run": lock["run_id"], "sha256": lock["kernel_sha256"],
        "upstream_prebuilt_used": False, "rebuilt_this_run": False,
    })


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
    exported = ROOT / "dist/chiron-system/system.apfs"
    exported.parent.mkdir(parents=True, exist_ok=True)
    os.link(kernel / "userland/system.apfs", exported)


def qemu(work, evidence):
    kernel = work / "kernel"
    run = kernel / "qemu/run.sh"
    run.write_text(apply_chiron.replace_once(
        run.read_text(), "-cpu cortex-a76", "-cpu cortex-a53 -gdb tcp:127.0.0.1:1234"))
    ios = kernel / "qemu/run-ios.sh"
    text = apply_chiron.replace_once(ios.read_text(), "RD_ADDR=0xFC000000", "RD_ADDR=0x108000000")
    seconds = json.loads(PROFILE.read_text())["qemu"]["guest_seconds"]
    text = apply_chiron.replace_once(text, "SECS=${1:-60}; shift", f"SECS={seconds}; shift")
    text = apply_chiron.replace_once(text, '--rdres "$(printf', '--physbase 100000000 --rdres "$(printf')
    ios.write_text(text)
    stop = threading.Event()
    def observe():
        if not stop.wait(270):
            touch = cloud.load_module("chiron_qmp", kernel / "qemu/touch.py")
            qmp = touch.Qmp()
            qmp.command("stop")
            try:
                observe_chiron.capture(work / "unixnu-observer", evidence / "actual-cpu-registers.json")
            finally:
                if not qmp.command("query-status")["running"]:
                    qmp.command("cont")
                qmp.file.close()
                qmp.sock.close()
    observer = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = observer.submit(observe)
    try:
        cloud.qemu(work, evidence)
    finally:
        stop.set()
        observer.shutdown(wait=True)
        result = evidence / "qemu-run.json"
        if result.exists():
            data = json.loads(result.read_text())
            data.update({
                "cpu": "cortex-a53", "physical_base": "0x100000000",
                "guest_budget_seconds": seconds,
                "kernel_window_limit": "0xbc000000", "native_granule_required": 4096,
                "pan_required": False, "lse_required": False,
                "chiron_cpu_qualified": False, "hardware_observed": False,
            })
            cloud.save(result, data)
    future.result()


def summarize(work, evidence):
    cloud.summarize(work, evidence)
    text = (evidence / "qemu-serial.log").read_text(errors="replace")
    identity = re.search(r"CurrentEL=1 MIDR=([0-9a-f]+) MMFR0=([0-9a-f]+)", text)
    if not identity or (int(identity[1], 16) >> 4) & 0xfff != 0xd03:
        raise RuntimeError("missing actual A53 loader CPU identity")
    raw = json.loads((evidence / "actual-cpu-registers.json").read_text())["system_registers"]
    tcr, sctlr, mmfr0, mmfr1, isar0 = [
        int(raw[name], 16) for name in ("TCR_EL1", "SCTLR_EL1", "ID_AA64MMFR0_EL1", "ID_AA64MMFR1_EL1", "ID_AA64ISAR0_EL1")]
    mpidr = int(raw.get("MPIDR_EL1", "0"), 16)
    if (tcr >> 14) & 3 or (tcr >> 30) & 3 != 2:
        raise RuntimeError("kernel did not use 4K hardware translation tables")
    if (mmfr1 >> 20) & 15 or (isar0 >> 20) & 15:
        raise RuntimeError("CPU model unexpectedly supplies PAN or LSE")
    if "phys window 100000000 size bc000000, claim status 0" not in text:
        raise RuntimeError("constrained high-bank memory allocation was not observed")
    cloud.save(evidence / "chiron-qualification.json", {
        "cortex_a53_no_pan_lse_desktop": True,
        "hardware_granule": 4096, "high_memory_base": "0x100000000",
        "kernel_window_bytes": 0xbc000000,
        "registers": dict(midr=hex(int(identity[1], 16)), tcr=hex(tcr), sctlr=hex(sctlr), mmfr0=hex(mmfr0),
                          mmfr1=hex(mmfr1), isar0=hex(isar0), mpidr=hex(mpidr)),
        "phone_timer_intid": 19, "qemu_timer_intid": 27,
        "hardware_boot_qualified": False,
        "scope": "A53 4K/no-PAN/no-LSE high-bank simulation, not physical Kryo qualification",
    })


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=("prepare", "build-fetch", "reuse-kernel", "images", "qemu", "summarize"))
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--evidence", type=Path, required=True)
    args = ap.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    cloud.require_runner()
    try:
        {"prepare": prepare, "build-fetch": build_fetch, "reuse-kernel": reuse_kernel, "images": images,
         "qemu": qemu, "summarize": summarize}[args.stage](args.work.resolve(), args.evidence.resolve())
    except Exception as error:
        cloud.save(args.evidence / f"failure-{args.stage}.json", {"error": str(error), "stage": args.stage})
        raise


if __name__ == "__main__":
    main()
