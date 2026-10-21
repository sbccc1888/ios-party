#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TREE=$1
mkdir -p "$TREE/unixnu/Build/Out/AARCH64-DEBUG" "$TREE/build/DarwinBoot/DEBUG_CLANGPDB/AARCH64"
cp "$ROOT/dist/efi/UniXNU.efi" "$TREE/unixnu/Build/Out/AARCH64-DEBUG/"
cp "$ROOT/dist/efi/DarwinBoot.efi" "$TREE/build/DarwinBoot/DEBUG_CLANGPDB/AARCH64/"
(
    cd "$TREE"
    python3 scripts/test-corecrypto14-post.py
    python3 scripts/probe-darwinboot-xnu.py \
        --output build/chiron-qemu-init --timeout 180 \
        --armvirt --armcpu --nvram-proxy --require-crypto14 \
        --native-user --trust-cache build/native-user-fixture/trustcache.bundle \
        --check-commpage --qemu-ramfb
)
python3 "$ROOT/ci/validate_runtime.py" --tree "$TREE" --output "$ROOT/dist/ram-init/runtime-test.json"
cp "$TREE/build/chiron-qemu-init/gdb-stop.json" "$ROOT/dist/ram-init/qemu-observation.json"
cp "$TREE/build/chiron-qemu-init/gdb-uart.log" "$ROOT/dist/ram-init/qemu-uart.log"
cp "$TREE/build/corecrypto-2023/post-test.json" "$ROOT/dist/ram-init/"
