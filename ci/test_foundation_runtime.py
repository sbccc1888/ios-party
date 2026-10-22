#!/usr/bin/env python3
"""Run independent userspace probes against the normally qualified source kernel."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from decode_source_trace import decode


def load_recipe(name):
    spec = importlib.util.spec_from_file_location("foundation_" + name, ROOT / "ci" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


require_runtime = load_recipe("build_ram_payload").require_runtime
validate = load_recipe("validate_runtime").validate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_baseline(runtime, proof, build):
    if (
        not runtime.get("user_selftest")
        or proof.get("user_selftest") != runtime["user_selftest"]
        or proof.get("kernel_sha256") != runtime.get("kernel_sha256")
        or proof.get("build_id") != build.get("build_id")
        or not all(proof.get(field) is True for field in (
            "source_entry_and_mmu_observed", "native_framebuffer_mapping_observed",
            "panic_reason_observed", "data_abort_pc_esr_far_observed",
            "user_sigbus_fault_observed",
        ))
    ):
        raise ValueError("the normal source kernel has not completed its independent qualification")


def verify_inputs(observed, fixture, build, kernel_sha256):
    if observed.get("kernel_sha256") != kernel_sha256:
        raise ValueError("foundation probe observed another kernel")
    for filename, observed_key in (
        ("launchd.macho", "native_user_image_sha256"),
        ("root.img", "offline_root_sha256"),
        ("trustcache.bundle", "trust_cache_bundle_sha256"),
    ):
        actual = digest(fixture / filename)
        if (build.get("artifacts", {}).get(filename, {}).get("sha256") != actual
                or observed.get(observed_key) != actual):
            raise ValueError("foundation input was changed or not observed: " + filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "dist/ram-init")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tree, runtime_dir, output = args.tree.resolve(), args.runtime.resolve(), args.output.resolve()
    runtime = require_runtime(runtime_dir)
    proof = json.loads((runtime_dir / "source-trace-tests/verification.json").read_text())
    build = json.loads((runtime_dir / "source-trace-build.json").read_text())
    require_baseline(runtime, proof, build)
    kernel = runtime_dir / "kernel.macho"
    if digest(kernel) != runtime["kernel_sha256"]:
        raise ValueError("normal kernel identity changed")
    output.mkdir(parents=True, exist_ok=False)
    (output / "baseline.json").write_text(json.dumps({
        "build_id": build["build_id"], "kernel_sha256": runtime["kernel_sha256"],
        "normal_selftest": runtime["user_selftest"], "normal_source_trace": proof,
    }, indent=2) + "\n")
    fixture = output / "abi-fixture"
    subprocess.run([
        sys.executable, ROOT / "ci/build_chiron_abi_probe.py",
        "--tree", tree, "--output", fixture,
    ], check=True)
    fixture_report = fixture / "abi-probe-build.json"
    probe_output = output / "qemu-abi"
    subprocess.run([
        sys.executable, tree / "scripts/probe-darwinboot-xnu.py",
        "--kernel", kernel, "--output", probe_output, "--timeout", "180",
        "--armvirt", "--armcpu", "--nvram-proxy", "--require-crypto14",
        "--native-user", "--native-user-image", fixture / "launchd.macho",
        "--native-user-ramdisk", fixture / "root.img",
        "--trust-cache", fixture / "trustcache.bundle",
        "--check-commpage", "--qemu-ramfb", "--capture-qemu-screen",
    ], cwd=tree, check=True)
    observation = probe_output / "gdb-stop.json"
    observed = json.loads(observation.read_text())
    fixture_build = json.loads(fixture_report.read_text())
    verify_inputs(observed, fixture, fixture_build, runtime["kernel_sha256"])
    post = json.loads((runtime_dir / "post-test.json").read_text())
    inherited = validate(post, observed)
    trace = decode((probe_output / "chiron-trace.bin").read_bytes())
    if trace["build_id"] != build["build_id"] or trace["panic_count"]:
        raise ValueError("foundation trace has a mismatched identity or a panic")
    subprocess.run([
        sys.executable, ROOT / "ci/validate_chiron_abi_probe.py",
        "--observation", observation, "--build-report", fixture_report,
        "--output", output / "abi-verification.json",
    ], check=True)
    subprocess.run([
        sys.executable, ROOT / "ci/build_chiron_ramfs_archive.py",
        "--tree", tree, "--output", output / "ramfs-archive",
    ], check=True)
    subprocess.run([
        sys.executable, ROOT / "ci/test_ramfs_runtime.py",
        "--tree", tree, "--runtime", runtime_dir, "--archive", output / "ramfs-archive",
        "--output", output / "ramfs-runtime",
    ], check=True)
    report = {
        "schema": 1, "build_id": build["build_id"],
        "kernel_sha256": runtime["kernel_sha256"],
        "baseline_runtime_requirements": inherited,
        "abi": json.loads((output / "abi-verification.json").read_text()),
        "ramfs_compile": json.loads(
            (output / "ramfs-archive/compile-verification.json").read_text()),
        "ramfs_runtime": json.loads(
            (output / "ramfs-runtime/verification.json").read_text()),
        "qemu_only": True, "hardware_observed": False,
        "apple_userspace_executed": False,
        "phone_selftest_fixture_replaced": False,
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
