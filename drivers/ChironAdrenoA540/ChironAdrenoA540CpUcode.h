// SPDX-License-Identifier: BSD-2-Clause
//
// A540 CP microcode placement: telling the command processor where PFP and PM4 live.
//
// Source: drivers/gpu/msm/adreno_a5xx.c, a5xx_microcode_load() at line 2238, with register
// indices from a5xx_reg.h:93-96.
//
// This is shorter than the name "microcode load" suggests. Nothing is written into ME RAM
// instruction by instruction -- the two images are placed in GPU-addressable memory and the
// CP is told their addresses:
//
//     CP_PM4_INSTR_BASE_LO/HI   <- PM4 image address
//     CP_PFP_INSTR_BASE_LO/HI   <- PFP image address
//
// and then CP_ME_CNTL = 0 releases the micro engine, which fetches from those addresses. The
// kernel's function does two more things that are not about placement: it resumes the ZAP
// shader through a secure monitor call, and it loads the ZAP image through PIL if that has
// not happened yet. Both are secure-world operations; a driver that is not doing content
// protection does not need them, and this file does not attempt them.
//
// The addresses are GPU virtual addresses, so the SMMU has to be translating and the images
// have to be mapped before these registers mean anything.

#ifndef CHIRON_ADRENO_A540_CPUC_H
#define CHIRON_ADRENO_A540_CPUC_H

#include <stdint.h>

namespace chiron_adreno {

// a5xx_reg.h:93-96, word offsets.
enum : uint32_t {
  CpPfpInstrBaseLo = 0x835,
  CpPfpInstrBaseHi = 0x836,
  CpPm4InstrBaseLo = 0x838,
  CpPm4InstrBaseHi = 0x839,
};

struct CpUcodeRegs {
  void (*write32)(void *ctx, uint32_t word_offset, uint32_t value);
  void *ctx;
};

enum class CpUcodeResult {
  Ok,
  NoRegisters,
  BadAddress,  // an address that cannot be a GPU virtual address on this part
};

// Programs the two base registers. Written as separate low and high words because that is
// how the hardware takes them: there is no 64-bit accessor at these offsets.
//
// Rejects an address that would need more than 49 bits. chiron's GPU virtual window is
// 0x500000000-0x600000000 for client memory and the SMMU is 4 KiB granule, so anything at or
// beyond 2^49 cannot be a mapping this port could create, and quietly programming it would
// mean the CP fetches from an address nothing answers.
CpUcodeResult CpProgramMicrocodeBase (const CpUcodeRegs &regs, uint64_t pfp_gpu_addr,
                                      uint64_t pm4_gpu_addr);

// The widest GPU virtual address this part can be given.
enum : uint64_t { CpMaxGpuAddress = (1ULL << 49) - 1 };

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_CPUC_H
