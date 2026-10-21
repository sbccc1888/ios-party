#!/usr/bin/env python3
"""Link the configured native runtime with genuine source providers."""

import argparse
import json
import os
import subprocess
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # The assembled tree excludes the driver package. Read defconfig via upstream module.
    import sys
    sys.path.insert(0, str(Path(os.environ["RUNNER_TEMP"]) / "chiron-sources/unixnu/scripts"))
    from unixnu.config import parse_config
    config = parse_config((root / "configs/chiron_raminit_defconfig").read_text())
    env = dict(os.environ)
    for key, value in config.items():
        if not key.startswith("XNU_"):
            continue
        env[key] = "1" if value is True else "0" if value is False else str(value)
    env["JOBS"] = "3"
    (args.tree / "build/chiron-runtime-environment.json").write_text(json.dumps({
        key: value for key, value in env.items() if key.startswith("XNU_")
    }, indent=2) + "\n")
    # The late board guard changes an upstream patch's reverse-check context.
    # Remove it before the upstream idempotence loop, which reapplies it last.
    guard = args.tree / "patches/xnu/zzzz-chiron-optional-display.patch"
    source = args.tree / ".cache/xnu-work"
    subprocess.run(["git", "-C", str(source), "apply", "--reverse", "--check", str(guard)], check=True)
    subprocess.run(["git", "-C", str(source), "apply", "--reverse", str(guard)], check=True)
    # Global provider flags affect many translation units; keep the bootstrap
    # objects as evidence and compile the runtime into a fresh object tree.
    objects = args.tree / "build/xnu/ios-obj"
    backup = args.tree / "build/xnu/bootstrap-obj-before-runtime"
    objects.rename(backup)
    subprocess.run(["bash", "scripts/build-xnu-4k-baseline.sh"], cwd=args.tree, env=env, check=True)
    shutil.rmtree(backup)


if __name__ == "__main__":
    main()
