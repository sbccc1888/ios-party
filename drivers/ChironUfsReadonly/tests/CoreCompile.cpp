// SPDX-License-Identifier: BSD-2-Clause
// Freestanding ARM64 compile qualification only; no XNU or hardware execution.
#include "../ChironUfsReadonly.h"
struct CompilePort {
  uint32_t read(unsigned);
  void write(unsigned, uint32_t);
  void toDevice(size_t);
  void fromDevice(size_t);
  uint64_t nowUs();
  void delay();
};
int compileChironUfsCore(CompilePort &p, const chiron_ufs::Handoff &h, chiron_ufs::Report &r) {
  chiron_ufs::Probe<CompilePort> probe(p, h, r);
  return probe.run();
}
