#!/usr/bin/env python3
"""Require real POST, EL0, 4K ABI and two completed write/sleep heartbeats."""

import argparse
import hashlib
import json
import struct
from pathlib import Path


def validate_user_selftest(memory):
    raw = memory.get("_chiron_selftest_results")
    if not isinstance(raw, str):
        raise ValueError("structured user selftest results were not captured")
    try:
        data = bytes.fromhex(raw)
    except ValueError as error:
        raise ValueError("malformed user selftest results") from error
    if len(data) != 64:
        raise ValueError("user selftest results must contain eight uint64 values")
    version, required, passed, failure, child_pid, waited_pid, wait_status, reserved = struct.unpack("<8Q", data)
    if version != 1 or required != 15 or passed != required or reserved:
        raise ValueError("unknown or incomplete user selftest result set")
    if failure:
        raise ValueError(f"user selftest failed at stage {failure}")
    if child_pid <= 1 or waited_pid != child_pid or wait_status != 37 << 8:
        raise ValueError("the independent child image did not execute and exit as expected")
    return {
        "schema": version, "required_mask": required, "passed_mask": passed,
        "file_read_passed": True, "anonymous_mmap_passed": True,
        "independent_exec_passed": True, "child_reaped": True,
        "child_pid": child_pid, "wait_pid": waited_pid,
        "wait_status": wait_status, "child_exit_code": 37,
        "qemu_only": True, "hardware_observed": False,
    }


def validate(post, observed, require_selftest=False):
    if not post.get("kernel_sha256") or post["kernel_sha256"] != observed.get("kernel_sha256"):
        raise ValueError("POST and EL0 observations must use the same kernel")
    if not post.get("default_provider_post_and_registration_passed") or not post.get("corrupted_hmac_refused"):
        raise ValueError("real source provider POST/refusal observations are required")
    if not observed.get("native_4k_mmu_observed") or not observed.get("native_user_el0_observed"):
        raise ValueError("real native-4K EL0 entry was not observed")
    if not observed.get("native_user_4k_abi_observed"):
        raise ValueError("PID 1 and page-size syscalls did not complete")
    memory = observed["native_user_memory"]
    beats = int.from_bytes(bytes.fromhex(memory["_chiron_heartbeats"]), "little")
    status = int.from_bytes(bytes.fromhex(memory["_chiron_init_status"]), "little", signed=True)
    if status != 2 or beats < 2:
        raise ValueError("init has not completed two real console-write/timer waits")
    cpu = observed["armcpu_globals"]
    if cpu["_armcpu_registered"] != 1 or cpu["_armcpu_gic_ready"] != 1 or cpu["_armcpu_timer_interrupts"] < 1:
        raise ValueError("CPU/GIC/timer are not functioning")
    pthread = observed["pthread_globals"]
    if pthread["_source_pthread_status"] != 0 or not pthread["_pthread_functions"]:
        raise ValueError("pthread registration was not observed")
    result = {"qemu_only": True, "hardware_boot_qualified": False,
            "native_4k_mmu_observed": True, "init_pid": 1,
            "init_heartbeats": beats, "timer_interrupts": cpu["_armcpu_timer_interrupts"],
            "real_crypto_post_and_corruption_refusal": True,
            "source_pthread_registered": True, "user_data_written": False,
            "apple_trust_chain_verified": False,
            "diagnostic_empty_entitlements_only": True,
            "trust_cache_runtime_enabled": False}
    if require_selftest:
        result["user_selftest"] = validate_user_selftest(memory)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-selftest", action="store_true")
    args = parser.parse_args()
    post = json.loads((args.tree / "build/corecrypto-2023/post-test.json").read_text())
    observed = json.loads((args.tree / "build/chiron-qemu-init/gdb-stop.json").read_text())
    result = validate(post, observed, require_selftest=args.require_selftest)
    kernel = args.tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
    result["kernel_sha256"] = hashlib.sha256(kernel.read_bytes()).hexdigest()
    if result["kernel_sha256"] != observed["kernel_sha256"]:
        raise ValueError("observed kernel differs from the current artifact")
    for filename, key in (("launchd.macho", "native_user_image_sha256"), ("trustcache.bundle", "trust_cache_bundle_sha256")):
        digest = hashlib.sha256((args.tree / "build/native-user-fixture" / filename).read_bytes()).hexdigest()
        if digest != observed[key]:
            raise ValueError(f"observed runtime input differs: {filename}")
        result[filename + "_sha256"] = digest
    if args.require_selftest:
        fixture = args.tree / "build/native-user-fixture"
        root = fixture / "root.img"
        root_digest = hashlib.sha256(root.read_bytes()).hexdigest()
        if root_digest != observed.get("offline_root_sha256"):
            raise ValueError("the observed selftest root differs from the artifact")
        result["root.img_sha256"] = root_digest
        result["child.macho_sha256"] = hashlib.sha256((fixture / "child.macho").read_bytes()).hexdigest()
    adt = args.output.parent / "kernel.macho.adt"
    result["kernel.macho.adt_sha256"] = hashlib.sha256(adt.read_bytes()).hexdigest()
    audit = json.loads((args.tree / "build/xnu/kernel-audit.json").read_text())
    if audit["sha256"] != result["kernel_sha256"]:
        raise ValueError("static architecture audit is stale")
    result["initial_tcr"] = audit["initial_tcr"]
    result["active_tcr"] = observed["system_registers"]["TCR_EL1"]
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
