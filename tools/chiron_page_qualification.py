#!/usr/bin/env python3
"""Compile pinned XNU algorithms in an offline, instrumented host harness."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package/ChironPageQualification"
SOURCE_DEFAULT = Path("/private/tmp/chiron-console-source")
UPSTREAM_DEFAULT = Path("/private/tmp/ios-party-research/unixnu")

# Content identity is deliberately stricter than a matching function name.
# Refresh only after reviewing the new source, never automatically in a test.
SOURCE_SHA256 = {
    "bsd/kern/ubc_subr.c": "4e28feefbfab2150ba53ba9a38774eee8db3617e71868f76841f85555e86eaf5",
    "bsd/sys/ubc_internal.h": "a450f2f4b686090f1e52b03940c2653d09796771990b331bdefaaa29223468b5",
    "osfmk/kern/cs_blobs.h": "7cd21aa6574bd8b934488da8391eb188599873568e486c4a0583c0cd7e0750e0",
    "osfmk/vm/vm_shared_region_pager.c": "1c5db57a3f9a061e0faa34a7488d2105622c2dba5482bd6af9666469bfeb8a7a",
    "osfmk/vm/vm_shared_region.c": "6c2e7ea7ebe2e807ebadc699a1888877f35355aba994d9a1afe27223a2f9d889",
    "osfmk/vm/vm_shared_region.h": "69e66d3aba399eceae11a1f58123345bb8504c22f5617e4a51774dfbc8dcac8e",
}
UPSTREAM_SHA256 = {
    "patches/xnu/native-4k-signature-hash-cache.patch": "f9a8b4d39022a726ba1e0e4842248d57b20fe3a05fbf41b3a01f6700b2e3ca72",
    "patches/xnu/native-4k-user-abi.patch": "981ac483ae898426486aab475e22b2286bc1d820ad047c566182c3fbf435f6d4",
    "scripts/build-xnu-4k-baseline.sh": "3486867d8c639eb10d31be16e875dbbdbb1d27afd97aedbb8ba930f0093c6852",
}


def checked_sources(root, expected):
    result = {}
    for name, digest in expected.items():
        path = Path(root) / name
        if not path.is_file():
            raise ValueError(f"required pinned source missing: {path}")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise ValueError(f"source fingerprint mismatch: {path}: {actual}; expected {digest}")
        result[name] = data.decode("utf-8")
    return result


def between(text, start, end):
    """Exact anchors in fingerprinted C, not a general C parser."""
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"source extraction anchor is not unique: {start!r} / {end!r}")
    first = text.index(start)
    last = text.index(end, first)
    return text[first:last]


def function(text, signature):
    """Extract through the next column-zero closing brace in pinned XNU style."""
    if text.count(signature) != 1:
        raise ValueError(f"function extraction anchor is not unique: {signature!r}")
    first = text.index(signature)
    last = text.index("\n}", first) + 2
    return text[first:last] + "\n"


def generated_source(source):
    ubc = source["bsd/kern/ubc_subr.c"]
    blobs = source["osfmk/kern/cs_blobs.h"]
    region = source["osfmk/vm/vm_shared_region.c"]
    header = source["osfmk/vm/vm_shared_region.h"]
    pager = source["osfmk/vm/vm_shared_region_pager.c"]
    declarations = between(blobs, "enum {\n\tCSMAGIC_REQUIREMENT", "/* The set of application types")
    declarations += between(blobs, "typedef struct __CodeDirectory {", "/*\n * Defined launch types")
    declarations += between(header, "typedef struct vm_shared_region_slide_info_entry_v1",
                            "#define MIN_SLIDE_INFO_SIZE")
    # Keep real hash dispatch and structures, substituting only host crypto APIs.
    hash_types = between(ubc, "typedef void (*cs_md_init)", "/*\n * Choose among different hash algorithms.")
    funcs = function(ubc, "static boolean_t\ncs_valid_range(")
    funcs += function(ubc, "static const unsigned char *\nhashes(")
    funcs += function(ubc, "static int\ncs_validate_codedirectory(")
    funcs += "#if XNU_NATIVE_CS_HASH_CACHE\n"
    funcs += function(ubc, "static int\ncs_native4k_prepare(vnode_t vp, struct cs_blob *blob)\n{")
    funcs += "#endif\n"
    funcs += function(ubc, "static boolean_t\ncs_validate_hash(")
    for version in (2, 3, 4, 5):
        funcs += function(region, f"static kern_return_t\nvm_shared_region_slide_sanity_check_v{version}(")
    v5 = function(region, "static kern_return_t\nvm_shared_region_slide_page_v5(")
    # Execute the original copy/range/index prefix; terminate before debug/UPL
    # completion code. The stub callback records the original call arguments.
    prefix = between(pager, "\t\tfor (offset_in_page = 0;", "\t\t\tif (shared_region_pager_data_request_debug)")
    pager_function = """
