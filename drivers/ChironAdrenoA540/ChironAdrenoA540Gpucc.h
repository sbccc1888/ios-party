// SPDX-License-Identifier: BSD-2-Clause
//
// MSM8998 GPUCC register map and the GPU power-up sequence.
//
// Sources, all fixed:
//   include/dt-bindings/clock/msm-clocks-hwio-8998.h   register offsets
//   drivers/clk/msm/clock-gpu-8998.c                   enable_gfx_crc(), frequency tables
//   drivers/gpu/msm/adreno-gpulist.h                    A540 chip identity
//
// chiron's device tree places this block at 0x05065000, size 0x9000
// (qcom,gpucc@5065000, compatible "qcom,gpucc-8998-v2").
//
// One register window is involved, and it took reading two device nodes to be sure:
//
//   qcom,gpucc@5065000  compatible "qcom,gpucc-8998"   reg = <0x5065000 0x9000>
//   qcom,gfxcc@5065000  compatible "qcom,gfxcc-8998"   reg = <0x5065000 0x9000>
//
// Both nodes sit at the same address and size, so the kernel's virt_base and
// virt_base_gfx are two mappings of one block, probed by two drivers. That is why
// enable_gfx_crc() reads GDSCRs through virt_base_gfx without contradiction: offsets
// 0x1004 and 0x1094 are inside the 0x9000 window either way.
//
// So there is a single base here, GPUCC at 0x05065000, and every offset below is from it.

#ifndef CHIRON_ADRENO_A540_GPUCC_H
#define CHIRON_ADRENO_A540_GPUCC_H

#include <stddef.h>
#include <stdint.h>

