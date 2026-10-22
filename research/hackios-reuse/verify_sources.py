#!/usr/bin/env python3
"""Verify pinned reuse sources and run upstream APFS host checks, without IPSWs or QEMU."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
LOCK = HERE / "sources.lock.json"


def run(args, *, cwd=None, env=None, data=None, required=True):
    if env is None:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [str(arg) for arg in args], cwd=cwd, env=env, input=data,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if required and result.returncode:
        raise RuntimeError(
            f"{args[0]} failed ({result.returncode}): "
            + result.stderr.decode(errors="replace")[-6000:]
        )
    return result


def git(path, *args, **kwargs):
    return run(["git", "-C", path, *args], **kwargs)


def verify_repository(cache, name, spec, fetch):
    path = cache / name
    if not path.exists():
        if not fetch:
            raise RuntimeError(f"missing {path}; use --fetch for source-only downloads")
        path.mkdir(parents=True)
        git(path, "init", "-q")
        git(path, "remote", "add", "origin", spec["url"])
        ref = f"refs/tags/{spec['tag']}" if "tag" in spec else spec["commit"]
        git(path, "fetch", "--depth=1", "origin", ref)
        git(path, "-c", "advice.detachedHead=false", "checkout", "--detach", "FETCH_HEAD")
    head = git(path, "rev-parse", "HEAD").stdout.decode().strip()
    if head != spec["commit"]:
        raise RuntimeError(f"{name}: HEAD {head} != locked {spec['commit']}")
    if git(path, "status", "--porcelain", "--untracked-files=all").stdout:
        raise RuntimeError(f"{name}: dirty source checkout; use a separate clean cache")
    return path


def patch_check(base, patch, commit, scratch):
    # A private index keeps even the reference source checkout unchanged.
    env = dict(os.environ, GIT_INDEX_FILE=str(scratch / (base.name + ".index")))
    git(base, "read-tree", commit, env=env)
    result = git(base, "apply", "--cached", "--check", "--whitespace=nowarn",
                 patch, env=env, required=False)
    stderr = result.stderr.decode(errors="replace")
    return {
        "returncode": result.returncode,
        "applies": result.returncode == 0,
        "rejected_paths": sorted(set(re.findall(r"^error: (.+): patch does not apply$", stderr, re.M))),
        "stderr": stderr,
    }


def source_inventory(repos, specs):
    paths = {
        "kernel": [
            "docs/PORTING.md", "xnu/README.md", "xnu/redwood.patch", "xnu/apply.sh",
            "xnu-build.sh", "xnuloader/loader.c", "xnuloader/efi.h",
            "xnuloader/mkpayload.py", "common/new-device.sh", "common/build.sh",
            "common/run.sh", "xl", "qemu/run.sh", "qemu/run-ios.sh",
            "qemu/daemonlog.sh", "mkxnuimg.py", "uefi/mu-silicium.patch",
            "xnu/files/bsd/miscfs/apfs/apfs_lib.c",
            "xnu/files/bsd/miscfs/apfs/apfs_vfsops.c",
            "xnu/files/bsd/miscfs/apfs/apfs_vnops.c",
            "xnu/files/bsd/miscfs/ramfs/ramfs.c",
            "xnu/files/iokit/Kernel/MiniIOSurface.cpp",
            "xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp",
            "xnu/files/bsd/kern/kern_minimac.c",
            "xnu/files/libkern/amfi/miniamfi.c",
            "xnu/files/osfmk/arm64/psci_smp.c",
            "xnu/files/osfmk/arm64/gic_v3_irq.c",
        ],
        "userland": [
            "ios165/fetch.sh", "ios165/select-system.py", "userland/build.sh",
            "userland/src/stage0.c", "userland/src/init.c", "userland/libmini/start.S",
            "userland/src/inject.c", "userland/src/inject_sb.c",
            "userland/src/hello_dyn.c", "userland/src/surf_test.c",
            "userland/rc.ios", "userland/rc.launchd", "userland/mkjobs.py",
            "tools/apfs/mkapfs.py", "tools/apfs/apfs.py", "tools/apfs/test_fetch.c",
        ],
        "sweet": ["AGENTS.md", "NOTES.md", "xnu/README.md", "tools/sweet-init.S",
                  "tools/mkiosroot.sh", "tools/mkadt.py", "tools/qemu-sweet.py",
                  "tools/sweet-log.py", "tools/sweet-usbnet.sh"],
        "xnu8796": ["osfmk/arm/arm_init.c", "bsd/kern/mach_loader.c",
                    "osfmk/arm/commpage/commpage.c", "osfmk/vm/vm_shared_region.c",
                    "osfmk/vm/vm_shared_region_pager.c", "osfmk/arm64/proc_reg.h"],
        "xnu10063": ["bsd/kern/mach_loader.c", "osfmk/vm/vm_shared_region.c",
                     "osfmk/vm/vm_shared_region_pager.c"],
    }
    paths["sweet"] += [
        str(p.relative_to(repos["sweet"]))
        for p in sorted((repos["sweet"] / "xnu/patches").glob("*.patch"))
    ]
    inventory = []
    for name, names in paths.items():
        for relative in names:
            content = git(repos[name], "show", f"HEAD:{relative}").stdout
            if (repos[name] / relative).read_bytes() != content:
                raise RuntimeError(f"working file differs from Git object: {name}/{relative}")
            inventory.append({
                "repository": name, "path": relative,
                "commit": specs[name]["commit"], "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })
    return inventory


def host_apfs(repos, scratch):
    src = repos["kernel"] / "xnu/files/bsd/miscfs/apfs"
    tools = repos["userland"] / "tools/apfs"
    results = {}
    commands = [
        ("format_compile", ["cc", "-std=c11", "-I", src, tools / "fmt_check.c",
                            "-o", scratch / "fmt_check"]),
        ("format_execute", [scratch / "fmt_check"]),
    ]
    for name, command in commands:
        result = run(command)
        results[name] = result.stdout.decode(errors="replace")
    # test_format.py takes a filename; keep the exact upstream output as that input.
    fmt_file = scratch / "format.txt"
    fmt_file.write_text(results["format_execute"])
    result = run([sys.executable, tools / "test_format.py", fmt_file])
    results["format_check"] = result.stdout.decode(errors="replace")
    result = run([sys.executable, tools / "test_sparse.py"])
    results["sparse_check"] = result.stdout.decode(errors="replace")
    run(["cc", "-std=c11", "-O1", "-I", tools / "host-shim", "-c",
         src / "apfs_deflate.c", "-o", scratch / "deflate.o"])
    crypto_flags = ["-lcrypto"]
    if shutil.which("pkg-config"):
        flags = run(["pkg-config", "--cflags", "--libs", "openssl"], required=False)
        if flags.returncode == 0:
            crypto_flags = shlex.split(flags.stdout.decode())
    run(["cc", "-std=c11", "-D_DEFAULT_SOURCE", "-O1", "-I", src,
         tools / "test_fetch.c", src / "apfs_zbitmap.c", scratch / "deflate.o",
         *crypto_flags, "-lz", "-pthread",
         "-o", scratch / "test_fetch"])
    result = run([scratch / "test_fetch"])
    results["kernel_stream_fetch_check"] = result.stdout.decode(errors="replace")

    fixture = scratch / "fixture"
    (fixture / "usr/lib").mkdir(parents=True)
    pattern = bytes((i * 31 + (i >> 8)) & 255 for i in range(70001))
    (fixture / "usr/lib/pattern.bin").write_bytes(pattern)
    compressed = b"chiron-reuse-source-only\n" * 5000
    (fixture / "compressed.bin").write_bytes(compressed)
    (fixture / "pattern-link").symlink_to("usr/lib/pattern.bin")
    image = scratch / "fixture.apfs"
    result = run([sys.executable, tools / "mkapfs.py", "-z", "-o", image,
                  f"Reuse={fixture}"])
    results["fixture_build"] = result.stdout.decode(errors="replace")
    # These are the upstream kernel reader sources, built in their supported host mode.
    c_reader = scratch / "apfs-tool"
    run(["cc", "-std=c11", "-D_DEFAULT_SOURCE", "-O1", "-I", src, "-I", tools,
         tools / "apfs-tool.c", src / "apfs_lib.c", src / "apfs_write.c",
         src / "apfs_store.c", src / "apfs_zbitmap.c", scratch / "deflate.o",
         *crypto_flags, "-lz", "-pthread", "-o", c_reader])
    for name, content in [("/usr/lib/pattern.bin", pattern), ("/compressed.bin", compressed)]:
        actual = run([sys.executable, tools / "apfs.py", "cat", image, name]).stdout
        if actual != content:
            raise RuntimeError(f"APFS fixture readback differs: {name}")
        actual = run([c_reader, "cat", image, name]).stdout
        if actual != content:
            raise RuntimeError(f"kernel C APFS reader differs: {name}")
    results["fixture_readback"] = {
        "files": 2, "pattern_bytes": len(pattern), "compressed_bytes": len(compressed),
        "image_bytes": image.stat().st_size,
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "scope": "upstream Python writer/reader and kernel C reader in host mode; not kernel VFS/pagein",
    }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=PROJECT / ".cache/hackios-reuse")
    parser.add_argument("--fetch", action="store_true", help="fetch only the locked source repositories")
    parser.add_argument("--current-xnu", type=Path, help="also check a local patched XNU snapshot, read-only")
    parser.add_argument("--output", type=Path, default=PROJECT / "out/hackios-reuse/verification.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema": "chiron-hackios-reuse-verification-v1",
        "source_checks_passed": False, "host_checks_passed": False,
        "kernel_built": False, "qemu_executed": False, "hardware_observed": False,
        "apple_target_binaries_verified": False, "desktop_boot_qualified": False,
    }
    try:
        specs = json.loads(LOCK.read_text())
        repos = {name: verify_repository(args.cache.resolve(), name, spec, args.fetch)
                 for name, spec in specs.items() if name != "schema"}
        evidence["sources"] = {name: spec for name, spec in specs.items() if name != "schema"}
        evidence["inventory"] = source_inventory(repos, specs)
        evidence["host_environment"] = {
            "python": sys.version.split()[0], "system": platform.system(),
            "machine": platform.machine(),
            "cc": run(["cc", "--version"]).stdout.decode().splitlines()[0],
        }
        if args.current_xnu:
            current = args.current_xnu.resolve()
            result = run(["git", "apply", "--check",
                          repos["kernel"] / "xnu/redwood.patch"],
                         cwd=current, required=False)
            evidence["current_patched_snapshot"] = {
                "provenance": "content hashes only; not authenticated as a complete Git tree",
                "patch_returncode": result.returncode,
                "patch_stderr": result.stderr.decode(errors="replace"),
                "files": {
                    relative: hashlib.sha256((current / relative).read_bytes()).hexdigest()
                    for relative in (
                        "osfmk/arm/arm_init.c", "bsd/kern/mach_loader.c",
                        "osfmk/arm/commpage/commpage.c", "osfmk/vm/vm_shared_region.c",
                        "osfmk/vm/vm_shared_region_pager.c",
                    )
                },
            }
        with tempfile.TemporaryDirectory(prefix="chiron-reuse-") as directory:
            scratch = Path(directory)
            patch = repos["kernel"] / "xnu/redwood.patch"
            evidence["patch_application"] = {
                name: patch_check(repos[name], patch, specs[name]["commit"], scratch)
                for name in ("xnu8796", "xnu10063")
            }
            if not evidence["patch_application"]["xnu8796"]["applies"]:
                raise RuntimeError("locked HackiOS patch no longer applies to its locked 8796 base")
            evidence["source_checks_passed"] = True
            evidence["host_apfs"] = host_apfs(repos, scratch)
            evidence["host_checks_passed"] = True
        code = 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        evidence["error"] = str(error)
        code = 1
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in evidence.items()
                      if key.endswith(("passed", "qualified", "observed", "executed"))
                      or key == "error"}, ensure_ascii=False))
    print(f"Evidence: {args.output.resolve()}")
    return code


if __name__ == "__main__":
    sys.exit(main())
