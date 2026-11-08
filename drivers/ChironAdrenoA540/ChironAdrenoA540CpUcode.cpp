// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540CpUcode.h"

namespace chiron_adreno {

CpUcodeResult CpProgramMicrocodeBase (const CpUcodeRegs &regs, uint64_t pfp_gpu_addr,
                                      uint64_t pm4_gpu_addr)
{
  if (regs.write32 == nullptr) {
    return CpUcodeResult::NoRegisters;
  }

  /*
   * Check before writing anything. The kernel does not, because its callers only ever pass
   * addresses that came out of its own allocator; a driver assembling its own mappings
   * should not inherit that assumption.
   */
  if ((pfp_gpu_addr > CpMaxGpuAddress) || (pm4_gpu_addr > CpMaxGpuAddress)) {
    return CpUcodeResult::BadAddress;
  }

  // Order follows the kernel: PM4 first, then PFP.
  regs.write32 (regs.ctx, CpPm4InstrBaseLo, uint32_t (pm4_gpu_addr & 0xFFFFFFFFu));
  regs.write32 (regs.ctx, CpPm4InstrBaseHi, uint32_t (pm4_gpu_addr >> 32));
  regs.write32 (regs.ctx, CpPfpInstrBaseLo, uint32_t (pfp_gpu_addr & 0xFFFFFFFFu));
  regs.write32 (regs.ctx, CpPfpInstrBaseHi, uint32_t (pfp_gpu_addr >> 32));

  return CpUcodeResult::Ok;
}

}  // namespace chiron_adreno