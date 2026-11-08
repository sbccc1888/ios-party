// SPDX-License-Identifier: BSD-2-Clause
//
// Legacy Adreno "fw2" container, as used by A540 on chiron.
//
// This is not the format UniXNU's AdrenoFirmware kit parses.  That one expects
// {u32 address, u32 length, u32 type, u32 value} blocks, where the first word is an
// address.  A540's a540_gpmu.fw2 keeps a block size there instead, so handing it to
// that parser reads a 576 KiB block out of an 11 KiB file and fails immediately.
//
// The authoritative reader is the MSM8998 kernel:
//   drivers/gpu/msm/adreno_a5xx.c : _load_gpmu_firmware()    (line ~683)
//                                  _read_fw2_block_header()  (line ~1241)
//   drivers/gpu/msm/adreno_a5xx.h : the GPMU_* / HEADER_* constants
//
// Layout, one u32 per field, all little-endian:
//
//   +0x00  u32 block_dwords   whole block, payload included.  Strictly less than the
//                             file's dword count; a540_gpmu.fw2 has 2934 dwords in
//                             the file and declares 2933 here.
//   +0x04  u32 block_id       2 = GPMU firmware, 3 = GPMU sequence
//   +0x08  u32 header_dwords  count of field dwords that follow, NOT including
//                             itself.  Even, non-zero, at most 10.
//   +0x0c  { u32 field_id; u32 value; } x (header_dwords / 2)
//   ...    payload, at dword (header_dwords + 3), for
//          (block_dwords - header_dwords - 2) dwords
//
// That last pair is the kernel's own arithmetic:
//     cmds     = data + data[2] + 3;
//     cmd_size = data[0] - data[2] - 2;
//
// Field ids are tags, not version numbers: HEADER_MAJOR (1) is followed by the actual
// major version, HEADER_MINOR (2) by the minor.  Reading the tag as the version gives 1
// and 2, which is wrong.

#ifndef CHIRON_ADRENO_A540_FW2_H
#define CHIRON_ADRENO_A540_FW2_H

#include <stddef.h>
#include <stdint.h>

namespace chiron_adreno {

// adreno_a5xx.h:215-228
enum : uint32_t {
  Fw2BlockGpmuFirmware = 2,
  Fw2BlockGpmuSequence = 3,

  Fw2FieldMajor = 1,
  Fw2FieldMinor = 2,
  Fw2FieldDate = 3,
  Fw2FieldTime = 4,
  Fw2FieldSequence = 5,

  Fw2MaxHeaderDwords = 10,
  Fw2InstanceRamDwords = 0xFFF,  // GPMU_INST_RAM_SIZE
  Fw2MaxSequenceId = 3,
};

enum class Fw2Result {
  Ok,
  TooSmall,        // fewer than three leading dwords
  BadBlockSize,    // block_dwords < 2 or not below the file's dword count
  BadBlockId,      // not a recognised block id
  BadHeaderSize,   // zero, odd, over the cap, or too large for the block
  Truncated,       // payload runs past the end of the image
  PayloadTooBig,   // payload exceeds the GPMU instruction RAM
  BadVersion,      // major version newer than the caller supports
};

// One parsed block.  payload points into the caller's image; nothing is copied.
struct Fw2Block {
  uint32_t block_dwords = 0;
  uint32_t block_id = 0;
  uint32_t header_dwords = 0;
  uint32_t major = 0;
  uint32_t minor = 0;
  uint32_t date = 0;
  uint32_t time = 0;
  uint32_t sequence_id = 0;
  const uint32_t *payload = nullptr;
  uint32_t payload_dwords = 0;
};

// Parses a GPMU firmware image.  required_major is what the caller's hardware expects;
// a block declaring a newer major is rejected, matching the kernel's check, which only
// complains when the block states a version and that version is newer.
// On failure the outputs are left zeroed.
Fw2Result Fw2ParseGpmu(const uint8_t *image, size_t bytes, uint32_t required_major,
                       Fw2Block *out);

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_FW2_H
