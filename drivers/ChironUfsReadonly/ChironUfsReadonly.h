// SPDX-License-Identifier: BSD-2-Clause
#ifndef CHIRON_UFS_READONLY_H
#define CHIRON_UFS_READONLY_H
#include <stdint.h>
#include <stddef.h>

namespace chiron_ufs {
enum : uint32_t {
  Abi = 1, DmaBytes = 32768, Ucd = 4096, Response = 512,
  Prdt = 1024, Data = 8192, DataMax = 24576,
  Power = 1, Clocks = 2, Link = 4, DmaMapping = 8,
  Exclusive = 16, ExitBootServices = 32, Required = 63,
  FatalMask = 0x71880,
};
enum Status : int {
  Ok = 0, Disabled = 1, Unqualified = 2, ResourceMismatch = 3,
  Busy = 4, Timeout = 5, Fatal = 6, Protocol = 7, Scsi = 8,
  Denied = 9, GeometryMismatch = 10, GptMismatch = 11, AlreadyAttempted = 12,
};
// Runtime proof supplied by a board handoff implementation, never by an ADT boolean.
// The caller owns DMA and its mapping until a verified controller reset/quiescence.
struct Handoff {
  uint32_t abi, verified;
  uint64_t hostBase, hostSize, phyBase, phySize;
  uint32_t socId, socRevision, lanes;
  uint8_t *dma;
  uint64_t dmaBus;
  size_t dmaSize;
};
struct Lun {
  uint8_t id, peripheral;
  char vendor[9], product[17], revision[5];
  uint64_t lastLba;
  uint32_t blockBytes;
};
struct Report {
  Status status;
  uint32_t stage, cap, version, hcs, hce, commands, polls;
  uint32_t interrupt, doorbell, ocs, scsi, sense, residual;
  uint32_t lunCount, headerCrc, entriesCrc;
  uint8_t opcode, lun;
  bool quarantined, queueTouched, gptMatched;
  Lun luns[8];
};
inline uint32_t be32(const uint8_t *p) {
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
inline uint64_t be64(const uint8_t *p) { return uint64_t(be32(p)) << 32 | be32(p + 4); }
inline uint32_t le32(const uint8_t *p) {
  return uint32_t(p[3]) << 24 | uint32_t(p[2]) << 16 | uint32_t(p[1]) << 8 | p[0];
}
inline uint64_t le64(const uint8_t *p) { return uint64_t(le32(p + 4)) << 32 | le32(p); }
inline void putBe32(uint8_t *p, uint32_t v) {
  p[0] = uint8_t(v >> 24); p[1] = uint8_t(v >> 16); p[2] = uint8_t(v >> 8); p[3] = uint8_t(v);
}
inline void putLe32(uint8_t *p, uint32_t v) {
  p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); p[2] = uint8_t(v >> 16); p[3] = uint8_t(v >> 24);
}
inline uint32_t crc32(const uint8_t *p, size_t n) {
  uint32_t crc = ~0U;
  for (size_t i = 0; i < n; ++i) {
    crc ^= p[i];
    for (unsigned b = 0; b < 8; ++b) crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1)));
  }
  return ~crc;
}
inline bool qualified(const Handoff &h) {
  return h.abi == Abi && h.verified == Required && h.dma &&
    !(reinterpret_cast<uintptr_t>(h.dma) & 4095) && !(h.dmaBus & 4095) &&
    reinterpret_cast<uintptr_t>(h.dma) <= UINTPTR_MAX - DmaBytes &&
    h.dmaBus && h.dmaBus <= UINT64_MAX - DmaBytes && h.dmaSize >= DmaBytes;
}
inline bool resources(const Handoff &h) {
  return h.hostBase == 0x1da4000 && h.hostSize == 0x2500 &&
    h.phyBase == 0x1da7000 && h.phySize == 0xda8 &&
    h.socId == 292 && h.socRevision == 0x20001 && h.lanes == 2;
}
// Canonical CDBs only. Even a no-data SCSI opcode can mutate media, so direction
// alone is insufficient. READ is confined to the six previously captured GPT blocks.
inline bool allowed(uint8_t lun, const uint8_t *cdb, unsigned length,
                    unsigned direction, uint32_t bytes, uint32_t blockBytes) {
  if (!cdb || !length || lun > 7 || bytes > DataMax || (bytes & 3)) return false;
  uint8_t want[16] = {};
  unsigned size = 0;
  switch (cdb[0]) {
    case 0x00: size = 6; if (bytes || direction) return false; break;
    case 0x03: size = 6; want[4] = 96; if (bytes != 96 || direction != 2) return false; break;
    case 0x12: size = 6; want[4] = 36; if (bytes != 36 || direction != 2) return false; break;
    case 0x25: size = 10; if (bytes != 8 || direction != 2) return false; break;
    case 0x9e:
      size = 16; want[1] = 0x10; want[13] = 32;
      if (bytes != 32 || direction != 2) return false;
      break;
    case 0xa0:
      size = 12; want[9] = 80;
      if (lun || bytes != 80 || direction != 2) return false;
      break;
    case 0x28: {
      if (length != 10 || lun || blockBytes != 4096 || direction != 2) return false;
      uint32_t lba = be32(cdb + 2), count = uint32_t(cdb[7]) << 8 | cdb[8];
      if (!count || lba >= 6 || count > 6 - lba || bytes != count * 4096) return false;
      size = 10;
      for (unsigned i = 2; i <= 5; ++i) want[i] = cdb[i];
      want[7] = cdb[7]; want[8] = cdb[8];
      break;
    }
    default: return false;
  }
  want[0] = cdb[0];
  if (length != size) return false;
  for (unsigned i = 0; i < size; ++i) if (want[i] != cdb[i]) return false;
  return true;
}

