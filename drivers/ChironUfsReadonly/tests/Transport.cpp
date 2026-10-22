// SPDX-License-Identifier: BSD-2-Clause
#include "../ChironUfsReadonly.h"
#include <assert.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>
using namespace chiron_ufs;

struct Model {
  alignas(4096) uint8_t dma[DmaBytes] = {};
  uint32_t regs[64] = {}, reads = 0, writes = 0, rings = 0, syncs = 0;
  uint64_t time = 0;
  bool freeze = false;
  std::string fault;
  std::vector<uint8_t> gpt;
  explicit Model(const std::vector<uint8_t> &fixture, const char *mode = "")
      : fault(mode), gpt(fixture) {
    regs[0] = 0x0100001f; regs[2] = 0x200;
    regs[0x30 / 4] = 15; regs[0x34 / 4] = 1;
    if (fault == "busy") regs[0x58 / 4] = 2;
    if (fault == "task-busy") regs[0x78 / 4] = 1;
    if (fault == "fatal-before") regs[0x20 / 4] = FatalMask;
    freeze = fault == "frozen-clock";
  }
  Handoff handoff() {
    return {Abi, Required, 0x1da4000, 0x2500, 0x1da7000, 0xda8,
            292, 0x20001, 2, dma, 0x90000000, sizeof(dma)};
  }
  uint32_t read(unsigned at) {
    ++reads;
    if (fault == "bad-list" && at == 0x50) return 0;
    return regs[at / 4];
  }
  void write(unsigned at, uint32_t v) {
    assert(at == 0x20 || at == 0x24 || at == 0x50 || at == 0x54 || at == 0x58 || at == 0x60);
    ++writes;
    if (at == 0x20) { regs[at / 4] &= ~v; return; }
    if (at == 0x60 && fault == "stop-stuck" && !v) { regs[at / 4] = 1; return; }
    regs[at / 4] = v;
    if (at != 0x58) return;
    ++rings;
    assert(v == 1 && le32(dma + 16) == 0x90001000 && le32(dma + 20) == 0);
    assert(le32(dma + 24) == ((Response >> 2) << 16 | (512 >> 2)));
    if (fault == "timeout" || freeze) return;
    if (fault == "fatal-during") { regs[0x20 / 4] = FatalMask; return; }
    regs[at / 4] = 0;
    if (fault == "stale-ocs") return;
    uint8_t *q = dma + Ucd, *rsp = q + Response, *data = dma + Data;
    bool scsi = q[0] == 1;
    rsp[0] = scsi ? 0x21 : 0x20; rsp[2] = q[2]; rsp[3] = q[3];
    putLe32(dma + 8, 0);
    if (fault == "bad-type") rsp[0] = 0;
    if (fault == "bad-tag") ++rsp[3];
    if (fault == "bad-lun") ++rsp[2];
    if (!scsi) return;
    const uint8_t *cdb = q + 16;
    uint32_t bytes = be32(q + 12);
    assert(allowed(q[2], cdb, cdb[0] == 0xa0 ? 12 : cdb[0] == 0x12 ? 6 : cdb[0] == 0x9e ? 16 : 10,
                   2, bytes, 4096));
    assert(q[1] == 0x40 && (le32(dma) & (3U << 25)) == (2U << 25));
    assert(le32(q + Prdt) == 0x90002000 && le32(q + Prdt + 12) == bytes - 1);
    if (cdb[0] == 0xa0) {
      putBe32(data, 16); data[17] = 1;
      rsp[1] = 0x20; putBe32(rsp + 12, 56);
    } else if (cdb[0] == 0x12) {
      data[4] = 31;
      for (unsigned j = 8; j < 36; ++j) data[j] = 'A';
      if (fault == "scsi-sense") {
        rsp[7] = 2; rsp[11] = 20; rsp[33] = 18;
        rsp[34] = 0x70; rsp[36] = 2; rsp[46] = 4; rsp[47] = 1;
      }
    } else if (cdb[0] == 0x25) {
      putBe32(data, q[2] ? 999 : fault == "geometry" ? 123 : fault == "capacity16" ? 0xffffffff : 29513727);
      putBe32(data + 4, q[2] ? 512 : 4096);
      if (fault == "short-capacity") { rsp[1] = 0x20; putBe32(rsp + 12, 4); }
    } else if (cdb[0] == 0x9e) {
      putBe32(data + 4, 29513727); putBe32(data + 8, 4096);
    } else if (cdb[0] == 0x28) {
      assert(bytes == gpt.size());
      for (size_t i = 0; i < gpt.size(); ++i) data[i] = gpt[i];
      if (fault == "gpt-corrupt") data[8192] ^= 1;
    } else assert(false);
  }
  void toDevice(size_t) { ++syncs; }
  void fromDevice(size_t) { ++syncs; }
  uint64_t nowUs() { return time; }
  void delay() { if (!freeze) time += 2; }
};

static void assertUntouched(const Model &m) {
  assert(!m.reads && !m.writes && !m.rings && !m.syncs);
  for (uint8_t byte : m.dma) assert(byte == 0);
}

