// SPDX-License-Identifier: BSD-2-Clause
//
// A540 command processor start-up: ring buffer, microcode load, ME release.
//
// Source: drivers/gpu/msm/adreno_a5xx.c, a5xx_rb_start() at line 2483, with the register
// indices from a5xx_reg.h:60-91 and the ring constants from adreno_ringbuffer.h.
//
// The order matters and is short:
//
//   1. CP_RB_RPTR_ADDR (64-bit)   where the hardware writes its read pointer
//   2. CP_RB_CNTL                 ring size and block size, in log2
//   3. CP_RB_BASE (64-bit)        where the ring lives in GPU address space
//   4. microcode load             PFP and PM4 into ME RAM
//   5. CP_ME_CNTL = 0             clear ME_HALT, which starts the micro engine
//   6. ME init and the unsecure-mode switch
//
// As with the GMU, every index here is a word offset.

#ifndef CHIRON_ADRENO_A540_CpStart_H
#define CHIRON_ADRENO_A540_CpStart_H

#include <stdint.h>

namespace chiron_adreno {

// a5xx_reg.h:60-91 word offsets.
enum : uint32_t {
  CpRbBase = 0x800,
  CpRbBaseHi = 0x801,
  CpRbCntl = 0x802,
  CpRbRptrAddrLo = 0x804,
  CpRbRptrAddrHi = 0x805,
  CpRbRptr = 0x806,
  CpRbWptr = 0x807,
  CpMeCntl = 0x832,
};

// adreno_ringbuffer.h:29,35 -- KGSL_RB_SIZE is 32 KiB, so KGSL_RB_DWORDS is 8192.
enum : uint32_t {
  CpRingBytes = 32u * 1024u,
  CpRingDwords = CpRingBytes / 4u,
};

// A5XX_CP_RB_CNTL_DEFAULT from adreno_a5xx.h:115:
//
//   ((ilog2(4) << 8) & 0x1F00) | (ilog2(KGSL_RB_DWORDS >> 1) & 0x3F)
//
// ilog2(4) is 2, so the block-size field is 0x200. ilog2(8192 >> 1) is ilog2(4096) = 12,
// so the size field is 0x0C. Together 0x20C. The comment in the kernel explains why the
// size is halved: the hardware takes the log2 of the ring size in quadwords, not dwords.
enum : uint32_t { CpRbCntlDefault = 0x20C };

// What the caller supplies for one ring buffer.
struct CpRingConfig {
  uint64_t ring_gpu_addr;         // CP_RB_BASE
  uint64_t read_ptr_gpu_addr;     // CP_RB_RPTR_ADDR, where the hardware writes its RPTR
  uint32_t ring_dwords;           // must be a power of two, used to derive CP_RB_CNTL
};

struct CpRegs {
  void (*write32)(void *ctx, uint32_t byte_offset, uint32_t value);
  uint32_t (*read32)(void *ctx, uint32_t byte_offset);
  void *ctx;
};

enum class CpResult {
  Ok,
  NoRegisters,
  BadRing,  // ring size is not a power of two, or zero
};

// Derives CP_RB_CNTL from a ring size. Returns 0 when the size cannot be expressed, which
// the caller should treat as an error rather than programming.
uint32_t CpRingControl(uint32_t ring_dwords);

// Programs RPTR_ADDR, CNTL and BASE, in that order, and nothing else. Loading the
// microcode and releasing the ME are separate steps so a caller can verify each one.
CpResult CpProgramRing(const CpRegs &regs, const CpRingConfig &config);

// Clears ME_HALT. This is the write that actually starts the micro engine, so callers
// should only reach it once the microcode is in ME RAM.
void CpStartMicroEngine(const CpRegs &regs);

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_CpStart_H
