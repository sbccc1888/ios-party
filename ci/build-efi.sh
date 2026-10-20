#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
SOURCE_ROOT="${RUNNER_TEMP}/chiron-sources"
python3 ci/fetch_sources.py unixnu mu --root "$SOURCE_ROOT"
EDK2="$SOURCE_ROOT/mu"
export WORKSPACE="$RUNNER_TEMP/chiron-efi-work"
export PACKAGES_PATH="$ROOT/package:$SOURCE_ROOT/unixnu/package:$SOURCE_ROOT/unixnu/drivers/DarwinBoot:$EDK2"
export EDK_TOOLS_PATH="$EDK2/BaseTools"
export PYTHON_COMMAND=python3
export CLANG_BIN=/usr/bin/
export PATH="$EDK_TOOLS_PATH/BinWrappers/PosixLike:$EDK_TOOLS_PATH/Source/C/bin:$PATH"
mkdir -p "$WORKSPACE/Conf" "$ROOT/dist/efi"
for name in target tools_def build_rule; do
    cp "$EDK_TOOLS_PATH/Conf/$name.template" "$WORKSPACE/Conf/$name.txt"
done
make -C "$EDK_TOOLS_PATH/Source/C" -j2
export CONF_PATH="$WORKSPACE/Conf"
build -p ChironCiPkg/ChironCi.dsc -a AARCH64 -b DEBUG -t CLANGPDB -n 2
for name in UniXNU UniXNUDxe DarwinBoot HelloPayload; do
    cp "$WORKSPACE/Build/UniXNU/DEBUG_CLANGPDB/AARCH64/$name.efi" "$ROOT/dist/efi/"
done
python3 ci/inspect_artifacts.py "$ROOT/dist/efi"
cp ci/sources.json dist/efi/
