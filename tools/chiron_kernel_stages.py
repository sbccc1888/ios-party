#!/usr/bin/env python3
"""Instrument one pinned source kernel's cold-start path, without disabling POST."""

import hmac
import struct
import subprocess
from pathlib import Path

from build_chiron_entry_probe import ROOT, read_aarch64_object, sha256
from chiron_kernel_log import SOURCE_KERNEL_SHA, MachKernel

LOW_VECTORS = 0xfffffff0073fa000
# Only position-independent, non-branch instructions may be displaced.
STAGES = (
    (1, 0xfffffff0073fb05c, 0xd510109f, "original start_first_cpu; before OSLAR"),
    (2, 0xfffffff0073fb060, 0xd5034fdf, "OSLAR write returned"),
    (3, 0xfffffff0073fb078, 0xf9400696, "low VBAR installed; reading boot memory parameters"),
    (4, 0xfffffff0073fb0d4, 0xd2800000, "both startup stack pointers established"),
    (5, 0xfffffff0073fb0f8, 0xb9401801, "bootstrap page tables cleared; reading Mach-O header"),
    (6, 0xfffffff0073fb288, 0xd5033b9f, "identity and kernel bootstrap tables built"),
    (7, 0xfffffff0073fb2a0, 0x9240bf20, "TCR write returned"),
    (8, 0xfffffff0073fb304, 0xd5033fdf, "TTBR0/TTBR1 and MAIR writes returned"),
    (9, 0xfffffff0073fb33c, 0xd518c000, "deferring native VBAR until MMU transition returns"),
    (10, 0xfffffff0073fb358, 0xf2e00001, "SCTLR MMU/cache enable and ISB returned"),
    (11, 0xfffffff0079e6b74, 0xd10343ff, "entered original arm_init C function"),
    (12, 0xfffffff0073f2498, 0xd10583ff, "entered original arm_vm_init C function"),
)
VM_STAGES = (
    (13, 0xfffffff0073f255c, 0x92dffe49, "arm_vm_init imported and bounded memory parameters"),
    (14, 0xfffffff0073f2580, 0x92407415, "early_random returned inside arm_vm_init"),
    (15, 0xfffffff0073f2df8, 0x8b080128, "Mach-O sections/page size read; before arm_vm_prot_init"),
    (16, 0xfffffff0073f2e0c, 0xd503201f, "arm_vm_prot_init returned; new tables not yet active"),
    (17, 0xfffffff0073f2fe8, 0x9240bd00, "physmap tables built; before switching kernel TTBR1"),
)
POST_VM_STAGES = (
    (18, 0xfffffff0073f2ff4, 0x912f4273, "new kernel TTBR1 active; before invalidating TTBR0"),
    (19, 0xfffffff0073f301c, 0xaa1603fb, "invalid TTBR0 installed and TLB maintenance returned"),
    (20, 0xfffffff0073f33c8, 0xaa1403e0, "before pmap_bootstrap"),
    (21, 0xfffffff0073f33e0, 0x9b087d37, "pmap_bootstrap returned; preparing early framebuffer reservation"),
    (22, 0xfffffff0073f3700, 0x910583ff, "arm_vm_init epilogue; saved registers and stack guard checked"),
    (23, 0xfffffff0079e72ec, 0xd503201f, "arm_vm_init returned to original arm_init caller"),
)


def writable_padding(model, minimum):
    """Select unsectioned, zero-filled, file-backed __DATA alignment padding."""
    for segment in model.segments:
        if segment["name"] != "__DATA" or segment["protection"] != 3:
            continue
        # Zerofill sections must not hide a hole between the file data and BSS.
        occupied = [
            section["address"] + section["bytes"]
            for section in segment["sections"] if section["bytes"]
            and section["flags"] & 255 not in (1, 12, 18)
        ]
        start = (max(occupied, default=segment["address"]) + 15) & ~15
        end = segment["address"] + segment["file_bytes"]
        if end - start < minimum:
            continue
        if any(section["bytes"] and section["address"] < start + minimum
               and start < section["address"] + section["bytes"]
               for section in segment["sections"]):
            continue
        offset = model.file_offset(start)
        if not any(model.data[offset:offset + minimum]):
            return start, offset
    raise ValueError("no unused zero-filled writable data padding")


