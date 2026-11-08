// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540CpStart.h"

namespace chiron_adreno {

namespace {

void Write64(const CpRegs &regs, uint32_t lo_word, uint32_t value_lo, uint32_t value_hi) {
  regs.write32(regs.ctx, lo_word * 4u, value_lo);
  regs.write32(regs.ctx, (lo_word + 1u) * 4u, value_hi);
}

bool IsPowerOfTwo(uint32_t v) { return v != 0 && (v & (v - 1)) == 0; }

uint32_t Log2(uint32_t v) {
  uint32_t n = 0;
  while ((v >>= 1) != 0) ++n;
  return n;
}

}  // namespace

uint32_t CpRingControl(uint32_t ring_dwords) {
  if (!IsPowerOfTwo(ring_dwords)) return 0;
  // The hardware wants log2 of the size in quadwords.
  const uint32_t quadwords = ring_dwords >> 1;
  if (quadwords == 0) return 0;
  const uint32_t block = (Log2(4) << 8) & 0x1F00u;
  const uint32_t size = Log2(quadwords) & 0x3Fu;
  return block | size;
}

CpResult CpProgramRing(const CpRegs &regs, const CpRingConfig &config) {
  if (regs.write32 == nullptr || regs.read32 == nullptr) return CpResult::NoRegisters;
  if (CpRingControl(config.ring_dwords) == 0) return CpResult::BadRing;

  // Order from a5xx_rb_start(): RPTR_ADDR, then CNTL, then BASE.
  Write64(regs, CpRbRptrAddrLo, uint32_t(config.read_ptr_gpu_addr & 0xFFFFFFFFu),
          uint32_t(config.read_ptr_gpu_addr >> 32));

  regs.write32(regs.ctx, CpRbCntl * 4u, CpRingControl(config.ring_dwords));

  Write64(regs, CpRbBase, uint32_t(config.ring_gpu_addr & 0xFFFFFFFFu),
          uint32_t(config.ring_gpu_addr >> 32));

  return CpResult::Ok;
}

void CpStartMicroEngine(const CpRegs &regs) {
  if (regs.write32 == nullptr) return;
  // ME_HALT lives in CP_ME_CNTL; writing zero clears it.
  regs.write32(regs.ctx, CpMeCntl * 4u, 0u);
}

}  // namespace chiron_adreno