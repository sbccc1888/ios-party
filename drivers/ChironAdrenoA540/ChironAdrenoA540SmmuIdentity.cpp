// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540SmmuIdentity.h"

namespace chiron_adreno {

SmmuIdentityResult SmmuDecodeIdentity (const SmmuIdentity &id, SmmuAddressWidths *out)
{
  if (out == nullptr) {
    return SmmuIdentityResult::UnsupportedAddressWidth;
  }

  /*
   * Order matters for the message, not the result: a caller reading a failure wants to know whether
   * the block is unusable or merely narrower than this port needs. Stage 1 and the 4 KiB granule are
   * hard requirements -- without either, no context can be built for the GPU at all.
   */
  if ((id.id0 & kId0Stage1) == 0) {
    return SmmuIdentityResult::NoStage1;
  }

  if ((id.id2 & kId2FourKB) == 0) {
    return SmmuIdentityResult::No4KGranule;
  }

  const unsigned va = SmmuWidthBits ((id.id2 >> 8) & 0xF);
  const unsigned pa = SmmuWidthBits ((id.id2 >> 4) & 0xF);
  if ((va == 0) || (pa == 0)) {
    return SmmuIdentityResult::UnsupportedAddressWidth;
  }

  /*
   * ArmSmmu4KConfig allows input_bits 32..48 and output_bits in the same set. A block reporting less
   * than 32 bits of either cannot be described by that type, and would be a strange SMMU anyway.
   */
  if ((va < 32) || (pa < 32)) {
    return SmmuIdentityResult::AddressWidthTooNarrow;
  }

  out->input_bits = va;
  out->output_bits = pa;
  return SmmuIdentityResult::Ok;
}

SmmuIdentityResult SmmuConfigFromIdentity (const SmmuIdentity &id, SmmuConfigWidths *out)
{
  if (out == nullptr) {
    return SmmuIdentityResult::UnsupportedAddressWidth;
  }

  SmmuAddressWidths w{};
  const SmmuIdentityResult r = SmmuDecodeIdentity (id, &w);
  if (r != SmmuIdentityResult::Ok) {
    return r;
  }

  out->input_bits = w.input_bits;
  out->output_bits = w.output_bits;
  return SmmuIdentityResult::Ok;
}

}  // namespace chiron_adreno