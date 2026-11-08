// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540Gmu.h"

namespace chiron_adreno {

GmuResult GmuStartFirmware(const GmuRegs &regs, uint32_t *error_value) {
  if (error_value != nullptr) *error_value = 0;
  if (regs.write32 == nullptr || regs.read32 == nullptr) return GmuResult::NoRegisters;

  // A530 gets its clock-gating configuration before the core is released. A540 does not.
  if (regs.is_a530) regs.write32(regs.ctx, GmuRegister(GmuWfiConfig), GmuWfiConfigA530);

  // Release the CM3 core.
  regs.write32(regs.ctx, GmuRegister(GmuCm3Sysreset), 0);

  // Poll for the magic. The kernel decrements a counter of 5000 and stops when it either
  // sees the value or runs out, with one microsecond between reads.
  uint32_t general0 = 0;
  uint32_t retry = GmuFirmwareInitRetry;
  do {
    if (regs.delay_us != nullptr) regs.delay_us(regs.ctx, 1);
    general0 = regs.read32(regs.ctx, GmuRegister(GmuGeneral0));
  } while (general0 != GmuFirmwareAlive && retry-- != 0);

  if (general0 != GmuFirmwareAlive) return GmuResult::FirmwareTimeout;

  // Everything that is not an A530 also has to see GENERAL_1 clear.
  if (!regs.is_a530) {
    const uint32_t general1 = regs.read32(regs.ctx, GmuRegister(GmuGeneral1));
    if (general1 != 0) {
      if (error_value != nullptr) *error_value = general1;
      return GmuResult::FirmwareError;
    }
  }

  return GmuResult::Ok;
}

uint32_t GmuTakePendingInterrupts(const GmuRegs &regs) {
  if (regs.read32 == nullptr) return 0;
  const uint32_t pending = regs.read32(regs.ctx, GmuRegister(GmuRbbmIntrInfo));
  // The kernel only reads it here; the comment says reading is what clears it.
  return pending;
}

}  // namespace chiron_adreno
