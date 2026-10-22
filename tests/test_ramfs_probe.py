"""Signed host fixture and synthetic SVC checks; never XNU pagein qualification."""

import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


BUILD = module("ramfs_runtime_build_test", "ci/build_chiron_ramfs_probe.py")
ABI = BUILD.ABI
RAM = BUILD.RAM
RAMFS = BUILD.RAMFS
VALIDATE = module("ramfs_runtime_validator_test", "ci/validate_chiron_abi_probe.py")
ABI_TESTS = module("ramfs_runtime_svc_fixture", "tests/test_abi_probe.py")
unicorn, REG = ABI_TESTS.unicorn, getattr(ABI_TESTS, "REG", None)


class RamfsSvcFixture(ABI_TESTS.ProbeFixture):
    """Real ARM64 instructions, host CHRAMFS lookup, synthetic kernel SVC answers."""

    def __init__(self, directory, fault=None):
        super().__init__(directory, fault=fault)
        self.root = (directory / "root.img").read_bytes()
        self.opened_path = None
        self.mapped_bytes = None

    def svc(self, machine, interrupt, unused):
        number = machine.reg_read(REG.UC_ARM64_REG_X16)
        stage = self.values()["failure_stage"]
        if number == 5:
            path = self.cstring(machine.reg_read(REG.UC_ARM64_REG_X0))
            if path != b"/dev/console":
                if interrupt != 2 or machine.reg_read(REG.UC_ARM64_REG_SP) & 15:
                    raise AssertionError("invalid syscall/alignment")
                self.opened_path = path.decode("ascii")
                if self.opened_path != BUILD.CHILD_PATH:
                    raise AssertionError("wrapper used the legacy file path")
                RAMFS.read_file(self.root, self.opened_path)
                self.calls.append((5, stage, []))
                self.reply(4)
                return
        if number == 197 and stage == 61:
            if interrupt != 2 or machine.reg_read(REG.UC_ARM64_REG_SP) & 15:
                raise AssertionError("invalid syscall/alignment")
            args = [machine.reg_read(getattr(REG, f"UC_ARM64_REG_X{i}")) for i in range(8)]
            size = self.report["artifacts"]["child.macho"]["bytes"]
            if args[:6] != [0, RAM.align(size), 1, 2, 4, 0]:
                raise AssertionError("wrong RAMFS file-mapping arguments")
            path = "/probe.bin" if self.fault == "alias_decoy" else self.opened_path
            child = bytearray(RAMFS.read_file(self.root, path))
            if self.fault == "later_page":
                child[8192 + 19] ^= 1
            elif self.fault == "last_byte":
                child[-1] ^= 1
            machine.mem_map(0x61000000, RAM.align(size))
            machine.mem_write(0x61000000, bytes(child))
            machine.mem_protect(0x61000000, RAM.align(size), unicorn.UC_PROT_READ)
            self.mapped_bytes = bytes(child)
            self.calls.append((197, stage, args))
            self.reply(0x61000000)
            return
        super().svc(machine, interrupt, unused)

    def run(self):
        # Whole-file byte accumulation needs more instructions than the default probe.
        self.machine.emu_start(self.addresses["_start"], 0, count=1_000_000)
        if not self.reached:
            raise AssertionError("RAMFS wrapper did not reach _checkpoint within instruction bound")
        return self


