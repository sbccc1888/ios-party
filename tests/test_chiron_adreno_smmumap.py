#!/usr/bin/env python3
"""Compiles and runs the A540 context-bank address arithmetic against arm-smmu.c."""
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DRIVER = ROOT / "drivers" / "ChironAdrenoA540"
SOURCES = [DRIVER / "tests" / "SmmuMap.cpp", DRIVER / "ChironAdrenoA540SmmuMap.cpp"]


def main() -> int:
    compiler = shutil.which("g++") or shutil.which("c++")
    if not compiler:
        print("no C++ compiler available", file=sys.stderr)
        return 1
    missing = [str(s) for s in SOURCES if not s.is_file()]
    if missing:
        print("missing sources: " + ", ".join(missing), file=sys.stderr)
        return 1
    binary = ROOT / "out" / "chiron-adreno-smmumap-test"
    binary.parent.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
         "-o", str(binary), *[str(s) for s in SOURCES]],
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
