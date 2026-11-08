// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <PiDxe.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>
#include <Protocol/GraphicsOutput.h>
#include <Library/HobLib.h>
#include "ChironPayload.h"

#define READ_REGISTER(Name, Value) __asm__ volatile ("mrs %0, " #Name : "=r" (Value))

/*
 * CHIRON_GPU_REGISTER_PROBE
 *
 * Reads the A540's registers from the firmware and prints them, without writing any of them.
 * Six layers of the A540 port agree with the MSM8998 kernel sources; this is the first thing
 * that can disagree with the phone.
 *
 * Read-only on purpose. Bringing the GPU up from here, while Android's kgsl may still hold the
 * block, is a step that hangs with nothing to show for it.
 *
 * Five stages, printed as they go, so a run that stops at stage N still shows stages 1..N-1 on
 * the panel. Ordered by risk rather than by tidiness: GPUCC at 0x05065000 has been in the chiron
 * memory map all along, so it goes first, and the GPU block at 0x05000000 and the SMMU at
 * 0x05040000 -- the two this port added -- come after, where their failure cannot hide the
 * values from the block that was always mapped.
 *
 * Every offset is a word offset from a5xx_reg.h multiplied by four, taken from the set the
 * kernel reads in adreno_a5xx.c around line 104 -- the function that dumps device state once
 * something has gone wrong -- rather than from what looked plausible.
 */
#define GPU_REG(word)   (0x05000000ULL + (UINT64)(word) * 4ULL)
#define GPUCC_REG(off)  (0x05065000ULL + (UINT64)(off))

STATIC
UINT32
GpuRead32 (
  IN UINT64 Address
  )
{
  return *(volatile UINT32 *)(UINTN)Address;
}