class RootTests(unittest.TestCase):
    def test_exact_paths_distinct_contents_and_metadata(self):
        image = BUILD.make_root(b"OWN MAIN\n", b"OWN CHILD\n")
        records = BUILD.validate_root(image, b"OWN MAIN\n", b"OWN CHILD\n")
        paths = {entry["path"]: entry for entry in records}
        self.assertEqual(len(records), 9)
        self.assertEqual(RAMFS.read_file(image, BUILD.CHILD_PATH), b"OWN CHILD\n")
        self.assertEqual(RAMFS.read_file(image, "/probe.bin"), BUILD.DECOY)
        self.assertNotEqual(paths["/probe.bin"]["offset"], paths[BUILD.CHILD_PATH]["offset"])
        self.assertNotEqual(paths["/usr"]["parent"], paths["/usr/lib"]["parent"])
        with self.assertRaises(FileNotFoundError):
            RAMFS.read_file(image, "/usr/probe.bin")
        corrupted = bytearray(image)
        corrupted[paths[BUILD.CHILD_PATH]["offset"]] ^= 1
        with self.assertRaisesRegex(ValueError, "readback"):
            BUILD.validate_root(corrupted, b"OWN MAIN\n", b"OWN CHILD\n")
        with self.assertRaises(ValueError):
            BUILD.make_root(b"", b"OWN CHILD\n")
        with self.assertRaises(ValueError):
            BUILD.make_root(b"OWN MAIN\n", BUILD.DECOY)

    def test_macro_readiness_is_explicit(self):
        BUILD.require_ramfs_macro()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "not-ready.S"
            source.write_text("#define CHIRON_ABI_CHILD_SIZE 1\n")
            with self.assertRaisesRegex(ValueError, "not ready"):
                BUILD.require_ramfs_macro(source)

    def test_normal_output_and_existing_abi_report_are_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            for destination in (
                tree / "build/native-user-fixture",
                tree / "build/native-user-fixture/nested",
                tree / "build",
            ):
                with self.assertRaisesRegex(ValueError, "separate"):
                    BUILD.check_destination(destination, tree)
            output = tree / "separate-abi"
            output.mkdir()
            original = json.dumps({"purpose": "original ABI", "root_format": "N4KROOT"})
            (output / BUILD.REPORT).write_text(original)
            with self.assertRaisesRegex(ValueError, "existing ABI"):
                BUILD.build(output)
            self.assertEqual((output / BUILD.REPORT).read_text(), original)
            own = {"purpose": BUILD.PURPOSE, "root_format": "CHRAMFS"}
            (output / BUILD.REPORT).write_text(json.dumps(own))
            with mock.patch.object(BUILD, "require_ramfs_macro", side_effect=ValueError("not ready")):
                with self.assertRaisesRegex(ValueError, "not ready"):
                    BUILD.build(output)
            self.assertFalse((output / BUILD.REPORT).exists(), "failed rebuild retained stale report")