def branch(source, destination):
    delta = destination - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError("unaligned or out-of-range diagnostic branch")
    return 0x14000000 | ((delta // 4) & 0x3ffffff)


def patch_stages(data, directory, clang="clang", vm_detail=False, post_vm=False):
    if sha256(data) != SOURCE_KERNEL_SHA:
        raise ValueError("unexpected source kernel fingerprint")
    model = MachKernel(data)
    text, field, old_hmac = model.integrity()
    start, end, address = model.padding(6144)
    # Catch unexpected layouts before assembling or changing any bytes.
    vm_detail = vm_detail or post_vm
    checkpoints = STAGES + (VM_STAGES if vm_detail else ()) + (POST_VM_STAGES if post_vm else ())
    storage = None
    if post_vm:
        storage, _ = writable_padding(model, 1440)
    for _, site, word, _ in checkpoints:
        if struct.unpack_from("<I", data, model.file_offset(site, executable=True))[0] != word:
            raise ValueError("unexpected startup instruction")
    for index in range(16):
        site = LOW_VECTORS + index * 128
        if struct.unpack_from("<I", data, model.file_offset(site, executable=True))[0] != 0x14000000:
            raise ValueError("low exception vector is not the expected self-loop")
    obj = Path(directory) / "kernel-stages.o"
    subprocess.run([
        clang, "--target=aarch64-none-elf",
        "-DCHIRON_ENTRY_DELTA=" + str(model.entry - address),
        "-DCHIRON_LOW_DELTA=" + str(LOW_VECTORS - address),
        *(["-DCHIRON_VM_DETAIL"] if vm_detail else []),
        *(["-DCHIRON_POST_VM", "-DCHIRON_DATA_DELTA=" + str(storage - address)]
          if post_vm else []),
        "-c", str(ROOT / "scripts/chiron-kernel-stages.S"), "-o", str(obj),
    ], check=True)
    program, symbols = read_aarch64_object(obj.read_bytes())
    if symbols.get("chiron_kernel_stages") != 0 or len(program) > end - start:
        raise ValueError("diagnostic program does not fit verified executable padding")
    modified = bytearray(data)
    modified[start:start + len(program)] = program
    changed_ranges = [(start, start + len(program)), (model.entry_field, model.entry_field + 8)]
    struct.pack_into("<Q", modified, model.entry_field, address)
    hooks = []
    for number, site, word, description in checkpoints:
        stub = address + symbols[f"ChironStage{number}"]
        displaced = address + symbols[f"ChironStage{number}Original"]
        resume = address + symbols[f"ChironStage{number}Resume"]
        # Stage 9 records, rather than executes, the premature high-VA VBAR write.
        replacement = 0xd503201f if number == 9 else word
        for location, value in (
            (site, branch(site, stub)), (displaced, replacement),
            (resume, branch(resume, site + 4)),
        ):
            offset = model.file_offset(location, executable=True)
            struct.pack_into("<I", modified, offset, value)
            changed_ranges.append((offset, offset + 4))
        hooks.append({
            "stage": number, "hex": f"{number:02X}", "description": description,
            "site_va": site, "stub_va": stub, "displaced_va": displaced,
            "resume_va": resume, "original_word": word,
            "displaced_word": replacement,
        })
    handler = address + symbols["ChironStageException"]
    for index in range(16):
        site = LOW_VECTORS + index * 128
        offset = model.file_offset(site, executable=True)
        struct.pack_into("<I", modified, offset, branch(site, handler))
        changed_ranges.append((offset, offset + 4))
    # This is our source diagnostic kernel. Recompute its measured-text HMAC,
    # exactly as a source build does; the actual POST implementation still runs.
    new_hmac = hmac.digest(
        b"\0", modified[text["offset"]:text["offset"] + text["bytes"]], "sha256"
    )
    modified[field["offset"]:field["offset"] + 32] = new_hmac
    changed_ranges.append((field["offset"], field["offset"] + 32))
    after = MachKernel(modified)
    after.integrity()
    ranges = sorted(changed_ranges)
    cursor = 0
    for left, right in ranges:
        if left > cursor and modified[cursor:left] != data[cursor:left]:
            raise ValueError("kernel patch exceeded declared ranges")
        cursor = max(cursor, right)
    if modified[cursor:] != data[cursor:]:
        raise ValueError("kernel patch exceeded declared ranges")
    return bytes(modified), {
        "source_kernel_sha256": SOURCE_KERNEL_SHA,
        "diagnostic_kernel_sha256": sha256(modified),
        "kernel_file_length_unchanged": len(modified) == len(data),
        "kernel_base": model.base, "original_entry": model.entry,
        "diagnostic_entry": address, "program_bytes": len(program),
        "padding_bytes": end - start, "symbols": symbols, "hooks": hooks,
        "vm_detail": vm_detail,
        "post_vm": post_vm, "writable_storage_va": storage,
        "writable_storage_bytes": 1440 if post_vm else 0,
        "low_vectors_va": LOW_VECTORS, "early_exception_handler_va": handler,
        "early_record_va": address + symbols["ChironStageRecord"],
        "original_text_unchanged": False,
        "post_hmac_recomputed_for_instrumented_text": True,
        "original_post_hmac": old_hmac.hex(), "diagnostic_post_hmac": new_hmac.hex(),
        "post_hmac_matches_text": True, "post_checks_disabled": False,
        "temporary_gop_mapping": {
            "root_pa": "0x4017a000", "root_slot": 2,
            "allocation": "next zero page in existing 16-page bootstrap reservation",
            "base": "0x9d400000", "bytes": 5 * 0x200000,
            "attributes": "device-nGnRnE, privileged RW, PXN/UXN",
        },
        "limits": [
            "Source diagnostic kernel; changed text has a newly computed POST HMAC.",
            "The full original QEMU PID-1 qualification does not cover this instrumented kernel.",
            ("Hooks extend through arm_vm_init return; GIC, timer and PID 1 remain unobserved."
             if post_vm else
             "Hooks stop before arm_vm_init switches TTBR1; its return, GIC, timer and PID 1 are unobserved."
             if vm_detail else
             "Hooks cover cold startup through arm_vm_init entry, not its return, GIC, timer or PID 1."),
            "The low-vector recorder is active until stage 0A restores native exception handling.",
            "Stage output can stop if later page tables remove the temporary identity GOP mapping.",
            "Shared hook scratch and TPIDR_EL0 borrowing are limited to single-core pre-thread startup.",
            *(["Late drawing briefly borrows the validated bootstrap TTBR0 and restores the incoming TTBR0.",
               "Mutable state/stack use validated writable __DATA padding, not protected executable pages."]
              if post_vm else []),
        ],
    }
