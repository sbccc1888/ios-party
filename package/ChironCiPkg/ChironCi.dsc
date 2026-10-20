## SPDX-License-Identifier: BSD-2-Clause-Patent
# Compile the upstream manager and DarwinBoot with one compatible EDK II base.
!include UniXNUPkg/UniXNUPkg.dsc

[Components]
  DarwinBootPkg/Driver/DarwinBoot.inf
