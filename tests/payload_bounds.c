// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "ChironPayload.h"

int main(void) {
  unsigned char sector[512] = {0};
  unsigned int i;
  const char label[] = "CHIRONESP  ";
  const char type[] = "FAT32   ";
  if (!ChironPayloadBoundsValid(0x41000000ULL, 0x4000000ULL)) return 1;
  if (ChironPayloadBoundsValid(0x41000001ULL, 0x4000000ULL)) return 2;
  if (ChironPayloadBoundsValid(0xfffffffffffff000ULL, 0x4000000ULL)) return 3;
  if (ChironPayloadBoundsValid(0x41000000ULL, 0x10000000ULL)) return 4;
  if (!ChironPayloadReadValid(0x4000000ULL, 0, 512)) return 5;
  if (!ChironPayloadReadValid(0x4000000ULL, 0x1ffff, 512)) return 6;
  if (ChironPayloadReadValid(0x4000000ULL, 0x1ffff, 1024)) return 7;
  if (ChironPayloadReadValid(0x4000000ULL, 0xffffffffffffffffULL, 512)) return 8;
  if (ChironPayloadReadValid(0x4000000ULL, 0, 513)) return 9;
  sector[11] = 0; sector[12] = 2; sector[16] = 2;
  sector[34] = 2;
  sector[510] = 0x55; sector[511] = 0xaa;
  for (i = 0; i < 11; i++) sector[71+i] = label[i];
  for (i = 0; i < 8; i++) sector[82+i] = type[i];
  if (!ChironPayloadFatValid(sector, 0x4000000ULL)) return 10;
  sector[71] = 'X';
  if (ChironPayloadFatValid(sector, 0x4000000ULL)) return 11;
  sector[71] = 'C';
  if (ChironPayloadFatValid(sector, 0x2000000ULL)) return 12;
  return 0;
}
