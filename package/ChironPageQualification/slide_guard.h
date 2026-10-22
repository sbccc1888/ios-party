#ifndef CHIRON_PAGE_QUALIFICATION_SLIDE_GUARD_H
#define CHIRON_PAGE_QUALIFICATION_SLIDE_GUARD_H
#include <stdbool.h>
#include <stdint.h>

/*
 * Conservative candidate admission policy, NOT wired into the kernel.
 * Reject before copying/faulting when slide and VM granules differ.
 * This checks span/index arithmetic only, not pointer chains or authenticity.
 */
static inline bool
chiron_slide_span_allowed(uint64_t vm_page, uint64_t slide_page,
    uint64_t relative, uint64_t range_length, uint64_t page_starts_count)
{
    if ((vm_page != 4096 && vm_page != 16384) || slide_page != vm_page) {
        return false;
    }
    if (relative % slide_page || range_length % slide_page ||
        relative >= range_length || slide_page > range_length - relative) {
        return false;
    }
    uint64_t index = relative / slide_page;
    return index <= UINT32_MAX && index < page_starts_count;
}
#endif
