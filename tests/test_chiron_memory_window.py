"""Host checks for real loader ownership validation and the retained transport."""

import ctypes
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]


class Descriptor(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("pad", ctypes.c_uint32),
                ("phys_start", ctypes.c_uint64), ("virt_start", ctypes.c_uint64),
                ("pages", ctypes.c_uint64), ("attribute", ctypes.c_uint64)]


class MemoryOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("host C compiler unavailable")
        cls.folder = tempfile.TemporaryDirectory(prefix="chiron-window-")
        cls.addClassCleanup(cls.folder.cleanup)
        path = Path(cls.folder.name)
        source = (ROOT / "ci/hackios/memory_loader.inc").read_text()
        function = source[:source.index("static int\nchiron_preclaimed_window(")]
        prelude = """#include <stdint.h>
typedef struct {
 uint32_t type, pad; uint64_t phys_start, virt_start, pages, attribute;
} efi_memory_descriptor;
enum { EfiLoaderData = 2 };
"""
        wrapper = """
int check(const uint8_t *map, uint64_t size, uint64_t stride, uint64_t base, uint64_t bytes) {
 return chiron_window_owned(map, size, stride, base, bytes);
}
"""
        (path / "window.c").write_text(prelude + function + wrapper)
        result = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                                 "-shared", "-fPIC", path / "window.c", "-o", path / "window.so"],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.library = ctypes.CDLL(str(path / "window.so"))
        cls.check = cls.library.check
        cls.check.argtypes = [ctypes.c_void_p, *([ctypes.c_uint64] * 4)]
        cls.check.restype = ctypes.c_int

    def owned(self, ranges, base=0x100000000, size=0xbc000000, stride=48):
        data = b"".join(bytes(Descriptor(kind, 0, start, 0, length // 4096, attributes))
                        + bytes(stride - ctypes.sizeof(Descriptor))
                        for kind, start, length, attributes in ranges)
        buffer = ctypes.create_string_buffer(data)
        return bool(self.check(buffer, len(data), stride, base, size))

    def test_contiguous_and_unsorted_owned_fragments(self):
        self.assertTrue(self.owned([(2, 0x100000000, 0xbc000000, 0)]))
        self.assertTrue(self.owned([(2, 0x170000000, 0x4c000000, 0),
                                    (2, 0x100000000, 0x70000000, 0)]))

    def test_live_uefi_types_and_gaps_are_rejected(self):
        for kind in (0, 3, 4, 5, 6, 7):
            with self.subTest(kind=kind):
                self.assertFalse(self.owned([(kind, 0x100000000, 0xbc000000, 0)]))
        self.assertFalse(self.owned([(2, 0x100000000, 0x6e000000, 0),
                                     (2, 0x170000000, 0x4c000000, 0)]))
        self.assertFalse(self.owned([(2, 0x100000000, 0xbc000000, 1 << 63)]))
        self.assertFalse(self.owned([(2, 0x100000000, 0xbc000000, 0),
                                     (4, 0x16e000000, 0x02000000, 0)]))

    def test_invalid_extent_and_stride_are_rejected(self):
        ranges = [(2, 0x100000000, 0xbc000000, 0)]
        self.assertFalse(self.owned(ranges, size=0))
        self.assertFalse(self.owned(ranges, base=0x100000001))
        self.assertFalse(self.owned(ranges, base=0xfffffffffffff000, size=0x2000))
        self.assertFalse(self.owned(ranges, stride=41))
        self.assertFalse(self.check(None, 0, 0, 0x100000000, 0xbc000000))


class TransportRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("chiron_package",
                                                      ROOT / "ci/hackios/package-chiron.py")
        cls.package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.package)

    def test_source_firmware_keeps_only_the_guarded_transport_prefix(self):
        with tempfile.TemporaryDirectory(prefix="chiron-firmware-test-") as directory:
            folder = Path(directory)
            frozen = bytearray(b"\xff" * 0x600000)
            frozen[:64] = bytes(64)
            frozen[4:8] = bytes.fromhex("0f000014")
            frozen[64:80] = b"transport-guard!"
            rebuilt = bytearray(frozen)
            rebuilt[:8] = bytes.fromhex("01000010ff1f0014")
            rebuilt[64:0x8000] = b"\xff" * (0x8000 - 64)
            rebuilt[0x9000:0x9008] = b"NEW-BODY"
            firmware = folder / "firmware.fd"
            firmware.write_bytes(rebuilt)
            policy = folder / "policy.json"
            report = {"schema": "chiron-owned-memory-window-v1",
                      "kernel_window_base": "0x100000000",
                      "kernel_window_bytes": "0xbc000000",
                      "early_uefi_heap_base": "0xd8000000",
                      "early_uefi_heap_bytes": "0x8000000",
                      "firmware_sha256": hashlib.sha256(rebuilt).hexdigest()}
            policy.write_text(json.dumps(report))
            dtb = b"preserved-dtb-sentinel"
            encoded, proof = self.package.rebuild_transport_kernel(
                gzip.compress(frozen) + dtb, firmware, policy)
            decoder = zlib.decompressobj(31)
            effective = decoder.decompress(encoded)
            self.assertEqual(effective[:0x8000], frozen[:0x8000])
            self.assertEqual(effective[0x8000:], rebuilt[0x8000:])
            self.assertEqual(decoder.unused_data, dtb)
            self.assertFalse(proof["hardware_qualified"])
            report["firmware_sha256"] = "wrong"
            policy.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "policy or digest"):
                self.package.rebuild_transport_kernel(
                    gzip.compress(frozen) + dtb, firmware, policy)


class EarlyHeapRegressionTests(unittest.TestCase):
    def test_actual_128_mib_heap_is_accepted_and_wrong_32_mib_heap_is_rejected(self):
        if not shutil.which("cc"):
            self.skipTest("host C compiler unavailable")
        source = (ROOT / "package/ChironCiPkg/ChironReserveWindow.inc").read_text()
        helpers = source[source.index("STATIC BOOLEAN\nChironWindowDisjoint("):
                         source.index("STATIC BOOLEAN\nChironWindowFdtReservationsSafe(")]
        unit = """#include <stdint.h>
#include <assert.h>
typedef uint64_t UINT64;
typedef int BOOLEAN;
#define STATIC static
#define FALSE 0
#define MAX_UINT64 UINT64_MAX
#include "ChironMemoryWindow.h"
""" + helpers + """
int main(void) {
 assert(ChironWindowHeapSafe(0xd8000000ULL, 0x08000000ULL));
 assert(!ChironWindowHeapSafe(0xde000000ULL, 0x02000000ULL));
 assert(!ChironWindowHeapSafe(0x168000000ULL, 0x08000000ULL));
 assert(!ChironWindowHeapSafe(0xd8000000ULL, 0));
 assert(!ChironWindowDisjoint(0x100000000ULL, 0xbc000000ULL, 0x168000000ULL, 0x08000000ULL));
 assert(!ChironWindowDisjoint(0x100000000ULL, 0xbc000000ULL, UINT64_MAX-3, 8));
 return 0;
}
"""
        with tempfile.TemporaryDirectory(prefix="chiron-heap-check-") as directory:
            folder = Path(directory)
            (folder / "heap.c").write_text(unit)
            result = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", ROOT / "package/ChironCiPkg",
                 folder / "heap.c", "-o", folder / "heap"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([folder / "heap"], check=True)


if __name__ == "__main__":
    unittest.main()
