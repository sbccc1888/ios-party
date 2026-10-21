#!/usr/bin/env python3
"""Cache the three public GitHub listings before running upstream header restore."""

import argparse
import json
import subprocess
import tomllib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    feeds = tomllib.loads((args.upstream / "feeds.toml").read_text())
    source = next(item for item in feeds["source"] if item["name"] == "xnu")
    tag = source["driverkit_tag"]
    trust = source["trustcache_tag"]
    stem = "xnu-driverkit-" + tag.removeprefix("xnu-")
    items = [
        ("iokit/DriverKit", tag, stem + ".json"),
        ("iokit/DriverKit/crypto", tag, "xnu-driverkit-crypto-" + tag.removeprefix("xnu-") + ".json"),
        ("EXTERNAL_HEADERS/TrustCache", trust, "xnu-trustcache-" + trust.removeprefix("xnu-") + ".json"),
    ]
    args.cache.mkdir(parents=True, exist_ok=True)
    for path, ref, filename in items:
        endpoint = "repos/apple-oss-distributions/xnu/contents/" + path + "?ref=" + ref
        data = subprocess.check_output(["gh", "api", endpoint])
        entries = json.loads(data)
        if not isinstance(entries, list) or not entries:
            raise ValueError("unexpected public source directory")
        for entry in entries:
            if entry["type"] == "file" and (
                not isinstance(entry.get("sha"), str) or len(entry["sha"]) != 40
                or not entry.get("download_url", "").startswith("https://raw.githubusercontent.com/apple-oss-distributions/xnu/")
            ):
                raise ValueError("unexpected public header provenance")
        (args.cache / filename).write_bytes(data)
        print(f"Cached {filename}: {len(entries)} entries")


if __name__ == "__main__":
    main()
