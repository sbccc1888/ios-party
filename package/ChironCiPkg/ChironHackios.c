// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <PiDxe.h>
#include <Guid/FileInfo.h>
#include <Protocol/BlockIo.h>
#include <Protocol/DriverBinding.h>
#include <Protocol/FirmwareVolume2.h>
#include <Protocol/LoadedImage.h>
#include <Protocol/SimpleFileSystem.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DevicePathLib.h>
#include <Library/HobLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiApplicationEntryPoint.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>
#include "ChironMemoryWindow.h"

#define PAYLOAD_BASE 0xF0000000ULL
#define PAYLOAD_MAX  0x03F00000ULL

STATIC EFI_STATUS
PublishMemoryWindow(VOID)
{
  STATIC EFI_GUID Guid = CHIRON_MEMORY_WINDOW_GUID;
  VOID *Hob;
  CHIRON_MEMORY_WINDOW *Window;
  EFI_HANDLE Handle = NULL;

  Hob = GetFirstGuidHob(&Guid);
  if (Hob == NULL || GET_GUID_HOB_DATA_SIZE(Hob) != sizeof(*Window)) {
    Print(L"HK6: owned memory window HOB missing\n");
    return EFI_NOT_FOUND;
  }
  Window = GET_GUID_HOB_DATA(Hob);
  Print(L"HK6: owned window stage %u status %r base %lx bytes %lx\n",
        (UINT32)Window->Stage, (EFI_STATUS)Window->Status, Window->Base, Window->Bytes);
  if (Window->Magic != CHIRON_MEMORY_WINDOW_MAGIC || Window->Revision != 1 ||
      Window->Base != CHIRON_MEMORY_WINDOW_BASE ||
      Window->Bytes != CHIRON_MEMORY_WINDOW_BYTES || Window->Status != EFI_SUCCESS)
    return EFI_UNSUPPORTED;
  return gBS->InstallProtocolInterface(&Handle, &Guid, EFI_NATIVE_INTERFACE, Window);
}

STATIC EFI_GUID mQcomUsbGuid =
  {0xd9d9ce48,0x44b8,0x4f49,{0x8e,0x3e,0x2a,0x3b,0x92,0x7d,0xc6,0xc1}};
STATIC EFI_GUID mUsbDeviceFileGuid =
  {0x3299a266,0x15f0,0x4346,{0x83,0x18,0x71,0x63,0x36,0x73,0x6d,0x3e}};
STATIC EFI_GUID mUsbInitGuid =
  {0x2da15ac7,0x4909,0x4c4c,{0xb8,0xc3,0xa7,0x42,0x19,0x5e,0xa4,0x6e}};
STATIC EFI_GUID mUsbInitFileGuid =
  {0x0a134f0e,0x075e,0x40b3,{0x9c,0x63,0x3b,0x39,0x06,0x80,0x46,0x63}};
STATIC EFI_GUID mDwc3FileGuid =
  {0x94f8a6a7,0xdc34,0x4101,{0x88,0xc1,0x99,0x17,0x9c,0xce,0xae,0x83}};
STATIC EFI_GUID mUsbfnGuid =
  {0x32d2963a,0xfe5d,0x4f30,{0xb6,0x33,0x6e,0x5d,0xc5,0x58,0x03,0xcc}};
STATIC EFI_GUID mUsbConfigGuid =
  {0xe722b03f,0xb250,0x42ce,{0x8e,0xbd,0x5b,0xd5,0x18,0x12,0xd0,0x37}};
STATIC EFI_GUID mUsbConfigFileGuid =
  {0xcd823a4d,0x7dec,0x4531,{0xae,0x5d,0x41,0x34,0xfa,0x41,0x27,0xb8}};

// The vendor DWC3 Supported() reads CoreNum/ModeType at offsets 0x88/0x8c.
typedef struct {
  UINT32 Revision;
  VOID *Methods[16];
  UINT32 CoreNum;
  UINT32 ModeType;
  VOID *GetVbusStatus;
  VOID *EnableVbus;
} QCOM_USB_CONFIG_INFO;

