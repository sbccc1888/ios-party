// SPDX-License-Identifier: BSD-2-Clause-Patent
// Included only by the explicit RAM selftest build. x20 is the open console.
#ifndef CHIRON_SELFTEST_MARKER_OFFSET
#error Build with ci/build_ram_selftest.py to bind the child file layout.
#endif

.macro ST_ADDRESS reg, symbol
  adrp \reg, \symbol@PAGE
  add \reg, \reg, \symbol@PAGEOFF
.endmacro

// Keep the last SVC's raw return and carry flag even for semantic failures.
.macro ST_SYSCALL number, stage
  mov x27, #\stage
  str x27, [x28, #24]
  mov x16, #\number
  svc #0x80
  mrs x9, nzcv
  str x16, [x22]
  str x0, [x22, #8]
  str x9, [x22, #16]
.endmacro

.macro ST_MESSAGE label
  ST_ADDRESS x1, \label
  mov x2, #(\label\()_end-\label)
  bl Lst_write
  cbnz x0, Lst_failure
.endmacro

.text
.p2align 2
.global _chiron_run_selftests
_chiron_run_selftests:
  stp x29, x30, [sp, #-96]!
  mov x29, sp
  stp x19, x20, [sp, #16]
  stp x21, x22, [sp, #32]
  stp x23, x24, [sp, #48]
  stp x25, x26, [sp, #64]
  stp x27, x28, [sp, #80]
  ST_ADDRESS x28, _chiron_selftest_results
  ST_ADDRESS x22, _chiron_selftest_diagnostics

  ST_ADDRESS x0, Lst_path
  mov x1, #0
  mov x2, #0
  ST_SYSCALL 5, 10
  b.cs Lst_failure
  mov x26, x0
  mov x0, x26
  ST_ADDRESS x1, Lst_buffer
  mov x2, #32
  ST_SYSCALL 3, 11
  b.cs Lst_failure
  cmp x0, #32
  b.ne Lst_failure
  ST_ADDRESS x9, Lst_buffer
  ldr w10, [x9]
  mov w11, #0xfacf
  movk w11, #0xfeed, lsl #16
  cmp w10, w11
  b.ne Lst_failure
  ldr w10, [x9, #4]
  mov w11, #12
  movk w11, #0x100, lsl #16
  cmp w10, w11
  b.ne Lst_failure
  ldr w10, [x9, #12]
  cmp w10, #2
  b.ne Lst_failure

  mov x0, x26
  ST_ADDRESS x1, Lst_buffer
  mov x2, #(Lst_expected_end-Lst_expected)
  mov x3, #(CHIRON_SELFTEST_MARKER_OFFSET & 65535)
  movk x3, #((CHIRON_SELFTEST_MARKER_OFFSET >> 16) & 65535), lsl #16
  ST_SYSCALL 153, 12
  b.cs Lst_failure
  cmp x0, #(Lst_expected_end-Lst_expected)
  b.ne Lst_failure
  ST_ADDRESS x9, Lst_buffer
  ST_ADDRESS x10, Lst_expected
  mov x11, #(Lst_expected_end-Lst_expected)
Lst_file_compare:
  ldrb w12, [x9], #1
  ldrb w13, [x10], #1
  cmp w12, w13
  b.ne Lst_failure
  subs x11, x11, #1
  b.ne Lst_file_compare
  ST_ADDRESS x9, Lst_buffer
  ldr x9, [x9]
  str x9, [x22, #24]
  mov x0, x26
  ST_ADDRESS x1, Lst_buffer
  mov x2, #1
  mov x3, #(CHIRON_SELFTEST_CHILD_SIZE & 65535)
  movk x3, #((CHIRON_SELFTEST_CHILD_SIZE >> 16) & 65535), lsl #16
  ST_SYSCALL 153, 13
  b.cs Lst_failure
  cbnz x0, Lst_failure
  mov x0, x26
  ST_SYSCALL 6, 14
  b.cs Lst_failure
  mov x9, #1
  str x9, [x28, #16]
  ST_MESSAGE Lst_file_pass

  mov x0, #0
  mov x1, #8192
  mov x2, #3                 // PROT_READ | PROT_WRITE
  mov x3, #0x1002            // MAP_ANON | MAP_PRIVATE
  mov x4, #-1
  mov x5, #0
  ST_SYSCALL 197, 20
  b.cs Lst_failure
  mov x25, x0
  str x0, [x22, #32]
  tst x0, #4095
  b.ne Lst_failure
  mov x27, #21
  str x27, [x28, #24]
  mov x9, #0
Lst_zero_check:
  ldr x10, [x25, x9]
  cbnz x10, Lst_failure
  add x9, x9, #8
  cmp x9, #8192
  b.lo Lst_zero_check
  // Touch every word on both sides of the native 4 KiB boundary.
  mov x9, #0
  mov x11, #0x5197
Lst_memory_write:
  eor x10, x9, x11
  str x10, [x25, x9]
  add x9, x9, #8
  cmp x9, #8192
  b.lo Lst_memory_write
  mov x27, #22
  str x27, [x28, #24]
  mov x9, #0
Lst_memory_read:
  ldr x10, [x25, x9]
  eor x12, x9, x11
  cmp x10, x12
  b.ne Lst_failure
  add x9, x9, #8
  cmp x9, #8192
  b.lo Lst_memory_read
  mov x0, x25
  mov x1, #8192
  ST_SYSCALL 73, 23
  b.cs Lst_failure
  cbnz x0, Lst_failure
  mov x9, #3
  str x9, [x28, #16]
  ST_MESSAGE Lst_mmap_pass

  // The fixed XNU accepts a NULL attribute descriptor and performs fork1+exec.
  ST_ADDRESS x0, Lst_child_pid
  ST_ADDRESS x1, Lst_path
  mov x2, #0
  ST_ADDRESS x3, Lst_argv
  ST_ADDRESS x4, Lst_envp
  ST_SYSCALL 244, 30
  b.cs Lst_failure
  cbnz x0, Lst_failure
  ST_ADDRESS x9, Lst_child_pid
  ldr w24, [x9]
  str x24, [x28, #32]
  cmp x24, #1
  b.ls Lst_failure
  ST_MESSAGE Lst_spawn_started

  mov x0, x24
  ST_ADDRESS x1, Lst_wait_status
  mov x2, #0
  mov x3, #0
  ST_SYSCALL 7, 31
  b.cs Lst_failure
  str x0, [x28, #40]
  ST_ADDRESS x9, Lst_wait_status
  ldr w10, [x9]
  str x10, [x28, #48]
  cmp x0, x24
  b.ne Lst_failure
  mov w11, #0x2500           // Only the independent child exits 37.
  cmp w10, w11
  b.ne Lst_failure
  mov x9, #7
  str x9, [x28, #16]
  ST_MESSAGE Lst_exec_pass

  mov x0, x24
  ST_ADDRESS x1, Lst_wait_status
  mov x2, #1                 // WNOHANG: already reaped must return ECHILD.
  mov x3, #0
  ST_SYSCALL 7, 32
  b.cc Lst_failure
  cmp x0, #10                // ECHILD
  b.ne Lst_failure
  mov x9, #15
  str x9, [x28, #16]
  ST_MESSAGE Lst_wait_pass
  str xzr, [x28, #24]
  mov x0, #0
  b Lst_return

// No success bit for exec is published at spawn return: wait must prove entry.
Lst_write:
  mov x21, x30
  mov x0, x20
  ST_SYSCALL 4, 40
  b.cs Lst_write_bad
  cmp x0, x2
  b.ne Lst_write_bad
  mov x0, #0
  ret x21
Lst_write_bad:
  mov x0, #-1
  ret x21

Lst_failure:
  // Preserve the first failed stage/SVC. Printing is best effort.
  ST_ADDRESS x1, Lst_fail_line
  add x0, x1, #(Lst_fail_stage-Lst_fail_line)
  ldr x9, [x28, #24]
  bl Lst_hex
  ST_ADDRESS x1, Lst_fail_line
  add x0, x1, #(Lst_fail_svc-Lst_fail_line)
  ldr x9, [x22]
  bl Lst_hex
  ST_ADDRESS x1, Lst_fail_line
  add x0, x1, #(Lst_fail_result-Lst_fail_line)
  ldr x9, [x22, #8]
  bl Lst_hex
  mov x0, x20
  ST_ADDRESS x1, Lst_fail_line
  mov x2, #(Lst_fail_line_end-Lst_fail_line)
  mov x16, #4
  svc #0x80
.global _chiron_selftest_failure_checkpoint
_chiron_selftest_failure_checkpoint:
  mov x0, #-1
Lst_return:
  ldp x19, x20, [sp, #16]
  ldp x21, x22, [sp, #32]
  ldp x23, x24, [sp, #48]
  ldp x25, x26, [sp, #64]
  ldp x27, x28, [sp, #80]
  ldp x29, x30, [sp], #96
  ret

Lst_hex:
  mov x10, #60
  ST_ADDRESS x12, Lst_digits
Lst_hex_loop:
  lsr x11, x9, x10
  and x11, x11, #15
  ldrb w11, [x12, x11]
  strb w11, [x0], #1
  subs x10, x10, #4
  b.ge Lst_hex_loop
  ret

.data
.balign 8
.global _chiron_selftest_results, _chiron_selftest_diagnostics
_chiron_selftest_results:
  .quad 1, 15, 0, 0, 0, 0, 0, 0
_chiron_selftest_diagnostics:
  .quad 0, 0, 0, 0, 0         // last SVC, return, NZCV, file marker, mmap base
Lst_child_pid:
  .word 0
Lst_wait_status:
  .word 0
Lst_argv:
  .quad Lst_path, 0
Lst_envp:
  .quad 0
Lst_buffer:
  .space 64
Lst_path:
  .asciz "/probe.bin"
Lst_expected:
  .ascii "CHIRON-SELFTEST-INDEPENDENT-CHILD-v1\n"
Lst_expected_end:
Lst_file_pass:
  .ascii "CHIRON SELFTEST PASS file read/marker/EOF\n"
Lst_file_pass_end:
Lst_mmap_pass:
  .ascii "CHIRON SELFTEST PASS mmap zero/RW/munmap 8192\n"
Lst_mmap_pass_end:
Lst_spawn_started:
  .ascii "CHIRON SELFTEST START independent child\n"
Lst_spawn_started_end:
Lst_exec_pass:
  .ascii "CHIRON SELFTEST PASS independent exec exit=37\n"
Lst_exec_pass_end:
Lst_wait_pass:
  .ascii "CHIRON SELFTEST PASS wait/reap ECHILD\n"
Lst_wait_pass_end:
Lst_digits:
  .ascii "0123456789abcdef"
Lst_fail_line:
  .ascii "CHIRON SELFTEST FAIL stage=0x"
Lst_fail_stage:
  .ascii "0000000000000000 svc=0x"
Lst_fail_svc:
  .ascii "0000000000000000 result=0x"
Lst_fail_result:
  .ascii "0000000000000000\n"
Lst_fail_line_end:
