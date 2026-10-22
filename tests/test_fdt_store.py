import hashlib
import importlib.util
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools"), str(ROOT / "ci"), *sys.path]):
        spec.loader.exec_module(module)
    return module


binary_patch = load("fdt_binary_patch", "tools/patch_chiron_fdt_store.py")
integration = load("fdt_integration", "ci/integrate_firmware.py")


def firmware_fixture():
    firmware = bytearray(b"\xff" * 0x600000)
    firmware[:64] = bytes(64)
    firmware[:8] = bytes.fromhex("01000010 ff1f0014")
    firmware[56:60] = b"ARM\x64"
    struct.pack_into("<Q", firmware, 16, len(firmware))
    fv = 0x8000
    header = bytearray(72)
    struct.pack_into("<Q", header, 32, len(firmware) - fv)
    header[40:44] = b"_FVH"
    struct.pack_into("<H", header, 48, len(header))
    checksum = (-sum(struct.unpack("<36H", header))) & 0xffff
    struct.pack_into("<H", header, 50, checksum)
    firmware[fv:fv + len(header)] = header
    ffs = bytearray(24)
    ffs[17], ffs[18], ffs[19], ffs[23] = 0xaa, 3, 0x28, 0xf8
    ffs[20:23] = (0x10000).to_bytes(3, "little")
    ffs[16] = (-(sum(ffs) - ffs[17] - ffs[23])) & 0xff
    firmware[0x8048:0x8060] = ffs
    firmware[0xfe64:0xfe64 + len(binary_patch.COPY_BODY)] = binary_patch.COPY_BODY
    return bytes(firmware)


class FdtStoreTests(unittest.TestCase):
    def test_source_store_moves_after_copy_and_rejects_changed_context(self):
        original = (
            "docopy:\n"
            "  mov x10, FixedPcdGet64 (PcdDeviceTreeStore)\n"
            "  str x0, [x10] // oh my fdt\n"
            "loop:\n  ldp x2, x3, [x4], #16\n"
            "  stp x2, x3, [x6], #16\n"
            "  b.ne\tloop\n  ret\n"
        )
        result = integration.defer_fdt_store(original)
        self.assertEqual(result.count("str x0, [x10]"), 1)
        self.assertGreater(result.index("str x0, [x10]"), result.index("b.ne\tloop"))
        with self.assertRaises(ValueError):
            integration.defer_fdt_store(result)
        with self.assertRaises(ValueError):
            integration.defer_fdt_store(original.replace("b.ne\tloop", "b.eq\tloop"))

    def test_binary_patch_changes_only_three_explicit_ranges(self):
        original = firmware_fixture()
        patched, report = binary_patch.patch_firmware(original, hashlib.sha256(original).hexdigest())
        self.assertTrue(report["fv_header_unchanged"])
        self.assertTrue(report["sec_ffs_header_unchanged"])
        start = original.index(binary_patch.COPY_BODY)
        tail = start + len(binary_patch.COPY_BODY) - 4
        allowed = set(range(0x40, 0x48)) | set(range(start + 4, start + 8)) | set(range(tail, tail + 4))
        self.assertTrue(all(index in allowed for index, (left, right)
                            in enumerate(zip(original, patched)) if left != right))
        self.assertEqual(patched[start + 4:start + 8], binary_patch.NOP)
        instruction = struct.unpack_from("<I", patched, tail)[0]
        immediate = instruction & 0x03ffffff
        if immediate & (1 << 25):
            immediate -= 1 << 26
        self.assertEqual(tail + immediate * 4, 0x40)
        self.assertEqual(patched[0x40:0x48], binary_patch.STORE_AND_RETURN)

    def test_patch_refuses_unknown_bytes_and_checksums(self):
        original = firmware_fixture()
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            binary_patch.patch_firmware(original, "0" * 64)
        for offset, value, message in (
            (0x40, 0, "entry padding"),
            (0xfe68, 0, "exactly one"),
            (0x8048 + 19, 0x68, "fixed-checksum"),
            (0x8000 + 50, 1, "header checksum"),
        ):
            changed = bytearray(original)
            changed[offset] = value
            with self.assertRaisesRegex(ValueError, message):
                binary_patch.patch_firmware(changed, hashlib.sha256(changed).hexdigest())


if __name__ == "__main__":
    unittest.main()
