"""Offline production extraction, corruption rejection, and source identity gates."""
import collections
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import chiron_page_qualification as qualification


class SourceGuardTests(unittest.TestCase):
    def test_missing_source_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "required pinned source missing"):
                qualification.checked_sources(temp, qualification.SOURCE_SHA256)

    def test_changed_source_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            name = next(iter(qualification.SOURCE_SHA256))
            path = Path(temp) / name
            path.parent.mkdir(parents=True)
            path.write_text("/* changed source, never substitute a model */\n")
            with self.assertRaisesRegex(ValueError, "source fingerprint mismatch"):
                qualification.checked_sources(temp, qualification.SOURCE_SHA256)

    def test_ambiguous_extraction_is_failure(self):
        with self.assertRaisesRegex(ValueError, "not unique"):
            qualification.between("start end start", "start", "end")
        with self.assertRaisesRegex(ValueError, "not unique"):
            qualification.function("f()\n{}\nf()\n{}", "f()")

    def test_cli_missing_source_nonzero_no_fake_success(self):
        with tempfile.TemporaryDirectory() as temp:
            ran = subprocess.run([
                sys.executable, str(ROOT / "tools/chiron_page_qualification.py"),
                "--source-root", temp, "--json",
            ], text=True, capture_output=True, check=False)
        self.assertEqual(ran.returncode, 1)
        self.assertEqual(json.loads(ran.stderr)["status"], "failed")
        self.assertEqual(ran.stdout, "")


@unittest.skipUnless(
    sys.platform == "darwin"
    and all((qualification.SOURCE_DEFAULT / name).is_file() for name in qualification.SOURCE_SHA256)
    and all((qualification.UPSTREAM_DEFAULT / name).is_file() for name in qualification.UPSTREAM_SHA256),
    "production extraction needs the pinned local mirrors and macOS CommonCrypto; no runtime qualification",
)
class ProductionAlgorithmTests(unittest.TestCase):
    # The standalone qualifier fails on missing sources; CI without mirrors
    # reports an explicit skip, never a positive qualification.
    @classmethod
    def setUpClass(cls):
        cls.source = qualification.checked_sources(
            qualification.SOURCE_DEFAULT, qualification.SOURCE_SHA256)
        cls.report = qualification.qualify()

    def named(self, name, mode=1):
        return [case for case in self.report["cases"]
                if case["name"] == name and case["compiled_bridge"] == mode]

    def test_default_off_and_evidence_scope(self):
        self.assertEqual(self.report["configuration"]["upstream_default"], 0)
        self.assertFalse(any(self.report["configuration"]["defconfig_bridge_entries"].values()))
        self.assertFalse(self.report["bridge_enabled_by_this_tool"])
        self.assertFalse(self.report["kernel_or_device_qualified"])
        self.assertIsNone(self.report["target_cache_compatible"])
        self.assertEqual(len(self.named("default-off-rejects-16K-CodeDirectory", 0)), 1)

    def test_every_subpage_corruption_before_and_after_publication(self):
        for name in ("each-original-subpage-corruption-rejects-whole-block",
                     "each-post-publication-4K-corruption-tainted"):
            cases = self.named(name)
            self.assertEqual(len(cases), 16)
            self.assertEqual({(c["hash_type"], c["page_offset"]) for c in cases},
                             {(h, p * 4096) for h in (1, 2, 3, 4) for p in range(4)})

    def test_tails_layout_overflow_and_cleanup(self):
        self.assertEqual({c["code_limit"] for c in self.named("tail-original-block-authenticates")},
                         {1, 4095, 4096, 4097, 16383, 16384, 16385, 20481, 32768})
        for name in ("scatter-bridge-layout-rejected", "firstlevel-bridge-layout-rejected",
                     "base-plus-second-block-signed-overflow-rejected",
                     "hash-layout-outside-signature-rejected",
                     "derived-table-one-byte-short-rejected",
                     "readonly-publication-failure-cleaned", "host-derived-storage-cleaned"):
            self.assertTrue(self.named(name), name)

    def test_pager_boundaries_are_explicitly_reproduced(self):
        counts = collections.Counter(c["name"] for c in self.report["cases"])
        self.assertEqual(counts["16K-copy-exceeds-logical-4K-by-12288"], 8)
        self.assertEqual(counts["four-4K-faults-reuse-slide-index-zero"], 8)
        for name in ("oversize-copy-precedes-outside-range-check",
                     "source-uint32-index-wrap-reproduced",
                     "v5-chain-start-4096-outside-4K-logical-page",
                     "v5-last-16K-pointer-outside-4K-logical-page",
                     "candidate-guard-rejects-16K-on-4K"):
            self.assertEqual(counts[name], 2)

    def test_original_block_comparator_mutation_is_caught(self):
        unit = qualification.generated_source(self.source)
        original = "if (bcmp(expected, digest, md->cs_size)) {"
        self.assertEqual(unit.count(original), 1)
        # Mutation only in the temporary host translation unit; never in XNU.
        mutated = unit.replace(original, "if (0) {")
        with self.assertRaisesRegex(ValueError, "host cases failed"):
            qualification.run_host(mutated)

    def test_fault_page_comparator_mutation_is_caught(self):
        unit = qualification.generated_source(self.source)
        original = "if (bcmp(expected_hash, actual_hash, hashtype->cs_size) != 0) {"
        self.assertEqual(unit.count(original), 1)
        mutated = unit.replace(original, "if (0) {")
        with self.assertRaisesRegex(ValueError, "host cases failed"):
            qualification.run_host(mutated)


if __name__ == "__main__":
    unittest.main()
