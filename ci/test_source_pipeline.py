#!/usr/bin/env python3
"""Gate the source observer and its actual transport, independently of retired probes."""

import sys
import unittest
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tests"))
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    # The source pipeline does not rebuild/recompress DarwinBoot firmware volumes
    # or use the archived kernel/handoff binary insertion tools.
    for name in ("test_bringup", "test_entry_probe", "test_source_trace",
                 "test_runtime_selftest_gate", "test_ram_selftest", "test_ufs_readonly",
                 "test_foundation_runtime", "test_ramfs", "test_ramfs_probe",
                 "test_ramfs_runtime", "test_userspace_audit",
                 "test_page_qualification", "test_abi_probe", "test_ufs_handoff"):
        suite.addTests(loader.loadTestsFromName(name))
    for name in (
        "test_fat_same_size_replacement_preserves_metadata_and_padding",
        "test_fat_rejects_name_checksum_and_chain_cycles",
    ):
        suite.addTests(loader.loadTestsFromName("test_kernel_log.KernelLogTests." + name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
