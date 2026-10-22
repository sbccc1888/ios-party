#!/usr/bin/env python3
"""Run the fixed HackiOS 8796 + 20F66 reference closure on a Linux Actions runner."""

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import zlib


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "research/hackios-reuse/sources.lock.json"
DEPS = ROOT / "ci/hackios/dependencies.lock.json"


def require_runner():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        raise RuntimeError("Full builds, Apple downloads and QEMU are restricted to Linux GitHub Actions")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def command(args, *, cwd=None, env=None, log=None, allow_failure=False):
    args = [str(arg) for arg in args]
    print("RUN", " ".join(args), flush=True)
    if log:
        with log.open("wb") as output:
            result = subprocess.run(args, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            print(log.read_text(errors="replace")[-12000:], flush=True)
    else:
        result = subprocess.run(args, cwd=cwd, env=env)
    if result.returncode and not allow_failure:
        raise RuntimeError(f"exit {result.returncode}: {' '.join(args)}")
    return result.returncode


def output(args, *, cwd=None):
    return subprocess.check_output([str(arg) for arg in args], cwd=cwd, text=True).strip()


def digest(path):
    sha = hashlib.sha256()
    crc = 0
    with path.open("rb") as source:
        while data := source.read(4 << 20):
            sha.update(data)
            crc = zlib.crc32(data, crc)
    return {"bytes": path.stat().st_size, "sha256": sha.hexdigest(), "crc32": f"{crc:08x}"}


def fetch_git(path, url, commit, sparse=None):
    if path.exists():
        raise RuntimeError(f"refusing to overwrite {path}")
    path.mkdir(parents=True)
    command(["git", "init", "-q", path])
    command(["git", "-C", path, "remote", "add", "origin", url])
    args = ["git", "-C", path, "fetch", "--depth=1"]
    if sparse:
        args.append("--filter=blob:none")
    command([*args, "origin", commit])
    if sparse:
        command(["git", "-C", path, "sparse-checkout", "set", sparse])
    command(["git", "-C", path, "-c", "advice.detachedHead=false", "checkout", "--detach", "FETCH_HEAD"])
    actual = output(["git", "-C", path, "rev-parse", "HEAD"])
    if actual != commit:
        raise RuntimeError(f"{path.name} commit mismatch: {actual}")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(work, evidence):
    sources = json.loads(SOURCES.read_text())
    deps = json.loads(DEPS.read_text())
    for name in ("kernel", "userland", "sweet"):
        fetch_git(work / name, sources[name]["url"], sources[name]["commit"])
    # Reproduce the upstream combined workspace, keeping source identities separately.
    userland, kernel = work / "userland", work / "kernel"
    paths = subprocess.check_output(
        ["git", "-C", str(userland), "ls-files", "-z"]
    ).decode().split("\0")
    for relative in filter(None, paths):
        if relative in ("README.md", "LICENSE", ".gitignore"):
            continue
        src, dst = userland / relative, kernel / relative
        if dst.exists() or dst.is_symlink():
            raise RuntimeError(f"unexpected repository path collision: {relative}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    sdk = deps["sdk"]
    fetch_git(work / "sdks", sdk["repository"], sdk["commit"], sdk["directory"])
    theos = work / "theos"
    (theos / "sdks").mkdir(parents=True)
    (theos / "toolchain").mkdir()
    (theos / "sdks" / sdk["directory"]).symlink_to(work / "sdks" / sdk["directory"])
    archive = work / "toolchain.tar.xz"
    command(["curl", "-fL", "--retry", "4", "--retry-all-errors", "-o", archive, deps["toolchain"]["url"]])
    if archive.stat().st_size != deps["toolchain"]["asset_bytes"]:
        raise RuntimeError("toolchain asset size changed")
    toolchain = digest(archive)
    command(["tar", "-xJf", archive, "-C", theos / "toolchain"])
    if not (theos / "toolchain/linux/iphone/bin/ld").exists():
        raise RuntimeError("toolchain did not contain the expected cctools linker")
    save(evidence / "sources.json", sources)
    save(evidence / "dependencies.json", {"locked": deps, "toolchain_download": toolchain})
    save(evidence / "runner.json", {
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "project_sha": os.environ["GITHUB_SHA"], "system": output(["uname", "-a"]),
        "clang": output(["clang", "--version"]), "qemu": output(["qemu-system-aarch64", "--version"]),
        "seven_zip": output(["7z", "i"]).splitlines()[:6],
        "scope": "C1 A76 reference; not chiron CPU or hardware qualification",
    })


def fetch_apple(work, evidence):
    kernel = work / "kernel"
    ios = kernel / "ios165"
    env = dict(os.environ, KEEP="1")
    command(["python3", ios / "fetch-from-ipsw.py", "BuildManifest.plist", ios / "BuildManifest.plist"],
            cwd=kernel, log=evidence / "fetch-buildmanifest.log")
    manifest = plistlib.loads((ios / "BuildManifest.plist").read_bytes())
    if manifest["ProductVersion"] != "16.5" or manifest["ProductBuildVersion"] != "20F66":
        raise RuntimeError("IPSW BuildManifest is not iOS 16.5 / 20F66")
    if not {"iPhone10,3", "iPhone10,6"} <= set(manifest["SupportedProductTypes"]):
        raise RuntimeError("IPSW BuildManifest does not support the locked iPhone X identities")
    command(["bash", ios / "fetch.sh"], cwd=kernel, env=env, log=evidence / "fetch-apple.log")
    fetcher = load_module("hackios_ipsw_fetch", ios / "fetch-from-ipsw.py")
    remotezip = load_module("sweet_remotezip", work / "sweet/tools/remotezip.py")
    # The locked upstream fetcher already records the archive size.
    remotezip.size_of = lambda url: fetcher.SIZE
    entries = {entry[0]: entry for entry in remotezip.central_dir(fetcher.URL)}
    members = {
        "096-04088-077.dmg": ios / "rootfs.dmg",
        "096-04666-078.dmg": ios / "cryptex-os.dmg",
        "096-03536-083.dmg": ios / "cryptex-app.dmg",
        "BuildManifest.plist": ios / "BuildManifest.plist",
    }
    verified = {}
    manifest_paths = {
        value.get("Info", {}).get("Path")
        for identity in manifest["BuildIdentities"]
        for value in identity["Manifest"].values()
    }
    for member, path in members.items():
        record = digest(path)
        entry = entries[member]
        if record["bytes"] != entry[3] or record["crc32"] != f"{entry[5]:08x}":
            raise RuntimeError(f"IPSW member size/CRC mismatch: {member}")
        if member.endswith(".dmg") and member not in manifest_paths:
            raise RuntimeError(f"downloaded disk image is not named in BuildManifest: {member}")
        verified[member] = record
    cache = ios / "20F66__iPhone10,3_6"
    cache_files = sorted(cache.glob("dyld_shared_cache_arm64*"))
    if len(cache_files) != 43:
        raise RuntimeError(f"expected 43 runtime cache files, found {len(cache_files)}")
    binaries = {
        "dyld": ios / "rootfs/usr/lib/dyld",
        "launchd": ios / "rootfs/sbin/launchd",
    }
    binary_records = {}
    for name, path in binaries.items():
        with path.open("rb") as stream:
            header = stream.read(32)
        magic, cpu, subtype = struct.unpack_from("<III", header)
        if (magic, cpu, subtype & 0xFFFFFF) != (0xFEEDFACF, 0x100000C, 0):
            raise RuntimeError(f"{name} is not the expected plain arm64 Mach-O")
        binary_records[name] = {**digest(path), "cputype": cpu, "cpusubtype": subtype}
    with (cache / "dyld_shared_cache_arm64").open("rb") as stream:
        magic = stream.read(16)
    if magic.rstrip(b"\0 ") != b"dyld_v1   arm64":
        raise RuntimeError(f"unexpected dyld cache magic: {magic!r}")
    save(evidence / "apple-inputs.json", {
        "product_version": manifest["ProductVersion"],
        "build_version": manifest["ProductBuildVersion"],
        "supported_products": manifest["SupportedProductTypes"],
        "ipsw_url": fetcher.URL, "ipsw_bytes": fetcher.SIZE, "members": verified,
        "binaries": binary_records, "cache_magic": magic.decode("ascii"),
        "cache_files": {p.name: digest(p) for p in cache_files},
        "scope": "identity, content hashes and ZIP integrity; runtime mapping is checked by the guest",
    })


def build_and_fetch(work, evidence):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        jobs = {
            "source kernel": executor.submit(
                command, ["bash", ROOT / "ci/hackios/build-kernel.sh", work, evidence],
                log=evidence / "kernel-build.log"),
            "20F66 files": executor.submit(fetch_apple, work, evidence),
        }
        failures = []
        for future in concurrent.futures.as_completed(jobs.values()):
            name = next(name for name, value in jobs.items() if value is future)
            try:
                future.result()
                print(f"COMPLETE {name}", flush=True)
            except Exception as error:
                failures.append(f"{name}: {error}")
                print(f"FAILED {name}: {error}", flush=True)
        if failures:
            raise RuntimeError("; ".join(failures))


def images(work, evidence):
    kernel = work / "kernel"
    env = dict(os.environ, THEOS=str(work / "theos"), REST="1", SHARE="1", SYSTEM="1")
    command(["bash", "userland/build.sh"], cwd=kernel, env=env, log=evidence / "userland-build.log")
    command(["bash", "tools/apfs/build-tool.sh", work / "apfs-tool"], cwd=kernel,
            log=evidence / "apfs-tool-build.log")
    readback = {}
    for name, packed in (("dyld", "/usr/lib/dyld"), ("launchd", "/sbin/launchd.ios")):
        data = subprocess.check_output([
            str(work / "apfs-tool"), "cat", str(kernel / "userland/system.apfs"), packed,
        ], stderr=subprocess.DEVNULL)
        sha = hashlib.sha256(data).hexdigest()
        original = digest(kernel / "ios165/rootfs" / packed.lstrip("/").removesuffix(".ios"))
        if sha != original["sha256"]:
            raise RuntimeError(f"APFS readback differs from extracted Apple {name}")
        readback[packed] = {"bytes": len(data), "sha256": sha}
    required = ["stage0", "init", "hello_dyn", "surf_test", "libredwood.dylib", "libredwood-sb.dylib"]
    save(evidence / "payloads.json", {
        "kernel": digest(evidence / "kernel.development.hackios"),
        "loader": digest(kernel / "xnuloader/loader.efi"),
        "root": digest(kernel / "userland/root.apfs"),
        "system": digest(kernel / "userland/system.apfs"),
        "rebuilt_userland": {name: digest(kernel / "userland/obj" / name) for name in required},
        "boot_rc": (kernel / "userland/root/etc/rc").read_text(),
        "jobs_rc": (kernel / "userland/root/etc/rc.jobs").read_text(),
        "inject": (kernel / "userland/root/etc/inject.conf").read_text(),
        "apple_binaries_readback_from_apfs": readback,
        "recipe": {"REST": "1", "GL": None, "SHARE": "1", "PREBUILT": None},
    })


def qemu(work, evidence):
    kernel = work / "kernel"
    frames = evidence / "screens"
    frames.mkdir(exist_ok=True)
    env = dict(
        os.environ, QEMU_EFI="/usr/share/AAVMF/AAVMF_CODE.fd",
        QEMU_VARS="/usr/share/AAVMF/AAVMF_VARS.fd", QEMU_RAM_GB="7",
        QEMU_INSTANCE="0", QEMU_SMP="1", AUDIO="none",
        CMDLINE="cpus=1", PYTHONDONTWRITEBYTECODE="1",
    )
    actions = []
    with (evidence / "qemu-serial.log").open("wb") as serial:
        process = subprocess.Popen(
            ["bash", "qemu/run-ios.sh", "720"], cwd=kernel, env=env,
            stdout=serial, stderr=subprocess.STDOUT, start_new_session=True,
        )
        started = time.monotonic()
        schedule = [
            (30, "screen"), (90, "screen"), (180, "screen"),
            (240, "unlock"), (245, "screen"), (300, "screen"),
            (360, "unlock"), (370, "screen"), (480, "home"),
            (485, "screen"), (600, "screen"), (660, "stacks"), (680, "screen"),
        ]
        try:
            for delay, kind in schedule:
                while process.poll() is None and time.monotonic() - started < delay:
                    time.sleep(1)
                if process.poll() is not None:
                    break
                if kind == "screen":
                    args = ["bash", "qemu/screendump.sh", frames / f"t{delay:04d}.png"]
                elif kind == "unlock":
                    args = ["python3", "qemu/touch.py", "swipe", "0.5", "0.98", "0.5", "0.3", "300"]
                elif kind == "home":
                    args = ["python3", "qemu/touch.py", "key", "home"]
                else:
                    args = ["python3", "qemu/touch.py", "stacks", "top"]
                rc = command(args, cwd=kernel, env=env, allow_failure=True,
                             log=evidence / f"qmp-{delay}-{kind}.log")
                actions.append({"host_seconds": delay, "action": kind, "returncode": rc})
            process.wait(timeout=max(1, 810 - (time.monotonic() - started)))
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            save(evidence / "qemu-run.json", {
                "cpu": "cortex-a76", "smp": 1, "guest_ram_gb": 7,
                "guest_budget_seconds": 720, "host_seconds": time.monotonic() - started,
                "returncode": process.returncode, "actions": actions,
                "hardware_observed": False, "chiron_cpu_qualified": False,
            })
    for name in ("SpringBoard", "backboardd", "launchd"):
        command(["bash", "qemu/daemonlog.sh", evidence / "qemu-serial.log", name, "250"],
                cwd=kernel, log=evidence / f"daemon-{name}.log", allow_failure=True)


def summarize(work, evidence):
    from PIL import Image, ImageChops, ImageStat

    log_path = evidence / "qemu-serial.log"
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    patterns = {
        "dynamic_main": r"Hello from a dynamically linked program!",
        "dynamic_exit": r"hello_dyn exited with status 7",
        "dynamic_check": r"\[\s*ok\s*\] run a program through dyld and the shared cache",
        "init_checks": r"=== \d+ checks, 0 failed ===",
        "surface_checks": r"surf_test: \d+ checks, 0 failed",
        "apple_pid1": r"(?m)^\s*1\s+.*(?:/sbin/launchd\.ios|launchd(?:\.ios)?\s*$)|launchd(?:\.ios)?\[1\]",
        "springboard": r"(?m)^\s*\d+\s+.*(?:/Applications/SpringBoard\.app/SpringBoard|SpringBoard\s*$)",
        "backboardd": r"(?m)^\s*\d+\s+.*(?:/usr/libexec/backboardd|backboardd\s*$)",
        "display_swaps": r"MiniIOMobileFramebuffer: swap \d+.*surfaces",
    }
    matches = {name: re.findall(pattern, text)[:12] for name, pattern in patterns.items()}
    screenshots = []
    previous = None
    for path in sorted((evidence / "screens").glob("*.png")):
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        stat = ImageStat.Stat(image)
        record = {"path": str(path.relative_to(evidence)), **digest(path),
                  "size": list(image.size), "channel_stddev": stat.stddev}
        record["nonuniform"] = max(stat.stddev) > 1
        if previous is not None and previous.size == image.size:
            record["mean_pixel_difference"] = ImageStat.Stat(ImageChops.difference(image, previous)).mean
        previous = image
        screenshots.append(record)
    runtime = all(matches.values()) and any(s["nonuniform"] for s in screenshots)
    save(evidence / "verification.json", {
        "schema": "hackios-8796-20F66-cloud-v1",
        "runtime_closure_observed": bool(runtime), "milestone_matches": matches,
        "screenshots": screenshots, "visual_review_required": True,
        "desktop_boot_qualified": False, "hardware_observed": False, "chiron_cpu_qualified": False,
        "kernel_built_from_source": (evidence / "kernel.development.hackios").exists(),
        "apple_target_identity_verified": (evidence / "apple-inputs.json").exists(),
        "scope": "C1 reference A76 QEMU only; screenshot pixels alone do not prove a desktop",
    })
    if not runtime:
        raise RuntimeError("C1 runtime closure has missing milestones; inspect verification.json and raw evidence")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "build-fetch", "images", "qemu", "summarize"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.work = args.work.resolve()
    args.evidence = args.evidence.resolve()
    args.evidence.mkdir(parents=True, exist_ok=True)
    try:
        require_runner()
        {"prepare": prepare, "build-fetch": build_and_fetch, "images": images,
         "qemu": qemu, "summarize": summarize}[args.stage](args.work, args.evidence)
    except Exception as error:
        save(args.evidence / f"failure-{args.stage}.json", {"stage": args.stage, "error": str(error)})
        raise


if __name__ == "__main__":
    main()
