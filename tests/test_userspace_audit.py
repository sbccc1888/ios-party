"""Own synthetic parser fixtures; these do not qualify any target iOS build."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/audit_chiron_userspace.py"
SPEC = importlib.util.spec_from_file_location("userspace_audit", TOOL)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
OWN_UUID = uuid.UUID("8ee27d80-1920-4677-bfa7-8623b34382aa")


def string_command(command, value, dylib=False):
    minimum = 24 if dylib else 12
    length = (minimum + len(value) + 1 + 7) & ~7
    result = bytearray(length)
    struct.pack_into("<III", result, 0, command, length, minimum)
    result[minimum:minimum + len(value)] = value
    return bytes(result)


def signed_image(shift=12, version=0x20001, subtype=0, extra=(), entitlements=False):
    """A deliberately non-executable own file with genuine code-slot digests."""
    sigoff = 8193
    commands = [struct.pack("<II16s", 0x1B, 24, OWN_UUID.bytes),
                struct.pack("<6I", 0x32, 24, 2, 0x110000, 0x110000, 0), *extra]
    commands.append(struct.pack("<4I", 0x1D, 16, sigoff, 0))
    image = bytearray(sigoff)
    raw = b"".join(commands)
    struct.pack_into("<8I", image, 0, 0xFEEDFACF, AUDIT.ARM64, subtype, 2,
                     len(commands), len(raw), 0, 0)
    image[32:32 + len(raw)] = raw
    minimum = 44
    for threshold, size in ((0x20100, 48), (0x20200, 52), (0x20300, 64),
                            (0x20400, 88), (0x20500, 96), (0x20600, 108)):
        if version >= threshold:
            minimum = size
    block = 1 << shift
    slots = (sigoff + block - 1) // block
    identifier = b"org.ios-party.synthetic-audit\0"
    hash_offset = minimum + len(identifier)
    cd = bytearray(hash_offset + slots * 32)
    struct.pack_into(">9I4BI", cd, 0, 0xFADE0C02, len(cd), version, 2,
                     hash_offset, minimum, 0, slots, sigoff, 32, 2, 0, shift, 0)
    cd[minimum:hash_offset] = identifier
    count = 2 if entitlements else 1
    index_end = 12 + count * 8
    ent = struct.pack(">II", 0xFADE7171, 10) + b"{}" if entitlements else b""
    total = index_end + len(cd) + len(ent)
    # LC_CODE_SIGNATURE is itself hashed, so fill it before computing digests.
    struct.pack_into("<I", image, 32 + len(raw) - 4, total)
    for index in range(slots):
        cd[hash_offset + index * 32:hash_offset + (index + 1) * 32] = hashlib.sha256(
            image[index * block:min((index + 1) * block, sigoff)]).digest()
    signature = struct.pack(">III", 0xFADE0CC0, total, count)
    signature += struct.pack(">II", 0, index_end)
    if entitlements:
        signature += struct.pack(">II", 5, index_end + len(cd))
    return bytes(image) + signature + bytes(cd) + ent


def fat_image(image, wide=False, endian=">"):
    offset = 4096
    header = struct.pack(endian + "II", 0xCAFEBABF if wide else 0xCAFEBABE, 1)
    fields = (AUDIT.ARM64, 0, offset, len(image), 12)
    header += struct.pack(endian + ("IIQQII" if wide else "5I"),
                          *fields, *((0,) if wide else ()))
    return header + bytes(offset - len(header)) + image


def cache_prefix(identity=OWN_UUID):
    return AUDIT.CACHE_PREFIX.pack(
        b"dyld_v1   arm64".ljust(16, b"\0"), 104, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, identity.bytes)


def codes(value):
    return {item["code"] for item in value["issues"]}


class MachOTests(unittest.TestCase):
    def test_signed_image_hash_uuid_and_short_last_page(self):
        data = signed_image()
        parsed = AUDIT.parse_macho(data)
        image = parsed["slices"][0]
        self.assertEqual(image["uuid"], str(OWN_UUID))
        self.assertEqual(image["architecture"], "arm64")
        self.assertEqual(image["issues"], [])
        signature = image["signature"]
        cd = signature["code_directories"][0]
        self.assertTrue(cd["code_hashes_verified"])
        self.assertEqual(cd["code_slots"], 3)
        self.assertEqual(cd["page_shift"], 12)
        self.assertTrue(cd["coverage"]["covers_all_bytes_before_signature"])
        self.assertFalse(signature["apple_trust_verified"])
        self.assertEqual(signature["entitlements"], {"xml": False, "der": False})

    def test_16k_integrity_is_separate_from_4k_admission(self):
        image = AUDIT.parse_macho(signed_image(shift=14))["slices"][0]
        self.assertTrue(image["signature"]["code_directories"][0]["code_hashes_verified"])
        self.assertIn("native_4k_signature", codes(image))

    def test_corrupted_code_hash_rejected(self):
        data = bytearray(signed_image())
        data[4096] ^= 1
        image = AUDIT.parse_macho(data)["slices"][0]
        self.assertIn("code_hash_mismatch", codes(image))
        self.assertFalse(image["signature"]["code_directories"][0]["code_hashes_verified"])

    def test_xml_presence_never_grants_entitlements(self):
        image = AUDIT.parse_macho(signed_image(entitlements=True))["slices"][0]
        self.assertTrue(image["signature"]["entitlements"]["xml"])
        self.assertIn("entitlement_policy", codes(image))
        self.assertFalse(image["signature"]["apple_trust_verified"])

    def test_all_verified_code_directory_header_versions(self):
        for version in (0x20001, 0x20100, 0x20200, 0x20300, 0x20400, 0x20500, 0x20600):
            with self.subTest(version=hex(version)):
                image = AUDIT.parse_macho(signed_image(version=version))["slices"][0]
                self.assertEqual(image["issues"], [])

    def test_scatter_and_future_directory_versions_refused(self):
        for version, field, value, expected in (
                (0x20100, 44, 48, "scatter_signature"),
                (0x20500, 92, 96, "pre_encryption_signature"),
                (0x20600, 96, 1 << 24, "linkage_signature"),
                (0x20001, 8, 0x20700, "code_directory_version")):
            data = bytearray(signed_image(version=version))
            cd = 8193 + 20
            struct.pack_into(">I", data, cd + field, value)
            image = AUDIT.parse_macho(data)["slices"][0]
            self.assertIn(expected, codes(image))
            self.assertFalse(image["signature"]["code_directories"][0]["code_hashes_verified"])

    def test_subtype_capabilities_not_masked_away(self):
        for subtype in (2, 0x80000002, 0x80000000):
            with self.subTest(subtype=subtype):
                image = AUDIT.parse_macho(signed_image(subtype=subtype))["slices"][0]
                self.assertEqual(image["cpu_subtype"], subtype)
                self.assertIn("architecture", codes(image))

    def test_load_dylinker_dylib_and_rpath(self):
        extra = (string_command(0xE, b"/usr/lib/dyld"),
                 string_command(0xC, b"/usr/lib/libSystem.B.dylib", True),
                 string_command(0x8000001C, b"@executable_path/Frameworks"))
        image = AUDIT.parse_macho(signed_image(extra=extra))["slices"][0]
        self.assertEqual(image["load_dylinker"], ["/usr/lib/dyld"])
        self.assertEqual(image["load_dylib"][0]["path"], "/usr/lib/libSystem.B.dylib")
        self.assertEqual(image["rpaths"], ["@executable_path/Frameworks"])

    def test_unterminated_load_command_string(self):
        command = bytearray(string_command(0xE, b"/usr/lib/dyld"))
        command[12:] = b"x" * (len(command) - 12)
        with self.assertRaisesRegex(AUDIT.AuditError, "NUL"):
            AUDIT.parse_macho(signed_image(extra=(command,)))

    def test_load_command_count_size_and_extent(self):
        for offset, value in ((16, 100), (20, 0xFFFFFFFF), (32 + 4, 0), (32 + 4, 23)):
            data = bytearray(signed_image())
            struct.pack_into("<I", data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(AUDIT.AuditError):
                AUDIT.parse_macho(data)

    def test_signature_bounds_and_overlap(self):
        original = signed_image()
        for field, value in ((4, 0xFFFFFFFF), (8, 9999), (16, 12)):
            data = bytearray(original)
            struct.pack_into(">I", data, 8193 + field, value)
            image = AUDIT.parse_macho(data)["slices"][0]
            self.assertIn("signature_rejected", codes(image))
            self.assertEqual(image["uuid"], str(OWN_UUID))

    def test_hash_slot_coverage_and_bounds(self):
        for field, value in ((16, 1), (28, 0xFFFFFFFF), (32, 8192)):
            data = bytearray(signed_image())
            struct.pack_into(">I", data, 8193 + 20 + field, value)
            image = AUDIT.parse_macho(data)["slices"][0]
            self.assertTrue(codes(image) & {"signature_rejected", "incomplete_coverage"})

    def test_fat32_fat64_each_endianness(self):
        for wide in (False, True):
            for endian in ("<", ">"):
                with self.subTest(wide=wide, endian=endian):
                    result = AUDIT.parse_macho(fat_image(signed_image(), wide, endian))
                    image = result["slices"][0]
                    self.assertEqual(result["format"], "FAT64" if wide else "FAT32")
                    self.assertEqual(image["offset"], 4096)
                    self.assertEqual(image["issues"], [])

    def test_fat_mismatch_alignment_truncation_and_reserved(self):
        for offset, value, wide in ((8, 7, False), (16, 4097, False),
                                    (24, 64, False), (36, 1, True)):
            data = bytearray(fat_image(signed_image(), wide))
            struct.pack_into(">I", data, offset, value)
            with self.subTest(offset=offset, wide=wide), self.assertRaises(AUDIT.AuditError):
                AUDIT.parse_macho(data)
        with self.assertRaises(AUDIT.AuditError):
            AUDIT.parse_macho(fat_image(signed_image())[:-1])

    def test_unsupported_or_truncated_inputs(self):
        for data in (b"", b"\xcf\xfa\xed\xfe", b"not Mach-O", b"\xce\xfa\xed\xfe" + bytes(64)):
            with self.subTest(data=data[:8]), self.assertRaises(AUDIT.AuditError):
                AUDIT.parse_macho(data)

    def test_existing_own_macho_cross_check_without_build_or_execution(self):
        path = ROOT / "dist/source-trace-39770335838/qemu-logs/user-sigbus-capture/launchd.macho"
        if not path.is_file():
            self.skipTest("optional existing own fixture absent")
        buildspec = importlib.util.spec_from_file_location("own_selftest_parser",
                                                         ROOT / "ci/build_ram_selftest.py")
        own = importlib.util.module_from_spec(buildspec)
        buildspec.loader.exec_module(own)
        data = path.read_bytes()
        image = AUDIT.parse_macho(data)["slices"][0]
        self.assertLessEqual(codes(image), {"special_slot_validation"})
        cd = image["signature"]["code_directories"][0]
        original = own.signature_info(data)
        self.assertEqual(cd["cdhash"], original["cdhash"])
        self.assertEqual(cd["code_limit"], original["code_limit"])
        self.assertEqual(cd["page_bytes"], original["signature_hash_page_bytes"])


class CacheTests(unittest.TestCase):
    def test_identity_prefix_only_is_always_refused(self):
        result = AUDIT.parse_cache(cache_prefix())
        self.assertEqual(result["uuid"], str(OWN_UUID))
        self.assertEqual(result["architecture"], "arm64")
        self.assertIn("cache_layout_unsupported", codes(result))
        self.assertFalse(result["subcache_discovery_verified"])
        self.assertFalse(result["subcache_identity_verified"])

    def test_unknown_cache_version_and_bad_extent(self):
        for offset, replacement in ((0, b"dyld_v9"), (16, struct.pack("<I", 103)),
                                     (16, struct.pack("<I", 105))):
            data = bytearray(cache_prefix())
            data[offset:offset + len(replacement)] = replacement
            with self.subTest(offset=offset), self.assertRaises(AUDIT.AuditError):
                AUDIT.parse_cache(data)
        with self.assertRaises(AUDIT.AuditError):
            AUDIT.parse_cache(cache_prefix()[:-1])


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="chiron-userspace-audit-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, entries, **values):
        path = self.root / "inputs.json"
        value = {"schema_version": 1, "files": entries, **values}
        path.write_text(json.dumps(value))
        return path

    def test_no_inputs_returns_machine_readable_missing_and_no_version_guess(self):
        report = AUDIT.audit()
        self.assertEqual(report["target"]["ProductVersion"], None)
        self.assertEqual(report["target"]["BuildVersion"], None)
        self.assertIn("no_target_inputs", codes(report))
        self.assertFalse(report["compatibility_verified"])
        self.assertFalse(report["admission"]["eligible"])

    def test_empty_directory_missing_roles_and_files(self):
        report = AUDIT.audit(self.root)
        self.assertEqual(len(report["files"]), 3)
        self.assertTrue(all(r["status"] == "missing" for r in report["files"]))
        self.assertIn("missing_runtime_libraries", codes(report))
        self.assertFalse(report["target"]["identity_verified"])

    def test_manifest_paths_and_duplicate_keys_fail_closed(self):
        for name in ("../escape", "/absolute", "a/../b", "a//b", "./a", "a\\b", ""):
            path = self.manifest([{"path": name}])
            with self.subTest(name=name), self.assertRaises(AUDIT.AuditError):
                AUDIT.load_manifest(path)
        path.write_text('{"schema_version":1,"files":[],"files":[]}')
        with self.assertRaisesRegex(AUDIT.AuditError, "duplicate"):
            AUDIT.load_manifest(path)

    def test_manifest_type_errors_reported(self):
        for entry in ({"path": "a", "kind": []}, {"path": "a", "role": {}},
                      {"path": "a", "uuid": 1}, {"path": "a", "sha256": True}):
            path = self.manifest([entry])
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                AUDIT.load_manifest(path)

    def test_symlink_file_parent_and_fifo_refused_without_following(self):
        (self.root / "own").write_bytes(signed_image())
        (self.root / "link").symlink_to(self.root / "own")
        (self.root / "parent").symlink_to(self.root, target_is_directory=True)
        os.mkfifo(self.root / "pipe")
        for name in ("link", "parent/own", "pipe"):
            record, _ = AUDIT.inspect_file(self.root, {"path": name}, AUDIT.MAX_TOTAL_BYTES)
            with self.subTest(name=name):
                self.assertEqual(record["status"], "rejected")
                self.assertIn("input_rejected", codes(record))

    def test_hash_uuid_and_missing_companion(self):
        (self.root / "main").write_bytes(cache_prefix())
        path = self.manifest([
            {"path": "main", "kind": "cache", "role": "cache", "sha256": "00" * 32,
             "uuid": "00000000-0000-0000-0000-000000000000"},
            {"path": "main.01", "kind": "cache", "role": "cache_subcache"},
        ])
        report = AUDIT.audit(manifest=path)
        self.assertIn("sha256_mismatch", codes(report))
        self.assertIn("uuid_mismatch", codes(report))
        self.assertIn("missing_file", codes(report))
        self.assertIn("cache_companions_unverified", codes(report))
        self.assertFalse(report["admission"]["eligible"])

    def test_dependency_missing_and_unresolved(self):
        data = signed_image(extra=(string_command(0xE, b"/usr/lib/dyld"),
                                   string_command(0xC, b"@rpath/missing.dylib", True)))
        (self.root / "own").write_bytes(data)
        report = AUDIT.audit(manifest=self.manifest([{"path": "own", "kind": "macho"}]))
        self.assertEqual({d["status"] for d in report["dependencies"]}, {"missing", "unresolved"})
        self.assertIn("dependency_missing", codes(report))
        self.assertIn("dependency_unresolved", codes(report))

    def test_structurally_complete_synthetic_inventory_is_not_ios_qualification(self):
        (self.root / "version.plist").write_bytes(plistlib.dumps({
            "ProductVersion": "0.0", "ProductBuildVersion": "0A0"}))
        entries = [{"path": "version.plist", "kind": "system_version", "role": "system_version"}]
        for role in ("dyld", "launchd", "libsystem"):
            (self.root / role).write_bytes(signed_image())
            entries.append({"path": role, "kind": "macho", "role": role})
        path = self.manifest(entries, ProductVersion="0.0", BuildVersion="0A0",
                             source="own synthetic fixtures; not an Apple system")
        report = AUDIT.audit(manifest=path)
        self.assertTrue(report["target"]["identity_verified"])
        self.assertTrue(report["structural_audit_complete"])
        self.assertTrue(report["unverified"])
        self.assertFalse(report["admission"]["eligible"])
        self.assertFalse(report["compatibility_verified"])
        conflict = AUDIT.audit(manifest=path, build_version="0A1")
        self.assertIn("target_identity_conflict", codes(conflict))
        self.assertFalse(conflict["target"]["identity_verified"])

    def test_byte_limit_and_opaque_role_do_not_pass(self):
        (self.root / "own").write_bytes(signed_image())
        record, _ = AUDIT.inspect_file(self.root, {"path": "own"}, 10)
        self.assertIn("input_rejected", codes(record))
        self.assertNotIn("sha256", record)
        path = self.manifest([{"path": "own", "kind": "data", "role": "dyld"}])
        self.assertIn("role_format_mismatch", codes(AUDIT.audit(manifest=path)))

    def test_cli_no_inputs_and_bad_manifest_emit_json_and_nonzero(self):
        for arguments, expected in (([], 1), (["--manifest", str(self.root / "missing.json")], 2)):
            result = subprocess.run([sys.executable, str(TOOL), *arguments],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, expected)
            report = json.loads(result.stdout)
            self.assertFalse(report["admission"]["eligible"])
            self.assertFalse(report["compatibility_verified"])
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
