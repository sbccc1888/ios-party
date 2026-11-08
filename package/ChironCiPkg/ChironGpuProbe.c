// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Reads the A540's registers without writing any of them, and reports what it finds.
//
// This is the first chance to check the port's assumptions against silicon rather than
// against the kernel sources. Every address below comes from ChironAdrenoA540Gpucc.h,
// ChironAdrenoA540Gmu.h, ChironAdrenoA540CpStart.h and ChironAdrenoA540Smmu.h, and the
// question this answers is whether those are the right addresses on this part.
//
// Read-only on purpose. Powering the GPU up from UEFI while Android's kgsl may still hold
// the block is the kind of thing that produces a hang with no diagnostic; reading first
// establishes that the windows are mapped and that the values look like a GPU before
// anything is written.
#include <PiDxe.h>
#include <Library/UefiLib.h>
#include <Library/UefiBootServicesTableLib.h>

#define GPU_BASE   0x05000000ULL
#define SMMU_BASE  0x05040000ULL
#define GPUCC_BASE 0x05065000ULL

// From ChironAdrenoA540Gpucc.h
#define GPU_CX_GDSCR       0x01004
#define GPU_GX_GDSCR       0x01094
#define GPU_GX_BCR         0x01090
#define GPUCC_GX_DOMAIN    0x00130
#define GPUCC_GFX3D_CBCR   0x01098
#define GPUCC_CXO_CBCR     0x01020
#define GPUCC_GFX3D_RCGR   0x01070

// From ChironAdrenoA540Gmu.h, word offsets -- multiply by four before use.
#define GPMU_CM3_SYSRESET  0xA8D8
#define GPMU_GENERAL_0     0xA8E0
#define GPMU_GENERAL_1     0xA8E1
#define GPMU_RBBM_INTR     0xA8D6

// From ChironAdrenoA540CpStart.h, word offsets.
#define CP_RB_BASE         0x800
#define CP_RB_CNTL         0x802
#define CP_RB_RPTR_ADDR_LO 0x804
#define CP_RB_RPTR         0x806
#define CP_RB_WPTR         0x807
#define CP_ME_CNTL         0x832

// SMMU context bank 0, from ChironAdrenoA540Smmu.h
#define SMMU_CB0           0x8000
#define SMMU_CB_SCTLR      0x00
#define SMMU_CB_ACTLR      0x04
#define SMMU_CB_TTBR0      0x20
#define SMMU_CB_TTBCR      0x30

static UINT32
Read32 (
  IN UINT64 Address
  )
{
  // Device memory: the compiler must not cache or reorder these.
  return *(volatile UINT32 *)(UINTN)Address;
}

