#!/usr/bin/env python3
"""Fetch only explicitly selected pinned repositories on the remote runner."""

import argparse
import json
import subprocess
from pathlib import Path


def git(path, *arguments):
    return subprocess.check_output(["git", "-C", str(path), *arguments], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="+", choices=("unixnu", "mu", "uefi", "corecrypto", "libpthread"))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    sources = json.loads((Path(__file__).with_name("sources.json")).read_text())
    args.root.mkdir(parents=True, exist_ok=True)
    for name in args.names:
        spec = sources[name]
        destination = args.root / name
        if not destination.exists():
            destination.mkdir()
            git(destination, "init", "-q")
            git(destination, "remote", "add", "origin", "https://github.com/" + spec["repository"] + ".git")
            git(destination, "fetch", "--depth=1", "origin", spec["commit"])
            git(destination, "checkout", "--detach", "FETCH_HEAD")
        if git(destination, "rev-parse", "HEAD") != spec["commit"]:
            raise SystemExit(f"unexpected source revision: {destination}")
        if name == "mu":
            paths = [
                "MdePkg/Library/BaseFdtLib/libfdt", "MdePkg/Library/MipiSysTLib/mipisyst",
                "BaseTools/Source/C/BrotliCompress/brotli",
            ]
            git(destination, "submodule", "update", "--init", "--depth=1", *paths)
        elif name == "uefi":
            git(destination, "submodule", "update", "--init", "--recursive", "--depth=1")
        print(f"{name}: {spec['commit']}", flush=True)


if __name__ == "__main__":
    main()
