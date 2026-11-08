// SPDX-License-Identifier: BSD-2-Clause
//
// Turning what the A540's SMMU reports about itself into ArmSmmu4K's inputs.
//
// ArmSmmu4KEncodeContext refuses to guess. Reading ArmSmmu4KContext.c:32-39, three things must come
// from the hardware and nowhere else:
//
//   unsigned va = width_bits((c->id2 >> 8) & 15);   // input address width
//   unsigned pa = width_bits((c->id2 >> 4) & 15);   // output address width
//   if (!(c->id0 & (1u << 30)) || !(c->id2 & (1u << 12)) || !va || !pa) return ArmSmmu4KUnsupported;
//
// bit 30 of ID0 is stage-1 translation support; bit 12 of ID2 is 4 KiB granule support. Neither can be
// assumed from "it is an Adreno", and a wrong guess here surfaces as ArmSmmu4KUnsupported at encode
// time with no indication of which check failed.
//
// The width encoding is SMMUv2's, from arm-smmu.c:
//
//   0 -> 32, 1 -> 36, 2 -> 40, 3 -> 42, 4 -> 44, 5 -> 48   (anything else: unsupported)
//
// So this layer exists to hold the identity registers a probe reads, decode them the way the kit
// does, and produce an ArmSmmu4KConfig whose input_bits and output_bits are derived rather than
// chosen. It does not decide table placement or leaf budgets -- those are the caller's, because they
// depend on how much memory the caller is willing to give the SMMU.

#ifndef CHIRON_ADRENO_A540_SMMU_IDENTITY_H
#define CHIRON_ADRENO_A540_SMMU_IDENTITY_H

#include <stdint.h>

namespace chiron_adreno {

// Register offsets within the SMMU block, arm-smmu.c:108-110.
inline constexpr uint32_t kSmmuGr0Id0 = 0x20;
inline constexpr uint32_t kSmmuGr0Id1 = 0x24;
inline constexpr uint32_t kSmmuGr0Id2 = 0x28;

// The raw values, as read.
struct SmmuIdentity {
  uint32_t id0;
  uint32_t id1;
  uint32_t id2;
};

enum class SmmuIdentityResult {
  Ok,
  UnsupportedAddressWidth,  // a width code outside 0..5
  NoStage1,                 // ID0 bit 30 clear
  No4KGranule,              // ID2 bit 12 clear
  AddressWidthTooNarrow,    // this port requires at least 32-bit input and output
};

struct SmmuAddressWidths {
  unsigned input_bits;
  unsigned output_bits;
};

// Decode the width code the way ArmSmmu4KContext.c does. Returns 0 for an unsupported code, which is
// the same sentinel the kit uses.
inline constexpr unsigned SmmuWidthBits (unsigned code)
{
  switch (code) {
    case 0: return 32;
    case 1: return 36;
    case 2: return 40;
    case 3: return 42;
    case 4: return 44;
    case 5: return 48;
    default: return 0;
  }
}

// The identity bits that have to be present before a context can be encoded at all.
inline constexpr uint32_t kId0Stage1 = 1u << 30;
inline constexpr uint32_t kId2FourKB = 1u << 12;

SmmuIdentityResult SmmuDecodeIdentity (const SmmuIdentity &id, SmmuAddressWidths *out);

// Fill the two width fields of an ArmSmmu4KConfig, leaving table_physical and leaf_budget to the
// caller. Kept as plain numbers so this header does not have to include the kit.
struct SmmuConfigWidths {
  unsigned input_bits;
  unsigned output_bits;
};

SmmuIdentityResult SmmuConfigFromIdentity (const SmmuIdentity &id, SmmuConfigWidths *out);

// Which context bank serves a stream id, from the device tree's iommus cells. The user bank is
// stream 0 and the secure bank is stream 2, but a caller should not hard-code that mapping.
enum class SmmuBankForStream {
  User,
  Secure,
  Unknown,
};

inline constexpr SmmuBankForStream SmmuBankForStreamId (uint32_t stream_id)
{
  if (stream_id == 0) return SmmuBankForStream::User;
  if (stream_id == 2) return SmmuBankForStream::Secure;
  return SmmuBankForStream::Unknown;
}

// The bank index each stream is wired to. User is bank 0; the secure stream is a separate bank, but
// which index it occupies is not given by the device tree -- qcom,gpu-offset only describes the user
// bank. This is deliberately not guessed: the probe reads banks 0 and 1 and the answer decides it.
inline constexpr unsigned kUserBankIndex = 0;
inline constexpr unsigned kCandidateSecureBankIndex = 1;

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_SMMU_IDENTITY_H