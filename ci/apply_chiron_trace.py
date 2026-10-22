#!/usr/bin/env python3
"""Apply one source-only observer overlay after the pinned upstream patch series."""

import argparse
import hashlib
import json
from pathlib import Path


def replace_once(text, old, new, path):
    if text.count(old) != 1:
        raise ValueError(f"source context changed: {path}: {old[:100]}")
    return text.replace(old, new, 1)


def apply(source, diagnostics, output):
    source, diagnostics = Path(source), Path(diagnostics)
    edits = {}
    pending = {}
    expected = json.loads((diagnostics / "source-contexts.json").read_text())["after_pinned_upstream_patches"]

    def edit(relative, transform):
        path = source / relative
        before = path.read_text()
        if hashlib.sha256(before.encode()).hexdigest() != expected.get(relative):
            raise ValueError(f"fixed source baseline changed: {relative}")
        if "CHIRON_SOURCE_TRACE" in before:
            raise ValueError(f"observer already present: {relative}")
        after = transform(before, relative)
        pending[path] = after
        edits[relative] = {
            "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
            "after_sha256": hashlib.sha256(after.encode()).hexdigest(),
        }

    header = diagnostics / "chiron-trace.h"
    asm = (diagnostics / "chiron-trace-asm.inc").read_text().replace(
        '#include "chiron-trace.h"', '#include "../kern/chiron_trace.h"'
    )

    def start(text, path):
        text = replace_once(text, '#include "assym.s"',
                            '#include "assym.s"\n// CHIRON_SOURCE_TRACE\n'
                            '#ifndef XNU_CHIRON_TRACE\n#define XNU_CHIRON_TRACE 1\n#endif\n'
                            '#if XNU_CHIRON_TRACE\n#include "chiron_trace_asm.inc"\n#endif', path)
        text = replace_once(text, "LEXT(start_first_cpu)\n",
                            "LEXT(start_first_cpu)\n#if XNU_CHIRON_TRACE\n\tCT_TRACE_ENTRY\n#endif\n", path)
        low = text.index("EXT(LowExceptionVectorBase)", text.index("LEXT(start_first_cpu)"))
        write = text.index("\tMSR_VBAR_EL1_X0", low)
        if write - low > 512:
            raise ValueError("low VBAR source sequence changed")
        text = text[:write] + text[write:].replace(
            "\tMSR_VBAR_EL1_X0",
            "#if XNU_CHIRON_TRACE\n\tCT_TRACE_VECTORS\n#else\n\tMSR_VBAR_EL1_X0\n#endif", 1)
        text = replace_once(text, "\t/* Ensure TTEs are visible */",
                            "#if XNU_CHIRON_TRACE\n\tCT_TRACE_MAP_VIDEO\n\tCT_TRACE_STAGE 2\n#endif\n"
                            "\t/* Ensure TTEs are visible */", path)
        anchor = "\t// Enable caches, MMU, ROP and JOP\n"
        text = replace_once(text, anchor,
                            "#if XNU_CHIRON_TRACE\n\tCT_TRACE_STAGE 3\n#endif\n" + anchor, path)
        # Keep a usable low vector until the MMU transition actually completes.
        anchor = "1:\n\tMSR_VBAR_EL1_X0\n\n#if HAS_APPLE_PAC"
        text = replace_once(text, anchor,
                            "1:\n#if XNU_CHIRON_TRACE\n\tmov x28, x0\n#else\n"
                            "\tMSR_VBAR_EL1_X0\n#endif\n\n#if HAS_APPLE_PAC", path)
        anchor = "\tMSR_SCTLR_EL1_X0\n\tisb\t\tsy\n"
        text = replace_once(text, anchor, anchor +
                            "#if XNU_CHIRON_TRACE\n\tCT_TRACE_STAGE 4\n"
                            "\tCT_TRACE_HIGH_VECTORS\n#endif\n", path)
        return text

    edit("osfmk/arm64/start.s", start)

    def arm_init(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../kern/chiron_trace.h"\n' + text
        anchor = "arm_init(\n\tboot_args       *args)\n{"
        insert = """
\t_Static_assert(offsetof(boot_args, virtBase) == CT_BA_VIRT, "boot virt ABI");
\t_Static_assert(offsetof(boot_args, physBase) == CT_BA_PHYS, "boot phys ABI");
\t_Static_assert(offsetof(boot_args, Video.v_baseAddr) == CT_BA_VIDEO, "boot video ABI");
\t_Static_assert(offsetof(boot_args, Video.v_rowBytes) == CT_BA_ROW, "boot stride ABI");
\t_Static_assert(offsetof(boot_args, Video.v_width) == CT_BA_WIDTH, "boot width ABI");
\t_Static_assert(offsetof(boot_args, Video.v_height) == CT_BA_HEIGHT, "boot height ABI");
\t_Static_assert(offsetof(boot_args, Video.v_depth) == CT_BA_DEPTH, "boot depth ABI");
\tchiron_trace_init(args->Video.v_baseAddr, (unsigned)args->Video.v_width,
\t    (unsigned)args->Video.v_height, (unsigned)args->Video.v_rowBytes,
\t    (unsigned)args->Video.v_depth, args->virtBase, args->physBase);
"""
        text = replace_once(text, anchor, anchor + insert, path)
        text = replace_once(text, '\tboot_progress_stage("arm_vm_init returned");',
                            '\tboot_progress_stage("arm_vm_init returned");\n'
                            '\tchiron_trace_native_map();\n\tchiron_trace_test();', path)
        boundary = text.index("\narm_init_cpu(")
        main, secondary = text[:boundary], text[boundary:]
        main = replace_once(main, "\tcpu_bootstrap();",
                            "\tcpu_bootstrap();\n\tchiron_trace_use_native_vectors();", path)
        for call in ("serial_init()", "PE_create_console()", "PE_init_printf(FALSE)",
                     "PE_init_platform(TRUE, &BootCpuData)", "cpu_timebase_init(TRUE)",
                     "machine_startup(args)"):
            # The source's exact call spelling is checked; assignments are preserved.
            rows = [line for line in main.splitlines() if call in line
                    and not line.lstrip().startswith(("//", "/*", "*"))]
            if len(rows) != 1:
                raise ValueError(f"source call context changed: {path}: {call}")
            row = rows[0]
            main = replace_once(main, row,
                                f'\tboot_progress_stage("{call} enter");\n{row}\n'
                                f'\tboot_progress_stage("{call} returned");', path)
        return main + secondary

    edit("osfmk/arm/arm_init.c", arm_init)

    def vm_init(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../kern/chiron_trace.h"\n' + text
        text = replace_once(text, "\tarm_vm_prot_init(args);",
                            '\tboot_progress_stage("arm_vm_prot_init enter");\n'
                            '\tarm_vm_prot_init(args);\n'
                            '\tboot_progress_stage("arm_vm_prot_init returned");', path)
        text = replace_once(text, "\tset_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);",
                            '\tboot_progress_stage("kernel TTBR1 switch enter");\n'
                            '\tset_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);\n'
                            '\tboot_progress_stage("kernel TTBR1 switch returned");', path)
        text = replace_once(text, "\tset_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);",
                            '\tboot_progress_stage("TTBR0 invalidation enter");\n'
                            '\tset_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);\n'
                            '\tboot_progress_stage("TTBR0 invalidation returned");', path)
        text = replace_once(text, "\tpmap_bootstrap(dynamic_memory_begin);",
                            '\tboot_progress_stage("pmap_bootstrap enter");\n'
                            '\tpmap_bootstrap(dynamic_memory_begin);\n'
                            '\tboot_progress_stage("pmap_bootstrap returned");', path)
        return text

    edit("osfmk/arm64/arm_vm_init.c", vm_init)

    def sleh(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../kern/chiron_trace.h"\n' + text
        for signature in (
            "sleh_synchronous_sp1(arm_context_t *context, uint32_t esr, vm_offset_t far __unused)\n{",
            "sleh_synchronous(arm_context_t *context, uint32_t esr, vm_offset_t far, __unused bool did_initiate_panic_lockdown)\n{",
        ):
            text = replace_once(text, signature, signature +
                                "\n\tif (ESR_EC(esr) != ESR_EC_SVC_64 && ESR_EC(esr) != ESR_EC_SVC_32) {\n"
                                "\t\tchiron_trace_exception(get_saved_state_pc(&context->ss), esr, far,\n"
                                "\t\t    get_saved_state_sp(&context->ss), get_saved_state_cpsr(&context->ss));\n\t}", path)
        return text

    edit("osfmk/arm64/sleh.c", sleh)

    def panic(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "chiron_trace.h"\n' + text
        anchor = 'panic_trap_to_debugger(const char *panic_format_str, va_list *panic_args, unsigned int reason, void *ctx,\n    uint64_t panic_options_mask, void *panic_data_ptr, unsigned long panic_caller)\n{'
        return replace_once(text, anchor, anchor +
                            "\n\tchiron_trace_panic(panic_format_str, panic_args, panic_caller);", path)

    edit("osfmk/kern/debug.c", panic)

    def startup(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "chiron_trace.h"\n' + text
        return replace_once(text, "\t\tcur->func(cur->arg);",
                            "\t\tchiron_trace_initializer(0, cur->subsystem, cur->rank,\n"
                            "\t\t    (uintptr_t)cur->func, (uintptr_t)cur->arg);\n"
                            "\t\tcur->func(cur->arg);\n"
                            "\t\tchiron_trace_initializer(1, cur->subsystem, cur->rank,\n"
                            "\t\t    (uintptr_t)cur->func, (uintptr_t)cur->arg);", path)

    edit("osfmk/kern/startup.c", startup)
    for path, text in pending.items():
        path.write_text(text)
    (source / "osfmk/kern/chiron_trace.h").write_bytes(header.read_bytes())
    (source / "osfmk/arm64/chiron_trace_asm.inc").write_text(asm)
    result = {"schema": 1, "binary_kernel_patching": False, "edits": edits,
              "observer_sources": {
                  path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in diagnostics.iterdir() if path.is_file()
              }}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    apply(args.source, root / "diagnostics", root / "build/chiron-source-overlay.json")


if __name__ == "__main__":
    main()
