// SPDX-License-Identifier: BSD-2-Clause
#include "../HandoffEvidence.h"

using namespace chiron_ufs_handoff;

struct CountingPort {
  unsigned calls = 0;
  uint32_t read(unsigned) { ++calls; return 0; }
  void write(unsigned, uint32_t) { ++calls; }
  uint64_t nowUs() { ++calls; return 0; }
  void delay() { ++calls; }
  void toDevice(unsigned) { ++calls; }
  void fromDevice(unsigned) { ++calls; }
};

// Also compiled freestanding for ARM64; no libc, registers or device handles.
extern "C" int chiron_ufs_handoff_model_tests() {
  Context c = {};
  c.current.identity.boot[0] = 0x45;
  c.current.identity.generation = 3;
  c.current.phase = Phase::KernelPostEbs;
  c.current.ticks = 120;
  c.ebsReturnedTicks = 100; c.maxAgeTicks = 20;
  c.exitBootServicesReturned = true;
  c.heldLease = {7, 0x80000000, 0x90000000, 32768, 32, 64, true, true, true, true};
  Evidence e = begin(c);
  if (assess(e, c).result != Result::Missing) return 1;
  for (unsigned i = 0; i < FactCount; ++i)
    e.facts[i] = {c.current, State::Observed};
  Assessment a = assess(e, c);
  if (a.result != Result::MetadataConsistent || a.verified || a.hardwareExecutionAllowed) return 2;
  if (chiron_ufs::qualified(unqualified(e))) return 3;
  alignas(4096) static uint8_t dma[chiron_ufs::DmaBytes];
  for (unsigned i = 0; i < sizeof(dma); ++i) dma[i] = 0xa5;
  chiron_ufs::Handoff denied = unqualified(e);
  denied.dma = dma;
  CountingPort port;
  chiron_ufs::Report report = {};
  chiron_ufs::Probe<CountingPort> probe(port, denied, report);
  if (probe.run() != chiron_ufs::Unqualified || port.calls) return 20;
  for (unsigned i = 0; i < sizeof(dma); ++i) if (dma[i] != 0xa5) return 21;
  for (unsigned i = 0; i < FactCount; ++i) {
    Evidence changed = e;
    changed.facts[i].state = State::Unknown;
    if (assess(changed, c).result != Result::Missing) return 4;
    changed = e; changed.facts[i].state = State::Failed;
    if (assess(changed, c).result != Result::FailedObservation) return 5;
    changed = e; changed.facts[i].stamp.identity.boot[0] ^= 1;
    if (assess(changed, c).result != Result::WrongBoot) return 6;
    changed = e; changed.facts[i].stamp.identity.generation++;
    if (assess(changed, c).result != Result::WrongBoot) return 7;
    changed = e; changed.facts[i].stamp.ticks = 99;
    if (assess(changed, c).result != Result::Stale) return 8;
    changed = e; changed.facts[i].stamp.phase = Phase::ExitCallback;
    if (assess(changed, c).result != Result::WrongPhase) return 9;
    changed = e; changed.facts[i].state = static_cast<State>(3);
    if (assess(changed, c).result != Result::Malformed) return 24;
  }
  Evidence changed = e; changed.resources.hostBase = 0x1d84000;
  if (assess(changed, c).result != Result::ResourceMismatch) return 10;
  changed = e; changed.stamp.identity.boot[0] ^= 1;
  if (assess(changed, c).result != Result::WrongBoot) return 11;
  changed = e; changed.stamp.ticks = 121;
  if (assess(changed, c).result != Result::Stale) return 12;
  changed = e; changed.lease.retainedUntilBootEnd = false;
  if (assess(changed, c).result != Result::InvalidLease) return 13;
  changed = e; changed.lease.token++;
  if (assess(changed, c).result != Result::InvalidLease) return 14;
  changed = e; changed.lease.bus = UINT64_MAX - 4095;
  if (assess(changed, c).result != Result::InvalidLease) return 15;
  changed = e; changed.lease.bus = 0x100000000ULL;
  if (assess(changed, c).result != Result::InvalidLease) return 16;
  Context later = c; later.current.ticks = 141;
  if (assess(e, later).result != Result::Stale) return 17;
  later = c; later.exitBootServicesReturned = false;
  if (assess(e, later).result != Result::WrongPhase) return 18;
  later = c; later.heldLease.cpu = reinterpret_cast<uintptr_t>(&e) & ~uint64_t(4095);
  // Use e itself to test the validator's overlap check.
  Evidence saved = e; e.lease = later.heldLease;
  if (assess(e, later).result != Result::InvalidLease) return 19;
  e = saved;
  uint64_t Resources::* const wide[] = {
    &Resources::hostBase, &Resources::hostSize, &Resources::phyBase, &Resources::phySize,
    &Resources::gccBase, &Resources::gccSize, &Resources::gdscBase, &Resources::gdscSize
  };
  for (auto member : wide) {
    changed = e; changed.resources.*member ^= 1;
    if (assess(changed, c).result != Result::ResourceMismatch) return 22;
  }
  uint32_t Resources::* const narrow[] = {
    &Resources::socId, &Resources::socRevision, &Resources::lanes, &Resources::spi,
    &Resources::irqFlags, &Resources::intid, &Resources::hostClocks
  };
  for (auto member : narrow) {
    changed = e; changed.resources.*member ^= 1;
    if (assess(changed, c).result != Result::ResourceMismatch) return 23;
  }
  return 0;
}

#ifndef CHIRON_HANDOFF_COMPILE_ONLY
int main() { return chiron_ufs_handoff_model_tests(); }
#endif