STATIC_ASSERT(OFFSET_OF(QCOM_USB_CONFIG_INFO, CoreNum) == 0x88, "USB Config core ABI");
STATIC_ASSERT(OFFSET_OF(QCOM_USB_CONFIG_INFO, ModeType) == 0x8c, "USB Config mode ABI");
STATIC_ASSERT(sizeof(QCOM_USB_CONFIG_INFO) == 0xa0, "USB Config complete ABI");

// EFIUsbInitProtocol.h: the protocol chooses the default core when CoreNum is NULL.
typedef struct {
  UINT8 CoreNum;
  UINT32 ModeType;
} QCOM_USB_CORE_INFO;

typedef struct QCOM_USB_INIT QCOM_USB_INIT;
struct QCOM_USB_INIT {
  UINT64 Revision;
  EFI_STATUS (EFIAPI *QueryCoreCount)(QCOM_USB_INIT *, UINT8 *);
  EFI_STATUS (EFIAPI *QueryCoreInfo)(QCOM_USB_INIT *, UINT8 *, QCOM_USB_CORE_INFO *);
  EFI_STATUS (EFIAPI *StartController)(QCOM_USB_INIT *, UINT8, UINT32);
  EFI_STATUS (EFIAPI *StopController)(QCOM_USB_INIT *, UINT8, UINT32);
};

STATIC EFI_STATUS
FindFirmwareImage(EFI_GUID *FileGuid, EFI_HANDLE *Image)
{
  EFI_STATUS Status;
  EFI_HANDLE *Handles;
  UINTN Count;
  UINTN Index;
  EFI_LOADED_IMAGE_PROTOCOL *Loaded;
  EFI_DEVICE_PATH_PROTOCOL *Node;

  Status = gBS->LocateHandleBuffer(ByProtocol, &gEfiLoadedImageProtocolGuid,
                                  NULL, &Count, &Handles);
  if (EFI_ERROR(Status)) return Status;
  Status = EFI_NOT_FOUND;
  for (Index = 0; Index < Count; Index++) {
    if (EFI_ERROR(gBS->HandleProtocol(Handles[Index], &gEfiLoadedImageProtocolGuid,
                                      (VOID **)&Loaded))) continue;
    for (Node = Loaded->FilePath; Node != NULL && !IsDevicePathEnd(Node);
         Node = NextDevicePathNode(Node)) {
      if (DevicePathType(Node) == MEDIA_DEVICE_PATH &&
          DevicePathSubType(Node) == MEDIA_PIWG_FW_FILE_DP &&
          DevicePathNodeLength(Node) == sizeof(MEDIA_FW_VOL_FILEPATH_DEVICE_PATH) &&
          CompareGuid(&((MEDIA_FW_VOL_FILEPATH_DEVICE_PATH *)Node)->FvFileName, FileGuid)) {
        *Image = Handles[Index];
        Status = EFI_SUCCESS;
        break;
      }
    }
    if (!EFI_ERROR(Status)) break;
  }
  FreePool(Handles);
  return Status;
}

STATIC EFI_STATUS
LoadFirmwareDriver(EFI_HANDLE Parent, EFI_GUID *FileGuid,
                   CONST CHAR16 *Name, EFI_HANDLE *Image)
{
  EFI_HANDLE *Handles;
  UINTN Count;
  UINTN Index;
  EFI_STATUS Status;
  EFI_FIRMWARE_VOLUME2_PROTOCOL *Fv;
  EFI_FV_FILETYPE Type;
  EFI_FV_FILE_ATTRIBUTES Attributes;
  UINT32 Authentication;
  UINTN Size;
  MEDIA_FW_VOL_FILEPATH_DEVICE_PATH Node;
  EFI_DEVICE_PATH_PROTOCOL *Path;
  EFI_DEVICE_PATH_PROTOCOL *Base;

  Status = FindFirmwareImage(FileGuid, Image);
  Print(L"HK6: resident %s: %r\n", Name, Status);
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
    Status = Fv->ReadFile(Fv, FileGuid, NULL, &Size, &Type,
                         &Attributes, &Authentication);
    if (EFI_ERROR(Status)) continue;
    Base = DevicePathFromHandle(Handles[Index]);
    if (Base == NULL) { Status = EFI_NOT_FOUND; continue; }
    EfiInitializeFwVolDevicepathNode(&Node, FileGuid);
    Path = AppendDevicePathNode(Base, (EFI_DEVICE_PATH_PROTOCOL *)&Node);
    if (Path == NULL) { Status = EFI_OUT_OF_RESOURCES; break; }
    Print(L"HK6: loading %s from firmware\n", Name);
    Status = gBS->LoadImage(FALSE, Parent, Path, NULL, 0, Image);
    FreePool(Path);
    Print(L"HK6: load %s: %r\n", Name, Status);
    if (EFI_ERROR(Status)) break;
    Status = gBS->StartImage(*Image, NULL, NULL);
    Print(L"HK6: start %s: %r\n", Name, Status);
    break;
  }
  FreePool(Handles);
  return Status;
}

