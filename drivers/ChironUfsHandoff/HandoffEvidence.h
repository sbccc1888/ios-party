// SPDX-License-Identifier: BSD-2-Clause
#ifndef CHIRON_UFS_HANDOFF_EVIDENCE_H
#define CHIRON_UFS_HANDOFF_EVIDENCE_H

#include "../ChironUfsReadonly/ChironUfsReadonly.h"

namespace chiron_ufs_handoff {
enum : uint32_t { EvidenceAbi = 1 };
enum class Phase : uint32_t { Unknown, Firmware, ExitCallback, KernelPostEbs };
enum class State : uint32_t { Unknown, Observed, Failed };
enum Fact : uint32_t {
  PowerRetained, ClocksRetained, PhyReady, LinkActive, FirmwareDrained,
  SubmissionsBlocked, InterruptOwned, FirmwareDmaRetired, BootServicesExited,
  DmaRouteKnown, DmaPermissions, CacheContract, LeaseRetained, FactCount
};
enum class Result : uint32_t {
  Missing, Malformed, ResourceMismatch, WrongBoot, WrongPhase, Stale,
  InvalidLease, FailedObservation, MetadataConsistent
};

struct Identity {
  uint8_t boot[16];
  uint64_t generation;
};
struct Stamp {
  Identity identity;
  uint64_t ticks;
  Phase phase;
};
struct Resources {
  uint64_t hostBase, hostSize, phyBase, phySize;
  uint64_t gccBase, gccSize, gdscBase, gdscSize;
  uint32_t socId, socRevision, lanes, spi, irqFlags, intid, hostClocks;
};
inline Resources measuredResources() {
  return {0x1da4000, 0x2500, 0x1da7000, 0xda8,
          0x100000, 0xb0000, 0x175004, 4, 292, 0x20001, 2, 265, 0, 297, 9};
}
struct Observation {
  Stamp stamp;
  State state;
};
// This describes a lease held by the board, not an allocation or a mapping.
struct Lease {
  uint64_t token, cpu, bus, bytes;
  uint32_t addressBits, cacheLine;
  bool cached, deviceRead, deviceWrite, retainedUntilBootEnd;
};
struct Evidence {
  uint32_t abi;
  Resources resources;
  Stamp stamp;
  Lease lease;
  Observation facts[FactCount];
};
// Must originate from current runtime ownership/clock objects, separately from
// the evidence packet. Copying packet fields into this context proves nothing.
struct Context {
  Stamp current;
  uint64_t ebsReturnedTicks, maxAgeTicks;
  Lease heldLease;
  bool exitBootServicesReturned;
};
struct Assessment {
  Result result;
  uint32_t observed, unknown, failed;
  uint32_t verified;
  bool hardwareExecutionAllowed;
};
inline bool identityValid(const Identity &id) {
  unsigned any = 0;
  for (unsigned i = 0; i < 16; ++i) any |= id.boot[i];
  return any && id.generation;
}
inline bool sameIdentity(const Identity &a, const Identity &b) {
  if (a.generation != b.generation) return false;
  for (unsigned i = 0; i < 16; ++i) if (a.boot[i] != b.boot[i]) return false;
  return true;
}
inline bool resourcesMatch(const Resources &r) {
  const Resources m = measuredResources();
  return r.hostBase == m.hostBase && r.hostSize == m.hostSize &&
    r.phyBase == m.phyBase && r.phySize == m.phySize &&
    r.gccBase == m.gccBase && r.gccSize == m.gccSize &&
    r.gdscBase == m.gdscBase && r.gdscSize == m.gdscSize &&
    r.socId == m.socId && r.socRevision == m.socRevision &&
    r.lanes == m.lanes && r.spi == m.spi && r.irqFlags == m.irqFlags &&
    r.intid == m.intid && r.hostClocks == m.hostClocks;
}
inline Result checkStamp(const Stamp &s, const Context &c) {
  if (!identityValid(s.identity) || !sameIdentity(s.identity, c.current.identity))
    return Result::WrongBoot;
  if (s.phase != Phase::KernelPostEbs) return Result::WrongPhase;
  if (s.ticks < c.ebsReturnedTicks || s.ticks > c.current.ticks ||
      c.current.ticks - s.ticks > c.maxAgeTicks) return Result::Stale;
  return Result::MetadataConsistent;
}
inline bool rangeValid(uint64_t start, uint64_t bytes) {
  return start && bytes && start <= UINT64_MAX - bytes;
}
inline bool overlaps(uint64_t a, uint64_t n, uint64_t b, uint64_t m) {
  return a < b + m && b < a + n;
}
inline bool leaseValid(const Lease &l) {
  if (!l.token || l.bytes < chiron_ufs::DmaBytes ||
      !rangeValid(l.cpu, l.bytes) || !rangeValid(l.bus, l.bytes) ||
      ((l.cpu | l.bus | l.bytes) & 4095) ||
      l.cpu > UINTPTR_MAX - l.bytes ||
      l.addressBits < 32 || l.addressBits > 64 || l.cacheLine != 64 ||
      !l.cached || !l.deviceRead || !l.deviceWrite || !l.retainedUntilBootEnd)
    return false;
  return l.addressBits == 64 || l.bus + l.bytes - 1 < (uint64_t(1) << l.addressBits);
}
inline bool sameLease(const Lease &a, const Lease &b) {
  return a.token == b.token && a.cpu == b.cpu && a.bus == b.bus &&
    a.bytes == b.bytes && a.addressBits == b.addressBits && a.cacheLine == b.cacheLine &&
    a.cached == b.cached && a.deviceRead == b.deviceRead &&
    a.deviceWrite == b.deviceWrite && a.retainedUntilBootEnd == b.retainedUntilBootEnd;
}
inline Evidence begin(const Context &c) {
  Evidence e = {};
  e.abi = EvidenceAbi;
  e.resources = measuredResources();
  e.stamp = c.current;
  e.lease = c.heldLease;
  // Facts intentionally remain Unknown. No register access or DMA modification.
  return e;
}
inline Assessment assess(const Evidence &e, const Context &c) {
  Assessment a = {Result::Malformed, 0, 0, 0, 0, false};
  if (e.abi != EvidenceAbi || !identityValid(c.current.identity) || !c.maxAgeTicks)
    return a;
  if (!c.exitBootServicesReturned || c.current.phase != Phase::KernelPostEbs ||
      !c.ebsReturnedTicks || c.ebsReturnedTicks > c.current.ticks) {
    a.result = Result::WrongPhase; return a;
  }
  if (!resourcesMatch(e.resources)) { a.result = Result::ResourceMismatch; return a; }
  a.result = checkStamp(e.stamp, c);
  if (a.result != Result::MetadataConsistent) return a;
  if (!leaseValid(e.lease) || !leaseValid(c.heldLease) || !sameLease(e.lease, c.heldLease)) {
    a.result = Result::InvalidLease; return a;
  }
  // Evidence and the current ownership object must not live in DMA RAM.
  const uint64_t cpu = e.lease.cpu, bytes = e.lease.bytes;
  if (overlaps(cpu, bytes, reinterpret_cast<uintptr_t>(&e), sizeof(e)) ||
      overlaps(cpu, bytes, reinterpret_cast<uintptr_t>(&c), sizeof(c))) {
    a.result = Result::InvalidLease; return a;
  }
  for (unsigned i = 0; i < FactCount; ++i) {
    const Observation &o = e.facts[i];
    if (o.state == State::Unknown) { a.unknown |= 1U << i; continue; }
    if (o.state != State::Observed && o.state != State::Failed) {
      a.result = Result::Malformed; return a;
    }
    a.result = checkStamp(o.stamp, c);
    if (a.result != Result::MetadataConsistent) return a;
    if (o.stamp.ticks > e.stamp.ticks) { a.result = Result::Stale; return a; }
    if (o.state == State::Failed) a.failed |= 1U << i;
    else a.observed |= 1U << i;
  }
  a.result = a.failed ? Result::FailedObservation :
    a.unknown ? Result::Missing : Result::MetadataConsistent;
  // Metadata consistency is not trusted board qualification. This module has
  // no authentic board producer, MMIO reader, queue API or enabling conversion.
  return a;
}
inline chiron_ufs::Handoff unqualified(const Evidence &e) {
  chiron_ufs::Handoff h = {};
  h.abi = chiron_ufs::Abi;
  h.hostBase = e.resources.hostBase; h.hostSize = e.resources.hostSize;
  h.phyBase = e.resources.phyBase; h.phySize = e.resources.phySize;
  h.socId = e.resources.socId; h.socRevision = e.resources.socRevision;
  h.lanes = e.resources.lanes;
  h.dma = reinterpret_cast<uint8_t *>(static_cast<uintptr_t>(e.lease.cpu));
  h.dmaBus = e.lease.bus; h.dmaSize = static_cast<size_t>(e.lease.bytes);
  return h; // verified == 0, so the old probe refuses without touching hardware.
}
}
#endif
