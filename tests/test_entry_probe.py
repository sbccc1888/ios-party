import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("entry_probe_builder", ROOT / "tools/build_chiron_entry_probe.py")
builder = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
    spec.loader.exec_module(builder)
zero_spec = importlib.util.spec_from_file_location("zero_tail_builder", ROOT / "tools/build_chiron_zero_tail_boot.py")
zero_builder = importlib.util.module_from_spec(zero_spec)
with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
    zero_spec.loader.exec_module(zero_builder)


class EntryProbeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("clang"), "clang is required to assemble the diagnostic probe")
    def test_assembles_without_relocations_and_preserves_firmware_body(self):
        with tempfile.TemporaryDirectory() as directory:
            program, halt = builder.compile_probe(Path(directory))
        self.assertGreater(len(program), halt)
        self.assertGreater(halt, 0)
        self.assertLess(len(program), 0x4000 - 64)
        firmware = bytearray(b"\xff" * 0x600000)
        firmware[:64] = bytes(64)
        firmware[:8] = bytes.fromhex("01000010 ff1f0014")
        firmware[56:60] = b"ARM\x64"
        firmware[16:24] = len(firmware).to_bytes(8, "little")
        result = builder.install_probe(firmware, program)
        self.assertEqual(result[0x8000:], firmware[0x8000:])
        self.assertEqual(result[:4], firmware[:4])
        self.assertEqual(result[8:64], firmware[8:64])
        self.assertEqual(result[64:64 + len(program)], program)
        self.assertEqual(int.from_bytes(result[4:8], "little"), 0x1400000f)
        firmware[0x1000] = 0
        with self.assertRaisesRegex(ValueError, "unused erased"):
            builder.install_probe(firmware, program)

    @unittest.skipUnless(shutil.which("clang"), "clang is required to assemble the handoff stub")
    def test_zero_tail_variant_assembles_without_relocations(self):
        with tempfile.TemporaryDirectory() as directory:
            program, halt = builder.compile_probe(Path(directory), defines=("CHIRON_ZERO_TAIL",))
        self.assertGreater(len(program), halt)
        self.assertGreater(halt, 0)
        self.assertLess(len(program), 0x4000 - 64)

    def test_zero_tail_requires_exact_geometry_and_never_discards_nonzero_data(self):
        volume = bytearray(zero_builder.LOGICAL_BYTES)
        volume[510:512] = b"\x55\xaa"
        volume[11:13] = b"\x00\x02"
        volume[16] = 2
        volume[71:82] = b"CHIRONESP  "
        volume[82:90] = b"FAT32   "
        volume[32:36] = (len(volume) // 512).to_bytes(4, "little")
        prefix = zero_builder.require_zero_tail(volume)
        self.assertEqual(len(prefix), zero_builder.PHYSICAL_BYTES)
        self.assertEqual(prefix + bytes(zero_builder.LOGICAL_BYTES - len(prefix)), volume)
        volume[-1] = 1
        with self.assertRaisesRegex(ValueError, "nonzero data"):
            zero_builder.require_zero_tail(volume)
        volume[-1] = 0
        volume[71] = 0
        with self.assertRaisesRegex(ValueError, "geometry"):
            zero_builder.require_zero_tail(volume)

    def test_zero_tail_range_rejects_reserved_or_unmeasured_memory(self):
        profile = {
            "device": "chiron",
            "memblock_memory": [{"base": 0x40000000, "end_exclusive": 0x85800000}],
            "memblock_reserved": [], "static_reserved_regions": {},
        }
        zero_builder.require_measured_range(profile)
        profile["memblock_reserved"].append({"base": 0x46000000, "end_exclusive": 0x46100000})
        with self.assertRaisesRegex(ValueError, "reserved region"):
            zero_builder.require_measured_range(profile)
        profile["memblock_reserved"] = []
        profile["memblock_memory"][0]["end_exclusive"] = 0x45000000
        with self.assertRaisesRegex(ValueError, "outside"):
            zero_builder.require_measured_range(profile)


if __name__ == "__main__":
    unittest.main()
