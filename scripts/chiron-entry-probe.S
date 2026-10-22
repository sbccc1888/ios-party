// SPDX-License-Identifier: BSD-2-Clause-Patent
// Runs in the erased Image prefix before PrePi. Default mode is diagnostic-only.
// CHIRON_ZERO_TAIL restores an explicitly verified zero tail before continuing.
#ifndef CHIRON_KERNEL_RENDER_ONLY
.text
.balign 4
.global chiron_entry_probe
chiron_entry_probe:
.Lkernel_entry_image:
#ifdef CHIRON_KERNEL_WITNESS
    b .Lkernel_witness
#else
    msr daifset, #15
    mov x19, x0
    mov x20, x1
#ifdef CHIRON_ZERO_TAIL
    mov x27, sp
#endif
    add x16, x20, #8, lsl #12
    mov sp, x16
#ifdef CHIRON_ZERO_TAIL
    stp x2, x3, [sp, #-16]!
    b .Lrestore_zero_tail
#else
    mov x0, #1
    adr x1, .Lentry
    mov w2, #20
    mov w3, #0x00ff00
    bl .Lprint_value
    mov x0, x20
    adr x1, .Limage
    mov w2, #52
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x19
    adr x1, .Lfdt
    mov w2, #84
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x19
    bl .Lread_fdt
    mov x21, x0
    mov x22, x1
    mov x23, x2
    mov x0, x22
    adr x1, .Linit
    mov w2, #116
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x23
    adr x1, .Lend
    mov w2, #148
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, #0
    cbnz x21, .Lshow_size
    sub x0, x23, x22
.Lshow_size:
    adr x1, .Lsize
    mov w2, #180
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x21
    adr x1, .Lstate
    mov w2, #212
    mov w3, #0xffff00
    bl .Lprint_value
    // Bits: fixed store in Image, fixed store in initrd, FDT in Image, FDT in initrd.
    mov x24, #0
    mov x3, #0x83300000
    add x4, x20, #0x600, lsl #12
    cmp x3, x20
    b.lo .Lover_init
    cmp x3, x4
    b.hs .Lover_init
    orr x24, x24, #1
.Lover_init:
    cbnz x21, .Lover_fdt_image
    cmp x3, x22
    b.lo .Lover_fdt_image
    cmp x3, x23
    b.hs .Lover_fdt_image
    orr x24, x24, #2
.Lover_fdt_image:
    cmp x19, x20
    b.lo .Lover_fdt_init
    cmp x19, x4
    b.hs .Lover_fdt_init
    orr x24, x24, #4
.Lover_fdt_init:
    cbnz x21, .Lshow_overlap
    cmp x19, x22
    b.lo .Lshow_overlap
    cmp x19, x23
    b.hs .Lshow_overlap
    orr x24, x24, #8
.Lshow_overlap:
    mov x0, x24
    adr x1, .Lover
    mov w2, #244
    mov w3, #0xffff00
    bl .Lprint_value
#endif
    dsb sy
#endif
.global chiron_entry_probe_halt
chiron_entry_probe_halt:
.Lhalt:
    wfe
    b .Lhalt

#ifdef CHIRON_KERNEL_WITNESS
.Lkernel_witness:
    // x1 originally names this diagnostic entry. Reconstruct the normal entry
    // when restoring, and save every other GPR without borrowing the EFI stack.
    adr x1, .Lwitness_saved
    stp x0, x2, [x1, #0]
    stp x3, x4, [x1, #16]
    stp x5, x6, [x1, #32]
    stp x7, x8, [x1, #48]
    stp x9, x10, [x1, #64]
    stp x11, x12, [x1, #80]
    stp x13, x14, [x1, #96]
    stp x15, x16, [x1, #112]
    stp x17, x18, [x1, #128]
    stp x19, x20, [x1, #144]
    stp x21, x22, [x1, #160]
    stp x23, x24, [x1, #176]
    stp x25, x26, [x1, #192]
    stp x27, x28, [x1, #208]
    stp x29, x30, [x1, #224]
    mrs x9, NZCV
    str x9, [x1, #240]
    mov x9, sp
    str x9, [x1, #248]
    mrs x9, CurrentEL
    cmp x9, #4
    b.ne .Lwitness_restore
    mrs x19, SCTLR_EL1
    tbnz x19, #0, .Lwitness_restore
    tbnz x19, #2, .Lwitness_restore
    ldr x9, [x0, #0x28]
    mov x10, #0x9d400000
    cmp x9, x10
    b.ne .Lwitness_restore
    ldr x9, [x0, #0x38]
    mov x10, #4320
    cmp x9, x10
    b.ne .Lwitness_restore
    ldr x9, [x0, #0x40]
    cmp x9, #1080
    b.ne .Lwitness_restore
    ldr x9, [x0, #0x48]
    cmp x9, #2160
    b.ne .Lwitness_restore
    ldr x9, [x0, #0x50]
    cmp x9, #32
    b.ne .Lwitness_restore
    mov x18, x0
    adr x20, .Lkernel_entry_image
    ldr x21, .Lwitness_delta
    add x21, x20, x21
    adr x9, .Lwitness_stack_top
    mov sp, x9
    ldr x9, =(0x9d400000 + 3 * 583200)
    ldr x10, =583200
    mov w11, #-1
.Lwitness_white:
    stp w11, w11, [x9], #8
    subs x10, x10, #8
    b.ne .Lwitness_white
    dsb sy
    mov x0, x20
    adr x1, .Lwitness_title
    mov w2, #416
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x21
    adr x1, .Lwitness_original
    mov w2, #448
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x18
    adr x1, .Lwitness_args
    mov w2, #480
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x19
    adr x1, .Lcontrol
    mov w2, #512
    mov w3, #0xffffff
    bl .Lprint_value
.Lwitness_restore:
    adr x1, .Lwitness_saved
    ldr x9, [x1, #240]
    msr NZCV, x9
    ldr x9, [x1, #248]
    mov sp, x9
    ldp x0, x2, [x1, #0]
    ldp x3, x4, [x1, #16]
    ldp x5, x6, [x1, #32]
    ldp x7, x8, [x1, #48]
    ldp x9, x10, [x1, #64]
    ldp x11, x12, [x1, #80]
    ldp x13, x14, [x1, #96]
    ldp x15, x16, [x1, #112]
    ldp x17, x18, [x1, #128]
    ldp x19, x20, [x1, #144]
    ldp x21, x22, [x1, #160]
    ldp x23, x24, [x1, #176]
    ldp x25, x26, [x1, #192]
    ldp x27, x28, [x1, #208]
    ldp x29, x30, [x1, #224]
    adr x1, .Lkernel_entry_image
    ldr x9, .Lwitness_delta
    add x1, x1, x9
    ldr x9, .Lwitness_saved + 64
.global ChironWitnessOriginalBranch
ChironWitnessOriginalBranch:
    br x1
#endif

#ifdef CHIRON_ZERO_TAIL
.Lrestore_zero_tail:
    mov x21, #0
    mov x22, #0
    mov x25, #0
    mov x26, #0
    mov x0, #0x3000000
    adr x1, .Lexpand
    mov w2, #20
    mov w3, #0x00ff00
    bl .Lprint_value
    mov x28, #0x10
    mrs x5, CurrentEL
    mov x26, x5
    cmp x5, #4
    b.eq .Lcheck_el1
    cmp x5, #8
    b.ne .Lrestore_error
    mrs x5, SCTLR_EL2
    b .Lcheck_translation
.Lcheck_el1:
    mrs x5, SCTLR_EL1
.Lcheck_translation:
    mov x25, x5
    // Do not modify physical ranges while an unknown translation/cache is active.
    tbnz x5, #0, .Lrestore_error
    tbnz x5, #2, .Lrestore_error
    mov x28, #0x11
    mov x5, #0x40080000
    cmp x20, x5
    b.ne .Lrestore_error
    mov x5, #0x43200000
    cmp x19, x5
    b.ne .Lrestore_error
    mov x0, x19
    bl .Lread_fdt
    mov x28, x0
    cbnz x0, .Lrestore_error
    mov x21, x1
    mov x22, x2
    mov x23, x3
    mov x24, x4
    mov x28, #0x12
    mov x5, #0x43400000
    cmp x21, x5
    b.ne .Lrestore_error
    mov x5, #0x44c00000
    cmp x22, x5
    b.ne .Lrestore_error
    cbz x23, .Lrestore_error
    cmp x24, #4
    b.eq .Lcheck_fat_header
    cmp x24, #8
    b.ne .Lrestore_error
.Lcheck_fat_header:
    mov x28, #0x13
    ldrb w5, [x21, #11]
    cbnz w5, .Lrestore_error
    ldrb w5, [x21, #12]
    cmp w5, #2
    b.ne .Lrestore_error
    ldrb w5, [x21, #16]
    cmp w5, #2
    b.ne .Lrestore_error
    ldrb w5, [x21, #17]
    cbnz w5, .Lrestore_error
    ldrb w5, [x21, #18]
    cbnz w5, .Lrestore_error
    ldrh w5, [x21, #22]
    cbnz w5, .Lrestore_error
    ldr w5, [x21, #32]
    mov w6, #0x18000
    cmp w5, w6
    b.ne .Lrestore_error
    ldrh w5, [x21, #510]
    mov w6, #0xaa55
    cmp w5, w6
    b.ne .Lrestore_error
    // Exact range observed in the 24 MiB handoff, inside the measured low RAM bank.
    // It excludes this Image, the FDT and all measured reserved-memory regions.
    mov x5, x22
    mov x6, #0x1800000
.Lzero_tail_loop:
    stp xzr, xzr, [x5], #16
    subs x6, x6, #16
    b.ne .Lzero_tail_loop
    dsb sy
    mov x25, #0x46400000
    cmp x24, #4
    b.eq .Lwrite_end32
    lsr x6, x25, #32
    rev w6, w6
    rev w7, w25
    stp w6, w7, [x23]
    b .Lexpanded
.Lwrite_end32:
    rev w6, w25
    str w6, [x23]
.Lexpanded:
    dsb sy
    mov x0, #0x3000000
    adr x1, .Lready
    mov w2, #52
    mov w3, #0x00ff00
    bl .Lprint_value
    mov x0, x19
    mov x1, x20
    ldp x2, x3, [sp], #16
    add x16, x20, #8, lsl #12
    mov sp, x27
    br x16
.Lrestore_error:
    mov x0, x28
    adr x1, .Lfail
    mov w2, #52
    mov w3, #0xffff00
    bl .Lprint_value
    mov x0, x20
    adr x1, .Limage
    mov w2, #84
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x19
    adr x1, .Lfdt
    mov w2, #116
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x21
    adr x1, .Linit
    mov w2, #148
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x22
    adr x1, .Lend
    mov w2, #180
    mov w3, #0xffffff
    bl .Lprint_value
    mov x0, x26
    adr x1, .Llevel
    mov w2, #212
    mov w3, #0xffff00
    bl .Lprint_value
    mov x0, x25
    adr x1, .Lcontrol
    mov w2, #244
    mov w3, #0xffff00
    bl .Lprint_value
    b .Lhalt
#endif

#endif
// x0=value, x1=label, w2=y, w3=color. Caller supplies a diagnostic stack.
.Lprint_value:
    stp x29, x30, [sp, #-96]!
    mov x29, sp
    mov x9, x0
    mov w10, w2
    mov w11, w3
    add x12, sp, #16
.Llabel_loop:
    ldrb w13, [x1], #1
    cbz w13, .Lhex_begin
    strb w13, [x12], #1
    b .Llabel_loop
.Lhex_begin:
    mov w14, #16
.Lhex_loop:
    lsr x13, x9, #60
    cmp w13, #10
    add w15, w13, #48
    add w16, w13, #55
    csel w13, w15, w16, lo
    strb w13, [x12], #1
    lsl x9, x9, #4
    subs w14, w14, #1
    b.ne .Lhex_loop
    strb wzr, [x12]
    add x0, sp, #16
    mov w1, w10
    mov w2, w11
    bl .Ldraw_string
    ldp x29, x30, [sp], #96
    ret

// Draw only within measured display-reserved RAM, using four possible scanout layouts.
// Candidates are observations, not a claim that the pre-UEFI scanout address is known.
.Ldraw_string:
    adr x3, .Lfont
    mov w4, #16
    mov x16, #4320
    mov x17, #4352
.Lchar:
    ldrb w5, [x0], #1
    cbz w5, .Ldraw_done
    cmp w4, #1000
    b.hs .Ldraw_done
    mov w6, #0
    cmp w5, #48
    b.lo .Lglyph
    cmp w5, #57
    b.hi .Lletter
    sub w6, w5, #47
    b .Lglyph
.Lletter:
    cmp w5, #65
    b.lo .Lglyph
    cmp w5, #90
    b.hi .Lglyph
    sub w6, w5, #54
.Lglyph:
    mov w7, #5
    mul w6, w6, w7
    add x7, x3, x6
    mov w8, #0
.Lcolumn:
    mov w9, #0
    cmp w8, #5
    b.eq .Lcolumn_blank
    ldrb w9, [x7, x8]
.Lcolumn_blank:
    mov w10, #0
.Lpixel_row:
    lsr w11, w9, w10
    tst w11, #1
    csel w12, w2, wzr, ne
    add w11, w10, w10, lsl #1
    add w11, w11, w1
    mov x13, #0x9d400000
    madd x13, x11, x16, x13
#if !defined(CHIRON_KERNEL_WITNESS) && !defined(CHIRON_KERNEL_RENDER_ONLY)
    mov x15, #0x9d400000
    add x6, x11, #350
    madd x15, x6, x17, x15
    add w14, w8, w8, lsl #1
    add w14, w14, w4
    add x13, x13, x14, lsl #2
    add x15, x15, x14, lsl #2
    ldr x5, =(0x200000 + 850 * 4320)
    add x5, x13, x5
    ldr x14, =(0x200000 + 850 * 4352)
    add x14, x15, x14
#else
    add w14, w8, w8, lsl #1
    add w14, w14, w4
    add x13, x13, x14, lsl #2
#endif
    mov w6, #3
.Lrepeat_row:
    stp w12, w12, [x13]
    str w12, [x13, #8]
#if !defined(CHIRON_KERNEL_WITNESS) && !defined(CHIRON_KERNEL_RENDER_ONLY)
    stp w12, w12, [x15]
    str w12, [x15, #8]
    stp w12, w12, [x5]
    str w12, [x5, #8]
    stp w12, w12, [x14]
    str w12, [x14, #8]
#endif
    add x13, x13, x16
#if !defined(CHIRON_KERNEL_WITNESS) && !defined(CHIRON_KERNEL_RENDER_ONLY)
    add x15, x15, x17
    add x5, x5, x16
    add x14, x14, x17
#endif
    subs w6, w6, #1
    b.ne .Lrepeat_row
    add w10, w10, #1
    cmp w10, #7
    b.lo .Lpixel_row
    add w8, w8, #1
    cmp w8, #6
    b.lo .Lcolumn
    add w4, w4, #18
    b .Lchar
.Ldraw_done:
    dsb sy
    ret

// Returns state, start, end, end-property pointer and width in x0..x4.
.Lread_fdt:
    stp x29, x30, [sp, #-144]!
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    stp x25, x26, [sp, #64]
    stp x27, x28, [sp, #80]
    mov x19, x0
    mov x24, #0
    mov x25, #0
    mov x26, #0
    mov x27, #0
    mov x28, #0
    str xzr, [sp, #120]
    str xzr, [sp, #128]
    tst x19, #7
    b.ne .Lbad_header
    mov x3, #0x40000000
    cmp x19, x3
    b.lo .Lbad_header
    movz x3, #0xbd8c, lsl #16
    movk x3, #1, lsl #32
    sub x3, x3, #40
    cmp x19, x3
    b.hi .Lbad_header
    ldr w3, [x19]
    rev w3, w3
    movz w4, #0xfeed
    movk w4, #0xd00d, lsl #16
    cmp w3, w4
    b.ne .Lbad_header
    ldr w3, [x19, #4]
    rev w3, w3
    cmp w3, #40
    b.lo .Lbad_header
    cmp w3, #0x200, lsl #12
    b.hi .Lbad_header
    add x5, x19, x3
    movz x4, #0xbd8c, lsl #16
    movk x4, #1, lsl #32
    cmp x5, x4
    b.hi .Lbad_header
    ldr w3, [x19, #8]
    rev w3, w3
    cmp w3, #40
    b.lo .Lbad_header
    tst w3, #3
    b.ne .Lbad_header
    add x20, x19, x3
    ldr w4, [x19, #36]
    rev w4, w4
    add x21, x20, x4
    cmp x21, x5
    b.hi .Lbad_header
    ldr w3, [x19, #12]
    rev w3, w3
    cmp w3, #40
    b.lo .Lbad_header
    add x22, x19, x3
    ldr w4, [x19, #32]
    rev w4, w4
    add x23, x22, x4
    cmp x23, x5
    b.hi .Lbad_header
.Ltoken:
    add x3, x20, #4
    cmp x3, x21
    b.hi .Lbad_structure
    ldr w3, [x20], #4
    rev w3, w3
    cmp w3, #1
    b.eq .Lbegin_node
    cmp w3, #2
    b.eq .Lend_node
    cmp w3, #3
    b.eq .Lproperty
    cmp w3, #4
    b.eq .Ltoken
    cmp w3, #9
    b.eq .Lfinish_tree
    b .Lbad_structure
.Lbegin_node:
    add x27, x27, #1
    cmp x27, #64
    b.hi .Lbad_structure
    cmp x27, #2
    b.ne .Lskip_node_name
    mov x0, x20
    adr x1, .Lchosen_name
    mov x2, x21
    bl .Lequal_string
    cbz x0, .Lskip_node_name
    mov x28, #2
.Lskip_node_name:
    cmp x20, x21
    b.hs .Lbad_structure
    ldrb w3, [x20], #1
    cbnz w3, .Lskip_node_name
    add x20, x20, #3
    and x20, x20, #-4
    b .Ltoken
.Lend_node:
    cbz x27, .Lbad_structure
    cmp x27, x28
    b.ne .Ldecrement_depth
    mov x28, #0
.Ldecrement_depth:
    sub x27, x27, #1
    b .Ltoken
.Lproperty:
    add x3, x20, #8
    cmp x3, x21
    b.hi .Lbad_structure
    ldp w3, w4, [x20], #8
    rev w3, w3
    rev w4, w4
    mov x5, x20
    add x6, x5, x3
    cmp x6, x21
    b.hi .Lbad_structure
    add x20, x6, #3
    and x20, x20, #-4
    cbz x28, .Ltoken
    cmp x27, x28
    b.ne .Ltoken
    add x6, x22, x4
    cmp x6, x23
    b.hs .Lbad_structure
    stp x5, x3, [sp, #96]
    str x6, [sp, #112]
    mov x0, x6
    adr x1, .Lstart_name
    mov x2, x23
    bl .Lequal_string
    mov w16, #1
    cbnz x0, .Laddress_value
    ldr x0, [sp, #112]
    adr x1, .Lend_name
    mov x2, x23
    bl .Lequal_string
    mov w16, #2
    cbz x0, .Ltoken
.Laddress_value:
    ldp x5, x3, [sp, #96]
    cmp x3, #4
    b.eq .Laddress32
    cmp x3, #8
    b.ne .Lbad_address_width
    ldp w3, w4, [x5]
    rev w3, w3
    rev w4, w4
    orr x4, x4, x3, lsl #32
    b .Lsave_address
.Laddress32:
    ldr w4, [x5]
    rev w4, w4
.Lsave_address:
    tst x26, x16
    b.ne .Lbad_structure
    orr x26, x26, x16
    cmp w16, #1
    b.ne .Lsave_end
    mov x24, x4
    b .Ltoken
.Lsave_end:
    mov x25, x4
    str x5, [sp, #120]
    ldr x6, [sp, #104]
    str x6, [sp, #128]
    b .Ltoken
.Lfinish_tree:
    cbnz x27, .Lbad_structure
    cmp x26, #3
    b.ne .Lmissing_initrd
    cmp x25, x24
    b.ls .Lbad_range
    mov x0, #0
    b .Lparse_return
.Lbad_header:
    mov x0, #1
    b .Lparse_return
.Lbad_structure:
    mov x0, #2
    b .Lparse_return
.Lmissing_initrd:
    mov x0, #3
    b .Lparse_return
.Lbad_address_width:
    mov x0, #4
    b .Lparse_return
.Lbad_range:
    mov x0, #5
.Lparse_return:
    mov x1, x24
    mov x2, x25
    ldr x3, [sp, #120]
    ldr x4, [sp, #128]
    ldp x19, x20, [sp, #16]
    ldp x21, x22, [sp, #32]
    ldp x23, x24, [sp, #48]
    ldp x25, x26, [sp, #64]
    ldp x27, x28, [sp, #80]
    ldp x29, x30, [sp], #144
    ret

// x0=untrusted string, x1=trusted literal, x2=untrusted string bound.
.Lequal_string:
    cmp x0, x2
    b.hs .Lnot_equal
    ldrb w3, [x0], #1
    ldrb w4, [x1], #1
    cmp w3, w4
    b.ne .Lnot_equal
    cbnz w3, .Lequal_string
    mov x0, #1
    ret
.Lnot_equal:
    mov x0, #0
    ret

.Lentry: .asciz "ENTRY "
.Limage: .asciz "IMAGE "
.Lfdt:   .asciz "FDT   "
.Linit:  .asciz "INIT  "
.Lend:   .asciz "END   "
.Lsize:  .asciz "SIZE  "
.Lstate: .asciz "STATE "
.Lover:  .asciz "OVER  "
.Lexpand: .asciz "EXPAND "
.Lready: .asciz "READY  "
.Lfail: .asciz "FAIL   "
.Llevel: .asciz "EL     "
.Lcontrol: .asciz "SCTLR  "
#ifdef CHIRON_KERNEL_WITNESS
.Lwitness_title: .asciz "KERNEL ENTRY "
.Lwitness_original: .asciz "ORIG ENTRY "
.Lwitness_args: .asciz "BOOT ARGS "
.balign 8
.Lwitness_delta:
    .quad CHIRON_ENTRY_DELTA
.balign 16
.Lwitness_saved:
    .space 256, 0
    .space 1024, 0
.global ChironWitnessStackTop
ChironWitnessStackTop:
.Lwitness_stack_top:
#endif
.Lchosen_name: .asciz "chosen"
.Lstart_name: .asciz "linux,initrd-start"
.Lend_name: .asciz "linux,initrd-end"
.Lfont:
    .byte 0,0,0,0,0
    .byte 0x3e,0x51,0x49,0x45,0x3e
    .byte 0,0x42,0x7f,0x40,0
    .byte 0x42,0x61,0x51,0x49,0x46
    .byte 0x21,0x41,0x45,0x4b,0x31
    .byte 0x18,0x14,0x12,0x7f,0x10
    .byte 0x27,0x45,0x45,0x45,0x39
    .byte 0x3c,0x4a,0x49,0x49,0x30
    .byte 1,0x71,9,5,3
    .byte 0x36,0x49,0x49,0x49,0x36
    .byte 6,0x49,0x49,0x29,0x1e
    .byte 0x7e,0x11,0x11,0x11,0x7e
    .byte 0x7f,0x49,0x49,0x49,0x36
    .byte 0x3e,0x41,0x41,0x41,0x22
    .byte 0x7f,0x41,0x41,0x22,0x1c
    .byte 0x7f,0x49,0x49,0x49,0x41
    .byte 0x7f,9,9,9,1
    .byte 0x3e,0x41,0x49,0x51,0x32
    .byte 0x7f,8,8,8,0x7f
    .byte 0,0x41,0x7f,0x41,0
    .byte 0x20,0x40,0x41,0x3f,1
    .byte 0x7f,8,0x14,0x22,0x41
    .byte 0x7f,0x40,0x40,0x40,0x40
    .byte 0x7f,2,0x0c,2,0x7f
    .byte 0x7f,4,8,0x10,0x7f
    .byte 0x3e,0x41,0x41,0x41,0x3e
    .byte 0x7f,9,9,9,6
    .byte 0x3e,0x41,0x51,0x21,0x5e
    .byte 0x7f,9,0x19,0x29,0x46
    .byte 0x46,0x49,0x49,0x49,0x31
    .byte 1,1,0x7f,1,1
    .byte 0x3f,0x40,0x40,0x40,0x3f
    .byte 0x1f,0x20,0x40,0x20,0x1f
    .byte 0x3f,0x40,0x38,0x40,0x3f
    .byte 0x63,0x14,8,0x14,0x63
    .byte 7,8,0x70,8,7
    .byte 0x61,0x51,0x49,0x45,0x43
.balign 4
.ltorg
