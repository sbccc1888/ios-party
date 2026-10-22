"""Static audit and synthetic SVC control-flow tests. No XNU/device qualification."""

import copy
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


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ci" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


BUILD = module("abi_build_test", "build_chiron_abi_probe.py")
VALIDATE = module("abi_validate_test", "validate_chiron_abi_probe.py")
RAM = BUILD.RAM

try:
    import unicorn
    import unicorn.arm64_const as REG
except ImportError:
    unicorn = None


class ProbeFixture:
    """Execute actual ARM64 instructions; every SVC answer is synthetic."""

    def __init__(self, directory, fault=None, entry_sp=0x7000FFF8):
        self.directory, self.fault = directory, fault
        self.report = json.loads((directory / BUILD.REPORT).read_text())
        self.addresses = json.loads((directory / "addresses.json").read_text())
        self.machine = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        data = (directory / "launchd.macho").read_bytes()
        for cmd, at, _ in RAM.load_commands(data):
            if cmd == 0x19:
                address, size, offset, length = struct.unpack_from("<4Q", data, at + 24)
                if address and size:
                    self.machine.mem_map(address, RAM.align(size))
                    if length:
                        self.machine.mem_write(address, data[offset:offset + length])
        self.machine.mem_map(0x70000000, 0x10000)
        self.machine.reg_write(REG.UC_ARM64_REG_SP, entry_sp)
        self.machine.reg_write(REG.UC_ARM64_REG_PSTATE, 0)
        self.machine.mem_map(0xFFFFF4000, 4096)
        self.machine.mem_write(0xFFFFF4025, bytes([14 if fault == "user_shift" else 12]))
        self.machine.mem_write(0xFFFFF4037, bytes([14 if fault == "kernel_shift" else 12]))
        self.machine.hook_add(unicorn.UC_HOOK_INTR, self.svc)
        self.machine.hook_add(unicorn.UC_HOOK_CODE, self.checkpoint)
        self.output, self.calls = bytearray(), []
        self.messages, self.beats, self.protects = [], 0, []
        self.reached = False
        self.write_stages = []
        self.port_refs = set()

    def values(self):
        raw = bytes(self.machine.mem_read(self.addresses["_chiron_abi_results"], 256))
        return dict(zip(BUILD.FIELDS, struct.unpack("<32Q", raw)))

    def checkpoint(self, machine, address, size, _):
        if address == self.addresses["_checkpoint"]:
            self.reached = True
            machine.emu_stop()

    def reply(self, value=0, error=False, mach=False):
        self.machine.reg_write(REG.UC_ARM64_REG_X0, value & ((1 << 64) - 1))
        flags = self.machine.reg_read(REG.UC_ARM64_REG_NZCV) & ~(1 << 29)
        # Mach traps have no BSD carry contract. Deliberately poison it.
        self.machine.reg_write(REG.UC_ARM64_REG_NZCV, flags | (int(error or mach) << 29))

    def cstring(self, address):
        result = bytearray()
        for index in range(256):
            byte = self.machine.mem_read(address + index, 1)[0]
            if not byte:
                return bytes(result)
            result.append(byte)
        raise AssertionError("unterminated path")

    def svc(self, machine, interrupt, _):
        if interrupt != 2:
            raise AssertionError(f"unexpected exception {interrupt}")
        stack = machine.reg_read(REG.UC_ARM64_REG_SP)
        if stack & 15:
            raise AssertionError(f"SVC with unaligned SP {stack:#x}")
        number = machine.reg_read(REG.UC_ARM64_REG_X16)
        if number & (1 << 63):
            number -= 1 << 64
        args = [machine.reg_read(getattr(REG, f"UC_ARM64_REG_X{i}")) for i in range(8)]
        stage = self.values()["failure_stage"]
        self.calls.append((number, stage, args))
        if self.fault == f"svc_{stage}" and number != 4:
            self.reply(5, error=number >= 0, mach=number < 0)
            return
        if number == 5:
            path = self.cstring(args[0])
            if path not in (b"/dev/console", b"/probe.bin"):
                raise AssertionError(path)
            self.reply(3 if path == b"/dev/console" else 4)
        elif number == 20:
            self.reply(2 if self.fault == "pid" else 1)
        elif number == 202:
            if struct.unpack("<2I", machine.mem_read(args[0], 8)) != (6, 7):
                raise AssertionError("wrong hw.pagesize MIB")
            machine.mem_write(args[2], struct.pack("<Q", 16384 if self.fault == "pagesize" else 4096))
            machine.mem_write(args[3], struct.pack("<Q", 8 if self.fault == "oldlength" else 4))
            self.reply()
        elif number == -28:
            self.reply(0 if self.fault == "task_port" else 0x103, mach=True)
        elif number == -16:
            if args[:2] != [0x103, 1]:
                raise AssertionError("wrong allocate trap arguments")
            machine.mem_write(args[2], struct.pack("<I", 0 if self.fault == "port_name" else 0x207))
            self.port_refs.add("receive")
            self.reply(mach=True)
        elif number == -21:
            if args[:4] != [0x103, 0x207, 0x207, 20]:
                raise AssertionError("wrong insert_right trap arguments")
            self.port_refs.add("send")
            self.reply(mach=True)
        elif number == -47:
            self.message(stage, args)
        elif number == -19:
            right = "send" if stage == 24 else "receive"
            if args[:4] != [0x103, 0x207, 0 if right == "send" else 1, (1 << 64) - 1]:
                raise AssertionError("wrong port release arguments")
            self.port_refs.remove(right)
            self.reply(mach=True)
        elif number == -89:
            numerator = 0 if self.fault == "numer" else 125
            denominator = 0 if self.fault == "denom" else 3
            machine.mem_write(args[0], struct.pack("<2I", numerator, denominator))
            self.reply(mach=True)
        elif number == -3:
            value = 100 if stage == 31 or self.fault == "time_stalled" else 200
            if self.fault == "time_zero" and stage == 31:
                value = 0
            self.reply(value, mach=True)
        elif number == 197:
            if stage == 50:
                if args[:6] != [0, 8192, 3, 0x1002, (1 << 64) - 1, 0]:
                    raise AssertionError("wrong anonymous mmap arguments")
                machine.mem_map(0x60000000, 8192)
                if self.fault == "not_zero":
                    machine.mem_write(0x60001000, b"\x01")
                self.reply(0x60000001 if self.fault == "unaligned_anon" else 0x60000000)
            else:
                child = bytearray((self.directory / "child.macho").read_bytes())
                for fault, offset in (("file_magic", 0), ("file_arch", 4), ("file_type", 12),
                                      ("file_marker", self.report["child_marker_offset"])):
                    if self.fault == fault:
                        child[offset] ^= 1
                if args[:6] != [0, RAM.align(len(child)), 1, 2, 4, 0]:
                    raise AssertionError("wrong file mmap arguments")
                machine.mem_map(0x61000000, RAM.align(len(child)))
                machine.mem_write(0x61000000, bytes(child))
                machine.mem_protect(0x61000000, RAM.align(len(child)), unicorn.UC_PROT_READ)
                self.reply(0x61000001 if self.fault == "unaligned_file" else 0x61000000)
        elif number == 74:
            if args[:3] != [0x60000000, 8192, 1 if stage == 52 else 3]:
                raise AssertionError("wrong mprotect arguments")
            if stage == 52:
                expected = b"".join(struct.pack("<Q", offset ^ 0x5555555555555555)
                                    for offset in range(0, 8192, 8))
                if bytes(machine.mem_read(args[0], 8192)) != expected:
                    raise AssertionError("probe did not write every word in both pages")
                if self.fault == "rw_corrupt":
                    machine.mem_write(0x60001000, b"\xff")
            self.protects.append(args[2])
            machine.mem_protect(args[0], args[1], unicorn.UC_PROT_READ if stage == 52
                                else unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
            self.reply()
        elif number == 73:
            if stage == 56 and bytes(machine.mem_read(0x60001000, 8)) != struct.pack("<Q", 0x7391):
                raise AssertionError("probe did not write after RW restoration")
            machine.mem_unmap(args[0], args[1])
            self.reply()
        elif number == 6:
            if args[0] != 4:
                raise AssertionError("wrong close fd")
            self.reply()
        elif number == 4:
            line = bytes(machine.mem_read(args[1], args[2]))
            self.output.extend(line)
            if b"FAIL" not in line:
                self.write_stages.append(stage)
            if self.fault == "short_console" and b"PASS mach-port" in line:
                self.reply(args[2] - 1)
            elif self.fault == "console_error" and b"PASS mach-port" in line:
                self.reply(5, error=True)
            else:
                self.reply(args[2])
        elif number == 93:
            if args[:4] != [0, 0, 0, 0] or struct.unpack("<2Q", machine.mem_read(args[4], 16)) != (1, 0):
                raise AssertionError("select lacks explicit one-second timeout")
            if stage == 91:
                self.beats += 1
            self.reply()
        else:
            raise AssertionError(f"unexpected SVC {number} stage {stage}")

    def message(self, stage, args):
        if stage == 20:
            expected = [0x400000011, (32 << 32) | 19, 0x207, 0x4348 << 32, 0, 0, 100]
            if args[1:] != expected or self.port_refs != {"send", "receive"}:
                raise AssertionError("wrong bounded mach_msg2 send arguments")
            data = bytes(self.machine.mem_read(args[0], 32))
            if struct.unpack("<6IQ", data) != (19, 32, 0x207, 0, 0, 0x4348, 0x434849524F4E4142):
                raise AssertionError("wrong Mach user header")
            self.messages.append(data)
            self.reply(mach=True)
        elif stage == 21:
            if args[1:] != [0x102, 0, 0, 0, 0x207 << 32, 128, 100]:
                raise AssertionError("wrong bounded mach_msg2 receive arguments")
            data = bytearray(self.messages.pop())
            struct.pack_into("<4I", data, 0, 17 << 8, 32, 0, 0x207)
            bad = {"message_bits": 0, "message_size": 4, "message_remote": 8,
                   "message_local": 12, "message_voucher": 16, "message_id": 20,
                   "message_payload": 24}
            if self.fault in bad:
                data[bad[self.fault]] ^= 1
            self.machine.mem_write(args[0], bytes(data))
            self.reply(mach=True)
        elif stage == 23:
            if args[1:] != [0x102, 0, 0, 0, 0x207 << 32, 128, 1] or self.messages:
                raise AssertionError("empty receive is unbounded/not empty")
            self.reply(0 if self.fault == "empty_success" else 0x10004003, mach=True)
        else:
            raise AssertionError(stage)

    def run(self):
        self.machine.emu_start(self.addresses["_start"], 0, count=150_000)
        if not self.reached:
            raise AssertionError("probe did not reach _checkpoint within instruction bound")
        return self

    def observation(self):
        memory = {}
        for symbol, size in (("_chiron_abi_results", 256), ("_results", 32),
                             ("_user_page_size", 8), ("_old_length", 8),
                             ("_chiron_heartbeats", 8), ("_chiron_init_status", 8)):
            memory[symbol] = bytes(self.machine.mem_read(self.addresses[symbol], size)).hex()
        return {"native_user_memory": memory}


class SourceTests(unittest.TestCase):
    def test_fixed_abi(self):
        self.assertEqual(len(BUILD.FIELDS), 32)
        self.assertEqual(struct.calcsize(BUILD.RESULT_ABI["format"]), 256)
        self.assertEqual(sum(BUILD.BITS.values()), 127)

    def test_source_guard_rejects_changed_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in BUILD.PINNED_SOURCE:
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"changed ABI")
            with self.assertRaisesRegex(ValueError, "pinned layout"):
                BUILD.verify_source(directory)


