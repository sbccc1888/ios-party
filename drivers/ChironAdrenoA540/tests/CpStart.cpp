// SPDX-License-Identifier: BSD-2-Clause
//
// Checks the CP ring programming against a5xx_rb_start() in adreno_a5xx.c.
#include "../ChironAdrenoA540CpStart.h"

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
  static void Write32(void *ctx, uint32_t offset, uint32_t value) {
    static_cast<Model *>(ctx)->writes.push_back(Write{offset, value});
  }
  static uint32_t Read32(void *ctx, uint32_t offset) {
    (void)ctx;
    (void)offset;
    return 0;
  }
  CpRegs regs() { return CpRegs{Write32, Read32, this}; }
  int index_of(uint32_t word) const {
    for (size_t i = 0; i < writes.size(); ++i)
      if (writes[i].offset == word * 4u) return int(i);
    return -1;
  }
  uint32_t value_at(uint32_t word) const {
    for (const Write &w : writes)
      if (w.offset == word * 4u) return w.value;
    return 0;
  }
};

}  // namespace

int main() {
  // ---- the default control word for a 32 KiB ring --------------------------
  // KGSL_RB_SIZE is 32 KiB, so 8192 dwords, halved to 4096 quadwords, log2 = 12.
  // ilog2(4) = 2 shifted to 0x200. 0x200 | 0x0C = 0x20C.
  CHECK(CpRingDwords == 8192u);
  CHECK(CpRingBytes == 32768u);
  CHECK(CpRingControl(CpRingDwords) == 0x20Cu);
  CHECK(CpRbCntlDefault == 0x20Cu);
  CHECK(CpRingControl(8192) == CpRbCntlDefault);

  // A few other sizes, to show the size field tracks the ring rather than being fixed.
  CHECK(CpRingControl(1024) == (0x200u | 9u));   // 1024 dwords = 512 qwords, log2 = 9
  CHECK(CpRingControl(2048) == (0x200u | 10u));
  CHECK(CpRingControl(4096) == (0x200u | 11u));
  CHECK(CpRingControl(16384) == (0x200u | 13u));
  // The block field is always 0x200, contributed by ilog2(4).
  for (uint32_t n : {1024u, 2048u, 4096u, 8192u, 16384u})
    CHECK((CpRingControl(n) & 0x1F00u) == 0x200u);

  // ---- sizes that cannot be programmed -------------------------------------
  CHECK(CpRingControl(0) == 0u);
  CHECK(CpRingControl(3) == 0u);       // not a power of two
  CHECK(CpRingControl(8191) == 0u);
  CHECK(CpRingControl(1) == 0u);       // power of two but quadwords would be zero

  // ---- a normal ring programming -------------------------------------------
  {
    Model m;
    CpRingConfig cfg{};
    cfg.ring_gpu_addr = (1ULL << 32) | 0x23450000ULL;
    cfg.read_ptr_gpu_addr = (1ULL << 32) | 0x23460000ULL;
    cfg.ring_dwords = 8192;

    CHECK(CpProgramRing(m.regs(), cfg) == CpResult::Ok);

    // RPTR_ADDR comes first, as two halves.
    CHECK(m.index_of(CpRbRptrAddrLo) == 0);
    CHECK(m.index_of(CpRbRptrAddrHi) == 1);
    CHECK(m.value_at(CpRbRptrAddrLo) == 0x23460000u);
    CHECK(m.value_at(CpRbRptrAddrHi) == 0x00000001u);

    // Then CNTL, with the derived value.
    CHECK(m.index_of(CpRbCntl) == 2);
    CHECK(m.value_at(CpRbCntl) == 0x20Cu);

    // Then BASE, low half before high half.
    CHECK(m.index_of(CpRbBase) == 3);
    CHECK(m.index_of(CpRbBaseHi) == 4);
    CHECK(m.value_at(CpRbBase) == 0x23450000u);
    CHECK(m.value_at(CpRbBaseHi) == 0x00000001u);

    // Exactly five writes: nothing else was touched.
    CHECK(m.writes.size() == 5);
  }

  // ---- bad rings are refused before any write ------------------------------
  {
    Model m;
    CpRingConfig cfg{};
    cfg.ring_gpu_addr = 0x1000;
    cfg.read_ptr_gpu_addr = 0x2000;
    cfg.ring_dwords = 8191;  // not a power of two
    CHECK(CpProgramRing(m.regs(), cfg) == CpResult::BadRing);
    CHECK(m.writes.empty());

    cfg.ring_dwords = 0;
    CHECK(CpProgramRing(m.regs(), cfg) == CpResult::BadRing);
    CHECK(m.writes.empty());
  }

  // ---- missing callbacks ---------------------------------------------------
  {
    CpRingConfig cfg{};
    cfg.ring_dwords = 8192;
    CpRegs none{nullptr, nullptr, nullptr};
    CHECK(CpProgramRing(none, cfg) == CpResult::NoRegisters);
  }

  // ---- releasing the ME writes zero to CP_ME_CNTL --------------------------
  {
    Model m;
    CpStartMicroEngine(m.regs());
    CHECK(m.writes.size() == 1);
    CHECK(m.writes[0].offset == CpMeCntl * 4u);
    CHECK(m.writes[0].value == 0u);
    // A null callback must not crash.
    CpRegs none{nullptr, nullptr, nullptr};
    CpStartMicroEngine(none);
  }

  if (failures == 0) std::printf("CpStart: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
