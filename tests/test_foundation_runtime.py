"""Keep independent probe reports bound to the actual baseline and runtime inputs."""

import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("foundation_runtime", ROOT / "ci/test_foundation_runtime.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FoundationRuntimeTests(unittest.TestCase):
    def test_ramfs_compile_keeps_outputs_and_headers_independent(self):
        spec = importlib.util.spec_from_file_location(
            "ramfs_archive", ROOT / "ci/build_chiron_ramfs_archive.py")
        archive = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(archive)
        record = {
            "file": "/normal/mockfs.c",
            "arguments": ["clang", "-I", "/normal/bsd", "-DMOCKFS=0", "-MD", "-MP",
                          "-MF", "/normal/deps", "-MJ", "/normal/compile.json",
                          "-serialize-diagnostics", "/normal/diagnostics",
                          "-c", "/normal/mockfs.c", "-o", "/normal/mockfs.o"],
        }
        command = archive.compile_arguments(record, "/experiment/mockfs.c",
                                            "/experiment/mockfs.o", "/experiment/bsd")
        self.assertEqual(command[1:3], ["-I", "/experiment/bsd"])
        self.assertEqual(command[command.index("-o") + 1], "/experiment/mockfs.o")
        self.assertIn("/experiment/mockfs.c", command)
        self.assertEqual(command[-1], "-DMOCKFS=1")
        for value in ("/normal/mockfs.c", "/normal/mockfs.o", "/normal/deps",
                      "/normal/compile.json", "/normal/diagnostics", "-DMOCKFS=0"):
            self.assertNotIn(value, command)
        with self.assertRaises(ValueError):
            archive.compile_arguments({"file": "missing", "arguments": ["clang", "-c", "other"]},
                                      "source", "object", "headers")

    def test_missing_or_mixed_normal_qualification_is_rejected(self):
        runtime = {"kernel_sha256": "kernel", "user_selftest": {"passed_mask": 15}}
        build = {"build_id": "CT-TEST"}
        proof = {
            "kernel_sha256": "kernel", "build_id": "CT-TEST",
            "user_selftest": runtime["user_selftest"],
        }
        flags = (
            "source_entry_and_mmu_observed", "native_framebuffer_mapping_observed",
            "panic_reason_observed", "data_abort_pc_esr_far_observed",
            "user_sigbus_fault_observed",
        )
        proof.update(dict.fromkeys(flags, True))
        MODULE.require_baseline(runtime, proof, build)
        for key in (*flags, "kernel_sha256", "build_id", "user_selftest"):
            changed = copy.deepcopy(proof)
            changed.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                MODULE.require_baseline(runtime, changed, build)
        changed = copy.deepcopy(proof)
        changed["user_selftest"]["passed_mask"] = 7
        with self.assertRaises(ValueError):
            MODULE.require_baseline(runtime, changed, build)

    def test_changed_or_unobserved_probe_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            report = {"artifacts": {}}
            observed = {"kernel_sha256": "kernel"}
            names = {
                "launchd.macho": "native_user_image_sha256",
                "root.img": "offline_root_sha256",
                "trustcache.bundle": "trust_cache_bundle_sha256",
            }
            for name, key in names.items():
                data = ("fixture " + name).encode()
                (fixture / name).write_bytes(data)
                value = hashlib.sha256(data).hexdigest()
                report["artifacts"][name] = {"sha256": value}
                observed[key] = value
            MODULE.verify_inputs(observed, fixture, report, "kernel")
            for name, key in names.items():
                changed = dict(observed)
                changed.pop(key)
                with self.subTest(missing=key), self.assertRaises(ValueError):
                    MODULE.verify_inputs(changed, fixture, report, "kernel")
                original = (fixture / name).read_bytes()
                (fixture / name).write_bytes(original + b"changed")
                with self.subTest(changed=name), self.assertRaises(ValueError):
                    MODULE.verify_inputs(observed, fixture, report, "kernel")
                (fixture / name).write_bytes(original)
            with self.assertRaises(ValueError):
                MODULE.verify_inputs(observed, fixture, report, "another-kernel")


if __name__ == "__main__":
    unittest.main()
