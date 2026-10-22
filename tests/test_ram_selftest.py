"""Static build and actual ARM64 control flow; SVC responses are host fixtures.

These tests do NOT run XNU or qualify a kernel, scheduler, timer or device.
Use PYTHONPATH for an existing Unicorn install to enable instruction tests.
"""

import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ram_selftest", ROOT / "ci/build_ram_selftest.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)

try:
    import unicorn
    import unicorn.arm64_const as REG
except ImportError:
    unicorn = None


class ContainerTests(unittest.TestCase):
    def test_two_file_layout_and_bad_inputs(self):
        launchd, child = b"L" * 8193, b"C" * 4097
        image = BUILD.make_root(launchd, child)
        self.assertEqual(struct.unpack_from("<8sII4Q", image),
                         (b"N4KROOT\0", 1, 2, 4096, 8193, 16384, 4097))
        BUILD.validate_root(image, launchd, child)
        for offset in (0, 8, 12, 16, 24, 32, 40, 4096, 16384, len(image) - 1):
            changed = bytearray(image)
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                BUILD.validate_root(changed, launchd, child)
        with self.assertRaises(ValueError):
            BUILD.make_root(b"", child)
        with self.assertRaises(ValueError):
            BUILD.validate_root(image[:-1], launchd, child)

    def test_two_distinct_sorted_trust_entries(self):
        hashes = ["aa" * 20, "11" * 20]
        bundle = BUILD.make_trustcache(hashes)
        self.assertEqual(struct.unpack_from("<II", bundle), (1, 8))
        self.assertEqual(struct.unpack_from("<I", bundle, 8), (2,))
        self.assertEqual(struct.unpack_from("<I", bundle, 28), (2,))
        self.assertEqual(bundle[32:52], bytes.fromhex(hashes[1]))
        self.assertEqual(bundle[56:76], bytes.fromhex(hashes[0]))
        with self.assertRaises(ValueError):
            BUILD.make_trustcache([hashes[0]] * 2)

    def test_source_hash_guard_rejects_changed_mockfs(self):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            directory = tree / ".cache/xnu-work/bsd/miscfs/mockfs"
            directory.mkdir(parents=True)
            for name in BUILD.MOCKFS_HASHES:
                (directory / name).write_bytes(b"unexpected source")
            with self.assertRaises(ValueError):
                BUILD.verify_mockfs(tree)