STATIC EFI_STATUS
CreatePeripheralController(QCOM_USB_INIT *Init,
                           EFI_DRIVER_BINDING_PROTOCOL *Binding,
                           QCOM_USB_CONFIG_INFO *Original, UINT8 CoreNum)
{
  EFI_STATUS Status;
  EFI_HANDLE ConfigImage;
  EFI_LOADED_IMAGE_PROTOCOL *Loaded;
  UINTN Base;
  QCOM_USB_CONFIG_INFO *Config;
  EFI_HANDLE Controller = NULL;
  EFI_HANDLE Drivers[2];
  VOID *Usbfn;

  if (Init->StopController == NULL || Original->Revision != 0x10003)
    return EFI_UNSUPPORTED;
  Status = FindFirmwareImage(&mUsbConfigFileGuid, &ConfigImage);
  if (EFI_ERROR(Status)) return Status;
  Status = gBS->HandleProtocol(ConfigImage, &gEfiLoadedImageProtocolGuid, (VOID **)&Loaded);
  if (EFI_ERROR(Status)) return Status;
  Base = (UINTN)Loaded->ImageBase;
  if (Loaded->ImageSize != 0x13000 ||
      (UINTN)Original->Methods[0] != Base + 0x1440 ||
      (UINTN)Original->Methods[1] != Base + 0x1514 ||
      (UINTN)Original->Methods[4] != Base + 0x1750) {
    Print(L"HK6: USB Config API differs from the pinned MSM8998 driver\n");
    return EFI_UNSUPPORTED;
  }
  Print(L"HK6: pinned USB Config public API verified\n");
  Config = AllocateCopyPool(sizeof(*Config), Original);
  if (Config == NULL) return EFI_OUT_OF_RESOURCES;
  Config->CoreNum = CoreNum;
  Config->ModeType = 0x04;
  Status = Init->StopController(Init, CoreNum, 0x01);
  Print(L"HK6: stop host controller: %r\n", Status);
  if (EFI_ERROR(Status)) { FreePool(Config); return Status; }

  // Keep the copied public table resident; every callback belongs to vendor DXE.
  Status = gBS->InstallProtocolInterface(&Controller, &mUsbConfigGuid,
                                        EFI_NATIVE_INTERFACE, Config);
  Print(L"HK6: create dedicated peripheral USB Config: %r\n", Status);
  if (EFI_ERROR(Status)) { FreePool(Config); return Status; }
  Status = Binding->Supported(Binding, Controller, NULL);
  Print(L"HK6: DWC3 Supported dedicated controller: %r\n", Status);
  if (EFI_ERROR(Status)) return Status;
  Drivers[0] = Binding->ImageHandle;
  Drivers[1] = NULL;
  Status = gBS->ConnectController(Controller, Drivers, NULL, FALSE);
  Print(L"HK6: connect dedicated DWC3 controller: %r\n", Status);
  if (EFI_ERROR(Status)) return Status;
  Status = gBS->HandleProtocol(Controller, &mUsbfnGuid, &Usbfn);
  Print(L"HK6: dedicated controller USB Function IO: %r\n", Status);
  return Status;
}

