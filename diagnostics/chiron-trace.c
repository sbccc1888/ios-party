/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#include "chiron-trace.h"

#ifndef CHIRON_TRACE_BUILD_ID
#define CHIRON_TRACE_BUILD_ID "UNNUMBERED-SOURCE-TRACE"
#endif
const char chiron_trace_build_id[96] = CHIRON_TRACE_BUILD_ID;
struct chiron_trace_storage chiron_trace_log __attribute__((aligned(4096)));
unsigned char chiron_trace_early_stack[4096] __attribute__((aligned(16)));
static unsigned trace_busy, trace_ready;
static uint64_t trace_irqs;
static uint64_t trace_painted, trace_painted_base;
static unsigned trace_x, trace_y, trace_repaint, trace_painted_fault;
static uint64_t trace_panic_start;
static unsigned trace_panic_start_valid;

extern int vsnprintf(char *, size_t, const char *, va_list);

#ifdef CHIRON_TRACE_HOST_TEST
extern uint64_t ct_host_sctlr(void);
extern uint64_t ct_host_tcr(void);
extern int ct_host_translation(uint64_t, uint64_t);
extern uint64_t *ct_host_physical(uint64_t);
extern uint64_t ct_host_window_open(uint64_t);
extern void ct_host_window_close(uint64_t);
extern volatile uint32_t *ct_host_pixels(uint64_t);
#define trace_sctlr ct_host_sctlr
#define trace_tcr ct_host_tcr
static uint64_t trace_mask(void) { return 0; }
static void trace_restore(uint64_t value) { (void)value; }
#else
extern uint64_t chiron_trace_window_open(uint64_t);
extern void chiron_trace_window_close(uint64_t);
extern uintptr_t ml_io_map(uint64_t, uintptr_t);
extern int PE_parse_boot_argn(const char *, void *, int);
static uint64_t trace_sctlr(void) { uint64_t x; __asm__ volatile("mrs %0, SCTLR_EL1" : "=r"(x)); return x; }
static uint64_t trace_tcr(void) { uint64_t x; __asm__ volatile("mrs %0, TCR_EL1" : "=r"(x)); return x; }
static uint64_t trace_mask(void) {
    uint64_t value;
    __asm__ volatile("mrs %0, DAIF\nmsr DAIFSet, #15" : "=r"(value) :: "memory");
    return value;
}
static void trace_restore(uint64_t value) {
    __asm__ volatile("msr DAIF, %0" :: "r"(value) : "memory");
}
#endif

static void trace_copy(char *out, const char *in, size_t size) {
    size_t i = 0;
    if (in) for (; i + 1 < size && in[i]; ++i) out[i] = in[i];
    if (size) out[i] = 0;
}

static void trace_header(void) {
    chiron_trace_log.magic = CT_MAGIC;
    chiron_trace_log.version = CT_VERSION;
    chiron_trace_log.bytes = CT_BYTES;
    chiron_trace_log.extension_version = 1;
    trace_copy(chiron_trace_log.build_id, chiron_trace_build_id, 96);
}

static void trace_char(char c) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    log->text[log->text_write] = c;
    log->text_write = (log->text_write + 1) % CT_TEXT_BYTES;
    if (log->text_used < CT_TEXT_BYTES) ++log->text_used;
    else ++log->text_dropped;
}

static void trace_text(const char *text) {
    if (text) for (size_t i = 0; i < 2048 && text[i]; ++i) trace_char(text[i]);
}

static void trace_hex(uint64_t value) {
    static const char digits[] = "0123456789ABCDEF";
    for (unsigned i = 0; i < 16; ++i) trace_char(digits[(value >> (60 - i * 4)) & 15]);
}

static unsigned trace_viewport_height(void) {
    unsigned height = chiron_trace_log.height;
    if (height > 768) height = 768;
    if (chiron_trace_log.stride && height > 0x400000 / chiron_trace_log.stride)
        height = 0x400000 / chiron_trace_log.stride;
    return height;
}

static int trace_geometry(void) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    if (log->depth != 32 || log->width < 160 || log->width > 4096 ||
        log->height < 160 || log->height > 4096 || (log->stride & 3) ||
        log->stride < log->width * 4 || log->stride > 32768) return 0;
    uint64_t bytes = (uint64_t)log->stride * trace_viewport_height();
    if (!log->video_phys || (log->video_phys & 3) || bytes > 0x1000000 ||
        log->video_phys + bytes < log->video_phys) return 0;
    log->video_bytes = bytes;
    return 1;
}

static int trace_translation(uint64_t address, uint64_t expected) {
    if (!(trace_sctlr() & 1)) return address == expected;
#ifdef CHIRON_TRACE_HOST_TEST
    return ct_host_translation(address, expected);
#else
    uint64_t saved, result;
    __asm__ volatile("mrs %0, PAR_EL1" : "=r"(saved));
    __asm__ volatile("at s1e1w, %0\nisb\nmrs %1, PAR_EL1"
                     : "+r"(address), "=r"(result) :: "memory");
    __asm__ volatile("msr PAR_EL1, %0" :: "r"(saved) : "memory");
    return !(result & 1) && (result & 0xfffffff000ULL) == (expected & 0xfffffff000ULL);
#endif
}

static uint64_t *trace_physical(uint64_t address) {
#ifdef CHIRON_TRACE_HOST_TEST
    return ct_host_physical(address);
#else
    return (uint64_t *)(uintptr_t)(address - chiron_trace_log.phys_base + chiron_trace_log.virt_base);
#endif
}

static int trace_boot_mapping(void) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    uint64_t root = log->boot_root, first = log->video_phys & ~0x1fffffULL;
    uint64_t last = (log->video_phys + log->video_bytes - 1) & ~0x1fffffULL;
    if (!root || (root & 4095) || !log->virt_base || !log->phys_base) return 0;
    uint64_t *table = trace_physical(root);
    if (!table) return 0;
    for (uint64_t page = first; page <= last; page += 0x200000) {
        uint64_t entry = table[(page >> 30) & 255];
        if ((entry & 3) != 3) return 0;
        uint64_t next = entry & 0xfffffff000ULL;
        if (next < root + 8192 || next >= root + 65536) return 0;
        uint64_t *blocks = trace_physical(next);
        if (!blocks) return 0;
        uint64_t block = blocks[(page >> 21) & 511];
        if ((block & 3) != 1 || (block & 0xffffe00000ULL) != page ||
            !(block & 0x400) || (block & 0xc0)) return 0;
    }
    return 1;
}

/* Five-column glyphs, uppercasing only for display. Raw logs retain original text. */
static const unsigned char trace_font[][5] = {
    {0,0,0,0,0}, {0x3e,0x51,0x49,0x45,0x3e}, {0,0x42,0x7f,0x40,0},
    {0x42,0x61,0x51,0x49,0x46}, {0x21,0x41,0x45,0x4b,0x31},
    {0x18,0x14,0x12,0x7f,0x10}, {0x27,0x45,0x45,0x45,0x39},
    {0x3c,0x4a,0x49,0x49,0x30}, {1,0x71,9,5,3},
    {0x36,0x49,0x49,0x49,0x36}, {6,0x49,0x49,0x29,0x1e},
    {0x7e,0x11,0x11,0x11,0x7e}, {0x7f,0x49,0x49,0x49,0x36},
    {0x3e,0x41,0x41,0x41,0x22}, {0x7f,0x41,0x41,0x22,0x1c},
    {0x7f,0x49,0x49,0x49,0x41}, {0x7f,9,9,9,1},
    {0x3e,0x41,0x49,0x51,0x32}, {0x7f,8,8,8,0x7f}, {0,0x41,0x7f,0x41,0},
    {0x20,0x40,0x41,0x3f,1}, {0x7f,8,0x14,0x22,0x41}, {0x7f,0x40,0x40,0x40,0x40},
    {0x7f,2,0x0c,2,0x7f}, {0x7f,4,8,0x10,0x7f}, {0x3e,0x41,0x41,0x41,0x3e},
    {0x7f,9,9,9,6}, {0x3e,0x41,0x51,0x21,0x5e}, {0x7f,9,0x19,0x29,0x46},
    {0x46,0x49,0x49,0x49,0x31}, {1,1,0x7f,1,1}, {0x3f,0x40,0x40,0x40,0x3f},
    {0x1f,0x20,0x40,0x20,0x1f}, {0x3f,0x40,0x38,0x40,0x3f},
    {0x63,0x14,8,0x14,0x63}, {7,8,0x70,8,7}, {0x61,0x51,0x49,0x45,0x43},
    {0,0x36,0x36,0,0}, {8,8,8,8,8}, {0,0x60,0x60,0,0},
    {0x20,0x10,8,4,2}, {0x14,0x14,0x14,0x14,0x14}, {0x7f,0x41,0x41,0,0},
    {0,0,0x41,0x41,0x7f}, {0x40,0x40,0x40,0x40,0x40}
};