class Arm64Fixture:
    """Runs signed image instructions against explicit synthetic syscall answers."""

    def __init__(self, directory, fault=None, child=False, data=None):
        self.directory, self.fault, self.child = directory, fault, child
        name = "child.macho" if child else "launchd.macho"
        self.data = data if data is not None else (directory / name).read_bytes()
        address_file = "child-addresses.json" if child else "addresses.json"
        self.symbols = json.loads((directory / address_file).read_text())
        self.machine = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        for cmd, at, _ in BUILD.load_commands(self.data):
            if cmd != 0x19:
                continue
            address, size, offset, length = struct.unpack_from("<4Q", self.data, at + 24)
            if not address or not size:
                continue
            self.machine.mem_map(address, BUILD.align(size))
            if length:
                self.machine.mem_write(address, self.data[offset:offset + length])
        self.machine.mem_map(0x70000000, 0x10000)
        self.machine.reg_write(REG.UC_ARM64_REG_SP, 0x7000FF00)
        self.machine.reg_write(REG.UC_ARM64_REG_PSTATE, 0)
        self.machine.hook_add(unicorn.UC_HOOK_INTR, self.svc)
        self.machine.hook_add(unicorn.UC_HOOK_CODE, self.checkpoint)
        self.calls, self.output, self.child_calls = [], bytearray(), []
        self.waits, self.sleeps, self.child_exit = 0, 0, None
        self.checkpoint_reached = False
        self.mapped = False
        self.corrupted_mapping = False

    def value(self, name, count=1):
        return struct.unpack("<" + "Q" * count, self.machine.mem_read(self.symbols[name], count * 8))

    def checkpoint(self, machine, address, size, _):
        if not self.child and address == self.symbols["_checkpoint"]:
            self.checkpoint_reached = True
            machine.emu_stop()
        if (not self.child and self.fault == "mmap_rw_corrupt" and self.mapped
                and not self.corrupted_mapping and self.value("_chiron_selftest_results", 8)[3] == 22):
            machine.mem_write(0x60001000, b"\xff")
            self.corrupted_mapping = True

    def reply(self, result=0, error=False):
        self.machine.reg_write(REG.UC_ARM64_REG_X0, result & ((1 << 64) - 1))
        flags = self.machine.reg_read(REG.UC_ARM64_REG_NZCV) & ~(1 << 29)
        self.machine.reg_write(REG.UC_ARM64_REG_NZCV, flags | (int(error) << 29))

    def cstring(self, address):
        value = bytearray()
        for index in range(1024):
            byte = self.machine.mem_read(address + index, 1)[0]
            if not byte:
                return bytes(value)
            value.append(byte)
        raise AssertionError("unterminated string")

    def svc(self, machine, interrupt, _):
        self.assert_svc(interrupt)
        number = machine.reg_read(REG.UC_ARM64_REG_X16)
        args = [machine.reg_read(getattr(REG, f"UC_ARM64_REG_X{i}")) for i in range(6)]
        self.calls.append(number)
        if number == 20:
            self.reply(1 if not self.child or self.fault == "child_pid_one" else 42)
        elif number == 39:
            self.reply(9 if self.fault == "child_wrong_parent" else 1)
        elif number == 202:
            machine.mem_write(args[2], struct.pack("<Q", 4096))
            machine.mem_write(args[3], struct.pack("<Q", 4))
            self.reply()
        elif number == 5:
            path = self.cstring(args[0])
            if path == b"/probe.bin" and self.fault == "missing_child":
                self.reply(2, True)
            else:
                if path not in (b"/probe.bin", b"/dev/console"):
                    raise AssertionError(path)
                self.reply(4 if path == b"/probe.bin" else 3)
        elif number in (3, 153):
            child = bytearray((self.directory / "child.macho").read_bytes())
            report = json.loads((self.directory / "selftest-build.json").read_text())
            if self.fault == "bad_marker":
                child[report["child_marker_offset"]] ^= 1
            offset = args[3] if number == 153 else 0
            data = child[offset:offset + args[2]]
            if self.fault == "short_read" and number == 3:
                data = data[:-1]
            if self.fault == "false_eof" and number == 153 and offset == len(child):
                data = b"\x01"
            machine.mem_write(args[1], bytes(data))
            self.reply(len(data))
        elif number == 197:
            if self.fault == "mmap_error":
                self.reply(12, True)
            else:
                if args[1:6] != [8192, 3, 0x1002, (1 << 64) - 1, 0]:
                    raise AssertionError(args)
                machine.mem_map(0x60000000, 8192)
                if self.fault == "mmap_not_zero":
                    machine.mem_write(0x60001000, b"\x01")
                self.mapped = True
                self.reply(0x60000000)
        elif number == 73:
            expected = b"".join(struct.pack("<Q", offset ^ 0x5197) for offset in range(0, 8192, 8))
            if bytes(machine.mem_read(args[0], args[1])) != expected:
                raise AssertionError("actual stores did not fill both pages")
            if self.fault == "munmap_error":
                self.reply(22, True)
            else:
                machine.mem_unmap(args[0], args[1])
                self.mapped = False
                self.reply()
        elif number == 244:
            if self.fault == "spawn_error":
                self.reply(8, True)
                return
            if args[2] != 0 or self.cstring(args[1]) != b"/probe.bin":
                raise AssertionError("unexpected spawn descriptor/path")
            argv = struct.unpack("<2Q", machine.mem_read(args[3], 16))
            if self.cstring(argv[0]) != b"/probe.bin" or argv[1] != 0:
                raise AssertionError("invalid argv")
            child = Arm64Fixture(self.directory, fault=self.fault, child=True)
            child.run()
            self.child_calls = child.calls
            self.output.extend(child.output)
            self.child_exit = child.child_exit
            machine.mem_write(args[0], struct.pack("<I", 42))
            self.reply()
        elif number == 7:
            self.waits += 1
            if args[0] != 42:
                raise AssertionError("wait did not target spawned pid")
            if self.waits == 1:
                if self.fault == "wait_error":
                    self.reply(4, True)
                    return
                machine.mem_write(args[1], struct.pack("<I", self.child_exit << 8))
                self.reply(99 if self.fault == "wrong_wait_pid" else 42)
            elif self.fault == "not_reaped":
                self.reply()
            else:
                self.reply(10, True)
        elif number == 4:
            data = bytes(machine.mem_read(args[1], args[2]))
            if self.fault == "short_console" and b"PASS file" in data:
                self.reply(args[2] - 1)
            else:
                self.output.extend(data)
                self.reply(args[2])
        elif number == 6:
            self.reply(9, True) if self.fault == "file_close_error" and args[0] == 4 else self.reply()
        elif number == 93:
            if args[:4] != [0, 0, 0, 0] or struct.unpack("<2Q", machine.mem_read(args[4], 16)) != (1, 0):
                raise AssertionError("select did not request one-second timeout")
            self.sleeps += 1
            if self.sleeps >= 4:
                machine.emu_stop()
            self.reply(4, True) if self.fault == "timer_error" else self.reply()
        elif number == 1 and self.child:
            self.child_exit = args[0]
            machine.emu_stop()
        else:
            raise AssertionError(f"unexpected syscall {number}")

    @staticmethod
    def assert_svc(interrupt):
        if interrupt != 2:
            raise AssertionError(f"unexpected ARM64 exception {interrupt}")

    def run(self):
        self.machine.emu_start(self.symbols["_start"], 0, count=200_000)
        return self


