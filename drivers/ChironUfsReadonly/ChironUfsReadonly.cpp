// SPDX-License-Identifier: BSD-2-Clause
#include "ChironUfsReadonly.h"
#include <IOKit/IOLib.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IOService.h>
#include <kern/clock.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>

namespace {
// Kept for the boot lifetime. No destructor/unload path can release live DMA.
IOMemoryMap *controllerMap;
uint32_t attempted;
struct XnuPort {
  uintptr_t base;
  uint8_t *dma;
  uint32_t read(unsigned offset) {
    __asm__ volatile("dsb sy" ::: "memory");
    uint32_t v = *reinterpret_cast<volatile uint32_t *>(base + offset);
    __asm__ volatile("dsb sy" ::: "memory");
    return v;
  }
  void write(unsigned offset, uint32_t v) {
    __asm__ volatile("dsb sy" ::: "memory");
    *reinterpret_cast<volatile uint32_t *>(base + offset) = v;
    __asm__ volatile("dsb sy" ::: "memory");
  }
  void toDevice(size_t bytes) {
    for (uintptr_t a = reinterpret_cast<uintptr_t>(dma), end = a + bytes; a < end; a += 64)
      __asm__ volatile("dc civac, %0" :: "r"(a) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
  }
  void fromDevice(size_t bytes) {
    __asm__ volatile("dsb sy" ::: "memory");
    for (uintptr_t a = reinterpret_cast<uintptr_t>(dma), end = a + bytes; a < end; a += 64)
      __asm__ volatile("dc ivac, %0" :: "r"(a) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
  }
  uint64_t nowUs() {
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    return ns / 1000;
  }
  void delay() { IODelay(2); }
};
bool property(DTEntry entry, const char *name, const void *expected, unsigned size) {
  const void *value = nullptr; unsigned length = 0;
  if (SecureDTGetProperty(entry, name, &value, &length) != kSuccess || length != size) return false;
  const uint8_t *a = static_cast<const uint8_t *>(value);
  const uint8_t *b = static_cast<const uint8_t *>(expected);
  for (unsigned i = 0; i < size; ++i) if (a[i] != b[i]) return false;
  return true;
}
bool adt(const chiron_ufs::Handoff &h) {
  DTEntry entry;
  if (SecureDTLookupEntry(nullptr, "/arm-io/ufs-ro", &entry) != kSuccess) return false;
  // Explicit absolute addresses: unlike standard arm-io reg, these are not bus-relative.
  uint64_t host[] = {h.hostBase, h.hostSize}, phy[] = {h.phyBase, h.phySize};
  uint32_t soc[] = {h.socId, h.socRevision};
  const char compat[] = "chiron,ufs-readonly-v1";
  return property(entry, "compatible", compat, sizeof(compat)) &&
    property(entry, "host-physical-range", host, sizeof(host)) &&
    property(entry, "phy-physical-range", phy, sizeof(phy)) &&
    property(entry, "qcom,msm-id", soc, sizeof(soc)) &&
    property(entry, "lanes-per-direction", &h.lanes, sizeof(h.lanes));
}
}

extern "C" int chiron_ufs_readonly_probe(const chiron_ufs::Handoff *h, chiron_ufs::Report *out) {
  using namespace chiron_ufs;
  if (!out) return Unqualified;
  *out = {};
  uint32_t enabled = 0;
  if (!PE_parse_boot_argn("chiron-ufs-readonly", &enabled, sizeof(enabled)) || enabled != 1)
    return out->status = Disabled;
  if (!h || !qualified(*h)) return out->status = Unqualified;
  if (!resources(*h) || !adt(*h)) return out->status = ResourceMismatch;
  uint64_t ctr;
  __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
  if (((ctr >> 16) & 15) != 4) return out->status = Unqualified;
  if (__atomic_exchange_n(&attempted, 1, __ATOMIC_ACQ_REL)) return out->status = AlreadyAttempted;
  IODeviceMemory *memory = IODeviceMemory::withRange(h->hostBase, h->hostSize);
  if (!memory) return out->status = Unqualified;
  controllerMap = memory->map(kIOMapAnywhere | kIOMapInhibitCache);
  memory->release();
  if (!controllerMap) return out->status = Unqualified;
  XnuPort port{static_cast<uintptr_t>(controllerMap->getVirtualAddress()), h->dma};
  Probe<XnuPort> probe(port, *h, *out);
  int result = probe.run();
  IOLog("CHIRON_UFS_RO result=%d stage=%u cmds=%u op=%02x lun=%u "
        "ocs=%x scsi=%x sense=%x hcs=%x is=%x db=%x gpt=%u retained=%u\n",
        result, out->stage, out->commands, out->opcode, out->lun, out->ocs,
        out->scsi, out->sense, out->hcs, out->interrupt, out->doorbell,
        out->gptMatched, out->quarantined);
  for (unsigned i = 0; i < out->lunCount; ++i) {
    const Lun &l = out->luns[i];
    IOLog("CHIRON_UFS_RO lun=%u vendor=%s product=%s rev=%s last_lba=%llu block=%u\n",
          l.id, l.vendor, l.product, l.revision, static_cast<unsigned long long>(l.lastLba), l.blockBytes);
  }
  return result;
}