static void run_pager_prefix(struct host_pager *pager, uint64_t offset,
    uint64_t cur_offset, uintptr_t src_vaddr, uintptr_t dst_vaddr,
    uint32_t slide_info_page_size, uint64_t slide_start_address)
{
    uint64_t offset_in_page;
    kern_return_t kr;
""" + prefix + "\n(void)kr;\n}\n}\n"
    return "\n".join([
        (PACKAGE / "host_prelude.h").read_text(),
        declarations, hash_types,
        (PACKAGE / "host_shims.h").read_text(),
        funcs,
        "#define memcpy host_traced_memcpy",
        v5,
        "#undef memcpy",
        "#define bcopy host_traced_bcopy",
        pager_function,
        "#undef bcopy",
        (PACKAGE / "host_cases.c").read_text(),
    ])


def configuration(upstream):
    settings = {}
    for name in ("chiron_defconfig", "chiron_trace_defconfig", "chiron_raminit_defconfig"):
        text = (ROOT / "configs" / name).read_text()
        settings[name] = [line for line in text.splitlines()
                          if "XNU_NATIVE_CS_HASH_CACHE" in line]
    script = upstream["scripts/build-xnu-4k-baseline.sh"]
    default_off = "CS_HASH_CACHE_MODE=${XNU_NATIVE_CS_HASH_CACHE:-0}" in script
    return {
        "upstream_default": 0 if default_off else "unknown",
        "defconfig_bridge_entries": settings,
        "current_environment": os.environ.get("XNU_NATIVE_CS_HASH_CACHE", "unset"),
        "kernel_execution_or_artifact_checked": False,
        "note": "Defconfigs do not enable the bridge; an inherited environment or binary needs separate verification.",
    }


def run_host(translation_unit):
    if sys.platform != "darwin":
        raise ValueError("host harness requires macOS CommonCrypto and clang; no dependency installation performed")
    compiler = shutil.which("clang")
    if not compiler:
        raise ValueError("clang is required")
    results = []
    with tempfile.TemporaryDirectory(prefix="chiron-page-qualification-") as temp:
        c_path = Path(temp) / "production_extract.c"
        c_path.write_text(translation_unit)
        for mode in (0, 1):
            executable = Path(temp) / f"qualification-{mode}"
            args = [
                compiler, "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wno-deprecated-declarations", "-Wno-unused-function", "-Wno-unused-parameter",
                "-Wno-format", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                f"-DXNU_NATIVE_CS_HASH_CACHE={mode}", "-I", str(PACKAGE),
                str(c_path), "-o", str(executable),
            ]
            built = subprocess.run(args, capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise ValueError(f"host compile failed (bridge={mode}):\n{built.stderr}")
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            cases = [json.loads(line[5:]) for line in ran.stdout.splitlines() if line.startswith("CASE ")]
            if ran.returncode or not cases or any(not case["pass"] for case in cases):
                raise ValueError(f"host cases failed (bridge={mode}):\n{ran.stdout}\n{ran.stderr}")
            results.extend(dict(case, compiled_bridge=mode) for case in cases)
    return results, compiler


def qualify(source_root=SOURCE_DEFAULT, upstream_root=UPSTREAM_DEFAULT, source_only=False):
    source = checked_sources(source_root, SOURCE_SHA256)
    upstream = checked_sources(upstream_root, UPSTREAM_SHA256)
    report = {
        "schema": 1,
        "scope": "offline pinned-source host qualification",
        "source_sha256": SOURCE_SHA256,
        "upstream_sha256": UPSTREAM_SHA256,
        "configuration": configuration(upstream),
        "target_cache_supplied": False,
        "target_cache_compatible": None,
        "bridge_enabled_by_this_tool": False,
        "kernel_or_device_qualified": False,
        "known_findings": [
            "16K slide copies 16384 bytes into a logical 4096-byte pager page, before range checks",
            "offsets 0/4096/8192/12288 reuse slide index 0 with different destination bases",
            "slide index narrows to uint32_t; relative offset 2**32 * slide_page_size wraps to zero",
        ],
        "cases": [],
    }
    pins = json.loads((ROOT / "ci/sources.json").read_text())
    expected_pins = {
        "xnu": "d8b80295118ef25ac3a784134bcf95cd8e88109f",
        "unixnu": "7b52e7968c76453cdc18b5aa174fc368d5293b91",
    }
    for name, commit in expected_pins.items():
        if pins[name]["commit"] != commit:
            raise ValueError(f"{name} pin changed; review qualification sources before reuse")
    report["declared_source_commits"] = expected_pins
    report["source_origin_authenticated_by_tool"] = False
    if source_only:
        report["status"] = "source-matched-only"
        return report
    translation_unit = generated_source(source)
    report["generated_translation_unit_sha256"] = hashlib.sha256(translation_unit.encode()).hexdigest()
    report["cases"], compiler = run_host(translation_unit)
    report["compiler"] = compiler
    report["compiler_version"] = subprocess.run(
        [compiler, "--version"], check=True, capture_output=True, text=True, timeout=10
    ).stdout.splitlines()[0]
    report["sanitizers"] = ["address", "undefined"]
    report["status"] = "host-boundaries-qualified-with-known-pager-rejection-required"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--upstream-root", type=Path, default=UPSTREAM_DEFAULT)
    parser.add_argument("--source-check-only", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit the complete evidence report")
    parser.add_argument("--output", type=Path, help="save a new JSON evidence file")
    args = parser.parse_args(argv)
    try:
        report = qualify(args.source_root, args.upstream_root, args.source_check_only)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"{report['status']}: {len(report['cases'])} host cases passed")
        print("Bridge default OFF; target cache, kernel lifecycle and device NOT qualified.")
        for finding in report["known_findings"]:
            print(f"KNOWN: {finding}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