// Port owns ordered MMIO, DMA cache maintenance, a monotonic microsecond clock
// and pacing. Layout and completion rules follow pinned UniXNU NativeUfs.
template<class Port> class Probe {
  Port &p;
  const Handoff &h;
  Report &r;
  bool poisoned = false;
  bool active = false, attempted = false;
  uint32_t blockBytes = 0, received = 0;
  uint8_t tag = 0;
  Status fail(Status s) {
    poisoned = true; r.quarantined = true; r.status = s; return s;
  }
  bool expired(uint64_t start, uint32_t polls, uint32_t us) {
    return p.nowUs() - start >= us || polls >= us / 2 + 1;
  }
  bool idle() {
    if (poisoned) return false;
    r.doorbell = p.read(0x58); r.interrupt = p.read(0x20);
    if (r.doorbell || p.read(0x78)) { fail(Busy); return false; }
    if (r.interrupt & FatalMask) { fail(Fatal); return false; }
    return true;
  }
  bool clear() {
    if (!idle()) return false;
    for (unsigned i = Ucd; i < Ucd + Prdt + 16; ++i) h.dma[i] = 0;
    return true;
  }
  Status submit(bool scsi, uint8_t lun, uint32_t bytes) {
    if (!idle()) return r.status;
    r.ocs = 15; r.scsi = 0xffff; r.sense = 0; r.residual = 0; received = 0;
    const unsigned direction = bytes ? 2U : 0U;
    uint8_t *d = h.dma;
    for (unsigned i = 0; i < 32; ++i) d[i] = 0;
    putLe32(d, 0x10000000U | (direction << 25));
    putLe32(d + 8, 15);
    putLe32(d + 16, uint32_t(h.dmaBus + Ucd));
    putLe32(d + 20, uint32_t((h.dmaBus + Ucd) >> 32));
    putLe32(d + 24, (512 >> 2) | ((Response >> 2) << 16));
    putLe32(d + 28, (bytes ? 1U : 0U) | ((Prdt >> 2) << 16));
    if (bytes) {
      putLe32(d + Ucd + Prdt, uint32_t(h.dmaBus + Data));
      putLe32(d + Ucd + Prdt + 4, uint32_t((h.dmaBus + Data) >> 32));
      putLe32(d + Ucd + Prdt + 12, bytes - 1);
      for (unsigned i = 0; i < bytes; ++i) d[Data + i] = 0;
    }
    p.toDevice(DmaBytes);
    p.write(0x20, 1); // W1C transfer completion only; retain error evidence.
    p.write(0x58, 1);
    ++r.commands;
    uint64_t start = p.nowUs();
    uint32_t polls = 0;
    for (;;) {
      ++polls; ++r.polls;
      r.doorbell = p.read(0x58); r.interrupt = p.read(0x20);
      if (r.interrupt & FatalMask) return fail(Fatal);
      if (!(r.doorbell & 1)) {
        p.fromDevice(64);
        r.ocs = le32(d + 8) & 0xff;
        if (r.ocs != 15) break;
      }
      if (expired(start, polls, 1000000)) return fail(Timeout);
      p.delay();
    }
    p.fromDevice(DmaBytes);
    if (r.ocs) return fail(Protocol);
    const uint8_t *rsp = d + Ucd + Response;
    uint32_t segment = uint32_t(rsp[10]) << 8 | rsp[11];
    if (rsp[0] != (scsi ? 0x21 : 0x20) || rsp[2] != lun ||
        rsp[3] != tag || rsp[8] || segment > 480) return fail(Protocol);
    if (!scsi) {
      if (rsp[1] || rsp[6] || rsp[7] || segment) return fail(Protocol);
      return Ok;
    }
    r.scsi = uint32_t(rsp[6]) << 8 | rsp[7];
    r.residual = be32(rsp + 12);
    r.sense = 0;
    if (segment) {
      if (segment < 2) return fail(Protocol);
      unsigned senseLength = uint32_t(rsp[32]) << 8 | rsp[33];
      if (senseLength > segment - 2) return fail(Protocol);
      const uint8_t *s = rsp + 34;
      if (senseLength >= 14 && (s[0] & 0x7e) == 0x70)
        r.sense = uint32_t(s[2] & 15) << 16 | uint32_t(s[12]) << 8 | s[13];
      else if (senseLength >= 4 && (s[0] & 0x7e) == 0x72)
        r.sense = uint32_t(s[1] & 15) << 16 | uint32_t(s[2]) << 8 | s[3];
    }
    if (rsp[6]) return fail(Protocol);
    if (rsp[7]) return Scsi;
    if ((rsp[1] & ~0x20U) || r.residual > bytes ||
        ((rsp[1] & 0x20) != 0) != (r.residual != 0)) return fail(Protocol);
    received = bytes - r.residual;
    return Ok;
  }
 public:
  Probe(Port &port, const Handoff &handoff, Report &report) : p(port), h(handoff), r(report) {}
  // Rejected before MMIO and before touching the DMA buffer.
  Status command(uint8_t lun, const uint8_t *cdb, unsigned length, unsigned direction, uint32_t bytes) {
    if (!allowed(lun, cdb, length, direction, bytes, blockBytes)) return Denied;
    if (!active) return Unqualified;
    if (!clear()) return r.status;
    tag = tag == 255 ? 1 : uint8_t(tag + 1);
    uint8_t *q = h.dma + Ucd;
    q[0] = 1; q[1] = bytes ? 0x40 : 0; q[2] = lun; q[3] = tag;
    putBe32(q + 12, bytes);
    for (unsigned i = 0; i < length; ++i) q[16 + i] = cdb[i];
    r.opcode = cdb[0]; r.lun = lun;
    return submit(true, lun, bytes);
  }
  Status run() {
    r.status = Unqualified;
    if (!qualified(h)) return r.status;
    if (!resources(h)) return r.status = ResourceMismatch;
    if (attempted) return r.status = AlreadyAttempted;
    attempted = true;
    r.stage = 1;
    r.cap = p.read(0); r.version = p.read(8); r.hcs = p.read(0x30); r.hce = p.read(0x34);
    if (!r.version || r.version == ~0U || !(r.hce & 1) || (r.hcs & 15) != 15 ||
        (!(r.cap & (1U << 24)) && h.dmaBus + DmaBytes > 0x100000000ULL))
      return r.status = Unqualified;
    if (!idle()) return r.status;
    // No reset, link startup, UIC commands, clock writes, SMMU writes or device wake.
    // Queue ownership change is a controller write, never a storage data write.
    r.stage = 2; r.queueTouched = true; r.quarantined = true;
    p.write(0x24, 0);
    p.write(0x60, 0);
    uint64_t start = p.nowUs();
    for (uint32_t n = 0; p.read(0x60) & 1; ++n) {
      if (expired(start, n, 100000)) return fail(Timeout);
      p.delay();
    }
    if (!idle()) return r.status;
    p.write(0x50, uint32_t(h.dmaBus)); p.write(0x54, uint32_t(h.dmaBus >> 32));
    if (p.read(0x50) != uint32_t(h.dmaBus) || p.read(0x54) != uint32_t(h.dmaBus >> 32))
      return fail(Protocol);
    p.write(0x60, 1);
    if (!(p.read(0x60) & 1) || (p.read(0x30) & 3) != 3) return fail(Unqualified);
    active = true;
    r.stage = 3;
    if (!clear()) return r.status;
    tag = 1; h.dma[Ucd + 3] = tag;
    Status s = submit(false, 0, 0);
    if (s != Ok) return r.status = s;
    uint8_t reportLuns[12] = {0xa0}; reportLuns[9] = 80;
    if ((s = command(0, reportLuns, 12, 2, 80)) != Ok) return r.status = s;
    if (received < 8) return fail(Protocol);
    uint32_t listBytes = be32(h.dma + Data);
    if (!listBytes || listBytes > 64 || (listBytes & 7) || listBytes > received - 8)
      return fail(Protocol);
    r.lunCount = listBytes / 8;
    bool seen[8] = {};
    for (unsigned i = 0; i < r.lunCount; ++i) {
      const uint8_t *entry = h.dma + Data + 8 + i * 8;
      if (entry[0] || entry[1] > 7 || seen[entry[1]]) return fail(Protocol);
      for (unsigned j = 2; j < 8; ++j) if (entry[j]) return fail(Protocol);
      r.luns[i].id = entry[1]; seen[entry[1]] = true;
    }
    r.stage = 4;
    bool matched = false;
    for (unsigned i = 0; i < r.lunCount; ++i) {
      Lun &l = r.luns[i];
      uint8_t inquiry[6] = {0x12, 0, 0, 0, 36, 0};
      if ((s = command(l.id, inquiry, 6, 2, 36)) != Ok) return r.status = s;
      if (received != 36 || h.dma[Data + 4] < 31) return fail(Protocol);
      l.peripheral = h.dma[Data];
      for (unsigned j = 0; j < 8; ++j) l.vendor[j] = char(h.dma[Data + 8 + j]);
      for (unsigned j = 0; j < 16; ++j) l.product[j] = char(h.dma[Data + 16 + j]);
      for (unsigned j = 0; j < 4; ++j) l.revision[j] = char(h.dma[Data + 32 + j]);
      if (l.peripheral != 0) return r.status = GeometryMismatch;
      uint8_t cap10[10] = {0x25};
      if ((s = command(l.id, cap10, 10, 2, 8)) != Ok) return r.status = s;
      if (received != 8) return fail(Protocol);
      l.lastLba = be32(h.dma + Data); l.blockBytes = be32(h.dma + Data + 4);
      if (l.lastLba == 0xffffffff) {
        uint8_t cap16[16] = {0x9e, 0x10}; cap16[13] = 32;
        if ((s = command(l.id, cap16, 16, 2, 32)) != Ok) return r.status = s;
        if (received != 32) return fail(Protocol);
        l.lastLba = be64(h.dma + Data); l.blockBytes = be32(h.dma + Data + 8);
      }
      if (l.id == 0) {
        if (l.lastLba != 29513727 || l.blockBytes != 4096) return r.status = GeometryMismatch;
        blockBytes = l.blockBytes; matched = true;
      }
    }
    if (!matched) return r.status = GeometryMismatch;
    r.stage = 5;
    uint8_t read[10] = {0x28}; read[8] = 6;
    if ((s = command(0, read, 10, 2, DataMax)) != Ok) return r.status = s;
    if (received != DataMax) return fail(Protocol);
    uint8_t *hdr = h.dma + Data + 4096;
    const char sig[] = "EFI PART";
    for (unsigned i = 0; i < 8; ++i) if (hdr[i] != uint8_t(sig[i])) return r.status = GptMismatch;
    r.headerCrc = le32(hdr + 16); r.entriesCrc = le32(hdr + 88);
    if (le32(hdr + 8) != 0x10000 || le32(hdr + 12) != 92 || le32(hdr + 20) ||
        le64(hdr + 24) != 1 || le64(hdr + 32) != 29513727 ||
        le64(hdr + 72) != 2 || le32(hdr + 80) != 32 || le32(hdr + 84) != 128)
      return r.status = GptMismatch;
    uint8_t copy[92];
    for (unsigned i = 0; i < 92; ++i) copy[i] = hdr[i];
    putLe32(copy + 16, 0);
    // These two CRCs are from the locally captured Android primary GPT.
    if (r.headerCrc != 2578770494U || r.entriesCrc != 1352948940U ||
        crc32(copy, sizeof(copy)) != r.headerCrc ||
        crc32(h.dma + Data + 8192, 4096) != r.entriesCrc) return r.status = GptMismatch;
    r.gptMatched = true; r.stage = 6;
    // Retain mapping/buffer even on success: the queue still references it.
    return r.status = Ok;
  }
};
}

extern "C" int chiron_ufs_readonly_probe(const chiron_ufs::Handoff *, chiron_ufs::Report *);
#endif
