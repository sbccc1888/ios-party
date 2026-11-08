// SPDX-License-Identifier: BSD-2-Clause
#include "ChironAdrenoA540Gpucc.h"

namespace chiron_adreno {

// clock-gpu-8998.c:168, ftbl_gfx3d_clk_src_v2.
const GpuFreqLevel kGfx3dV2[] = {
    {180000000, 360000000},   {257000000, 514000000},
    {342000000, 684000000},   {414000000, 828000000},
    {515000000, 1030000000},  {596000000, 1192000000},
    {670000000, 1340000000},  {710000000, 1420000000},
};
const size_t kGfx3dV2Count = sizeof(kGfx3dV2) / sizeof(kGfx3dV2[0]);

// clock-gpu-8998.c:493, msm_clocks_gpucc_8998. The gfx3d clock source is exported by the
// separate gfxcc table, not this one, which is why only the gate and the three auxiliary
// RCGs appear here.
const GpuccGate kGpuccGates[] = {
    {"gpucc_cxo_clk", GpuccCxoCbcr},
    {"gpucc_rbbmtimer_clk", GpuccRbbmtimerCbcr},
    {"gpucc_gfx3d_isense_clk", GpuccGfx3dIsenseCbcr},
    {"gpucc_rbcpr_clk", GpuccRbcprCbcr},
    {"gpucc_gfx3d_clk", GpuccGfx3dCbcr},
};
const size_t kGpuccGateCount = sizeof(kGpuccGates) / sizeof(kGpuccGates[0]);

static void Delay(const GpuccBoard &board, uint32_t us) {
  if (board.delay_us != nullptr) board.delay_us(board.ctx, us);
}

bool GpuccPowerUp(const GpuccRegs &regs, const GpuccBoard &board) {
  if (regs.block == nullptr) return false;

  // Step 1: take the CX domain out of collapse.
  uint32_t cx = regs.block[GpuCxGdscr / 4];
  cx &= ~GdscrSwCollapse;
  regs.block[GpuCxGdscr / 4] = cx;
  Delay(board, 10);

  // Step 2: the graphics rail. Without it the GX domain will not come up, so a missing
  // callback is a real failure rather than something to pass over.
  if (board.rail_on == nullptr) return false;
  board.rail_on(board.ctx);

  // Step 3: assert GX block reset for at least 1 us, then release.
  regs.block[GpuGxBcr / 4] = 1u;
  Delay(board, 1);
  regs.block[GpuGxBcr / 4] = 0u;

  // Step 4: hold the domain in reset, then let it go.
  uint32_t misc = regs.block[GpuccGxDomainMisc / 4];
  misc |= GxDomainReset;
  regs.block[GpuccGxDomainMisc / 4] = misc;
  Delay(board, 1);
  misc &= ~GxDomainReset;
  regs.block[GpuccGxDomainMisc / 4] = misc;

  // Step 5: GMEM_RESET must be de-asserted before the domain is brought up.
  misc &= ~GxDomainClampIo;
  regs.block[GpuccGxDomainMisc / 4] = misc;

  // Step 6: take the GX domain out of collapse.
  uint32_t gx = regs.block[GpuGxGdscr / 4];
  gx &= ~GdscrSwCollapse;
  regs.block[GpuGxGdscr / 4] = gx;
  Delay(board, 10);

  // Step 7: enable the gfx3d clock gate.
  regs.block[GpuccGfx3dCbcr / 4] |= 1u;

  return true;
}

}  // namespace chiron_adreno
