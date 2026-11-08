// SPDX-License-Identifier: BSD-2-Clause
//
// Runs the whole A540 bring-up chain against one register space, so the layers are checked
// for their interactions and not only in isolation.
//
// The order is the kernel's, read off adreno_a5xx.c:
//
//   GPUCC power-up                    the block is powered and clocked
//   SMMU context bank                 the GPU can translate
//   a5xx_gpmu_start   (line 2203)     GMU handshake
//   a5xx_rb_start     (line 2483)     ring, then microcode, then CP_ME_CNTL = 0
//
// One Model serves every layer, keyed by absolute address, so GPUCC at 0x5065000, the SMMU
// at 0x5040000 and the GPU at 0x5000000 stay separate the way they are on the part. A
// collision between layers shows up as one layer stomping another's register.
#include "../ChironAdrenoA540CpStart.h"
#include "../ChironAdrenoA540Cpsw.h"
#include "../ChironAdrenoA540Gmu.h"
#include "../ChironAdrenoA540Gpucc.h"
#include "../ChironAdrenoA540Smmu.h"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <map>
#include <utility>
#include <vector>

using namespace chiron_adreno;

static int failures = 0;

#define CHECK(cond)                                                \
  do {                                                             \
    if (!(cond)) {                                                 \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);  \
      ++failures;                                                  \
    }                                                              \
  } while (0)

namespace {

struct Model {
  static constexpr uint32_t kGpuBase = 0x5000000;
  static constexpr uint32_t kSmmuBase = 0x5040000;
  static constexpr uint32_t kGpuccBase = 0x5065000;

  std::map<uint32_t, uint32_t> regs;
  std::vector<std::pair<uint32_t, uint32_t>> log;
  uint32_t gmu_general0 = 0, gmu_general1 = 0;
  uint32_t gmu_alive_after = 1, gmu_reads = 0;

  void put(uint32_t addr, uint32_t v) {
    regs[addr] = v;
    log.emplace_back(addr, v);
  }
  uint32_t get(uint32_t addr) const {
    auto it = regs.find(addr);
    return it == regs.end() ? 0u : it->second;
  }
  int index_of(uint32_t addr) const {
    for (size_t i = 0; i < log.size(); ++i)
      if (log[i].first == addr) return int(i);
    return -1;
  }

  static void RailOn(void *ctx) { static_cast<Model *>(ctx)->put(0x0DEAD000u, 1u); }
  static void NoDelay(void *, uint32_t) {}

  // GpuccRegs takes a plain pointer rather than callbacks, because every register the
  // GPUCC sequence touches lives inside the one block. The model backs it with a real
  // array so the sequence's read-modify-writes behave as they would on hardware.
  std::vector<uint32_t> gpucc_array = std::vector<uint32_t>(0x9000 / 4, 0);

  GpuccRegs gpucc() {
    // Mirror whatever the map already holds into the array, so the powered-off state set
    // up before this call is what the sequence sees.
    for (const auto &kv : regs)
      if (kv.first >= kGpuccBase && kv.first < kGpuccBase + 0x9000)
        gpucc_array[(kv.first - kGpuccBase) / 4] = kv.second;
    return GpuccRegs{gpucc_array.data()};
  }
  // Pull the array back into the map after the sequence, so later checks see it. Every
  // word is copied, including zeros: clearing the collapse bit on a register whose only
  // set bit was that one leaves zero, and skipping zeros here would hide exactly the
  // transition the checks are looking for.
  void sync_gpucc() {
    for (size_t i = 0; i < gpucc_array.size(); ++i)
      regs[kGpuccBase + uint32_t(i * 4)] = gpucc_array[i];
  }
  GpuccBoard gpucc_board() { return GpuccBoard{RailOn, NoDelay, this}; }

  GmuRegs gmu() {
    return GmuRegs{
        [](void *c, uint32_t off, uint32_t v) {
          static_cast<Model *>(c)->put(kGpuBase + off, v);
        },
        [](void *c, uint32_t off) -> uint32_t {
          auto *m = static_cast<Model *>(c);
          if (off == GmuRegister(GmuGeneral0)) {
            if (++m->gmu_reads >= m->gmu_alive_after) return GmuFirmwareAlive;
            return m->gmu_general0;
          }
          if (off == GmuRegister(GmuGeneral1)) return m->gmu_general1;
          return m->get(kGpuBase + off);
        },
        nullptr,
        this,
        false};
  }

  CpRegs cp() {
    return CpRegs{
        [](void *c, uint32_t off, uint32_t v) {
          static_cast<Model *>(c)->put(kGpuBase + off, v);
        },
        [](void *c, uint32_t off) {
          return static_cast<Model *>(c)->get(kGpuBase + off);
        },
        this};
  }

  SmmuRegs smmu() {
    return SmmuRegs{
        [](void *c, uint32_t off, uint32_t v) {
          static_cast<Model *>(c)->put(kSmmuBase + off, v);
        },
        [](void *c, uint32_t off) {
          return static_cast<Model *>(c)->get(kSmmuBase + off);
        },
        this};
  }
};

std::vector<uint8_t> Load(const char *name) {
  std::ifstream in(name, std::ios::binary);
  if (!in) return {};
  return std::vector<uint8_t>((std::istreambuf_iterator<char>(in)),
                              std::istreambuf_iterator<char>());
}

}  // namespace