STATIC EFI_STATUS
ConnectUsbFunction(QCOM_USB_INIT *Init, EFI_DRIVER_BINDING_PROTOCOL *Binding,
                   UINT8 CoreNum)
{
  EFI_STATUS Status;
  EFI_HANDLE *Handles;
  EFI_HANDLE Drivers[2];
  UINTN Count;
  UINTN Index;
  UINTN Matches = 0;
  VOID *Usbfn;
  QCOM_USB_CONFIG_INFO *Config;
  QCOM_USB_CONFIG_INFO *HostConfig = NULL;
  EFI_OPEN_PROTOCOL_INFORMATION_ENTRY *Owners;
  UINTN OwnerCount;
  UINTN Owner;

  Status = gBS->LocateHandleBuffer(ByProtocol, &mUsbConfigGuid,
                                  NULL, &Count, &Handles);
  Print(L"HK6: enumerate USB Config controllers: %r\n", Status);
  if (EFI_ERROR(Status)) return Status;
  Drivers[0] = Binding->ImageHandle;
  Drivers[1] = NULL;
  for (Index = 0; Index < Count; Index++) {
    Status = gBS->HandleProtocol(Handles[Index], &mUsbConfigGuid, (VOID **)&Config);
    if (EFI_ERROR(Status)) continue;
    Print(L"HK6: USB cfg %u revision %x core %u mode %x\n",
          (UINT32)Index, Config->Revision, Config->CoreNum, Config->ModeType);
    if (Config->Revision == 0x10003 && Config->CoreNum == CoreNum &&
        Config->ModeType == 0x01) HostConfig = Config;
    if (Config->Revision < 0x10003 || Config->CoreNum != CoreNum ||
        Config->ModeType != 0x04) continue;
    Matches++;
    Status = gBS->HandleProtocol(Handles[Index], &mUsbfnGuid, &Usbfn);
    if (!EFI_ERROR(Status)) { FreePool(Handles); return EFI_SUCCESS; }
    Status = Binding->Supported(Binding, Handles[Index], NULL);
    Print(L"HK6: DWC3 Supported controller %u: %r\n", (UINT32)Index, Status);
    if (!EFI_ERROR(Status)) {
      Status = gBS->ConnectController(Handles[Index], Drivers, NULL, FALSE);
      Print(L"HK6: connect DWC3 controller %u: %r\n", (UINT32)Index, Status);
      Status = gBS->HandleProtocol(Handles[Index], &mUsbfnGuid, &Usbfn);
      Print(L"HK6: controller %u USB Function IO: %r\n", (UINT32)Index, Status);
      if (!EFI_ERROR(Status)) { FreePool(Handles); return EFI_SUCCESS; }
    }
    Status = gBS->OpenProtocolInformation(Handles[Index], &mUsbConfigGuid,
                                        &Owners, &OwnerCount);
    if (EFI_ERROR(Status)) continue;
    for (Owner = 0; Owner < OwnerCount; Owner++) {
      if ((Owners[Owner].Attributes & EFI_OPEN_PROTOCOL_BY_DRIVER) == 0) continue;
      Print(L"HK6: controller %u owner %p flags %x count %u DWC3=%u\n",
            (UINT32)Index, Owners[Owner].AgentHandle, Owners[Owner].Attributes,
            Owners[Owner].OpenCount,
            (UINT32)(Owners[Owner].AgentHandle == Binding->DriverBindingHandle));
    }
    FreePool(Owners);
  }
  FreePool(Handles);
  Print(L"HK6: matching peripheral controllers: %u\n", (UINT32)Matches);
  if (Matches == 0 && HostConfig != NULL)
    return CreatePeripheralController(Init, Binding, HostConfig, CoreNum);
  return EFI_NOT_FOUND;
}

