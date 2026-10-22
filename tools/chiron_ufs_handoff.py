#!/usr/bin/env python3
"""Audit saved UFS handoff metadata. Never qualifies a board or opens devices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[1]
U64 = (1 << 64) - 1
CAPTURE_SHA256 = "08e76d50d5b513614203521be6f5eeaa40966e1e24a087ea582ee3fe6e9d7718"
FACTS = (
    "power_retained", "clocks_retained", "phy_ready", "link_active",
    "firmware_drained", "submissions_blocked", "interrupt_owned",
    "firmware_dma_retired", "boot_services_exited", "dma_route_known",
    "dma_permissions", "cache_contract", "lease_retained",
)
RESOURCES = dict(
    host_base=0x1da4000, host_size=0x2500, phy_base=0x1da7000, phy_size=0xda8,
    gcc_base=0x100000, gcc_size=0xb0000, gdsc_base=0x175004, gdsc_size=4,
    soc_id=292, soc_revision=0x20001, lanes=2, spi=265, irq_flags=0,
    intid=297, host_clocks=9,
)
STAMP_KEYS = {"boot_id", "generation", "ticks", "phase"}
LEASE_INTS = {"token", "cpu", "bus", "bytes", "address_bits", "cache_line"}
LEASE_BOOLS = {"cached", "device_read", "device_write", "retained_until_boot_end"}


def regular_bytes(path, limit=32 << 20):
    # O_NONBLOCK plus fstat prevents a raced FIFO from blocking before rejection.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("only saved regular files are accepted")
        if info.st_size > limit:
            raise ValueError("saved file exceeds size limit")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("saved file exceeds size limit")
        return data


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    return json.loads(regular_bytes(path, 1 << 20), object_pairs_hook=unique_object)


def keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label}: exact fields required: {sorted(expected)}")


def uint(value, label):
    if type(value) is not int or not 0 <= value <= U64:
        raise ValueError(f"{label}: uint64 required")
    return value


def stamp(value, label):
    keys(value, STAMP_KEYS, label)
    boot = value["boot_id"]
    if not isinstance(boot, str) or len(boot) != 32:
        raise ValueError(f"{label}: 16-byte boot ID required")
    try:
        decoded = bytes.fromhex(boot)
    except ValueError as error:
        raise ValueError(f"{label}: invalid boot ID") from error
    if len(decoded) != 16 or not any(decoded) or boot != decoded.hex():
        raise ValueError(f"{label}: nonzero canonical boot ID required")
    if not uint(value["generation"], label):
        raise ValueError(f"{label}: nonzero generation required")
    uint(value["ticks"], label)
    if value["phase"] not in ("unknown", "firmware", "exit_callback", "kernel_post_ebs"):
        raise ValueError(f"{label}: invalid phase")


def check_stamp(value, context, label):
    stamp(value, label)
    current = context["current"]
    if any(value[k] != current[k] for k in ("boot_id", "generation")):
        raise ValueError(f"{label}: different boot or ownership generation")
    if value["phase"] != "kernel_post_ebs":
        raise ValueError(f"{label}: not after successful ExitBootServices return")
    if not context["ebs_returned_ticks"] <= value["ticks"] <= current["ticks"]:
        raise ValueError(f"{label}: stale or future evidence")
    if current["ticks"] - value["ticks"] > context["max_age_ticks"]:
        raise ValueError(f"{label}: stale evidence")


def lease(value, label):
    keys(value, LEASE_INTS | LEASE_BOOLS, label)
    for field in LEASE_INTS:
        uint(value[field], f"{label}.{field}")
    if any(type(value[k]) is not bool for k in LEASE_BOOLS):
        raise ValueError(f"{label}: boolean lease attributes required")
    if not value["token"] or value["bytes"] < 32768:
        raise ValueError(f"{label}: missing lease token or undersized DMA")
    for field in ("cpu", "bus"):
        start, length = value[field], value["bytes"]
        if not start or start > U64 - length or (start | length) & 4095:
            raise ValueError(f"{label}: unaligned or overflowing DMA range")
    bits = value["address_bits"]
    if not 32 <= bits <= 64 or value["bus"] + value["bytes"] > 1 << bits:
        raise ValueError(f"{label}: DMA address width exceeded")
    if value["cache_line"] != 64 or not all(value[k] for k in LEASE_BOOLS):
        raise ValueError(f"{label}: cache, DMA permissions or lifetime contract missing")


def validate_metadata(evidence, context):
    """Structural check only; even two mutually consistent JSONs cannot authorize."""
    keys(evidence, {"abi", "resources", "stamp", "lease", "facts"}, "evidence")
    keys(context, {"current", "ebs_returned_ticks", "max_age_ticks",
                   "held_lease", "exit_boot_services_returned"}, "context")
    if type(evidence["abi"]) is not int or evidence["abi"] != 1:
        raise ValueError("unsupported evidence ABI")
    keys(evidence["resources"], RESOURCES, "resources")
    for field, expected in RESOURCES.items():
        if uint(evidence["resources"][field], field) != expected:
            raise ValueError(f"resource mismatch: {field}")
    stamp(context["current"], "current")
    for field in ("ebs_returned_ticks", "max_age_ticks"):
        if not uint(context[field], field):
            raise ValueError(f"{field}: nonzero tick value required")
    if context["exit_boot_services_returned"] is not True:
        raise ValueError("ExitBootServices return is not confirmed")
    check_stamp(context["current"], context, "current")
    check_stamp(evidence["stamp"], context, "packet")
    lease(evidence["lease"], "lease")
    lease(context["held_lease"], "held_lease")
    if evidence["lease"] != context["held_lease"]:
        raise ValueError("DMA lease differs from the current owner object")
    facts = evidence["facts"]
    if not isinstance(facts, dict) or set(facts) - set(FACTS):
        raise ValueError("unknown fact name or invalid facts object")
    unknown, observed, failed = [], [], []
    for name in FACTS:
        item = facts.get(name)
        if item is None:
            unknown.append(name)
            continue
        keys(item, {"state", "stamp"}, name)
        if item["state"] == "unknown":
            # An explicit unknown may have no stamp; it never contributes evidence.
            if item["stamp"] is not None:
                stamp(item["stamp"], name)
            unknown.append(name)
            continue
        if item["state"] not in ("observed", "failed"):
            raise ValueError(f"{name}: invalid observation state")
        check_stamp(item["stamp"], context, name)
        if item["stamp"]["ticks"] > evidence["stamp"]["ticks"]:
            raise ValueError(f"{name}: observation is newer than its packet")
        (observed if item["state"] == "observed" else failed).append(name)
    return {
        "status": "failed_observation" if failed else "missing" if unknown else "metadata_consistent",
        "observed": observed, "unknown": unknown, "failed": failed,
        "verified": 0, "board_handoff_qualified": False,
        "hardware_execution_allowed": False, "hardware_executed": False,
        "trusted_board_producer_present": False,
        "scope": "Saved metadata only. Matching IDs, times and lease claims do not authenticate a producer.",
    }


def inventory(root, edk2, unixnu):
    paths = [
        root / "ci/sources.json",
        root / "drivers/ChironUfsReadonly/ChironUfsReadonly.h",
        root / "research/upstream/linux-msm8998/msm8998.dtsi",
        root / "research/upstream/linux-msm8998/chiron-msm8998-mtp.dtsi",
        edk2 / "Platforms/Msm8998Binaries/chiron/dxe.inc",
        edk2 / "Platforms/Msm8998Binaries/common/Binaries/UFSDxe/UFSDxe.inf",
        edk2 / "Platforms/Msm8998Binaries/common/Binaries/UFSDxe/UFSDxe.efi",
        edk2 / "Platforms/Msm8998FamilyPkg/Drivers/SynapticsRmi4Dxe/SynapticsTouchDxe.c",
        edk2 / "Platforms/Msm8998ACPI/xiaomi/chiron/dsdt.asl",
        unixnu / "drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c",
    ]
    records = []
    for path in paths:
        if not path.exists():
            records.append({"path": str(path), "available": False})
            continue
        data = regular_bytes(path)
        record = {"path": str(path), "available": True, "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()}
        if path.suffix in (".c", ".inf", ".inc"):
            terms = ("ExitBootServices", "ExitBootService", "UFSDxe", "I2cQupProtocol->Close")
            record["relevant_lines"] = [
                {"line": n, "text": line.strip()}
                for n, line in enumerate(data.decode().splitlines(), 1)
                if any(term in line for term in terms)
            ]
        records.append(record)
    return records


def saved_resources(data):
    from chiron_ufs_readonly import inspect_resources, runtime_nodes
    resources = inspect_resources(runtime_nodes(data))
    for name, base, size, compatible in (
            ("gcc", RESOURCES["gcc_base"], RESOURCES["gcc_size"], ["qcom,gcc-8998-v2"]),
            ("gdsc", RESOURCES["gdsc_base"], RESOURCES["gdsc_size"], ["qcom,gdsc"])):
        device = resources[name]
        if device["compatible"] != compatible or device["ranges"] != [
                {"base": base, "size": size, "end_exclusive": base + size}]:
            raise ValueError(f"saved {name} resource mismatch")
    if any(item["provider"] != "/soc/qcom,gcc@100000" for item in resources["clocks"]):
        raise ValueError("saved clock provider mismatch")
    return resources


def audit(root=ROOT, edk2=Path("/private/tmp/ios-party-research/edk2"),
          unixnu=Path("/private/tmp/ios-party-research/unixnu")):
    capture = root / "research/artifacts/chiron-live-20261020/runtime-tree.tar"
    resource_evidence = {"available": False}
    if capture.exists():
        data = regular_bytes(capture)
        manifest = load_json(capture.parent / "manifest.json")
        saved = manifest["files"]["runtime-tree.tar"]
        digest = hashlib.sha256(data).hexdigest()
        if (digest != CAPTURE_SHA256 or saved["returncode"] != 0 or
                saved["bytes"] != len(data) or saved["sha256"] != digest):
            raise ValueError("runtime resource capture manifest mismatch")
        resource_evidence = {
            "available": True, "sha256": digest,
            "resources": saved_resources(data),
            "scope": "Android resource capture; not evidence from the current XNU boot",
        }
    return {
        "abi": 1, "status": "blocked_no_trusted_board_producer",
        "verified": 0, "hardware_execution_allowed": False,
        "hardware_executed": False, "board_handoff_qualified": False,
        "device_contacted": False, "mmio_accessed": False,
        "unknown": list(FACTS), "resource_capture": resource_evidence,
        "source_inventory": inventory(root, edk2, unixnu),
        "source_provenance": "Local snapshot hashes only; no .git commit authentication inferred.",
        "source_gaps": [
            "UFSDxe source and authenticated exit callback trace are unavailable",
            "Linux reference is DTS/DTSI only; ufshcd/ufs-qcom/QMP/GCC implementations are absent",
            "Referenced msm-arm-smmu-8998.dtsi and implementation definitions are absent",
            "Expanded edk2 snapshot lacks Common and simple-init submodule contents",
        ],
        "ufs_exit_callback": "Unknown: fixed UFSDxe is binary-only; no UFS callback source available.",
        "blockers": [
            "No trusted in-boot board producer or independently held live context",
            "No post-EBS power/clock/PHY/link retention and safe-access evidence",
            "No firmware drain/submission exclusion/IRQ and old DMA retirement evidence",
            "No measured MSM8998 DMA stream/context route or retained mapping",
            "No dedicated retained DMA allocation and cache-maintenance owner",
        ],
    }


def write_report(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("report output must be a regular file")
        stream.truncate(0)
        stream.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--edk2", type=Path, default=Path("/private/tmp/ios-party-research/edk2"))
    parser.add_argument("--unixnu", type=Path, default=Path("/private/tmp/ios-party-research/unixnu"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.evidence) != bool(args.context):
        parser.error("--evidence and --context must be provided together")
    try:
        report = (validate_metadata(load_json(args.evidence), load_json(args.context))
                  if args.evidence else audit(args.root, args.edk2, args.unixnu))
    except (ValueError, OSError, KeyError) as error:
        report = {"status": "rejected", "reason": str(error), "verified": 0,
                  "hardware_execution_allowed": False, "board_handoff_qualified": False}
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        write_report(args.output, text)
    print(text, end="")
    return 2 if report["status"] == "rejected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
