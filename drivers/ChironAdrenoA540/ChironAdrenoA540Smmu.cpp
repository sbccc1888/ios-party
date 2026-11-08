// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540Smmu.h"

namespace chiron_adreno {

namespace {

void Write64(const SmmuRegs &regs, uint32_t offset, uint64_t value) {
  regs.write32(regs.ctx, offset, uint32_t(value & 0xFFFFFFFFu));
  regs.write32(regs.ctx, offset + 4, uint32_t(value >> 32));
}

}  // namespace

SmmuResult SmmuProgramContext(const SmmuRegs &regs, const SmmuContextConfig &config,
                              uint32_t bank_count) {
  if (regs.write32 == nullptr || regs.read32 == nullptr) return SmmuResult::NoRegisters;
  if (bank_count == 0 || config.index >= bank_count) return SmmuResult::BadContext;

  const uint32_t cb = SmmuContextBank(config.index);

  // arm_smmu_write_context_bank() order: CBAR/CBA2R live in the global block and belong
  // to whoever set up the stream mapping, so this starts at the context bank itself.
  Write64(regs, cb + SmmuCbTtbr0, config.ttbr0);
  Write64(regs, cb + SmmuCbTtbr1, config.ttbr1);

  regs.write32(regs.ctx, cb + SmmuCbTtbcr, config.ttbcr);
  regs.write32(regs.ctx, cb + SmmuCbTtbcr2, config.ttbcr2);

  regs.write32(regs.ctx, cb + SmmuCbMair0, config.mair0);
  regs.write32(regs.ctx, cb + SmmuCbMair1, config.mair1);

  regs.write32(regs.ctx, cb + SmmuCbContextidr, config.contextidr);

  // The Qualcomm-only write. A stock SMMU-500 would leave ACTLR alone; qcom,smmu-v2 needs
  // the shareability attributes set or the GPU's transactions are ordered too weakly.
  regs.write32(regs.ctx, cb + SmmuCbActlr, SmmuActlrQcomV2);

  // SCTLR last: translation starts only once every register it consults is loaded.
  // CFCFG and HUPCF are mutually exclusive: CFCFG stalls the GPU on a fault, HUPCF lets
  // later transactions through independently of it. The generic driver makes the same
  // substitution when the domain asks for stall-disabled.
  uint32_t sctlr = SmmuSctlrCfie | SmmuSctlrEaeSbop | SmmuSctlrM | SmmuSctlrS1Asidpne;
  if (config.stall_on_fault) {
    sctlr |= SmmuSctlrCfcfg;
  } else {
    sctlr |= SmmuSctlrHupcf;
  }
  regs.write32(regs.ctx, cb + SmmuCbSctlr, sctlr);

  return SmmuResult::Ok;
}

uint32_t SmmuInvalidateContext(const SmmuRegs &regs, uint32_t index, uint32_t attempts) {
  if (regs.write32 == nullptr || regs.read32 == nullptr) return 0;

  // TLBIVMID takes the VMID in bits 0-7 and an ALL bit; writing the context's VMID
  // invalidates just that one. The kernel's kgsl path writes the global TLBIALL at 0x618
  // instead, but that offset only exists on some revisions, so this uses the
  // architectural register.
  regs.write32(regs.ctx, SmmuGr0Tlbivmid, index);

  // Wait for the invalidation to complete. The architected completion reports through
  // TLBSYNC in the context bank; a model that never settles gets bounded out.
  const uint32_t status_offset = SmmuContextBank(index) + SmmuCbFsr;
  uint32_t seen = 0;
  for (uint32_t i = 0; i < attempts; ++i) {
    seen = regs.read32(regs.ctx, status_offset);
    if (seen == 0) return 0;
  }
  return seen;
}

uint32_t SmmuReadFault(const SmmuRegs &regs, uint32_t index, uint64_t *far) {
  if (regs.read32 == nullptr) return 0;
  const uint32_t cb = SmmuContextBank(index);
  const uint32_t fsr = regs.read32(regs.ctx, cb + SmmuCbFsr);
  if (far != nullptr) {
    const uint64_t lo = regs.read32(regs.ctx, cb + SmmuCbFarLo);
    const uint64_t hi = regs.read32(regs.ctx, cb + SmmuCbFarHi);
    *far = (hi << 32) | lo;
  }
  return fsr;
}

}  // namespace chiron_adreno