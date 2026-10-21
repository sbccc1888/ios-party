// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <Uefi.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>
#include <Protocol/GraphicsOutput.h>
#include <Library/HobLib.h>
#include "ChironPayload.h"

#define READ_REGISTER(Name, Value) __asm__ volatile ("mrs %0, " #Name : "=r" (Value))

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
  if (gST->ConIn != NULL) {
    Print (L"Press a key to return.\r\n");
    gBS->WaitForEvent (1, &gST->ConIn->WaitForKey, &Event);
    gST->ConIn->ReadKeyStroke (gST->ConIn, &Input);
  }
  return Status;
}
