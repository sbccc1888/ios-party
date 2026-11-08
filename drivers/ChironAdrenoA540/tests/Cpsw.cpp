// SPDX-License-Identifier: BSD-2-Clause
//
// Splits the real a530_pfp.fw and a530_pm4.fw the way adreno_a5xx.c:_load_firmware() does.
#include "../ChironAdrenoA540Cpsw.h"

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

static std::vector<uint8_t> Load(const char *name) {
  std::ifstream in(name, std::ios::binary);
  if (!in) return {};
  return std::vector<uint8_t>((std::istreambuf_iterator<char>(in)),
                              std::istreambuf_iterator<char>());
}

int main() {
  // ---- the header length is four bytes, not zero and not eight --------------
  CHECK(CpFirmwareHeaderBytes == 4u);

  // ---- PFP ------------------------------------------------------------------
  {
    const std::vector<uint8_t> raw = Load("a530_pfp.fw");
    CHECK(raw.size() == 16820);

    CpFirmware fw;
    CHECK(CpFirmwareSplit(raw.data(), raw.size(), &fw) == CpFirmwareResult::Ok);
    // (16820 - 4) / 4 = 4204 words
    CHECK(fw.words == 4204u);
    CHECK(fw.code != nullptr);
    // The pointer is four bytes into the image, and the version is the word there.
    CHECK(reinterpret_cast<const uint8_t *>(fw.code) == raw.data() + 4);
    CHECK(fw.version == 0x005ff112u);
    // The next word is what a reader would mistake for a length field.
    CHECK(fw.code[1] == 0x00000fecu);
    CHECK(fw.words > 1);
  }

  // ---- PM4 ------------------------------------------------------------------
  {
    const std::vector<uint8_t> raw = Load("a530_pm4.fw");
    CHECK(raw.size() == 19652);

    CpFirmware fw;
    CHECK(CpFirmwareSplit(raw.data(), raw.size(), &fw) == CpFirmwareResult::Ok);
    // (19652 - 4) / 4 = 4912 words
    CHECK(fw.words == 4912u);
    CHECK(fw.version == 0x005ff066u);
    CHECK(fw.code[1] == 0x000012b0u);
  }

  // ---- the two images differ, so they are not being read as one -------------
  {
    const std::vector<uint8_t> pfp = Load("a530_pfp.fw");
    const std::vector<uint8_t> pm4 = Load("a530_pm4.fw");
    CpFirmware a, b;
    CHECK(CpFirmwareSplit(pfp.data(), pfp.size(), &a) == CpFirmwareResult::Ok);
    CHECK(CpFirmwareSplit(pm4.data(), pm4.size(), &b) == CpFirmwareResult::Ok);
    CHECK(a.version != b.version);
    CHECK(a.words != b.words);
  }

  // ---- rejections -----------------------------------------------------------
  {
    CpFirmware fw;
    // Exactly the header and nothing else leaves no payload.
    std::vector<uint8_t> tiny(4, 0);
    CHECK(CpFirmwareSplit(tiny.data(), tiny.size(), &fw) == CpFirmwareResult::TooSmall);
    CHECK(fw.code == nullptr);
    CHECK(fw.words == 0);
    CHECK(fw.version == 0);

    // Empty and null.
    CHECK(CpFirmwareSplit(nullptr, 0, &fw) == CpFirmwareResult::TooSmall);
    CHECK(CpFirmwareSplit(tiny.data(), 0, &fw) == CpFirmwareResult::TooSmall);

    // A payload that is not a whole number of dwords: 4 header + 5 bytes.
    std::vector<uint8_t> odd(9, 0);
    CHECK(CpFirmwareSplit(odd.data(), odd.size(), &fw) == CpFirmwareResult::Misaligned);

    // Six bytes is fine -- payload of 2, which is half a dword.
    std::vector<uint8_t> half(6, 0);
    CHECK(CpFirmwareSplit(half.data(), half.size(), &fw) == CpFirmwareResult::Misaligned);

    // Eight bytes is one whole dword of payload.
    std::vector<uint8_t> one{0, 0, 0, 0, 0x78, 0x56, 0x34, 0x12};
    CHECK(CpFirmwareSplit(one.data(), one.size(), &fw) == CpFirmwareResult::Ok);
    CHECK(fw.words == 1u);
    CHECK(fw.version == 0x12345678u);
    CHECK(fw.code[0] == 0x12345678u);
  }

  // ---- the dropped first word really is dropped -----------------------------
  {
    // Put a recognisable sentinel in the first word; it must not appear in the split.
    std::vector<uint8_t> img{0xEF, 0xBE, 0xAD, 0xDE, 0x11, 0x22, 0x33, 0x44};
    CpFirmware fw;
    CHECK(CpFirmwareSplit(img.data(), img.size(), &fw) == CpFirmwareResult::Ok);
    CHECK(fw.words == 1u);
    CHECK(fw.version == 0x44332211u);
    CHECK(fw.version != 0xDEADBEEFu);
  }

  if (failures == 0) std::printf("Cpsw: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
