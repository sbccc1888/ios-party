import importlib.util
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decode = load("source_decoder", "tools/decode_source_trace.py").decode
recipes = load("source_recipes", "ci/prepare_runtime.py")


class SourceTraceTests(unittest.TestCase):
    def test_manifest_names_the_boot_image_and_separates_packaging_provenance(self):
        module = load("source_packaging", "ci/package_source_trace.py")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            runtime, firmware, output = directory / "runtime", directory / "firmware", directory / "output"
            runtime.mkdir()
            firmware.mkdir()
            (runtime / "source-trace-build.json").write_text(json.dumps({
                "build_id": "CT-HOST-PACK", "run_id": "kernel-run", "source_commit": "kernel-commit",
            }))
            for name in ("chiron-source-overlay.json",):
                (runtime / name).write_text("{}")
            (runtime / "kernel-symbols.txt").write_text("test symbols\n")
            (runtime / "chiron-trace.bin").write_bytes(bytes(65536))
            for name in ("source-trace-tests", "source-trace-captures", "kernel.dSYM", "observer-source"):
                (runtime / name).mkdir()
            (runtime / "source-trace-tests/verification.json").write_text("{}")
            image = b"ANDROID! qualified source image fixture"

            def build_fixture(command, check):
                destination = Path(command[command.index("--output") + 1])
                destination.mkdir()
                if str(command[1]).endswith("build_ram_boot.py"):
                    (destination / "ram-transport-report.json").write_text('{"type":"fat-report"}')
                else:
                    (destination / "chiron-ram-init-zero-tail.img").write_bytes(image)
                    (destination / "zero-tail-report.json").write_text('{"type":"transport-report"}')

            arguments = ["package", "--runtime", str(runtime), "--firmware", str(firmware),
                         "--output", str(output)]
            with patch.object(sys, "argv", arguments), patch.object(module.subprocess, "run", build_fixture), \
                    patch.dict(os.environ, {"GITHUB_SHA": "packaging-commit", "GITHUB_RUN_ID": "packaging-run"}):
                module.main()
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["image"], "CT-HOST-PACK.img")
            self.assertEqual(manifest["image_sha256"], hashlib.sha256(image).hexdigest())
            self.assertEqual(manifest["image_bytes"], len(image))
            self.assertEqual(manifest["source_commit"], "kernel-commit")
            self.assertEqual(manifest["packaging_source_commit"], "packaging-commit")
            self.assertNotEqual(manifest["image"], "fat-verification.json")

    @unittest.skipUnless(shutil.which("clang"), "clang required")
    def test_ring_fault_panic_mapping_and_host_display_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "trace-host"
            data = directory / "trace.bin"
            subprocess.run([
                "clang", "-std=c11", "-O2", "-DCHIRON_TRACE_HOST_TEST",
                '-DCHIRON_TRACE_BUILD_ID="CT-HOST-TEST"',
                str(ROOT / "diagnostics/chiron-trace.c"), str(ROOT / "tests/source_trace_host.c"),
                "-o", str(executable),
            ], check=True)
            subprocess.run([str(executable), str(data)], check=True)
            raw = data.read_bytes()
            result = decode(raw)
        self.assertEqual(result["build_id"], "CT-HOST-TEST")
        self.assertEqual(result["fault"]["kind"], 2)
        self.assertEqual(result["panic_caller"], "0xfffffff007999000")
        self.assertEqual(result["panic_count"], 1)
        self.assertGreaterEqual(result["exception_count"], 2)
        self.assertEqual(int(result["fault"]["far"], 16), 0x1000)
        self.assertIn("expected panic 42 source-channel", result["console"])
        self.assertIn("SOURCE BUFFERED CONSOLE CAPTURE", result["console"])
        self.assertIn("host framebuffer mapping PA=000000009d400000", result["console"])
        self.assertIn("native video console bound BASE=fffffff020000000", result["console"])
        self.assertIn("W=1080 H=2160 ROW=4320 DEPTH=32", result["console"])
        self.assertIn("W=4294968376 H=2160 ROW=4320 DEPTH=32", result["console"])
        self.assertIn("PANIC CALLER FFFFFFF007999000", result["console"])
        self.assertEqual(len(result["events"]), 256)
        self.assertGreater(result["events_dropped"], 0)
        self.assertGreater(result["reentries"], 0)
        for offset, value in ((8, 2), (176, 32768), (180, 32769)):
            changed = bytearray(raw)
            struct.pack_into("<I", changed, offset, value)
            with self.assertRaises(ValueError):
                decode(changed)

    def test_new_config_changes_only_the_observer_switches(self):
        def config(path):
            return dict(line.split("=", 1) for line in path.read_text().splitlines()
                        if line.startswith("CONFIG_"))
        baseline = config(ROOT / "configs/chiron_raminit_defconfig")
        observer = config(ROOT / "configs/chiron_trace_defconfig")
        self.assertEqual(observer.pop("CONFIG_XNU_CHIRON_TRACE"), "y")
        self.assertEqual(observer.pop("CONFIG_XNU_BOOT_PROGRESS"), "y")
        baseline.pop("CONFIG_XNU_BOOT_PROGRESS")
        self.assertEqual(observer, baseline)
        self.assertEqual(baseline["CONFIG_XNU_DT_PANIC_REGION"], "y")

    def test_console_qualification_requires_ordered_runtime_evidence(self):
        verify = load("source_qualification", "ci/test_source_trace.py").verify_console_capture
        messages = [
            "panic region uses device tree",
            "panic DT region absent; no memory accessed",
            "check_for_panic_log returned",
            "screen map enter PA=000000009d400000 SIZE=8e7000",
            "screen map returned PA=000000009d400000 VA=fffffff010000000 SIZE=8e7000",
            "gc_initialize returned",
            "screen init returned",
            "PE_create_console() returned",
        ]
        self.assertTrue(verify({"console": "\n".join(messages), "panic_count": 0})["console_returned"])
        for index in range(len(messages)):
            incomplete = messages[:index] + messages[index + 1:]
            with self.assertRaises(ValueError):
                verify({"console": "\n".join(incomplete), "panic_count": 0})
        for console, panics in (
            ("\n".join(reversed(messages)), 0),
            ("\n".join(messages) + "\npanic fixed board region", 0),
            ("\n".join(messages), 1),
        ):
            with self.assertRaises(ValueError):
                verify({"console": console, "panic_count": panics})

    def test_unknown_or_uninitialized_trace_is_rejected(self):
        for raw in (b"", bytes(65536), bytes(65537)):
            with self.assertRaises(ValueError):
                decode(raw)

    @unittest.skipUnless(shutil.which("clang"), "clang required")
    def test_source_archive_compiles_for_arm64_without_got(self):
        if not shutil.which("xcrun"):
            self.skipTest("Mach-O audit requires Xcode")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ("chiron-trace.c", "chiron-trace-entry.S"):
                obj = directory / (name + ".o")
                subprocess.run([
                    "clang", "--target=arm64-apple-ios17.0", "-O2", "-ffreestanding",
                    "-fno-builtin", "-fno-stack-protector", "-fno-common", "-fno-pic",
                    "-fvisibility=hidden", "-fno-jump-tables", "-mkernel",
                    "-march=armv8-a", "-mgeneral-regs-only", "-mno-outline-atomics",
                    "-c", str(ROOT / "diagnostics" / name), "-o", str(obj),
                ], check=True)
                relocation = subprocess.check_output(["xcrun", "llvm-objdump", "-r", str(obj)], text=True)
                self.assertNotIn("GOT_LOAD", relocation)
            macro = directory / "entry-macros.S"
            macro.write_text(
                '#include "' + str(ROOT / "diagnostics/chiron-trace-asm.inc") + '"\n'
                ".text\nCT_TRACE_ENTRY\nCT_TRACE_MAP_VIDEO\nCT_TRACE_STAGE 2\n"
                "CT_TRACE_STAGE 3\nCT_TRACE_STAGE 4\nCT_TRACE_HIGH_VECTORS\nret\n"
            )
            subprocess.run(["clang", "--target=arm64-apple-ios17.0", "-march=armv8-a",
                            "-c", str(macro), "-o", str(directory / "entry-macros.o")], check=True)


if __name__ == "__main__":
    unittest.main()