static unsigned trace_glyph(unsigned char c) {
    if (c >= 'a' && c <= 'z') c -= 'a' - 'A';
    if (c >= '0' && c <= '9') return c - '0' + 1;
    if (c >= 'A' && c <= 'Z') return c - 'A' + 11;
    switch (c) {
    case ':': return 37; case '-': return 38; case '.': return 39;
    case '/': return 40; case '=': return 41; case '[': return 42;
    case ']': return 43; case '_': return 44; default: return 0;
    }
}

static void trace_cell(volatile uint32_t *pixels, unsigned column, unsigned row,
                       unsigned char c, uint32_t color, unsigned top) {
    const unsigned char *glyph = trace_font[trace_glyph(c)];
    unsigned pitch = chiron_trace_log.stride / 4;
    for (unsigned dx = 0; dx < 5; ++dx)
        for (unsigned dy = 0; dy < 7; ++dy)
            for (unsigned sx = 0; sx < 2; ++sx)
                for (unsigned sy = 0; sy < 2; ++sy)
                    if (glyph[dx] & (1u << dy))
                        pixels[(uint64_t)(top + row * 18 + dy * 2 + sy) * pitch +
                               8 + column * 12 + dx * 2 + sx] = color;
}

static void trace_page(volatile uint32_t *pixels, unsigned cols) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    for (unsigned r = 0; r < trace_viewport_height(); ++r)
        for (unsigned c = 0; c < log->width; ++c) pixels[(uint64_t)r * (log->stride / 4) + c] = 0;
    for (unsigned i = 0; i < cols && log->build_id[i]; ++i)
        trace_cell(pixels, i, 0, (unsigned char)log->build_id[i], 0x80e0c0, 8);
    trace_x = trace_y = 0;
}

