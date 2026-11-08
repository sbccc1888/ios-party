#!/usr/bin/env python3
"""Compiles and runs the A540 SMMU parameter checks against the device tree values."""
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "drivers" / "ChironAdrenoA540" / "tests" / "SmmuParams.cpp"


def main() -> int:
    compiler = shutil.which("g++") or shutil.which("c++")
    if not compiler:
        print("no C++ compiler available", file=sys.stderr)
        return 1
    if not SRC.is_file():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1
    binary = ROOT / "out" / "chiron-adreno-smmuparams-test"
    binary.parent.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
         "-o", str(binary), str(SRC)],
        capture_output=True, text=True)
    if build.returncode:
        print(build.stdout + build.stderr, file=sys.stderr)
        return build.returncode
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
