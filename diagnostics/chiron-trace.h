/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef CHIRON_TRACE_H
#define CHIRON_TRACE_H

/* Shared wire layout. C asserts below also qualify the assembly offsets. */
#define CT_MAGIC 0x434849524f4e5431
#define CT_VERSION 1
#define CT_BYTES 65536
#define CT_TEXT_BYTES 32768
#define CT_EVENT_COUNT 256
#define CT_TEST_ADDRESS 0x1000
#define CT_EARLY_STAGE 24
#define CT_BOOT_ARGS 32
#define CT_BOOT_ROOT 40
#define CT_VIRT_BASE 48
#define CT_PHYS_BASE 56
#define CT_VIDEO 64
#define CT_FAULT_VALID 96
#define CT_PC 104
#define CT_ESR 112
#define CT_FAR 120
#define CT_SP 128
#define CT_SPSR 136
#define CT_SCTLR 144
#define CT_TCR 152
#define CT_BUILD_ID 184
#define CT_BA_VIRT 8
#define CT_BA_PHYS 16
#define CT_BA_MEMORY 24
#define CT_BA_VIDEO 40
#define CT_BA_ROW 56
#define CT_BA_WIDTH 64
#define CT_BA_HEIGHT 72
#define CT_BA_DEPTH 80
#define CT_NATIVE_VECTORS 57736
#define CT_EARLY_DEPTH 57744

#ifndef __ASSEMBLER__
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>

struct chiron_trace_event {
    uint64_t sequence;
    char text[88];
};

struct chiron_trace_storage {
    uint64_t magic;
    uint32_t version, bytes;
    uint64_t sequence, early_stage, boot_args, boot_root, virt_base, phys_base;
    uint64_t video_phys, video_bytes;
    uint32_t width, height, stride, depth;
    uint32_t fault_valid, reentries;
    uint64_t pc, esr, far, sp, spsr, sctlr, tcr, render_base;
    uint32_t mapping_mode, text_dropped, text_write, text_used;
    char build_id[96];
    char text[CT_TEXT_BYTES];
    struct chiron_trace_event events[CT_EVENT_COUNT];
    uint32_t event_write, event_dropped;
    uint64_t early_pc[8];
    uint64_t panic_caller;
    uint32_t exception_count, panic_count;
    uint64_t paints, native_paints, render_refused;
    uint64_t native_vectors;
    uint32_t early_fault_depth, early_fault_slot;
    uint64_t nested_pc, nested_esr, nested_far;
    unsigned char reserved[7760];
};

_Static_assert(sizeof(struct chiron_trace_storage) == CT_BYTES, "trace wire size");
_Static_assert(offsetof(struct chiron_trace_storage, early_stage) == CT_EARLY_STAGE, "stage offset");
_Static_assert(offsetof(struct chiron_trace_storage, boot_root) == CT_BOOT_ROOT, "root offset");
_Static_assert(offsetof(struct chiron_trace_storage, video_phys) == CT_VIDEO, "video offset");
_Static_assert(offsetof(struct chiron_trace_storage, fault_valid) == CT_FAULT_VALID, "fault offset");
_Static_assert(offsetof(struct chiron_trace_storage, pc) == CT_PC, "PC offset");
_Static_assert(offsetof(struct chiron_trace_storage, esr) == CT_ESR, "ESR offset");
_Static_assert(offsetof(struct chiron_trace_storage, far) == CT_FAR, "FAR offset");
_Static_assert(offsetof(struct chiron_trace_storage, sp) == CT_SP, "SP offset");
_Static_assert(offsetof(struct chiron_trace_storage, spsr) == CT_SPSR, "SPSR offset");
_Static_assert(offsetof(struct chiron_trace_storage, sctlr) == CT_SCTLR, "SCTLR offset");
_Static_assert(offsetof(struct chiron_trace_storage, tcr) == CT_TCR, "TCR offset");
_Static_assert(offsetof(struct chiron_trace_storage, build_id) == CT_BUILD_ID, "build ID offset");
_Static_assert(offsetof(struct chiron_trace_storage, early_pc) == 57632, "early PC offsets");
_Static_assert(offsetof(struct chiron_trace_storage, native_vectors) == CT_NATIVE_VECTORS, "native vector offset");
_Static_assert(offsetof(struct chiron_trace_storage, early_fault_depth) == CT_EARLY_DEPTH, "early depth offset");

extern struct chiron_trace_storage chiron_trace_log;
extern const char chiron_trace_build_id[96];
void chiron_trace_init(uint64_t, unsigned, unsigned, unsigned, unsigned, uint64_t, uint64_t);
void chiron_trace_native_map(void);
void chiron_trace_use_native_vectors(void);
void chiron_trace_console(const char *, unsigned);
void chiron_trace_initializer(unsigned, unsigned, unsigned, uintptr_t, uintptr_t);
void chiron_trace_exception(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
void chiron_trace_panic(const char *, va_list *, unsigned long);
void chiron_trace_fault_checkpoint(void);
void chiron_trace_test(void);
void chiron_trace_early_panic(void) __attribute__((noreturn));
void boot_progress_init(void);
void boot_progress_start(void);
int boot_progress_active(void);
void boot_progress_bind(uintptr_t, unsigned, unsigned, unsigned, unsigned);
void boot_progress_putc(char);
void boot_progress_stage(const char *);
void boot_progress_irq(uint64_t, uint64_t, uint64_t);
void boot_progress_poll(void);
void boot_progress_user_breakpoint(void *, unsigned);
void boot_progress_user_abort(void *, unsigned, uint64_t);
void boot_progress_critical_exit(int, char *, unsigned long);
void boot_progress_process_stage(int, const char *, const char *);
void boot_progress_exit(int, const char *, int, unsigned, uint64_t);
#endif
#endif
