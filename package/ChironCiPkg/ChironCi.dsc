## SPDX-License-Identifier: BSD-2-Clause-Patent
# Library bindings adapted from UniXNUPkg.dsc, Copyright (c) 2026 UniXNU contributors.
[Defines]
  PLATFORM_NAME           = UniXNU
  PLATFORM_GUID           = 1C1F7C2E-7A84-4F7B-9C55-2B7E4C0F5A31
  PLATFORM_VERSION        = 0.1
  DSC_SPECIFICATION       = 0x0001001C
  OUTPUT_DIRECTORY        = Build/UniXNU
  SUPPORTED_ARCHITECTURES = AARCH64
  BUILD_TARGETS           = DEBUG|RELEASE
  SKUID_IDENTIFIER        = DEFAULT

!include MdePkg/MdeLibs.dsc.inc

[LibraryClasses]
  BaseLib|MdePkg/Library/BaseLib/BaseLib.inf
  BaseMemoryLib|MdePkg/Library/BaseMemoryLib/BaseMemoryLib.inf
  DevicePathLib|MdePkg/Library/UefiDevicePathLib/UefiDevicePathLib.inf
  FdtLib|MdePkg/Library/BaseFdtLib/BaseFdtLib.inf
  MemoryAllocationLib|MdePkg/Library/UefiMemoryAllocationLib/UefiMemoryAllocationLib.inf
  PcdLib|MdePkg/Library/BasePcdLibNull/BasePcdLibNull.inf
  PrintLib|MdePkg/Library/BasePrintLib/BasePrintLib.inf
  UefiApplicationEntryPoint|MdePkg/Library/UefiApplicationEntryPoint/UefiApplicationEntryPoint.inf
  UefiBootServicesTableLib|MdePkg/Library/UefiBootServicesTableLib/UefiBootServicesTableLib.inf
  UefiDriverEntryPoint|MdePkg/Library/UefiDriverEntryPoint/UefiDriverEntryPoint.inf
  UefiLib|MdePkg/Library/UefiLib/UefiLib.inf
  UefiRuntimeServicesTableLib|MdePkg/Library/UefiRuntimeServicesTableLib/UefiRuntimeServicesTableLib.inf
  DebugPrintErrorLevelLib|MdePkg/Library/BaseDebugPrintErrorLevelLib/BaseDebugPrintErrorLevelLib.inf
  UniXNULib|UniXNUPkg/Library/UniXNULib/UniXNULib.inf
  HobLib|MdePkg/Library/DxeHobLib/DxeHobLib.inf
!if $(TARGET) == RELEASE
  DebugLib|MdePkg/Library/BaseDebugLibNull/BaseDebugLibNull.inf
!else
  DebugLib|MdePkg/Library/UefiDebugLibStdErr/UefiDebugLibStdErr.inf
!endif

[PcdsFixedAtBuild]
  gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x80000000
  gEfiMdePkgTokenSpaceGuid.PcdDebugPropertyMask|0x07

[Components]
  UniXNUPkg/Application/UniXNU/UniXNU.inf
  UniXNUPkg/Driver/UniXNUDxe/UniXNUDxe.inf
  UniXNUPkg/Test/HelloPayload/HelloPayload.inf
  UniXNUPkg/Test/SampleDriver/SampleDriver.inf
  UniXNUPkg/Test/XnuTest/XnuTest.inf
  DarwinBootPkg/Driver/DarwinBoot.inf
  ChironCiPkg/ChironProbe.inf
  ChironCiPkg/ChironRamdisk.inf
