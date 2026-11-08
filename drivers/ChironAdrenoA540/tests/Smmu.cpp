// SPDX-License-Identifier: BSD-2-Clause
//
// Programs a context bank against a recording model and checks the result against what
// arm_smmu_write_context_bank() does for a stage-1 domain on qcom,smmu-v2.
#include "../ChironAdrenoA540Smmu.h"

#include <cstdio>
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

struct Write {
  uint32_t offset;
  uint32_t value;
};

struct Model {
  std::vector<Write> writes;
  std::vector<uint32_t> regs = std::vector<uint32_t>(0x10000 / 4, 0);
  uint32_t status_after = 0;  // reads needed before FSR returns non-zero; 0 = always zero

  static void Write32(void *ctx, uint32_t offset, uint32_t value) {
    auto *m = static_cast<Model *>(ctx);
    m->writes.push_back(Write{offset, value});
    if (offset / 4 < m->regs.size()) m->regs[offset / 4] = value;
  }
  static uint32_t Read32(void *ctx, uint32_t offset) {
    auto *m = static_cast<Model *>(ctx);
    const size_t i = offset / 4;
    return i < m->regs.size() ? m->regs[i] : 0u;
  }

  SmmuRegs regs_view() { return SmmuRegs{Write32, Read32, this}; }
  const Write *find(uint32_t offset) const {
    for (const Write &w : writes)
      if (w.offset == offset) return &w;
    return nullptr;
  }
  int index_of(uint32_t offset) const {
    for (size_t i = 0; i < writes.size(); ++i)
      if (writes[i].offset == offset) return int(i);
    return -1;
  }
};

SmmuContextConfig DefaultConfig(uint32_t index) {
  SmmuContextConfig c{};
  c.index = index;
  c.ttbr0 = 0x00000000ABCD0000ULL | (7ULL << 48);  // page table base + ASID 7
  c.ttbr1 = 0;
  c.ttbcr = 0x00000010;   // T0SZ 16
  c.ttbcr2 = 0;
  c.mair0 = 0xFF4400;
  c.mair1 = 0;
  c.contextidr = 1;
  c.stall_on_fault = true;
  return c;
}

}  // namespace

