// SPDX-License-Identifier: BSD-2-Clause
//
// Parses the real a540_gpmu.fw2 pulled from the phone, then checks that the parser
// rejects each way the container can be malformed.
#include "../ChironAdrenoA540Fw2.h"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <vector>

using namespace chiron_adreno;

static int failures = 0;

#define CHECK(cond)                                                \
  do {                                                             \
    if (!(cond)) {                                                 \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);  \
      ++failures;                                                  \
    }                                                              \
  } while (0)

static void Put32(std::vector<uint8_t> &v, size_t dword, uint32_t value) {
  const size_t o = dword * 4;
  v[o + 0] = uint8_t(value);
  v[o + 1] = uint8_t(value >> 8);
  v[o + 2] = uint8_t(value >> 16);
  v[o + 3] = uint8_t(value >> 24);
}

int main() {
  // ---- the real image -------------------------------------------------------
  std::ifstream in("a540_gpmu.fw2", std::ios::binary);
  if (!in) {
    std::printf("FAIL cannot open a540_gpmu.fw2 (run from the tests directory)\n");
    return 1;
  }
  const std::vector<uint8_t> raw((std::istreambuf_iterator<char>(in)),
                                 std::istreambuf_iterator<char>());
  CHECK(raw.size() == 11736);

  Fw2Block b;
  // adreno-gpulist.h gives A540 gpmu_major = 3.
  Fw2Result r = Fw2ParseGpmu(raw.data(), raw.size(), 3, &b);
  CHECK(r == Fw2Result::Ok);
  CHECK(b.block_id == Fw2BlockGpmuFirmware);
  CHECK(b.block_dwords == 2933);
  CHECK(b.header_dwords == 8);
  CHECK(b.major == 3);
  CHECK(b.minor == 9);
  CHECK(b.payload_dwords == 2923);
  CHECK(b.payload_dwords <= Fw2InstanceRamDwords);
  CHECK(b.payload != nullptr);

  // The payload must land exactly where the kernel computes it, dword 11.
  const auto *begin = reinterpret_cast<const uint8_t *>(raw.data());
  const auto *p = reinterpret_cast<const uint8_t *>(b.payload);
  CHECK(p - begin == 11 * 4);
  CHECK(p + size_t(b.payload_dwords) * 4 <= begin + raw.size());
  // dword 11 is the first payload word, which is the load size, not header metadata.
  CHECK(b.payload[0] == 0x00005ff4u);
  // dword 12 is the first instruction word.
  CHECK(b.payload[1] == 0x0000018du);

  // ---- a major the caller does not support must be refused ------------------
  r = Fw2ParseGpmu(raw.data(), raw.size(), 2, &b);
  CHECK(r == Fw2Result::BadVersion);
  CHECK(b.payload == nullptr);

  // ---- structural rejections ------------------------------------------------
  std::vector<uint8_t> bad = raw;

  // Declared block size at or beyond the file's own dword count.
  Put32(bad, 0, uint32_t(raw.size() / 4));
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::BadBlockSize);

  // Block size below the two-dword floor.
  bad = raw;
  Put32(bad, 0, 1);
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::BadBlockSize);

  // Unrecognised block id.
  bad = raw;
  Put32(bad, 1, 7);
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::BadBlockId);

  // Zero header size.
  bad = raw;
  Put32(bad, 2, 0);
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::BadHeaderSize);

  // Odd header size.
  bad = raw;
  Put32(bad, 2, 9);
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::BadHeaderSize);

  // Over the 10-dword cap.
  bad = raw;
  Put32(bad, 2, 12);
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::BadHeaderSize);

  // Header that does not fit inside the declared block.
  bad = raw;
  Put32(bad, 0, 6);
  Put32(bad, 2, 8);
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::BadHeaderSize);

  // Too small to hold the three leading dwords.
  bad.assign(8, 0);
  CHECK(Fw2ParseGpmu(bad.data(), bad.size(), 3, &b) == Fw2Result::TooSmall);
  CHECK(Fw2ParseGpmu(nullptr, 0, 3, &b) == Fw2Result::TooSmall);

  // ---- a synthetic image with a known layout --------------------------------
  // block_dwords 10, header 4 (one field pair), payload 4 dwords at dword 7.
  // The file carries one dword past the declared block, because the kernel insists
  // data[0] be strictly smaller than the file's dword count.
  std::vector<uint8_t> small(4 * 11, 0);
  Put32(small, 0, 10);                   // block_dwords
  Put32(small, 1, Fw2BlockGpmuFirmware); // block id
  Put32(small, 2, 4);                    // header_dwords: one pair
  Put32(small, 3, Fw2FieldMinor);        // tag
  Put32(small, 4, 7);                    // value
  for (uint32_t i = 0; i < 4; ++i) Put32(small, 7 + i, 0xA0000000u + i);
  r = Fw2ParseGpmu(small.data(), small.size(), 3, &b);
  CHECK(r == Fw2Result::Ok);
  CHECK(b.minor == 7);
  CHECK(b.payload_dwords == 4);
  CHECK(b.payload[0] == 0xA0000000u);
  CHECK(b.payload[3] == 0xA0000003u);

  if (failures == 0) std::printf("Fw2: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
