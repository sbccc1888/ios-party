// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540Cpsw.h"

namespace chiron_adreno {

CpFirmwareResult CpFirmwareSplit(const uint8_t *image, size_t bytes, CpFirmware *out) {
  if (out == nullptr) return CpFirmwareResult::TooSmall;
  *out = CpFirmware{};
  if (image == nullptr) return CpFirmwareResult::TooSmall;

  // The kernel computes fw->size - 4 with no guard of its own, so a four-byte file would
  // underflow. Refuse anything that leaves no payload.
  if (bytes <= CpFirmwareHeaderBytes) return CpFirmwareResult::TooSmall;

  const size_t payload_bytes = bytes - CpFirmwareHeaderBytes;
  if (payload_bytes % sizeof(uint32_t) != 0) return CpFirmwareResult::Misaligned;

  const uint32_t *code = reinterpret_cast<const uint32_t *>(image + CpFirmwareHeaderBytes);
  out->code = code;
  out->words = uint32_t(payload_bytes / sizeof(uint32_t));
  // ucode_version is the first payload word, which is the same address the code starts at.
  out->version = code[0];
  return CpFirmwareResult::Ok;
}

}  // namespace chiron_adreno