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
python3 "$ROOT/ci/build_ram_selftest.py" --tree "$TREE"
RUNTIME="$ROOT/dist/ram-init"
mkdir -p "$RUNTIME"
KERNEL="$TREE/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
cp "$KERNEL" "$RUNTIME/kernel.macho"
cp "$KERNEL.crypto-hmac.json" "$RUNTIME/"
cp "$TREE/build/native-user-fixture/launchd.macho" "$RUNTIME/"
cp "$TREE/build/native-user-fixture/trustcache.bundle" "$RUNTIME/"
cp "$TREE/build/native-user-fixture/addresses.json" "$RUNTIME/"
for file in root.img child.macho child-addresses.json selftest-build.json; do
    cp "$TREE/build/native-user-fixture/$file" "$RUNTIME/"
done
cp "$TREE/build/corecrypto-2023/build-report-namespaced.json" "$RUNTIME/crypto-build-report.json"
python3 "$ROOT/scripts/make-chiron-adt.py" --ram-root --output "$RUNTIME/kernel.macho.adt"
if [ "${CHIRON_KERNEL_MODE:-}" = ram-trace ]; then
    cp "$TREE/build/boot-progress/source-trace-build.json" "$RUNTIME/"
    cp "$TREE/build/chiron-source-overlay.json" "$RUNTIME/"
    cp "$TREE/build/chiron-runtime-environment.json" "$RUNTIME/"
    cp "$TREE/build/chiron-recipe-overlays.json" "$RUNTIME/"
    xcrun nm -n "$KERNEL" > "$RUNTIME/kernel-symbols.txt"
    xcrun dsymutil "$KERNEL" -o "$RUNTIME/kernel.dSYM"
    mkdir -p "$RUNTIME/observer-source"
    cp -R "$TREE/diagnostics" "$RUNTIME/observer-source/"
    for file in scripts/chiron-init.S scripts/chiron-selftest-main.S scripts/chiron-selftest-child.S ci/build_ram_selftest.py; do
        mkdir -p "$RUNTIME/observer-source/$(dirname "$file")"
        cp "$ROOT/$file" "$RUNTIME/observer-source/$file"
    done
    for file in osfmk/arm64/start.s osfmk/arm/arm_init.c osfmk/arm64/arm_vm_init.c osfmk/arm64/sleh.c osfmk/kern/debug.c osfmk/kern/startup.c osfmk/console/serial_console.c pexpert/arm/pe_init.c osfmk/console/video_console.c osfmk/arm/io_map.c; do
        mkdir -p "$RUNTIME/observer-source/$(dirname "$file")"
        cp "$TREE/.cache/xnu-work/$file" "$RUNTIME/observer-source/$file"
    done
fi
