// SPDX-License-Identifier: BSD-2-Clause-Patent
// Own static Darwin PID 1: verify the ABI, then write and sleep repeatedly.
.text
.p2align 2
.global _start, _checkpoint
_start:
  adrp x19, _results@PAGE
  add x19, x19, _results@PAGEOFF
  mov x16, #20
  svc #0x80
  str x0, [x19]
  mrs x9, nzcv
  str x9, [x19, #16]
  b.cs Lfailure
  cmp x0, #1
  b.ne Lfailure

  adrp x0, Lmib@PAGE
  add x0, x0, Lmib@PAGEOFF
  mov x1, #2
  adrp x2, _user_page_size@PAGE
  add x2, x2, _user_page_size@PAGEOFF
  adrp x3, _old_length@PAGE
  add x3, x3, _old_length@PAGEOFF
  mov x4, #0
  mov x5, #0
  mov x16, #202
  svc #0x80
  str x0, [x19, #8]
  mrs x9, nzcv
  str x9, [x19, #24]
  b.cs Lfailure
  adrp x9, _user_page_size@PAGE
  ldr x9, [x9, _user_page_size@PAGEOFF]
  cmp x9, #4096
  b.ne Lfailure

  adrp x0, Lconsole@PAGE
  add x0, x0, Lconsole@PAGEOFF
  mov x1, #1
  mov x2, #0
  mov x16, #5
  svc #0x80
  b.cs Lfailure
  mov x20, x0
#ifdef CHIRON_RAM_SELFTEST
  // Darwin's argc/argv entry stack is not a C call frame. Align before BL/STP.
  mov x9, sp
  and x9, x9, #-16
  mov sp, x9
  bl _chiron_run_selftests
  cbz x0, Lheartbeat
  mov x9, #-1
  adrp x10, _chiron_init_status@PAGE
  str x9, [x10, _chiron_init_status@PAGEOFF]
#endif
Lheartbeat:
  mov x0, x20
  adrp x1, Lmessage@PAGE
  add x1, x1, Lmessage@PAGEOFF
#ifdef CHIRON_RAM_SELFTEST
  adrp x9, _chiron_selftest_results@PAGE
  add x9, x9, _chiron_selftest_results@PAGEOFF
  ldr x10, [x9, #16]
  cmp x10, #15
  b.ne Lheartbeat_failed
  ldr x10, [x9, #24]
  cbz x10, Lheartbeat_message
Lheartbeat_failed:
  adrp x1, Lmessage_failed@PAGE
  add x1, x1, Lmessage_failed@PAGEOFF
Lheartbeat_message:
#endif
  mov x2, #(Lmessage_end-Lmessage)
  mov x16, #4
  svc #0x80
  b.cs Lfailure
  cmp x0, #(Lmessage_end-Lmessage)
  b.ne Lfailure

  mov x0, #0
  mov x1, #0
  mov x2, #0
  mov x3, #0
  adrp x4, Ltimeval@PAGE
  add x4, x4, Ltimeval@PAGEOFF
  mov x9, #1
  str x9, [x4]
  str xzr, [x4, #8]
  mov x16, #93
  svc #0x80
  b.cs Lfailure
  cbnz x0, Lfailure

  adrp x21, _chiron_heartbeats@PAGE
  add x21, x21, _chiron_heartbeats@PAGEOFF
  ldr x9, [x21]
  add x9, x9, #1
  str x9, [x21]
  cmp x9, #2
  b.lo Lheartbeat
#ifdef CHIRON_RAM_SELFTEST
  adrp x10, _chiron_init_status@PAGE
  ldr x9, [x10, _chiron_init_status@PAGEOFF]
  cmn x9, #1
  b.eq Lheartbeat
#endif
  mov x9, #2
  adrp x10, _chiron_init_status@PAGE
  str x9, [x10, _chiron_init_status@PAGEOFF]
_checkpoint:
  adrp x10, _chiron_init_status@PAGE
  ldr x9, [x10, _chiron_init_status@PAGEOFF]
  cmp x9, #2
  b.eq Lheartbeat
  b _checkpoint

Lfailure:
  mov x9, #-1
  adrp x10, _chiron_init_status@PAGE
  str x9, [x10, _chiron_init_status@PAGEOFF]
  b _checkpoint

.data
.balign 8
.global _results, _user_page_size, _old_length
.global _chiron_heartbeats, _chiron_init_status
_results:
  .quad 0, 0, 0, 0
_user_page_size:
  .quad 0
_old_length:
  .quad 8
_chiron_heartbeats:
  .quad 0
_chiron_init_status:
  .quad 0
Lmib:
  .word 6, 7
Ltimeval:
  .quad 1, 0
Lconsole:
  .asciz "/dev/console"
Lmessage:
  .ascii "CHIRON RAM init: PID 1, 4K ABI, heartbeat"
#ifdef CHIRON_RAM_SELFTEST
  .ascii " SELFTEST PASS"
#endif
  .ascii "\n"
Lmessage_end:

#ifdef CHIRON_RAM_SELFTEST
Lmessage_failed:
  .ascii "CHIRON RAM init: PID 1, 4K ABI, heartbeat SELFTEST FAIL\n"
Lmessage_failed_end:
  .if (Lmessage_failed_end-Lmessage_failed) != (Lmessage_end-Lmessage)
  .error "selftest heartbeat variants must have equal write lengths"
  .endif
#include "chiron-selftest-main.S"
#endif