static void trace_draw(void) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    if (!trace_geometry()) return;
    uint64_t base = log->render_base ? log->render_base : log->video_phys;
    uint64_t borrowed = 0;
    int window = 0;
    int mapped = trace_translation(base, log->video_phys) &&
                 trace_translation(base + log->video_bytes - 1, log->video_phys + log->video_bytes - 1);
    if (!mapped && log->mapping_mode < 2 && (trace_sctlr() & 1) && trace_boot_mapping()) {
#ifdef CHIRON_TRACE_HOST_TEST
        borrowed = ct_host_window_open(log->boot_root);
#else
        borrowed = chiron_trace_window_open(log->boot_root);
#endif
        window = 1;
        mapped = trace_translation(base, log->video_phys) &&
                 trace_translation(base + log->video_bytes - 1, log->video_phys + log->video_bytes - 1);
    }
    if (mapped) {
        unsigned cols = (log->width - 16) / 12, rows = (trace_viewport_height() - 64) / 18;
        uint64_t total = (uint64_t)log->text_dropped + log->text_used;
        int reset = trace_repaint || trace_painted_base != base || trace_painted > total ||
                    total - trace_painted > log->text_used || trace_painted_fault != log->fault_valid;
        unsigned count = reset ? log->text_used : (unsigned)(total - trace_painted);
        if (reset && trace_panic_start_valid && trace_panic_start <= total &&
            total - trace_panic_start < count)
            count = (unsigned)(total - trace_panic_start);
        /* A bounded last page is enough for display; the full ring remains exportable. */
        if (reset && count > cols * (rows - 1)) count = cols * (rows - 1);
        unsigned start = (log->text_write + CT_TEXT_BYTES - count) % CT_TEXT_BYTES;
        uint32_t color = log->fault_valid >= 2 ? 0xff6060 : 0xffffff;
#ifdef CHIRON_TRACE_HOST_TEST
        volatile uint32_t *pixels = ct_host_pixels(base);
#else
        volatile uint32_t *pixels = (volatile uint32_t *)(uintptr_t)base;
#endif
        if (reset) trace_page(pixels, cols);
        for (unsigned i = 0; i < count; ++i) {
            unsigned char c = (unsigned char)log->text[(start + i) % CT_TEXT_BYTES];
            if (c == '\r') continue;
            if (trace_y >= rows) trace_page(pixels, cols);
            if (c == '\n') { trace_x = 0; ++trace_y; continue; }
            trace_cell(pixels, trace_x, trace_y, c, color, 40);
            if (++trace_x >= cols) { trace_x = 0; ++trace_y; }
        }
        trace_painted = total; trace_painted_base = base;
        trace_painted_fault = log->fault_valid; trace_repaint = 0;
        ++log->paints;
        if (log->mapping_mode == 2) ++log->native_paints;
#ifndef CHIRON_TRACE_HOST_TEST
        __asm__ volatile("dsb sy" ::: "memory");
#endif
    } else {
        ++log->render_refused;
    }
    if (window) {
#ifdef CHIRON_TRACE_HOST_TEST
        ct_host_window_close(borrowed);
#else
        chiron_trace_window_close(borrowed);
#endif
    }
}

static int trace_enter(uint64_t *flags) {
    *flags = trace_mask();
    if (trace_busy) { ++chiron_trace_log.reentries; trace_restore(*flags); return 0; }
    trace_busy = 1;
    trace_header();
    return 1;
}
static void trace_leave(uint64_t flags, int draw) {
    if (draw) trace_draw();
    trace_busy = 0;
    trace_restore(flags);
}

void boot_progress_stage(const char *message) {
    uint64_t flags;
    if (!trace_enter(&flags)) return;
    struct chiron_trace_storage *log = &chiron_trace_log;
    ++log->sequence;
    struct chiron_trace_event *event = &log->events[log->event_write % CT_EVENT_COUNT];
    event->sequence = log->sequence;
    trace_copy(event->text, message, sizeof(event->text));
    if (log->event_write >= CT_EVENT_COUNT) ++log->event_dropped;
    ++log->event_write;
    trace_text("\n["); trace_hex(log->sequence); trace_text("] ");
    trace_text(message); trace_char('\n');
    trace_leave(flags, 1);
}

static void trace_stage_format(const char *format, ...) {
    char text[192];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof(text), format, args);
    va_end(args);
    boot_progress_stage(text);
}

void chiron_trace_initializer(unsigned returned, unsigned subsystem, unsigned rank,
                              uintptr_t function, uintptr_t argument) {
    trace_stage_format("INIT %s SUB=%u RANK=%u PC=%016llx ARG=%016llx",
                       returned ? "RETURN" : "ENTER", subsystem, rank,
                       (unsigned long long)function, (unsigned long long)argument);
}

void chiron_trace_mapping(const char *stage, uint64_t physical, uint64_t virtual_address,
                          uint64_t bytes, unsigned flags) {
    trace_stage_format("%s PA=%016llx VA=%016llx SIZE=%llx FLAGS=%x", stage,
                       (unsigned long long)physical, (unsigned long long)virtual_address,
                       (unsigned long long)bytes, flags);
}