EFI_STATUS
EFIAPI
ChironGpuProbeEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  UINT32 Rev;
  UINT32 ChipId;
  UINT32 ProductId;
  UINT64 El;

  (VOID)ImageHandle;
  (VOID)SystemTable;

  __asm__ volatile ("mrs %0, CurrentEL" : "=r" (El));

  Print (L"\r\n==== Chiron A540 GPU probe (read only) ====\r\n");
  Print (L"CurrentEL=%lx  (need EL >= 2 to touch MMIO)\r\n", El);

  // A5XX_REVISION (0x0) and A5XX_PRODUCT_ID report the core. On an A540 the product id
  // is in the 0x0504 range, so a wild value here means the window is not what this port
  // thinks it is.
  Rev     = Read32 (GPU_BASE + 0x0);
  ChipId  = Read32 (GPU_BASE + 0x4);
  ProductId = Read32 (GPU_BASE + 0x8);
  Print (L"GPU_REVISION=%08x CHIP_ID=%08x PRODUCT_ID=%08x\r\n",
         Rev, ChipId, ProductId);

  // RBBM_STATUS tells whether the block is even clocked. A read of all ones or all zeros
  // is the usual signature of a window that is not mapped or is powered down.
  Print (L"RBBM_STATUS=%08x\r\n", Read32 (GPU_BASE + 0x7C));

  Print (L"-- power domains --\r\n");
  Print (L"GPU_CX_GDSCR=%08x  (bit0 collapse: %u)\r\n",
         Read32 (GPU_BASE + GPU_CX_GDSCR),
         (Read32 (GPU_BASE + GPU_CX_GDSCR) & 1u));
  Print (L"GPU_GX_GDSCR=%08x  (bit0 collapse: %u)\r\n",
         Read32 (GPU_BASE + GPU_GX_GDSCR),
         (Read32 (GPU_BASE + GPU_GX_GDSCR) & 1u));
  Print (L"GPU_GX_BCR=%08x GPUCC_GX_DOMAIN=%08x\r\n",
         Read32 (GPU_BASE + GPU_GX_BCR),
         Read32 (GPUCC_BASE + GPUCC_GX_DOMAIN));

  Print (L"-- clocks --\r\n");
  Print (L"GPUCC_GFX3D_CBCR=%08x  (bit0 on: %u)\r\n",
         Read32 (GPUCC_BASE + GPUCC_GFX3D_CBCR),
         (Read32 (GPUCC_BASE + GPUCC_GFX3D_CBCR) & 1u));
  Print (L"GPUCC_CXO_CBCR=%08x GPUCC_GFX3D_RCGR=%08x\r\n",
         Read32 (GPUCC_BASE + GPUCC_CXO_CBCR),
         Read32 (GPUCC_BASE + GPUCC_GFX3D_RCGR));

  Print (L"-- GMU --\r\n");
  Print (L"GPMU_CM3_SYSRESET=%08x\r\n",
         Read32 (GPU_BASE + GPMU_CM3_SYSRESET * 4));
  Print (L"GPMU_GENERAL_0=%08x  (0xBABEFACE means firmware running)\r\n",
         Read32 (GPU_BASE + GPMU_GENERAL_0 * 4));
  Print (L"GPMU_GENERAL_1=%08x GPMU_RBBM_INTR=%08x\r\n",
         Read32 (GPU_BASE + GPMU_GENERAL_1 * 4),
         Read32 (GPU_BASE + GPMU_RBBM_INTR * 4));

  Print (L"-- CP ring --\r\n");
  Print (L"CP_RB_BASE=%08x CP_RB_CNTL=%08x\r\n",
         Read32 (GPU_BASE + CP_RB_BASE * 4),
         Read32 (GPU_BASE + CP_RB_CNTL * 4));
  Print (L"CP_RB_RPTR=%08x CP_RB_WPTR=%08x\r\n",
         Read32 (GPU_BASE + CP_RB_RPTR * 4),
         Read32 (GPU_BASE + CP_RB_WPTR * 4));
  Print (L"CP_ME_CNTL=%08x  (bit0 ME_HALT)\r\n",
         Read32 (GPU_BASE + CP_ME_CNTL * 4));

  Print (L"-- SMMU context bank 0 --\r\n");
  Print (L"CB_SCTLR=%08x  (bit0 M: %u)\r\n",
         Read32 (SMMU_BASE + SMMU_CB0 + SMMU_CB_SCTLR),
         (Read32 (SMMU_BASE + SMMU_CB0 + SMMU_CB_SCTLR) & 1u));
  Print (L"CB_ACTLR=%08x  (expect 70000000 on qcom,smmu-v2)\r\n",
         Read32 (SMMU_BASE + SMMU_CB0 + SMMU_CB_ACTLR));
  Print (L"CB_TTBR0=%08x CB_TTBCR=%08x\r\n",
         Read32 (SMMU_BASE + SMMU_CB0 + SMMU_CB_TTBR0),
         Read32 (SMMU_BASE + SMMU_CB0 + SMMU_CB_TTBCR));

  Print (L"==== end of GPU probe ====\r\n");
  return EFI_SUCCESS;
}