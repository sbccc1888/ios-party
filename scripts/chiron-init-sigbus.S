// SPDX-License-Identifier: BSD-2-Clause-Patent
// Qualification fixture only: write to our own RX text to cause a real SIGBUS.
.text
.p2align 2
.global _start, _chiron_sigbus_store, _chiron_sigbus_target
_start:
  adrp x9, _chiron_sigbus_target@PAGE
  add x9, x9, _chiron_sigbus_target@PAGEOFF
_chiron_sigbus_store:
  str xzr, [x9]
  mov x0, #112
  mov x16, #1
  svc #0x80
  b .
.p2align 3
_chiron_sigbus_target:
  nop
  nop
.data
.balign 8
.global _results, _user_page_size, _old_length, _chiron_heartbeats, _chiron_init_status
_results:
  .quad 0, 0, 0, 0
_user_page_size:
  .quad 0
_old_length:
  .quad 0
_chiron_heartbeats:
  .quad 0
_chiron_init_status:
  .quad 0
