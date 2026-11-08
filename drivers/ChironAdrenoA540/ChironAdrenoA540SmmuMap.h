// SPDX-License-Identifier: BSD-2-Clause
//
// Mapping the A540's SMMU into ArmSmmu4K's configuration types.
//
// ArmSmmu4K is a SMMUv2 driver and chiron's GPU SMMU is SMMUv2, so this layer is arithmetic and
// validation rather than new machinery. What it has to get right is the geometry: where the context
// banks live, and how far apart they are.
//
// From drivers/iommu/arm-smmu.c, which is the generic SMMUv2 driver and the authority for these
// offsets:
//
//   #define ARM_SMMU_CB_BASE(smmu)  ((smmu)->base + ((smmu)->size >> 1))
//   #define ARM_SMMU_CB(smmu, n)    ((n) * (1 << (smmu)->pgshift))
//   #define ARM_SMMU_CB_SCTLR       0x0
//   #define ARM_SMMU_CB_ACTLR       0x4
//   #define ARM_SMMU_CB_TTBR0       0x20
//   #define ARM_SMMU_CB_TTBCR       0x30
//
//   smmu->pgshift = (id & ID1_PAGESIZE) ? 16 : 12;
//
// The important part is that pgshift is read from the block, not assumed. With pgshift 12 the bank
// stride is 0x1000 and CB_BASE for a 64 KiB window is 0x05048000, so bank 0 lands exactly where the
// probe has been reading. With pgshift 16 the stride is 0x10000 and bank 0 is still at CB_BASE but
// bank 1 moves from 0x05049000 to 0x05058000 -- outside a 64 KiB mapping. A driver that hard-coded
// 0x1000 would address the wrong registers for every bank past zero on such a part.
//
// One thing qcom,gpu-offset is NOT: CB_BASE relative to the block. chiron has gpu-offset 0x48000 while
// CB_BASE - base is 0x8000 for a 64 KiB window. They agree only in the low bits that happen to matter
// for a type0 packet address, and treating one as the other moves every read by 0x40000. The probe
// reads 0x05048000 because that is CB_BASE, not because gpu_offset said 0x48000.

#ifndef CHIRON_ADRENO_A540_SMMU_MAP_H
#define CHIRON_ADRENO_A540_SMMU_MAP_H

#include <stdint.h>

#include "ChironAdrenoA540SmmuParams.h"

namespace chiron_adreno {

// Byte offsets within a context bank, arm-smmu.c:205-211.
inline constexpr uint32_t kCbSctlr = 0x00;
inline constexpr uint32_t kCbActlr = 0x04;
inline constexpr uint32_t kCbTtbr0 = 0x20;
inline constexpr uint32_t kCbTtbcr = 0x30;

// ID1_PAGESIZE, the bit that selects a 64 KiB bank stride.
inline constexpr uint32_t kId1PageSize = 1u << 31;

struct SmmuGeometry {
  uint64_t base;      // SmmuBase from the device tree
  uint64_t size;      // SmmuSize
  uint32_t pgshift;   // 12 or 16, derived from ID1
};

// Context bank base for a 64 KiB window, the flexRAM half boundary:
//   base + (size >> 1)
inline constexpr uint64_t SmmuContextBankBase (uint64_t base, uint64_t size)
{
  return base + (size >> 1);
}

// Which pgshift an ID1 value implies. Anything other than the 64 KiB bit set means 4 KiB.
inline constexpr uint32_t SmmuPgshiftFromId1 (uint32_t id1)
{
  return (id1 & kId1PageSize) ? 16u : 12u;
}

inline constexpr uint32_t SmmuBankStride (uint32_t pgshift)
{
  return 1u << pgshift;
}

enum class SmmuMapResult {
  Ok,
  BadGeometry,   // pgshift outside {12, 16}, or a band that cannot hold one bank
  BankOutOfRange // the requested bank's registers do not fit in the mapped window
};

struct SmmuBankAddresses {
  uint64_t sctlr;
  uint64_t actlr;
  uint64_t ttbr0;
  uint64_t ttbcr;
};

// Resolve one context bank's register addresses.
//
// The range check is not ceremony. chiron maps 64 KiB and the device tree gives two banks, 0 for
// gfx3d_user and (by stream id 2) the secure one; a caller that asked for bank 8 with pgshift 16
// would compute an address the CPU cannot read, and the failure would look like a dead SMMU rather
// than an arithmetic mistake.
SmmuMapResult SmmuResolveBank (const SmmuGeometry &geom, unsigned bank,
                               SmmuBankAddresses *out);

// The stream id a bank serves, from the device tree's iommus cells.
inline constexpr uint32_t kStreamGfx3dUser = SmmuStreamGfx3dUser;
inline constexpr uint32_t kStreamGfx3dSecure = SmmuStreamGfx3dSecure;

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_SMMU_MAP_H