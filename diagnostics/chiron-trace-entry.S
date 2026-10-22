/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#include "chiron-trace.h"
.text
.p2align 11
.globl _chiron_trace_early_vectors
_chiron_trace_early_vectors:
.macro CT_VECTOR number
    mov x0, #\number
    b Lct_early_fault
    .space 120, 0
.endm
CT_VECTOR 0
CT_VECTOR 1
CT_VECTOR 2
CT_VECTOR 3
CT_VECTOR 4
CT_VECTOR 5
CT_VECTOR 6
CT_VECTOR 7
CT_VECTOR 8
CT_VECTOR 9
CT_VECTOR 10
CT_VECTOR 11
CT_VECTOR 12
CT_VECTOR 13
CT_VECTOR 14
CT_VECTOR 15

Lct_early_fault:
    msr DAIFSet, #15
    adrp x9, _chiron_trace_log@PAGE
    add x9, x9, _chiron_trace_log@PAGEOFF
    add x10, x9, #14, lsl #12
    ldr w11, [x10, #400]
    add w11, w11, #1
    str w11, [x10, #400]
    cmp w11, #1
    b.hi Lct_nested
    str w0, [x10, #404]
    mov w10, #3
    str w10, [x9, #CT_FAULT_VALID]
    mrs x10, ELR_EL1
    str x10, [x9, #CT_PC]
    mrs x10, ESR_EL1
    str x10, [x9, #CT_ESR]
    mrs x10, FAR_EL1
    str x10, [x9, #CT_FAR]
    mrs x10, SPSR_EL1
    str x10, [x9, #CT_SPSR]
    and x11, x10, #15
    cmp x11, #4
    b.ne Lct_sp1
    mrs x10, SP_EL0
    b Lct_save_sp
Lct_sp1:
    mov x10, sp
Lct_save_sp:
    str x10, [x9, #CT_SP]
    mrs x10, SCTLR_EL1
    str x10, [x9, #CT_SCTLR]
    mrs x10, TCR_EL1
    str x10, [x9, #CT_TCR]
    adrp x10, _chiron_trace_early_stack@PAGE
    add x10, x10, _chiron_trace_early_stack@PAGEOFF
    add x10, x10, #4096
    mov sp, x10
    bl _chiron_trace_early_panic
    b Lct_halt
Lct_nested:
    add x10, x9, #14, lsl #12
    mrs x11, ELR_EL1
    str x11, [x10, #408]
    mrs x11, ESR_EL1
    str x11, [x10, #416]
    mrs x11, FAR_EL1
    str x11, [x10, #424]
    dsb sy
Lct_halt:
    wfe
    b Lct_halt

.p2align 2
.globl _chiron_trace_window_open
_chiron_trace_window_open:
    mrs x1, TTBR0_EL1
    dsb ish
    msr TTBR0_EL1, x0
    isb
    tlbi vmalle1is
    dsb ish
    isb
    mov x0, x1
    ret

.globl _chiron_trace_window_close
_chiron_trace_window_close:
    dsb ish
    msr TTBR0_EL1, x0
    isb
    tlbi vmalle1is
    dsb ish
    isb
    ret
