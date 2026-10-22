#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TREE=$1
mkdir -p "$TREE/unixnu/Build/Out/AARCH64-DEBUG" "$TREE/build/DarwinBoot/DEBUG_CLANGPDB/AARCH64"
cp "$ROOT/dist/efi/UniXNU.efi" "$TREE/unixnu/Build/Out/AARCH64-DEBUG/"
cp "$ROOT/dist/efi/DarwinBoot.efi" "$TREE/build/DarwinBoot/DEBUG_CLANGPDB/AARCH64/"
TEST_EXIT=0
(
    cd "$TREE"
    python3 scripts/test-corecrypto14-post.py &&
    python3 scripts/probe-darwinboot-xnu.py \
        --output build/chiron-qemu-init --timeout 180 \
        --armvirt --armcpu --nvram-proxy --require-crypto14 \
        --native-user --trust-cache build/native-user-fixture/trustcache.bundle \
        --native-user-image build/native-user-fixture/launchd.macho \
        --native-user-ramdisk build/native-user-fixture/root.img \
        --check-commpage --qemu-ramfb --capture-qemu-screen
) || TEST_EXIT=$?
for name in gdb-stop.json gdb-failure.json gdb-uart.log gdb-stderr.log chiron-trace.bin screen.png; do
    if [ -f "$TREE/build/chiron-qemu-init/$name" ]; then
        cp "$TREE/build/chiron-qemu-init/$name" "$ROOT/dist/ram-init/qemu-$name"
    fi
done
if [ "${CHIRON_KERNEL_MODE:-}" = ram-trace ]; then
    for run in darwinboot-crypto14 darwinboot-bad-hmac chiron-qemu-init; do
        capture="$ROOT/dist/ram-init/source-trace-captures/$run"
        mkdir -p "$capture"
        for name in gdb-stop.json gdb-failure.json gdb-uart.log gdb-stderr.log chiron-trace.bin screen.png; do
            if [ -f "$TREE/build/$run/$name" ]; then
                cp "$TREE/build/$run/$name" "$capture/"
            fi
        done
    done
fi
if [ -f "$TREE/build/corecrypto-2023/post-test.json" ]; then
    cp "$TREE/build/corecrypto-2023/post-test.json" "$ROOT/dist/ram-init/"
fi
if [ "$TEST_EXIT" -ne 0 ]; then
    exit "$TEST_EXIT"
fi
python3 "$ROOT/ci/validate_runtime.py" --tree "$TREE" --output "$ROOT/dist/ram-init/runtime-test.json" --require-selftest
cp "$TREE/build/chiron-qemu-init/gdb-stop.json" "$ROOT/dist/ram-init/qemu-observation.json"
cp "$TREE/build/chiron-qemu-init/gdb-uart.log" "$ROOT/dist/ram-init/qemu-uart.log"
cp "$TREE/build/corecrypto-2023/post-test.json" "$ROOT/dist/ram-init/"
if [ "${CHIRON_KERNEL_MODE:-}" = ram-trace ]; then
    cp "$TREE/build/chiron-qemu-init/chiron-trace.bin" "$ROOT/dist/ram-init/"
    python3 "$ROOT/ci/test_source_trace.py" --tree "$TREE" --output "$ROOT/dist/ram-init/source-trace-tests"
fi
