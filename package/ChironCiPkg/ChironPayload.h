// SPDX-License-Identifier: BSD-2-Clause-Patent
#ifndef CHIRON_PAYLOAD_H
#define CHIRON_PAYLOAD_H

#define CHIRON_PAYLOAD_GUID \
  {0x2658cc1f,0x2a6f,0x47ba,{0xad,0xdf,0x36,0x4a,0xb8,0x09,0x50,0x62}}
#define CHIRON_PAYLOAD_MAGIC 0x314d41524e524843ULL

typedef struct {
  unsigned long long Magic;
  unsigned long long Base;
  unsigned long long Bytes;
} CHIRON_PAYLOAD;

static inline int
ChironPayloadBoundsValid (unsigned long long Base, unsigned long long Bytes)
{
  return Base >= 0x40000000ULL && !(Base & 4095ULL) &&
         Bytes >= 0x100000ULL && Bytes <= 0x8000000ULL &&
         !(Bytes & 511ULL) && Base <= ~0ULL - Bytes;
}

static inline int
ChironPayloadFatValid (const unsigned char *Sector, unsigned long long Bytes)
{
  unsigned int Index;
  unsigned int Total;
  const char Label[] = "CHIRONESP  ";
  const char Type[] = "FAT32   ";
  if (!Sector || Bytes < 512 || Sector[510] != 0x55 || Sector[511] != 0xaa ||
      Sector[11] != 0 || Sector[12] != 2 || Sector[16] != 2 ||
      Sector[17] != 0 || Sector[18] != 0) {
    return 0;
  }
  Total = (unsigned int)Sector[32] | ((unsigned int)Sector[33] << 8) |
          ((unsigned int)Sector[34] << 16) | ((unsigned int)Sector[35] << 24);
  if ((unsigned long long)Total * 512 != Bytes) {
    return 0;
  }
  for (Index = 0; Index < 11; Index++) {
    if (Sector[71 + Index] != (unsigned char)Label[Index]) { return 0; }
  }
  for (Index = 0; Index < 8; Index++) {
    if (Sector[82 + Index] != (unsigned char)Type[Index]) { return 0; }
  }
  return 1;
}

static inline int
ChironPayloadReadValid (unsigned long long Total, unsigned long long Lba, unsigned long long Bytes)
{
  if (Bytes == 0) { return 1; }
  if ((Bytes & 511ULL) || Total < 512 || Lba >= Total / 512) { return 0; }
  return Bytes / 512 <= Total / 512 - Lba;
}

#endif