namespace chiron_adreno {

// ---- GPUCC block, offsets from 0x05065000 --------------------------------
enum : uint32_t {
  GpuccGpuPll0PllMode = 0x00000,
  GpuccGpuPll0UserCtlMode = 0x0000C,
  GpuccCxoCbcr = 0x01020,
  GpuccRbcprCmdRcgr = 0x01030,
  GpuccRbcprCbcr = 0x01054,
  GpuccGfx3dCmdRcgr = 0x01070,
  GpuccGfx3dCbcr = 0x01098,
  GpuccRbbmtimerCmdRcgr = 0x010B0,
  GpuccRbbmtimerCbcr = 0x010D0,
  GpuccGfx3dIsenseCmdRcgr = 0x01100,
  GpuccGfx3dIsenseCbcr = 0x01124,
  GpuccGxDomainMisc = 0x00130,
  GpuccDebugClkCtl = 0x00120,
  GpuccGpuDdWrapCtrl = 0x00430,
};

// ---- GPU power-domain registers, in the same 0x5065000 block ------------
// These look like they belong to the GPU at 0x05000000 but they do not: the kernel
// reaches them through the gfxcc mapping of the same GPUCC block.
enum : uint32_t {
  GpuCxGdscr = 0x01004,
  GpuGxBcr = 0x01090,
  GpuGxGdscr = 0x01094,
  GpuCrcSidFsm = 0x010A0,
  GpuCrcMndCfg = 0x010A4,
};

// GDSCR software-collapse bit. Set means collapsed (power off), clear means on.
enum : uint32_t { GdscrSwCollapse = 1u << 0 };

// GX domain miscellaneous control. Bit 0 asserts GMEM clamp, bit 4 holds the
// graphics domain in reset.
enum : uint32_t {
  GxDomainClampIo = 1u << 0,
  GxDomainReset = 1u << 4,
};

// Clock sources as described by the GPUCC RCGs.
enum : uint32_t {
  GpuccSourceCxo = 0,
  GpuccSourceGpll0 = 5,
  GpuccSourcePll0OutEven = 1,
  GpuccSourcePll0OutOdd = 2,
};

// One entry of a gfx3d frequency table. The kernel's F_SLEW() rows carry a half-rate
// figure and the PLL rate it wants the even divider to produce; the core clock is the
// half-rate value.
struct GpuFreqLevel {
  uint32_t core_hz;  // gfx3d_clk, the frequency the GPU runs at
  uint32_t pll_hz;   // gpu_pll0_pll_out_even target
};

// ftbl_gfx3d_clk_src_v2 from clock-gpu-8998.c. Ascending, which is the order the kernel
// table is written in.
//
// COUNT IS SEVEN, NOT EIGHT, and that is measured rather than derived. The kernel table
// lists eight rates including 180 MHz, and chiron's device tree also lists eight, but the
// running kernel reports:
//
//     gpu_available_frequencies = 710000000 670000000 596000000 515000000 414000000
//                                 342000000 257000000
//     num_pwrlevels = 7    min_clock_mhz = 257    max_clock_mhz = 710
//
// so 180 MHz is not a selectable level on this part. Programming it would ask the GPUCC
// for a rate the kernel never uses.
extern const GpuFreqLevel kGfx3dV2[];
extern const size_t kGfx3dV2Count;

// Power-level indices run the other way round from this table. The kernel's pwrlevels are
// ordered fastest first, and the device tree's qcom,initial-pwrlevel = 6 therefore names
// the *slowest* level:
//
//     num_pwrlevels = 7    default_pwrlevel = 6    min_pwrlevel = 6
//     min_clock_mhz = 257  max_clock_mhz = 710
//
// So level 6 is 257 MHz, which is this ascending table's index 0. Reading 6 as an index
// into kGfx3dV2 would pick 670 MHz and start the part nearly three times too fast.
enum : uint32_t {
  kGfx3dPwrLevelCount = 7,
  kGfx3dInitialPwrLevel = 6,     // kernel numbering, fastest-first
  kGfx3dInitialTableIndex = 0,   // the same level as an index into kGfx3dV2
};

// Converts a kernel power level to an index into kGfx3dV2. Level 0 is the fastest.
inline uint32_t Gfx3dTableIndexFromPwrLevel(uint32_t level) {
  return level >= kGfx3dPwrLevelCount ? 0u : (kGfx3dPwrLevelCount - 1 - level);
}

// Which clock each named GPUCC CBCR gates. Order matches the device tree's clock-names
// list, which is core_clk, iface_clk, rbbmtimer_clk, mem_clk, mem_iface_clk, isense_clk,
// rbcpr_clk, iref_clk -- but only five of those have their own CBCR inside GPUCC; the
// rest come from GCC/MMSS and are not part of this block.
struct GpuccGate {
  const char *name;
  uint32_t cbcr_offset;
};

extern const GpuccGate kGpuccGates[];
extern const size_t kGpuccGateCount;

// Register operations, in the order enable_gfx_crc() performs them. The caller supplies
// the two windows and the two OD callbacks, so the sequence can run against real
// hardware or against a recording model in tests.
struct GpuccRegs {
  // The GPUCC block at 0x05065000. One pointer is enough: the GDSCRs, the GX reset and
  // the GX domain control all live inside its 0x9000 bytes.
  volatile uint32_t *block;
};

// Callbacks for the two things the sequence needs from outside the register file:
// the GPU vdd rail (vdd_gfx) and a microsecond delay. Either may be null, in which case
// the step is skipped and recorded as such.
struct GpuccBoard {
  void (*rail_on)(void *ctx);
  void (*delay_us)(void *ctx, uint32_t us);
  void *ctx;
};

// The subset of enable_gfx_crc() that leaves the GPU powered and clocked, which is what a
// driver needs before touching the GMU. The kernel's function goes further -- it runs the
// whole CRC calibration and then powers everything back down -- so this stops after the
// point the sequence is up and running.
//
// Steps, in order:
//   1. clear GDSCR SW_COLLAPSE on the CX domain, wait 10 us
//   2. enable the graphics rail
//   3. assert GX block reset (write 1 to GPU_GX_BCR), wait 1 us, deassert
//   4. assert then release the GX domain reset bit, with 1 us between
//   5. clear the GMEM clamp bit
//   6. clear GDSCR SW_COLLAPSE on the GX domain, wait 10 us
//   7. enable the gfx3d clock gate
//
// Returns false if a step that must not be skipped was skipped because the board
// callbacks were missing.
bool GpuccPowerUp(const GpuccRegs &regs, const GpuccBoard &board);

}  // namespace chiron_adreno

#endif  // CHIRON_ADRENO_A540_GPUCC_H
