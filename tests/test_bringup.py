import importlib.util
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
if __name__ == "__main__":
    unittest.main()