STATIC EFI_STATUS
PrepareUsbFunction(EFI_HANDLE Parent)
{
  EFI_STATUS Status;
  EFI_STATUS StartStatus;
  VOID *Usbfn;
  QCOM_USB_INIT *Init;
  QCOM_USB_CORE_INFO Core;
  EFI_HANDLE Image;
  EFI_DRIVER_BINDING_PROTOCOL *Binding;

  Status = gBS->LocateProtocol(&mUsbfnGuid, NULL, &Usbfn);
  Print(L"HK6: USB Function IO before init: %r\n", Status);
  if (!EFI_ERROR(Status)) return EFI_SUCCESS;
  if (Status != EFI_NOT_FOUND) return Status;
  Status = gBS->LocateProtocol(&mUsbInitGuid, NULL, (VOID **)&Init);
  Print(L"HK6: USB Init protocol: %r\n", Status);
  if (Status == EFI_NOT_FOUND) {
    Status = LoadFirmwareDriver(Parent, &mUsbInitFileGuid, L"UsbInitDxe", &Image);
    if (EFI_ERROR(Status)) return Status;
    Status = gBS->LocateProtocol(&mUsbInitGuid, NULL, (VOID **)&Init);
    Print(L"HK6: USB Init after load: %r\n", Status);
  }
  if (EFI_ERROR(Status)) return Status;
  if (Init->Revision < 0x10000 || Init->QueryCoreInfo == NULL ||
      Init->StartController == NULL) return EFI_UNSUPPORTED;

  Status = LoadFirmwareDriver(Parent, &mDwc3FileGuid, L"UsbfnDwc3Dxe", &Image);
  if (EFI_ERROR(Status)) return Status;
  Status = gBS->HandleProtocol(Image, &gEfiDriverBindingProtocolGuid, (VOID **)&Binding);
  Print(L"HK6: DWC3 driver binding: %r\n", Status);
  if (EFI_ERROR(Status)) return Status;
  if (Binding->Supported == NULL || Binding->ImageHandle == NULL) return EFI_UNSUPPORTED;

  ZeroMem(&Core, sizeof(Core));
  Status = Init->QueryCoreInfo(Init, NULL, &Core);
  Print(L"HK6: query default USB core: %r\n", Status);
  if (EFI_ERROR(Status)) return Status;
  Print(L"HK6: USB core %u supported modes %x\n", (UINT32)Core.CoreNum, Core.ModeType);
  if ((Core.ModeType & (0x02 | 0x04)) == 0) return EFI_UNSUPPORTED;
  // The vendor API creates its peripheral handle and connects its driver stack.
  StartStatus = Init->StartController(Init, Core.CoreNum, 0x02);
  Print(L"HK6: start peripheral controller: %r\n", StartStatus);
  Status = gBS->LocateProtocol(&mUsbfnGuid, NULL, &Usbfn);
  Print(L"HK6: USB Function IO after init: %r\n", Status);
  if (EFI_ERROR(StartStatus)) return StartStatus;
  if (Status == EFI_NOT_FOUND) return ConnectUsbFunction(Init, Binding, Core.CoreNum);
  return Status;
}

STATIC EFI_STATUS
PrepareUsb(EFI_HANDLE Parent)
{
  EFI_STATUS Status;
  EFI_HANDLE Image;
  VOID *Usb;

  Status = gBS->LocateProtocol(&mQcomUsbGuid, NULL, &Usb);
  Print(L"HK6: Qualcomm USB protocol: %r\n", Status);
  if (Status == EFI_NOT_FOUND) {
    Status = LoadFirmwareDriver(Parent, &mUsbDeviceFileGuid, L"UsbDeviceDxe", &Image);
    if (EFI_ERROR(Status)) return Status;
    Status = gBS->LocateProtocol(&mQcomUsbGuid, NULL, &Usb);
    Print(L"HK6: Qualcomm USB after load: %r\n", Status);
  }
  if (EFI_ERROR(Status)) return Status;
  return PrepareUsbFunction(Parent);
}

STATIC EFI_STATUS
ShowFailure(CONST CHAR16 *Stage, EFI_STATUS Status)
{
  EFI_INPUT_KEY Key;
  UINTN Index;
  Print(L"\nHK6 FAIL %s\n%r  [%016lx]\n", Stage, Status, (UINT64)Status);
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
  Print(L"HK6: payload file found\n");
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
    Print(L"HK6: reserve 64 MiB at %lx failed: %r\n", Address, Status);
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
  Print(L"HK6: loading loader from RAM filesystem\n");
  Status = gBS->LoadImage(FALSE, Parent, Path, NULL, 0, &Image);
  FreePool(Path);
  if (EFI_ERROR(Status)) return ShowFailure(L"LOAD LOADER", Status);
  Print(L"HK6: starting native Qualcomm USB loader; UEFI remains resident\n");
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
  Print(L"\nHK6 EFI ENTRY: chiron / 8796 / 20F66\n");
  Status = PublishMemoryWindow();
  if (EFI_ERROR(Status)) return ShowFailure(L"OWNED MEMORY WINDOW", Status);
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
