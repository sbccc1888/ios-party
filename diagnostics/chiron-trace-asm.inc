/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Cold-start hooks only. x9..x15 are scratch at the selected source locations. */
#include "chiron-trace.h"

.macro CT_TRACE_STAGE number
    adrp x9, _chiron_trace_log@PAGE
    add x9, x9, _chiron_trace_log@PAGEOFF
    mov x10, #\number
    str x10, [x9, #CT_EARLY_STAGE]
    adr x11, .
    add x10, x9, #14, lsl #12
    str x11, [x10, #(288 + (\number - 1) * 8)]
    mrs x10, SCTLR_EL1
    str x10, [x9, #CT_SCTLR]
    mrs x10, TCR_EL1
    str x10, [x9, #CT_TCR]
.endm

.macro CT_TRACE_VECTORS
    adrp x9, _chiron_trace_early_vectors@PAGE
    add x9, x9, _chiron_trace_early_vectors@PAGEOFF
    msr VBAR_EL1, x9
    isb
.endm

.macro CT_TRACE_HIGH_VECTORS
    adrp x9, _chiron_trace_log@PAGE
    add x9, x9, _chiron_trace_log@PAGEOFF
    add x10, x9, #14, lsl #12
    str x28, [x10, #392]
    adrp x9, _chiron_trace_early_vectors@PAGE
    add x9, x9, _chiron_trace_early_vectors@PAGEOFF
    add x9, x9, x22
    sub x9, x9, x23
    msr VBAR_EL1, x9
    isb
.endm

.macro CT_TRACE_ENTRY
    adrp x9, _chiron_trace_log@PAGE
    add x9, x9, _chiron_trace_log@PAGEOFF
    ldr x10, =CT_MAGIC
    str x10, [x9]
    mov w10, #CT_VERSION
    str w10, [x9, #8]
    mov w10, #CT_BYTES
    str w10, [x9, #12]
    str x0, [x9, #CT_BOOT_ARGS]
    ldr x10, [x0, #CT_BA_VIRT]
    str x10, [x9, #CT_VIRT_BASE]
    ldr x10, [x0, #CT_BA_PHYS]
    str x10, [x9, #CT_PHYS_BASE]
    ldr x10, [x0, #CT_BA_VIDEO]
    str x10, [x9, #CT_VIDEO]
    ldr x10, [x0, #CT_BA_WIDTH]
    str w10, [x9, #80]
    ldr x10, [x0, #CT_BA_HEIGHT]
    str w10, [x9, #84]
    ldr x10, [x0, #CT_BA_ROW]
    str w10, [x9, #88]
    ldr x10, [x0, #CT_BA_DEPTH]
    str w10, [x9, #92]
    adrp x12, _chiron_trace_build_id@PAGE
    add x12, x12, _chiron_trace_build_id@PAGEOFF
    add x13, x9, #CT_BUILD_ID
    mov x14, #96
Lct_build_copy:
    ldrb w10, [x12], #1
    strb w10, [x13], #1
    subs x14, x14, #1
    b.ne Lct_build_copy
    CT_TRACE_VECTORS
    CT_TRACE_STAGE 1
.endm

/* The original source allocator has finished; x2 names its last allocated page.
 * No calls or stack accesses are made while the original SP is still a KVA.
 */
.macro CT_TRACE_MAP_VIDEO
    adrp x9, _chiron_trace_log@PAGE
    add x9, x9, _chiron_trace_log@PAGEOFF
    str x25, [x9, #CT_BOOT_ROOT]
    ldr x10, [x20, #CT_BA_VIDEO]
    ldr x11, [x20, #CT_BA_ROW]
    ldr x12, [x20, #CT_BA_HEIGHT]
    mul x11, x11, x12
    add x12, x10, x11
    cmp x10, x23
    b.lo Lct_outside
    add x13, x23, x24
    cmp x12, x13
    b.ls Lct_map_done
Lct_outside:
    /* Only the measured chiron GOP may lie outside the owned RAM window. */
    mov x13, #0x9d400000
    cmp x10, x13
    b.ne Lct_map_done
    ldr x13, [x20, #CT_BA_ROW]
    mov x14, #4320
    cmp x13, x14
    b.ne Lct_map_done
    ldr x13, [x20, #CT_BA_HEIGHT]
    cmp x13, #2160
    b.ne Lct_map_done
    ldr x13, [x20, #CT_BA_DEPTH]
    cmp x13, #32
    b.ne Lct_map_done
    lsr x13, x10, #30
    and x13, x13, #255
    add x9, x25, x13, lsl #3
    ldr x13, [x9]
    cbnz x13, Lct_map_done
    add x13, x2, #4096
    add x14, x25, #16, lsl #12
    cmp x13, x14
    b.hs Lct_map_done
    add x14, x25, #4, lsl #12
    cmp x13, x14
    b.lo Lct_map_done
    lsr x14, x10, #21
    and x14, x14, #511
    add x14, x13, x14, lsl #3
    ldr x15, =0x006000000000060d
    and x10, x10, #0xffffe00000
Lct_map_block:
    orr x11, x10, x15
    str x11, [x14], #8
    add x10, x10, #0x200, lsl #12
    cmp x10, x12
    b.lo Lct_map_block
    orr x13, x13, #3
    str x13, [x9]
    dsb sy
Lct_map_done:
.endm