@unittest.skipUnless(shutil.which("xcrun") and shutil.which("codesign") and BUILD.SOURCE.is_dir(),
                     "Apple build tools and pinned local source required")
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="chiron-abi-test-")
        cls.directory = Path(cls.temporary.name)
        subprocess.run([sys.executable, str(ROOT / "ci/build_chiron_abi_probe.py"),
                        "--output", str(cls.directory)], check=True, capture_output=True, text=True)
        cls.report = json.loads((cls.directory / BUILD.REPORT).read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_artifacts_signatures_root_and_symbols(self):
        report = self.report
        self.assertEqual(report["result_abi"], BUILD.RESULT_ABI)
        addresses = json.loads((self.directory / "addresses.json").read_text())
        self.assertEqual(addresses["_chiron_abi_results_end"] - addresses["_chiron_abi_results"], 256)
        for name, record in report["artifacts"].items():
            data = (self.directory / name).read_bytes()
            self.assertEqual((RAM.sha(data), len(data)), (record["sha256"], record["bytes"]))
        launchd = (self.directory / "launchd.macho").read_bytes()
        child = (self.directory / "child.macho").read_bytes()
        RAM.validate_root((self.directory / "root.img").read_bytes(), launchd, child)
        for name, data in (("launchd", launchd), ("child", child)):
            RAM.audit_static(data)
            self.assertEqual(RAM.signature_info(data), report["signatures"][name])
            corrupted = bytearray(data)
            corrupted[4096] ^= 1
            with self.assertRaises(ValueError):
                RAM.signature_info(corrupted)
        trust = (self.directory / "trustcache.bundle").read_bytes()
        self.assertEqual(trust, RAM.make_trustcache(
            [report["signatures"][name]["cdhash"] for name in ("launchd", "child")]))
        self.assertFalse(report["qemu_runtime_qualified"])
        self.assertFalse(report["pthread_tls_tested"])

    def test_prepared_tree_source_and_trace_overlay_binding(self):
        with tempfile.TemporaryDirectory(prefix="chiron-abi-prepared-") as temporary:
            tree = Path(temporary)
            source = tree / ".cache/xnu-work"
            files = [*BUILD.PINNED_SOURCE, "osfmk/arm64/sleh.c"]
            files += ["bsd/miscfs/mockfs/" + name for name in RAM.MOCKFS_HASHES]
            for name in files:
                destination = source / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(BUILD.SOURCE / name, destination)
            sleh = source / "osfmk/arm64/sleh.c"
            sleh.write_text(sleh.read_text() + "\n// Different diagnostic overlay, same ABI branches.\n")
            overlay = tree / "build/chiron-source-overlay.json"
            overlay.parent.mkdir()
            overlay.write_text(json.dumps({"edits": {"osfmk/arm64/sleh.c": {
                "after_sha256": RAM.sha(sleh.read_bytes())}}}))
            report = BUILD.build(tree / "build/separate-abi-probe", tree=tree)
            self.assertEqual(report["abi_source_mode"], "prepared-tree")
            self.assertEqual(report["abi_source_root"], str(source.resolve()))
            self.assertTrue(report["mockfs_source_verified"])
            self.assertTrue(report["dispatch_audit"]["source_overlay_bound"])
            with self.assertRaisesRegex(ValueError, "separate"):
                BUILD.build(tree / "build/native-user-fixture", tree=tree)
            original = sleh.read_text()
            sleh.write_text(original.replace("handle_mach_absolute_time_trap(state);",
                                            "handle_mach_continuous_time_trap(state);"))
            with self.assertRaisesRegex(ValueError, "dispatch branch"):
                BUILD.audit_dispatch(source, tree)
            sleh.write_text(original + "// stale overlay digest\n")
            with self.assertRaisesRegex(ValueError, "overlay report"):
                BUILD.audit_dispatch(source, tree)

    @unittest.skipUnless(unicorn, "existing Unicorn install required for SVC fixtures")
    def test_success_unaligned_entry_and_validator_cli(self):
        for entry_sp in (0x7000FFF0, 0x7000FFF8):
            with self.subTest(entry_sp=entry_sp):
                fixture = ProbeFixture(self.directory, entry_sp=entry_sp).run()
                self.assertEqual(fixture.values()["passed_mask"], 127)
                self.assertEqual(fixture.beats, 2)
                self.assertEqual(fixture.protects, [1, 3])
                self.assertFalse(fixture.port_refs)
                self.assertEqual(fixture.output.count(b"CHIRON ABI PASS "), 7)
                self.assertIn(b"CHIRON ABI PROBE PASS\n", fixture.output)
                result = VALIDATE.validate(fixture.observation(), self.report)
                self.assertTrue(result["abi_probe_passed"])
                self.assertFalse(result["kernel_artifact_binding_checked"])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            observation, verification = directory / "observation.json", directory / "verification.json"
            observation.write_text(json.dumps(fixture.observation()))
            subprocess.run([sys.executable, str(ROOT / "ci/validate_chiron_abi_probe.py"),
                            "--observation", str(observation),
                            "--build-report", str(self.directory / BUILD.REPORT),
                            "--output", str(verification)], check=True, capture_output=True, text=True)
            self.assertTrue(json.loads(verification.read_text())["abi_probe_passed"])
            malformed = fixture.observation()
            malformed["native_user_memory"]["_chiron_abi_results"] = "00"
            observation.write_text(json.dumps(malformed))
            process = subprocess.run(
                [sys.executable, str(ROOT / "ci/validate_chiron_abi_probe.py"),
                 "--observation", str(observation), "--build-report", str(self.directory / BUILD.REPORT),
                 "--output", str(verification)], capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(verification.exists(), "failed validation retained stale PASS")

    @unittest.skipUnless(unicorn, "existing Unicorn install required for SVC fixtures")
    def test_errors_and_semantic_faults_reach_failure_checkpoint(self):
        stages = (1, 2, 3, 10, 11, 12, 20, 21, 23, 24, 25, 30, 32, 50, 52, 54, 56, 60, 61, 63, 64, 91)
        faults = {f"svc_{stage}": stage for stage in stages}
        # task_self is a port-returning trap: a nonzero synthetic 5 is a port name.
        faults.pop("svc_10")
        faults.update({
            "pid": 2, "pagesize": 3, "oldlength": 3, "task_port": 10, "port_name": 11,
            "empty_success": 23, "numer": 30, "denom": 30, "time_stalled": 33, "time_zero": 31,
            "user_shift": 40, "kernel_shift": 40, "not_zero": 51, "rw_corrupt": 53,
            "unaligned_anon": 50, "unaligned_file": 61, "short_console": 90, "console_error": 90,
        })
        faults.update({name: 22 for name in ("message_bits", "message_size", "message_remote",
                                           "message_local", "message_voucher", "message_id", "message_payload")})
        faults.update({name: 62 for name in ("file_magic", "file_arch", "file_type", "file_marker")})
        for fault, stage in faults.items():
            with self.subTest(fault=fault):
                fixture = ProbeFixture(self.directory, fault=fault).run()
                self.assertEqual(fixture.values()["failure_stage"], stage)
                status = struct.unpack("<q", bytes(fixture.machine.mem_read(
                    fixture.addresses["_chiron_init_status"], 8)))[0]
                self.assertEqual(status, -1)
                self.assertIn(b"CHIRON ABI PROBE FAIL stage=0x", fixture.output)
                self.assertIn(f"{stage:016x}".encode(), fixture.output)
                with self.assertRaises(ValueError):
                    VALIDATE.validate(fixture.observation(), self.report)
                expected_bits = (0 if stage <= 12 or fault in ("short_console", "console_error")
                                 else 1 if stage <= 22 else 3 if stage <= 25 else
                                 67 if stage <= 33 else 71 if stage == 40 else
                                 79 if stage <= 56 else 95 if stage <= 64 else 127)
                if fault in ("short_console", "console_error"):
                    expected_bits = 1  # allocate/insert already asserted, output then failed
                self.assertEqual(fixture.values()["passed_mask"], expected_bits)

    @unittest.skipUnless(unicorn, "existing Unicorn install required for SVC fixtures")
    def test_validator_rejects_forged_fields_and_bad_capture(self):
        fixture = ProbeFixture(self.directory).run()
        observed = fixture.observation()
        invalid = {
            "version": 2, "required_mask": 255, "passed_mask": 0, "failure_stage": 22,
            "task_port": 0, "receive_port": 0xFFFFFFFF, "message_id": 1, "message_payload": 0,
            "send_result": 5, "receive_result": 5, "port_cleanup_result": 5,
            "empty_receive_result": 0, "time_before": 0, "time_after": 100,
            "timebase_numer": 0, "timebase_denom": 0, "commpage_user_shift": 14,
            "commpage_kernel_shift": 14, "anon_base": 1, "file_base": 0, "file_magic": 0,
            "file_length": 0, "completed_stage": 63, "console_fd": (1 << 64) - 1,
            "aligned_sp": fixture.values()["aligned_sp"] + 1, "last_call": 4,
            "last_return": 5, "last_nzcv": 1 << 29, "reserved0": 1, "reserved1": 1, "reserved2": 1,
        }
        for name, value in invalid.items():
            with self.subTest(field=name):
                changed = copy.deepcopy(observed)
                raw = bytearray.fromhex(changed["native_user_memory"]["_chiron_abi_results"])
                struct.pack_into("<Q", raw, BUILD.FIELDS.index(name) * 8, value)
                changed["native_user_memory"]["_chiron_abi_results"] = raw.hex()
                with self.assertRaises(ValueError):
                    VALIDATE.validate(changed, self.report)
        for symbol in observed["native_user_memory"]:
            for value in (None, "", "garbage", "00"):
                with self.subTest(symbol=symbol, value=value):
                    changed = copy.deepcopy(observed)
                    changed["native_user_memory"][symbol] = value
                    with self.assertRaises(ValueError):
                        VALIDATE.validate(changed, self.report)
        changed_report = copy.deepcopy(self.report)
        changed_report["result_abi"]["bytes"] = 64
        with self.assertRaises(ValueError):
            VALIDATE.validate(observed, changed_report)


if __name__ == "__main__":
    unittest.main()
