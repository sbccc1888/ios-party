// SPDX-License-Identifier: BSD-2-Clause-Patent
// Fixed-kernel, single-CPU diagnostic. Hooks are restricted to pre-thread startup.
.text
.balign 4
.global chiron_kernel_stages
chiron_kernel_stages:
.Ldiagnostic_entry:
    // x1 names this diagnostic entry; restore the ordinary entry contract.
    adr x1, .Lstage_saved
    str x0, [x1]
    stp x2, x3, [x1, #16]
    stp x4, x5, [x1, #32]
    stp x6, x7, [x1, #48]
    stp x8, x9, [x1, #64]
    stp x10, x11, [x1, #80]
    stp x12, x13, [x1, #96]
    stp x14, x15, [x1, #112]
    stp x16, x17, [x1, #128]
    stp x18, x19, [x1, #144]
    stp x20, x21, [x1, #160]
    stp x22, x23, [x1, #176]
    stp x24, x25, [x1, #192]
    stp x26, x27, [x1, #208]
    stp x28, x29, [x1, #224]
    str x30, [x1, #240]
    adr x9, .Ldiagnostic_entry
    ldr x10, .Lentry_delta
    add x9, x9, x10
    str x9, [x1, #8]
    mov x9, sp
    str x9, [x1, #248]
    mrs x9, NZCV
    str x9, [x1, #256]
    mrs x9, DAIF
    str x9, [x1, #264]
    mrs x9, TPIDR_EL0
    adr x10, .Linitial_tpidr
    str x9, [x10]
    msr daifset, #15
    mrs x9, CurrentEL
    cmp x9, #4
    b.ne .Lstage_halt
    mrs x9, SCTLR_EL1
    tbnz x9, #0, .Lstage_halt
    tbnz x9, #2, .Lstage_halt
    // Accept only the measured load/window and GOP layout.
    adr x9, .Ldiagnostic_entry
    ldr x10, =0x40a5a370
    cmp x9, x10
    b.ne .Lstage_halt
    tst x0, #7
    b.ne .Lstage_halt
    ldr x10, =0x40004000
    cmp x0, x10
    b.lo .Lstage_halt
    ldr x10, =0x80003b80
    cmp x0, x10
    b.hi .Lstage_halt
    ldr x9, [x0, #8]
    ldr x10, =0xfffffff007004000
    cmp x9, x10
    b.ne .Lstage_halt
    ldr x9, [x0, #16]
    ldr x10, =0x40004000
    cmp x9, x10
    b.ne .Lstage_halt
    ldr x9, [x0, #24]
    mov x10, #0x40000000
    cmp x9, x10
    b.ne .Lstage_halt
    ldr x9, [x0, #40]
    mov x10, #0x9d400000
    cmp x9, x10
    b.ne .Lstage_halt
    ldr x9, [x0, #56]
    mov x10, #4320
    cmp x9, x10
    b.ne .Lstage_halt
    ldr x9, [x0, #64]
    cmp x9, #1080
    b.ne .Lstage_halt
    ldr x9, [x0, #72]
    cmp x9, #2160
    b.ne .Lstage_halt
    ldr x9, [x0, #80]
    cmp x9, #32
    b.ne .Lstage_halt
    adr x9, .Ldiagnostic_entry
    ldr x10, .Llow_delta
    add x9, x9, x10
    msr VBAR_EL1, x9
    isb
    adr x9, .Lstack_top
    mov sp, x9
    ldr x9, =(0x9d400000 + 540 * 4320)
    ldr x10, =(510 * 4320 / 8)
.Lclear:
    str xzr, [x9], #8
    subs x10, x10, #1
    b.ne .Lclear
    mov x0, #0
    bl .Lmark
    b .Lentry_restore

// TPIDR_EL0 is scratch only inside masked, pre-thread-start hooks.
// Its entry value is restored until start.s clears it, then zero is restored.
.Lsave:
    stp x1, x2, [x0, #8]
    stp x3, x4, [x0, #24]
    stp x5, x6, [x0, #40]
    stp x7, x8, [x0, #56]
    stp x9, x10, [x0, #72]
    stp x11, x12, [x0, #88]
    stp x13, x14, [x0, #104]
    stp x15, x16, [x0, #120]
    stp x17, x18, [x0, #136]
    stp x19, x20, [x0, #152]
    stp x21, x22, [x0, #168]
    stp x23, x24, [x0, #184]
    stp x25, x26, [x0, #200]
    stp x27, x28, [x0, #216]
    str x29, [x0, #232]
    mrs x1, TPIDR_EL0
    str x1, [x0]
    mov x1, sp
    str x1, [x0, #248]
    mrs x1, NZCV
    str x1, [x0, #256]
    mrs x1, DAIF
    str x1, [x0, #264]
    msr daifset, #15
    adr x1, .Lstack_top
    mov sp, x1
    ret

.macro restore_registers
    adr x0, .Lstage_saved
    ldr x9, [x0, #248]
    mov sp, x9
    ldr x9, [x0, #256]
    msr NZCV, x9
    ldr x9, [x0, #264]
    msr DAIF, x9
    ldp x1, x2, [x0, #8]
    ldp x3, x4, [x0, #24]
    ldp x5, x6, [x0, #40]
    ldp x7, x8, [x0, #56]
    ldp x9, x10, [x0, #72]
    ldp x11, x12, [x0, #88]
    ldp x13, x14, [x0, #104]
    ldp x15, x16, [x0, #120]
    ldp x17, x18, [x0, #136]
    ldp x19, x20, [x0, #152]
    ldp x21, x22, [x0, #168]
    ldp x23, x24, [x0, #184]
    ldp x25, x26, [x0, #200]
    ldp x27, x28, [x0, #216]
    ldp x29, x30, [x0, #232]
    ldr x0, [x0]
.endm

.macro stage number
.global ChironStage\number
ChironStage\number:
    msr TPIDR_EL0, x0
    adr x0, .Lstage_saved
    str x30, [x0, #240]
    bl .Lsave
.if \number == 6
    bl .Lmap_video
.endif
.if \number == 9
    adr x0, .Lstage_saved
    ldr x1, [x0]
    adr x0, .Lnative_vbar
    str x1, [x0]
.endif
#ifdef CHIRON_POST_VM
.if \number >= 18
    bl .Lwindow_open
.endif
#endif
    mov x0, #\number
    bl .Lmark
#ifdef CHIRON_POST_VM
.if \number >= 18
    bl .Lwindow_close
.endif
#endif
.if \number == 10
    adr x0, .Lnative_vbar
    ldr x0, [x0]
    msr VBAR_EL1, x0
    isb
.endif
.if \number <= 3
    adr x0, .Linitial_tpidr
    ldr x0, [x0]
.else
    mov x0, #0
.endif
    msr TPIDR_EL0, x0
    restore_registers
.global ChironStage\number\()Original
ChironStage\number\()Original:
    nop
.global ChironStage\number\()Resume
ChironStage\number\()Resume:
    b .
.endm

stage 1
stage 2
stage 3
stage 4
stage 5
stage 6
stage 7
stage 8
stage 9
stage 10
stage 11
stage 12
#ifdef CHIRON_VM_DETAIL
stage 13
stage 14
stage 15
stage 16
stage 17
#endif
#ifdef CHIRON_POST_VM
stage 18
stage 19
stage 20
stage 21
stage 22
stage 23
#endif

.Lentry_restore:
    restore_registers
    br x1

// Use the next unused page inside start.s's 16-page bootstrap reservation.
// Add five device, privileged-RW, execute-never identity blocks for GOP.
.Lmap_video:
    adr x0, .Lstage_saved
    ldr x1, [x0, #200]
    ldr x3, =0x4017a000
    cmp x1, x3
    b.ne .Lmap_fail
    ldr x2, [x0, #16]
    add x2, x2, #4096
    add x3, x1, #0x10, lsl #12
    cmp x2, x3
    b.hs .Lmap_fail
    add x3, x1, #0x4, lsl #12
    cmp x2, x3
    b.lo .Lmap_fail
    tst x2, #4095
    b.ne .Lmap_fail
    ldr x3, [x1, #16]
    cbnz x3, .Lmap_fail
    mov x3, #0
.Lcheck_page:
    ldr x4, [x2, x3]
    cbnz x4, .Lmap_fail
    add x3, x3, #8
    cmp x3, #4096
    b.lo .Lcheck_page
    ldr x3, =0x006000009d40060d
    add x4, x2, #(234 * 8)
    mov x5, #5
.Lvideo_blocks:
    str x3, [x4], #8
    add x3, x3, #0x200, lsl #12
    subs x5, x5, #1
    b.ne .Lvideo_blocks
    orr x2, x2, #3
    str x2, [x1, #16]
#ifdef CHIRON_POST_VM
    adr x3, .Lvideo_descriptor
    str x2, [x3]
#endif
    dsb sy
    ret
.Lmap_fail:
    mov x0, #0xe1
    bl .Lmark
    b .Lstage_halt

// Verify both ends before painting after MMU enable; preserve PAR_EL1.
.Lcan_render:
    mrs x1, SCTLR_EL1
    tbz x1, #0, .Lrender_yes
    mrs x15, PAR_EL1
    mov x2, #0x9d400000
    at s1e1w, x2
    isb
    mrs x3, PAR_EL1
    tbnz x3, #0, .Lrender_no
    and x3, x3, #0xfffffff000
    cmp x3, x2
    b.ne .Lrender_no
    ldr x2, =(0x9d400000 + 1050 * 4320 - 1)
    at s1e1w, x2
    isb
    mrs x3, PAR_EL1
    tbnz x3, #0, .Lrender_no
    and x2, x2, #0xfffffff000
    and x3, x3, #0xfffffff000
    cmp x3, x2
    b.ne .Lrender_no
    msr PAR_EL1, x15
.Lrender_yes:
    mov x0, #1
    ret
.Lrender_no:
    msr PAR_EL1, x15
    mov x0, #0
    ret

.Lmark:
    stp x19, x30, [sp, #-16]!
    mov x19, x0
    adr x1, .Lstage_record
    str x19, [x1]
    bl .Lcan_render
    cbz x0, .Lmark_done
    mov x0, x19
    adr x1, .Lstage_label
    mov w2, #560
    mov w3, #0xffffff
    bl .Lprint_value
    mrs x0, SCTLR_EL1
    adr x1, .Lsctlr_label
    mov w2, #592
    mov w3, #0xffffff
    bl .Lprint_value
    mrs x0, TCR_EL1
    adr x1, .Ltcr_label
    mov w2, #624
    mov w3, #0xffffff
    bl .Lprint_value
.Lmark_done:
    ldp x19, x30, [sp], #16
    ret

#ifdef CHIRON_POST_VM
// These hooks execute in KVA with interrupts masked and no user process active.
// Validate the old root/table before temporarily using it solely for drawing.
.Lwindow_open:
    adr x0, .Lwindow_active
    str xzr, [x0]
    mrs x0, SCTLR_EL1
    tbz x0, #0, .Lwindow_return
    mrs x0, TTBR0_EL1
    adr x1, .Lsaved_ttbr0
    str x0, [x1]
    ldr x1, =0xfffffff00717a000
    ldr x2, [x1, #16]
    adr x3, .Lvideo_descriptor
    ldr x3, [x3]
    cmp x2, x3
    b.ne .Lwindow_fail
    and x2, x2, #0xfffffff000
    ldr x3, =0x4017a000
    sub x2, x2, x3
    cmp x2, #0x4, lsl #12
    b.lo .Lwindow_fail
    cmp x2, #0x10, lsl #12
    b.hs .Lwindow_fail
    add x2, x1, x2
    add x2, x2, #(234 * 8)
    ldr x3, =0x006000009d40060d
    mov x4, #5
.Lvalidate_video_blocks:
    ldr x5, [x2], #8
    cmp x5, x3
    b.ne .Lwindow_fail
    add x3, x3, #0x200, lsl #12
    subs x4, x4, #1
    b.ne .Lvalidate_video_blocks
    ldr x0, =0x4017a000
    dsb ish
.global ChironWindowInstall
ChironWindowInstall:
    msr TTBR0_EL1, x0
    isb
    tlbi vmalle1is
    dsb ish
    isb
    adr x0, .Lwindow_active
    mov x1, #1
    str x1, [x0]
.Lwindow_return:
    ret
.Lwindow_fail:
    mov x0, #0xe2
    bl .Lmark
    b .Lstage_halt

.Lwindow_close:
    adr x0, .Lwindow_active
    ldr x1, [x0]
    cbz x1, .Lwindow_return
    adr x1, .Lsaved_ttbr0
    ldr x1, [x1]
    dsb ish
.global ChironWindowRestore
ChironWindowRestore:
    msr TTBR0_EL1, x1
    isb
    tlbi vmalle1is
    dsb ish
    isb
    str xzr, [x0]
    ret
#endif

.global ChironStageException
ChironStageException:
    msr daifset, #15
    adr x1, .Lstage_record
    mrs x0, ESR_EL1
    str x0, [x1, #8]
    mrs x0, ELR_EL1
    str x0, [x1, #16]
    mrs x0, FAR_EL1
    str x0, [x1, #24]
    mrs x0, SPSR_EL1
    str x0, [x1, #32]
    mov x0, sp
    str x0, [x1, #40]
    mrs x0, SCTLR_EL1
    str x0, [x1, #48]
    adr x0, .Lstack_top
    mov sp, x0
    bl .Lcan_render
    cbz x0, .Lstage_halt
    adr x19, .Lstage_record
    ldr x0, [x19]
    adr x1, .Lexception_label
    mov w2, #700
    ldr w3, =0xff3030
    bl .Lprint_value
    ldr x0, [x19, #8]
    adr x1, .Lesr_label
    mov w2, #732
    mov w3, #0xffffff
    bl .Lprint_value
    ldr x0, [x19, #16]
    adr x1, .Lelr_label
    mov w2, #764
    mov w3, #0xffffff
    bl .Lprint_value
    ldr x0, [x19, #24]
    adr x1, .Lfar_label
    mov w2, #796
    mov w3, #0xffffff
    bl .Lprint_value
    ldr x0, [x19, #32]
    adr x1, .Lspsr_label
    mov w2, #828
    mov w3, #0xffffff
    bl .Lprint_value
    ldr x0, [x19, #40]
    adr x1, .Lsp_label
    mov w2, #860
    mov w3, #0xffffff
    bl .Lprint_value
    ldr x0, [x19, #48]
    adr x1, .Lsctlr_label
    mov w2, #892
    mov w3, #0xffffff
    bl .Lprint_value
.global ChironStageHalt
ChironStageHalt:
.Lstage_halt:
    wfe
    b .Lstage_halt

.Lstage_label: .asciz "XNU STAGE "
.Lsctlr_label: .asciz "SCTLR "
.Ltcr_label: .asciz "TCR "
.Lexception_label: .asciz "EARLY EXCEPTION "
.Lesr_label: .asciz "ESR "
.Lelr_label: .asciz "ELR "
.Lfar_label: .asciz "FAR "
.Lspsr_label: .asciz "SPSR "
.Lsp_label: .asciz "SP "
.balign 8
.Lentry_delta: .quad CHIRON_ENTRY_DELTA
.Llow_delta: .quad CHIRON_LOW_DELTA
#ifdef CHIRON_POST_VM
// Only addresses are assembled here; the selected __DATA padding stays zero.
.set .Lstage_saved, .Ldiagnostic_entry + CHIRON_DATA_DELTA
.set .Lstack_top, .Lstage_saved + 1296
.set .Linitial_tpidr, .Lstage_saved + 1296
.set .Lnative_vbar, .Lstage_saved + 1304
.set .Lstage_record, .Lstage_saved + 1312
.set .Lsaved_ttbr0, .Lstage_saved + 1368
.set .Lwindow_active, .Lstage_saved + 1376
.set .Lvideo_descriptor, .Lstage_saved + 1384
.global ChironStageSaved, ChironStageStackTop, ChironStageInitialTpidr, ChironStageRecord
.set ChironStageSaved, .Lstage_saved
.set ChironStageStackTop, .Lstack_top
.set ChironStageInitialTpidr, .Linitial_tpidr
.set ChironStageRecord, .Lstage_record
.global ChironWindowDescriptor
.set ChironWindowDescriptor, .Lvideo_descriptor
#else
.Lnative_vbar: .quad 0
.global ChironStageInitialTpidr
ChironStageInitialTpidr:
.Linitial_tpidr: .quad 0
.global ChironStageRecord
ChironStageRecord:
.Lstage_record:
    .space 56, 0
.balign 16
.global ChironStageSaved
ChironStageSaved:
.Lstage_saved:
    .space 272, 0
    .space 1024, 0
.global ChironStageStackTop
ChironStageStackTop:
.Lstack_top:
#endif
#define CHIRON_KERNEL_RENDER_ONLY
#include "chiron-entry-probe.S"
