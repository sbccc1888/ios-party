#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
test "$(uname -s)" = Linux
WORK=$1
EVIDENCE=$2
export THEOS="$WORK/theos"
export JOBS=2
cd "$WORK/kernel"
bash xnu/apply.sh
bash xnu-deps/setup.sh
bash xnuloader/build.sh
K=xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837
test ! -e "$K"
BUILD_START=$(date +%s)
BUILD_RC=0
bash xnu-build.sh -j2 -k || BUILD_RC=$?
# Upstream's export-list checks can fail after linking. Require fresh executables,
# reject unresolved symbols, and retain the original make result and complete log.
test -s "$K"
test -s "$K.unstripped"
test "$(stat -c %Y "$K")" -ge "$BUILD_START"
source xnu-env.sh
nm -u "$K.unstripped" > "$EVIDENCE/kernel-undefined.txt"
test ! -s "$EVIDENCE/kernel-undefined.txt"
nm -n "$K.unstripped" > "$EVIDENCE/kernel-symbols.txt"
otool -l "$K" > "$EVIDENCE/kernel-load-commands.txt"
cp "$K" "$EVIDENCE/kernel.development.hackios"
cp "$K.unstripped" "$EVIDENCE/kernel.development.hackios.unstripped"
cp xnuloader/loader.efi "$EVIDENCE/loader.efi"
printf '%s\n' "$BUILD_RC" > "$EVIDENCE/kernel-make-exit.txt"
printf 'Fresh source kernel linked; make exit=%s\n' "$BUILD_RC"
