// SPDX-License-Identifier: BSD-2-Clause
//
// The A540's SMMU, as the phone's own device tree describes it.
//
// Source: chiron.dts, qcom,kgsl-iommu at line 10276 and arm,smmu-kgsl@5040000 at line 6135.
//
// Everything here is read from the device, not inferred. It answers three questions the port had
// been carrying as unknowns: which SMMU generation, which stream IDs, and what the Qualcomm
// implementation registers have to contain before a context bank can be programmed at all.

#ifndef CHIRON_ADRENO_A540_SMMU_PARAMS_H
#define CHIRON_ADRENO_A540_SMMU_PARAMS_H

#include <stdint.h>

namespace chiron_adreno {

// qcom,kgsl-smmu-v2 -- and arm,smmu-kgsl@5040000 says qcom,smmu-v2 as well. The generation is not
// in doubt, which is what makes ArmSmmu4K applicable: it is a SMMUv2 driver.
inline constexpr uint64_t SmmuBase = 0x05040000;
inline constexpr uint64_t SmmuSize = 0x00010000;

// qcom,protect = <0x40000 0x10000>. A region the SMMU is not to map.
inline constexpr uint64_t SmmuProtectBase = 0x00040000;
inline constexpr uint64_t SmmuProtectSize = 0x00010000;

// qcom,micro-mmu-control = <0x6000>, and attach-impl-defs starts with 0x6000 = 0x2378.
inline constexpr uint32_t SmmuMicroMmuControl = 0x6000;

// #iommu-cells = 1, so the second cell of an iommus property is a stream id, not a mask.
//   gfx3d_user   { iommus = <0x1a7 0x00>; qcom,gpu-offset = <0x48000>; }
//   gfx3d_secure { iommus = <0x1a7 0x02>; }
inline constexpr uint32_t SmmuStreamGfx3dUser = 0;
inline constexpr uint32_t SmmuStreamGfx3dSecure = 2;

// qcom,gpu-offset on the user bank.
//
// Do not mistake this for a CPU MMIO offset. kgsl_iommu.h describes it as "Offset of this context
// bank in the GPU register space", and kgsl_iommu_get_reg_ahbaddr() returns
//
//     ctx->gpu_offset + kgsl_iommu_reg_list[reg]
//
// as "the address of register which can be used in type0 packet" -- that is, an address the command
// processor writes into, not one the kernel dereferences. The CPU-side window is ioremap of the
// device tree's reg (0x5040000, 0x10000) only, so 0x48000 is outside it by design.
//
// For a CPU read of the user bank, the address is SmmuBase + this offset: 0x05040000 + 0x48000.
inline constexpr uint64_t SmmuGfx3dUserOffset = 0x48000;

// The CPU-visible address of that context bank, which is what a probe should read.
inline constexpr uint64_t SmmuGfx3dUserCpuBase = 0x05040000ull + 0x48000ull;

// interrupts = <0 0x149 4  0 0x14a 4  0 0x14b 1> with #global-interrupts = 0, so all three are
// per-context-bank and none is global.
inline constexpr uint32_t SmmuIrqUser = 329;
inline constexpr uint32_t SmmuIrqSecure = 330;
inline constexpr uint32_t SmmuIrqThird = 331;
inline constexpr uint32_t SmmuGlobalInterrupts = 0;

// attach-impl-defs, verbatim, in the order the device tree gives it. These are Qualcomm SMMU-500
// implementation registers, and ArmSmmu4KProgram.h says plainly that the caller establishes
// platform requirements before programming a context. Leaving them at whatever XBL left is not a
// documented state, so they are applied rather than assumed.
struct SmmuImplWrite {
  uint32_t offset;
  uint32_t value;
};

inline constexpr SmmuImplWrite kSmmuImplDefs[] = {
  {0x6000, 0x2378},     {0x6060, 0x1055},     {0x6470, 0x110011},   {0x6478, 0x00},
  {0x647c, 0x1000100},  {0x6480, 0x81108110}, {0x6484, 0x81108110}, {0x6488, 0x3e003e0},
  {0x648c, 0x3e003e0},  {0x6490, 0x80008010}, {0x6494, 0x8020},     {0x649c, 0x06},
  {0x6800, 0x06},       {0x6900, 0x3ff},      {0x6924, 0x604},      {0x6928, 0x11000},
  {0x6930, 0x800},      {0x6960, 0x03},       {0x6b64, 0x1a5551},   {0x6b68, 0x2aaa2f82},
};

inline constexpr unsigned kSmmuImplDefCount =
    sizeof (kSmmuImplDefs) / sizeof (kSmmuImplDefs[0]);

// qcom,skip-init is present, so the bootloader has already set the block up. That changes what a
// driver should do first: not reset it, but establish whether what it finds is usable.
inline constexpr bool kSmmuBootloaderInitialised = true;

// qcom,highest-bank-bit = 15, the highest set bit in a 4 KiB-granule bank address.
inline constexpr unsigned kSmmuHighestBankBit = 15;

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_SMMU_PARAMS_H
