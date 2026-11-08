// SPDX-License-Identifier: BSD-2-Clause
//
// Checks the microcode base programming against a5xx_microcode_load() in adreno_a5xx.c.
#include "../ChironAdrenoA540CpUcode.h"

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
  uint32_t word;
  uint32_t value;
};

struct Model {
  std::vector<Write> writes;
  static void Write32(void *ctx, uint32_t word, uint32_t value) {
    static_cast<Model *>(ctx)->writes.push_back(Write{word, value});
  }
  CpUcodeRegs regs() { return CpUcodeRegs{Write32, this}; }
  uint32_t at(uint32_t word) const {
    for (const Write &w : writes)
      if (w.word == word) return w.value;
    return 0;
  }
  int index_of(uint32_t word) const {
    for (size_t i = 0; i < writes.size(); ++i)
      if (writes[i].word == word) return int(i);
    return -1;
  }
};

}  // namespace

int main() {
  // ---- the register indices, from a5xx_reg.h:93-96 --------------------------
  CHECK(CpPfpInstrBaseLo == 0x835);
  CHECK(CpPfpInstrBaseHi == 0x836);
  CHECK(CpPm4InstrBaseLo == 0x838);
  CHECK(CpPm4InstrBaseHi == 0x839);
  // Low and high must be adjacent, or a pair write would land in the wrong place.
  CHECK(CpPfpInstrBaseHi == CpPfpInstrBaseLo + 1);
  CHECK(CpPm4InstrBaseHi == CpPm4InstrBaseLo + 1);

  // ---- a normal programming -------------------------------------------------
  {
    Model m;
    const uint64_t pfp = 0x0000000512340000ULL;
    const uint64_t pm4 = 0x0000000524680000ULL;
    CHECK(CpProgramMicrocodeBase(m.regs(), pfp, pm4) == CpUcodeResult::Ok);

    // Kernel order: PM4 first, then PFP.
    CHECK(m.index_of(CpPm4InstrBaseLo) == 0);
    CHECK(m.index_of(CpPm4InstrBaseHi) == 1);
    CHECK(m.index_of(CpPfpInstrBaseLo) == 2);
    CHECK(m.index_of(CpPfpInstrBaseHi) == 3);

    CHECK(m.at(CpPm4InstrBaseLo) == 0x24680000u);
    CHECK(m.at(CpPm4InstrBaseHi) == 0x00000005u);
    CHECK(m.at(CpPfpInstrBaseLo) == 0x12340000u);
    CHECK(m.at(CpPfpInstrBaseHi) == 0x00000005u);

    // Exactly four writes.
    CHECK(m.writes.size() == 4);
  }

  // ---- an address at the top of the window is fine -------------------------
  {
    Model m;
    CHECK(CpProgramMicrocodeBase(m.regs(), CpMaxGpuAddress, CpMaxGpuAddress) == CpUcodeResult::Ok);
    // 2^49 - 1 splits into 0x0001FFFF high and 0xFFFFFFFF low.
    CHECK(m.at(CpPm4InstrBaseHi) == 0x0001FFFFu);
    CHECK(m.at(CpPm4InstrBaseLo) == 0xFFFFFFFFu);
  }

  // ---- past the window is refused before anything is written ---------------
  {
    Model m;
    CHECK(CpProgramMicrocodeBase(m.regs(), CpMaxGpuAddress + 1, 0) == CpUcodeResult::BadAddress);
    CHECK(m.writes.empty());
    CHECK(CpProgramMicrocodeBase(m.regs(), 0, CpMaxGpuAddress + 1) == CpUcodeResult::BadAddress);
    CHECK(m.writes.empty());
    CHECK(CpProgramMicrocodeBase(m.regs(), 1ULL << 63, 0) == CpUcodeResult::BadAddress);
    CHECK(m.writes.empty());
  }

  // ---- a low address, as an identity-mapped image would be ----------------
  {
    Model m;
    CHECK(CpProgramMicrocodeBase(m.regs(), 0x12345000ULL, 0x12346000ULL) == CpUcodeResult::Ok);
    // Below 4 GiB the high words are zero, which must be written explicitly -- leaving them
    // alone would keep whatever a previous owner put there.
    CHECK(m.at(CpPfpInstrBaseHi) == 0u);
    CHECK(m.at(CpPm4InstrBaseHi) == 0u);
    CHECK(m.writes.size() == 4);
  }

  // ---- missing callback ----------------------------------------------------
  {
    CpUcodeRegs none{nullptr, nullptr};
    CHECK(CpProgramMicrocodeBase(none, 0x1000, 0x2000) == CpUcodeResult::NoRegisters);
  }

  if (failures == 0) std::printf("CpUcode: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
