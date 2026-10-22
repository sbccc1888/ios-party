/* Fixtures provide independent signed digests; the algorithms under test are XNU's. */
static unsigned passed;
static size_t context_limit;
static int context_hash;
static uint64_t context_page;
#define CHECK(name, condition) do { \
    bool ok = !!(condition); \
    printf("CASE {\"name\":\"%s\",\"pass\":%s,\"sequence\":%u," \
        "\"code_limit\":%zu,\"hash_type\":%d,\"page_offset\":%" PRIu64 "}\n", \
        name, ok ? "true" : "false", passed, context_limit, context_hash, context_page); \
    if (!ok) return 1; \
    passed++; \
} while (0)

struct fixture {
    uint8_t data[32768];
    uint8_t cd_storage[sizeof(CS_CodeDirectory) + 256];
    struct host_vnode vnode;
    struct cs_blob blob;
};
static void fixture_init(struct fixture *f, size_t limit, int hash_type)
{
    context_limit = limit;
    context_hash = hash_type;
    context_page = 0;
    memset(f, 0, sizeof(*f));
    for (size_t i = 0; i < sizeof(f->data); i++) f->data[i] = (uint8_t)(i * 17 + i / 257);
    f->vnode = (struct host_vnode){f->data, limit, 0};
    const struct cs_hash *md = cs_find_md(hash_type);
    CS_CodeDirectory *cd = (void *)f->cd_storage;
    uint32_t count = (uint32_t)((limit + 16383) / 16384);
    size_t length = sizeof(*cd) + count * md->cs_size;
    cd->magic = htonl(CSMAGIC_CODEDIRECTORY);
    cd->length = htonl((uint32_t)length);
    cd->version = htonl(CS_SUPPORTSCODELIMIT64);
    cd->hashOffset = htonl(sizeof(*cd));
    cd->nCodeSlots = htonl(count);
    cd->codeLimit = htonl((uint32_t)limit);
    cd->hashSize = (uint8_t)md->cs_size;
    cd->hashType = (uint8_t)hash_type;
    cd->pageSize = 14;
    for (uint32_t i = 0; i < count; i++) {
        uint8_t digest[CS_HASH_MAX_SIZE];
        size_t n = MIN((size_t)16384, limit - (size_t)i * 16384);
        /* One-shot API does not call the extracted hash dispatch. */
        if (hash_type == CS_HASHTYPE_SHA1) CC_SHA1(f->data + i * 16384, (CC_LONG)n, digest);
        else if (hash_type == CS_HASHTYPE_SHA384) CC_SHA384(f->data + i * 16384, (CC_LONG)n, digest);
        else CC_SHA256(f->data + i * 16384, (CC_LONG)n, digest);
        memcpy(f->cd_storage + sizeof(*cd) + i * md->cs_size, digest, md->cs_size);
    }
    f->blob = (struct cs_blob){
        .csb_end_offset = (off_t)((limit + PAGE_MASK) & ~PAGE_MASK),
        .csb_mem_size = length, .csb_mem_kaddr = f->cd_storage,
        .csb_hashtype = md, .csb_hash_pageshift = 14, .csb_cd = cd,
    };
}
static void release_fixture(struct fixture *f)
{
    if (f->blob.csb_native4k_hashes) {
        kmem_free(NULL, (uintptr_t)f->blob.csb_native4k_hashes, f->blob.csb_native4k_hashes_size);
        f->blob.csb_native4k_hashes = NULL;
    }
}
static bool page_valid(struct fixture *f, uint64_t relative, unsigned *taint)
{
    size_t bytes = 0;
    bool found = cs_validate_hash(&f->blob, NULL, relative + (uint64_t)f->blob.csb_base_offset,
        f->data + MIN(relative, sizeof(f->data) - 1), &bytes, taint);
    return found && !(*taint & CS_VALIDATE_TAINTED) && bytes == PAGE_SIZE;
}

