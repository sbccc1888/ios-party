#!/usr/bin/env python3
"""Replay the exact compiled MIX2 closure without SDK, kernel or userland rebuilds."""

import argparse
import json
import os
from pathlib import Path
import shutil

import chiron
import cloud


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime", type=Path, required=True)
    ap.add_argument("--system", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--evidence", type=Path, required=True)
    args = ap.parse_args()
    cloud.require_runner()
    args.evidence.mkdir(parents=True, exist_ok=True)
    payloads = json.loads((args.runtime / "payloads.json").read_text())
    pairs = [
        (args.runtime / "kernel.development.hackios", payloads["kernel"]),
        (args.runtime / "loader.efi", payloads["loader"]),
        (args.runtime / "phone/root.apfs", payloads["root"]),
        (args.system, payloads["system"]),
    ]
    for path, expected in pairs:
        if cloud.digest(path) != expected:
            raise RuntimeError(f"replay input differs from the real compiled artifact: {path.name}")
    sources = json.loads(cloud.SOURCES.read_text())
    kernel = args.work / "kernel"
    cloud.fetch_git(kernel, sources["kernel"]["url"], sources["kernel"]["commit"])
    observer = json.loads((cloud.ROOT / "ci/sources.json").read_text())["unixnu"]
    cloud.fetch_git(args.work / "unixnu-observer",
                    "https://github.com/" + observer["repository"] + ".git", observer["commit"])
    k = kernel / "xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837"
    k.parent.mkdir(parents=True)
    shutil.copy2(args.runtime / "kernel.development.hackios", k)
    shutil.copy2(args.runtime / "loader.efi", kernel / "xnuloader/loader.efi")
    (kernel / "userland").mkdir()
    shutil.copy2(args.runtime / "phone/root.apfs", kernel / "userland/root.apfs")
    os.link(args.system, kernel / "userland/system.apfs")
    shutil.copytree(args.runtime / "phone", args.evidence / "phone")
    for name in ("kernel.development.hackios", "kernel.development.hackios.unstripped",
                 "payloads.json", "sources.json", "apple-inputs.json", "kernel-undefined.txt"):
        shutil.copy2(args.runtime / name, args.evidence / name)
    cloud.save(args.evidence / "runner.json", {
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "project_sha": os.environ["GITHUB_SHA"], "binary_input_run": "39915264473",
        "scope": "exact native MIX2 kernel/loader/root replay; no binary rebuild",
    })
    try:
        chiron.qemu(args.work.resolve(), args.evidence.resolve())
        chiron.summarize(args.work.resolve(), args.evidence.resolve())
    except Exception as error:
        cloud.save(args.evidence / "failure-recheck.json", {"error": str(error)})
        raise


if __name__ == "__main__":
    main()
