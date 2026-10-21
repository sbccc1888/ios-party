#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TREE=$1
SOURCE_ROOT="$RUNNER_TEMP/chiron-sources"
python3 "$ROOT/ci/fetch_sources.py" corecrypto libpthread --root "$SOURCE_ROOT"
ln -s "$SOURCE_ROOT/corecrypto" "$TREE/refer/corecrypto-2023"
ln -s "$SOURCE_ROOT/libpthread" "$TREE/refer/libpthread"
python3 "$ROOT/ci/prepare_runtime.py" --tree "$TREE"
(
    cd "$TREE"
    python3 scripts/build-corecrypto-14.py
    python3 scripts/build-corecrypto-14.py --namespace
    python3 scripts/build-libpthread-kernel.py
    python3 scripts/build-mockfs-fixture.py
)
mkdir -p "$TREE/build/armcpu" "$TREE/build/armdt-platform"
cp "$ROOT/dist/kernel/drivers/libArmCpu.a" "$TREE/build/armcpu/"
cp "$ROOT/dist/kernel/drivers/libArmDTPlatform.a" "$TREE/build/armdt-platform/"
python3 "$ROOT/ci/run_runtime_kernel.py" --tree "$TREE"
cp "$ROOT/scripts/chiron-init.S" "$TREE/scripts/qemu-entry/native-user.S"
(
    cd "$TREE"
    python3 scripts/build-native-user-fixture.py
)
RUNTIME="$ROOT/dist/ram-init"
mkdir -p "$RUNTIME"
KERNEL="$TREE/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
cp "$KERNEL" "$RUNTIME/kernel.macho"
cp "$KERNEL.crypto-hmac.json" "$RUNTIME/"
cp "$TREE/build/native-user-fixture/launchd.macho" "$RUNTIME/"
cp "$TREE/build/native-user-fixture/trustcache.bundle" "$RUNTIME/"
cp "$TREE/build/native-user-fixture/addresses.json" "$RUNTIME/"
cp "$TREE/build/corecrypto-2023/build-report-namespaced.json" "$RUNTIME/crypto-build-report.json"
python3 "$ROOT/scripts/make-chiron-adt.py" --ram-root --output "$RUNTIME/kernel.macho.adt"
