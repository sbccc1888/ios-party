// SPDX-License-Identifier: BSD-2-Clause-Patent
// Static PID 1 diagnostic; trap numbers/layout pinned by the independent builder.
#ifndef CHIRON_ABI_CHILD_SIZE
#error Build with ci/build_chiron_abi_probe.py.
#endif
#ifdef CHIRON_ABI_RAMFS
#ifndef CHIRON_ABI_FILE_SUM
#error RAMFS probe requires the expected uint64 file byte sum.
#endif
#endif

.macro ADDRESS reg, symbol
  adrp \reg, \symbol@PAGE
  add \reg, \reg, \symbol@PAGEOFF
.endmacro
.macro STAGE number
  mov x9, #\number
  str x9, [x28, #24]
.endmacro
.macro CALL number, stage
  STAGE \stage
  mov x16, #\number
  svc #0x80
  mrs x9, nzcv
  str x16, [x28, #32]
  str x0, [x28, #40]
  str x9, [x28, #48]
.endmacro
.macro PASS bit, label
  ldr x9, [x28, #16]
  orr x9, x9, #\bit
  str x9, [x28, #16]
  ldr x9, [x28, #24]
  str x9, [x28, #176]
  ADDRESS x1, \label
  mov x2, #(\label\()_end-\label)
  bl Lwrite
.endmacro

.text
.p2align 2
.global _start, _checkpoint
_start:
  mov x10, sp
  and x11, x10, #-16
  mov sp, x11
  ADDRESS x28, _chiron_abi_results
  str x10, [x28, #184]
  str x11, [x28, #192]
  mov x20, #-1
  ADDRESS x0, Lconsole
  mov x1, #1
  mov x2, #0
  CALL 5, 1
  b.cs Lfailure
  mov x20, x0
  str x20, [x28, #168]

  CALL 20, 2
  ADDRESS x19, _results
  str x0, [x19]
  str x9, [x19, #16]
  b.cs Lfailure
  cmp x0, #1
  b.ne Lfailure
  ADDRESS x0, Lmib
  mov x1, #2
  ADDRESS x2, _user_page_size
  ADDRESS x3, _old_length
  mov x4, #0
  mov x5, #0
  CALL 202, 3
  str x0, [x19, #8]
  str x9, [x19, #24]
  b.cs Lfailure
  cbnz x0, Lfailure
  ADDRESS x9, _user_page_size
  ldr x10, [x9]
  cmp x10, #4096
  b.ne Lfailure
  ADDRESS x9, _old_length
  ldr x10, [x9]
  cmp x10, #4
  b.ne Lfailure

  CALL -28, 10             // task_self_trap: port name, not kern_return_t
  mov w23, w0
  cbz w23, Lfailure
  cmn w23, #1
  b.eq Lfailure
  str x23, [x28, #56]
  mov x0, x23
  mov x1, #1               // MACH_PORT_RIGHT_RECEIVE
  ADDRESS x2, Lport
  CALL -16, 11
  cbnz w0, Lfailure
  ADDRESS x9, Lport
  ldr w24, [x9]
  cbz w24, Lfailure
  cmn w24, #1
  b.eq Lfailure
  str x24, [x28, #64]
  mov x0, x23
  mov x1, x24
  mov x2, x24
  mov x3, #20              // MACH_MSG_TYPE_MAKE_SEND
  CALL -21, 12
  cbnz w0, Lfailure
  PASS 1, Lpass_port

  ADDRESS x9, Lsend
  str w24, [x9, #8]
  mov x0, x9
  mov x1, #0x11            // MACH_SEND_MSG | MACH_SEND_TIMEOUT
  movk x1, #4, lsl #32     // MACH64_SEND_MQ_CALL
  mov x2, #19              // COPY_SEND | (32-byte send_size << 32)
  movk x2, #32, lsl #32
  mov x3, x24              // remote port; no reply port
  mov x4, #0
  movk x4, #0x4348, lsl #32 // message id, voucher=0
  mov x5, #0
  mov x6, #0
  mov x7, #100             // bounded milliseconds
  CALL -47, 20
  str x0, [x28, #200]
  cbnz w0, Lfailure
  bl Lreceive_args
  mov x7, #100
  CALL -47, 21
  str x0, [x28, #208]
  cbnz w0, Lfailure
  STAGE 22
  ADDRESS x9, Lreceive
  ldr w10, [x9]            // remote disposition none, local receive name
  mov w11, #17
  lsl w11, w11, #8
  cmp w10, w11
  b.ne Lfailure
  ldr w10, [x9, #4]
  cmp w10, #32
  b.ne Lfailure
  ldr w10, [x9, #8]
  cbnz w10, Lfailure
  ldr w10, [x9, #12]
  cmp w10, w24
  b.ne Lfailure
  ldr w10, [x9, #16]
  cbnz w10, Lfailure
  ldr w10, [x9, #20]
  str x10, [x28, #72]
  mov w11, #0x4348
  cmp w10, w11
  b.ne Lfailure
  ldr x10, [x9, #24]
  str x10, [x28, #80]
  ADDRESS x11, Lsend
  ldr x11, [x11, #24]
  cmp x10, x11
  b.ne Lfailure
  PASS 2, Lpass_message

  bl Lreceive_args
  mov x7, #1               // empty queue: must time out, never wait forever
  CALL -47, 23
  str x0, [x28, #88]
  mov w9, #0x4003
  movk w9, #0x1000, lsl #16
  cmp w0, w9
  b.ne Lfailure
  mov x0, x23
  mov x1, x24
  mov x2, #0               // release inserted send right first
  mov x3, #-1
  CALL -19, 24
  cbnz w0, Lfailure
  mov x0, x23
  mov x1, x24
  mov x2, #1               // then release receive right
  mov x3, #-1
  CALL -19, 25
  str x0, [x28, #216]
  cbnz w0, Lfailure
  PASS 64, Lpass_timeout

  ADDRESS x0, Ltimebase
  CALL -89, 30
  cbnz w0, Lfailure
  ADDRESS x9, Ltimebase
  ldr w10, [x9]
  str x10, [x28, #112]
  cbz w10, Lfailure
  ldr w10, [x9, #4]
  str x10, [x28, #120]
  cbz w10, Lfailure
  CALL -3, 31              // MACH_ARM_TRAP_ABSTIME special SVC, not table slot
  str x0, [x28, #96]
  cbz x0, Lfailure
  bl Lsleep_args
  CALL 93, 32
  b.cs Lfailure
  cbnz x0, Lfailure
  CALL -3, 33
  str x0, [x28, #104]
  ldr x9, [x28, #96]
  cmp x0, x9
  b.ls Lfailure
  PASS 4, Lpass_time

  STAGE 40
  mov x9, #0x4000
  movk x9, #0xffff, lsl #16
  movk x9, #0xf, lsl #32   // fixed _COMM_PAGE64_RO_ADDRESS = 0xfffff4000
  ldrb w10, [x9, #0x25]
  str x10, [x28, #128]
  cmp w10, #12
  b.ne Lfailure
  ldrb w10, [x9, #0x37]
  str x10, [x28, #136]
  cmp w10, #12
  b.ne Lfailure
  PASS 8, Lpass_commpage

  mov x0, #0
  mov x1, #8192
  mov x2, #3
  mov x3, #0x1002          // MAP_ANON | MAP_PRIVATE
  mov x4, #-1
  mov x5, #0
  CALL 197, 50
  b.cs Lfailure
  mov x25, x0
  str x0, [x28, #144]
  cbz x0, Lfailure
  tst x0, #4095
  b.ne Lfailure
  STAGE 51
  mov x9, #0
Lzero:
  ldr x10, [x25, x9]
  cbnz x10, Lfailure
  eor x10, x9, #0x5555555555555555
  str x10, [x25, x9]
  add x9, x9, #8
  cmp x9, #8192
  b.lo Lzero
  mov x0, x25
  mov x1, #8192
  mov x2, #1              // PROT_READ; inspect without intentionally faulting PID 1
  CALL 74, 52
  b.cs Lfailure
  cbnz x0, Lfailure
  STAGE 53
  mov x9, #0
Lread:
  ldr x10, [x25, x9]
  eor x11, x9, #0x5555555555555555
  cmp x10, x11
  b.ne Lfailure
  add x9, x9, #8
  cmp x9, #8192
  b.lo Lread
  mov x0, x25
  mov x1, #8192
  mov x2, #3
  CALL 74, 54
  b.cs Lfailure
  cbnz x0, Lfailure
  STAGE 55
  mov x9, #0x7391
  str x9, [x25, #4096]
  ldr x10, [x25, #4096]
  cmp x10, x9
  b.ne Lfailure
  mov x0, x25
  mov x1, #8192
  CALL 73, 56
  b.cs Lfailure
  cbnz x0, Lfailure
  PASS 16, Lpass_protect

  ADDRESS x0, Lfile
  mov x1, #0
  mov x2, #0
  CALL 5, 60
  b.cs Lfailure
  mov x26, x0
  mov x0, #0
  mov x1, #CHIRON_ABI_CHILD_MAP_SIZE
  mov x2, #1
  mov x3, #2              // MAP_PRIVATE, file offset=0
  mov x4, x26
  mov x5, #0
  CALL 197, 61
  b.cs Lfailure
  mov x25, x0
  str x0, [x28, #152]
  cbz x0, Lfailure
  tst x0, #4095
  b.ne Lfailure
  STAGE 62
  ldr w10, [x25]
  str x10, [x28, #160]
  mov w11, #0xfacf
  movk w11, #0xfeed, lsl #16
  cmp w10, w11
  b.ne Lfailure
  ldr w10, [x25, #4]
  mov w11, #12
  movk w11, #0x100, lsl #16
  cmp w10, w11
  b.ne Lfailure
  ldr w10, [x25, #12]
  cmp w10, #2
  b.ne Lfailure
  mov x9, #CHIRON_ABI_MARKER_OFFSET
  add x9, x25, x9
  ADDRESS x10, Lmarker
  mov x11, #(Lmarker_end-Lmarker)
Lmarker_compare:
  ldrb w12, [x9], #1
  ldrb w13, [x10], #1
  cmp w12, w13
  b.ne Lfailure
  subs x11, x11, #1
  b.ne Lmarker_compare
#ifdef CHIRON_ABI_RAMFS
  STAGE 65
  mov x9, x25
  mov x10, #0
  mov x11, #CHIRON_ABI_CHILD_SIZE
  cbz x11, Lfailure
Lfile_sum:
  ldrb w12, [x9], #1
  add x10, x10, x12         // uint64 modulo sum; read every byte across file pages
  subs x11, x11, #1
  b.ne Lfile_sum
  movz x13, #((CHIRON_ABI_FILE_SUM) & 0xffff)
  movk x13, #(((CHIRON_ABI_FILE_SUM) >> 16) & 0xffff), lsl #16
  movk x13, #(((CHIRON_ABI_FILE_SUM) >> 32) & 0xffff), lsl #32
  movk x13, #(((CHIRON_ABI_FILE_SUM) >> 48) & 0xffff), lsl #48
  cmp x10, x13
  b.ne Lfailure
#endif
  mov x9, #CHIRON_ABI_CHILD_SIZE
  str x9, [x28, #224]
  mov x0, x25
  mov x1, #CHIRON_ABI_CHILD_MAP_SIZE
  CALL 73, 63
  b.cs Lfailure
  cbnz x0, Lfailure
  mov x0, x26
  CALL 6, 64
  b.cs Lfailure
  cbnz x0, Lfailure
  PASS 32, Lpass_file

  ADDRESS x1, Lpass_all
  mov x2, #(Lpass_all_end-Lpass_all)
  bl Lwrite
  str xzr, [x28, #24]
Lheartbeat:
  ADDRESS x1, Lheartbeat_line
  mov x2, #(Lheartbeat_line_end-Lheartbeat_line)
  bl Lwrite
  bl Lsleep_args
  CALL 93, 91
  b.cs Lfailure
  cbnz x0, Lfailure
  ADDRESS x10, _chiron_heartbeats
  ldr x9, [x10]
  add x9, x9, #1
  str x9, [x10]
  str xzr, [x28, #24]
  cmp x9, #2
  b.lo Lheartbeat
  ADDRESS x10, _chiron_init_status
  mov x9, #2
  str x9, [x10]
_checkpoint:
  ADDRESS x10, _chiron_init_status
  ldr x9, [x10]
  cmp x9, #2
  b.eq Lheartbeat
  b _checkpoint

Lreceive_args:
  ADDRESS x0, Lreceive
  mov x1, #0x102          // MACH_RCV_MSG | MACH_RCV_TIMEOUT
  mov x2, #0
  mov x3, #0
  mov x4, #0
  lsl x5, x24, #32        // desc_count=0 | receive name << 32
  mov x6, #128
  ret
Lsleep_args:
  mov x0, #0
  mov x1, #0
  mov x2, #0
  mov x3, #0
  ADDRESS x4, Ltimeval
  mov x9, #1
  str x9, [x4]
  str xzr, [x4, #8]
  ret
Lwrite:
  mov x21, x30
  mov x0, x20
  CALL 4, 90
  b.cs Lfailure
  cmp x0, x2
  b.ne Lfailure
  ret x21
Lfailure:
  ADDRESS x10, _chiron_init_status
  mov x9, #-1
  str x9, [x10]
  // Keep the first failure's stage/return; best effort output uses an untracked SVC.
  ADDRESS x0, Lfail_stage
  ldr x9, [x28, #24]
  bl Lhex
  ADDRESS x0, Lfail_call
  ldr x9, [x28, #32]
  bl Lhex
  ADDRESS x0, Lfail_return
  ldr x9, [x28, #40]
  bl Lhex
  mov x0, x20
  ADDRESS x1, Lfail
  mov x2, #(Lfail_end-Lfail)
  mov x16, #4
  svc #0x80
  b _checkpoint
Lhex:
  mov x10, #60
  ADDRESS x12, Ldigits
Lhex_loop:
  lsr x11, x9, x10
  and x11, x11, #15
  ldrb w11, [x12, x11]
  strb w11, [x0], #1
  subs x10, x10, #4
  b.ge Lhex_loop
  ret

.data
.balign 8
.global _results, _user_page_size, _old_length
.global _chiron_heartbeats, _chiron_init_status
.global _chiron_abi_results, _chiron_abi_results_end
_results: .quad 0, 0, 0, 0
_user_page_size: .quad 0
_old_length: .quad 8
_chiron_heartbeats: .quad 0
_chiron_init_status: .quad 0
_chiron_abi_results:
  .quad 1, 127, 0, 0
  .space 224
_chiron_abi_results_end:
Ltimebase: .word 0, 0
Lport: .word 0
.balign 8
Lsend:
  .word 19, 32, 0, 0, 0, 0x4348
  .quad 0x434849524f4e4142
Lreceive: .space 128
Ltimeval: .quad 1, 0
Lmib: .word 6, 7
Lconsole: .asciz "/dev/console"
#ifdef CHIRON_ABI_RAMFS
Lfile: .asciz "/usr/lib/chiron/probe.bin"
#else
Lfile: .asciz "/probe.bin"
#endif
Lmarker: .ascii "CHIRON-SELFTEST-INDEPENDENT-CHILD-v1\n"
Lmarker_end:
Ldigits: .ascii "0123456789abcdef"
Lpass_port: .ascii "CHIRON ABI PASS mach-port allocate/insert\n"
Lpass_port_end:
Lpass_message: .ascii "CHIRON ABI PASS mach-msg2 send/receive/payload\n"
Lpass_message_end:
Lpass_timeout: .ascii "CHIRON ABI PASS empty-receive timeout/port-release\n"
Lpass_timeout_end:
Lpass_time: .ascii "CHIRON ABI PASS absolute-time/timebase\n"
Lpass_time_end:
Lpass_commpage: .ascii "CHIRON ABI PASS commpage 4K shifts\n"
Lpass_commpage_end:
Lpass_protect: .ascii "CHIRON ABI PASS mmap/mprotect/munmap\n"
Lpass_protect_end:
Lpass_file: .ascii "CHIRON ABI PASS readonly file-mmap/header/marker\n"
Lpass_file_end:
Lpass_all: .ascii "CHIRON ABI PROBE PASS\n"
Lpass_all_end:
Lheartbeat_line: .ascii "CHIRON RAM init: PID 1, 4K ABI, heartbeat ABI PROBE PASS\n"
Lheartbeat_line_end:
Lfail: .ascii "CHIRON ABI PROBE FAIL stage=0x"
Lfail_stage: .ascii "0000000000000000 call=0x"
Lfail_call: .ascii "0000000000000000 return=0x"
Lfail_return: .ascii "0000000000000000\n"
Lfail_end:
