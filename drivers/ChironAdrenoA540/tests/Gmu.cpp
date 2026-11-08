// SPDX-License-Identifier: BSD-2-Clause
//
// Drives the GMU start handshake against a model of the GPU register window.
#include "../ChironAdrenoA540Gmu.h"

#include <cstdio>
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

namespace {

struct Write {
  uint32_t offset;
  uint32_t value;
};

struct Model {
  std::vector<Write> writes;
  std::vector<uint32_t> reads;
  std::vector<uint32_t> delays;
  uint32_t general0 = 0;
  uint32_t general1 = 0;
  uint32_t alive_after = 0;  // number of reads before GENERAL_0 reports alive; 0 = never
  uint32_t general0_reads = 0;

  static void Write32(void *ctx, uint32_t offset, uint32_t value) {
    static_cast<Model *>(ctx)->writes.push_back(Write{offset, value});
  }
  static uint32_t Read32(void *ctx, uint32_t offset) {
    auto *m = static_cast<Model *>(ctx);
    m->reads.push_back(offset);
    if (offset == GmuRegister(GmuGeneral0)) {
      ++m->general0_reads;
      if (m->alive_after != 0 && m->general0_reads >= m->alive_after)
        return GmuFirmwareAlive;
      return m->general0;
    }
    if (offset == GmuRegister(GmuGeneral1)) return m->general1;
    return 0;
  }
  static void Delay(void *ctx, uint32_t us) {
    static_cast<Model *>(ctx)->delays.push_back(us);
  }

  GmuRegs regs(bool a530 = false) {
    return GmuRegs{Write32, Read32, Delay, this, a530};
  }
};

}  // namespace

int main() {
  // ---- the word-offset conversion is the thing most likely to be wrong -------
  CHECK(GmuRegister(GmuCm3Sysreset) == 0xA8D8u * 4u);
  CHECK(GmuRegister(GmuGeneral0) == 0xA8E0u * 4u);
  CHECK(GmuGeneral0 < GmuGeneral1);
  CHECK(GmuInstRamBase * 4u < GmuDataRamBase * 4u);

  // ---- a clean start --------------------------------------------------------
  {
    Model m;
    m.alive_after = 1;  // alive on the first read
    uint32_t err = 0xDEAD;
    const GmuResult r = GmuStartFirmware(m.regs(), &err);
    CHECK(r == GmuResult::Ok);
    CHECK(err == 0);

    // Exactly one register write, releasing the core, at the right offset.
    CHECK(m.writes.size() == 1);
    CHECK(m.writes[0].offset == GmuRegister(GmuCm3Sysreset));
    CHECK(m.writes[0].value == 0);

    // A540 is not an a530, so GENERAL_1 must be consulted.
    bool read_general1 = false;
    for (uint32_t off : m.reads)
      if (off == GmuRegister(GmuGeneral1)) read_general1 = true;
    CHECK(read_general1);
  }

  // ---- the firmware answers after a few polls -------------------------------
  {
    Model m;
    m.alive_after = 4;
    uint32_t err = 0;
    CHECK(GmuStartFirmware(m.regs(), &err) == GmuResult::Ok);
    CHECK(m.general0_reads == 4);
    // One microsecond between each poll, so exactly one delay per read.
    CHECK(m.delays.size() == 4);
    for (uint32_t d : m.delays) CHECK(d == 1);
  }

  // ---- never alive is a timeout, not a hang --------------------------------
  {
    Model m;
    m.alive_after = 0;  // never
    m.general0 = 0;
    uint32_t err = 0;
    CHECK(GmuStartFirmware(m.regs(), &err) == GmuResult::FirmwareTimeout);
    // The do/while runs once, then retries while the counter is non-zero, so the total is
    // the retry budget plus the initial pass.
    CHECK(m.general0_reads == GmuFirmwareInitRetry + 1);
    CHECK(m.delays.size() == m.general0_reads);
  }

  // ---- alive but GENERAL_1 complains ---------------------------------------
  {
    Model m;
    m.alive_after = 2;
    m.general1 = 42;
    uint32_t err = 0;
    CHECK(GmuStartFirmware(m.regs(), &err) == GmuResult::FirmwareError);
    CHECK(err == 42u);
  }

  // ---- the a530 branch takes a different path ------------------------------
  {
    Model m;
    m.alive_after = 1;
    uint32_t err = 0;
    CHECK(GmuStartFirmware(m.regs(/*a530=*/true), &err) == GmuResult::Ok);
    // Two writes: the WFI configuration, then the core release.
    CHECK(m.writes.size() == 2);
    CHECK(m.writes[0].offset == GmuRegister(GmuWfiConfig));
    CHECK(m.writes[0].value == GmuWfiConfigA530);
    CHECK(m.writes[1].offset == GmuRegister(GmuCm3Sysreset));
    CHECK(m.writes[1].value == 0);
    // And GENERAL_1 is never read.
    for (uint32_t off : m.reads) CHECK(off != GmuRegister(GmuGeneral1));
  }

  // ---- missing callbacks are refused ---------------------------------------
  {
    Model m;
    m.alive_after = 1;
    GmuRegs none{nullptr, nullptr, nullptr, nullptr, false};
    CHECK(GmuStartFirmware(none, nullptr) == GmuResult::NoRegisters);
    GmuRegs half{Model::Write32, nullptr, Model::Delay, &m, false};
    CHECK(GmuStartFirmware(half, nullptr) == GmuResult::NoRegisters);
    // A null error pointer must be tolerated on the error paths.
    GmuRegs ok{Model::Write32, Model::Read32, Model::Delay, &m, false};
    CHECK(GmuStartFirmware(ok, nullptr) == GmuResult::Ok);
  }

  // ---- pending interrupts are read from the right place --------------------
  {
    Model m;
    CHECK(GmuTakePendingInterrupts(m.regs()) == 0);
    CHECK(!m.reads.empty());
    CHECK(m.reads.back() == GmuRegister(GmuRbbmIntrInfo));
  }

  if (failures == 0) std::printf("Gmu: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