void chiron_trace_video(const char *stage, uint64_t base, uint64_t width, uint64_t height,
                        uint64_t stride, uint64_t depth) {
    trace_stage_format("%s BASE=%016llx W=%llu H=%llu ROW=%llu DEPTH=%llu", stage,
                       (unsigned long long)base, (unsigned long long)width,
                       (unsigned long long)height, (unsigned long long)stride,
                       (unsigned long long)depth);
}

void chiron_trace_init(uint64_t physical, unsigned width, unsigned height, unsigned stride,
                       unsigned depth, uint64_t virtual_base, uint64_t physical_base) {
    chiron_trace_log.video_phys = physical;
    chiron_trace_log.width = width; chiron_trace_log.height = height;
    chiron_trace_log.stride = stride; chiron_trace_log.depth = depth;
    chiron_trace_log.virt_base = virtual_base; chiron_trace_log.phys_base = physical_base;
    chiron_trace_log.render_base = physical;
    chiron_trace_log.mapping_mode = 1;
    trace_ready = 1;
    boot_progress_stage(chiron_trace_build_id);
    boot_progress_stage("source arm_init entered; early records retained");
}

void chiron_trace_native_map(void) {
#ifndef CHIRON_TRACE_HOST_TEST
    if (trace_geometry() && chiron_trace_log.mapping_mode != 2) {
        boot_progress_stage("native framebuffer mapping enter");
        chiron_trace_log.render_base = ml_io_map(chiron_trace_log.video_phys,
                                               (uintptr_t)chiron_trace_log.video_bytes);
        chiron_trace_log.mapping_mode = 2;
        boot_progress_stage("native framebuffer mapping ready; fallback window retired");
    }
#endif
}

void chiron_trace_use_native_vectors(void) {
#ifndef CHIRON_TRACE_HOST_TEST
    uint64_t vectors = chiron_trace_log.native_vectors;
    if (vectors) {
        __asm__ volatile("msr VBAR_EL1, %0\nisb" :: "r"(vectors) : "memory");
        boot_progress_stage("CPU bootstrap returned; native exception vectors installed");
    }
#endif
}

void boot_progress_init(void) { boot_progress_stage("platform expert initialized"); }
void boot_progress_start(void) { boot_progress_stage("clock service created"); }
int boot_progress_active(void) { return trace_ready != 0; }
void boot_progress_bind(uintptr_t base, unsigned width, unsigned height, unsigned row, unsigned depth) {
    /* Keep our own native mapping when the normal video console is replaced/cleared. */
    trace_repaint = 1;
    chiron_trace_video("native video console bound", base, width, height, row, depth);
}
void boot_progress_putc(char c) {
    uint64_t flags;
    if (!trace_enter(&flags)) return;
    trace_char(c);
    trace_leave(flags, c == '\n');
}

void chiron_trace_console(const char *text, unsigned size) {
    while (size) {
        unsigned chunk = size > 256 ? 256 : size;
        uint64_t flags;
        if (!trace_enter(&flags)) return;
        int newline = 0;
        for (unsigned i = 0; i < chunk; ++i) {
            trace_char(text[i]);
            if (text[i] == '\n') newline = 1;
        }
        trace_leave(flags, newline);
        text += chunk;
        size -= chunk;
    }
}

__attribute__((noinline))
void chiron_trace_fault_checkpoint(void) { __asm__ volatile("nop\nnop\nnop" ::: "memory"); }

__attribute__((noinline))
void chiron_trace_panic_checkpoint(void) { __asm__ volatile("nop\nnop\nnop" ::: "memory"); }

__attribute__((noinline))
void chiron_trace_console_checkpoint(void) { __asm__ volatile("nop\nnop\nnop" ::: "memory"); }

