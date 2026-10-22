// SPDX-License-Identifier: BSD-2-Clause-Patent
// A separate static Mach-O, not a fork branch or an entry in PID 1's image.
.text
.p2align 2
.global _start, _chiron_selftest_child_entry
_start:
_chiron_selftest_child_entry:
  mov x16, #20
  svc #0x80
  b.cs Lchild_bad
  cmp x0, #1
  b.ls Lchild_bad
  mov x16, #39
  svc #0x80
  b.cs Lchild_bad
  cmp x0, #1
  b.ne Lchild_bad
  adrp x0, Lchild_console@PAGE
  add x0, x0, Lchild_console@PAGEOFF
  mov x1, #1
  mov x2, #0
  mov x16, #5
  svc #0x80
  b.cs Lchild_bad
  mov x19, x0
  adrp x1, Lchild_message@PAGE
  add x1, x1, Lchild_message@PAGEOFF
  mov x2, #(Lchild_message_end-Lchild_message)
  mov x16, #4
  svc #0x80
  b.cs Lchild_bad
  cmp x0, #(Lchild_message_end-Lchild_message)
  b.ne Lchild_bad
  mov x0, x19
  mov x16, #6
  svc #0x80
  b.cs Lchild_bad
  mov x0, #37
  b Lchild_exit
Lchild_bad:
  mov x0, #111
Lchild_exit:
  mov x16, #1
  svc #0x80
  b .                       // exit must not return.
.section __TEXT,__st_marker,regular,no_dead_strip
.global _chiron_selftest_child_marker
_chiron_selftest_child_marker:
  .ascii "CHIRON-SELFTEST-INDEPENDENT-CHILD-v1\n"
.data
Lchild_console:
  .asciz "/dev/console"
Lchild_message:
  .ascii "CHIRON SELFTEST CHILD PASS pid>1 ppid=1 exit=37\n"
Lchild_message_end:
