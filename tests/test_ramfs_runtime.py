"""Guard the normal build while deriving an explicit relink-only experiment."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ramfs_runtime_test", ROOT / "ci/test_ramfs_runtime.py")
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class RecipeTests(unittest.TestCase):
    def test_relink_skips_source_patch_application_and_keeps_make_flags(self):
        text = (
            '#!/usr/bin/env bash\nset -euo pipefail\nROOT=/tree\n'
            'if [ ! -d refer/xnu/.git ]; then\n  git clone source\nfi\n'
            'git -C .cache/xnu-work apply patches\npython3 scripts/prepare-xnu-source.py\n'
            'BOOT_PROGRESS=${XNU_BOOT_PROGRESS:-0}\n'
            'make -C .cache/xnu-work TARGET=DEVELOPMENT CFLAGS_EXTRA="$EXTRA_CFLAGS"\n'
        )
        recipe = RUNTIME.relink_recipe(text)
        self.assertNotIn("git clone", recipe)
        self.assertNotIn("git -C", recipe)
        self.assertNotIn("prepare-xnu-source", recipe)
        self.assertIn('CFLAGS_EXTRA="$EXTRA_CFLAGS"', recipe)
        self.assertIn("XNU_AVAILABILITY_PL", recipe)
        self.assertTrue(recipe.startswith("#!/usr/bin/env bash\n"))
        for changed in (
            text.replace("BOOT_PROGRESS=", "CHANGED="),
            text + 'BOOT_PROGRESS=${XNU_BOOT_PROGRESS:-0}\n',
            text.replace("make -C .cache/xnu-work ", "echo "),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                RUNTIME.relink_recipe(changed)


if __name__ == "__main__":
    unittest.main()