void chiron_trace_exception(uint64_t pc, uint64_t esr, uint64_t far, uint64_t sp, uint64_t spsr) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    uint64_t flags = trace_mask();
    log->fault_valid = 1; log->pc = pc; log->esr = esr; log->far = far;
    log->sp = sp; log->spsr = spsr; log->sctlr = trace_sctlr(); log->tcr = trace_tcr();
    ++log->exception_count;
    if (trace_busy) { ++log->reentries; trace_restore(flags); return; }
    trace_busy = 1;
    trace_header();
    trace_text("\nEXCEPTION - MAY BE RECOVERABLE\nPC "); trace_hex(pc);
    trace_text("\nESR "); trace_hex(esr); trace_text("\nFAR "); trace_hex(far);
    trace_text("\nSP "); trace_hex(sp); trace_text("\nSPSR "); trace_hex(spsr);
    trace_text("\nSCTLR "); trace_hex(log->sctlr); trace_text("\nTCR "); trace_hex(log->tcr);
    trace_char('\n');
    trace_leave(flags, 1);
    chiron_trace_fault_checkpoint();
}

static void trace_user_abort_text(void) {
    const struct chiron_trace_storage *log = &chiron_trace_log;
    trace_text("\nLAST UNHANDLED USER ABORT PID "); trace_hex(log->user_abort_pid);
    trace_text("\nUSER PC "); trace_hex(log->user_abort_pc);
    trace_text("\nUSER ESR "); trace_hex(log->user_abort_esr);
    trace_text("\nUSER FAR "); trace_hex(log->user_abort_far);
    unsigned ec = (unsigned)(log->user_abort_esr >> 26);
    if ((ec != 0x20 && ec != 0x21 && ec != 0x24 && ec != 0x25) ||
        (log->user_abort_esr & 0x400))
        trace_text(" NOT VALID FOR THIS EC");
    trace_text("\nUSER MACH CODE "); trace_hex(log->user_abort_code);
    trace_text("\nUSER SP "); trace_hex(log->user_abort_sp);
    trace_text("\nUSER SPSR "); trace_hex(log->user_abort_spsr);
    trace_char('\n');
}

static void trace_signal_text(void) {
    const struct chiron_trace_storage *log = &chiron_trace_log;
    trace_text("\nLAST USER SIGNAL PID "); trace_hex(log->signal_pid);
    trace_text(" SIG "); trace_hex(log->signal_number);
    trace_text("\nMACH EXCEPTION "); trace_hex(log->signal_exception);
    trace_text(" CODE "); trace_hex(log->signal_code);
    trace_text("\nMACH ADDRESS "); trace_hex(log->signal_subcode);
    trace_char('\n');
}

void chiron_trace_user_abort(unsigned pid, uint64_t pc, uint64_t esr, uint64_t far,
                             uint64_t sp, uint64_t spsr, uint64_t code) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    uint64_t flags = trace_mask();
    log->extension_version = 1;
    ++log->user_abort_count;
    log->user_abort_pid = pid; log->user_abort_pc = pc;
    log->user_abort_esr = esr; log->user_abort_far = far;
    log->user_abort_sp = sp; log->user_abort_spsr = spsr; log->user_abort_code = code;
    if (trace_busy) { ++log->reentries; trace_restore(flags); return; }
    trace_busy = 1;
    trace_header();
    trace_user_abort_text();
    trace_leave(flags, 1);
}

void chiron_trace_signal(unsigned pid, unsigned signal, unsigned exception,
                         uint64_t code, uint64_t subcode) {
    struct chiron_trace_storage *log = &chiron_trace_log;
    uint64_t flags = trace_mask();
    log->extension_version = 1;
    ++log->signal_count;
    log->signal_pid = pid; log->signal_number = signal; log->signal_exception = exception;
    log->signal_code = code; log->signal_subcode = subcode;
    if (trace_busy) { ++log->reentries; trace_restore(flags); return; }
    trace_busy = 1;
    trace_header();
    trace_signal_text();
    trace_leave(flags, 1);
}

