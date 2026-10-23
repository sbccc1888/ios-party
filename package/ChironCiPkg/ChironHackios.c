// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <Uefi.h>
#include <Guid/FileInfo.h>
#include <Protocol/BlockIo.h>
#include <Protocol/SimpleFileSystem.h>
#include <Library/BaseMemoryLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiApplicationEntryPoint.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>

#define PAYLOAD_BASE 0xF0000000ULL
#define PAYLOAD_MAX  0x03F00000ULL

// Same ABI as EmbeddedPkg's fixed AndroidFastbootTransport.h.
typedef struct {
  EFI_STATUS (*Start)(EFI_EVENT);
  EFI_STATUS (*Stop)(VOID);
  EFI_STATUS (*Send)(UINTN, CONST VOID *, EFI_EVENT *);
  EFI_STATUS (*Receive)(UINTN *, VOID **);
} FASTBOOT_TRANSPORT;

typedef struct {
  UINT64 Revision;
  EFI_STATUS (EFIAPI *Start)(VOID);
  EFI_STATUS (EFIAPI *Receive)(UINTN *, VOID **);
  EFI_STATUS (EFIAPI *Send)(UINTN, CONST VOID *);
} CHIRON_USB_TRANSPORT;

STATIC EFI_GUID mFastbootGuid =
  {0x74bd9fe0,0x8902,0x11e3,{0xb9,0xd3,0xf7,0x22,0x38,0xfc,0x9a,0x31}};
STATIC EFI_GUID mChironUsbGuid =
  {0x46037394,0xa01b,0x41fa,{0x83,0xcc,0xd8,0x45,0xd9,0x1c,0xae,0x70}};
STATIC FASTBOOT_TRANSPORT *mFastboot;
STATIC EFI_EVENT mReceiveEvent;
STATIC EFI_EVENT mFatalEvent;

STATIC EFI_STATUS EFIAPI
UsbStart(VOID)
{
  EFI_STATUS Status;
  Status = gBS->LocateProtocol(&mFastbootGuid, NULL, (VOID **)&mFastboot);
  if (EFI_ERROR(Status)) {
    Print(L"Existing EDK2 Fastboot transport unavailable: %r\n", Status);
    return Status;
  }
  Status = gBS->CreateEvent(0, TPL_APPLICATION, NULL, NULL, &mReceiveEvent);
  if (EFI_ERROR(Status)) return Status;
  Status = gBS->CreateEvent(0, TPL_APPLICATION, NULL, NULL, &mFatalEvent);
  if (EFI_ERROR(Status)) return Status;
  return mFastboot->Start(mReceiveEvent);
}

STATIC EFI_STATUS EFIAPI
UsbReceive(UINTN *Size, VOID **Buffer)
{
  if (gBS->CheckEvent(mFatalEvent) == EFI_SUCCESS) return EFI_DEVICE_ERROR;
  return mFastboot->Receive(Size, Buffer);
}

STATIC EFI_STATUS EFIAPI
UsbSend(UINTN Size, CONST VOID *Buffer)
{
  return mFastboot->Send(Size, Buffer, &mFatalEvent);
}

STATIC CHIRON_USB_TRANSPORT mUsb = {1, UsbStart, UsbReceive, UsbSend};

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
  if (EFI_ERROR(Status)) goto Done;
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
StartLoader(EFI_HANDLE Parent, EFI_FILE_PROTOCOL *Root)
{
  EFI_FILE_PROTOCOL *File;
  VOID *Bytes;
  UINTN Size = 1024 * 1024;
  EFI_HANDLE Image;
  EFI_STATUS Status;

  Status = Root->Open(Root, &File, L"\\EFI\\HackiOS\\loader.efi", EFI_FILE_MODE_READ, 0);
  if (EFI_ERROR(Status)) return Status;
  Bytes = AllocatePool(Size);
  if (Bytes == NULL) { File->Close(File); return EFI_OUT_OF_RESOURCES; }
  Status = File->Read(File, &Size, Bytes);
  File->Close(File);
  if (!EFI_ERROR(Status) && Size > 512) {
    Status = gBS->LoadImage(FALSE, Parent, NULL, Bytes, Size, &Image);
    if (!EFI_ERROR(Status)) {
      Print(L"Starting chiron HackiOS USB loader; UEFI remains resident.\n");
      Status = gBS->StartImage(Image, NULL, NULL);
    }
  }
  FreePool(Bytes);
  return Status;
}

EFI_STATUS EFIAPI
UefiMain(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_HANDLE *Handles;
  UINTN Count;
  EFI_STATUS Status;
  EFI_HANDLE UsbHandle = NULL;
  UINTN Index;

  (VOID)SystemTable;
  Print(L"Chiron HackiOS 8796 / iOS 16.5\n");
  Status = gBS->InstallProtocolInterface(&UsbHandle, &mChironUsbGuid,
                                        EFI_NATIVE_INTERFACE, &mUsb);
  if (EFI_ERROR(Status)) return Status;
  Status = gBS->LocateHandleBuffer(ByProtocol, &gEfiSimpleFileSystemProtocolGuid,
                                  NULL, &Count, &Handles);
  if (EFI_ERROR(Status)) return Status;
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
      Status = StartLoader(ImageHandle, Root);
      Root->Close(Root);
      FreePool(Handles);
      return Status;
    }
    Root->Close(Root);
  }
  FreePool(Handles);
  Print(L"No valid HackiOS payload on the read-only RAM volume: %r\n", Status);
  return EFI_NOT_FOUND;
}
