#!/usr/bin/env python3
"""Make the phone loader accept only the early reserved PEI memory window."""

from pathlib import Path
import shutil

from apply_chiron import replace_once


def apply(source):
    path = source / "xnuloader/loader.c"
    text = replace_once(
        path.read_text(), "static int\nchoose_window(const struct payload_info *pi)",
        '#include "chiron_memory_window.inc"\n\n'
        "static int\nchoose_window(const struct payload_info *pi)",
    )
    text = replace_once(
        text,
        "\tif (win_size != 0) {\n\t\treturn 1;\n\t}\n",
        "\tif (win_size != 0) {\n\t\treturn 1;\n\t}\n"
        "\tif (chiron_mode) return chiron_preclaimed_window(pi);\n",
    )
    shutil.copy2(Path(__file__).with_name("memory_loader.inc"),
                 source / "xnuloader/chiron_memory_window.inc")
    path.write_text(text)
