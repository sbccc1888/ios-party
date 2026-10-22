#!/usr/bin/env python3
"""Relink an isolated RAMFS kernel, then observe legacy and new roots in real QEMU."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location("ramfs_runtime_" + name, ROOT / "ci" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load("test_foundation_runtime")


def relink_recipe(text):
    prefix_anchor = "if [ ! -d refer/xnu/.git ]; then\n"
    suffix_anchor = "BOOT_PROGRESS=${XNU_BOOT_PROGRESS:-0}\n"
    if (text.count(prefix_anchor) != 1 or text.count(suffix_anchor) != 1
            or text.count("python3 scripts/prepare-xnu-source.py\n") != 1):
        raise ValueError("fixed source-build recipe changed")
    prefix = text[:text.index(prefix_anchor)]
    suffix = text[text.index(suffix_anchor):]
    if "make -C .cache/xnu-work " not in suffix:
        raise ValueError("source-build recipe lacks the real kernel make invocation")
    # The already qualified source stays intact. Only normal link inputs change.
    return prefix + 'export XNU_AVAILABILITY_PL="$ROOT/.cache/fakeroot/usr/local/libexec/availability.pl"\n' + suffix


def relink(tree, runtime_dir, archive_dir, output):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("kernel relink is restricted to the remote build runner")
    recipe = tree / "scripts/build-xnu-4k-baseline.sh"
    generated = tree / "scripts/build-chiron-ramfs-only.sh"
    if generated.exists():
        raise ValueError("experimental relink recipe already exists")
    kernel = tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
    archive = tree / "build/mockfs-fixture/libmockfs_fixture.a"
    normal = runtime_dir / "kernel.macho"
    if GATE.digest(kernel) != GATE.digest(normal):
        raise ValueError("kernel tree differs from the qualified normal runtime")
    compile_report = json.loads((archive_dir / "compile-verification.json").read_text())
    replacement = archive_dir / compile_report["archive"]
    if (not compile_report.get("real_xnu_units_compiled")
            or GATE.digest(replacement) != compile_report["archive_sha256"]):
        raise ValueError("experimental archive compilation proof changed")
    pins = json.loads((ROOT / "ci/sources.json").read_text())
    actual = subprocess.check_output(
        ["git", "-C", str(tree / "refer/xnu"), "rev-parse", "HEAD"], text=True).strip()
    if actual != pins["xnu"]["commit"]:
        raise ValueError("XNU reference commit changed")
    env = dict(os.environ)
    settings = json.loads((runtime_dir / "chiron-runtime-environment.json").read_text())
    if settings.get("XNU_CHIRON_TRACE") != "1" or settings.get("XNU_SOURCE_MOCKFS") != "1":
        raise ValueError("normal runtime configuration is not the source-trace mockfs baseline")
    env.update(settings)
    env["JOBS"] = "3"
    paths = (kernel, Path(str(kernel) + ".crypto-hmac.json"), archive)
    saved = {path: path.read_bytes() for path in paths}
    output_kernel = output / "kernel.macho"
    try:
        generated.write_text(relink_recipe(recipe.read_text()))
        shutil.copyfile(replacement, archive)
        kernel.unlink()
        with (output / "relink.log").open("wb") as log:
            subprocess.run(["bash", generated], cwd=tree, env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
        shutil.copyfile(kernel, output_kernel)
        shutil.copyfile(Path(str(kernel) + ".crypto-hmac.json"),
                        Path(str(output_kernel) + ".crypto-hmac.json"))
    finally:
        for path, data in saved.items():
            path.write_bytes(data)
        generated.unlink(missing_ok=True)
    if kernel.read_bytes() != saved[kernel] or archive.read_bytes() != saved[archive]:
        raise ValueError("normal kernel/archive were not restored exactly")
    if GATE.digest(output_kernel) == GATE.digest(normal):
        raise ValueError("experimental kernel did not incorporate the RAMFS archive")
    return output_kernel


def observe(tree, kernel, fixture, output):
    subprocess.run([
        sys.executable, tree / "scripts/probe-darwinboot-xnu.py",
        "--kernel", kernel, "--output", output, "--timeout", "180",
        "--armvirt", "--armcpu", "--nvram-proxy", "--require-crypto14",
        "--native-user", "--native-user-image", fixture / "launchd.macho",
        "--native-user-ramdisk", fixture / "root.img",
        "--trust-cache", fixture / "trustcache.bundle", "--check-commpage",
        "--qemu-ramfb", "--capture-qemu-screen",
    ], cwd=tree, check=True)
    return json.loads((output / "gdb-stop.json").read_text())


def run(tree, runtime_dir, archive_dir, output):
    tree, runtime_dir = tree.resolve(), runtime_dir.resolve()
    archive_dir, output = archive_dir.resolve(), output.resolve()
    GATE.require_runtime(runtime_dir)
    output.mkdir(parents=True, exist_ok=False)
    kernel = relink(tree, runtime_dir, archive_dir, output)
    fixture = output / "fixture"
    subprocess.run([
        sys.executable, ROOT / "ci/build_chiron_ramfs_probe.py",
        "--tree", tree, "--output", fixture,
    ], check=True)
    subprocess.run([
        sys.executable, tree / "scripts/test-corecrypto14-post.py", "--kernel", kernel,
    ], cwd=tree, check=True)
    post = json.loads((tree / "build/corecrypto-2023/post-test.json").read_text())
    (output / "post-test.json").write_text(json.dumps(post, indent=2) + "\n")
    for name in ("darwinboot-crypto14", "darwinboot-bad-hmac"):
        capture = output / name
        capture.mkdir()
        for filename in ("gdb-stop.json", "gdb-failure.json", "gdb-uart.log", "screen.png", "chiron-trace.bin"):
            source = tree / "build" / name / filename
            if source.exists():
                shutil.copyfile(source, capture / filename)
    legacy_output = output / "qemu-legacy"
    legacy = observe(tree, kernel, runtime_dir, legacy_output)
    legacy_result = GATE.validate(post, legacy, require_selftest=True)
    for filename, field in (("root.img", "offline_root_sha256"),
                            ("launchd.macho", "native_user_image_sha256"),
                            ("trustcache.bundle", "trust_cache_bundle_sha256")):
        if legacy.get(field) != GATE.digest(runtime_dir / filename):
            raise ValueError("legacy RAMFS-kernel observation used other runtime inputs")
    probe_output = output / "qemu-ramfs"
    observed = observe(tree, kernel, fixture, probe_output)
    build = json.loads((fixture / "abi-probe-build.json").read_text())
    if build.get("root_format") != "CHRAMFS" or not build.get("whole_file_readback_requested"):
        raise ValueError("new root probe did not request nested-path/full-file readback")
    GATE.verify_inputs(observed, fixture, build, GATE.digest(kernel))
    inherited = GATE.validate(post, observed)
    result = load("validate_chiron_abi_probe").validate(observed, build)
    source_build = json.loads((runtime_dir / "source-trace-build.json").read_text())
    trace = GATE.decode((probe_output / "chiron-trace.bin").read_bytes())
    if trace["build_id"] != source_build["build_id"] or trace["panic_count"]:
        raise ValueError("RAMFS runtime trace identity/panic check failed")
    report = {
        "schema": 1, "kernel_sha256": GATE.digest(kernel),
        "normal_kernel_sha256": GATE.digest(runtime_dir / "kernel.macho"),
        "root_sha256": GATE.digest(fixture / "root.img"),
        "kernel_linked_with_experimental_archive": True,
        "kernel_binary_instruction_patching": False,
        "legacy_two_file_root": legacy_result,
        "baseline_runtime_requirements": inherited, "probe": result,
        "new_root_mounted": True, "nested_path_and_full_file_mapping_observed": True,
        "normal_kernel_and_archive_restored": True,
        "qemu_only": True, "hardware_tested": False, "apple_userspace_executed": False,
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.tree, args.runtime, args.archive, args.output), indent=2))


if __name__ == "__main__":
    main()
