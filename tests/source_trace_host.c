/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#include "../diagnostics/chiron-trace.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ROOT 0x4017a000ULL
#define GOP 0x9d400000ULL
static uint64_t root_table[512], block_table[512], current_root = 0x1234000040000000ULL;
static unsigned opens, closes, refuse, reenter;
static uint32_t framebuffer[320 * 240 + 16];

uint64_t ct_host_sctlr(void) { return 0x34d4593d; }
uint64_t ct_host_tcr(void) { return 0x22a51a251a; }
int ct_host_translation(uint64_t address, uint64_t expected) {
    if (reenter) {
        reenter = 0;
        chiron_trace_exception(0xfffffff007123400ULL, 0x96000004, 0x1000,
                               0xfffffff007ffff00ULL, 0x600003c5);
    }
    return !refuse && current_root == ROOT && address == expected;
}
uint64_t *ct_host_physical(uint64_t physical) {
    if (physical == ROOT) return root_table;
    if (physical == ROOT + 0x4000) return block_table;
    return NULL;
}
uint64_t ct_host_window_open(uint64_t root) {
    assert(root == ROOT);
    uint64_t old = current_root;
    current_root = root;
    ++opens;
    return old;
}
void ct_host_window_close(uint64_t old) {
    assert(current_root == ROOT);
    current_root = old;
    ++closes;
}
volatile uint32_t *ct_host_pixels(uint64_t base) {
    assert(base == GOP);
    return framebuffer + 8;
}

static void panic_case(const char *format, ...) {
    va_list args;
    va_start(args, format);
    chiron_trace_panic(format, &args, 0xfffffff007999000ULL);
    assert(va_arg(args, int) == 42); /* Observer must not consume native panic's args. */
    va_end(args);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    for (unsigned i = 0; i < sizeof(framebuffer) / sizeof(*framebuffer); ++i)
        framebuffer[i] = 0xa5a5a5a5;
    root_table[2] = ROOT + 0x4000 + 3;
    for (unsigned i = 0; i < 5; ++i) block_table[234 + i] = 0x006000009d40060dULL + i * 0x200000;
    chiron_trace_log.boot_root = ROOT;
    chiron_trace_init(GOP, 320, 240, 1280, 32, 0xfffffff007004000ULL, 0x40004000);
    assert(chiron_trace_log.magic == CT_MAGIC && boot_progress_active());
    assert(opens && opens == closes && current_root == 0x1234000040000000ULL);
    chiron_trace_initializer(0, 4, 1, 0xfffffff007333000ULL, 0);
    chiron_trace_initializer(1, 4, 1, 0xfffffff007333000ULL, 0);
    for (unsigned i = 0; i < 270; ++i) boot_progress_stage("bounded stage journal wrap");
    assert(chiron_trace_log.event_dropped > 0);
    for (unsigned i = 0; i < 40000; ++i) boot_progress_putc('x');
    boot_progress_putc('\n');
    assert(chiron_trace_log.text_used == CT_TEXT_BYTES && chiron_trace_log.text_dropped > 0);
    unsigned count = opens;
    refuse = 1;
    boot_progress_stage("translation refusal still restores the original root");
    assert(opens == count + 1 && opens == closes);
    refuse = 0;
    count = opens;
    root_table[2] = 0;
    boot_progress_stage("changed mapping skips display but retains the journal");
    assert(opens == count);
    root_table[2] = ROOT + 0x4000 + 3;
    reenter = 1;
    boot_progress_stage("nested observer fault keeps an independent fault snapshot");
    assert(chiron_trace_log.reentries && chiron_trace_log.far == 0x1000);
    chiron_trace_exception(0xfffffff007123456ULL, 0x96000004, 0x1000,
                           0xfffffff007ffff00ULL, 0x600003c5);
    assert(chiron_trace_log.pc == 0xfffffff007123456ULL);
    assert(chiron_trace_log.esr == 0x96000004 && chiron_trace_log.fault_valid == 1);
    panic_case("expected panic %d %s", 42, "source-channel");
    assert(chiron_trace_log.fault_valid == 2);
    assert(opens == closes && current_root == 0x1234000040000000ULL);
    for (unsigned i = 0; i < 8; ++i) {
        assert(framebuffer[i] == 0xa5a5a5a5);
        assert(framebuffer[8 + 320 * 240 + i] == 0xa5a5a5a5);
    }
    FILE *output = fopen(argv[1], "wb");
    assert(output);
    assert(fwrite(&chiron_trace_log, sizeof(chiron_trace_log), 1, output) == 1);
    assert(!fclose(output));
    return 0;
}
