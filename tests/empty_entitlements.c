// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <stddef.h>
#include <arpa/inet.h>
#define BCM2837 1
#define XNU_DIAGNOSTIC_EMPTY_ENTITLEMENTS 1
#define CSSLOT_ENTITLEMENTS 5
#define ENOTSUP 45
static void *amfi;
struct directory { unsigned int nSpecialSlots; };
struct cs_blob {
  void *csb_entitlements;
  void *csb_entitlements_blob;
  void *csb_der_entitlements_blob;
  struct directory *csb_cd;
};
static int check(struct cs_blob *cs_blob) {
#include "guard.inc"
  return 1234;
}
static void *query(void) {
#include "queryguard.inc"
  return (void *)1;
}
int main(void) {
  struct directory cd = {0};
  struct cs_blob blob = {0,0,0,&cd};
  cd.nSpecialSlots = htonl(2);
  if (query() != NULL) return 7;
  if (check(&blob) != 0) return 1;
  blob.csb_entitlements = (void *)1;
  if (check(&blob) != ENOTSUP) return 2;
  blob.csb_entitlements = 0;
  blob.csb_entitlements_blob = (void *)1;
  if (check(&blob) != ENOTSUP) return 3;
  blob.csb_entitlements_blob = 0;
  blob.csb_der_entitlements_blob = (void *)1;
  if (check(&blob) != ENOTSUP) return 4;
  blob.csb_der_entitlements_blob = 0;
  cd.nSpecialSlots = htonl(5);
  if (check(&blob) != ENOTSUP) return 5;
  amfi = (void *)1;
  if (query() != (void *)1) return 8;
  if (check(&blob) != 1234) return 6;
  return 0;
}
