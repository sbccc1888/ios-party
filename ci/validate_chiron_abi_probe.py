#!/usr/bin/env python3
"""Validate the independent probe's captured result ABI; not a whole-kernel gate."""

import argparse
import importlib.util
import json
from pathlib import Path
import struct

SPEC = importlib.util.spec_from_file_location("abi_probe_builder", Path(__file__).with_name("build_chiron_abi_probe.py"))
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def memory_bytes(memory, symbol, size):
    raw = memory.get(symbol)
    if not isinstance(raw, str):
        raise ValueError(f"missing captured symbol {symbol}")
    try:
        data = bytes.fromhex(raw)
    except ValueError as error:
        raise ValueError(f"invalid hex for {symbol}") from error
    if len(data) != size:
        raise ValueError(f"{symbol} must contain exactly {size} bytes")
    return data


def validate(observed: dict, build_report: dict) -> dict:
    if build_report.get("result_abi") != BUILD.RESULT_ABI:
        raise ValueError("unknown build result ABI")
    if not build_report.get("static_build_verified") or not build_report.get("abi_source_verified"):
        raise ValueError("probe build/source audit did not pass")
    if build_report.get("abi_source_sha256") != BUILD.PINNED_SOURCE:
        raise ValueError("build used a different syscall/trap source layout")
    dispatch = build_report.get("dispatch_audit", {})
    fragments = {name: BUILD.RAM.sha(value.encode()) for name, value in BUILD.DISPATCH_FRAGMENTS.items()}
    if not dispatch.get("verified") or dispatch.get("fragments_sha256") != fragments:
        raise ValueError("build did not audit Mach/BSD/absolute-time dispatch")
    memory = observed.get("native_user_memory", {})
    values = struct.unpack("<32Q", memory_bytes(memory, "_chiron_abi_results", 256))
    result = dict(zip(BUILD.FIELDS, values))
    if result["version"] != 1 or result["required_mask"] != 127:
        raise ValueError("unknown probe result version/mask")
    if result["failure_stage"]:
        raise ValueError(f"ABI probe failed/running at stage {result['failure_stage']}: "
                         f"call=0x{result['last_call']:016x}, return=0x{result['last_return']:016x}")
    if result["passed_mask"] != 127 or any(result[name] for name in ("reserved0", "reserved1", "reserved2")):
        raise ValueError("incomplete or malformed probe results")
    if any(not 0 < result[name] < 0xFFFFFFFF for name in ("task_port", "receive_port")):
        raise ValueError("invalid task/receive port name")
    if (result["message_id"], result["message_payload"]) != (0x4348, 0x434849524F4E4142):
        raise ValueError("Mach receive header/payload assertion missing")
    if (result["send_result"], result["receive_result"], result["port_cleanup_result"]) != (0, 0, 0):
        raise ValueError("Mach message or port release failed")
    if result["empty_receive_result"] != 0x10004003:
        raise ValueError("empty receive did not return MACH_RCV_TIMED_OUT")
    if not 0 < result["time_before"] < result["time_after"]:
        raise ValueError("absolute time did not advance")
    if any(not 0 < result[name] <= 0xFFFFFFFF for name in ("timebase_numer", "timebase_denom")):
        raise ValueError("invalid timebase ratio")
    if (result["commpage_user_shift"], result["commpage_kernel_shift"]) != (12, 12):
        raise ValueError("commpage does not publish native 4K shifts")
    if any(not result[name] or result[name] & 4095 for name in ("anon_base", "file_base")):
        raise ValueError("unaligned/empty mmap address")
    if result["file_magic"] != 0xFEEDFACF:
        raise ValueError("mapped file is not the child Mach-O")
    if result["file_length"] != build_report["artifacts"]["child.macho"]["bytes"]:
        raise ValueError("mapped child length differs from build")
    if result["aligned_sp"] != result["entry_sp"] & ~15 or not result["aligned_sp"]:
        raise ValueError("probe did not align its entry SP")
    if result["completed_stage"] != 64 or result["console_fd"] > 0x7FFFFFFF:
        raise ValueError("probe did not complete file/console stages")
    if result["last_call"] != 93 or result["last_return"] or result["last_nzcv"] & (1 << 29):
        raise ValueError("final heartbeat select failed")
    # Compatibility assertions are cheap local prerequisites; outer gate binds artifacts/kernel.
    base = struct.unpack("<4Q", memory_bytes(memory, "_results", 32))
    page = struct.unpack("<Q", memory_bytes(memory, "_user_page_size", 8))[0]
    length = struct.unpack("<Q", memory_bytes(memory, "_old_length", 8))[0]
    status = struct.unpack("<q", memory_bytes(memory, "_chiron_init_status", 8))[0]
    beats = struct.unpack("<Q", memory_bytes(memory, "_chiron_heartbeats", 8))[0]
    if base[:2] != (1, 0) or base[2] & (1 << 29) or base[3] & (1 << 29):
        raise ValueError("PID/page-size syscall compatibility failed")
    if (page, length, status) != (4096, 4, 2) or beats < 2:
        raise ValueError("probe lacks 4K ABI/status2/two completed heartbeats")
    return {"schema_version": 1, "abi_probe_passed": True, "probe_results": result,
            "heartbeats": beats, "runtime_qualification_scope": "probe result semantics only",
            "kernel_artifact_binding_checked": False, "post_cpu_pthread_gate_checked": False,
            "hardware_tested": False, "pthread_tls_tested": False,
            "write_fault_enforcement_tested": False, "post_unmap_fault_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.unlink(missing_ok=True)
    result = validate(json.loads(args.observation.read_text()), json.loads(args.build_report.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
