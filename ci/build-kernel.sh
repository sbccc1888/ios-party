#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
SOURCE_ROOT="$RUNNER_TEMP/chiron-sources"
OUT="$RUNNER_TEMP/chiron-kernel"
python3 ci/fetch_sources.py unixnu --root "$SOURCE_ROOT"
UPSTREAM="$SOURCE_ROOT/unixnu"
cp "$ROOT"/ci/patches/*.patch "$UPSTREAM/patches/xnu/"
python3 "$UPSTREAM/unixnu" defconfig chiron_defconfig -D "$ROOT" --out "$OUT"
python3 "$UPSTREAM/unixnu" fetch xnu libplatform libdispatch AvailabilityVersions -D "$ROOT" --out "$OUT"
python3 "$UPSTREAM/unixnu" tree -D "$ROOT" --out "$OUT"
TREE="$OUT/chiron/tree"
python3 "$ROOT/ci/cache_public_headers.py" --upstream "$UPSTREAM" --cache "$TREE/.cache/sources"
unset GH_TOKEN
mkdir -p "$ROOT/dist/kernel"
export JOBS=3
# Source compilation only: deliberately do not call the IPSW/prelink step.
BUILD_EXIT=0
(
    cd "$TREE"
    bash scripts/build-xnu-4k-baseline.sh
) > "$ROOT/dist/kernel/build.log" 2>&1 || BUILD_EXIT=$?
if [ "$BUILD_EXIT" -ne 0 ]; then
    tail -n 100 "$ROOT/dist/kernel/build.log"
    exit "$BUILD_EXIT"
fi
KERNEL="$TREE/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
cp "$KERNEL" "$ROOT/dist/kernel/kernel.source.unstripped"
python3 "$ROOT/ci/build_driver_archives.py" --tree "$TREE" --output "$ROOT/dist/kernel/drivers"
python3 "$ROOT/scripts/make-chiron-adt.py" --output "$ROOT/dist/kernel/kernel.source.adt"
cp ci/sources.json board.toml configs/chiron_defconfig "$ROOT/dist/kernel/"
python3 ci/inspect_artifacts.py "$ROOT/dist/kernel"
if [ "${CHIRON_KERNEL_MODE:-bootstrap}" = ram-init ]; then
    RUNTIME_EXIT=0
    bash "$ROOT/ci/build-runtime.sh" "$TREE" > "$ROOT/dist/kernel/runtime-build.log" 2>&1 || RUNTIME_EXIT=$?
    if [ "$RUNTIME_EXIT" -ne 0 ]; then
        tail -n 100 "$ROOT/dist/kernel/runtime-build.log"
        exit "$RUNTIME_EXIT"
    fi
    bash "$ROOT/ci/test-runtime-qemu.sh" "$TREE" > "$ROOT/dist/ram-init/qemu-test.log" 2>&1 || RUNTIME_EXIT=$?
    if [ "$RUNTIME_EXIT" -ne 0 ]; then
        tail -n 100 "$ROOT/dist/ram-init/qemu-test.log"
        exit "$RUNTIME_EXIT"
    fi
fi
