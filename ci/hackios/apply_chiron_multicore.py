#!/usr/bin/env python3
"""Keep the MIX 2 booting on more than one core.

The first chiron port pins the kernel to the boot processor (more_cores = 0 in
apply_chiron_usb.py, cpu_count = 1 in chiron-profile.json).  That is a workaround,
not a property of the device: MSM8998 brings cores up with PSCI CPU_ON, and the
kernel already does that itself (GenericARMPE -> psci_smp_start_cpu).  This patch
lifts the pin so several processors run, and records what had to be right for it
to work.

Two things had to be true first, both of them in this repository already:

  * the EL1 virtual timer is PPI 19 on MSM8998, not Apple's 27.  With the wrong
    number a starting core takes no clock interrupt and stops inside cpu_init()
    on its way in.  That is what `virtual-timer-intid` in the device tree is for
    (apply_chiron.py writes it, the kernel reads it).

  * `cpus=` reaches the kernel, since the loader's own default is one processor.
    The payload's command line carries it.

Nothing else about the device changes: the same SMC conduit, the same GICv3
geometry, the same GOP mode.  A core that fails to start is reported by the
kernel's own psci diagnostics rather than hidden.
"""

import argparse
from pathlib import Path

from apply_chiron import replace_once


def apply(source):
    path = source / "xnuloader/loader.c"
    text = path.read_text()

    # 1. apply_chiron_usb.py wrote "more_cores = 0" for chiron; take that back.
    text = replace_once(
        text,
        "\tif (PLAT == &platform_chiron) {\n\t\tmore_cores = 0;\n\t\tif (kernel_mpidr != this_mpidr) {",
        "\tif (PLAT == &platform_chiron) {\n\t\tif (kernel_mpidr != this_mpidr) {",
    )

    # 2. a place to say how many processors the payload asked for.
    text = replace_once(
        text,
        '\tprint("platform %s\\n", PLAT->name);',
        '\tprint("platform %s\\n", PLAT->name);\n'
        '\tprint("multicore: more_cores=%d boot mpidr=%x this mpidr=%x\\n",\n'
        '\t    (uint64_t)(more_cores ? 1 : 0), kernel_mpidr, this_mpidr);',
    )
    path.write_text(text)
    return {"xnuloader/loader.c": "patched"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    print(apply(ap.parse_args().source))


if __name__ == "__main__":
    main()
