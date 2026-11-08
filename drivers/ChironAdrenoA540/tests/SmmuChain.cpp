// SPDX-License-Identifier: BSD-2-Clause
//
// End to end through the real ArmSmmu4K: build stage-1 tables for an A540-shaped range, then encode a
// context from the decoded identity and check the result is what SMMUv2 requires.
//
// This is the first test in this port that links the kit's own code rather than reimplementing it, so
// it is also the first place a misunderstanding of the kit's expectations can show up as something
// other than a review comment.
#include "../ChironAdrenoA540SmmuIdentity.h"
#include "../ChironAdrenoA540SmmuMap.h"
#include "../ChironAdrenoA540SmmuParams.h"

extern "C" {
#include "ArmSmmu4K.h"
#include "ArmSmmu4KContext.h"
}

#include <cstdio>
#include <cstring>
#include <vector>

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

// What a QCOM SMMUv2 for an Adreno reports: stage 1, 4 KiB granule, 48-bit VA, 40-bit PA. The exact
// values are a placeholder until the probe reads them -- what matters is that they are a coherent set,
// and the test asserts the config that comes out rather than the bits that went in.
SmmuIdentity QcomIdentity() {
  SmmuIdentity id{};
  id.id0 = kId0Stage1;
  id.id2 = kId2FourKB | (5u << 8) | (2u << 4);   // VA 48, PA 40
  id.id1 = 0;
  return id;
}

struct TablePool {
  std::vector<uint64_t> words;
  explicit TablePool(size_t pages) : words(pages * 512, 0) {}
  uint64_t phys() const { return 0; }  // the kit takes the address as given
};

}  // namespace

