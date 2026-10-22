#!/usr/bin/env python3
"""Inspect saved Chiron resources/GPT; never opens a device or authorizes hardware."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import tarfile

from summarize_chiron_live import cells, strings, ranges, inspect_gpt

ROOT = Path(__file__).resolve().parents[1]
HOST = "/soc/ufshc@1da4000"
PHY = "/soc/ufsphy@1da7000"


def saved_file(path):
    # Opening /dev/block/... must not become a way to operate hardware through this tool.
    path = Path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("only saved regular files are accepted")
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("only saved regular files are accepted")
        return stream.read()


def runtime_nodes(data):
    nodes = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe saved tree member")
            if member.size > 1 << 20:
                raise ValueError("oversized saved tree property")
            node = "/" + str(path.parent).removeprefix(".").strip("/")
            props = nodes.setdefault(node, {})
            if path.name in props:
                raise ValueError("duplicate saved tree property")
            props[path.name] = archive.extractfile(member).read()
    return nodes


def inspect_resources(nodes):
    if cells(nodes["/"]["qcom,msm-id"]) != [292, 0x20001]:
        raise ValueError("not the measured MSM8998 v2.1")
    parent = nodes["/soc"]
    if cells(parent["#address-cells"]) != [1] or cells(parent["#size-cells"]) != [1]:
        raise ValueError("unreviewed bus cell width")
    if cells(parent["ranges"]) != [0, 0, 0, 0xffffffff]:
        raise ValueError("unreviewed soc address translation")
    host, phy = nodes[HOST], nodes[PHY]
    if strings(host["compatible"]) != ["qcom,ufshc"] or strings(host["status"]) not in (["ok"], ["okay"]):
        raise ValueError("UFS host is absent/disabled")
    if strings(phy["compatible"]) != ["qcom,ufs-phy-qmp-v3"] or strings(phy["status"]) not in (["ok"], ["okay"]):
        raise ValueError("UFS PHY is absent/disabled")
    if cells(host["reg"]) != [0x1da4000, 0x2500] or cells(phy["reg"]) != [0x1da7000, 0xda8]:
        raise ValueError("unexpected host/PHY ranges")
    if cells(host["phys"]) != cells(phy["phandle"]):
        raise ValueError("host PHY phandle mismatch")
    if cells(host["interrupts"]) != [0, 265, 0]:
        raise ValueError("unexpected interrupt specifier")
    clocks = strings(host["clock-names"])
    expected = ["core_clk", "bus_aggr_clk", "iface_clk", "core_clk_unipro",
                "core_clk_ice", "ref_clk", "tx_lane0_sync_clk",
                "rx_lane0_sync_clk", "rx_lane1_sync_clk"]
    if clocks != expected or cells(host["lanes-per-direction"]) != [2]:
        raise ValueError("unexpected measured lanes/clocks")
    phandles = {cells(v["phandle"])[0]: (k, v) for k, v in nodes.items() if "phandle" in v}
    resolved_clocks = []
    clock_cells = cells(host["clocks"])
    at = 0
    while at < len(clock_cells):
        target, props = phandles[clock_cells[at]]
        width = cells(props["#clock-cells"])[0]
        if width != 1 or at + 2 > len(clock_cells):
            raise ValueError("unreviewed clock specifier")
        resolved_clocks.append({"provider": target, "specifier": clock_cells[at + 1]})
        at += 2
    if len(resolved_clocks) != len(clocks):
        raise ValueError("clock count mismatch")
    supplies = {}
    for key in ("vdd-hba-supply", "vcc-supply", "vccq-supply", "vccq2-supply"):
        target, _ = phandles[cells(host[key])[0]]
        supplies[key] = target
    gcc = nodes["/soc/qcom,gcc@100000"]
    gdsc = nodes[supplies["vdd-hba-supply"]]
    return {
        "host": ranges(host["reg"], 1, 1), "phy": ranges(phy["reg"], 1, 1),
        "interrupt_specifier": cells(host["interrupts"]), "spi_intid": 297,
        "lanes": 2, "clock_names": clocks, "clocks": resolved_clocks, "supplies": supplies,
        "reset_specifier": cells(host["resets"]),
        "gcc": {"compatible": strings(gcc["compatible"]), "ranges": ranges(gcc["reg"], 1, 1)},
        "gdsc": {"compatible": strings(gdsc["compatible"]), "ranges": ranges(gdsc["reg"], 1, 1)},
        "ufs_iommus_present": "iommus" in host,
        "dma_mapping_qualified": False, "power_clocks_qualified_after_ebs": False,
    }


def inspect_capture(root=ROOT, compare_gpt=None):
    profile = json.loads(saved_file(root / "research/chiron-live-profile.json"))
    capture = root / "research/artifacts/chiron-live-20261020/runtime-tree.tar"
    gpt_path = root / "research/artifacts/chiron-live-details-20261020/sda-primary-gpt.bin"
    raw_gpt = saved_file(gpt_path)
    relative = str(gpt_path.relative_to(root))
    if hashlib.sha256(raw_gpt).hexdigest() != profile["evidence"][relative]:
        raise ValueError("saved GPT hash differs from measured profile")
    geometry = inspect_gpt(raw_gpt, profile["sda"]["logical_block_bytes"])
    if any(geometry[k] != profile["sda"][k] for k in geometry):
        raise ValueError("saved GPT differs from measured profile")
    lun_path = root / "research/artifacts/chiron-live-details-20261020/sda-lun.txt"
    lun = saved_file(lun_path).decode().splitlines()
    if lun != [profile["sda"]["scsi_path"], "4096", "4096", "236109824"]:
        raise ValueError("saved sysfs LUN geometry mismatch")
    raw_tree = saved_file(capture)
    manifest = json.loads(saved_file(capture.parent / "manifest.json"))
    entry = manifest["files"]["runtime-tree.tar"]
    if entry["returncode"] or entry["bytes"] != len(raw_tree) or entry["sha256"] != hashlib.sha256(raw_tree).hexdigest():
        raise ValueError("saved runtime tree differs from capture manifest")
    report = {
        "resources": inspect_resources(runtime_nodes(raw_tree)),
        "capture_sha256": hashlib.sha256(raw_tree).hexdigest(),
        "gpt_sha256": hashlib.sha256(raw_gpt).hexdigest(),
        "gpt": geometry, "known_read_lun": 0, "known_read_lbas": [0, 5],
        "expected_last_lba": 29513727, "expected_block_bytes": 4096,
        "expected_header_crc": struct.unpack_from("<I", raw_gpt, 4096 + 16)[0],
        "expected_entries_crc": struct.unpack_from("<I", raw_gpt, 4096 + 88)[0],
        "hardware_execution_allowed": False, "hardware_executed": False,
        "blockers": ["Post-ExitBootServices power/clock/PHY/link state is unverified",
                     "UFS DMA stream/context and retained DMA mapping are unverified",
                     "Exclusive controller ownership and quiescence are unverified"],
    }
    if compare_gpt is not None:
        candidate = saved_file(compare_gpt)
        if len(candidate) != len(raw_gpt) or candidate != raw_gpt:
            raise ValueError("probe GPT bytes differ from saved six-block capture")
        report["probe_gpt_exact_match"] = True
        report["comparison_scope"] = "Saved files only; does not authenticate origin or prove hardware execution"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--compare-gpt", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_capture(args.root, args.compare_gpt)
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
