// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <Uefi.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/DevicePathLib.h>
#include <Library/HobLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Protocol/BlockIo.h>
#include <Protocol/DevicePath.h>
#include "ChironPayload.h"

STATIC EFI_GUID mPayloadGuid = CHIRON_PAYLOAD_GUID;
STATIC CHIRON_PAYLOAD mPayload;
STATIC EFI_BLOCK_IO_MEDIA mMedia;
STATIC EFI_BLOCK_IO_PROTOCOL mBlock;
STATIC struct {
  VENDOR_DEVICE_PATH Vendor;
  EFI_DEVICE_PATH_PROTOCOL End;
} mPath;

STATIC EFI_STATUS EFIAPI
RamReset (EFI_BLOCK_IO_PROTOCOL *This, BOOLEAN Extended)
{
  (VOID)This;
  (VOID)Extended;
  return EFI_SUCCESS;
}

STATIC EFI_STATUS EFIAPI
RamRead (EFI_BLOCK_IO_PROTOCOL *This, UINT32 MediaId, EFI_LBA Lba, UINTN Bytes, VOID *Buffer)
{
  if (This != &mBlock) { return EFI_INVALID_PARAMETER; }
  if (MediaId != mMedia.MediaId) { return EFI_MEDIA_CHANGED; }
  if (Bytes == 0) { return EFI_SUCCESS; }
  if (Buffer == NULL) { return EFI_INVALID_PARAMETER; }
  if (Bytes % mMedia.BlockSize != 0) { return EFI_BAD_BUFFER_SIZE; }
  if (!ChironPayloadReadValid (mPayload.Bytes, Lba, Bytes)) {
    return EFI_INVALID_PARAMETER;
  }
  CopyMem (Buffer, (CONST VOID *)(UINTN)(mPayload.Base + Lba * mMedia.BlockSize), Bytes);
  return EFI_SUCCESS;
}

STATIC EFI_STATUS EFIAPI
RamWrite (EFI_BLOCK_IO_PROTOCOL *This, UINT32 MediaId, EFI_LBA Lba, UINTN Bytes, VOID *Buffer)
{
  (VOID)Lba;
  (VOID)Bytes;
  (VOID)Buffer;
  if (This != &mBlock) { return EFI_INVALID_PARAMETER; }
  if (MediaId != mMedia.MediaId) { return EFI_MEDIA_CHANGED; }
  return EFI_WRITE_PROTECTED;
}

STATIC EFI_STATUS EFIAPI
RamFlush (EFI_BLOCK_IO_PROTOCOL *This)
{
  return This == &mBlock ? EFI_SUCCESS : EFI_INVALID_PARAMETER;
}

EFI_STATUS EFIAPI
ChironRamdiskEntry (EFI_HANDLE Image, EFI_SYSTEM_TABLE *SystemTable)
{
  VOID *Hob;
  EFI_HANDLE Handle;
  EFI_STATUS Status;
  (VOID)Image;
  (VOID)SystemTable;
  Hob = GetFirstGuidHob (&mPayloadGuid);
  if (Hob == NULL) { return EFI_NOT_FOUND; }
  if (GET_GUID_HOB_DATA_SIZE (Hob) != sizeof (mPayload)) { return EFI_COMPROMISED_DATA; }
  CopyMem (&mPayload, GET_GUID_HOB_DATA (Hob), sizeof (mPayload));
  if (mPayload.Magic != CHIRON_PAYLOAD_MAGIC ||
      !ChironPayloadBoundsValid (mPayload.Base, mPayload.Bytes) ||
      !ChironPayloadFatValid ((CONST unsigned char *)(UINTN)mPayload.Base, mPayload.Bytes)) {
    return EFI_COMPROMISED_DATA;
  }
  ZeroMem (&mMedia, sizeof (mMedia));
  mMedia.MediaId = 0x43485231;
  mMedia.MediaPresent = TRUE;
  mMedia.ReadOnly = TRUE;
  mMedia.BlockSize = 512;
  mMedia.IoAlign = 1;
  mMedia.LastBlock = mPayload.Bytes / mMedia.BlockSize - 1;
  ZeroMem (&mBlock, sizeof (mBlock));
  mBlock.Revision = EFI_BLOCK_IO_PROTOCOL_REVISION;
  mBlock.Media = &mMedia;
  mBlock.Reset = RamReset;
  mBlock.ReadBlocks = RamRead;
  mBlock.WriteBlocks = RamWrite;
  mBlock.FlushBlocks = RamFlush;
  ZeroMem (&mPath, sizeof (mPath));
  mPath.Vendor.Header.Type = HARDWARE_DEVICE_PATH;
  mPath.Vendor.Header.SubType = HW_VENDOR_DP;
  SetDevicePathNodeLength (&mPath.Vendor.Header, sizeof (mPath.Vendor));
  CopyMem (&mPath.Vendor.Guid, &mPayloadGuid, sizeof (mPayloadGuid));
  SetDevicePathEndNode (&mPath.End);
  Handle = NULL;
  Status = gBS->InstallMultipleProtocolInterfaces (
      &Handle, &gEfiDevicePathProtocolGuid, &mPath,
      &gEfiBlockIoProtocolGuid, &mBlock, NULL);
  if (!EFI_ERROR (Status)) {
    gBS->ConnectController (Handle, NULL, NULL, TRUE);
  }
  return Status;
}
