// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540Fw2.h"

namespace chiron_adreno {

static uint32_t Le32(const uint8_t *p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) |
         (uint32_t(p[3]) << 24);
}

Fw2Result Fw2ParseGpmu(const uint8_t *image, size_t bytes, uint32_t required_major,
                       Fw2Block *out) {
  if (out == nullptr) return Fw2Result::TooSmall;
  *out = Fw2Block{};

  // The three leading dwords are the block size, the block id and the header size.
  if (image == nullptr || bytes < 3 * sizeof(uint32_t)) return Fw2Result::TooSmall;

  const uint32_t total_dwords = uint32_t(bytes / sizeof(uint32_t));
  const uint32_t block_dwords = Le32(image);
  const uint32_t block_id = Le32(image + 4);
  const uint32_t header_dwords = Le32(image + 8);

  // The kernel's guard is "data[0] >= fw->size / 4 || data[0] < 2", so the declared
  // block size must be at least two dwords and strictly smaller than the file.
  if (block_dwords < 2 || block_dwords >= total_dwords) return Fw2Result::BadBlockSize;

  if (block_id != Fw2BlockGpmuFirmware && block_id != Fw2BlockGpmuSequence)
    return Fw2Result::BadBlockId;

  // _read_fw2_block_header: non-zero, even, and within the cap.
  if (header_dwords == 0 || (header_dwords & 1u) || header_dwords > Fw2MaxHeaderDwords)
    return Fw2Result::BadHeaderSize;

  // It must also leave room inside the block: the kernel checks data[2] <= data[0] - 2.
  if (header_dwords > block_dwords - 2) return Fw2Result::BadHeaderSize;

  Fw2Block block;
  block.block_dwords = block_dwords;
  block.block_id = block_id;
  block.header_dwords = header_dwords;

  // Fields are the dwords after the header size, taken as (id, value) pairs.
  const uint32_t field_first = 3;                       // dword index of the first tag
  const uint32_t field_end = 3 + header_dwords;         // one past the last field dword
  if (field_end > total_dwords) return Fw2Result::Truncated;

  for (uint32_t i = field_first; i + 1 < field_end; i += 2) {
    const uint32_t field = Le32(image + size_t(i) * 4);
    const uint32_t value = Le32(image + size_t(i + 1) * 4);

    switch (field) {
      case Fw2FieldMajor:
        if (value != 0 && value > required_major) return Fw2Result::BadVersion;
        block.major = value;
        break;
      case Fw2FieldMinor:
        block.minor = value;
        break;
      case Fw2FieldDate:
        block.date = value;
        break;
      case Fw2FieldTime:
        block.time = value;
        break;
      case Fw2FieldSequence:
        if (value >= Fw2MaxSequenceId) return Fw2Result::BadBlockId;
        block.sequence_id = value;
        break;
      default:
        // The kernel logs unknown tags and carries on.
        break;
    }
  }

  // The kernel's own payload arithmetic, reproduced exactly rather than inferred.
  const uint64_t payload_start = uint64_t(header_dwords) + 3;
  const uint64_t payload_dwords = uint64_t(block_dwords) - header_dwords - 2;

  if (payload_start + payload_dwords > total_dwords) return Fw2Result::Truncated;
  if (payload_dwords > Fw2InstanceRamDwords) return Fw2Result::PayloadTooBig;

  block.payload = reinterpret_cast<const uint32_t *>(image + payload_start * 4);
  block.payload_dwords = uint32_t(payload_dwords);
  *out = block;
  return Fw2Result::Ok;
}

}  // namespace chiron_adreno
