import hashlib
import hmac
import importlib.util
import shutil
import struct
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
        spec.loader.exec_module(module)
    return module


fat = load("kernel_log_fat", "tools/chiron_ram_fat.py")
log = load("kernel_log_tools", "tools/chiron_kernel_log.py")


def fat_fixture():
    data = bytearray(1048576)
    data[11:13] = b"\0\x02"
    data[13], data[16] = 1, 2
    struct.pack_into("<H", data, 14, 1)
    struct.pack_into("<I", data, 32, len(data) // 512)
    struct.pack_into("<I", data, 36, 16)
    struct.pack_into("<I", data, 44, 2)
    data[82:90] = b"FAT32   "
    data[510:512] = b"\x55\xaa"
    for cluster, value in ((2, 0x0fffffff), (3, 4), (4, 0x0fffffff)):
        struct.pack_into("<I", data, 512 + cluster * 4, value)
    data[8704:16896] = data[512:8704]
    short = b"KERNEL~1MAC"
    text = "kernel.macho\0".encode("utf-16le").ljust(26, b"\xff")
    lfn = bytearray(32)
    lfn[0], lfn[11], lfn[13] = 0x41, 0x0f, fat.RamFat32.short_checksum(short)
    lfn[1:11], lfn[14:26], lfn[28:32] = text[:10], text[10:22], text[22:]
    root = 16896
    data[root:root + 32] = lfn
    entry = bytearray(32)
    entry[:11], entry[11] = short, 0x20
    struct.pack_into("<H", entry, 26, 3)
    struct.pack_into("<I", entry, 28, 600)
    data[root + 32:root + 64] = entry
    data[root + 512:root + 1024] = b"a" * 512
    data[root + 1024:root + 1112] = b"b" * 88
    return bytes(data)


def section(name, segment, address, size, offset):
    return struct.pack("<16s16sQQ8I", name.encode(), segment.encode(), address, size,
                       offset, 0, 0, 0, 0, 0, 0, 0)


def segment(name, address, size, offset, count, protection):
    return struct.pack("<II16s4Q4I", 0x19, 72 + count * 80, name.encode(),
                       address, size, offset, size, protection, protection, count, 0)


def kernel_fixture():
    base = 0xfffffff007004000
    commands = segment("__TEXT", base, 0x1000, 0, 1, 1)
    commands += section("__fips_hmacs", "__TEXT", base + 0x800, 32, 0x800)
    commands += segment("__TEXT_EXEC", base + 0x1000, 0x3000, 0x1000, 2, 5)
    commands += section("__text", "__TEXT_EXEC", base + 0x1000, 0x1000, 0x1000)
    commands += section("__hib_text", "__TEXT_EXEC", base + 0x2000, 0x100, 0x2000)
    thread = bytearray(288)
    struct.pack_into("<4I", thread, 0, 5, 288, 6, 68)
    struct.pack_into("<Q", thread, 272, base + 0x1000)
    commands += thread
    data = bytearray(0x4000)
    struct.pack_into("<8I", data, 0, 0xfeedfacf, 0x100000c, 0, 2, 3, len(commands), 0, 0)
    data[32:32 + len(commands)] = commands
    struct.pack_into("<I", data, 0x1000, 0x14000008)
    data[0x2000:0x2100] = b"\x42" * 256
    data[0x800:0x820] = hmac.digest(b"\0", data[0x1000:0x2000], "sha256")
    return bytes(data)


class KernelLogTests(unittest.TestCase):
    def test_fat_same_size_replacement_preserves_metadata_and_padding(self):
        original = fat_fixture()
        volume = fat.RamFat32(original)
        self.assertEqual(volume.read("KERNEL.MACHO"), b"a" * 512 + b"b" * 88)
        updated = volume.replace_same_size("kernel.macho", b"c" * 600)
        self.assertEqual(updated.read("kernel.macho"), b"c" * 600)
        self.assertEqual(updated.data[:16896 + 512], original[:16896 + 512])
        self.assertEqual(updated.data[16896 + 1112:], original[16896 + 1112:])
        with self.assertRaisesRegex(ValueError, "exact file size"):
            volume.replace_same_size("kernel.macho", b"x")

    def test_fat_rejects_name_checksum_and_chain_cycles(self):
        changed = bytearray(fat_fixture())
        changed[16896 + 13] ^= 1
        with self.assertRaisesRegex(ValueError, "long name"):
            fat.RamFat32(changed).read("kernel.macho")
        changed = bytearray(fat_fixture())
        struct.pack_into("<I", changed, 512 + 4 * 4, 3)
        changed[8704:16896] = changed[512:8704]
        with self.assertRaisesRegex(ValueError, "cyclic"):
            fat.RamFat32(changed).read("kernel.macho")

    @unittest.skipUnless(shutil.which("clang"), "clang is required for witness assembly")
    def test_witness_keeps_original_text_entry_words_and_post_hmac(self):
        original = kernel_fixture()
        before = log.MachKernel(original)
        with tempfile.TemporaryDirectory() as directory:
            changed, report = log.patch_kernel(original, Path(directory),
                                               expected_sha=hashlib.sha256(original).hexdigest())
        after = log.MachKernel(changed)
        self.assertNotEqual(after.entry, before.entry)
        self.assertEqual(changed[0x1000:0x2100], original[0x1000:0x2100])
        self.assertEqual(changed[0x800:0x820], original[0x800:0x820])
        self.assertTrue(report["original_entry_words_unchanged"])
        self.assertTrue(report["post_hmac_matches_text"])
        self.assertTrue(report["kernel_file_length_unchanged"])
        after.integrity()

    def test_post_corruption_is_not_silently_resealed(self):
        changed = bytearray(kernel_fixture())
        changed[0x1004] ^= 1
        with self.assertRaisesRegex(ValueError, "POST integrity"):
            log.MachKernel(changed).integrity()

    def test_native_console_config_is_bounded_and_preserves_other_settings(self):
        source = (
            'version = 1\n[boot]\ntimeout = 5\ndefault = "chiron-ram-init"\n'
            '[kits]\nautoload = false\n[[entry]]\nid = "chiron-ram-init"\n'
            'title = "Chiron native 4K XNU / RAM init"\ntype = "xnu"\n'
            'path = "Payloads/kernel.macho"\ninitrd = "Payloads/launchd.macho"\n'
            'args = "-v serial=3 debug=0x14e cpus=1 rd=md0 keepsyms=1 kt_fixed_zones=120"\n'
            '[[entry]]\nid = "firmware"\ntitle = "Return to firmware"\ntype = "action"\naction = "exit"\n'
        ).encode()
        changed, report = log.console_config(source)
        before, after = tomllib.loads(source.decode()), tomllib.loads(changed.decode())
        self.assertEqual(len(changed), len(source))
        self.assertEqual(after["boot"], before["boot"])
        self.assertEqual(after["kits"], before["kits"])
        self.assertEqual(after["entry"][1], before["entry"][1])
        args = after["entry"][0]["args"].split()
        self.assertIn("serial=0", args)
        self.assertIn("-enable_kprintf_spam", args)
        self.assertIn("startup_debug=1", args)
        self.assertIn("rd=md0", args)
        self.assertNotIn("serial=3", args)
        self.assertEqual(report["old_arguments"], before["entry"][0]["args"])


if __name__ == "__main__":
    unittest.main()
