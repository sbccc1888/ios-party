#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
SOURCE_ROOT="$RUNNER_TEMP/chiron-sources"
OUT="$RUNNER_TEMP/chiron-kernel"
python3 ci/fetch_sources.py unixnu --root "$SOURCE_ROOT"
UPSTREAM="$SOURCE_ROOT/unixnu"
python3 "$UPSTREAM/unixnu" defconfig chiron_defconfig -D "$ROOT" --out "$OUT"
python3 "$UPSTREAM/unixnu" fetch xnu libplatform libdispatch AvailabilityVersions -D "$ROOT" --out "$OUT"
python3 "$UPSTREAM/unixnu" tree -D "$ROOT" --out "$OUT"
TREE="$OUT/chiron/tree"
mkdir -p "$ROOT/dist/kernel"
export JOBS=3
# Source compilation only: deliberately do not call the IPSW/prelink step.
(
    cd "$TREE"
    bash scripts/build-xnu-4k-baseline.sh
) 2>&1 | tee "$ROOT/dist/kernel/build.log"
KERNEL="$TREE/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
cp "$KERNEL" "$ROOT/dist/kernel/kernel.source.unstripped"
python3 "$ROOT/ci/build_driver_archives.py" --tree "$TREE" --output "$ROOT/dist/kernel/drivers"
python3 "$ROOT/scripts/make-chiron-adt.py" --output "$ROOT/dist/kernel/kernel.source.adt"
cp ci/sources.json board.toml configs/chiron_defconfig "$ROOT/dist/kernel/"
python3 ci/inspect_artifacts.py "$ROOT/dist/kernel"