int main() {
  // ---- context bank placement ----------------------------------------------
  CHECK(SmmuCb0Offset == 0x8000);
  CHECK(SmmuContextBank(0) == 0x8000);
  CHECK(SmmuContextBank(1) == 0x9000);
  CHECK(SmmuContextBank(2) == 0xA000);

  // ---- the Qualcomm ACTLR value -------------------------------------------
  // ACTLR_QCOM_{NSH,ISH,OSH} at shifts 30, 29 and 28, all set bit 1.
  CHECK(SmmuActlrQcomV2 == 0x70000000u);
  CHECK((SmmuActlrQcomV2 >> 30) & 1u);
  CHECK((SmmuActlrQcomV2 >> 29) & 1u);
  CHECK((SmmuActlrQcomV2 >> 28) & 1u);

  // ---- a normal stage-1 programming ---------------------------------------
  {
    Model m;
    const SmmuContextConfig cfg = DefaultConfig(0);
    CHECK(SmmuProgramContext(m.regs_view(), cfg, 8) == SmmuResult::Ok);

    const uint32_t cb = SmmuContextBank(0);

    // TTBR0 is written as two halves, low at +0 then high at +4.
    const Write *ttbr_lo = m.find(cb + SmmuCbTtbr0);
    const Write *ttbr_hi = m.find(cb + SmmuCbTtbr0 + 4);
    CHECK(ttbr_lo != nullptr);
    CHECK(ttbr_hi != nullptr);
    CHECK(ttbr_lo->value == 0xABCD0000u);
    // ASID 7 sits in TTBR0 bits 55:48, so after the shift by 32 it appears at bits 23:16
    // of the high word -- 0x00070000, not 0x7. Writing it as the latter would put the ASID
    // in bits 39:32, which is the wrong field.
    CHECK(ttbr_hi->value == 0x00070000u);

    // TTBCR, MAIR, CONTEXTIDR and the Qualcomm ACTLR are all present.
    CHECK(m.find(cb + SmmuCbTtbcr) != nullptr);
    CHECK(m.find(cb + SmmuCbTtbcr)->value == cfg.ttbcr);
    CHECK(m.find(cb + SmmuCbMair0)->value == cfg.mair0);
    CHECK(m.find(cb + SmmuCbContextidr)->value == cfg.contextidr);
    CHECK(m.find(cb + SmmuCbActlr) != nullptr);
    CHECK(m.find(cb + SmmuCbActlr)->value == SmmuActlrQcomV2);

    // SCTLR is written last, after every register it consults.
    const int sctlr_at = m.index_of(cb + SmmuCbSctlr);
    CHECK(sctlr_at >= 0);
    CHECK(size_t(sctlr_at) == m.writes.size() - 1);
    CHECK(sctlr_at > m.index_of(cb + SmmuCbTtbr0));
    CHECK(sctlr_at > m.index_of(cb + SmmuCbTtbcr));
    CHECK(sctlr_at > m.index_of(cb + SmmuCbMair0));
    CHECK(sctlr_at > m.index_of(cb + SmmuCbActlr));

    // And SCTLR carries M, CFIE, EAE_SBOP, S1_ASIDPNE and CFCFG, but not HUPCF.
    const uint32_t sctlr = m.find(cb + SmmuCbSctlr)->value;
    CHECK(sctlr & SmmuSctlrM);
    CHECK(sctlr & SmmuSctlrCfie);
    CHECK(sctlr & SmmuSctlrEaeSbop);
    CHECK(sctlr & SmmuSctlrS1Asidpne);
    CHECK(sctlr & SmmuSctlrCfcfg);
    CHECK((sctlr & SmmuSctlrHupcf) == 0);
  }

  // ---- stall disabled swaps CFCFG for HUPCF -------------------------------
  {
    Model m;
    SmmuContextConfig cfg = DefaultConfig(0);
    cfg.stall_on_fault = false;
    CHECK(SmmuProgramContext(m.regs_view(), cfg, 8) == SmmuResult::Ok);
    const uint32_t sctlr = m.find(SmmuContextBank(0) + SmmuCbSctlr)->value;
    CHECK((sctlr & SmmuSctlrCfcfg) == 0);
    CHECK(sctlr & SmmuSctlrHupcf);
    // M stays on either way, otherwise nothing would translate.
    CHECK(sctlr & SmmuSctlrM);
  }

  // ---- a different context bank lands at a different offset ---------------
  {
    Model m;
    SmmuContextConfig cfg = DefaultConfig(2);
    CHECK(SmmuProgramContext(m.regs_view(), cfg, 8) == SmmuResult::Ok);
    CHECK(m.find(SmmuContextBank(2) + SmmuCbSctlr) != nullptr);
    CHECK(m.find(SmmuContextBank(0) + SmmuCbSctlr) == nullptr);
  }

  // ---- out-of-range contexts and missing callbacks ------------------------
  {
    Model m;
    CHECK(SmmuProgramContext(m.regs_view(), DefaultConfig(8), 8) == SmmuResult::BadContext);
    CHECK(SmmuProgramContext(m.regs_view(), DefaultConfig(0), 0) == SmmuResult::BadContext);
    CHECK(m.writes.empty());

    SmmuRegs none{nullptr, nullptr, nullptr};
    CHECK(SmmuProgramContext(none, DefaultConfig(0), 8) == SmmuResult::NoRegisters);
  }

  // ---- fault reading ------------------------------------------------------
  {
    Model m;
    // Programme FAR by hand, then read it back through the accessor.
    m.regs[(SmmuContextBank(1) + SmmuCbFarLo) / 4] = 0x11223344u;
    m.regs[(SmmuContextBank(1) + SmmuCbFarHi) / 4] = 0x00000005u;
    m.regs[(SmmuContextBank(1) + SmmuCbFsr) / 4] = 0x0F;
    uint64_t far = 0;
    const uint32_t fsr = SmmuReadFault(m.regs_view(), 1, &far);
    CHECK(fsr == 0x0Fu);
    CHECK(far == 0x0000000511223344ULL);
    // A null FAR pointer must be tolerated.
    CHECK(SmmuReadFault(m.regs_view(), 1, nullptr) == 0x0Fu);
  }

  if (failures == 0) std::printf("Smmu: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
