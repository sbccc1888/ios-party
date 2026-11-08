// SPDX-License-Identifier: BSD-2-Clause
//
// Checks the identity decode against ArmSmmu4KContext.c. The encoding table is short and easy to get
// subtly wrong -- a 42 where 44 belongs still produces a plausible config -- so every code is pinned.
#include "../ChironAdrenoA540SmmuIdentity.h"

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

namespace {

// A block that passes every requirement, with 48-bit VA and 48-bit PA.
SmmuIdentity Good() {
  SmmuIdentity id{};
  id.id0 = kId0Stage1;                    // supports stage 1
  id.id2 = kId2FourKB | (5u << 8) | (5u << 4);
  id.id1 = 0;
  return id;
}

}  // namespace

int main() {
  // ---- the width table, every entry --------------------------------
  CHECK(SmmuWidthBits(0) == 32);
  CHECK(SmmuWidthBits(1) == 36);
  CHECK(SmmuWidthBits(2) == 40);
  CHECK(SmmuWidthBits(3) == 42);
  CHECK(SmmuWidthBits(4) == 44);
  CHECK(SmmuWidthBits(5) == 48);
  // 6..15 are not defined by SMMUv2 and must not silently become a width.
  for (unsigned c = 6; c <= 15; ++c) CHECK(SmmuWidthBits(c) == 0);

  // Every defined width is one ArmSmmu4KConfig accepts.
  for (unsigned c = 0; c <= 5; ++c) {
    const unsigned b = SmmuWidthBits(c);
    CHECK(b >= 32 && b <= 48);
  }

  // ---- a good block -------------------------------------------------
  {
    SmmuAddressWidths w{};
    CHECK(SmmuDecodeIdentity(Good(), &w) == SmmuIdentityResult::Ok);
    CHECK(w.input_bits == 48);
    CHECK(w.output_bits == 48);
  }

  // ---- narrower blocks ----------------------------------------------
  {
    SmmuIdentity id = Good();
    id.id2 = kId2FourKB | (0u << 8) | (1u << 4);   // 32-bit VA, 36-bit PA
    SmmuAddressWidths w{};
    CHECK(SmmuDecodeIdentity(id, &w) == SmmuIdentityResult::Ok);
    CHECK(w.input_bits == 32);
    CHECK(w.output_bits == 36);
  }

  // Each code round-trips through the right nibble.
  for (unsigned va_code = 0; va_code <= 5; ++va_code) {
    for (unsigned pa_code = 0; pa_code <= 5; ++pa_code) {
      SmmuIdentity id = Good();
      id.id2 = kId2FourKB | (va_code << 8) | (pa_code << 4);
      SmmuAddressWidths w{};
      CHECK(SmmuDecodeIdentity(id, &w) == SmmuIdentityResult::Ok);
      CHECK(w.input_bits == SmmuWidthBits(va_code));
      CHECK(w.output_bits == SmmuWidthBits(pa_code));
    }
  }

  // ---- the hard requirements ----------------------------------------
  {
    // No stage 1: refused before anything else is looked at.
    SmmuIdentity id = Good();
    id.id0 = 0;
    SmmuAddressWidths w{};
    CHECK(SmmuDecodeIdentity(id, &w) == SmmuIdentityResult::NoStage1);

    // Other ID0 bits do not substitute for bit 30.
    SmmuIdentity id2 = Good();
    id2.id0 = 0xFFFFFFFFu & ~kId0Stage1;
    CHECK(SmmuDecodeIdentity(id2, &w) == SmmuIdentityResult::NoStage1);
  }
  {
    // No 4 KiB granule.
    SmmuIdentity id = Good();
    id.id2 = (5u << 8) | (5u << 4);   // bit 12 clear
    SmmuAddressWidths w{};
    CHECK(SmmuDecodeIdentity(id, &w) == SmmuIdentityResult::No4KGranule);
  }

  // ---- undefined width codes ----------------------------------------
  {
    SmmuIdentity id = Good();
    id.id2 = kId2FourKB | (6u << 8) | (5u << 4);
    SmmuAddressWidths w{};
    CHECK(SmmuDecodeIdentity(id, &w) == SmmuIdentityResult::UnsupportedAddressWidth);

    id.id2 = kId2FourKB | (5u << 8) | (15u << 4);
    CHECK(SmmuDecodeIdentity(id, &w) == SmmuIdentityResult::UnsupportedAddressWidth);

    // A zero VA code is NOT an error: 0 means 32 bits. Only codes 6..15 are undefined, which is the
    // trap this line exists to keep visible -- an all-zero ID2 yields a 32/32 block, not a refusal.
    id.id2 = kId2FourKB;
    CHECK(SmmuDecodeIdentity(id, &w) == SmmuIdentityResult::Ok);
    CHECK(w.input_bits == 32);
    CHECK(w.output_bits == 32);
  }

  // ---- the config view matches the decode ---------------------------
  {
    SmmuConfigWidths c{};
    CHECK(SmmuConfigFromIdentity(Good(), &c) == SmmuIdentityResult::Ok);
    CHECK(c.input_bits == 48);
    CHECK(c.output_bits == 48);

    SmmuIdentity bad = Good();
    bad.id0 = 0;
    CHECK(SmmuConfigFromIdentity(bad, &c) == SmmuIdentityResult::NoStage1);
  }

  // ---- null out ------------------------------------------------------
  {
    CHECK(SmmuDecodeIdentity(Good(), nullptr) == SmmuIdentityResult::UnsupportedAddressWidth);
    CHECK(SmmuConfigFromIdentity(Good(), nullptr) == SmmuIdentityResult::UnsupportedAddressWidth);
  }

  // ---- the register offsets -----------------------------------------
  CHECK(kSmmuGr0Id0 == 0x20);
  CHECK(kSmmuGr0Id1 == 0x24);
  CHECK(kSmmuGr0Id2 == 0x28);
  CHECK(kSmmuGr0Id0 + 4 == kSmmuGr0Id1);
  CHECK(kSmmuGr0Id1 + 4 == kSmmuGr0Id2);

  // ---- stream to bank mapping ---------------------------------------
  CHECK(SmmuBankForStreamId(0) == SmmuBankForStream::User);
  CHECK(SmmuBankForStreamId(2) == SmmuBankForStream::Secure);
  CHECK(SmmuBankForStreamId(1) == SmmuBankForStream::Unknown);
  CHECK(SmmuBankForStreamId(0xFFFFFFFF) == SmmuBankForStream::Unknown);

  // Bank indices must not collide, or the two streams would share a translation.
  CHECK(kUserBankIndex != kCandidateSecureBankIndex);

  if (failures == 0) std::printf("SmmuIdentity: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
