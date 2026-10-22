#!/usr/bin/env python3
"""Adapt pinned upstream recipes to the current bootstrap's recorded paths."""

import argparse
import json
import shutil
from pathlib import Path


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"upstream recipe changed: {path}: {old[:80]}")
    path.write_text(text.replace(old, new, 1))


def prepare_trace_recipes(tree, root):
    scripts = tree / "scripts"
    recipe = scripts / "build-xnu-4k-baseline.sh"
    shutil.copytree(root / "diagnostics", tree / "diagnostics", dirs_exist_ok=True)
    shutil.copyfile(root / "ci/apply_chiron_trace.py", scripts / "apply-chiron-trace.py")
    shutil.copyfile(root / "ci/build_chiron_trace.py", scripts / "build-chiron-trace.py")
    replace_once(recipe, "python3 scripts/prepare-xnu-source.py\n",
                 'python3 scripts/prepare-xnu-source.py\n'
                 'if [ "${XNU_CHIRON_TRACE:-0}" = 1 ]; then\n'
                 '  python3 scripts/apply-chiron-trace.py --source .cache/xnu-work\nfi\n')
    old = (
        '  if [ "${XNU_NATIVE_UFS:-0}" != 1 ] || [ "${XNU_OWNER_ROOT_AUDIT:-0}" != 1 ] || \\\n'
        '     [ "${XNU_NATIVE_VIRTIO_BLOCK:-0}" != 1 ] || [ "${XNU_SOURCE_CORECRYPTO14:-0}" != 1 ]; then\n'
        "    echo 'Boot progress requires the audited UFS/owner-root, Virtio and source-corecrypto build' >&2\n"
        "    exit 1\n  fi\n"
    )
    new = (
        '  if [ "${XNU_CHIRON_TRACE:-0}" = 1 ]; then\n'
        '    test "${XNU_SOURCE_CORECRYPTO14:-0}" = 1\n'
        '    test "${XNU_ARM_DT_PLATFORM:-0}" = 1\n'
        '    test "${XNU_ARMCPU:-0}" = 1\n'
        '    test "${XNU_SOURCE_MOCKFS:-0}" = 1\n'
        '  elif [ "${XNU_NATIVE_UFS:-0}" != 1 ] || [ "${XNU_OWNER_ROOT_AUDIT:-0}" != 1 ] || \\\n'
        '       [ "${XNU_NATIVE_VIRTIO_BLOCK:-0}" != 1 ] || [ "${XNU_SOURCE_CORECRYPTO14:-0}" != 1 ]; then\n'
        "    echo 'Boot progress requires a qualified source observer or storage closure' >&2\n"
        "    exit 1\n  fi\n"
    )
    replace_once(recipe, old, new)
    replace_once(recipe, "  python3 scripts/build-boot-progress.py\n",
                 '  if [ "${XNU_CHIRON_TRACE:-0}" = 1 ]; then\n'
                 '    python3 scripts/build-chiron-trace.py\n'
                 '  else\n    python3 scripts/build-boot-progress.py\n  fi\n')
    probe = scripts / "probe-darwinboot-xnu.py"
    anchor = "subprocess.run(gdb_command, check=True)\n"
    observer = '''
trace_requests = []
import re as _trace_re
trace_match = _trace_re.search(r'^([0-9a-fA-F]+)\\s+\\S\\s+_chiron_trace_log$', symbols, _trace_re.M)
if trace_match:
    trace_address = int(trace_match[1], 16)
    timeout_option = gdb_command.index('--timeout') + 1
    gdb_command[timeout_option] = str(max(int(gdb_command[timeout_option]), 600))
    for trace_offset in range(0, 65536, 1024):
        request = f'{trace_address + trace_offset:x}:400'
        trace_requests.append(request)
        gdb_command.extend(['--read-memory', request])
'''
    replace_once(probe, anchor, observer + anchor)
    anchor = "report = json.loads((output / 'gdb-stop.json').read_text())\n"
    replace_once(probe, anchor, anchor +
                 "if trace_requests:\n"
                 "    trace_data = b''.join(bytes.fromhex(report['memory'][r]) for r in trace_requests)\n"
                 "    (output / 'chiron-trace.bin').write_bytes(trace_data)\n"
                 "    report['chiron_trace_file'] = 'chiron-trace.bin'\n")
    replace_once(probe, "for fallback_name in ('_panic', '_panic_with_options'):\n",
                 "for fallback_name in (() if args.break_symbol == '_chiron_trace_fault_checkpoint'\n"
                 "                      else ('_panic', '_panic_with_options')):\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path)
    args = parser.parse_args()
    scripts = args.tree / "scripts"
    replace_once(scripts / "build-xnu-4k-baseline.sh",
                 'EXTRA_CFLAGS="$EXTRA_CFLAGS -DXNU_BOOT_PROGRESS=$BOOT_PROGRESS"',
                 'EXTRA_CFLAGS="$EXTRA_CFLAGS -DXNU_BOOT_PROGRESS=$BOOT_PROGRESS '
                 '-DXNU_DIAGNOSTIC_EMPTY_ENTITLEMENTS=${XNU_DIAGNOSTIC_EMPTY_ENTITLEMENTS:-0} '
                 '-DXNU_CHIRON_TRACE=${XNU_CHIRON_TRACE:-0}"')
    prepare_trace_recipes(args.tree, Path(__file__).resolve().parents[1])
    replace_once(scripts / "build-corecrypto-14.py",
                 "SOURCE = ROOT / 'refer/corecrypto-2023'",
                 "SOURCE = (ROOT / 'refer/corecrypto-2023').resolve()")
    kernel = "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
    replace_once(scripts / "build-libpthread-kernel.py",
                 "build/xnu/checkpoints/native-4k-user-abi/kernel.macho", kernel)
    for filename in ("build-corecrypto-14.py", "build-libpthread-kernel.py"):
        replace_once(scripts / filename, "'-target', 'arm64-apple-ios17.0',",
                     "'-target', 'arm64-apple-ios17.0', '-march=armv8-a', '-mno-outline-atomics',")
    probe = scripts / "probe-darwinboot-xnu.py"
    replace_once(probe,
                 "user_inspections = [('_results', 32), ('_user_page_size', 8), ('_old_length', 8)]",
                 "user_inspections = [('_results', 32), ('_user_page_size', 8), ('_old_length', 8), "
                 "('_chiron_heartbeats', 8), ('_chiron_init_status', 8)]\n"
                 "    if '_chiron_selftest_results' in user_addresses:\n"
                 "        user_inspections.append(('_chiron_selftest_results', 64))\n"
                 "    if '_chiron_selftest_diagnostics' in user_addresses:\n"
                 "        user_inspections.append(('_chiron_selftest_diagnostics', 40))")
    observer = scripts / "qemu-entry/gdb-stop.py"
    text = observer.read_text()
    cpu_expression = "'cortex-a710' if args.qemu_accel == 'tcg' else 'host'"
    if text.count(cpu_expression) != 3:
        raise ValueError("QEMU command/report CPU contexts changed")
    observer.write_text(text.replace(cpu_expression, "'cortex-a53' if args.qemu_accel == 'tcg' else 'host'"))
    qemu_dt = scripts / "qemu-entry/make-dt.py"
    text = qemu_dt.read_text()
    if text.count("b'arm,cortex-a710\\0'") != 2:
        raise ValueError("QEMU CPU identity contexts changed")
    qemu_dt.write_text(text.replace("b'arm,cortex-a710\\0'", "b'arm,cortex-a53\\0'"))
    hashes = {}
    import hashlib
    for name in ("build-corecrypto-14.py", "build-libpthread-kernel.py", "probe-darwinboot-xnu.py",
                 "qemu-entry/gdb-stop.py", "qemu-entry/make-dt.py",
                 "build-xnu-4k-baseline.sh", "apply-chiron-trace.py", "build-chiron-trace.py"):
        hashes[name] = hashlib.sha256((scripts / name).read_bytes()).hexdigest()
    (args.tree / "build/chiron-recipe-overlays.json").write_text(json.dumps(hashes, indent=2) + "\n")


if __name__ == "__main__":
    main()
