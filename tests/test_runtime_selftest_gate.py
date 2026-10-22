import importlib.util
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("runtime_selftest_gate", ROOT / "ci/validate_runtime.py")
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)
PAYLOAD_SPEC = importlib.util.spec_from_file_location("selftest_payload_gate", ROOT / "ci/build_ram_payload.py")
PAYLOAD = importlib.util.module_from_spec(PAYLOAD_SPEC)
PAYLOAD_SPEC.loader.exec_module(PAYLOAD)


class RuntimeSelftestGateTests(unittest.TestCase):
    def memory(self, values=None):
        if values is None:
            values = [1, 15, 15, 0, 2, 2, 37 << 8, 0]
        return {"_chiron_selftest_results": struct.pack("<8Q", *values).hex()}

    def test_accepts_complete_child_execution_and_individual_checks(self):
        proof = RUNTIME.validate_user_selftest(self.memory())
        self.assertTrue(proof["file_read_passed"])
        self.assertTrue(proof["anonymous_mmap_passed"])
        self.assertTrue(proof["independent_exec_passed"])
        self.assertTrue(proof["child_reaped"])
        self.assertEqual(proof["child_exit_code"], 37)
        self.assertFalse(proof["hardware_observed"])

    def test_rejects_missing_partial_unknown_and_failed_checks(self):
        baseline = [1, 15, 15, 0, 2, 2, 37 << 8, 0]
        for field, value in (
            (0, 0), (0, 2), (1, 7), (1, 31), (2, 0), (2, 7), (2, 31),
            (3, 1), (4, 1), (5, 3), (6, 0), (6, 127 << 8), (7, 1),
        ):
            changed = baseline.copy()
            changed[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                RUNTIME.validate_user_selftest(self.memory(changed))
        for memory in (
            {}, {"_chiron_selftest_results": ""}, {"_chiron_selftest_results": "invalid"},
            {"_chiron_selftest_results": bytes(63).hex()},
            {"_chiron_selftest_results": bytes(65).hex()},
        ):
            with self.subTest(memory=memory), self.assertRaises(ValueError):
                RUNTIME.validate_user_selftest(memory)

    def test_mandatory_selftest_keeps_existing_runtime_requirements(self):
        post = {
            "kernel_sha256": "a" * 64,
            "default_provider_post_and_registration_passed": True,
            "corrupted_hmac_refused": True,
        }
        memory = self.memory()
        memory["_chiron_heartbeats"] = struct.pack("<Q", 2).hex()
        memory["_chiron_init_status"] = struct.pack("<Q", 2).hex()
        observed = {
            "kernel_sha256": "a" * 64, "native_4k_mmu_observed": True,
            "native_user_el0_observed": True, "native_user_4k_abi_observed": True,
            "native_user_memory": memory,
            "armcpu_globals": {
                "_armcpu_registered": 1, "_armcpu_gic_ready": 1, "_armcpu_timer_interrupts": 3,
            },
            "pthread_globals": {"_source_pthread_status": 0, "_pthread_functions": 1},
            "sp": "0x16fdffe00", "system_registers": {"SCTLR_EL1": "0x3454593d"},
        }
        self.assertTrue(RUNTIME.validate(post, observed, require_selftest=True)["user_selftest"])
        observed["sp"] = "0x16fdffe08"
        with self.assertRaises(ValueError):
            RUNTIME.validate(post, observed, require_selftest=True)
        observed["sp"] = "0x16fdffe00"
        observed["system_registers"]["SCTLR_EL1"] = "0x3454592d"
        with self.assertRaises(ValueError):
            RUNTIME.validate(post, observed, require_selftest=True)
        observed["system_registers"]["SCTLR_EL1"] = "0x3454593d"
        memory.pop("_chiron_selftest_results")
        with self.assertRaises(ValueError):
            RUNTIME.validate(post, observed, require_selftest=True)
        RUNTIME.validate(post, observed)

    def test_packaging_binds_the_observed_root_and_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {
                "init_pid": 1, "init_heartbeats": 2, "native_4k_mmu_observed": True,
                "real_crypto_post_and_corruption_refusal": True,
                "source_pthread_registered": True, "initial_tcr": "0x200000000",
                "user_selftest": RUNTIME.validate_user_selftest(self.memory()),
            }
            report["user_selftest"].update({
                "checkpoint_stack_aligned": True, "stack_alignment_check_enabled": True,
                "checkpoint_sp": "0x16fdffe00", "sctlr_el1": "0x3454593d",
            })
            for filename, field in (
                *PAYLOAD.FILES.items(),
                ("root.img", "root.img_sha256"), ("child.macho", "child.macho_sha256"),
            ):
                data = filename.encode()
                (directory / filename).write_bytes(data)
                report[field] = hashlib.sha256(data).hexdigest()
            (directory / "selftest-build.json").write_text("{}")
            report_path = directory / "runtime-test.json"
            report_path.write_text(json.dumps(report))
            self.assertTrue(PAYLOAD.require_runtime(directory)["user_selftest"]["child_reaped"])
            report["user_selftest"].pop("checkpoint_stack_aligned")
            report_path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                PAYLOAD.require_runtime(directory)
            report["user_selftest"]["checkpoint_stack_aligned"] = True
            report_path.write_text(json.dumps(report))
            for filename in ("root.img", "child.macho"):
                original = (directory / filename).read_bytes()
                (directory / filename).write_bytes(b"changed after QEMU observation")
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    PAYLOAD.require_runtime(directory)
                (directory / filename).write_bytes(original)
            report.pop("user_selftest")
            report_path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                PAYLOAD.require_runtime(directory)


if __name__ == "__main__":
    unittest.main()
