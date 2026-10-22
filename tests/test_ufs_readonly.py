"""Offline policy/resource/transport verification; never a device qualification."""
import copy
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import chiron_ufs_readonly as ufs

DRIVER = ROOT / "drivers/ChironUfsReadonly"
GPT = ROOT / "research/artifacts/chiron-live-details-20261020/sda-primary-gpt.bin"
TREE = ROOT / "research/artifacts/chiron-live-20261020/runtime-tree.tar"


class PolicyTests(unittest.TestCase):
    def run_host(self, argument):
        compiler = shutil.which("clang++") or shutil.which("c++")
        self.assertIsNotNone(compiler, "a C++ compiler is required")
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "transport"
            subprocess.run([compiler, "-std=c++14", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", str(DRIVER / "tests/Transport.cpp"),
                            "-o", str(exe)], check=True)
            return subprocess.run([str(exe), str(argument)], check=True, capture_output=True, text=True).stdout

    def test_host_policy_without_capture(self):
        output = self.run_host("--policy-only")
        self.assertIn("PASS: policy-only:", output)
        self.assertIn("zero MMIO/cache/DMA changes", output)
        self.assertNotIn("18 transport cases", output)

    def test_host_transport_and_policy(self):
        if not GPT.exists():
            self.skipTest("private saved GPT capture unavailable; no synthetic success substituted")
        self.assertIn("18 transport cases", self.run_host(GPT))

    def test_arm64_core_compile(self):
        compiler = shutil.which("clang++")
        self.assertIsNotNone(compiler, "clang++ required for ARM64 compile check")
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([
                compiler, "-target", "arm64-apple-darwin", "-std=c++14", "-ffreestanding",
                "-fno-exceptions", "-fno-rtti", "-Wall", "-Wextra", "-Werror",
                "-c", str(DRIVER / "tests/CoreCompile.cpp"), "-o", str(Path(temp) / "core.o"),
            ], check=True)

    def test_compile_record_rewrite(self):
        spec = importlib.util.spec_from_file_location("build_ufs", ROOT / "ci/build_chiron_ufs_readonly.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        record = {"file": "/tree/IOCPU.cpp",
                  "arguments": ["clang++", "-DKERNEL", "-c", "/tree/IOCPU.cpp", "-o", "old.o",
                                "-MJ", "old.json", "-MF", "old.d", "-serialize-diagnostics", "old.dia"]}
        args = build.compile_arguments(record, Path("/repo/ChironUfsReadonly.cpp"), Path("/out/ufs.o"))
        self.assertEqual(args, ["clang++", "-DKERNEL", "-c", "/repo/ChironUfsReadonly.cpp",
                                "-o", "/out/ufs.o", "-MJ", "/out/ufs.compile.json"])

    def test_devices_rejected_by_file_tool(self):
        with self.assertRaisesRegex(ValueError, "regular files"):
            ufs.saved_file("/dev/null")


@unittest.skipUnless(TREE.exists() and GPT.exists(), "private saved captures are unavailable")
class ResourceTests(unittest.TestCase):
    def test_actual_capture_and_gpt_match(self):
        report = ufs.inspect_capture(ROOT, GPT)
        self.assertEqual(report["resources"]["lanes"], 2)
        self.assertEqual(len(report["resources"]["clocks"]), 9)
        self.assertFalse(report["resources"]["ufs_iommus_present"])
        self.assertTrue(report["probe_gpt_exact_match"])
        self.assertFalse(report["hardware_execution_allowed"])
        self.assertEqual(report["expected_last_lba"], 29513727)

    def test_resource_mismatch_rejected(self):
        nodes = ufs.runtime_nodes(TREE.read_bytes())
        for field, value in (("reg", bytes.fromhex("01d8400000003000")),
                             ("lanes-per-direction", bytes.fromhex("00000001")),
                             ("phys", bytes.fromhex("00000000"))):
            changed = copy.deepcopy(nodes)
            changed[ufs.HOST][field] = value
            with self.assertRaises(ValueError, msg=field):
                ufs.inspect_resources(changed)

    def test_corrupt_saved_probe_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            changed = bytearray(GPT.read_bytes())
            changed[8192] ^= 1
            path = Path(temp) / "probe.bin"
            path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, "GPT bytes differ"):
                ufs.inspect_capture(ROOT, path)


if __name__ == "__main__":
    unittest.main()
