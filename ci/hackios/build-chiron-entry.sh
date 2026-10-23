#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python3 "$ROOT/ci/fetch_sources.py" mu --root "$RUNNER_TEMP/chiron-entry-sources"
EDK2="$RUNNER_TEMP/chiron-entry-sources/mu"
export WORKSPACE="$RUNNER_TEMP/chiron-entry-work"
export PACKAGES_PATH="$ROOT/package:$EDK2"
export EDK_TOOLS_PATH="$EDK2/BaseTools"
export PYTHON_COMMAND=python3
export CLANG_BIN="$(llvm-config --bindir)/"
export PATH="$EDK_TOOLS_PATH/BinWrappers/PosixLike:$EDK_TOOLS_PATH/Source/C/bin:$PATH"
mkdir -p "$WORKSPACE/Conf" "$ROOT/dist/chiron-entry"
for name in target tools_def build_rule; do
  cp "$EDK_TOOLS_PATH/Conf/$name.template" "$WORKSPACE/Conf/$name.txt"
done
make -C "$EDK_TOOLS_PATH/Source/C" -j2
export CONF_PATH="$WORKSPACE/Conf"
build -p ChironCiPkg/ChironHackios.dsc -a AARCH64 -b DEBUG -t CLANGPDB -n 2
cp "$WORKSPACE/Build/ChironHackios/DEBUG_CLANGPDB/AARCH64/ChironHackios.efi" "$ROOT/dist/chiron-entry/"
