// SPDX-License-Identifier: BSD-2-Clause
//
// A540 GMU (GPMU) start-up handshake.
//
// Source: drivers/gpu/msm/adreno_a5xx.c, a5xx_gpmu_start() at line 780, with the register
// indices from drivers/gpu/msm/a5xx_reg.h:768-784.
//
// The indices in that header are word offsets, not byte offsets -- kgsl_regwrite() is
// declared with a parameter called offsetwords and adreno_regwrite() asserts
// offsetwords * sizeof(uint32_t) < reg_len. So every constant here is multiplied by four
// before it reaches a pointer, which is done once by GmuRegister().
//
// The handshake itself is two steps and one number:
//
//   1. write 0 to GPMU_CM3_SYSRESET, which releases the CM3 core
//   2. poll GPMU_GENERAL_0 until it reads 0xBABEFACE
//   3. on anything that is not an A530, also require GPMU_GENERAL_1 to read 0
//
// The firmware tells the host it is alive by writing that magic into a general-purpose
// register. The hardware team's figure for how long that takes is about 3000 cycles, so
// the kernel polls 5000 times at 1 us each and calls it a timeout after that.

#ifndef CHIRON_ADRENO_A540_GMU_H
#define CHIRON_ADRENO_A540_GMU_H

#include <stdint.h>

namespace chiron_adreno {

// a5xx_reg.h word offsets.
enum : uint32_t {
  GmuInstRamBase = 0x8800,
  GmuDataRamBase = 0x9800,
  GmuWfiConfig = 0xA8C1,
  GmuRbbmIntrInfo = 0xA8D6,
  GmuCm3Sysreset = 0xA8D8,
  GmuGeneral0 = 0xA8E0,
  GmuGeneral1 = 0xA8E1,
};

// The value the firmware leaves in GENERAL_0 once it is running.
enum : uint32_t { GmuFirmwareAlive = 0xBABEFACE };

// GPMU_FW_INIT_RETRY from adreno_a5xx.c:85.
enum : uint32_t { GmuFirmwareInitRetry = 5000 };

// a530 needs a clock-gating register written before the core is released, and does not
// get the GENERAL_1 error check. A540 is an a5xx part but not an a530, so it takes the
// other branch -- worth stating because the kernel's condition reads
// adreno_is_a530(), which looks like it might be true for every a5xx.
enum : uint32_t { GmuWfiConfigA530 = 0x00004014 };

// Word offset to byte offset, the conversion adreno_regwrite() performs.
inline uint32_t GmuRegister(uint32_t word_offset) { return word_offset * 4u; }

// What the caller has to provide. read32/write32 take a byte offset into the GPU register
// window, which on chiron starts at 0x05000000; delay_us may be null in tests.
struct GmuRegs {
  void (*write32)(void *ctx, uint32_t byte_offset, uint32_t value);
  uint32_t (*read32)(void *ctx, uint32_t byte_offset);
  void (*delay_us)(void *ctx, uint32_t us);
  void *ctx;
  bool is_a530;
};

enum class GmuResult {
  Ok,
  NoRegisters,      // a required callback was missing
  FirmwareTimeout,  // GENERAL_0 never read 0xBABEFACE
  FirmwareError,    // GENERAL_0 was good but GENERAL_1 reported a failure
};

// Runs the handshake. On FirmwareError the failing GENERAL_1 value is stored in
// *error_value when it is non-null.
GmuResult GmuStartFirmware(const GmuRegs &regs, uint32_t *error_value);

// Reads the pending-interrupt register and clears it, which is what the kernel does once
// the firmware is up. Returns the value that was pending.
uint32_t GmuTakePendingInterrupts(const GmuRegs &regs);

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_GMU_H