STATIC
VOID
ChironGpuRegisterProbe (
  VOID
  )
{
  UINT32  A;
  UINT32  B;
  UINT32  C;
  UINT32  D;
  UINT32  E;

  Print (L"\r\n==== A540 GPU probe (read only) ====\r\n");

  /* ---- stage 1: GPUCC, the block that was always mapped ---- */
  Print (L"stage 1: GPUCC\r\n");
  A = GpuRead32 (GPUCC_REG (0x01004));   /* CX GDSCR   */
  B = GpuRead32 (GPUCC_REG (0x01094));   /* GX GDSCR   */
  C = GpuRead32 (GPUCC_REG (0x01090));   /* GX BCR     */
  D = GpuRead32 (GPUCC_REG (0x00130));   /* GX domain  */
  E = GpuRead32 (GPUCC_REG (0x01098));   /* gfx3d gate */
  Print (L"  CX_GDSCR=%08x GX_GDSCR=%08x BCR=%08x DOMAIN=%08x GATE=%08x\r\n",
         A, B, C, D, E);
  A = GpuRead32 (GPUCC_REG (0x01020));   /* CXO gate   */
  B = GpuRead32 (GPUCC_REG (0x01070));   /* gfx3d RCGR */
  Print (L"  CXO_CBCR=%08x GFX3D_RCGR=%08x\r\n", A, B);

  /* ---- stage 2: GPU status, the first read into the added window ---- */
  Print (L"stage 2: GPU block status\r\n");
  A = GpuRead32 (GPU_REG (0x4F5));       /* RBBM_STATUS       */
  B = GpuRead32 (GPU_REG (0x530));       /* RBBM_STATUS3      */
  C = GpuRead32 (GPU_REG (0x4E1));       /* RBBM_INT_0_STATUS */
  D = GpuRead32 (GPU_REG (0xB1A));       /* CP_HW_FAULT       */
  Print (L"  RBBM_STATUS=%08x STATUS3=%08x INT_0=%08x CP_HW_FAULT=%08x\r\n",
         A, B, C, D);

  /* ---- stage 3: GMU ---- */
  Print (L"stage 3: GMU\r\n");
  A = GpuRead32 (GPU_REG (0xA8D8));      /* CM3_SYSRESET */
  B = GpuRead32 (GPU_REG (0xA8E0));      /* GENERAL_0    */
  C = GpuRead32 (GPU_REG (0xA8E1));      /* GENERAL_1    */
  D = GpuRead32 (GPU_REG (0xA8D6));      /* RBBM_INTR    */
  Print (L"  SYSRESET=%08x GENERAL_0=%08x GENERAL_1=%08x RBBM_INTR=%08x\r\n",
         A, B, C, D);
  Print (L"  (GENERAL_0 == BABEFACE means the GMU firmware is running)\r\n");

  /* ---- stage 4: CP ring ---- */
  Print (L"stage 4: CP ring\r\n");
  A = GpuRead32 (GPU_REG (0x800));       /* RB_BASE */
  B = GpuRead32 (GPU_REG (0x802));       /* RB_CNTL */
  C = GpuRead32 (GPU_REG (0x806));       /* RB_RPTR */
  D = GpuRead32 (GPU_REG (0x807));       /* RB_WPTR */
  E = GpuRead32 (GPU_REG (0x832));       /* ME_CNTL */
  Print (L"  RB_BASE=%08x CNTL=%08x RPTR=%08x WPTR=%08x ME_CNTL=%08x\r\n",
         A, B, C, D, E);

  /* ---- stage 5: SMMU context bank 0 ---- */
  Print (L"stage 5: SMMU\r\n");
  A = GpuRead32 (0x05048000);            /* SCTLR */
  B = GpuRead32 (0x05048004);            /* ACTLR */
  C = GpuRead32 (0x05048020);            /* TTBR0 */
  D = GpuRead32 (0x05048030);            /* TTBCR */
  Print (L"  SCTLR=%08x ACTLR=%08x TTBR0=%08x TTBCR=%08x\r\n", A, B, C, D);
  Print (L"  (ACTLR == 70000000 means qcom,smmu-v2, not a stock SMMU-500)\r\n");

  Print (L"==== end of A540 GPU probe: all five stages completed ====\r\n");
}
EFI_STATUS
EFIAPI
ChironProbeEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS                    Status;
  EFI_GRAPHICS_OUTPUT_PROTOCOL   *Gop;
  EFI_MEMORY_DESCRIPTOR         *Map;
  EFI_MEMORY_DESCRIPTOR         *Entry;
  UINTN                         Size;
  UINTN                         Key;
  UINTN                         DescriptorSize;
  UINT32                        DescriptorVersion;
  UINTN                         Offset;
  UINTN                         Shown;
  UINTN                         Event;
  EFI_INPUT_KEY                 Input;
  UINT64                        El;
  UINT64                        Midr;
  UINT64                        Mpidr;
  UINT64                        Mmfr0;
  UINT64                        Mmfr1;
  UINT64                        Isar0;
  UINT64                        Frequency;
  EFI_GUID                      PayloadGuid = CHIRON_PAYLOAD_GUID;
  VOID                          *PayloadHob;
  CHIRON_PAYLOAD                 Payload;

  (VOID)ImageHandle;
  (VOID)SystemTable;
  READ_REGISTER (CurrentEL, El);
  Print (L"\r\nChiron read-only probe\r\nCurrentEL=%lx\r\n", El);
  if (El < 4) {
    Print (L"Privileged CPU register reads unavailable.\r\n");
    return EFI_UNSUPPORTED;
  }
  READ_REGISTER (midr_el1, Midr);
  READ_REGISTER (mpidr_el1, Mpidr);
  READ_REGISTER (id_aa64mmfr0_el1, Mmfr0);
  READ_REGISTER (id_aa64mmfr1_el1, Mmfr1);
  READ_REGISTER (id_aa64isar0_el1, Isar0);
  READ_REGISTER (cntfrq_el0, Frequency);
  Print (L"MIDR=%016lx MPIDR=%016lx\r\n", Midr, Mpidr);
  Print (L"MMFR0=%016lx MMFR1=%016lx\r\n", Mmfr0, Mmfr1);
  Print (L"ISAR0=%016lx CNTFRQ=%lu\r\n", Isar0, Frequency);
  Print (L"TGran4(raw)=%lx TGran16(raw)=%lx\r\n",
         (Mmfr0 >> 28) & 15, (Mmfr0 >> 20) & 15);
  PayloadHob = GetFirstGuidHob (&PayloadGuid);
  if (PayloadHob != NULL && GET_GUID_HOB_DATA_SIZE (PayloadHob) == sizeof (Payload)) {
    Payload = *(CHIRON_PAYLOAD *)GET_GUID_HOB_DATA (PayloadHob);
    Print (L"RAM payload base=%016lx bytes=%lu\r\n", Payload.Base, Payload.Bytes);
  } else {
    Print (L"RAM payload not reserved\r\n");
  }

  Status = gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Gop);
  if (!EFI_ERROR (Status) && Gop->Mode != NULL && Gop->Mode->Info != NULL) {
    Print (L"GOP %ux%u pitch=%u format=%u\r\n",
           Gop->Mode->Info->HorizontalResolution,
           Gop->Mode->Info->VerticalResolution,
           Gop->Mode->Info->PixelsPerScanLine,
           Gop->Mode->Info->PixelFormat);
    Print (L"GOP base=%016lx bytes=%lu\r\n",
           (UINT64)Gop->Mode->FrameBufferBase, (UINT64)Gop->Mode->FrameBufferSize);
  } else {
    Print (L"GOP unavailable: %r\r\n", Status);
  }

  Size = 0;
  DescriptorSize = 0;
  Status = gBS->GetMemoryMap (&Size, NULL, &Key, &DescriptorSize, &DescriptorVersion);
  if (Status != EFI_BUFFER_TOO_SMALL || DescriptorSize < sizeof (EFI_MEMORY_DESCRIPTOR)) {
    return EFI_ERROR (Status) ? Status : EFI_COMPROMISED_DATA;
  }
  if (DescriptorSize > MAX_UINTN / 8 || Size > MAX_UINTN - 8 * DescriptorSize) {
    return EFI_OUT_OF_RESOURCES;
  }
  Size += 8 * DescriptorSize;
  Map = AllocatePool (Size);
  if (Map == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }
  Status = gBS->GetMemoryMap (&Size, Map, &Key, &DescriptorSize, &DescriptorVersion);
  if (!EFI_ERROR (Status) &&
      (DescriptorSize < sizeof (EFI_MEMORY_DESCRIPTOR) || Size % DescriptorSize != 0)) {
    Status = EFI_COMPROMISED_DATA;
  }
  if (!EFI_ERROR (Status)) {
    Print (L"UEFI conventional memory (base, pages):\r\n");
    Shown = 0;
    for (Offset = 0; Offset + DescriptorSize <= Size; Offset += DescriptorSize) {
      Entry = (EFI_MEMORY_DESCRIPTOR *)((UINT8 *)Map + Offset);
      if (Entry->Type == EfiConventionalMemory) {
        if (Shown < 12) {
          Print (L"  %016lx  %lx\r\n", Entry->PhysicalStart, Entry->NumberOfPages);
        }
        Shown++;
      }
    }
    Print (L"Conventional regions=%u; first 12 shown.\r\n", (UINT32)Shown);
  } else {
    Print (L"GetMemoryMap: %r\r\n", Status);
  }
  FreePool (Map);
  ChironGpuRegisterProbe ();

  if (gST->ConIn != NULL) {
    Print (L"Press a key to return.\r\n");
    gBS->WaitForEvent (1, &gST->ConIn->WaitForKey, &Event);
    gST->ConIn->ReadKeyStroke (gST->ConIn, &Input);
  }
  return Status;
}