@unittest.skipUnless(shutil.which("xcrun") and shutil.which("codesign") and ABI.SOURCE.is_dir(),
                     "Apple build tools and pinned local ABI source are required")
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="chiron-ramfs-probe-test-")
        cls.directory = Path(cls.temporary.name)
        process = subprocess.run([
            sys.executable, "-B", str(ROOT / "ci/build_chiron_ramfs_probe.py"),
            "--output", str(cls.directory),
        ], capture_output=True, text=True)
        if process.returncode:
            cls.temporary.cleanup()
            raise AssertionError(process.stdout + process.stderr)
        cls.report = json.loads((cls.directory / BUILD.REPORT).read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_final_artifacts_signatures_root_and_abi(self):
        report = self.report
        self.assertEqual(report["result_abi"], ABI.RESULT_ABI)
        self.assertEqual(report["root_format"], "CHRAMFS")
        self.assertEqual((report["root_version"], report["root_file_count"], report["root_node_count"]),
                         (1, 3, 9))
        self.assertEqual(report["root_paths"], BUILD.ROOT_PATHS)
        self.assertEqual(report["root_directories"], list(BUILD.DIRECTORIES))
        self.assertTrue(report["whole_file_readback_requested"])
        self.assertEqual(report["whole_file_readback_failure_stage"], 65)
        for name in ("qemu_runtime_qualified", "hardware_tested", "kernel_compiled",
                     "ramfs_mount_verified", "file_pagein_verified", "whole_file_readback_runtime_verified"):
            self.assertFalse(report[name])
        for name, record in report["artifacts"].items():
            data = (self.directory / name).read_bytes()
            self.assertEqual((RAM.sha(data), len(data)), (record["sha256"], record["bytes"]), name)
        for name, digest in report["source_sha256"].items():
            self.assertEqual(RAM.sha((ROOT / name).read_bytes()), digest, name)
        launchd = (self.directory / "launchd.macho").read_bytes()
        child = (self.directory / "child.macho").read_bytes()
        root = (self.directory / "root.img").read_bytes()
        records = BUILD.validate_root(root, launchd, child)
        self.assertEqual(sum(e["kind"] == "file" for e in records), 3)
        self.assertEqual(report["file_byte_sum"], sum(child) & ((1 << 64) - 1))
        self.assertIn(f"-DCHIRON_ABI_FILE_SUM=0x{report['file_byte_sum']:016x}", report["wrapper_defines"])
        self.assertEqual(report["child_marker_offset"], RAM.marker_offset(child))
        self.assertEqual(report["file_map_bytes"], RAM.align(len(child)))
        self.assertIn(BUILD.CHILD_PATH.encode() + b"\0", launchd)
        for name, data in (("launchd", launchd), ("child", child)):
            RAM.audit_static(data)
            self.assertEqual(RAM.signature_info(data), report["signatures"][name])
            subprocess.run(["codesign", "--verify", "--strict", str(self.directory / (name + ".macho"))],
                           check=True, capture_output=True)
            damaged = bytearray(data)
            damaged[4096] ^= 1
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                RAM.signature_info(damaged)
        trust = (self.directory / "trustcache.bundle").read_bytes()
        self.assertEqual(trust, RAM.make_trustcache(
            [report["signatures"][name]["cdhash"] for name in ("launchd", "child")]))
        addresses = json.loads((self.directory / "addresses.json").read_text())
        self.assertTrue(BUILD.COMPAT_SYMBOLS <= addresses.keys())
        self.assertEqual(addresses["_chiron_abi_results_end"] - addresses["_chiron_abi_results"], 256)
        with self.assertRaises(ValueError):
            RAM.validate_root(root, launchd, child)

    def test_prepared_normal_tree_preserves_d_source_verification(self):
        with tempfile.TemporaryDirectory(prefix="chiron-ramfs-prepared-") as temp:
            tree = Path(temp)
            source = tree / ".cache/xnu-work"
            files = [*ABI.PINNED_SOURCE, "osfmk/arm64/sleh.c"]
            files += ["bsd/miscfs/mockfs/" + name for name in RAM.MOCKFS_HASHES]
            for name in files:
                destination = source / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ABI.SOURCE / name, destination)
            report = BUILD.build(tree / "build/ramfs-probe", tree=tree)
            self.assertEqual(report["abi_source_mode"], "prepared-tree")
            self.assertEqual(report["abi_source_root"], str(source.resolve()))
            self.assertEqual(report["abi_source_sha256"], ABI.PINNED_SOURCE)
            self.assertEqual(report["mockfs_source_sha256"], RAM.MOCKFS_HASHES)
            self.assertTrue(report["mockfs_source_verified"])
            self.assertEqual(RAM.verify_mockfs(tree), RAM.MOCKFS_HASHES)
            self.assertFalse(report["ramfs_mount_verified"])

    def test_failed_wrapper_rebuild_does_not_leave_seed_pass_report(self):
        original = RAM.build_program

        def fail_wrapper(source, *args, **kwargs):
            if source == BUILD.WRAPPER:
                raise ValueError("deliberate wrapper compile failure")
            return original(source, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="chiron-ramfs-failure-") as temp:
            output = Path(temp)
            with mock.patch.object(RAM, "build_program", side_effect=fail_wrapper):
                with self.assertRaisesRegex(ValueError, "deliberate wrapper"):
                    BUILD.build(output)
            self.assertTrue((output / "child.macho").exists())
            self.assertFalse((output / BUILD.REPORT).exists(),
                             "intermediate N4KROOT report could be mistaken for RAMFS success")

    @unittest.skipUnless(unicorn, "existing Unicorn required; no emulator installed by this task")
    def test_wrapper_instruction_success_and_generic_result_validator(self):
        fixture = RamfsSvcFixture(self.directory).run()
        result = VALIDATE.validate(fixture.observation(), self.report)
        self.assertTrue(result["abi_probe_passed"])
        self.assertFalse(result["kernel_artifact_binding_checked"])
        self.assertEqual(fixture.opened_path, BUILD.CHILD_PATH)
        self.assertEqual(fixture.mapped_bytes, (self.directory / "child.macho").read_bytes())
        self.assertEqual(fixture.values()["passed_mask"], 127)
        self.assertEqual(fixture.values()["completed_stage"], 64)
        self.assertEqual(fixture.beats, 2)
        self.assertIn(b"CHIRON ABI PROBE PASS\n", fixture.output)

    @unittest.skipUnless(unicorn, "existing Unicorn required; no emulator installed by this task")
    def test_wrapper_later_page_and_final_byte_corruption_fail_at_65(self):
        for fault in ("later_page", "last_byte"):
            with self.subTest(fault=fault):
                fixture = RamfsSvcFixture(self.directory, fault=fault).run()
                self.assertEqual(fixture.values()["failure_stage"], 65)
                self.assertEqual(fixture.values()["passed_mask"], 95)
                self.assertNotIn(b"CHIRON ABI PROBE PASS\n", fixture.output)
                with self.assertRaisesRegex(ValueError, "stage 65"):
                    VALIDATE.validate(fixture.observation(), self.report)

    @unittest.skipUnless(unicorn, "existing Unicorn required; no emulator installed by this task")
    def test_path_alias_to_root_decoy_fails_header_assertion(self):
        fixture = RamfsSvcFixture(self.directory, fault="alias_decoy").run()
        self.assertEqual(fixture.values()["failure_stage"], 62)
        self.assertEqual(fixture.values()["passed_mask"], 95)
        self.assertNotIn(b"CHIRON ABI PROBE PASS\n", fixture.output)
        with self.assertRaisesRegex(ValueError, "stage 62"):
            VALIDATE.validate(fixture.observation(), self.report)


if __name__ == "__main__":
    unittest.main()
