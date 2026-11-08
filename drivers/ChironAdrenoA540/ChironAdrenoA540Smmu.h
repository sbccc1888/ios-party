// SPDX-License-Identifier: BSD-2-Clause
//
// Qualcomm SMMU v2 (qcom,smmu-v2) context-bank programming for the A540 GPU.
//
// Sources:
//   drivers/iommu/arm-smmu.c        register offsets, arm_smmu_write_context_bank()
//   drivers/gpu/msm/kgsl_iommu.c    kgsl_iommu_reg_list[], context layout
//   drivers/gpu/msm/kgsl_iommu.h    CB0 offset, CB stride
//
// chiron's device tree puts the GPU SMMU at 0x05040000, size 0x10000.
//
// The two drivers agree on every register offset, which is the reassuring part:
//
//   register      kgsl_iommu.c   arm-smmu.c
//   SCTLR                0x00         0x00
//   TTBR0                0x20         0x20
//   CONTEXTIDR           0x34         0x34
//   FSR                  0x58         0x58
//   FAR                  0x60         0x60
//
// So qcom,smmu-v2 is a standard Arm SMMU-500 register file plus one Qualcomm addition: an
// ACTLR write the generic driver performs only when the model is QCOM_V2. That single
// write is what this file has to preserve.

#ifndef CHIRON_ADRENO_A540_SMMU_H
#define CHIRON_ADRENO_A540_SMMU_H

#include <stdint.h>

namespace chiron_adreno {

// Context bank placement, kgsl_iommu.h:183-185.
enum : uint32_t {
  SmmuCb0Offset = 0x8000,
  SmmuCbShift = 12,  // each context bank occupies 0x1000
};

// Per-context-bank registers, arm-smmu.c:205-220. Relative to the bank's own base.
enum : uint32_t {
  SmmuCbSctlr = 0x00,
  SmmuCbActlr = 0x04,
  SmmuCbTtbcr2 = 0x10,
  SmmuCbTtbr0 = 0x20,
  SmmuCbTtbr1 = 0x28,
  SmmuCbTtbcr = 0x30,
  SmmuCbContextidr = 0x34,
  SmmuCbMair0 = 0x38,
  SmmuCbMair1 = 0x3C,
  SmmuCbFsr = 0x58,
  SmmuCbFarLo = 0x60,
  SmmuCbFarHi = 0x64,
};

// Global registers, relative to the SMMU base.
enum : uint32_t { SmmuGr0Scr0 = 0x00, SmmuGr0Tlbivmid = 0x64 };

// SCTLR bits, arm-smmu.c:235-244.
enum : uint32_t {
  SmmuSctlrM = 1u << 0,
  SmmuSctlrCfie = 1u << 6,
  SmmuSctlrCfcfg = 1u << 7,
  SmmuSctlrHupcf = 1u << 8,
  SmmuSctlrS1Asidpne = 1u << 12,
};

// SCTLR_EAE_SBOP is SCTLR_AFE | SCTLR_TRE. The values are in arm-smmu.c next to the
// others; AFE is bit 29 and TRE is bit 26.
enum : uint32_t { SmmuSctlrEaeSbop = (1u << 29) | (1u << 26) };

// ACTLR for QCOM_V2: ISH, OSH and NSH each set bit 1 at shifts 29, 28 and 30, so the
// register reads 0x70000000. arm-smmu.c:267-274 and 1665-1670.
enum : uint32_t { SmmuActlrQcomV2 = (1u << 30) | (1u << 29) | (1u << 28) };

// Byte offset of a context bank from the SMMU base.
inline uint32_t SmmuContextBank(uint32_t index) {
  return SmmuCb0Offset + (index << SmmuCbShift);
}

// What a context bank needs before it can translate.
struct SmmuContextConfig {
  uint32_t index;       // context bank number
  uint64_t ttbr0;       // page table base, plus the ASID in the top 16 bits
  uint64_t ttbr1;       // usually 0 on a stage-1 GPU domain
  uint32_t ttbcr;       // translation control: T0SZ, IRGN0, ORGN0, EAE, ASID size
  uint32_t ttbcr2;      // upper half of TTBCR, SMMU-500 only
  uint32_t mair0;       // memory attributes
  uint32_t mair1;
  uint32_t contextidr;  // address space id visible to the GPU
  bool stall_on_fault;  // true = CFCFG, false = terminate and set HUPCF
};

// Register accessors. Offsets are bytes from the SMMU base; 64-bit registers are handled
// as two 32-bit halves so the caller does not have to expose 64-bit MMIO.
struct SmmuRegs {
  void (*write32)(void *ctx, uint32_t offset, uint32_t value);
  uint32_t (*read32)(void *ctx, uint32_t offset);
  void *ctx;
};

enum class SmmuResult {
  Ok,
  NoRegisters,
  BadContext,  // context bank index is outside the bank range
};

// Programs one context bank the way arm_smmu_write_context_bank() does for a stage-1
// domain: TTBR0/TTBR1, TTBCR/TTBCR2, MAIR, then the Qualcomm ACTLR, then SCTLR last so
// translation only starts once everything it reads is in place.
//
// SCTLR is built as CFCFG | CFIE | CFRE | EAE_SBOP, plus M and S1_ASIDPNE. When the
// caller does not want stall-on-fault, CFCFG is dropped and HUPCF is set instead, which
// is the same substitution the generic driver makes for DOMAIN_ATTR_CB_STALL_DISABLE.
SmmuResult SmmuProgramContext(const SmmuRegs &regs, const SmmuContextConfig &config,
                              uint32_t bank_count);

// Invalidates one context bank's TLB entries and waits for the invalidation to be
// acknowledged. Returns the TLBSTATUS value it observed, or 0 if it never settled.
uint32_t SmmuInvalidateContext(const SmmuRegs &regs, uint32_t index, uint32_t attempts);

// Reads the fault status and fault address of a context bank. Returns the FSR value and
// writes the 64-bit FAR to *far when it is non-null.
uint32_t SmmuReadFault(const SmmuRegs &regs, uint32_t index, uint64_t *far);

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_SMMU_H
