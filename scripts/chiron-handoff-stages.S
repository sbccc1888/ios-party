// SPDX-License-Identifier: BSD-2-Clause-Patent
// Diagnostic replacement for the pinned DarwinEnter leaf function.
// x0..x3 retain the original boot arguments, entry, window base and window size.
.text
.balign 4
.global DarwinEnter
DarwinEnter:
    msr daifset, #15
    mov x12, x4
    mov x13, x5
    mov x16, x30
    mov w14, #0xff00
    movk w14, #0xff00, lsl #16
    mov x15, #0
    bl .Lpaint
    add x5, x2, x3
    mrs x6, ctr_el0
    ubfx x6, x6, #16, #4
    mov x7, #4
    lsl x6, x7, x6
    sub x7, x6, #1
    bic x4, x2, x7
    dsb sy
.global ChironHandoffCacheLoop
ChironHandoffCacheLoop:
.Lclean:
    dc civac, x4
    add x4, x4, x6
    cmp x4, x5
    b.lo .Lclean
    dsb sy
    ic iallu
    dsb sy
    isb
.global ChironHandoffCacheDone
ChironHandoffCacheDone:
    mov w14, #0xff
    movk w14, #0xff00, lsl #16
    mov x15, #1
    bl .Lpaint
    mrs x4, sctlr_el1
    bic x4, x4, #1
    bic x4, x4, #4
    bic x4, x4, #0x1000
    msr sctlr_el1, x4
    isb
    tlbi vmalle1
    dsb sy
    isb
.global ChironHandoffMmuOff
ChironHandoffMmuOff:
    mov w14, #0
    movk w14, #0xffff, lsl #16
    mov x15, #2
    bl .Lpaint
    mov x30, x16
.global ChironHandoffKernelBranch
ChironHandoffKernelBranch:
    br x1

// The three stripes use the supplied physical GOP buffer and its measured size.
.Lpaint:
    cbz x12, .Lpaint_done
    lsr x9, x13, #4
    bic x9, x9, #7
    cbz x9, .Lpaint_done
    madd x10, x15, x9, x12
.Lpixel:
    stp w14, w14, [x10], #8
    subs x9, x9, #8
    b.hi .Lpixel
    dsb sy
.Lpaint_done:
    ret
