// SPDX-License-Identifier: BSD-2-Clause
//
// Pins the SMMU parameters to the device tree. Every constant here has a line in chiron.dts; if one
// drifts, this fails rather than silently programming a different SMMU.
#include "../ChironAdrenoA540SmmuParams.h"

#include <cstdio>
#include <cstring>

using namespace chiron_adreno;

static int failures = 0;

#define CHECK(cond)                                                \
  do {                                                             \
    if (!(cond)) {                                                 \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);  \
      ++failures;                                                  \
    }                                                              \
  } while (0)

namespace {

const SmmuImplWrite *Find(uint32_t offset) {
  for (unsigned i = 0; i < kSmmuImplDefCount; ++i)
    if (kSmmuImplDefs[i].offset == offset) return &kSmmuImplDefs[i];
  return nullptr;
}

}  // namespace

int main() {
  // ---- the block ------------------------------------------------------------
  CHECK(SmmuBase == 0x05040000);
  CHECK(SmmuSize == 0x10000);
  CHECK(SmmuBase + SmmuSize == 0x05050000);

  // The protect region must sit outside the register window, or the SMMU would be told not to map
  // the file it is programmed through.
  CHECK(!(SmmuProtectBase >= SmmuBase && SmmuProtectBase < SmmuBase + SmmuSize));

  // ---- streams --------------------------------------------------------------
  // #iommu-cells = 1 makes these stream ids, and they must not collide.
  CHECK(SmmuStreamGfx3dUser == 0);
  CHECK(SmmuStreamGfx3dSecure == 2);
  CHECK(SmmuStreamGfx3dUser != SmmuStreamGfx3dSecure);

  // The secure stream id must actually be the GPIO bit, not 1: the device tree says 0x02.
  CHECK((SmmuStreamGfx3dSecure & 1u) == 0);

  // ---- the user context window ---------------------------------------------
  CHECK(SmmuGfx3dUserOffset == 0x48000);

  // Deliberately outside the 64 KiB register window. kgsl_iommu.h calls gpu_offset the context
  // bank's offset "in the GPU register space" and uses it to build type0 packet addresses, not CPU
  // MMIO offsets -- the kernel only ioremaps 0x5040000..0x5050000 from the device tree's reg.
  CHECK(SmmuGfx3dUserOffset >= SmmuSize);
  CHECK((SmmuGfx3dUserOffset & 0xFFF) == 0);
  CHECK(SmmuGfx3dUserOffset == 0x48u * 0x1000u);

  // What a CPU read actually needs: base plus offset, still 4 KiB aligned for the register block.
  CHECK(SmmuGfx3dUserCpuBase == 0x05088000);
  CHECK((SmmuGfx3dUserCpuBase & 0xFFF) == 0);
  // SCTLR sits at +0 and TTBR0 at +0x20 within that bank, which is the arithmetic the probe used.
  CHECK(SmmuGfx3dUserCpuBase + 0x00 == 0x05088000);
  CHECK(SmmuGfx3dUserCpuBase + 0x20 == 0x05088020);

  // ---- interrupts ----------------------------------------------------------
  CHECK(SmmuGlobalInterrupts == 0);
  CHECK(SmmuIrqUser == 329);
  CHECK(SmmuIrqSecure == 330);
  CHECK(SmmuIrqThird == 331);
  // With no global interrupts, every one of them is a context-bank interrupt.
  CHECK(SmmuIrqUser < SmmuIrqSecure && SmmuIrqSecure < SmmuIrqThird);

  // ---- the implementation registers ---------------------------------------
  // 20 pairs, exactly as the device tree lists them.
  CHECK(kSmmuImplDefCount == 20);

  // The first is micro-mmu-control, and its value has to agree with the separate property.
  CHECK(Find(SmmuMicroMmuControl) != nullptr);
  CHECK(Find(SmmuMicroMmuControl)->value == 0x2378);

  // Spot-check four more against the device tree, from different parts of the list.
  CHECK(Find(0x6060) && Find(0x6060)->value == 0x1055);
  CHECK(Find(0x6480) && Find(0x6480)->value == 0x81108110);
  CHECK(Find(0x6930) && Find(0x6930)->value == 0x800);
  CHECK(Find(0x6b68) && Find(0x6b68)->value == 0x2aaa2f82);

  // No offset appears twice. A duplicated pair would mean one value silently wins.
  for (unsigned i = 0; i < kSmmuImplDefCount; ++i)
    for (unsigned j = i + 1; j < kSmmuImplDefCount; ++j)
      CHECK(kSmmuImplDefs[i].offset != kSmmuImplDefs[j].offset);

  // Offsets ascend, so the list can be walked in order without a sort.
  for (unsigned i = 1; i < kSmmuImplDefCount; ++i)
    CHECK(kSmmuImplDefs[i - 1].offset < kSmmuImplDefs[i].offset);

  // Every offset is inside the 64 KiB block.
  for (unsigned i = 0; i < kSmmuImplDefCount; ++i)
    CHECK(kSmmuImplDefs[i].offset < SmmuSize);

  // ---- the two facts that shape the driver's first move --------------------
  CHECK(kSmmuBootloaderInitialised);
  CHECK(kSmmuHighestBankBit == 15);

  if (failures == 0) std::printf("SmmuParams: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
