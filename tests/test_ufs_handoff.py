"""Host-only UFS evidence validation. Model claims never authorize hardware."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import chiron_ufs_handoff as handoff

CORE = ROOT / "drivers/ChironUfsHandoff/tests/Core.cpp"


def model():
    stamp = dict(boot_id="45" + "00" * 15, generation=3, ticks=120, phase="kernel_post_ebs")
    lease = dict(token=7, cpu=0x80000000, bus=0x90000000, bytes=32768,
                 address_bits=32, cache_line=64, cached=True, device_read=True,
                 device_write=True, retained_until_boot_end=True)
    context = dict(current=stamp, ebs_returned_ticks=100, max_age_ticks=20,
                   held_lease=lease, exit_boot_services_returned=True)
    evidence = dict(abi=1, resources=dict(handoff.RESOURCES), stamp=copy.deepcopy(stamp),
                    lease=copy.deepcopy(lease), facts={
                        name: dict(state="observed", stamp=copy.deepcopy(stamp))
                        for name in handoff.FACTS})
    return evidence, context


class MetadataTests(unittest.TestCase):
    def test_complete_model_never_qualifies_board(self):
        report = handoff.validate_metadata(*model())
        self.assertEqual(report["status"], "metadata_consistent")
        self.assertEqual(report["verified"], 0)
        self.assertFalse(report["hardware_execution_allowed"])
        self.assertFalse(report["board_handoff_qualified"])
        self.assertFalse(report["trusted_board_producer_present"])

    def test_every_missing_unknown_and_failed_fact(self):
        for name in handoff.FACTS:
            with self.subTest(name=name):
                evidence, context = model()
                del evidence["facts"][name]
                report = handoff.validate_metadata(evidence, context)
                self.assertEqual(report["unknown"], [name])
                self.assertEqual(report["status"], "missing")
                evidence["facts"][name] = dict(state="unknown", stamp=None)
                self.assertEqual(handoff.validate_metadata(evidence, context)["status"], "missing")
                evidence["facts"][name] = dict(state="failed", stamp=evidence["stamp"])
                self.assertEqual(handoff.validate_metadata(evidence, context)["status"], "failed_observation")

    def test_every_wrong_resource_rejected(self):
        for name in handoff.RESOURCES:
            evidence, context = model()
            evidence["resources"][name] ^= 1
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "resource mismatch"):
                handoff.validate_metadata(evidence, context)

    def test_boot_generation_phase_and_time_of_each_fact(self):
        mutations = [("boot_id", "46" + "00" * 15), ("generation", 4),
                     ("phase", "exit_callback"), ("phase", "firmware"),
                     ("ticks", 99), ("ticks", 121)]
        for name in handoff.FACTS:
            for field, value in mutations:
                evidence, context = model()
                evidence["facts"][name]["stamp"][field] = value
                with self.subTest(name=name, field=field, value=value), self.assertRaises(ValueError):
                    handoff.validate_metadata(evidence, context)

    def test_packet_replay_and_expiry_rejected(self):
        for field, value in (("boot_id", "46" + "00" * 15), ("generation", 4),
                             ("ticks", 99), ("ticks", 121), ("phase", "exit_callback")):
            evidence, context = model()
            evidence["stamp"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                handoff.validate_metadata(evidence, context)
        evidence, context = model()
        context["current"]["ticks"] = 141
        with self.assertRaisesRegex(ValueError, "stale"):
            handoff.validate_metadata(evidence, context)

    def test_lease_binding_and_lifetime_rejected(self):
        for field, value in (
                ("retained_until_boot_end", False), ("device_write", False),
                ("device_read", False), ("cached", False), ("token", 8),
                ("bytes", 28672), ("bus", 0x100000000), ("bus", (1 << 64) - 4096),
                ("cpu", 0x80000001), ("cache_line", 128), ("address_bits", 0)):
            evidence, context = model()
            evidence["lease"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                handoff.validate_metadata(evidence, context)

    def test_ebs_callback_is_not_successful_return(self):
        evidence, context = model()
        context["exit_boot_services_returned"] = False
        with self.assertRaisesRegex(ValueError, "return is not confirmed"):
            handoff.validate_metadata(evidence, context)

    def test_malformed_json_and_type_confusion_rejected(self):
        for field, value in (("abi", True), ("abi", 2)):
            evidence, context = model()
            evidence[field] = value
            with self.assertRaises(ValueError):
                handoff.validate_metadata(evidence, context)
        evidence, context = model()
        evidence["resources"]["irq_flags"] = False
        with self.assertRaises(ValueError):
            handoff.validate_metadata(evidence, context)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"abi": 1, "abi": 1}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                handoff.load_json(path)

    def test_device_fifo_and_symlink_inputs_rejected(self):
        with self.assertRaisesRegex(ValueError, "regular"):
            handoff.regular_bytes("/dev/null")
        with tempfile.TemporaryDirectory() as temp:
            fifo = Path(temp) / "fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "regular"):
                handoff.regular_bytes(fifo)
            path = Path(temp) / "file"
            path.write_text("{}")
            symlink = Path(temp) / "symlink"
            symlink.symlink_to(path)
            with self.assertRaises(OSError):
                handoff.regular_bytes(symlink)

    def test_cli_rejection_overwrites_old_success_report(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            evidence, context = model()
            evidence["stamp"]["generation"] = 1
            for name, data in (("evidence", evidence), ("context", context)):
                (directory / f"{name}.json").write_text(json.dumps(data))
            output = directory / "report.json"
            output.write_text('{"status":"old_success"}')
            run = subprocess.run([
                sys.executable, str(ROOT / "tools/chiron_ufs_handoff.py"),
                "--evidence", str(directory / "evidence.json"),
                "--context", str(directory / "context.json"), "--output", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "rejected")
            self.assertFalse(report["hardware_execution_allowed"])

    def test_local_resource_audit(self):
        capture = ROOT / "research/artifacts/chiron-live-20261020/runtime-tree.tar"
        if not capture.exists():
            self.skipTest("private saved Android capture unavailable")
        report = handoff.audit()
        self.assertEqual(report["resource_capture"]["resources"]["lanes"], 2)
        self.assertEqual(report["status"], "blocked_no_trusted_board_producer")
        self.assertFalse(report["mmio_accessed"])
        self.assertFalse(report["board_handoff_qualified"])


class CoreTests(unittest.TestCase):
    def test_host_core_with_sanitizers(self):
        compiler = shutil.which("clang++")
        self.assertIsNotNone(compiler, "clang++ required")
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "core"
            subprocess.run([compiler, "-std=c++14", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", str(CORE), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_arm64_freestanding_core_compile(self):
        compiler = shutil.which("clang++")
        self.assertIsNotNone(compiler, "clang++ required")
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([
                compiler, "-target", "arm64-apple-darwin", "-std=c++14",
                "-ffreestanding", "-nostdinc++", "-fno-exceptions", "-fno-rtti",
                "-Wall", "-Wextra", "-Werror", "-DCHIRON_HANDOFF_COMPILE_ONLY",
                "-c", str(CORE), "-o", str(Path(temp) / "core.o"),
            ], check=True)


if __name__ == "__main__":
    unittest.main()