@unittest.skipUnless(shutil.which("xcrun") and shutil.which("codesign"), "Apple tools required")
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="chiron-selftest-test-")
        cls.directory = Path(cls.temporary.name)
        subprocess.run([sys.executable, str(ROOT / "ci/build_ram_selftest.py"),
                        "--output", str(cls.directory)], check=True, capture_output=True, text=True)
        cls.report = json.loads((cls.directory / "selftest-build.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_signed_binaries_exact_root_hashes_and_abi(self):
        launchd = (self.directory / "launchd.macho").read_bytes()
        child = (self.directory / "child.macho").read_bytes()
        root = (self.directory / "root.img").read_bytes()
        BUILD.validate_root(root, launchd, child)
        self.assertNotEqual(launchd, child)
        self.assertEqual(BUILD.marker_offset(child), self.report["child_marker_offset"])
        self.assertEqual(self.report["result_abi"]["fields"], list(BUILD.RESULT_FIELDS))
        self.assertEqual(self.report["result_abi"]["wait_status"], 37 << 8)
        for name, record in self.report["artifacts"].items():
            self.assertEqual(BUILD.sha((self.directory / name).read_bytes()), record["sha256"])
        for name, data in (("launchd", launchd), ("child", child)):
            BUILD.audit_static(data)
            self.assertEqual(BUILD.signature_info(data), self.report["signatures"][name])
            changed = bytearray(data)
            changed[4096] ^= 1
            with self.assertRaisesRegex(ValueError, "page hash mismatch"):
                BUILD.signature_info(changed)
        self.assertFalse(self.report["qemu_runtime_qualified"])
        self.assertFalse(self.report["hardware_tested"])

    @unittest.skipUnless(unicorn, "Unicorn required for instruction execution")
    def test_real_instructions_publish_pass_only_after_child_and_two_waits(self):
        fixture = Arm64Fixture(self.directory).run()
        self.assertTrue(fixture.checkpoint_reached)
        self.assertEqual(fixture.value("_chiron_selftest_results", 8), (1, 15, 15, 0, 42, 42, 37 << 8, 0))
        self.assertEqual(fixture.value("_chiron_init_status"), (2,))
        self.assertEqual(fixture.value("_chiron_heartbeats"), (2,))
        self.assertEqual(fixture.sleeps, 2)
        self.assertEqual(fixture.child_calls, [20, 39, 5, 4, 6, 1])
        self.assertIn(b"CHILD PASS", fixture.output)
        self.assertEqual(fixture.child_exit, 37)
        self.assertEqual(fixture.waits, 2)
        self.assertFalse(fixture.mapped)

    @unittest.skipUnless(unicorn, "Unicorn required for instruction execution")
    def test_instruction_fault_paths_cannot_qualify(self):
        cases = [
            ("missing_child", 10, 0), ("short_read", 11, 0), ("bad_marker", 12, 0),
            ("false_eof", 13, 0), ("file_close_error", 14, 0),
            ("mmap_error", 20, 1), ("mmap_not_zero", 21, 1),
            ("mmap_rw_corrupt", 22, 1), ("munmap_error", 23, 1),
            ("spawn_error", 30, 3), ("child_pid_one", 31, 3),
            ("child_wrong_parent", 31, 3), ("wrong_wait_pid", 31, 3), ("wait_error", 31, 3),
            ("not_reaped", 32, 7), ("short_console", 40, 1),
        ]
        for fault, stage, mask in cases:
            with self.subTest(fault=fault):
                fixture = Arm64Fixture(self.directory, fault).run()
                result = fixture.value("_chiron_selftest_results", 8)
                self.assertEqual(result[2:4], (mask, stage))
                self.assertEqual(result[7], 0)
                self.assertEqual(fixture.value("_chiron_init_status"), ((1 << 64) - 1,))
                self.assertFalse(fixture.checkpoint_reached)
                self.assertIn(b"CHIRON SELFTEST FAIL", fixture.output)
                self.assertGreaterEqual(fixture.sleeps, 3)
                if fault.startswith("child_"):
                    self.assertEqual(fixture.child_exit, 111)

    @unittest.skipUnless(unicorn, "Unicorn required for instruction execution")
    def test_timer_failure_keeps_existing_gate_failed(self):
        fixture = Arm64Fixture(self.directory, "timer_error").run()
        self.assertEqual(fixture.value("_chiron_selftest_results", 8)[2:4], (15, 0))
        self.assertEqual(fixture.value("_chiron_init_status"), ((1 << 64) - 1,))
        self.assertEqual(fixture.value("_chiron_heartbeats"), (0,))

    def test_baseline_still_assembles_without_selftest_include_path(self):
        destination = self.directory / "baseline.o"
        sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
        subprocess.run(["xcrun", "clang", "-target", "arm64-apple-ios17.0",
                        "-isysroot", sdk,
                        "-c", str(ROOT / "scripts/chiron-init.S"), "-o", str(destination)], check=True)
        symbols = subprocess.check_output(["xcrun", "nm", str(destination)], text=True)
        self.assertIn("_chiron_heartbeats", symbols)
        self.assertNotIn("_chiron_selftest_results", symbols)


if __name__ == "__main__":
    unittest.main()
