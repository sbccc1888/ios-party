#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
SOURCE_ROOT="$RUNNER_TEMP/chiron-sources"
python3 ci/fetch_sources.py uefi --root "$SOURCE_ROOT"
UEFI="$SOURCE_ROOT/uefi"
mkdir -p dist/firmware
python3 ci/integrate_firmware.py --source "$UEFI" --efi dist/efi \
    --report dist/firmware/integration-report.json
# The original platform uses distro Python packages and the old EDK II tools.
(
    export PATH="/usr/bin:/bin:$PATH"
    cd "$UEFI"
    bash build.sh --device chiron --skip-rootfs-gen
) 2>&1 | tee dist/firmware/build.log
cp "$UEFI/workspace/Build/chiron/RELEASE_GCC5/FV/MSM8998PKG_UEFI.fd" \
    dist/firmware/chiron-unixnu.fd
python3 ci/pack_firmware.py --source "$UEFI" --output dist/firmware
python3 tools/inspect_chiron_boot.py dist/firmware/chiron-uefi-experimental.img \
    --output dist/firmware/container-inspection
cp ci/sources.json dist/firmware/
