// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540SmmuMap.h"

namespace chiron_adreno {

SmmuMapResult SmmuResolveBank (const SmmuGeometry &geom, unsigned bank,
                               SmmuBankAddresses *out)
{
  if (out == nullptr) {
    return SmmuMapResult::BadGeometry;
  }

  if ((geom.pgshift != 12) && (geom.pgshift != 16)) {
    return SmmuMapResult::BadGeometry;
  }

  if ((geom.size < 0x1000) || ((geom.base & 0xFFF) != 0)) {
    return SmmuMapResult::BadGeometry;
  }

  const uint64_t cb_base = SmmuContextBankBase (geom.base, geom.size);
  const uint64_t stride = SmmuBankStride (geom.pgshift);

  /*
   * The bank's last register is TTBCR at 0x30. Checking the whole bank rather than just the base
   * means a bank whose first register fits but whose last does not is refused here, not by a fault
   * during a read.
   */
  const uint64_t bank_base = cb_base + uint64_t (bank) * stride;
  if (bank_base + kCbTtbcr + 4 > geom.base + geom.size) {
    return SmmuMapResult::BankOutOfRange;
  }

  out->sctlr = bank_base + kCbSctlr;
  out->actlr = bank_base + kCbActlr;
  out->ttbr0 = bank_base + kCbTtbr0;
  out->ttbcr = bank_base + kCbTtbcr;
  return SmmuMapResult::Ok;
}

}  // namespace chiron_adreno