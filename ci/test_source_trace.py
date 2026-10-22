#!/usr/bin/env python3
"""Require actual QEMU panic/data-abort snapshots from the numbered source kernel."""

import argparse
import importlib.util
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import uuid
from pathlib import Path


def decode_module(root):
    spec = importlib.util.spec_from_file_location("source_trace_decoder", root / "tools/decode_source_trace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_user_sigbus_fixture(root, tree):
    spec = importlib.util.spec_from_file_location("sigbus_fixture_builder", root / "ci/build_ram_selftest.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    destination = tree / "build/chiron-user-sigbus"
    destination.mkdir(parents=True, exist_ok=True)
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
    _, signature, addresses = builder.build_program(
        root / "scripts/chiron-init-sigbus.S", "launchd", destination, sdk)
    (destination / "addresses.json").write_text(json.dumps(addresses, indent=2) + "\n")
    identity = uuid.uuid5(uuid.NAMESPACE_URL, "org.ios-party.sigbus:" + signature["cdhash"])
    module = struct.pack("<I16sI", 2, identity.bytes, 1)
    module += bytes.fromhex(signature["cdhash"]) + bytes((2, 0, 0, 0))
    (destination / "trustcache.bundle").write_bytes(struct.pack("<II", 1, 8) + module)
    return destination, addresses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    decoder = decode_module(root)
    args.output.mkdir(parents=True, exist_ok=True)
    symbols = root / "dist/ram-init/kernel-symbols.txt"
    build = json.loads((root / "dist/ram-init/source-trace-build.json").read_text())
    normal = decoder.decode((args.tree / "build/chiron-qemu-init/chiron-trace.bin").read_bytes(), symbols)
    if normal["build_id"] != build["build_id"] or normal["early_stage"] != 4:
        raise ValueError("normal QEMU run did not execute all four source assembly stages")
    if not all(int(value, 16) for value in normal["early_pc"][:4]):
        raise ValueError("normal source entry/MMU PCs are missing")
    if normal["panic_count"]:
        raise ValueError("normal init run contains a panic")
    if normal["console"].count("CHIRON RAM init: PID 1, 4K ABI, heartbeat") < 2:
        raise ValueError("the actual PID 1 console writes were not captured twice")
    if normal["mapping_mode"] != 2 or not normal["native_paints"] or not any(
        "load_init_program" in event["text"] for event in normal["events"]
    ):
        raise ValueError("native source mapping/init path was not observed")
    (args.output / "normal.json").write_text(json.dumps(normal, indent=2) + "\n")
    (args.output / "normal.txt").write_text(normal["console"])
    shutil.copyfile(args.tree / "build/chiron-qemu-init/screen.png", args.output / "normal.png")
    kernel = args.tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
    kernel_sha256 = hashlib.sha256(kernel.read_bytes()).hexdigest()
    qualification = json.loads((root / "dist/ram-init/runtime-test.json").read_text())
    if qualification["kernel_sha256"] != kernel_sha256:
        raise ValueError("source observer tests must qualify the same normal runtime kernel")
    sigbus, sigbus_addresses = build_user_sigbus_fixture(root, args.tree)
    scenarios = (
        ("console", kernel, "", "_chiron_trace_console_checkpoint", []),
        ("panic", args.tree / "build/corecrypto-2023/negative-hmac/kernel.macho",
         "", "_chiron_trace_fault_checkpoint", []),
        ("data-abort", kernel, "chiron-trace-test=1", "_chiron_trace_fault_checkpoint", []),
        ("user-sigbus", kernel, "", "_chiron_trace_panic_checkpoint",
         ["--native-user", "--native-user-image", sigbus / "launchd.macho",
          "--trust-cache", sigbus / "trustcache.bundle", "--armvirt", "--armcpu", "--nvram-proxy"]),
    )
    reports = {}
    console_proof = None
    sigbus_proof = None
    for name, image, extra, checkpoint, options in scenarios:
        destination = args.tree / ("build/chiron-source-" + name)
        completed = subprocess.run([
            sys.executable, args.tree / "scripts/probe-darwinboot-xnu.py",
            "--kernel", image, "--output", destination, "--timeout", "180",
            "--break-symbol", checkpoint, "--kernel-extra-args", extra,
            "--qemu-ramfb", "--capture-qemu-screen", *options,
        ], cwd=args.tree, check=False)
        capture = args.output / (name + "-capture")
        capture.mkdir(parents=True, exist_ok=True)
        for filename in ("chiron-trace.bin", "gdb-stop.json", "gdb-failure.json",
                         "gdb-stderr.log", "gdb-uart.log", "screen.png"):
            path = destination / filename
            if path.exists():
                shutil.copyfile(path, capture / filename)
        if completed.returncode:
            if (capture / "chiron-trace.bin").exists():
                try:
                    partial = decoder.decode((capture / "chiron-trace.bin").read_bytes(), symbols)
                    (capture / "decoded.json").write_text(json.dumps(partial, indent=2) + "\n")
                    (capture / "console.txt").write_text(partial["console"])
                except ValueError as error:
                    (capture / "decode-error.txt").write_text(str(error) + "\n")
            raise RuntimeError(f"{name} observation failed; raw evidence preserved in {capture}")
        observed = decoder.decode((destination / "chiron-trace.bin").read_bytes(), symbols)
        if observed["build_id"] != build["build_id"]:
            raise ValueError("fault capture belongs to another source build")
        if name == "console":
            console_proof = verify_console_capture(observed)
        elif name == "panic":
            if (observed["fault"]["kind"] != 2 or not int(observed["panic_caller"], 16)
                    or "FIPS Kernel POST Failed" not in observed["console"]):
                raise ValueError("real POST panic was not captured before native debugger handling")
        elif name == "user-sigbus":
            sigbus_proof = verify_user_sigbus_capture(observed, sigbus_addresses)
            for filename in ("launchd.macho", "trustcache.bundle", "addresses.json"):
                shutil.copyfile(sigbus / filename, capture / filename)
        else:
            if observed["fault"]["kind"] != 1 or int(observed["fault"]["far"], 16) != 0x1000:
                raise ValueError("real data-abort FAR was not captured")
            if (observed["fault"].get("nearest_symbol") != "_chiron_trace_test"
                    or not any("explicit diagnostic data-abort injection requested" in event["text"]
                               for event in observed["events"])):
                raise ValueError("data abort must originate from the requested source injection")
            if not int(observed["fault"]["pc"], 16) or int(observed["fault"]["esr"], 16) >> 26 not in (0x24, 0x25):
                raise ValueError("data-abort PC/ESR is missing or wrong")
        reports[name] = observed["fault"]
        (args.output / (name + ".json")).write_text(json.dumps(observed, indent=2) + "\n")
        (args.output / (name + ".txt")).write_text(observed["console"])
        shutil.copyfile(destination / "screen.png", args.output / (name + ".png"))
    (args.output / "verification.json").write_text(json.dumps({
        "schema": 1, "build_id": build["build_id"], "qemu_only": True,
        "kernel_sha256": kernel_sha256,
        "source_entry_and_mmu_observed": True, "native_framebuffer_mapping_observed": True,
        "panic_reason_observed": True, "data_abort_pc_esr_far_observed": True,
        "console_initialization": console_proof,
        "user_selftest": qualification.get("user_selftest"),
        "user_sigbus_fault_observed": sigbus_proof is not None,
        "user_sigbus": sigbus_proof,
        "faults": reports, "hardware_observed": False,
    }, indent=2) + "\n")


def verify_user_sigbus_capture(observed, addresses):
    abort = observed.get("user_abort", {})
    signal = observed.get("user_signal", {})
    pc, target = addresses["_chiron_sigbus_store"], addresses["_chiron_sigbus_target"]
    if (observed["fault"]["kind"] != 2 or not observed["panic_count"]
            or not int(observed["panic_caller"], 16)):
        raise ValueError("the actual PID 1 signal did not reach the panic observer")
    reason = observed["console"].lower()
    if "initproc" not in reason or "exit reason namespace 2 subcode 0xa" not in reason:
        raise ValueError("SIGBUS did not produce the expected initproc signal exit panic")
    if (not abort.get("count") or abort.get("pid") != 1 or int(abort["pc"], 16) != pc
            or int(abort["far"], 16) != target or int(abort["mach_code"], 16) != 2
            or not abort["far_valid"]
            or int(abort["esr"], 16) >> 26 != 0x24 or not (int(abort["esr"], 16) & 0x40)):
        raise ValueError("unhandled EL0 write-protection abort was not captured exactly")
    if (not signal.get("count") or signal.get("pid") != 1 or signal.get("signal") != 10
            or signal.get("mach_exception") != 1 or int(signal["mach_code"], 16) != 2
            or int(signal["mach_address"], 16) != target):
        raise ValueError("the real Mach exception was not translated to PID 1 SIGBUS")
    tail = observed["console"].split("PANIC CALLER", 1)[-1]
    for text in ("LAST USER SIGNAL PID 0000000000000001", f"USER PC {pc:016X}",
                 f"USER FAR {target:016X}", "USER MACH CODE 0000000000000002",
                 "USER ESR ", "LAST EXCEPTION PC "):
        if text not in tail:
            raise ValueError("the final panic text lost user fault context: " + text)
    return {"pid": 1, "signal": 10, "mach_exception": 1, "mach_code": 2,
            "pc": hex(pc), "far": hex(target), "esr": abort["esr"],
            "panic_retains_user_fault": True, "qemu_only": True}


def verify_console_capture(observed):
    console = observed["console"]
    required = (
        "panic region uses device tree",
        "panic DT region absent; no memory accessed",
        "check_for_panic_log returned",
        "screen map enter",
        "screen map returned",
        "gc_initialize returned",
        "screen init returned",
        "PE_create_console() returned",
    )
    if observed["panic_count"] or "panic fixed board region" in console:
        raise ValueError("console must not panic or access a foreign board's fixed panic region")
    position = -1
    for message in required:
        position = console.find(message, position + 1)
        if position < 0:
            raise ValueError(f"console initialization evidence missing or out of order: {message}")
    return {"device_tree_panic_path": True, "absent_region_skipped": True,
            "framebuffer_map_returned": True, "console_returned": True}


if __name__ == "__main__":
    main()
