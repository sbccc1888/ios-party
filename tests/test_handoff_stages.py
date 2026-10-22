import importlib.util
import lzma
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("handoff_builder", ROOT / "tools/build_chiron_handoff_stages.py")
builder = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
    spec.loader.exec_module(builder)


def pe_fixture():
    data = bytearray(2048)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<HH", data, 68, 0xaa64, 2)
    struct.pack_into("<H", data, 84, 240)
    opt = 88
    struct.pack_into("<H", data, opt, 0x20b)
    struct.pack_into("<H", data, opt + 68, 11)
    first = opt + 240
    data[first:first + 8] = b".text\0\0\0"
    struct.pack_into("<4I", data, first + 8, 212, 0x1000, 1024, 512)
    data[first + 40:first + 48] = b".rdata\0\0"
    struct.pack_into("<4I", data, first + 48, 32, 0x2000, 512, 1536)
    data[512:576] = bytes.fromhex("1f2003d5") * 16
    data[576:724] = bytes.fromhex("df4f03d5") + bytes.fromhex("1f2003d5") * 35 + bytes.fromhex("20001fd6")
    return bytes(data)


def ffs(payload, guid, kind=7, attributes=0):
    header = bytearray(24)
    header[:16] = guid
    header[18], header[19], header[23] = kind, attributes, 0xf8
    return builder.ffs_header(payload, header)


def fv(payload, size):
    header = bytearray(72)
    struct.pack_into("<Q", header, 32, size)
    header[40:44] = b"_FVH"
    struct.pack_into("<H", header, 48, len(header))
    checksum = (-sum(struct.unpack("<36H", header))) & 0xffff
    struct.pack_into("<H", header, 50, checksum)
    return bytes(header) + payload + b"\xff" * (size - len(header) - len(payload))


def section(payload, kind):
    return (len(payload) + 4).to_bytes(3, "little") + bytes((kind,)) + payload


def known_lzma(data):
    encoded = lzma.compress(data, format=lzma.FORMAT_ALONE, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": 16 * 1048576,
        "lc": 3, "lp": 0, "pb": 2,
    }])
    return encoded[:5] + struct.pack("<Q", len(data)) + encoded[13:]


class HandoffStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("clang"):
            raise unittest.SkipTest("clang is required for AArch64 handoff assembly")
        with tempfile.TemporaryDirectory() as directory:
            cls.code, cls.stages = builder.compile_handoff(Path(directory))

    def test_pe_patch_fits_existing_section_and_preserves_file_layout(self):
        original = pe_fixture()
        modified, report = builder.patch_pe(original, self.code, builder.sha256(original))
        self.assertEqual(len(modified), len(original))
        self.assertEqual(report["function_rva"], 0x1040)
        self.assertGreater(report["new_text_virtual_size"], report["old_text_virtual_size"])
        self.assertEqual(modified[576:576 + len(self.code)], self.code)
        self.assertEqual(modified[1536:], original[1536:])
        self.assertEqual(len(self.stages), 4)

    def test_pe_rejects_unknown_or_occupied_layouts(self):
        original = pe_fixture()
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            builder.patch_pe(original, self.code, "0" * 64)
        for offset, value, message in (
            (724, 1, "linker padding"),
            (88 + 64, 1, "PE checksum"),
            (88 + 144, 1, "security directory"),
            (576, 0, "final code function"),
        ):
            modified = bytearray(original)
            modified[offset] = value
            with self.assertRaisesRegex(ValueError, message):
                builder.patch_pe(modified, self.code, builder.sha256(modified))
        with self.assertRaisesRegex(ValueError, "fit"):
            builder.patch_pe(original, b"\0" * 1200, builder.sha256(original))

    def test_uniform_cc_linker_padding_is_supported(self):
        original = bytearray(pe_fixture())
        original[724:1536] = b"\xcc" * (1536 - 724)
        modified, report = builder.patch_pe(original, self.code, builder.sha256(original))
        self.assertEqual(modified[576:576 + len(self.code)], self.code)
        self.assertEqual(len(modified), len(original))
        self.assertTrue(report["pe_file_length_unchanged"])

    def test_ffs_checksums_and_known_length_lzma(self):
        file = ffs(section(b"payload", 25), bytes(16), attributes=0x40)
        volume = fv(file, 4096)
        files, end = builder.fv_files(volume, 0)
        self.assertEqual(len(files), 1)
        self.assertEqual(end, 4096)
        broken = bytearray(volume)
        broken[72 + 28] ^= 1
        with self.assertRaisesRegex(ValueError, "data checksum"):
            builder.fv_files(broken, 0)
        reference = known_lzma(volume)
        encoded = builder.encode_lzma(volume, reference)
        self.assertEqual(builder.decode_lzma(encoded), volume)
        self.assertEqual(encoded[:5], reference[:5])
        self.assertEqual(struct.unpack_from("<Q", encoded, 5)[0], len(volume))
        with self.assertRaisesRegex(ValueError, "allocation"):
            builder.decode_lzma(lzma.compress(volume, format=lzma.FORMAT_ALONE))

    def test_nested_fv_roundtrip_only_changes_target_file(self):
        pe = pe_fixture()
        target = ffs(section(pe, 16), builder.DARWIN_GUID)
        neighbor = ffs(section(b"unchanged neighbor", 25), bytes(range(16)))
        target += b"\xff" * (-len(target) % 8)
        inner = fv(target + neighbor, 4096)
        stream = section(b"", 25) + section(inner, 23)
        packed = known_lzma(stream)
        guided = (len(packed) + 24).to_bytes(3, "little") + b"\x02" + builder.LZMA_GUID
        guided += struct.pack("<HH", 24, 1) + packed
        outer_file = ffs(guided, bytes(reversed(range(16))), kind=11)
        original = b"\xff" * 0x8000 + fv(outer_file, 0x600000 - 0x8000)
        modified, patched_pe, report = builder.patch_firmware(
            original, self.code, builder.sha256(pe))
        self.assertEqual(len(modified), len(original))
        self.assertEqual(modified[:0x8000], original[:0x8000])
        self.assertEqual(len(patched_pe), len(pe))
        self.assertTrue(report["other_inner_files_unchanged"])
        self.assertTrue(report["lzma_properties_preserved"])
        self.assertTrue(report["zero_tail_stub_unchanged"])


if __name__ == "__main__":
    unittest.main()
