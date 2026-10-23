#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SOURCES="$RUNNER_TEMP/chiron-memory-sources"
OUTPUT="$ROOT/dist/chiron-memory-firmware"
mkdir -p "$OUTPUT"
python3 "$ROOT/ci/fetch_sources.py" uefi --root "$SOURCES"
UEFI="$SOURCES/uefi"
python3 "$ROOT/ci/integrate_firmware.py" --source "$UEFI" --efi "$ROOT/dist/efi" \
  --report "$OUTPUT/integration-report.json"
python3 "$ROOT/ci/hackios/apply_memory_policy.py" --source "$UEFI" \
  --output "$OUTPUT/memory-policy.json"
(
  export PATH="/usr/bin:/bin:$PATH"
  cd "$UEFI"
  bash build.sh --device chiron --skip-rootfs-gen
) 2>&1 | tee "$OUTPUT/build.log"
cp "$UEFI/workspace/Build/chiron/RELEASE_GCC5/FV/MSM8998PKG_UEFI.fd" "$OUTPUT/chiron-unixnu.fd"
python3 -B -c 'import hashlib,json,os,pathlib,sys;p=pathlib.Path(sys.argv[1]);f=p/"chiron-unixnu.fd";m=json.loads((p/"memory-policy.json").read_text());m.update(firmware_sha256=hashlib.sha256(f.read_bytes()).hexdigest(),firmware_bytes=f.stat().st_size,project_sha=os.environ["GITHUB_SHA"],run_id=os.environ["GITHUB_RUN_ID"]);(p/"memory-policy.json").write_text(json.dumps(m,indent=2)+"\n")' "$OUTPUT"
cp "$ROOT/ci/sources.json" "$OUTPUT/sources.json"
