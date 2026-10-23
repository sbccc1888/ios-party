#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
test "$(uname -s)" = Linux
WORK=$1
EVIDENCE=$2
export THEOS="$WORK/theos"
export JOBS=2
collect_logs() {
  local status=$?
  for log in "$WORK/kernel/xnu-deps/"*.log; do
    test -f "$log" || continue
    cp "$log" "$EVIDENCE/dependency-$(basename "$log")"
    if [ "$status" -ne 0 ]; then
      printf '\n--- %s ---\n' "$log"
      tail -n 100 "$log"
    fi
  done
}
trap collect_logs EXIT
cd "$WORK/kernel"
bash xnu/apply.sh
bash xnu-deps/setup.sh
bash xnuloader/build.sh
K=xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837
test ! -e "$K"
BUILD_START=$(date +%s)
BUILD_RC=0
if [ -n "${HACKIOS_REUSE_KERNEL_DIR:-}" ]; then
  python3 "$GITHUB_WORKSPACE/ci/hackios/cloud.py" reuse-kernel --work "$WORK" --evidence "$EVIDENCE"
  BUILD_RC=$(cat "$HACKIOS_REUSE_KERNEL_DIR/kernel-make-exit.txt")
else
  bash xnu-build.sh -j2 -k || BUILD_RC=$?
  test "$(stat -c %Y "$K")" -ge "$BUILD_START"
fi
# Upstream's export checks can fail after linking. Require a fresh link or the
# hash-pinned source build above, and reject unresolved symbols in either case.
test -s "$K"
test -s "$K.unstripped"
source xnu-env.sh
nm -u "$K.unstripped" > "$EVIDENCE/kernel-undefined.txt"
test ! -s "$EVIDENCE/kernel-undefined.txt"
nm -n "$K.unstripped" > "$EVIDENCE/kernel-symbols.txt"
otool -l "$K" > "$EVIDENCE/kernel-load-commands.txt"
cp "$K" "$EVIDENCE/kernel.development.hackios"
cp "$K.unstripped" "$EVIDENCE/kernel.development.hackios.unstripped"
cp xnuloader/loader.efi "$EVIDENCE/loader.efi"
printf '%s\n' "$BUILD_RC" > "$EVIDENCE/kernel-make-exit.txt"
printf 'Verified source kernel ready; original make exit=%s\n' "$BUILD_RC"
