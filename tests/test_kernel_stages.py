import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
    from chiron_kernel_stages import branch, patch_stages, writable_padding, STAGES, VM_STAGES
    from build_chiron_entry_probe import read_aarch64_object


class KernelStagesTests(unittest.TestCase):
    def test_branch_round_trip_both_directions_and_boundaries(self):
        base = 0xfffffff0073fb05c
        for delta in (0, 4, -4, -(1 << 27), (1 << 27) - 4):
            instruction = branch(base, base + delta)
            displacement = instruction & 0x3ffffff
            if displacement & (1 << 25):
                displacement -= 1 << 26
            self.assertEqual(instruction & 0xfc000000, 0x14000000)
            self.assertEqual(base + displacement * 4, base + delta)
        for delta in (1, -1, 1 << 27, -(1 << 27) - 4):
            with self.assertRaisesRegex(ValueError, "unaligned or out-of-range"):
                branch(base, base + delta)

    def test_unknown_kernel_is_rejected_before_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                patch_stages(b"unknown", Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipUnless(shutil.which("clang"), "clang required")
    def test_assembled_hooks_fit_padding_and_have_only_resolved_references(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = Path(directory) / "stages.o"
            subprocess.run([
                "clang", "--target=aarch64-none-elf",
                "-DCHIRON_ENTRY_DELTA=-6684800", "-DCHIRON_LOW_DELTA=-6680000",
                "-c", str(ROOT / "scripts/chiron-kernel-stages.S"), "-o", str(obj),
            ], check=True)
            program, symbols = read_aarch64_object(obj.read_bytes())
        self.assertLessEqual(len(program), 7312)
        self.assertEqual(symbols["chiron_kernel_stages"], 0)
        self.assertEqual((0x70 + symbols["ChironStageStackTop"]) % 16, 0)
        for number, _, _, _ in STAGES:
            displaced = symbols[f"ChironStage{number}Original"]
            resume = symbols[f"ChironStage{number}Resume"]
            self.assertEqual(resume, displaced + 4)
            self.assertEqual(struct.unpack_from("<I", program, displaced)[0], 0xd503201f)
            self.assertEqual(struct.unpack_from("<I", program, resume)[0], 0x14000000)

    @unittest.skipUnless(shutil.which("clang"), "clang required")
    def test_vm_detail_fits_and_stops_before_page_tables_become_active(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = Path(directory) / "vm-stages.o"
            subprocess.run([
                "clang", "--target=aarch64-none-elf", "-DCHIRON_VM_DETAIL",
                "-DCHIRON_ENTRY_DELTA=-6684800", "-DCHIRON_LOW_DELTA=-6680000",
                "-c", str(ROOT / "scripts/chiron-kernel-stages.S"), "-o", str(obj),
            ], check=True)
            program, symbols = read_aarch64_object(obj.read_bytes())
        self.assertLessEqual(len(program), 7312)
        for number, site, _, _ in VM_STAGES:
            self.assertIn(f"ChironStage{number}", symbols)
            self.assertLess(site, 0xfffffff0073f2fec)

    def test_writable_padding_rejects_sections_nonzero_data_and_wrong_protection(self):
        segment = {
            "name": "__DATA", "address": 0x5000, "file_bytes": 0x1000, "protection": 3,
            "sections": [
                {"address": 0x5000, "bytes": 0x800, "flags": 0},
                {"address": 0x6000, "bytes": 0x1000, "flags": 1},
            ],
        }
        model = SimpleNamespace(segments=[segment], data=bytes(0x3000),
                                file_offset=lambda address: address - 0x4000)
        self.assertEqual(writable_padding(model, 1440), (0x5800, 0x1800))
        segment["sections"][1]["address"] = 0x5a00
        with self.assertRaisesRegex(ValueError, "writable data padding"):
            writable_padding(model, 1440)
        segment["sections"][1]["address"] = 0x6000
        model.data = bytes(0x1800) + b"x" + bytes(0x17ff)
        with self.assertRaisesRegex(ValueError, "writable data padding"):
            writable_padding(model, 1440)
        model.data = bytes(0x3000)
        segment["protection"] = 5
        with self.assertRaisesRegex(ValueError, "writable data padding"):
            writable_padding(model, 1440)

    @unittest.skipUnless(shutil.which("clang"), "clang required")
    def test_post_vm_uses_external_writable_storage_and_fits_code_padding(self):
        delta = 0x50da0
        with tempfile.TemporaryDirectory() as directory:
            obj = Path(directory) / "post-vm.o"
            subprocess.run([
                "clang", "--target=aarch64-none-elf", "-DCHIRON_VM_DETAIL", "-DCHIRON_POST_VM",
                "-DCHIRON_DATA_DELTA=" + str(delta),
                "-DCHIRON_ENTRY_DELTA=-6684800", "-DCHIRON_LOW_DELTA=-6680000",
                "-c", str(ROOT / "scripts/chiron-kernel-stages.S"), "-o", str(obj),
            ], check=True)
            program, symbols = read_aarch64_object(obj.read_bytes())
        self.assertLessEqual(len(program), 7312)
        self.assertEqual(symbols["ChironStageSaved"], delta)
        self.assertEqual(symbols["ChironStageStackTop"], delta + 1296)
        self.assertLess(symbols["ChironStageRecord"] - delta + 56, 1440)
        self.assertIn("ChironStage23", symbols)
        self.assertIn("ChironWindowInstall", symbols)
        self.assertIn("ChironWindowRestore", symbols)


if __name__ == "__main__":
    unittest.main()