int main() {
  // ---- identity decodes to the widths the kit needs ---------------------
  SmmuConfigWidths widths{};
  CHECK(SmmuConfigFromIdentity(QcomIdentity(), &widths) == SmmuIdentityResult::Ok);
  CHECK(widths.input_bits == 48);
  CHECK(widths.output_bits == 40);

  // ---- one range covering the GPU's register window ---------------------
  // A device range the GPU would be given: 64 KiB of MMIO at the SMMU's own base, readable and
  // writable through the translation. The address is arbitrary; the shape is not.
  const uint64_t iova = 0x05040000ull;
  // Physical must not overlap the table pool, or the kit refuses it as an overlap rather than a
  // mapping -- the pool is in the same address space as what it maps.
  const uint64_t phys = 0x30000000ull;
  ArmSmmu4KRange range{};
  range.iova = iova;
  range.physical = phys;
  range.bytes = 0x10000;
  range.permissions = ArmSmmu4KRead | ArmSmmu4KWrite;
  range.writeback = 0;
  range.shareability = 3;

  // A table pool. The kit wants contiguous storage aligned to 4 KiB; a vector of uint64_t is not
  // guaranteed aligned, so the pool is over-allocated and the address taken from the aligned middle.
  // The range is 0x10000 = 16 pages, and leaf_budget bounds leaves rather than table pages, so it has
  // to be at least 16 before the build can succeed. 32 leaves the table pages room as well.
  const size_t pool_pages = 32;
  // Over-allocate by a full page and step in bytes, not in uint64_t indices: the first aligned
  // address inside the buffer can be up to 4095 bytes in, which is 512 words away, so stepping by
  // words from element 0 walks straight past it unless the base happens to be aligned already.
  std::vector<uint64_t> raw(pool_pages * 512 + 1024, 0);
  uintptr_t base = reinterpret_cast<uintptr_t>(raw.data());
  uintptr_t aligned = (base + 0xFFF) & ~uintptr_t(0xFFF);
  uint64_t *tables = reinterpret_cast<uint64_t *>(aligned);
  CHECK(aligned + pool_pages * 4096 <= base + raw.size() * sizeof(uint64_t));
  CHECK((reinterpret_cast<uintptr_t>(tables) & 0xFFF) == 0);

  /*
   * table_physical must lie below 2^output_bits (ArmSmmu4K.c:26-27), and a real pointer on a 64-bit
   * host does not: it is above 2^40, so passing the buffer's address would be refused as BadRange
   * even though the memory is fine. The kit treats the field as a device physical address, so the test
   * supplies one -- the buffers stand in for a pool that would be physically contiguous on the phone.
   */
  ArmSmmu4KConfig cfg{};
  cfg.input_bits = widths.input_bits;
  cfg.output_bits = widths.output_bits;
  cfg.table_physical = 0x90000000ull;   // below 2^40, 4 KiB aligned
  cfg.leaf_budget = 32;

  ArmSmmu4KResult built{};
  const ArmSmmu4KStatus bst = ArmSmmu4KBuild(&cfg, &range, 1, tables, pool_pages, &built);
  CHECK(bst == ArmSmmu4KSuccess);
  if (bst != ArmSmmu4KSuccess) {
    std::printf("SmmuChain: build failed %d\n", int(bst));
    return 1;
  }

  // The kit reports what it did; these are the properties that matter for a 48-bit input with 4 KiB
  // leaves: the root is where the caller said, the address is 4 KiB granule, and MAIR is its constant.
  CHECK(built.root_physical == cfg.table_physical);
  CHECK(built.mair == 0xff44);
  CHECK(built.upper_region == 0);
  CHECK(built.table_pages > 0);
  CHECK(built.leaf_pages >= 1);
  CHECK(built.leaf_pages <= cfg.leaf_budget);
  // start_level for 48-bit input: 4 - (48 - 12 + 8) / 9 = 4 - 4 = 0
  CHECK(built.start_level == 0);

  // ---- encode a context from that table --------------------------------
  ArmSmmu4KContextConfig cctx{};
  cctx.lower = &cfg;
  cctx.lower_tables = &built;
  cctx.upper = nullptr;
  cctx.upper_tables = nullptr;
  cctx.asid = 7;
  cctx.coherent_walk = 0;
  cctx.stall_on_fault = 1;
  cctx.continue_after_fault = 0;
  cctx.ttbr0_switched = 0;

  const SmmuIdentity id = QcomIdentity();
  cctx.id0 = id.id0;
  cctx.id2 = id.id2;

  ArmSmmu4KContextImage img{};
  const ArmSmmu4KStatus est = ArmSmmu4KEncodeContext(&cctx, &img);
  CHECK(est == ArmSmmu4KSuccess);
  if (est != ArmSmmu4KSuccess) {
    std::printf("SmmuChain: encode failed %d\n", int(est));
    return 1;
  }

  // CBAR: stage 1 translation, stage 2 bypass.
  CHECK(img.cbar == 0x1f300);
  // CBA2R 1 selects AArch64.
  CHECK(img.cba2r == 1);
  // TCR: TG1 and TG0 both 4 KiB.
  CHECK((img.tcr >> 30) == 2);
  // ASID in TTBR0 bits 55:48.
  CHECK(((img.ttbr0 >> 48) & 0xFF) == 7);
  // The root physical address is in the low bits of TTBR0.
  CHECK((img.ttbr0 & 0xFFFFFFFFFFFFull) == (built.root_physical & 0xFFFFFFFFFFFFull));
  // MAIR: non-cacheable at index 0, write-back at index 1.
  CHECK(img.mair0 == 0xff44);
  CHECK(img.mair1 == 0);
  // SCTLR base 0x1067, with stall_on_fault set at bit 7 and continue_after_fault clear at bit 8.
  CHECK((img.sctlr & 0x1067) == 0x1067);
  CHECK((img.sctlr & (1u << 7)) != 0);
  CHECK((img.sctlr & (1u << 8)) == 0);
  // No upper region, so TTBR1 carries the ASID only and TCR.EPD1 is set.
  CHECK(img.ttbr1 == (7ull << 48));
  CHECK((img.tcr & (1u << 23)) != 0);
  // EPD0 must be clear because there is a lower region.
  CHECK((img.tcr & (1u << 7)) == 0);

  // ---- readback validation accepts the image it just produced ----------
  CHECK(ArmSmmu4KValidateContextReadback(&cctx, &img) == ArmSmmu4KSuccess);

  // ---- and rejects an image with a different ASID ---------------------
  {
    ArmSmmu4KContextImage wrong = img;
    wrong.ttbr0 = (wrong.ttbr0 & 0xFFFFFFFFFFFFull) | (9ull << 48);
    CHECK(ArmSmmu4KValidateContextReadback(&cctx, &wrong) == ArmSmmu4KStateMismatch);
  }

  // ---- and rejects a changed SCTLR -----------------------------------
  {
    ArmSmmu4KContextImage wrong = img;
    wrong.sctlr ^= (1u << 1);
    CHECK(ArmSmmu4KValidateContextReadback(&cctx, &wrong) == ArmSmmu4KStateMismatch);
  }

  // ---- the geometry agrees with the address the probe uses -------------
  {
    SmmuGeometry g{SmmuBase, SmmuSize, SmmuPgshiftFromId1(id.id1)};
    CHECK(g.pgshift == 12);
    SmmuBankAddresses a{};
    CHECK(SmmuResolveBank(g, kUserBankIndex, &a) == SmmuMapResult::Ok);
    CHECK(a.sctlr == 0x05048000);
  }

  if (failures == 0) std::printf("SmmuChain: chain complete\n");
  return failures == 0 ? 0 : 1;
}
