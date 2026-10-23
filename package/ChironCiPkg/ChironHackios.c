// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <Uefi.h>
#include <Guid/FileInfo.h>
#include <Protocol/BlockIo.h>
#include <Protocol/FirmwareVolume2.h>
#include <Protocol/SimpleFileSystem.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DevicePathLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiApplicationEntryPoint.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>

#define PAYLOAD_BASE 0xF0000000ULL
#define PAYLOAD_MAX  0x03F00000ULL

STATIC EFI_GUID mQcomUsbGuid =
  {0xd9d9ce48,0x44b8,0x4f49,{0x8e,0x3e,0x2a,0x3b,0x92,0x7d,0xc6,0xc1}};
STATIC EFI_GUID mUsbDeviceFileGuid =
  {0x3299a266,0x15f0,0x4346,{0x83,0x18,0x71,0x63,0x36,0x73,0x6d,0x3e}};

STATIC EFI_STATUS
PrepareUsb(EFI_HANDLE Parent)
{
  EFI_HANDLE *Handles;
  EFI_HANDLE Image;
  UINTN Count;
  UINTN Index;
  VOID *Usb;
  EFI_STATUS Status;
  EFI_FIRMWARE_VOLUME2_PROTOCOL *Fv;
  EFI_FV_FILETYPE Type;
  EFI_FV_FILE_ATTRIBUTES Attributes;
  UINT32 Authentication;
  UINTN Size;
  MEDIA_FW_VOL_FILEPATH_DEVICE_PATH Node;
  EFI_DEVICE_PATH_PROTOCOL *Path;
  EFI_DEVICE_PATH_PROTOCOL *Base;

  Status = gBS->LocateProtocol(&mQcomUsbGuid, NULL, &Usb);
  Print(L"HK2: Qualcomm USB protocol: %r\n", Status);
  if (!EFI_ERROR(Status)) return EFI_SUCCESS;
  if (Status != EFI_NOT_FOUND) return Status;

  Status = gBS->LocateHandleBuffer(ByProtocol, &gEfiFirmwareVolume2ProtocolGuid,
                                  NULL, &Count, &Handles);
  if (EFI_ERROR(Status)) return Status;
  Status = EFI_NOT_FOUND;
  for (Index = 0; Index < Count; Index++) {
    Status = gBS->HandleProtocol(Handles[Index], &gEfiFirmwareVolume2ProtocolGuid,
                                (VOID **)&Fv);
    if (EFI_ERROR(Status)) continue;
    Size = 0;
    Status = Fv->ReadFile(Fv, &mUsbDeviceFileGuid, NULL, &Size, &Type,
                         &Attributes, &Authentication);
    if (EFI_ERROR(Status)) continue;
    Base = DevicePathFromHandle(Handles[Index]);
    if (Base == NULL) { Status = EFI_NOT_FOUND; continue; }
    EfiInitializeFwVolDevicepathNode(&Node, &mUsbDeviceFileGuid);
    Path = AppendDevicePathNode(Base, (EFI_DEVICE_PATH_PROTOCOL *)&Node);
    if (Path == NULL) { Status = EFI_OUT_OF_RESOURCES; break; }
    Print(L"HK2: loading Qualcomm UsbDeviceDxe from firmware\n");
    Status = gBS->LoadImage(FALSE, Parent, Path, NULL, 0, &Image);
    FreePool(Path);
    Print(L"HK2: load UsbDeviceDxe: %r\n", Status);
    if (EFI_ERROR(Status)) break;
    Status = gBS->StartImage(Image, NULL, NULL);
    Print(L"HK2: start UsbDeviceDxe: %r\n", Status);
    if (EFI_ERROR(Status)) break;
    Status = gBS->LocateProtocol(&mQcomUsbGuid, NULL, &Usb);
    Print(L"HK2: Qualcomm USB protocol after start: %r\n", Status);
    break;
  }
  FreePool(Handles);
  return Status;
}

STATIC EFI_STATUS
ShowFailure(CONST CHAR16 *Stage, EFI_STATUS Status)
{
  EFI_INPUT_KEY Key;
  UINTN Index;
  Print(L"\nHK2 FAIL %s\n%r  [%016lx]\n", Stage, Status, (UINT64)Status);
  Print(L"Take a photo of this screen. Press a key to return.\n");
  if (gST->ConIn != NULL && gST->ConIn->WaitForKey != NULL) {
    while (gST->ConIn->ReadKeyStroke(gST->ConIn, &Key) == EFI_SUCCESS) { }
    gBS->WaitForEvent(1, &gST->ConIn->WaitForKey, &Index);
    gST->ConIn->ReadKeyStroke(gST->ConIn, &Key);
  } else {
    gBS->Stall(15000000);
  }
  return Status;
}

