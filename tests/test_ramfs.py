"""Host and compile qualification only. No kernel build, QEMU, or device access."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import chiron_ramfs as ramfs

SPEC = importlib.util.spec_from_file_location("ramfs_overlay", ROOT / "ci/apply_chiron_ramfs.py")
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)
PINNED = Path(os.environ.get("CHIRON_RAMFS_PINNED_SOURCE", "/private/tmp/chiron-console-source"))
PACKAGE = ROOT / "package/ChironRamfs"
CC = shutil.which("clang") or shutil.which("cc")


def fixture():
    return ramfs.build([
        ramfs.Entry("/", "dir"), ramfs.Entry("/dev", "dir"),
        ramfs.Entry("/usr", "dir"), ramfs.Entry("/usr/lib", "dir"),
        ramfs.Entry("/sbin", "dir"), ramfs.Entry("/sbin/launchd", "file", b"LAUNCHD\n"),
        ramfs.Entry("/sbin/dyld", "file", b"OTHER\n"),
        ramfs.Entry("/usr/lib/dyld", "file", b"DYLD\n" + b"d" * 8996, 0o550, 501, 20),
        ramfs.Entry("/usr/lib/empty", "file", b""),
        ramfs.Entry("/probe.bin", "file", b"PROBE\n"),
    ])


def malformed_images():
    good = fixture()
    entries = ramfs.parse(good)
    ids = {e["path"]: i for i, e in enumerate(entries)}
    cases = {}

    def field(label, at, value, fmt="<I"):
        image = bytearray(good)
        struct.pack_into(fmt, image, at, value)
        cases[label] = bytes(image)

    for at, label, value, fmt in (
        (0, "magic", 0, "<Q"), (8, "version", 2, "<I"), (12, "header-size", 0, "<I"),
        (16, "entry-size", 127, "<I"), (20, "count-zero", 0, "<I"),
        (20, "count-overflow", 0xFFFFFFFF, "<I"), (24, "image-size-overflow", 2**64 - 1, "<Q"),
        (32, "table-offset", 4096, "<Q"), (40, "data-offset", 4097, "<Q"),
        (48, "flags", 1, "<Q"),
    ):
        field(label, at, value, fmt)
    file_at = ramfs.HEADER.size + ids["/usr/lib/dyld"] * ramfs.RECORD.size
    dir_at = ramfs.HEADER.size + ids["/usr/lib"] * ramfs.RECORD.size
    for label, base, at, value, fmt in (
        ("root-parent", 64, 0, 1, "<I"), ("root-kind", 64, 4, 2, "<I"),
        ("root-name", 64, 24, 1, "<I"), ("mode-type-bits", file_at, 8, 0o100755, "<I"),
        ("record-reserved", file_at, 20, 1, "<I"), ("record-reserved-2", file_at, 28, 1, "<I"),
        ("kind-unsupported", file_at, 4, 3, "<I"), ("parent-self", dir_at, 0, ids["/usr/lib"], "<I"),
        ("parent-overflow", dir_at, 0, 0xFFFFFFFF, "<I"),
        ("parent-file", file_at, 0, ids["/probe.bin"], "<I"),
        ("name-long", file_at, 24, 80, "<I"), ("name-empty", file_at, 24, 0, "<I"),
        ("offset-overflow", file_at, 32, 2**64 - 1, "<Q"),
        ("length-overflow", file_at, 40, 2**64 - 1, "<Q"),
        ("directory-extent", dir_at, 32, 4096, "<Q"),
        ("directory-size", dir_at, 40, 1, "<Q"),
        ("offset-unaligned", file_at, 32, entries[ids["/usr/lib/dyld"]]["offset"] + 1, "<Q"),
        ("offset-overlap", file_at, 32, 4096, "<Q"),
    ):
        field(label, base + at, value, fmt)
    for name in (b".", b"..", b"a/b", b"a\\b", b"\0", b"\x1f", b"\x7f", b"\xff"):
        image = bytearray(good)
        struct.pack_into("<I", image, file_at + 24, len(name))
        image[file_at + 48:file_at + 128] = name.ljust(80, b"\0")
        cases["name-" + name.hex()] = bytes(image)
    image = bytearray(good)
    # Same name under the same parent, with distinct extents still present.
    other = ramfs.HEADER.size + ids["/usr/lib/empty"] * ramfs.RECORD.size
    image[other + 24:other + 28] = image[file_at + 24:file_at + 28]
    image[other + 48:other + 128] = image[file_at + 48:file_at + 128]
    cases["duplicate"] = bytes(image)
    image = bytearray(good)
    image[64 + len(entries) * 128] = 1
    cases["metadata-padding"] = bytes(image)
    image = bytearray(good)
    e = entries[ids["/usr/lib/dyld"]]
    image[e["offset"] + e["size"]] = 1
    cases["file-padding"] = bytes(image)
    image = bytearray(good)
    image[file_at + 48 + 5] = 1
    cases["name-padding"] = bytes(image)
    image = bytearray(good + bytes(4096))
    struct.pack_into("<Q", image, 24, len(image))
    cases["trailing-page"] = bytes(image)
    cases["truncated-byte"] = good[:-1]
    image = bytearray(good[:-4096])
    struct.pack_into("<Q", image, 24, len(image))
    cases["truncated-page"] = bytes(image)
    for label, levels, name in (("depth", 17, b"x"), ("path-length", 13, b"x" * 79)):
        records = [ramfs.RECORD.pack(0, 1, 0o555, 0, 0, 0, 0, 0, 0, 0, b"")]
        for i in range(levels):
            records.append(ramfs.RECORD.pack(i, 1, 0o555, 0, 0, 0, len(name), 0, 0, 0, name))
        size = ramfs.align(64 + len(records) * 128)
        image = bytearray(size)
        ramfs.HEADER.pack_into(image, 0, ramfs.MAGIC, 1, 64, 128, len(records), size, 64, size, bytes(16))
        image[64:64 + len(records) * 128] = b"".join(records)
        cases[label] = bytes(image)
    return cases


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CC:
            raise RuntimeError("local C compiler is required")
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        library = cls.directory / "parser.dylib"
        subprocess.run([CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                        str(PACKAGE / "tests/ParserHost.c"), "-o", str(library)], check=True)
        cls.c = ctypes.CDLL(str(library))
        p, z, q, u, signed = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_int64
        cls.c.parse.argtypes = [p, z, q, ctypes.c_int]
        cls.c.lookup.argtypes = [p, z, u, ctypes.c_char_p, u, ctypes.POINTER(u)]
        cls.c.map.argtypes = [p, z, u, signed, q, u, ctypes.POINTER(q), ctypes.POINTER(q)]
        cls.c.read_file.argtypes = [p, z, u, signed, p, z, ctypes.POINTER(z)]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def accepted(self, image):
        self.assertEqual(self.c.parse(image, len(image), len(image), 0), 0)
        return ramfs.parse(image)

    def test_roundtrip_paths_metadata_and_distinct_files(self):
        image = fixture()
        records = self.accepted(image)
        self.assertEqual(len(records), 10)
        self.assertEqual(ramfs.read_file(image, "/sbin/dyld"), b"OTHER\n")
        self.assertTrue(ramfs.read_file(image, "/usr/lib/dyld").startswith(b"DYLD\n"))
        self.assertEqual(ramfs.read_file(image, "/usr/lib/empty"), b"")
        e = next(e for e in records if e["path"] == "/usr/lib/dyld")
        self.assertEqual((e["mode"], e["uid"], e["gid"], e["size"]), (0o550, 501, 20, 9001))
        self.assertEqual(image, fixture())
        self.assertTrue(all(e["offset"] % 4096 == 0 for e in records))

    def test_production_lookup_read_and_map(self):
        image = fixture()
        records = self.accepted(image)
        ids = {e["path"]: i for i, e in enumerate(records)}
        index = ctypes.c_uint32()
        lib = ids["/usr/lib"]
        self.assertEqual(self.c.lookup(image, len(image), lib, b"dyld", 4, ctypes.byref(index)), 0)
        self.assertEqual(index.value, ids["/usr/lib/dyld"])
        self.assertEqual(self.c.lookup(image, len(image), 0, b"dyld", 4, ctypes.byref(index)), 3)
        self.assertEqual(self.c.lookup(image, len(image), lib, b"dyldX", 5, ctypes.byref(index)), 3)
        self.assertEqual(self.c.lookup(image, len(image), lib, b"..", 2, ctypes.byref(index)), 0)
        self.assertEqual(index.value, ids["/usr"])
        self.assertEqual(self.c.lookup(image, len(image), 999, b"x", 1, ctypes.byref(index)), 1)
        for path in ("/sbin/dyld", "/usr/lib/dyld", "/usr/lib/empty"):
            e = records[ids[path]]
            for offset in (0, 4095, 4096, 8192, e["size"], e["size"] + 1):
                buffer = ctypes.create_string_buffer(16384)
                actual = ctypes.c_size_t()
                self.assertEqual(self.c.read_file(image, len(image), ids[path], offset, buffer,
                                                 len(buffer), ctypes.byref(actual)), 0)
                self.assertEqual(buffer.raw[:actual.value], ramfs.read_file(image, path)[offset:])
                self.assertEqual(actual.value, max(0, e["size"] - offset))
        block, run = ctypes.c_uint64(), ctypes.c_uint64()
        e = records[ids["/usr/lib/dyld"]]
        for blksize in (512, 4096):
            self.assertEqual(self.c.map(image, len(image), ids[e["path"]], 8192, 4096, blksize,
                                       ctypes.byref(block), ctypes.byref(run)), 0)
            self.assertEqual(block.value * blksize, e["offset"] + 8192)
            self.assertEqual(run.value, 4096)
        for offset, size, bs in ((-1, 1, 512), (2**63 - 1, 1, 512), (1, 1, 512),
                                 (8192, 4097, 512), (12288, 1, 512), (0, 2**64 - 1, 512),
                                 (0, 1, 0), (0, 1, 513), (0, 1, 8192), (0, 0, 512)):
            self.assertEqual(self.c.map(image, len(image), ids[e["path"]], offset, size, bs,
                                       ctypes.byref(block), ctypes.byref(run)), 1)
        self.assertEqual(self.c.map(image, len(image), ids["/usr/lib/empty"], 0, 1, 512,
                                   ctypes.byref(block), ctypes.byref(run)), 1)

    def test_malformed_and_truncated_production_parser(self):
        for label, image in malformed_images().items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    ramfs.parse(image)
                self.assertNotEqual(self.c.parse(image, len(image), len(image), 0), 0)
        image = fixture()
        self.assertEqual(self.c.parse(image, len(image), len(image), 1), 2)
        self.assertEqual(self.c.parse(image[:-4096], len(image) - 4096, len(image), 0), 2)
        for claimed in (0, 1, 4095, ramfs.MAX_IMAGE + 4096, 2**64 - 1):
            self.assertNotEqual(self.c.parse(image, len(image), claimed, 0), 0)

    def test_deterministic_mutation_differential(self):
        image = fixture()
        rng = random.Random(0x43485241)
        for _ in range(1200):
            bad = bytearray(image)
            # Metadata only; data-content mutations are valid without a payload checksum.
            at = rng.randrange(64 + 128 * 10)
            bad[at] ^= 1 << rng.randrange(8)
            bad = bytes(bad)
            try:
                ramfs.parse(bad)
                python_accepted = True
            except ValueError:
                python_accepted = False
            self.assertEqual(self.c.parse(bad, len(bad), len(bad), 0) == 0, python_accepted,
                             f"parser disagreement at {at}")

    def test_format_limits(self):
        entries = [ramfs.Entry("/", "dir")]
        entries += [ramfs.Entry(f"/file{i:03}", "file") for i in range(127)]
        self.assertEqual(len(self.accepted(ramfs.build(entries))), 128)
        with self.assertRaises(ValueError):
            ramfs.build(entries + [ramfs.Entry("/extra", "file")])
        large = ramfs.Entry("/large", "file", bytes(ramfs.MAX_IMAGE - ramfs.PAGE))
        image = ramfs.build([ramfs.Entry("/", "dir"), large])
        self.assertEqual(len(image), ramfs.MAX_IMAGE)
        self.accepted(image)
        with self.assertRaises(ValueError):
            ramfs.build([ramfs.Entry("/", "dir"), ramfs.Entry("/large", "file", large.data + b"x")])
        self.accepted(ramfs.build([ramfs.Entry("/", "dir"), ramfs.Entry("/" + "x" * 79, "file")]))

    def test_builder_rejects_paths_and_metadata(self):
        for path in ("relative", "/a/../b", "/a/./b", "//a", "/a/", "/a\\b", "/a\0b",
                     "/a\nb", "/" + "x" * 80, "/" + "/".join(["x"] * 17), "/中文"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ramfs.build([ramfs.Entry("/", "dir"), ramfs.Entry(path, "file")])
        for entries in (
            [ramfs.Entry("/", "dir"), ramfs.Entry("/a", "file"), ramfs.Entry("/a", "file")],
            [ramfs.Entry("/", "dir"), ramfs.Entry("/a/b", "file")],
            [ramfs.Entry("/", "dir"), ramfs.Entry("/a", "file"), ramfs.Entry("/a/b", "file")],
            [ramfs.Entry("/", "dir", b"x")], [ramfs.Entry("/", "file")],
            [ramfs.Entry("/", "dir", mode=0o4755)], [ramfs.Entry("/", "dir", uid=-1)],
            [ramfs.Entry("/", "dir", gid=2**32)], [ramfs.Entry("/", "dir", mode=True)],
        ):
            with self.assertRaises(ValueError):
                ramfs.build(entries)

    def test_manifest_cli_roundtrip_and_input_rejection(self):
        work = self.directory / "manifest"
        work.mkdir()
        (work / "a.bin").write_bytes(b"abc\0def")
        manifest = {"schema_version": 1, "entries": [
            {"path": "/", "type": "dir", "mode": "0755"},
            {"path": "/a", "type": "file", "source": "a.bin", "mode": "0440", "uid": 501, "gid": 20},
        ]}
        path, out = work / "manifest.json", work / "root.img"
        path.write_text(json.dumps(manifest))
        subprocess.run([sys.executable, "-B", str(ROOT / "tools/chiron_ramfs.py"), "build",
                        "--manifest", str(path), "--output", str(out)], check=True, capture_output=True)
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/chiron_ramfs.py"),
                                 "inspect", str(out)], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["format"], "CHRAMFS")
        self.accepted(out.read_bytes())
        (work / "symlink").symlink_to(work / "a.bin")
        manifest["entries"][1]["source"] = "symlink"
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            ramfs.from_manifest(path)
        manifest["entries"][1]["source"] = "/dev/null"
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            ramfs.from_manifest(path)

    def test_arm64_freestanding_parser_compile(self):
        if not shutil.which("clang"):
            self.skipTest("clang ARM64 target unavailable")
        subprocess.run(["clang", "-target", "arm64-apple-darwin", "-ffreestanding",
                        "-std=c11", "-Wall", "-Wextra", "-Werror", "-c",
                        str(PACKAGE / "tests/CoreCompile.c"), "-o", str(self.directory / "core.o")],
                       check=True)


@unittest.skipUnless((PINNED / overlay.RELATIVE / "mockfs_fsnode.c").exists(),
                     "pinned patched mockfs snapshot unavailable; no substitute kernel claim")
class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.tree = self.directory / "xnu"
        self.mockfs = self.tree / overlay.RELATIVE
        self.mockfs.mkdir(parents=True)
        for name, expected in overlay.BASELINE.items():
            data = (PINNED / overlay.RELATIVE / name).read_bytes()
            self.assertEqual(overlay.digest(data), expected, name)
            (self.mockfs / name).write_bytes(data)

    def test_explicit_enable_identity_and_preflight(self):
        before = {p.name: p.read_bytes() for p in self.mockfs.iterdir()}
        with self.assertRaisesRegex(ValueError, "explicit"):
            overlay.apply(self.tree)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.mockfs.iterdir()})
        changed = self.mockfs / "mockfs_vnops.c"
        changed.write_bytes(changed.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "baseline changed"):
            overlay.apply(self.tree, enable_experimental=True)
        self.assertEqual((self.mockfs / "mockfs.h").read_bytes(), before["mockfs.h"])
        self.assertFalse((self.mockfs / "chiron_ramfs.h").exists())
        changed.write_bytes(before["mockfs_vnops.c"])
        report = overlay.apply(self.tree, enable_experimental=True)
        self.assertEqual(report["overlay_sha256"], overlay.OVERLAY)
        self.assertFalse(report["kernel_compiled"])
        with self.assertRaises(ValueError):
            overlay.apply(self.tree, enable_experimental=True)
        self.assertEqual(overlay.verify(self.tree)["baseline_sha256"], overlay.BASELINE)
        # Fixed verification rejects tampering, irrespective of application reports.
        changed.write_bytes(changed.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "source identity"):
            overlay.verify(self.tree)

    def test_payload_identity(self):
        overlay.apply(self.tree, enable_experimental=True)
        path = self.mockfs / "chiron_ramfs.h"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "payload identity"):
            overlay.verify(self.tree)

    def test_legacy_verifier_remains_strict(self):
        spec = importlib.util.spec_from_file_location("selftest_builder", ROOT / "ci/build_ram_selftest.py")
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        # The existing verifier accepts only the original source identity.
        prepared = self.directory / "prepared"
        nested = prepared / ".cache/xnu-work"
        nested.parent.mkdir(parents=True)
        nested.symlink_to(self.tree, target_is_directory=True)
        self.assertEqual(baseline.verify_mockfs(prepared), baseline.MOCKFS_HASHES)
        overlay.apply(self.tree, enable_experimental=True)
        with self.assertRaisesRegex(ValueError, "pinned two-file"):
            baseline.verify_mockfs(prepared)

    def test_actual_overlay_translation_units_and_sanitizers(self):
        overlay.apply(self.tree, enable_experimental=True)
        includes = self.directory / "include"
        headers = (
            "kern/assert.h", "kern/debug.h", "kern/locks.h", "libkern/libkern.h",
            "miscfs/specfs/specdev.h", "sys/disk.h", "sys/buf.h", "sys/kauth.h",
            "sys/malloc.h", "sys/mount_internal.h", "sys/vnode_internal.h", "sys/kernel_types.h",
            "sys/ubc_internal.h", "sys/ubc.h", "sys/fcntl.h", "sys/dirent.h", "sys/uio.h",
            "vm/vm_protos.h", "vfs/vfs_support.h",
        )
        for name in headers:
            path = includes / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "KernelShim.h"\n')
        exe = self.directory / "overlay-host"
        subprocess.run([CC, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-but-set-variable",  # pinned legacy lookup's ctx
                        "-fsanitize=address,undefined", "-I", str(includes),
                        "-I", str(PACKAGE / "tests"), "-I", str(self.tree / "bsd"),
                        "-include", str(PACKAGE / "tests/KernelShim.h"),
                        str(self.mockfs / "mockfs_fsnode.c"), str(self.mockfs / "mockfs_vfsops.c"),
                        str(self.mockfs / "mockfs_vnops.c"), str(PACKAGE / "tests/OverlayHost.c"),
                        "-o", str(exe)], check=True)
        new = self.directory / "ramfs.img"
        old = self.directory / "n4kroot.img"
        raw = self.directory / "raw.img"
        new.write_bytes(fixture())
        spec = importlib.util.spec_from_file_location("selftest_build", ROOT / "ci/build_ram_selftest.py")
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        old.write_bytes(baseline.make_root(b"LAUNCHD\n", b"CHILD\n"))
        raw.write_bytes(b"RAW\n".ljust(4096, b"\0"))
        result = subprocess.run([str(exe), str(new), str(old), str(raw)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: actual overlay", result.stdout)
        bad_paths = []
        for label, image in malformed_images().items():
            path = self.directory / (label + ".bad")
            path.write_bytes(image)
            bad_paths.append(str(path))
        result = subprocess.run([str(exe), "--reject", *bad_paths], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: production C parser", result.stdout)


if __name__ == "__main__":
    unittest.main()