void chiron_trace_panic(const char *format, va_list *arguments, unsigned long caller) {
    uint64_t flags = trace_mask();
    chiron_trace_log.fault_valid = 2;
    chiron_trace_log.panic_caller = caller;
    ++chiron_trace_log.panic_count;
    if (trace_busy) { ++chiron_trace_log.reentries; trace_restore(flags); return; }
    trace_busy = 1;
    trace_header();
    trace_panic_start = (uint64_t)chiron_trace_log.text_dropped + chiron_trace_log.text_used;
    trace_panic_start_valid = 1;
    trace_text("\nPANIC CALLER "); trace_hex(caller); trace_char('\n');
    char message[512];
    va_list copy;
    va_copy(copy, *arguments);
    vsnprintf(message, sizeof(message), format ? format : "(null panic format)", copy);
    va_end(copy);
    trace_text(message); trace_char('\n');
    if (chiron_trace_log.signal_count) trace_signal_text();
    if (chiron_trace_log.user_abort_count) trace_user_abort_text();
    if (chiron_trace_log.exception_count) {
        trace_text("\nLAST EXCEPTION PC "); trace_hex(chiron_trace_log.pc);
        trace_text("\nLAST ESR "); trace_hex(chiron_trace_log.esr);
        trace_text("\nLAST FAR "); trace_hex(chiron_trace_log.far);
        trace_text("\nLAST SP "); trace_hex(chiron_trace_log.sp);
        trace_text("\nLAST SPSR "); trace_hex(chiron_trace_log.spsr);
        trace_text("\nLAST SCTLR "); trace_hex(chiron_trace_log.sctlr);
        trace_text("\nLAST TCR "); trace_hex(chiron_trace_log.tcr);
        trace_char('\n');
    }
    trace_leave(flags | 0x3c0, 1);
    chiron_trace_panic_checkpoint();
    chiron_trace_fault_checkpoint();
#ifndef CHIRON_TRACE_HOST_TEST
    /* This observer build is a fail-stop diagnostic, so the cause stays visible. */
    (void)trace_mask();
    for (;;) __asm__ volatile("dsb sy\nwfe" ::: "memory");
#endif
}

void chiron_trace_test(void) {
#ifndef CHIRON_TRACE_HOST_TEST
    unsigned test = 0;
    if (PE_parse_boot_argn("chiron-trace-test", &test, sizeof(test)) && test == 1) {
        boot_progress_stage("explicit diagnostic data-abort injection requested");
        (void)*(volatile uint64_t *)(uintptr_t)CT_TEST_ADDRESS;
    }
#endif
}

void chiron_trace_early_panic(void) {
    trace_ready = 1;
    trace_busy = 0;
    trace_header();
    chiron_trace_log.render_base = chiron_trace_log.video_phys;
    chiron_trace_exception(chiron_trace_log.pc, chiron_trace_log.esr, chiron_trace_log.far,
                           chiron_trace_log.sp, chiron_trace_log.spsr);
    chiron_trace_log.fault_valid = 3;
    boot_progress_stage("EARLY EXCEPTION: CPU STOPPED");
    for (;;) {
#ifndef CHIRON_TRACE_HOST_TEST
        __asm__ volatile("msr DAIFSet, #15\ndsb sy\nwfe" ::: "memory");
#endif
    }
}

void boot_progress_irq(uint64_t pc, uint64_t state, uint64_t sp) {
    (void)pc; (void)state; (void)sp;
    if (++trace_irqs <= 4) boot_progress_stage("native IRQ handler entered");
}
void boot_progress_poll(void) {}
void boot_progress_user_breakpoint(void *state, unsigned esr) { (void)state; (void)esr; boot_progress_stage("user breakpoint"); }
void boot_progress_user_abort(void *state, unsigned esr, uint64_t far) { (void)state; (void)esr; (void)far; boot_progress_stage("user abort"); }
void boot_progress_critical_exit(int pid, char *text, unsigned long bytes) { (void)pid; (void)text; (void)bytes; }
void boot_progress_process_stage(int pid, const char *name, const char *stage) {
    trace_stage_format("PID=%d NAME=%s %s", pid, name ? name : "", stage ? stage : "");
}
void boot_progress_exit(int pid, const char *name, int status, unsigned domain, uint64_t code) {
    trace_stage_format("PROC_PREPAREEXIT HOOK PID=%d NAME=%s STATUS=%d DOMAIN=%u CODE=%llx",
                       pid, name ? name : "", status, domain, (unsigned long long)code);
}
