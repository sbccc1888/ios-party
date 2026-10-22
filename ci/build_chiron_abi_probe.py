#!/usr/bin/env python3
"""Build an independent signed static ARM64 ABI probe and two-file N4KROOT."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("abi_ram_builder", ROOT / "ci/build_ram_selftest.py")
RAM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RAM)
SOURCE = Path("/private/tmp/chiron-console-source")
REPORT = "abi-probe-build.json"
FIELDS = (
    "version", "required_mask", "passed_mask", "failure_stage",
    "last_call", "last_return", "last_nzcv", "task_port", "receive_port",
    "message_id", "message_payload", "empty_receive_result",
    "time_before", "time_after", "timebase_numer", "timebase_denom",
    "commpage_user_shift", "commpage_kernel_shift", "anon_base", "file_base",
    "file_magic", "console_fd", "completed_stage", "entry_sp", "aligned_sp",
    "send_result", "receive_result", "port_cleanup_result", "file_length",
    "reserved0", "reserved1", "reserved2",
)
BITS = {"mach_port": 1, "mach_message": 2, "absolute_time": 4, "commpage": 8,
        "anonymous_protect": 16, "file_mapping": 32, "receive_timeout_cleanup": 64}
RESULT_ABI = {"symbol": "_chiron_abi_results", "end_symbol": "_chiron_abi_results_end",
              "format": "<32Q", "bytes": 256, "fields": list(FIELDS),
              "version": 1, "required_mask": 127, "bits": BITS}
PINNED_SOURCE = {
    "osfmk/kern/syscall_sw.c": "b438f36565aaf23cab728bd11a9d456b73e4be2204754b74222f0b34b2eb186f",
    "bsd/kern/syscalls.master": "4bc39fcc0aa08f40e367064b947d586937d181ce6dd688683cefbdbe4650fa46",
    "osfmk/mach/message.h": "41d875b0dbdf98ad84802792607d91b7bf2fae28565ec624fd8df8cde96a9fe5",
    "osfmk/mach/mach_traps.h": "f661205105df097d67e93a8dcb623bdb6a159494cf2ae5791d21a3a80a61b817",
    "osfmk/mach/arm/traps.h": "9a112085e5635e71d0386d653ebad784c19763dc61d561301213a0eb81d65b7a",
    "osfmk/arm/cpu_capabilities.h": "e56cb1da12c65cc304fe802615cd5b393c793c98c9e10e5658382089a2baa672",
    "osfmk/ipc/ipc_policy.c": "5063e80669c192478c6556aa7d5e83f454b43430c6e6e91395f9f3d62fd9dba3",
}
TRAPS = {"task_self": -28, "port_allocate": -16, "port_insert_right": -21,
         "port_mod_refs": -19, "mach_msg2": -47, "timebase_info": -89, "absolute_time": -3}
DISPATCH_FRAGMENTS = {
    "svc_number": "inttrap_no=get_saved_state_svc_number(state);",
    "negative_mach_dispatch": "if(trap_no<0){switch(trap_no){",
    "absolute_time_case": "caseMACH_ARM_TRAP_ABSTIME:handle_mach_absolute_time_trap(state);return;",
    "mach_bsd_split": "mach_syscall(state);}else{",
    "bsd_dispatch": "unix_syscall(state,thread,p);",
    "absolute_time_value": "uint64_tnow=mach_absolute_time();saved_state64(state)->x[0]=now;",
}


def verify_source(directory):
    actual = {name: RAM.sha((directory / name).read_bytes()) for name in PINNED_SOURCE}
    if actual != PINNED_SOURCE:
        raise ValueError("ABI source differs from /private/tmp/chiron-console-source pinned layout")
    return actual


def audit_dispatch(directory, tree=None):
    """Check ABI-relevant function fragments without pinning unrelated trace edits."""
    path = directory / "osfmk/arm64/sleh.c"
    text = path.read_text()
    bodies = {}
    for name in ("handle_svc", "handle_mach_absolute_time_trap"):
        pattern = rf"\n{name}\(\s*arm_saved_state_t\s*\*state\s*\)\s*\{{(.*?)\n\}}"
        found = re.findall(pattern, text, re.S)
        if len(found) != 1:
            raise ValueError(f"expected one {name} definition in sleh.c")
        body = re.sub(r"/\*.*?\*/|//[^\n]*", "", found[0], flags=re.S)
        bodies[name] = re.sub(r"\s+", "", body)
    for name, fragment in DISPATCH_FRAGMENTS.items():
        function = "handle_mach_absolute_time_trap" if name == "absolute_time_value" else "handle_svc"
        if fragment not in bodies[function]:
            raise ValueError(f"sleh.c ABI dispatch branch changed: {name}")
    digest = RAM.sha(path.read_bytes())
    overlay = tree / "build/chiron-source-overlay.json" if tree else None
    overlay_digest = None
    if overlay and overlay.is_file():
        data = json.loads(overlay.read_text())
        if data.get("edits", {}).get("osfmk/arm64/sleh.c", {}).get("after_sha256") != digest:
            raise ValueError("prepared sleh.c differs from its source overlay report")
        overlay_digest = RAM.sha(overlay.read_bytes())
    return {"sleh_sha256": digest, "verified": True,
            "fragments_sha256": {name: RAM.sha(value.encode()) for name, value in DISPATCH_FRAGMENTS.items()},
            "source_overlay_report_sha256": overlay_digest,
            "source_overlay_bound": overlay_digest is not None}


def build(output, tree=None, abi_source=None):
    output = output.resolve()
    # Refuse destinations that overlap the original runtime fixture.
    if tree and output == (tree / "build/native-user-fixture").resolve():
        raise ValueError("ABI probe requires a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT).unlink(missing_ok=True)
    # Prepared CI trees carry the same ABI-defining source; never fetch a source tree.
    source_mode = "explicit-mirror" if abi_source is not None else "prepared-tree" if tree else "local-reference"
    if abi_source is None:
        abi_source = tree / ".cache/xnu-work" if tree else SOURCE
    source_hashes = verify_source(abi_source)
    dispatch = audit_dispatch(abi_source, tree)
    mockfs = RAM.verify_mockfs(tree) if tree else None
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
    child, child_signature, child_symbols = RAM.build_program(
        ROOT / "scripts/chiron-selftest-child.S", "child", output, sdk)
    marker = RAM.marker_offset(child)
    launchd, signature, addresses = RAM.build_program(
        ROOT / "scripts/chiron-abi-probe.S", "launchd", output, sdk,
        (f"-DCHIRON_ABI_CHILD_SIZE={len(child)}", f"-DCHIRON_ABI_CHILD_MAP_SIZE={RAM.align(len(child))}",
         f"-DCHIRON_ABI_MARKER_OFFSET={marker}"))
    required = {"_start", "_checkpoint", "_results", "_user_page_size", "_old_length",
                "_chiron_heartbeats", "_chiron_init_status",
                "_chiron_abi_results", "_chiron_abi_results_end"}
    if not required <= addresses.keys():
        raise ValueError("ABI probe compatibility/result symbols missing")
    if addresses["_chiron_abi_results_end"] - addresses["_chiron_abi_results"] != RESULT_ABI["bytes"]:
        raise ValueError("result structure size differs from the declared ABI")
    root = RAM.make_root(launchd, child)
    trust = RAM.make_trustcache([signature["cdhash"], child_signature["cdhash"]])
    (output / "root.img").write_bytes(root)
    (output / "trustcache.bundle").write_bytes(trust)
    (output / "addresses.json").write_text(json.dumps(addresses, indent=2) + "\n")
    (output / "child-addresses.json").write_text(json.dumps(child_symbols, indent=2) + "\n")
    artifacts = ("launchd.macho", "child.macho", "root.img", "trustcache.bundle",
                 "addresses.json", "child-addresses.json")
    sources = ("scripts/chiron-abi-probe.S", "scripts/chiron-selftest-child.S",
               "ci/build_chiron_abi_probe.py", "ci/build_ram_selftest.py",
               "ci/validate_chiron_abi_probe.py")
    report = {
        "schema_version": 1, "purpose": "independent static ARM64 system interface probe",
        "target": "arm64-apple-ios17.0", "march": "armv8-a", "result_abi": RESULT_ABI,
        "source_sha256": {name: RAM.sha((ROOT / name).read_bytes()) for name in sources},
        "abi_source_sha256": source_hashes, "abi_source_verified": True, "traps": TRAPS,
        "abi_source_mode": source_mode, "abi_source_root": str(abi_source.resolve()),
        "dispatch_audit": dispatch,
        "mockfs_source_sha256": mockfs, "mockfs_source_verified": mockfs is not None,
        "root_format": "N4KROOT", "root_version": 1, "root_file_count": 2,
        "root_paths": {"/sbin/launchd": "launchd.macho", "/probe.bin": "child.macho"},
        "child_marker_offset": marker, "child_marker_hex": RAM.MARKER.hex(),
        "file_map_bytes": RAM.align(len(child)), "message_id": 0x4348,
        "message_payload": 0x434849524F4E4142, "empty_receive_result": 0x10004003,
        "message_timeout_ms": 100, "empty_receive_timeout_ms": 1,
        "signatures": {"launchd": signature, "child": child_signature},
        "artifacts": {name: {"sha256": RAM.sha((output / name).read_bytes()),
                             "bytes": (output / name).stat().st_size} for name in artifacts},
        "static_build_verified": True, "qemu_runtime_qualified": False,
        "hardware_tested": False, "pthread_tls_tested": False,
    }
    (output / REPORT).write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, help="prepared tree; verify pinned mockfs")
    parser.add_argument("--output", type=Path, required=True, help="separate probe output directory")
    parser.add_argument("--abi-source", type=Path,
                        help="exact pinned source mirror (hash verified; never downloaded)")
    args = parser.parse_args()
    report = build(args.output, args.tree, args.abi_source)
    print(json.dumps({"output": str(args.output.resolve()), "report": REPORT,
                      "result_bytes": report["result_abi"]["bytes"],
                      "static_build_verified": True, "runtime_qualified": False}, indent=2))


if __name__ == "__main__":
    main()