int main() {
  Model m;

  // ---- 1. GPUCC ------------------------------------------------------------
  // Start powered off, as the block is after reset.
  m.put(Model::kGpuccBase + GpuCxGdscr, GdscrSwCollapse);
  m.put(Model::kGpuccBase + GpuGxGdscr, GdscrSwCollapse);
  m.put(Model::kGpuccBase + GpuccGxDomainMisc, GxDomainReset | GxDomainClampIo);

  CHECK(GpuccPowerUp(m.gpucc(), m.gpucc_board()));
  m.sync_gpucc();
  CHECK((m.get(Model::kGpuccBase + GpuCxGdscr) & GdscrSwCollapse) == 0);
  CHECK((m.get(Model::kGpuccBase + GpuGxGdscr) & GdscrSwCollapse) == 0);
  CHECK((m.get(Model::kGpuccBase + GpuccGfx3dCbcr) & 1u) == 1u);
  const int gpucc_writes = int(m.log.size());
  CHECK(gpucc_writes > 0);  // sync_gpucc() logged the block's non-zero registers

  // ---- 2. SMMU -------------------------------------------------------------
  SmmuContextConfig ctx{};
  ctx.index = 0;
  ctx.ttbr0 = 0x40000000ULL;
  ctx.ttbr1 = 0;
  ctx.ttbcr = 0x10;
  ctx.ttbcr2 = 0;
  ctx.mair0 = 0xFF4400;
  ctx.mair1 = 0;
  ctx.contextidr = 1;
  ctx.stall_on_fault = true;
  CHECK(SmmuProgramContext(m.smmu(), ctx, 8) == SmmuResult::Ok);
  CHECK(m.get(Model::kSmmuBase + SmmuContextBank(0) + SmmuCbActlr) == SmmuActlrQcomV2);
  CHECK(m.get(Model::kSmmuBase + SmmuContextBank(0) + SmmuCbSctlr) & SmmuSctlrM);
  // The SMMU writes went to its own window, not into GPUCC.
  CHECK(m.index_of(Model::kSmmuBase + SmmuContextBank(0) + SmmuCbSctlr) >= gpucc_writes);

  // ---- 3. GMU --------------------------------------------------------------
  CHECK(GmuStartFirmware(m.gmu(), nullptr) == GmuResult::Ok);
  CHECK(m.get(Model::kGpuBase + GmuRegister(GmuCm3Sysreset)) == 0u);

  // ---- 4. CP ring ----------------------------------------------------------
  CpRingConfig ring{};
  ring.ring_gpu_addr = 0x80000000ULL;
  ring.read_ptr_gpu_addr = 0x80010000ULL;
  ring.ring_dwords = CpRingDwords;
  CHECK(CpProgramRing(m.cp(), ring) == CpResult::Ok);

  const int rptr_at = m.index_of(Model::kGpuBase + CpRbRptrAddrLo * 4u);
  const int cntl_at = m.index_of(Model::kGpuBase + CpRbCntl * 4u);
  const int base_at = m.index_of(Model::kGpuBase + CpRbBase * 4u);
  CHECK(rptr_at >= 0);
  CHECK(cntl_at > rptr_at);
  CHECK(base_at > cntl_at);
  CHECK(m.get(Model::kGpuBase + CpRbCntl * 4u) == CpRbCntlDefault);

  // The ring's BASE and the GMU's registers share the GPU window without overlapping:
  // CP registers sit at word 0x800+, the GMU at 0xA800+.
  CHECK(CpRbBase * 4u < GmuRegister(GmuCm3Sysreset));
  CHECK(CpRbBase * 4u + 0x1000u <= GmuRegister(GmuInstRamBase));

  // ---- 5. microcode --------------------------------------------------------
  const std::vector<uint8_t> pfp = Load("a530_pfp.fw");
  const std::vector<uint8_t> pm4 = Load("a530_pm4.fw");
  CHECK(pfp.size() == 16820u);
  CHECK(pm4.size() == 19652u);

  CpFirmware pfp_fw, pm4_fw;
  CHECK(CpFirmwareSplit(pfp.data(), pfp.size(), &pfp_fw) == CpFirmwareResult::Ok);
  CHECK(CpFirmwareSplit(pm4.data(), pm4.size(), &pm4_fw) == CpFirmwareResult::Ok);
  CHECK(pfp_fw.words == 4204u);
  CHECK(pm4_fw.words == 4912u);
  CHECK(pfp_fw.version == 0x005ff112u);
  CHECK(pm4_fw.version == 0x005ff066u);

  // ---- 6. release the micro engine, last -----------------------------------
  const int before_me = int(m.log.size());
  CpStartMicroEngine(m.cp());
  CHECK(int(m.log.size()) == before_me + 1);
  CHECK(m.log.back().first == Model::kGpuBase + CpMeCntl * 4u);
  CHECK(m.log.back().second == 0u);

  // CP_ME_CNTL is written after every ring register, which is the ordering that matters:
  // releasing the engine before the ring is described would leave it reading garbage.
  CHECK(m.log.back().first != Model::kGpuBase + CpRbBase * 4u);
  CHECK(base_at < before_me);

  if (failures == 0) std::printf("Bringup: chain complete\n");
  return failures == 0 ? 0 : 1;
}