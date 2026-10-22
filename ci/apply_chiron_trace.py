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
        main = replace_once(main, '\tboot_progress_stage("PE_create_console() returned");',
                            '\tboot_progress_stage("PE_create_console() returned");\n'
                            '\tchiron_trace_console_checkpoint();', path)
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
        text = '// CHIRON_SOURCE_TRACE\n#include "../kern/chiron_trace.h"\n#include <sys/proc.h>\n' + text
        for signature in (
            "sleh_synchronous_sp1(arm_context_t *context, uint32_t esr, vm_offset_t far __unused)\n{",
            "sleh_synchronous(arm_context_t *context, uint32_t esr, vm_offset_t far, __unused bool did_initiate_panic_lockdown)\n{",
        ):
            text = replace_once(text, signature, signature +
                                "\n\tif (ESR_EC(esr) != ESR_EC_SVC_64 && ESR_EC(esr) != ESR_EC_SVC_32) {\n"
                                "\t\tchiron_trace_exception(get_saved_state_pc(&context->ss), esr, far,\n"
                                "\t\t    get_saved_state_sp(&context->ss), get_saved_state_cpsr(&context->ss));\n\t}", path)
        for code, address in (("EXC_ARM_SP_ALIGN", "sp"), ("EXC_ARM_DA_ALIGN", "pc")):
            anchor = f"\tcodes[0] = {code};\n\tcodes[1] = get_saved_state_{address}(ss);"
            text = replace_once(text, anchor, anchor +
                                '\n\tchiron_trace_user_abort((unsigned)proc_selfpid(), get_saved_state_pc(ss),\n'
                                '\t    get_saved_state_esr(ss), get_saved_state_far(ss),\n'
                                f'\t    get_saved_state_sp(ss), get_saved_state_cpsr(ss), {code});', path)
        anchor = "\tboot_progress_user_abort(state, esr, fault_addr);"
        return replace_once(text, anchor,
                            '\tchiron_trace_user_abort((unsigned)proc_selfpid(), get_saved_state_pc(state),\n'
                            '\t    esr, fault_addr, get_saved_state_sp(state), get_saved_state_cpsr(state), codes[0]);\n'
                            + anchor, path)

    edit("osfmk/arm64/sleh.c", sleh)

    def user_signal(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../../osfmk/kern/chiron_trace.h"\n' + text
        anchor = "\t\tthreadsignal(thread, ux_signal, code, TRUE);"
        return replace_once(text, anchor,
                            '\t\tchiron_trace_signal((unsigned)proc_pid(p), (unsigned)ux_signal,\n'
                            '\t\t    (unsigned)exception, code, subcode);\n' + anchor, path)

    edit("bsd/uxkern/ux_exception.c", user_signal)

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

    def console(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../kern/chiron_trace.h"\n' + text
        text = replace_once(text, "console_write(char *str, int size)\n{",
                            "console_write(char *str, int size)\n{\n"
                            "\tif (boot_progress_active() && str != NULL && size > 0) {\n"
                            "\t\tchiron_trace_console(str, (unsigned)size);\n\t\treturn;\n\t}", path)
        return replace_once(text, "console_write_unbuffered(char c)\n{",
                            "console_write_unbuffered(char c)\n{\n"
                            "\tif (boot_progress_active()) { boot_progress_putc(c); return; }", path)

    edit("osfmk/console/serial_console.c", console)

    def platform_console(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../../osfmk/kern/chiron_trace.h"\n' + text
        text = replace_once(text, "check_for_panic_log(void)\n{",
                            "check_for_panic_log(void)\n{\n"
                            '\tboot_progress_stage("panic region check enter");', path)
        anchor = "\tgPanicBase = ml_io_map_wcomb(PLATFORM_PANIC_LOG_PADDR, PLATFORM_PANIC_LOG_SIZE);"
        text = replace_once(text, anchor,
                            '\tchiron_trace_mapping("panic fixed board region", PLATFORM_PANIC_LOG_PADDR,\n'
                            '\t    0, PLATFORM_PANIC_LOG_SIZE, 0);\n' + anchor, path)
        anchor = '\tif (kSuccess != SecureDTLookupEntry(0, "pram", &entry)) {\n\t\treturn;\n\t}'
        text = replace_once(text, anchor,
                            '\tboot_progress_stage("panic region uses device tree");\n'
                            '\tif (kSuccess != SecureDTLookupEntry(0, "pram", &entry)) {\n'
                            '\t\tboot_progress_stage("panic DT region absent; no memory accessed");\n'
                            '\t\treturn;\n\t}', path)
        for expression, message in (
            ("reg_prop[0] > UINTPTR_MAX - reg_prop[1]", "panic DT reg invalid"),
            ('kSuccess != SecureDTLookupEntry(0, "/chosen", &chosen)', "panic DT chosen absent"),
            ("panic_region_length[0] > reg_prop[1]", "panic DT length invalid"),
        ):
            anchor = expression + ") {\n\t\treturn;"
            text = replace_once(text, anchor, expression + ") {\n"
                                f'\t\tboot_progress_stage("{message}");\n\t\treturn;', path)
        anchor = "\tgPanicBase = ml_io_map_wcomb(reg_prop[0], panic_region_length[0]);"
        text = replace_once(text, anchor,
                            '\tchiron_trace_mapping("panic DT map enter", reg_prop[0], 0,\n'
                            '\t    panic_region_length[0], 0);\n' + anchor + '\n'
                            '\tchiron_trace_mapping("panic DT map returned", reg_prop[0], gPanicBase,\n'
                            '\t    panic_region_length[0], 0);', path)
        anchor = "\tpanic_info = (struct embedded_panic_header *)gPanicBase;"
        text = replace_once(text, anchor,
                            '\tchiron_trace_mapping("panic header read enter", 0, gPanicBase, gPanicSize, 0);\n'
                            + anchor, path)
        anchor = "\tbzero((void *)gPanicBase, gPanicSize);"
        text = replace_once(text, anchor,
                            '\tboot_progress_stage("panic region clear enter");\n' + anchor + '\n'
                            '\tboot_progress_stage("panic region clear returned");', path)
        text = replace_once(text, "\tcheck_for_panic_log();",
                            '\tboot_progress_stage("check_for_panic_log enter");\n'
                            '\tcheck_for_panic_log();\n'
                            '\tboot_progress_stage("check_for_panic_log returned");\n'
                            '\tchiron_trace_video("PE boot video", PE_state.video.v_baseAddr,\n'
                            '\t    PE_state.video.v_width, PE_state.video.v_height,\n'
                            '\t    PE_state.video.v_rowBytes, PE_state.video.v_depth);', path)
        for mode in ("Graphics", "Text"):
            anchor = f"\t\tPE_initialize_console(&PE_state.video, kPE{mode}Mode);"
            text = replace_once(text, anchor,
                                f'\t\tboot_progress_stage("PE {mode.lower()} mode enter");\n' + anchor +
                                f'\n\t\tboot_progress_stage("PE {mode.lower()} mode returned");', path)
        text = replace_once(text, "PE_initialize_console(PE_Video * info, int op)\n{",
                            "PE_initialize_console(PE_Video * info, int op)\n{\n"
                            '\tboot_progress_stage("PE_initialize_console enter");', path)
        # These are the four branches of PE_initialize_console's operation switch.
        anchor = "\t\tinitialize_screen(info, op);"
        if text.count(anchor) != 4:
            raise ValueError(f"console dispatch context changed: {path}")
        text = text.replace(anchor, '\t\tboot_progress_stage("initialize_screen dispatch enter");\n' +
                            anchor + '\n\t\tboot_progress_stage("initialize_screen dispatch returned");')
        return text

    edit("pexpert/arm/pe_init.c", platform_console)

    def video_console(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../kern/chiron_trace.h"\n' + text
        anchor = "initialize_screen(PE_Video * boot_vinfo, unsigned int op)\n{"
        text = replace_once(text, anchor, anchor +
                            '\n\tboot_progress_stage("screen init enter");\n'
                            '\tif (boot_vinfo) {\n'
                            '\t\tchiron_trace_video("screen input", boot_vinfo->v_baseAddr,\n'
                            '\t\t    boot_vinfo->v_width, boot_vinfo->v_height,\n'
                            '\t\t    boot_vinfo->v_rowBytes, boot_vinfo->v_depth);\n'
                            '\t\tchiron_trace_mapping("screen length/offset/op", 0, boot_vinfo->v_offset,\n'
                            '\t\t    boot_vinfo->v_length, op);\n\t}', path)
        anchor = "\t\tmakeMapping = (kernel_map == VM_MAP_NULL) || (0 != (1 & newVideoVirt));"
        text = replace_once(text, anchor, anchor +
                            '\n\t\tchiron_trace_mapping("screen mapping decision", 0,\n'
                            '\t\t    (uintptr_t)kernel_map, 0, makeMapping);', path)
        anchor = "\t\t\t\tnewVideoVirt = ml_io_map_unmappable((vm_map_offset_t)new_vinfo.v_physaddr, newMapSize, flags);   /* Allocate address space for framebuffer */"
        text = replace_once(text, anchor,
                            '\t\t\t\tchiron_trace_mapping("screen map enter", new_vinfo.v_physaddr,\n'
                            '\t\t\t\t    0, newMapSize, flags);\n' + anchor +
                            '\n\t\t\t\tchiron_trace_mapping("screen map returned", new_vinfo.v_physaddr,\n'
                            '\t\t\t\t    newVideoVirt, newMapSize, flags);', path)
        anchor = "\t\t/* Update the vinfo structure atomically with respect to the vc_progress task if running */"
        text = replace_once(text, anchor,
                            '\t\tboot_progress_stage("screen vinfo update enter");\n' + anchor, path)
        anchor = "\t\t// If we changed the virtual address, remove the old mapping"
        text = replace_once(text, anchor,
                            '\t\tboot_progress_stage("screen old mapping retire enter");\n' + anchor, path)
        anchor = "\t\tif (kPEBaseAddressChange != op) {\n\t\t\t// Graphics mode setup by the booter."
        text = replace_once(text, anchor,
                            '\t\tboot_progress_stage("screen old mapping retire returned");\n' + anchor, path)
        anchor = "\t\t\tgc_initialize(&vinfo);"
        text = replace_once(text, anchor,
                            '\t\t\tboot_progress_stage("gc_initialize enter");\n' + anchor +
                            '\n\t\t\tboot_progress_stage("gc_initialize returned");', path)
        anchor = "\tgraphics_now = gc_graphics_boot && !gc_desire_text;"
        text = replace_once(text, anchor,
                            '\tboot_progress_stage("screen operation dispatch enter");\n' + anchor, path)
        anchor = "\t/* Console initialization/acquisition may clear the existing mapping. */"
        text = replace_once(text, anchor,
                            '\tboot_progress_stage("screen operation dispatch returned");\n' + anchor, path)
        anchor = "\t    vinfo.v_rowbytes, vinfo.v_depth);\n}\n\nvoid vcattach(void);"
        text = replace_once(text, anchor,
                            '\t    vinfo.v_rowbytes, vinfo.v_depth);\n'
                            '\tboot_progress_stage("screen init returned");\n}\n\nvoid vcattach(void);', path)
        begin = text.index("gc_initialize(struct vc_info * info)\n{")
        end = text.index("\n}\n", begin) + 3
        initialize = text[begin:end]
        for anchor, label in (
            ("\t\tVCPUTC_LOCK_INIT();", "vc lock init"),
            ("\tgc_ops.initialize(info);", "vc geometry init"),
            ("\tgc_reset_vt100();", "vc reset vt100"),
        ):
            initialize = replace_once(initialize, anchor,
                                      f'\tboot_progress_stage("{label} enter");\n' + anchor +
                                      f'\n\tboot_progress_stage("{label} returned");', path)
        text = text[:begin] + initialize + text[end:]
        for anchor, label in (
            ("\t\tgc_ops.clear_screen(gc_x, gc_y, 0, vinfo.v_rows, 2);", "vc clear screen"),
            ("\t\tgc_ops.show_cursor(gc_x, gc_y);", "vc show cursor"),
        ):
            text = replace_once(text, anchor, f'\tboot_progress_stage("{label} enter");\n' + anchor +
                                f'\n\tboot_progress_stage("{label} returned");', path)
        return text

    edit("osfmk/console/video_console.c", video_console)

    def io_mapping(text, path):
        text = '// CHIRON_SOURCE_TRACE\n#include "../kern/chiron_trace.h"\n' + text
        anchor = "\tif (startup_phase < STARTUP_SUB_KMEM) {"
        text = replace_once(text, anchor, anchor +
                            '\n\t\tchiron_trace_mapping("early io_map enter", phys_addr,\n'
                            '\t\t    virtual_space_start, alloc_size, flags);', path)
        anchor = "\t\tassert(flags == VM_WIMG_WCOMB || flags == VM_WIMG_IO);"
        text = replace_once(text, anchor, anchor +
                            '\n\t\tboot_progress_stage("early pmap_map_bd enter");', path)
        anchor = "#if KASAN\n\t\tkasan_notify_address(start + start_offset, size);"
        text = replace_once(text, anchor,
                            '\t\tchiron_trace_mapping("early pmap_map_bd returned", phys_addr,\n'
                            '\t\t    start, alloc_size, flags);\n' + anchor, path)
        return text

    edit("osfmk/arm/io_map.c", io_mapping)
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
