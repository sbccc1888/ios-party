// SPDX-License-Identifier: BSD-2-Clause-Patent
// Included after ChironReservePayload, while the PEI allocation HOBs are writable.
#include "ChironMemoryWindow.h"

STATIC EFI_GUID mChironMemoryWindowGuid = CHIRON_MEMORY_WINDOW_GUID;

STATIC BOOLEAN
ChironWindowDisjoint(UINT64 Base, UINT64 Bytes, UINT64 Other, UINT64 Length)
{
  if (Bytes > MAX_UINT64 - Base || Length > MAX_UINT64 - Other) return FALSE;
  return Length == 0 || Base + Bytes <= Other || Other + Length <= Base;
}

STATIC BOOLEAN
ChironWindowHeapSafe(UINT64 HeapBase, UINT64 HeapBytes)
{
  return HeapBase == 0xd8000000ULL && HeapBytes == 0x08000000ULL &&
         ChironWindowDisjoint(CHIRON_MEMORY_WINDOW_BASE, CHIRON_MEMORY_WINDOW_BYTES,
                              HeapBase, HeapBytes);
}

STATIC BOOLEAN
ChironWindowFdtReservationsSafe(CONST VOID *Blob, UINT64 Base, UINT64 Bytes)
{
  INT32 Node;
  INT32 Child;
  INT32 Length;
  INT32 Offset;
  INT32 Count;
  CONST UINT8 *Reg;
  UINT64 Address;
  UINT64 Size;
  uint64_t ReservedBase;
  uint64_t ReservedBytes;

  Count = fdt_num_mem_rsv(Blob);
  if (Count < 0) return FALSE;
  for (Offset = 0; Offset < Count; Offset++) {
    if (fdt_get_mem_rsv(Blob, Offset, &ReservedBase, &ReservedBytes) != 0 ||
        !ChironWindowDisjoint(Base, Bytes, ReservedBase, ReservedBytes)) return FALSE;
  }
  Node = fdt_path_offset(Blob, "/reserved-memory");
  if (Node < 0 || fdt_address_cells(Blob, Node) != 2 ||
      fdt_size_cells(Blob, Node) != 2) return FALSE;
  for (Child = fdt_first_subnode(Blob, Node); Child >= 0;
       Child = fdt_next_subnode(Blob, Child)) {
    Reg = fdt_getprop(Blob, Child, "reg", &Length);
    if (Reg == NULL) {
      if (fdt_getprop(Blob, Child, "size", &Length) == NULL) continue;
      Reg = fdt_getprop(Blob, Child, "alloc-ranges", &Length);
      if (Reg == NULL) return FALSE;
    }
    if (Length <= 0 || Length % 16 != 0) return FALSE;
    for (Offset = 0; Offset < Length; Offset += 16) {
      CopyMem(&Address, Reg + Offset, sizeof(Address));
      CopyMem(&Size, Reg + Offset + 8, sizeof(Size));
      if (!ChironWindowDisjoint(Base, Bytes, SwapBytes64(Address), SwapBytes64(Size)))
        return FALSE;
    }
  }
  return Child == -FDT_ERR_NOTFOUND;
}

STATIC VOID
ChironReserveKernelWindow(fdt *Parsed, UINT64 HeapBase, UINT64 HeapBytes)
{
  CHIRON_MEMORY_WINDOW Window;
  CONST VOID *Blob;
  uint64_t BankBase;
  uint64_t BankBytes;
  UINTN Index;
  BOOLEAN InRam = FALSE;
  BOOLEAN Conventional = FALSE;
  VOID *Hob;
  EFI_HOB_MEMORY_ALLOCATION *Allocation;

  ZeroMem(&Window, sizeof(Window));
  Window.Magic = CHIRON_MEMORY_WINDOW_MAGIC;
  Window.Revision = 1;
  Window.Base = CHIRON_MEMORY_WINDOW_BASE;
  Window.Bytes = CHIRON_MEMORY_WINDOW_BYTES;
  Window.Status = EFI_UNSUPPORTED;
  Window.Stage = 1;
  if (!ChironWindowHeapSafe(HeapBase, HeapBytes)) goto Done;
  Window.Stage = 2;
  Blob = (CONST VOID *)(UINTN)(*(EFI_PHYSICAL_ADDRESS *)(UINTN)
                             FixedPcdGet64(PcdDeviceTreeStore));
  if (Blob == NULL || fdt_check_header(Blob) != 0) goto Done;
  Window.Stage = 3;
  for (Index = 0; fdt_get_memory(Parsed, (int)Index, &BankBase, &BankBytes); Index++) {
    if (BankBytes <= MAX_UINT64 - BankBase && Window.Base >= BankBase &&
        Window.Base + Window.Bytes <= BankBase + BankBytes) InRam = TRUE;
  }
  if (!InRam) goto Done;
  Window.Stage = 4;
  if (!ChironWindowFdtReservationsSafe(Blob, Window.Base, Window.Bytes)) goto Done;
  Window.Stage = 5;
  for (Hob = GetFirstHob(EFI_HOB_TYPE_MEMORY_ALLOCATION); Hob != NULL;
       Hob = GetNextHob(EFI_HOB_TYPE_MEMORY_ALLOCATION, GET_NEXT_HOB(Hob))) {
    Allocation = (EFI_HOB_MEMORY_ALLOCATION *)Hob;
    if (Allocation->AllocDescriptor.MemoryType == EfiConventionalMemory) {
      UINT64 Start = Allocation->AllocDescriptor.MemoryBaseAddress;
      UINT64 Length = Allocation->AllocDescriptor.MemoryLength;
      if (Length <= MAX_UINT64 - Start && Start <= Window.Base &&
          Start + Length >= Window.Base + Window.Bytes) Conventional = TRUE;
    } else if (!ChironWindowDisjoint(Window.Base, Window.Bytes,
                    Allocation->AllocDescriptor.MemoryBaseAddress,
                    Allocation->AllocDescriptor.MemoryLength)) {
      goto Done;
    }
  }
  if (!Conventional) goto Done;
  BuildMemoryAllocationHob(Window.Base, Window.Bytes, EfiLoaderData);
  Window.Status = EFI_SUCCESS;
  Window.Stage = 6;
Done:
  BuildGuidDataHob(&mChironMemoryWindowGuid, &Window, sizeof(Window));
}
