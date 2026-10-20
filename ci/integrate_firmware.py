#!/usr/bin/env python3
"""Embed validated EFI binaries into the pinned MSM8998 firmware source."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from inspect_artifacts import inspect

EFI_FILES = {
    "UniXNU": ("APPLICATION", "9B665204-A133-47A2-9396-9E2B370A4E25"),
    "UniXNUDxe": ("DRIVER", "29B950CF-586D-49EA-9D71-F88645CCDAF6"),
    "DarwinBoot": ("DRIVER", "1B64AF87-8415-4B57-9B16-680D70913B9F"),
}


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"upstream context changed: {old[:100]}")
    return text.replace(old, new, 1)


def integrate(root, efi):
    destination = root / "ChironPrebuilt"
    destination.mkdir(exist_ok=False)
    hashes = {}
    for name in EFI_FILES:
        source = efi / (name + ".efi")
        inspect(source)
        shutil.copyfile(source, destination / source.name)
        hashes[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    dsc = root / "Platforms/Msm8998FamilyPkg/Msm8998FamilyPkg.dsc.inc"
    text = dsc.read_text()
    text = replace_once(text, "-march=armv8-a+lse -moutline-atomics", "-march=armv8-a -mno-outline-atomics")
    text = replace_once(text, " -DENABLE_LINUX_SIMPLE_MASS_STORAGE", "")
    mass_storage = "  Platforms/Msm8998FamilyPkg/LinuxSimpleMassStorage/LinuxSimpleMassStorage.inf"
    text = replace_once(text, mass_storage, "  # Omitted from the chiron bring-up firmware.")
    dsc.write_text(text)

    # Keep libraries used by platform memory/SMBIOS code, but do not build the GUI.
    simple = root / "GPLDrivers/Library/simple-init/SimpleInit.inc"
    text = simple.read_text()
    if text.count("[Components]") != 1:
        raise ValueError("unexpected SimpleInit include layout")
    simple.write_text(text.split("[Components]", 1)[0])

    fdf = root / "Platforms/Msm8998Pkg/Device/xiaomi/chiron/chiron.fdf"
    text = fdf.read_text()
    text = replace_once(text, "    INF Platforms/Msm8998FamilyPkg/LinuxSimpleMassStorage/LinuxSimpleMassStorage.inf", "")
    text = replace_once(text, "  INF src/main/SimpleInitMain.inf", "")
    embedded = []
    for name, (kind, guid) in EFI_FILES.items():
        depex = "\n    SECTION DXE_DEPEX = { TRUE }" if kind == "DRIVER" else ""
        embedded.append(
            f"  FILE {kind} = {guid} {{{depex}\n"
            f"    SECTION PE32 = ChironPrebuilt/{name}.efi\n"
            f"    SECTION UI = \"{name}\"\n"
            "  }\n"
        )
    text = replace_once(text, "[FV.FVMAIN_COMPACT]", "\n".join(embedded) + "\n[FV.FVMAIN_COMPACT]")
    fdf.write_text(text)

    boot = root / "Platforms/Msm8998FamilyPkg/Library/PlatformBootManagerLib/PlatformBm.c"
    text = boot.read_text()
    text = replace_once(text, "#define DP_NODE_LEN(Type)", (
        "STATIC EFI_GUID gChironUniXnuFileGuid = "
        "{0x9b665204,0xa133,0x47a2,{0x93,0x96,0x9e,0x2b,0x37,0x0a,0x4e,0x25}};\n\n"
        "#define DP_NODE_LEN(Type)"
    ))
    text = replace_once(text, '&gSimpleInitFileGuid, L"Simple Init"', '&gChironUniXnuFileGuid, L"UniXNU"')
    text = replace_once(text, (
        '  PlatformRegisterFvBootOption(\n'
        '      &gLinuxSimpleMassStorageGuid, L"USB Attached SCSI (UAS) Storage", LOAD_OPTION_ACTIVE);'
    ), "  // No mass-storage boot entry in this bring-up firmware.")
    text = text.replace("SimpleInitGUI", "UniXNU")
    boot.write_text(text)
    return {
        "efi_sha256": hashes, "mass_storage_app_included": False,
        "simpleinit_gui_included": False, "instruction_baseline": "armv8-a; no outline atomics",
        "hardware_boot_qualified": False,
        "limitations": ["Vendor DXE drivers are retained; no general no-write guarantee is asserted."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--efi", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = integrate(args.source, args.efi)
    args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
