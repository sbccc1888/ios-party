// SPDX-License-Identifier: BSD-2-Clause
//
// A540 CP microcode: the PFP and PM4 images the command processor runs.
//
// Source: drivers/gpu/msm/adreno_a5xx.c, _load_firmware() at line 2534.
//
// The format is almost nothing, which is worth writing down because it is easy to imagine
// more structure than exists. _load_firmware() does exactly this:
//
//     kgsl_allocate_global(device, ucode, fw->size - 4, KGSL_MEMFLAGS_GPUREADONLY, 0, "ucode");
//     memcpy(ucode->hostptr, &fw->data[4], fw->size - 4);
//     *ucode_size = (fw->size - 4) / sizeof(uint32_t);
//     *ucode_version = *(unsigned int *)&fw->data[4];
//
// So the first four bytes are dropped, everything from offset 4 onward is copied verbatim
// into GPU-visible memory, and the fourth byte onward doubles as the version read. There is
// no container, no per-section header, no checksum. a530_pfp.fw and a530_pm4.fw are flat.
//
// Observed on chiron's own files:
//
//     file           size    dword at +4   dword at +8
//     a530_pfp.fw    16820   0x005ff112    0x00000fec (4012)
//     a530_pm4.fw    19652   0x005ff066    0x000012b0 (4784)
//
// The dword at +8 reading as a plausible instruction count for each image is consistent
// with it being the first word of the payload, not a separate length field -- the kernel
// never reads it.

#ifndef CHIRON_ADRENO_A540_CPSW_H
#define CHIRON_ADRENO_A540_CPSW_H

#include <stddef.h>
#include <stdint.h>

namespace chiron_adreno {

// The four bytes _load_firmware() skips.
enum : uint32_t { CpFirmwareHeaderBytes = 4 };

enum class CpFirmwareResult {
  Ok,
  TooSmall,      // no payload once the header is dropped
  Misaligned,    // payload length is not a whole number of dwords
};

// What the caller hands to the GPU: a pointer into the caller's own image, so nothing is
// copied here. The kernel copies into a device allocation; a driver is free to map the
// firmware in place instead, and this keeps that choice outside.
struct CpFirmware {
  const uint32_t *code = nullptr;  // word at fw->data[4]
  uint32_t words = 0;              // (size - 4) / 4
  uint32_t version = 0;            // the kernel's ucode_version, i.e. *code
};

// Splits a firmware image the way _load_firmware() does. On failure the outputs stay
// zeroed.
CpFirmwareResult CpFirmwareSplit(const uint8_t *image, size_t bytes, CpFirmware *out);

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_CPSW_H
