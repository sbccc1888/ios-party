// SPDX-License-Identifier: BSD-2-Clause
//
// Checks the context-bank arithmetic against arm-smmu.c. The two pgshift cases matter: chiron reads
// 12, but a driver that only works for 12 is a driver that silently mis-addresses every bank past 0
// on a part that reports 16.
#include "../ChironAdrenoA540SmmuMap.h"

#include <cstdio>

using namespace chiron_adreno;

static int failures = 0;

#define CHECK(cond)                                                \
  do {                                                             \
    if (!(cond)) {                                                 \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);  \
      ++failures;                                                  \
    }                                                              \
  } while (0)

int main() {
  // ---- CB_BASE is the half-window boundary -------------------------------
  // arm-smmu.c: base + (size >> 1)
  CHECK(SmmuContextBankBase(0x05040000, 0x10000) == 0x05048000);
  CHECK(SmmuContextBankBase(0x05040000, 0x20000) == 0x05050000);
  CHECK(SmmuContextBankBase(0x1000, 0x1000) == 0x1800);

  // And it is not the same number as qcom,gpu-offset, which is what a type0 packet carries. chiron
  // has gpu-offset 0x48000 while CB_BASE - base is 0x8000; conflating them would be a 0x40000 error.
  CHECK(SmmuContextBankBase(SmmuBase, SmmuSize) == SmmuBase + 0x8000);
  CHECK(SmmuContextBankBase(SmmuBase, SmmuSize) != SmmuBase + SmmuGfx3dUserOffset);

  // ---- pgshift from ID1 ------------------------------------------------
  CHECK(SmmuPgshiftFromId1(0x00000000) == 12);
  CHECK(SmmuPgshiftFromId1(0x80000000) == 16);
  // Every other bit is irrelevant to the page-size decision.
  CHECK(SmmuPgshiftFromId1(0x7FFFFFFF) == 12);
  CHECK(SmmuPgshiftFromId1(0xFFFFFFFF) == 16);
  CHECK(SmmuBankStride(12) == 0x1000);
  CHECK(SmmuBankStride(16) == 0x10000);

  // ---- the real configuration: pgshift 12 -------------------------------
  {
    SmmuGeometry g{SmmuBase, SmmuSize, 12};
    SmmuBankAddresses a{};
    CHECK(SmmuResolveBank(g, 0, &a) == SmmuMapResult::Ok);

    // Bank 0 is exactly what the probe reads.
    CHECK(a.sctlr == 0x05048000);
    CHECK(a.actlr == 0x05048004);
    CHECK(a.ttbr0 == 0x05048020);
    CHECK(a.ttbcr == 0x05048030);

    // Bank 1 is one 4 KiB step further, still inside the window.
    SmmuBankAddresses b{};
    CHECK(SmmuResolveBank(g, 1, &b) == SmmuMapResult::Ok);
    CHECK(b.sctlr == 0x05049000);
    CHECK(b.sctlr == a.sctlr + 0x1000);

    // With a 0x1000 stride a 64 KiB window holds banks (0x8000/0x1000) = 8 of them, so bank 7 is
    // the last that fits and bank 8 must be refused rather than computed.
    SmmuBankAddresses last{};
    CHECK(SmmuResolveBank(g, 7, &last) == SmmuMapResult::Ok);
    CHECK(last.sctlr + kCbTtbcr + 4 <= SmmuBase + SmmuSize);
    SmmuBankAddresses past{};
    CHECK(SmmuResolveBank(g, 8, &past) == SmmuMapResult::BankOutOfRange);
  }

  // ---- the case a hard-coded 0x1000 would get wrong ---------------------
  {
    SmmuGeometry g{SmmuBase, SmmuSize, 16};
    SmmuBankAddresses a{};
    CHECK(SmmuResolveBank(g, 0, &a) == SmmuMapResult::Ok);
    // Bank 0 is unchanged -- which is why the bug would go unnoticed on a one-bank test.
    CHECK(a.sctlr == 0x05048000);

    // Bank 1, if computed with the right shift, is 0x10000 further: outside the 64 KiB window.
    // The refusal is correct, and is exactly the case that would otherwise read live registers as
    // if they were a context bank.
    SmmuBankAddresses b{};
    CHECK(SmmuResolveBank(g, 1, &b) == SmmuMapResult::BankOutOfRange);

    // With a window large enough to hold it, the stride is visibly different from 0x1000.
    // CB_BASE for a 0x40000 window is base + 0x20000, so bank 1 with pgshift 16 is base + 0x30000.
    SmmuGeometry big{0x05040000, 0x40000, 16};
    SmmuBankAddresses c{};
    CHECK(SmmuResolveBank(big, 1, &c) == SmmuMapResult::Ok);
    CHECK(c.sctlr == 0x05040000 + 0x30000);
    // The same bank with pgshift 12 would be base + 0x21000 -- a 0xF000 difference, and the two
    // would read different registers entirely.
    SmmuGeometry big12{0x05040000, 0x40000, 12};
    SmmuBankAddresses d{};
    CHECK(SmmuResolveBank(big12, 1, &d) == SmmuMapResult::Ok);
    CHECK(d.sctlr == 0x05040000 + 0x21000);
    CHECK(c.sctlr != d.sctlr);
  }

  // ---- refusals -----------------------------------------------------------
  {
    SmmuGeometry g{SmmuBase, SmmuSize, 12};
    CHECK(SmmuResolveBank(g, 0, nullptr) == SmmuMapResult::BadGeometry);

    SmmuGeometry bad_shift{SmmuBase, SmmuSize, 13};
    SmmuBankAddresses a{};
    CHECK(SmmuResolveBank(bad_shift, 0, &a) == SmmuMapResult::BadGeometry);

    SmmuGeometry bad_shift2{SmmuBase, SmmuSize, 0};
    CHECK(SmmuResolveBank(bad_shift2, 0, &a) == SmmuMapResult::BadGeometry);

    // A base that is not 4 KiB aligned cannot be a real mapping.
    SmmuGeometry misaligned{0x05040001, SmmuSize, 12};
    CHECK(SmmuResolveBank(misaligned, 0, &a) == SmmuMapResult::BadGeometry);

    // A window smaller than one bank.
    SmmuGeometry tiny{SmmuBase, 0x800, 12};
    CHECK(SmmuResolveBank(tiny, 0, &a) == SmmuMapResult::BadGeometry);
  }

  // ---- the stream ids the banks serve -----------------------------------
  CHECK(kStreamGfx3dUser == 0);
  CHECK(kStreamGfx3dSecure == 2);

  if (failures == 0) std::printf("SmmuMap: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
