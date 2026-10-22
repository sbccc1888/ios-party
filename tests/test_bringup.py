import importlib.util
import gzip
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adt = load("chiron_adt", "scripts/make-chiron-adt.py")
artifact = load("artifact", "ci/inspect_artifacts.py")
runtime = load("runtime", "ci/validate_runtime.py")
payload_builder = load("payload_builder", "ci/build_ram_payload.py")
boot_inspector = load("boot_inspector", "tools/inspect_chiron_boot.py")


def read_adt(data):
    entries = {}

    def visit(offset, parent):
        count, children = struct.unpack_from("<II", data, offset)
        offset += 8
        props = {}
        for _ in range(count):
            raw, length = struct.unpack_from("<32sI", data, offset)
            offset += 36
            props[raw.split(b"\0")[0].decode()] = data[offset:offset + length]
            offset += (length + 3) & ~3
        name = props["name"].rstrip(b"\0").decode()
        path = parent + "/" + name
        entries[path] = props
        for _ in range(children):
            offset = visit(offset, path)
        return offset

    if visit(0, "") != len(data):
        raise ValueError("trailing ADT bytes")
    return entries


class BringupTests(unittest.TestCase):
    def setUp(self):
        self.board = tomllib.loads((ROOT / "board.toml").read_text())
        self.profile = json.loads((ROOT / "research/chiron-live-profile.json").read_text())

    def test_measured_interrupt_resources_are_not_qemu_defaults(self):
        tree = read_adt(adt.generate(self.board, self.profile, bytes(range(64))))
        bus = struct.unpack("<3Q", tree["/device-tree/arm-io"]["ranges"])
        gic = tree["/device-tree/arm-io/intc"]
        dist, dist_size, redist, redist_size = struct.unpack("<4Q", gic["reg"])
        self.assertEqual(bus[1] + dist - bus[0], 0x17A00000)
        self.assertEqual(bus[1] + redist - bus[0], 0x17B00000)
        self.assertEqual((dist_size, redist_size), (0x10000, 0x100000))
        self.assertEqual(struct.unpack("<I", gic["virtual-timer-intid"])[0], 19)
        self.assertEqual(len([p for p in tree if p.startswith("/device-tree/cpus/")]), 1)
        self.assertFalse(any("ufs" in name for name in tree))
        self.assertEqual(tree["/device-tree/psci"]["method"], b"smc\0")

    def test_volatile_nvram_bank_has_valid_chrp_headers(self):
        chosen = read_adt(adt.generate(self.board, self.profile, bytes(64)))["/device-tree/chosen"]
        bank = chosen["nvram-proxy-data"]
        offset = 32
        while offset < len(bank):
            header = bank[offset:offset + 16]
            summed = sum(header[:1] + header[2:])
            while summed > 255:
                summed = (summed & 255) + (summed >> 8)
            self.assertEqual(header[1], summed)
            length = struct.unpack_from("<H", header, 2)[0] * 16
            self.assertGreaterEqual(length, 16)
            offset += length
        self.assertEqual(offset, len(bank))

    def test_unsupported_profile_does_not_generate_chiron_tree(self):
        self.profile["soc_revision"] = "unknown"
        with self.assertRaises(ValueError):
            adt.generate(self.board, self.profile, bytes(64))

    def test_ram_root_bindings_are_loader_owned_and_empty(self):
        tree = read_adt(adt.generate(self.board, self.profile, bytes(64), ram_root=True))
        memory = tree["/device-tree/chosen/memory-map"]
        self.assertEqual(memory["RAMDisk"], bytes(16))
        self.assertEqual(memory["TrustCache"], bytes(16))

    def test_runtime_requires_matching_real_checkpoints(self):
        post = {"kernel_sha256": "a" * 64,
                "default_provider_post_and_registration_passed": True, "corrupted_hmac_refused": True}
        observed = {
            "kernel_sha256": "a" * 64, "native_4k_mmu_observed": True,
            "native_user_el0_observed": True, "native_user_4k_abi_observed": True,
            "native_user_memory": {"_chiron_heartbeats": struct.pack("<Q", 2).hex(),
                                   "_chiron_init_status": struct.pack("<Q", 2).hex()},
            "armcpu_globals": {"_armcpu_registered": 1, "_armcpu_gic_ready": 1,
                              "_armcpu_timer_interrupts": 3},
            "pthread_globals": {"_source_pthread_status": 0, "_pthread_functions": 1},
        }
        self.assertFalse(runtime.validate(post, observed)["hardware_boot_qualified"])
        observed["kernel_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            runtime.validate(post, observed)
        observed["kernel_sha256"] = "a" * 64
        observed["native_user_memory"]["_chiron_heartbeats"] = bytes(8).hex()
        with self.assertRaises(ValueError):
            runtime.validate(post, observed)

    def test_native_ram_payload_metadata_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "payload-bounds"
            subprocess.run([
                "cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "package/ChironCiPkg"),
                str(ROOT / "tests/payload_bounds.c"), "-o", str(executable),
            ], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True)

    def test_packaging_rejects_changed_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = {
                "init_pid": 1, "init_heartbeats": 2, "native_4k_mmu_observed": True,
                "real_crypto_post_and_corruption_refusal": True,
                "source_pthread_registered": True, "initial_tcr": "0x200000000",
            }
            for filename, key in payload_builder.FILES.items():
                data = filename.encode()
                (root / filename).write_bytes(data)
                report[key] = hashlib.sha256(data).hexdigest()
            (root / "runtime-test.json").write_text(json.dumps(report))
            self.assertEqual(payload_builder.require_runtime(root)["init_pid"], 1)
            (root / "kernel.macho").write_bytes(b"changed after observation")
            with self.assertRaises(ValueError):
                payload_builder.require_runtime(root)

    def test_diagnostic_guard_accepts_only_empty_entitlements(self):
        patch = ROOT / "ci/patches/zzzzz-chiron-empty-entitlements.patch"
        additions = "\n".join(line[1:] for line in patch.read_text().splitlines()
                              if line.startswith("+") and not line.startswith("+++"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guard.inc").write_text(additions + "\n")
            query_patch = ROOT / "ci/patches/zzzzzz-chiron-deny-missing-entitlements.patch"
            query = "\n".join(line[1:] for line in query_patch.read_text().splitlines()
                              if line.startswith("+") and not line.startswith("+++"))
            (root / "queryguard.inc").write_text(query + "\n")
            executable = root / "guard-check"
            subprocess.run([
                "cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                "-I", str(root), str(ROOT / "tests/empty_entitlements.c"),
                "-o", str(executable),
            ], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True)

    def test_reject_x64_efi_and_arm64e_kernel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.efi"
            data = bytearray(256)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 60, 64)
            data[64:68] = b"PE\0\0"
            struct.pack_into("<H", data, 68, 0x8664)
            struct.pack_into("<H", data, 64 + 24 + 68, 10)
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                artifact.inspect(path)
            path = Path(directory) / "kernel.source.unstripped"
            path.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x100000C, 2, 2, 0, 0, 0, 0))
            with self.assertRaises(ValueError):
                artifact.inspect(path)

    def test_firmware_header_must_cover_whole_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            dtb = root / "source/Platforms/Msm8998Pkg/Device/xiaomi/chiron/DeviceTreeBlob/Android/Android-chiron.dtb"
            dtb.parent.mkdir(parents=True)
            dtb.write_bytes(b"\xd0\x0d\xfe\xed")
            fd = bytearray(0x600000)
            fd[56:60] = b"ARM\x64"
            struct.pack_into("<Q", fd, 16, 0x200000)
            (output / "chiron-unixnu.fd").write_bytes(fd)
            result = subprocess.run([
                sys.executable, str(ROOT / "ci/pack_firmware.py"),
                "--source", str(root / "source"), "--output", str(output),
            ], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"does not cover", result.stderr)
            self.assertFalse((output / "chiron-uefi-experimental.img").exists())

    def test_firmware_control_preserves_kernel_bytes_and_rejects_bad_checksum(self):
        # A minimal FDT plus an intentionally distinctive gzip header exercises
        # exact preservation across Python/zlib versions.
        tree = struct.pack(">I", 1) + bytes(4) + struct.pack(">II", 2, 9)
        dtb = struct.pack(">10I", 0xD00DFEED, 72, 56, 72, 40, 17, 16, 0, 0, len(tree))
        dtb += bytes(16) + tree
        compressed = bytearray(gzip.compress(bytes(64), mtime=0))
        compressed[9] = 3
        kernel = bytes(compressed) + dtb
        ramdisk = bytes(range(256)) * 32
        header = bytearray(4096)
        header[:8] = b"ANDROID!"
        struct.pack_into("<10I", header, 8, len(kernel), 0x8000, len(ramdisk),
                         0x1000000, 0, 0, 0x100, 4096, 0, 0)
        digest = hashlib.sha1()
        for payload in (kernel, ramdisk, b""):
            digest.update(payload)
            digest.update(struct.pack("<I", len(payload)))
        header[576:608] = digest.digest().ljust(32, b"\0")
        original = bytes(header) + kernel + bytes(-len(kernel) % 4096) + ramdisk
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.img"
            source.write_bytes(original)
            command = [sys.executable, str(ROOT / "tools/make_chiron_firmware_control.py"),
                       "--source", str(source), "--output", str(root / "control")]
            subprocess.run(command, check=True, capture_output=True)
            result = (root / "control/chiron-same-firmware-no-ram.img").read_bytes()
            self.assertEqual(result[4096:4096 + len(kernel)], kernel)
            self.assertEqual(struct.unpack_from("<I", result, 16)[0], 0)
            self.assertEqual(len(result), len(original) - len(ramdisk))
            self.assertEqual(source.read_bytes(), original)
            legacy_firmware = bytearray(0x600000)
            legacy_firmware[56:60] = b"ARM\x64"
            struct.pack_into("<Q", legacy_firmware, 16, len(legacy_firmware))
            legacy_kernel = gzip.compress(legacy_firmware, mtime=0) + dtb
            legacy_header = bytearray(result[:4096])
            struct.pack_into("<I", legacy_header, 8, len(legacy_kernel))
            legacy_digest = hashlib.sha1()
            for payload in (legacy_kernel, b"", b""):
                legacy_digest.update(payload)
                legacy_digest.update(struct.pack("<I", len(payload)))
            legacy_header[576:608] = legacy_digest.digest().ljust(32, b"\0")
            legacy = root / "legacy.img"
            legacy.write_bytes(bytes(legacy_header) + legacy_kernel
                               + bytes(-len(legacy_kernel) % 4096))
            legacy_command = command[:-1] + [
                str(root / "legacy-control"), "--firmware-source", str(legacy),
            ]
            subprocess.run(legacy_command, check=True, capture_output=True)
            combined = (root / "legacy-control/chiron-legacy-firmware-same-ram.img").read_bytes()
            self.assertEqual(combined[4096:4096 + len(legacy_kernel)], legacy_kernel)
            self.assertEqual(struct.unpack_from("<I", combined, 16)[0], len(ramdisk))
            ramdisk_offset = (4096 + len(legacy_kernel) + 4095) // 4096 * 4096
            self.assertEqual(combined[ramdisk_offset:], ramdisk)
            prefix_command = legacy_command + ["--ramdisk-prefix-bytes", "4096"]
            prefix_command[5] = str(root / "prefix-control")
            subprocess.run(prefix_command, check=True, capture_output=True)
            prefix = (root / "prefix-control/chiron-legacy-ram-4k.img").read_bytes()
            self.assertEqual(prefix[4096:4096 + len(legacy_kernel)], legacy_kernel)
            self.assertEqual(struct.unpack_from("<I", prefix, 16)[0], 4096)
            self.assertEqual(prefix[ramdisk_offset:], ramdisk[:4096])
            prefix_command[5] = str(root / "invalid-prefix")
            prefix_command[-1] = "4097"
            invalid_prefix = subprocess.run(prefix_command, capture_output=True)
            self.assertNotEqual(invalid_prefix.returncode, 0)
            self.assertIn(b"RAM prefix must be page aligned", invalid_prefix.stderr)
            self.assertFalse((root / "invalid-prefix").exists())
            no_legacy = command + ["--ramdisk-prefix-bytes", "4096"]
            no_legacy[5] = str(root / "no-legacy")
            rejected_prefix = subprocess.run(no_legacy, capture_output=True)
            self.assertNotEqual(rejected_prefix.returncode, 0)
            self.assertIn(b"require legacy firmware", rejected_prefix.stderr)
            raw_command = legacy_command + ["--uncompressed-firmware"]
            raw_command[5] = str(root / "raw-control")
            subprocess.run(raw_command, check=True, capture_output=True)
            raw = (root / "raw-control/chiron-legacy-raw-firmware-same-ram.img").read_bytes()
            self.assertEqual(raw[4096:4096 + len(legacy_firmware)], legacy_firmware)
            self.assertEqual(raw[4096 + len(legacy_firmware):4096 + len(legacy_firmware) + len(dtb)], dtb)
            raw_ramdisk_offset = (4096 + len(legacy_firmware) + len(dtb) + 4095) // 4096 * 4096
            self.assertEqual(raw[raw_ramdisk_offset:], ramdisk)
            raw_report = json.loads((root / "raw-control/control-report.json").read_text())
            self.assertEqual(raw_report["kernel_compression"], "none")
            self.assertEqual(raw_report["firmware_content_sha256"], hashlib.sha256(legacy_firmware).hexdigest())
            malformed = bytearray(raw)
            struct.pack_into("<Q", malformed, 4096 + 16, len(raw) * 2)
            malformed_path = root / "malformed.img"
            malformed_path.write_bytes(malformed)
            with self.assertRaisesRegex(ValueError, "invalid ARM64 Image size"):
                boot_inspector.inspect(malformed_path, root / "malformed-inspection")
            incompatible = bytearray(legacy.read_bytes())
            struct.pack_into("<I", incompatible, 20, 0x2000000)
            legacy.write_bytes(incompatible)
            legacy_command[5] = str(root / "incompatible")
            rejected_legacy = subprocess.run(legacy_command, capture_output=True)
            self.assertNotEqual(rejected_legacy.returncode, 0)
            self.assertIn(b"incompatible ramdisk_addr", rejected_legacy.stderr)
            self.assertFalse((root / "incompatible").exists())
            source.write_bytes(original[:576] + bytes(32) + original[608:])
            command[-1] = str(root / "rejected")
            rejected = subprocess.run(command, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(b"integrity check failed", rejected.stderr)
            self.assertFalse((root / "rejected").exists())

if __name__ == "__main__":
    unittest.main()