STATIC EFI_STATUS
ReadPayload(EFI_FILE_PROTOCOL *Root)
{
  EFI_FILE_PROTOCOL *File;
  EFI_STATUS Status;
  EFI_FILE_INFO *Info = NULL;
  UINTN InfoSize = 0;
  UINTN Bytes;
  EFI_PHYSICAL_ADDRESS Address = PAYLOAD_BASE;
  UINT32 *Header;

  Status = Root->Open(Root, &File, L"\\EFI\\HackiOS\\payload.bin", EFI_FILE_MODE_READ, 0);
  if (EFI_ERROR(Status)) return Status;
  Print(L"HK2: payload file found\n");
  Status = File->GetInfo(File, &gEfiFileInfoGuid, &InfoSize, NULL);
  if (Status != EFI_BUFFER_TOO_SMALL || InfoSize > 4096) {
    Status = EFI_COMPROMISED_DATA;
    goto Done;
  }
  Info = AllocatePool(InfoSize);
  if (Info == NULL) { Status = EFI_OUT_OF_RESOURCES; goto Done; }
  Status = File->GetInfo(File, &gEfiFileInfoGuid, &InfoSize, Info);
  if (EFI_ERROR(Status)) goto Done;
  if (Info->FileSize < 32 || Info->FileSize > PAYLOAD_MAX) {
    Status = EFI_BAD_BUFFER_SIZE;
    goto Done;
  }
  Status = gBS->AllocatePages(AllocateAddress, EfiLoaderData,
                             EFI_SIZE_TO_PAGES(0x04000000), &Address);
  if (EFI_ERROR(Status)) {
    Print(L"HK2: reserve 64 MiB at %lx failed: %r\n", Address, Status);
    goto Done;
  }
  Bytes = (UINTN)Info->FileSize;
  Status = File->Read(File, &Bytes, (VOID *)(UINTN)Address);
  Header = (UINT32 *)(UINTN)Address;
  if (EFI_ERROR(Status) || Bytes != Info->FileSize || Header[0] != 0x50554e58 ||
      (UINT64)Header[1] + 8 != Info->FileSize) {
    gBS->FreePages(Address, EFI_SIZE_TO_PAGES(0x04000000));
    Status = EFI_COMPROMISED_DATA;
    goto Done;
  }
  Print(L"HackiOS payload: %u bytes at %lx\n", (UINT32)Bytes, Address);
Done:
  if (Info != NULL) FreePool(Info);
  File->Close(File);
  return Status;
}

STATIC EFI_STATUS
StartLoader(EFI_HANDLE Parent, EFI_HANDLE Volume)
{
  EFI_DEVICE_PATH_PROTOCOL *Path;
  EFI_HANDLE Image;
  EFI_STATUS Status;

  Path = FileDevicePath(Volume, L"\\EFI\\HackiOS\\loader.efi");
  if (Path == NULL) return ShowFailure(L"LOADER PATH", EFI_OUT_OF_RESOURCES);
  Print(L"HK2: loading loader from RAM filesystem\n");
  Status = gBS->LoadImage(FALSE, Parent, Path, NULL, 0, &Image);
  FreePool(Path);
  if (EFI_ERROR(Status)) return ShowFailure(L"LOAD LOADER", Status);
  Print(L"HK2: starting native Qualcomm USB loader; UEFI remains resident\n");
  Status = gBS->StartImage(Image, NULL, NULL);
  return ShowFailure(L"START LOADER", Status);
}

EFI_STATUS EFIAPI
UefiMain(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_HANDLE *Handles;
  UINTN Count;
  EFI_STATUS Status;
  UINTN Index;

  (VOID)SystemTable;
  Print(L"\nHK2 EFI ENTRY: chiron / 8796 / 20F66\n");
  Status = PrepareUsb(ImageHandle);
  if (EFI_ERROR(Status)) return ShowFailure(L"PREPARE QUALCOMM USB", Status);
  Status = gBS->LocateHandleBuffer(ByProtocol, &gEfiSimpleFileSystemProtocolGuid,
                                  NULL, &Count, &Handles);
  if (EFI_ERROR(Status)) return ShowFailure(L"ENUM FILESYSTEMS", Status);
  for (Index = 0; Index < Count; Index++) {
    EFI_BLOCK_IO_PROTOCOL *Block;
    EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *Fs;
    EFI_FILE_PROTOCOL *Root;
    Status = gBS->HandleProtocol(Handles[Index], &gEfiBlockIoProtocolGuid, (VOID **)&Block);
    if (EFI_ERROR(Status) || Block->Media == NULL || !Block->Media->ReadOnly) continue;
    Status = gBS->HandleProtocol(Handles[Index], &gEfiSimpleFileSystemProtocolGuid, (VOID **)&Fs);
    if (EFI_ERROR(Status)) continue;
    Status = Fs->OpenVolume(Fs, &Root);
    if (EFI_ERROR(Status)) continue;
    Status = ReadPayload(Root);
    if (!EFI_ERROR(Status)) {
      Status = StartLoader(ImageHandle, Handles[Index]);
      Root->Close(Root);
      FreePool(Handles);
      return Status;
    }
    if (Status != EFI_NOT_FOUND) {
      Root->Close(Root);
      FreePool(Handles);
      return ShowFailure(L"READ PAYLOAD", Status);
    }
    Root->Close(Root);
  }
  FreePool(Handles);
  Print(L"No valid HackiOS payload on the read-only RAM volume: %r\n", Status);
  return ShowFailure(L"FIND RAM PAYLOAD", EFI_NOT_FOUND);
}
