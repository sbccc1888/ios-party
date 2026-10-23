#!/usr/bin/env python3
"""Retain HackiOS's native Qualcomm USB path for the MIX2 phone loader."""

import argparse
from pathlib import Path

from apply_chiron import replace_once


def apply(source):
    path = source / "xnuloader/loader.c"
    text = path.read_text()
    text = replace_once(
        text,
        "\tif (PLAT == &platform_chiron) {\n\t\tif (kernel_mpidr != this_mpidr || more_cores) {",
        "\tif (PLAT == &platform_chiron) {\n\t\tmore_cores = 0;\n\t\tif (kernel_mpidr != this_mpidr) {",
    )
    text = replace_once(
        text,
        "\tefi_status s = USB->start_ex(&set);",
        '\tprint("usb: Qualcomm protocol revision %x\\n", USB->revision);\n'
        "\tif (USB->start_ex == NULL || USB->send == NULL || USB->handle_event == NULL ||\n"
        "\t    USB->allocate_transfer_buffer == NULL || USB->free_transfer_buffer == NULL) {\n"
        '\t\tprint("usb: incomplete Qualcomm USB interface\\n");\n'
        "\t\treturn 0;\n\t}\n"
        "\tefi_status s = USB->start_ex(&set);",
    )
    text = replace_once(
        text, 'if (str_eq(var, "product")) {',
        'if (str_eq(var, "board")) {\n'
        '\t\t\t\tfb_reply("OKAY", PLAT->name);\n'
        '\t\t\t} else if (str_eq(var, "product")) {',
    )
    text = replace_once(
        text,
        '\t\t\t\tfb_reply("OKAY", download_to_ramdisk ? "0xffffffff" : "0x03f00000");',
        '\t\t\t\tif (download_to_ramdisk && PLAT == &platform_chiron) {\n'
        '\t\t\t\t\tuint64_t mark = log_len;\n'
        '\t\t\t\t\tuint64_t limit = win_size - RESIDENT_RD_OFFSET - RESIDENT_RD_RESERVE;\n'
        '\t\t\t\t\tprint("0x%x", limit);\n'
        '\t\t\t\t\tlog_buf[log_len] = 0;\n'
        '\t\t\t\t\tfb_reply("OKAY", log_buf + mark);\n'
        '\t\t\t\t\tlog_len = (uint32_t)mark;\n'
        '\t\t\t\t} else {\n'
        '\t\t\t\t\tfb_reply("OKAY", download_to_ramdisk ? "0xffffffff" : "0x03f00000");\n'
        '\t\t\t\t}',
    )
    path.write_text(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    apply(ap.parse_args().source)


if __name__ == "__main__":
    main()
