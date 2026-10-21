// SPDX-License-Identifier: BSD-2-Clause-Patent
// Included by the old platform's MemoryPeim after its resource HOBs exist.
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <libfdt.h>
#include "ChironPayload.h"

STATIC EFI_GUID mChironPayloadGuid = CHIRON_PAYLOAD_GUID;

STATIC BOOLEAN
ChironFdtAddress (CONST VOID *Blob, INT32 Chosen, CONST CHAR8 *Name, UINT64 *Value)
{
  INT32 Length;
  CONST VOID *Property;
  UINT32 Short;
  UINT64 Wide;
  Property = fdt_getprop (Blob, Chosen, Name, &Length);
  if (Property == NULL) { return FALSE; }
  if (Length == 4) {
    CopyMem (&Short, Property, sizeof (Short));
    *Value = SwapBytes32 (Short);
    return TRUE;
  }
  if (Length == 8) {
    CopyMem (&Wide, Property, sizeof (Wide));
    *Value = SwapBytes64 (Wide);
    return TRUE;
  }
  return FALSE;
}

STATIC VOID
ChironReservePayload (fdt *Parsed, UINT64 HeapBase, UINT64 HeapBytes)
{
  CONST VOID *Blob;
  INT32 Chosen;
  UINT64 Start;
  UINT64 End;
  uint64_t BankBase;
  uint64_t BankBytes;
  UINT64 FdBase;
  UINT64 FdBytes;
  UINTN Index;
  BOOLEAN InRam;
  CHIRON_PAYLOAD Payload;

  // Exclude the actual firmware and measured persistent log from owned-memory allocation.
  FdBase = FixedPcdGet64 (PcdFdBaseAddress);
  FdBytes = FixedPcdGet32 (PcdFdSize);
  if (HeapBytes > MAX_UINT64 - HeapBase || FdBytes > MAX_UINT64 - FdBase) { return; }
  BuildMemoryAllocationHob (FdBase, FdBytes, EfiReservedMemoryType);
  BuildMemoryAllocationHob (0xb0000000ULL, 0x200000ULL, EfiReservedMemoryType);

  Blob = (CONST VOID *)(UINTN)(*(EFI_PHYSICAL_ADDRESS *)(UINTN)FixedPcdGet64 (PcdDeviceTreeStore));
  if (Blob == NULL || fdt_check_header (Blob) != 0) { return; }
  Chosen = fdt_path_offset (Blob, "/chosen");
  if (Chosen < 0 ||
      !ChironFdtAddress (Blob, Chosen, "linux,initrd-start", &Start) ||
      !ChironFdtAddress (Blob, Chosen, "linux,initrd-end", &End) ||
      End <= Start || !ChironPayloadBoundsValid (Start, End - Start)) {
    return;
  }
  if ((Start < HeapBase + HeapBytes && HeapBase < End) ||
      (Start < FdBase + FdBytes && FdBase < End) ||
      (Start < 0xb0200000ULL && 0xb0000000ULL < End)) {
    return;
  }
  InRam = FALSE;
  Index = 0;
  while (fdt_get_memory (Parsed, (int)Index, &BankBase, &BankBytes)) {
    if (BankBytes <= MAX_UINT64 - BankBase && Start >= BankBase && End <= BankBase + BankBytes) {
      InRam = TRUE;
      break;
    }
    Index++;
  }
  if (!InRam || !ChironPayloadFatValid ((CONST unsigned char *)(UINTN)Start, End - Start)) {
    return;
  }
  BuildMemoryAllocationHob (Start, ALIGN_VALUE (End - Start, EFI_PAGE_SIZE), EfiReservedMemoryType);
  Payload.Magic = CHIRON_PAYLOAD_MAGIC;
  Payload.Base = Start;
  Payload.Bytes = End - Start;
  BuildGuidDataHob (&mChironPayloadGuid, &Payload, sizeof (Payload));
}