static void checkPolicy() {
  // No GPT fixture: these paths must refuse before any MMIO or DMA change.
  const std::vector<uint8_t> noGpt;
  for (unsigned bit = 0; bit < 6; ++bit) {
    Model m(noGpt); auto h = m.handoff(); h.verified &= ~(1U << bit);
    Report r{}; Probe<Model> p(m, h, r);
    assert(p.run() == Unqualified);
    assertUntouched(m);
  }
  {
    Model m(noGpt); auto h = m.handoff(); h.hostBase = 0x1d84000;
    Report r{}; Probe<Model> p(m, h, r);
    assert(p.run() == ResourceMismatch);
    assertUntouched(m);
  }
  {
    Model m(noGpt); auto h = m.handoff(); Report r{}; Probe<Model> p(m, h, r);
    for (unsigned op = 0; op < 256; ++op) {
      uint8_t cdb[16] = {}; cdb[0] = uint8_t(op);
      assert(p.command(0, cdb, 16, 1, 4096) == Denied);
      for (unsigned size : {6U, 10U, 12U, 16U})
        assert(allowed(0, cdb, size, 0, 0, 4096) == (op == 0 && size == 6));
    }
    uint8_t tur[6] = {};
    assert(p.command(0, tur, 6, 0, 0) == Unqualified);
    assert(!allowed(0, tur, 0, 0, 0, 0));
    assertUntouched(m);
    uint8_t read[10] = {0x28}; read[8] = 6;
    assert(allowed(0, read, 10, 2, DataMax, 4096));
    assert(!allowed(1, read, 10, 2, DataMax, 4096));
    assert(!allowed(0, read, 10, 2, DataMax, 512));
    read[5] = 1;
    assert(!allowed(0, read, 10, 2, DataMax, 4096));
    uint8_t legal[6] = {0x12, 0, 0, 0, 36, 0};
    for (unsigned at = 1; at < 6; ++at) {
      legal[at] ^= 1;
      assert(!allowed(0, legal, 6, 2, 36, 0));
      legal[at] ^= 1;
    }
    uint8_t sense[6] = {0x03, 0, 0, 0, 96, 0};
    assert(allowed(0, sense, 6, 2, 96, 0));
    uint8_t cap16[16] = {0x9e, 0x10}; cap16[13] = 32;
    assert(allowed(0, cap16, 16, 2, 32, 0));
    cap16[1] = 0x11;
    assert(!allowed(0, cap16, 16, 2, 32, 0));
    assertUntouched(m);
  }
}

int main(int argc, char **argv) {
  if (argc != 2) {
    std::cerr << "Usage: transport --policy-only | <saved-gpt.bin>\n";
    return 2;
  }
  checkPolicy();
  if (std::string(argv[1]) == "--policy-only") {
    std::cout << "PASS: policy-only: readonly whitelist, preflight refusals, zero MMIO/cache/DMA changes\n";
    return 0;
  }
  // Only the separate fixture-backed mode can enter the 18 transfer models.
  std::ifstream in(argv[1], std::ios::binary);
  std::vector<uint8_t> gpt{std::istreambuf_iterator<char>(in), {}};
  assert(gpt.size() == DataMax);
  const struct { const char *name; Status status; } cases[] = {
    {"", Ok}, {"capacity16", Ok}, {"busy", Busy}, {"task-busy", Busy}, {"fatal-before", Fatal},
    {"timeout", Timeout}, {"frozen-clock", Timeout}, {"stale-ocs", Timeout},
    {"fatal-during", Fatal}, {"bad-type", Protocol}, {"bad-tag", Protocol},
    {"bad-lun", Protocol}, {"scsi-sense", Scsi}, {"short-capacity", Protocol},
    {"geometry", GeometryMismatch}, {"gpt-corrupt", GptMismatch},
    {"stop-stuck", Timeout}, {"bad-list", Protocol},
  };
  for (const auto &c : cases) {
    Model m(gpt, c.name); auto h = m.handoff(); Report r{}; Probe<Model> p(m, h, r);
    assert(p.run() == c.status);
    if (c.status == Ok) {
      assert(r.gptMatched && r.lunCount == 2 && r.luns[0].blockBytes == 4096);
      assert(r.luns[1].lastLba == 999 && r.luns[1].blockBytes == 512);
      assert(r.commands == (std::string(c.name) == "capacity16" ? 8 : 7));
    }
    if (std::string(c.name) == "scsi-sense") assert(r.sense == 0x020401);
    if (std::string(c.name) == "timeout" || std::string(c.name) == "stale-ocs" ||
        std::string(c.name) == "frozen-clock") assert(r.ocs == 15);
    if (r.queueTouched) assert(r.quarantined);
    if (c.status == Busy || std::string(c.name) == "fatal-before") assert(!m.writes);
    unsigned writes = m.writes, syncs = m.syncs;
    assert(p.run() == AlreadyAttempted && m.writes == writes && m.syncs == syncs);
    if (c.status == Timeout || c.status == Protocol || c.status == Fatal) {
      uint8_t inquiry[6] = {0x12, 0, 0, 0, 36, 0};
      assert(p.command(0, inquiry, 6, 2, 36) != Ok && m.writes == writes && m.syncs == syncs);
    }
  }
  std::cout << "PASS: readonly policy, no-MMIO gates, UFSHCI descriptors, 18 transport cases, GPT CRC\n";
}
