// SPDX-License-Identifier: BSD-2-Clause
//
// Runs GpuccPowerUp() against a recording model of the GPUCC block and checks the
// sequence matches enable_gfx_crc() in clock-gpu-8998.c.
//
// The register file is a plain array reached through a pointer, so writes cannot be
// intercepted. The board callbacks are the witness instead: the model snapshots the
// registers every time the sequence asks for a delay or fires the rail, and the order of
// those snapshots is what the assertions check.
#include "../ChironAdrenoA540Gpucc.h"

#include <cstdio>
#include <cstring>
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

struct Snapshot {
  uint32_t bcr;
  uint32_t misc;
};

struct Model {
  std::vector<uint32_t> regs = std::vector<uint32_t>(0x9000 / 4, 0);
  std::vector<uint32_t> delays;
  std::vector<Snapshot> at_delay;
  std::vector<uint32_t> cx_at_rail;
  uint32_t rail_on_calls = 0;
  bool rail_present = true;

  static void RailOn(void *ctx) {
    auto *m = static_cast<Model *>(ctx);
    ++m->rail_on_calls;
    m->cx_at_rail.push_back(m->regs[GpuCxGdscr / 4]);
  }
  static void Delay(void *ctx, uint32_t us) {
    auto *m = static_cast<Model *>(ctx);
    m->delays.push_back(us);
    m->at_delay.push_back(
        Snapshot{m->regs[GpuGxBcr / 4], m->regs[GpuccGxDomainMisc / 4]});
  }

  GpuccRegs regs_view() { return GpuccRegs{regs.data()}; }
  GpuccBoard board() {
    return GpuccBoard{rail_present ? RailOn : nullptr, Delay, this};
  }
  uint32_t at(uint32_t offset) const { return regs[offset / 4]; }
  void set(uint32_t offset, uint32_t value) { regs[offset / 4] = value; }
};

}  // namespace

int main() {
  // ---- the frequency table is the v2 one ------------------------------------
  CHECK(kGfx3dV2Count == 8);
  // The eight rates the device tree reports, in the same order, read off the phone.
  const uint32_t dt[8] = {180000000u, 257000000u, 342000000u, 414000000u,
                          515000000u, 596000000u, 670000000u, 710000000u};
  for (size_t i = 0; i < 8; ++i) CHECK(kGfx3dV2[i].core_hz == dt[i]);
  // Every entry's PLL target is exactly twice the core rate, which is what the even
  // post-divider produces.
  for (size_t i = 0; i < kGfx3dV2Count; ++i)
    CHECK(kGfx3dV2[i].pll_hz == 2 * kGfx3dV2[i].core_hz);
  // qcom,initial-pwrlevel is 6, and table index 6 is 670 MHz in the DT ordering... but
  // the DT lists levels high-to-low, so its index 6 is the second slowest, 257 MHz, which
  // is this table's index 1.  Check the rate, not the index, to keep that clear.
  CHECK(kGfx3dInitialLevel < kGfx3dV2Count);
  CHECK(kGfx3dV2[1].core_hz == 257000000u);

  // ---- the gate table covers the five CBCRs inside GPUCC --------------------
  CHECK(kGpuccGateCount == 5);
  bool saw_gfx3d = false;
  for (size_t i = 0; i < kGpuccGateCount; ++i) {
    CHECK(kGpuccGates[i].cbcr_offset != 0);
    CHECK(kGpuccGates[i].cbcr_offset < 0x9000);
    if (std::strcmp(kGpuccGates[i].name, "gpucc_gfx3d_clk") == 0) {
      saw_gfx3d = true;
      CHECK(kGpuccGates[i].cbcr_offset == GpuccGfx3dCbcr);
    }
  }
  CHECK(saw_gfx3d);

  // ---- a healthy power-up ---------------------------------------------------
  {
    Model m;
    // Powered off: both domains collapsed, GX reset and clamp asserted.
    m.set(GpuCxGdscr, GdscrSwCollapse);
    m.set(GpuGxGdscr, GdscrSwCollapse);
    m.set(GpuccGxDomainMisc, GxDomainReset | GxDomainClampIo);

    CHECK(GpuccPowerUp(m.regs_view(), m.board()));
    CHECK(m.rail_on_calls == 1);

    // Both GDSCRs end up out of collapse; reset and clamp are released.
    CHECK((m.at(GpuCxGdscr) & GdscrSwCollapse) == 0);
    CHECK((m.at(GpuGxGdscr) & GdscrSwCollapse) == 0);
    CHECK((m.at(GpuccGxDomainMisc) & GxDomainReset) == 0);
    CHECK((m.at(GpuccGxDomainMisc) & GxDomainClampIo) == 0);
    // GX block reset ends released, and the gfx3d gate is on.
    CHECK(m.at(GpuGxBcr) == 0);
    CHECK((m.at(GpuccGfx3dCbcr) & 1u) == 1u);

    // Four settling points, in order: 10 us after CX comes up, 1 us with GX block reset
    // held, 1 us with the GX domain reset held, 10 us after GX comes up.
    CHECK(m.delays.size() == 4);
    CHECK(m.delays[0] == 10);
    CHECK(m.delays[1] == 1);
    CHECK(m.delays[2] == 1);
    CHECK(m.delays[3] == 10);

    // Ordering, witnessed by those snapshots.
    //
    // At the first 1 us the GX block reset is held, and the domain reset has not been
    // touched yet -- it starts from the powered-off value set above, which does have the
    // reset bit set, so both are asserted at that instant.
    CHECK(m.at_delay[1].bcr == 1u);
    CHECK((m.at_delay[1].misc & GxDomainReset) != 0);
    // At the second 1 us the block reset has been released and the domain reset is what
    // is being held.
    CHECK(m.at_delay[2].bcr == 0u);
    CHECK((m.at_delay[2].misc & GxDomainReset) != 0);

    // The rail comes on after CX is out of collapse, before GX is touched.
    CHECK(m.cx_at_rail.size() == 1);
    CHECK((m.cx_at_rail[0] & GdscrSwCollapse) == 0);
  }

  // ---- no rail callback is a failure, not a silent skip ---------------------
  {
    Model m;
    m.set(GpuCxGdscr, GdscrSwCollapse);
    m.set(GpuGxGdscr, GdscrSwCollapse);
    m.rail_present = false;
    CHECK(!GpuccPowerUp(m.regs_view(), m.board()));
    // CX was brought up before the rail step failed, matching the kernel's order...
    CHECK((m.at(GpuCxGdscr) & GdscrSwCollapse) == 0);
    // ...and GX was never touched.
    CHECK((m.at(GpuGxGdscr) & GdscrSwCollapse) != 0);
    CHECK(m.at(GpuGxBcr) == 0);
  }

  // ---- a null register file is refused -------------------------------------
  {
    Model m;
    GpuccRegs none{nullptr};
    CHECK(!GpuccPowerUp(none, m.board()));
    CHECK(m.rail_on_calls == 0);
  }

  if (failures == 0) std::printf("Gpucc: all checks passed\n");
  return failures == 0 ? 0 : 1;
}