static int signature_cases(void)
{
    struct fixture f;
    fixture_init(&f, 16384, CS_HASHTYPE_SHA256);
    CS_CodeDirectory *cd = (void *)f.cd_storage;
#if !XNU_NATIVE_CS_HASH_CACHE
    CHECK("default-off-rejects-16K-CodeDirectory",
        cs_validate_codedirectory(cd, f.blob.csb_mem_size) == EBADEXEC);
    cd->pageSize = 12;
    CHECK("default-off-accepts-4K-CodeDirectory-layout",
        cs_validate_codedirectory(cd, f.blob.csb_mem_size) == 0);
    return 0;
#else
    unsigned taint = 0;
    for (int type = CS_HASHTYPE_SHA1; type <= CS_HASHTYPE_SHA384; type++) {
        fixture_init(&f, 16384, type);
        CHECK("enabled-accepts-16K-CodeDirectory", !cs_validate_codedirectory(f.blob.csb_cd, f.blob.csb_mem_size));
        unsigned long blocks = cs_native4k_full_blocks;
        CHECK("complete-original-16K-block", !cs_native4k_prepare(&f.vnode, &f.blob) &&
            f.blob.csb_native4k_hash_count == 4 && cs_native4k_full_blocks == blocks + 1);
        for (size_t page = 0; page < 4; page++) {
            context_page = page * PAGE_SIZE;
            CHECK("all-four-derived-4K-pages-validate", page_valid(&f, page * PAGE_SIZE, &taint) && taint == 0);
            f.data[page * PAGE_SIZE + 123] ^= 1;
            CHECK("each-post-publication-4K-corruption-tainted",
                !page_valid(&f, page * PAGE_SIZE, &taint) && (taint & CS_VALIDATE_TAINTED));
            f.data[page * PAGE_SIZE + 123] ^= 1;
        }
        CHECK("unaligned-page-rejected", !page_valid(&f, 1, &taint) && (taint & CS_VALIDATE_TAINTED));
        CHECK("offset-at-codeLimit-not-covered", !page_valid(&f, 16384, &taint));
        size_t count = f.blob.csb_native4k_hash_count;
        f.blob.csb_native4k_hash_count = 3;
        CHECK("derived-index-at-count-rejected", !page_valid(&f, 12288, &taint) && (taint & CS_VALIDATE_TAINTED));
        f.blob.csb_native4k_hash_count = count;
        size_t size = f.blob.csb_native4k_hashes_size;
        f.blob.csb_native4k_hashes_size = 4 * f.blob.csb_hashtype->cs_size - 1;
        CHECK("derived-table-one-byte-short-rejected", !page_valid(&f, 12288, &taint));
        f.blob.csb_native4k_hashes_size = size;
        release_fixture(&f);
        for (size_t page = 0; page < 4; page++) {
            fixture_init(&f, 16384, type);
            context_page = page * PAGE_SIZE;
            f.data[page * PAGE_SIZE + 7] ^= 1;
            CHECK("each-original-subpage-corruption-rejects-whole-block",
                cs_native4k_prepare(&f.vnode, &f.blob) == EBADEXEC &&
                !f.blob.csb_native4k_hashes && outstanding == 0);
        }
        fixture_init(&f, 16384, type);
        f.cd_storage[sizeof(*cd)] ^= 1;
        CHECK("original-signature-slot-corruption-rejected",
            cs_native4k_prepare(&f.vnode, &f.blob) == EBADEXEC && !outstanding);
    }
    const size_t limits[] = {1, 4095, 4096, 4097, 16383, 16384, 16385, 20481, 32768};
    for (size_t i = 0; i < sizeof(limits) / sizeof(limits[0]); i++) {
        fixture_init(&f, limits[i], CS_HASHTYPE_SHA256);
        CHECK("tail-original-block-authenticates", !cs_native4k_prepare(&f.vnode, &f.blob) &&
            f.blob.csb_native4k_hash_count == (limits[i] + PAGE_MASK) / PAGE_SIZE);
        size_t last = (limits[i] - 1) & ~PAGE_MASK;
        CHECK("tail-page-prefix-valid-NX-on-partial",
            page_valid(&f, last, &taint) && !!(taint & CS_VALIDATE_NX) == !!(limits[i] & PAGE_MASK));
        if (limits[i] & PAGE_MASK) {
            f.data[limits[i]] ^= 1;
            CHECK("unsigned-tail-byte-outside-codeLimit-remains-NX",
                page_valid(&f, last, &taint) && (taint & CS_VALIDATE_NX));
        }
        f.data[limits[i] - 1] ^= 1;
        CHECK("last-signed-byte-corruption-tainted", !page_valid(&f, last, &taint) && (taint & CS_VALIDATE_TAINTED));
        release_fixture(&f);
        CHECK("last-original-block-corruption-not-published",
            cs_native4k_prepare(&f.vnode, &f.blob) == EBADEXEC &&
            !f.blob.csb_native4k_hashes && !outstanding);
    }
    fixture_init(&f, 32768, CS_HASHTYPE_SHA256);
    cd = (void *)f.cd_storage;
    cd->codeLimit64 = htonll(1ULL << 32);
    CHECK("unsupported-64bit-codeLimit-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == ENOTSUP);
    cd->codeLimit64 = htonll(32768);
    CHECK("equal-codeLimit64-accepted", !cs_native4k_prepare(&f.vnode, &f.blob));
    release_fixture(&f);
    cd->codeLimit64 = 0;
    cd->scatterOffset = htonl(1);
    CHECK("scatter-bridge-layout-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == ENOTSUP);
    cd->scatterOffset = 0;
    f.blob.csb_hash_firstlevel_pageshift = 12;
    CHECK("firstlevel-bridge-layout-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == ENOTSUP);
    f.blob.csb_hash_firstlevel_pageshift = 0;
    f.blob.csb_base_offset = -1;
    CHECK("negative-base-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == ENOTSUP);
    f.blob.csb_base_offset = 1;
    CHECK("unaligned-base-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == ENOTSUP);
    f.blob.csb_base_offset = f.vnode.base = 4096;
    CHECK("aligned-fat-slice-base-accepted", !cs_native4k_prepare(&f.vnode, &f.blob) &&
        page_valid(&f, 4096, &taint));
    release_fixture(&f);
    f.blob.csb_base_offset = f.vnode.base = 0;
    cd->nCodeSlots = htonl(1);
    CHECK("slot-count-mismatch-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == ENOTSUP);
    cd->nCodeSlots = htonl(2);
    cd->codeLimit = 0;
    CHECK("zero-codeLimit-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == ENOTSUP);
    cd->codeLimit = htonl(32768);
    uint32_t saved_offset = cd->hashOffset;
    cd->hashOffset = htonl((uint32_t)f.blob.csb_mem_size + 1);
    CHECK("hash-layout-outside-signature-rejected",
        cs_validate_codedirectory(cd, f.blob.csb_mem_size) == EBADEXEC &&
        cs_native4k_prepare(&f.vnode, &f.blob) == EBADEXEC && !outstanding);
    cd->hashOffset = saved_offset;
    CHECK("truncated-signature-slots-rejected",
        cs_validate_codedirectory(cd, f.blob.csb_mem_size - 1) == EBADEXEC);
    cd->hashSize = 31;
    CHECK("hash-size-layout-rejected", cs_validate_codedirectory(cd, f.blob.csb_mem_size) == EBADEXEC);
    cd->hashSize = 32;
    read_error = EIO;
    CHECK("read-failure-not-published", cs_native4k_prepare(&f.vnode, &f.blob) == EIO && !outstanding);
    read_error = 0;
    read_short = 1;
    CHECK("short-read-not-published", cs_native4k_prepare(&f.vnode, &f.blob) == EIO && !outstanding);
    read_short = 0;
    protect_error = 1;
    CHECK("readonly-publication-failure-cleaned", cs_native4k_prepare(&f.vnode, &f.blob) == EPERM &&
        !f.blob.csb_native4k_hashes && !outstanding);
    protect_error = 0;
    alloc_error = 1;
    CHECK("allocation-failure-not-published", cs_native4k_prepare(&f.vnode, &f.blob) == ENOMEM && !outstanding);
    alloc_error = 0;
    force_round_overflow = 1;
    CHECK("injected-round-overflow-rejected", cs_native4k_prepare(&f.vnode, &f.blob) == EOVERFLOW && !outstanding);
    force_round_overflow = 0;
    f.blob.csb_base_offset = f.vnode.base = INT64_MAX & ~(off_t)PAGE_MASK;
    CHECK("base-plus-second-block-signed-overflow-rejected",
        cs_native4k_prepare(&f.vnode, &f.blob) == EOVERFLOW && !outstanding && !f.blob.csb_native4k_hashes);
    CHECK("host-derived-storage-cleaned", outstanding == 0 && protection_calls > 0);
    return 0;
#endif
}

static int slide_cases(void)
{
    context_limit = 0;
    context_hash = 0;
    context_page = 0;
    /* Real XNU structs and sanity functions, not a permissive reimplementation. */
    uint8_t info_memory[256] = {0};
    vm_shared_region_slide_info_entry_t info = (void *)info_memory;
    info->version = 5;
    info->page_size = 16384;
    info->v5.page_starts_count = 2;
    CHECK("v5-sanity-accepts-16K-at-source", vm_shared_region_slide_sanity_check_v5(&info->v5, sizeof(info_memory)) == KERN_SUCCESS);
    info->page_size = 4096;
    CHECK("v5-sanity-rejects-4K", vm_shared_region_slide_sanity_check_v5(&info->v5, sizeof(info_memory)) == KERN_FAILURE);
    CHECK("v3-sanity-accepts-4K", vm_shared_region_slide_sanity_check_v3(&info->v3, sizeof(info_memory)) == KERN_SUCCESS);
    CHECK("v2-sanity-accepts-4K", vm_shared_region_slide_sanity_check_v2(&info->v2, sizeof(info_memory)) == KERN_SUCCESS);
    CHECK("v4-sanity-accepts-4K", vm_shared_region_slide_sanity_check_v4(&info->v4, sizeof(info_memory)) == KERN_SUCCESS);
    info->page_size = 16384;
    CHECK("v2-sanity-rejects-16K", vm_shared_region_slide_sanity_check_v2(&info->v2, sizeof(info_memory)) == KERN_FAILURE);
    CHECK("v3-sanity-rejects-16K", vm_shared_region_slide_sanity_check_v3(&info->v3, sizeof(info_memory)) == KERN_FAILURE);
    CHECK("v4-sanity-rejects-16K", vm_shared_region_slide_sanity_check_v4(&info->v4, sizeof(info_memory)) == KERN_FAILURE);
    CHECK("truncated-v5-starts-array-rejected",
        vm_shared_region_slide_sanity_check_v5(&info->v5, sizeof(info->v5) + 3) == KERN_FAILURE);

    uint8_t *src = calloc(1, 32768), *dst = calloc(1, 32768);
    assert(src && dst);
    traced_src = (uintptr_t)src;
    traced_dst = (uintptr_t)dst;
    struct vm_shared_region_slide_info si = {0, 0, UINT64_MAX, info};
    struct host_pager pager = {0, &si};
    for (uint64_t offset = 0; offset < 16384; offset += PAGE_SIZE) {
        context_page = offset;
        copy_bytes = copy_overrun = callback_count = 0;
        run_pager_prefix(&pager, offset, 0, traced_src, traced_dst, 16384, 0x100000);
        CHECK("16K-copy-exceeds-logical-4K-by-12288", copy_bytes == 16384 && copy_overrun == 12288);
        CHECK("four-4K-faults-reuse-slide-index-zero", callback_count == 1 &&
            callback_index == 0 && callback_dst == traced_dst && callback_user == 0x100000 + offset);
    }
    si.si_start = 16384;
    copy_bytes = copy_overrun = callback_count = 0;
    run_pager_prefix(&pager, 0, 0, traced_src, traced_dst, 16384, 0);
    CHECK("oversize-copy-precedes-outside-range-check", copy_overrun == 12288 && !callback_count);
    si.si_start = 0;
    run_pager_prefix(&pager, 16384, 0, traced_src, traced_dst, 16384, 0);
    CHECK("next-16K-unit-index-one", callback_index == 1);
    run_pager_prefix(&pager, (1ULL << 32) * 16384, 0, traced_src, traced_dst, 16384, 0);
    CHECK("source-uint32-index-wrap-reproduced", callback_index == 0);
    info->v5.page_starts_count = 2;
    info->v5.page_starts[0] = 4096;
    rebase_overrun = 0;
    CHECK("v5-chain-start-4096-outside-4K-logical-page",
        vm_shared_region_slide_page_v5(&si, traced_dst, 0, 0, 0) == KERN_SUCCESS && rebase_overrun >= 2);
    CHECK("v5-page-index-at-count-rejected",
        vm_shared_region_slide_page_v5(&si, traced_dst, 0, 2, 0) == KERN_FAILURE);
    info->v5.page_starts[0] = 16376;
    rebase_overrun = 0;
    CHECK("v5-last-16K-pointer-outside-4K-logical-page",
        vm_shared_region_slide_page_v5(&si, traced_dst, 0, 0, 0) == KERN_SUCCESS && rebase_overrun >= 2);
    info->page_size = 4096;
    copy_bytes = copy_overrun = callback_count = 0;
    run_pager_prefix(&pager, 4096, 0, traced_src, traced_dst, 4096, 0);
    CHECK("4K-copy-span-and-index-control", copy_bytes == 4096 && !copy_overrun && callback_index == 1);
    CHECK("candidate-guard-accepts-complete-4K-span", chiron_slide_span_allowed(4096, 4096, 4096, 8192, 2));
    CHECK("candidate-guard-rejects-16K-on-4K", !chiron_slide_span_allowed(4096, 16384, 0, 16384, 1));
    CHECK("candidate-guard-rejects-zero-slide", !chiron_slide_span_allowed(4096, 0, 0, 16384, 1));
    CHECK("candidate-guard-rejects-misaligned-relative", !chiron_slide_span_allowed(4096, 4096, 1, 8192, 2));
    CHECK("candidate-guard-rejects-partial-range", !chiron_slide_span_allowed(4096, 4096, 0, 4097, 2));
    CHECK("candidate-guard-rejects-index-at-count", !chiron_slide_span_allowed(4096, 4096, 4096, 8192, 1));
    CHECK("candidate-guard-rejects-truncated-uint32-index",
        !chiron_slide_span_allowed(4096, 4096, (1ULL << 32) * 4096, ((1ULL << 32) + 1) * 4096, (1ULL << 32) + 1));
    CHECK("candidate-guard-rejects-range-end-and-underflow",
        !chiron_slide_span_allowed(4096, 4096, 8192, 8192, 3) &&
        !chiron_slide_span_allowed(4096, 4096, UINT64_MAX - 4095, 4096, 3));
    free(src);
    free(dst);
    return 0;
}
int main(void)
{
    if (signature_cases() || slide_cases()) return 1;
    return passed ? 0 : 1;
}
