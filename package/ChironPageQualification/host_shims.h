/* Minimal field-level host cs_blob; not a claim about the kernel binary ABI. */
struct cs_blob {
    struct cs_blob *csb_next;
    off_t csb_base_offset, csb_start_offset, csb_end_offset;
    vm_size_t csb_mem_size, csb_mem_offset;
    void *csb_mem_kaddr;
    const struct cs_hash *csb_hashtype;
    int csb_hash_pageshift, csb_hash_firstlevel_pageshift;
    const CS_CodeDirectory *csb_cd;
    const uint8_t *csb_native4k_hashes;
    vm_size_t csb_native4k_hashes_size, csb_native4k_hash_count;
};
static int cs_debug;
static unsigned long cs_validate_page_no_hash, cs_validate_page_bad_hash;
#if XNU_NATIVE_CS_HASH_CACHE
static unsigned long cs_native4k_full_blocks, cs_native4k_page_checks, cs_native4k_ready_blobs;
#endif
static int outstanding, read_error, read_short, protect_error, alloc_error;
static int force_round_overflow;
static size_t reads, protection_calls;

static bool round_page_overflow(vm_size_t size, vm_size_t *result)
{
    vm_size_t value;
    if (force_round_overflow || __builtin_add_overflow(size, PAGE_MASK, &value)) return true;
    *result = value & ~PAGE_MASK;
    return false;
}
static kern_return_t kmem_alloc(void *map, vm_offset_t *address, vm_size_t size, int flags, int tag)
{
    if (alloc_error) return KERN_FAILURE;
    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (p == MAP_FAILED) return KERN_FAILURE;
    *address = (uintptr_t)p;
    outstanding++;
    return KERN_SUCCESS;
}
static void kmem_free(void *map, vm_offset_t address, vm_size_t size)
{
    assert(!munmap((void *)address, size));
    outstanding--;
}
static void *kalloc_data(size_t size, int flags)
{
    void *p = malloc(size);
    assert(p);
    outstanding++;
    return p;
}
static void kfree_data(void *p, size_t size)
{
    if (p) { free(p); outstanding--; }
}
static kern_return_t vm_map_protect(void *map, uintptr_t start, uintptr_t end, int prot, int setmax)
{
    protection_calls++;
    if (protect_error) return KERN_FAILURE;
    return mprotect((void *)start, end - start, PROT_READ) ? KERN_FAILURE : KERN_SUCCESS;
}
static int vn_rdwr(int op, vnode_t vp, caddr_t dst, int length, off_t position,
    int segment, int flags, void *cred, int *resid, void *proc)
{
    reads++;
    if (read_error) return read_error;
    if (read_short) { *resid = 1; return 0; }
    if (position < vp->base) return EIO;
    uint64_t relative = (uint64_t)position - (uint64_t)vp->base;
    if (relative > vp->size || (size_t)length > vp->size - relative) return EIO;
    memcpy(dst, vp->data + relative, (size_t)length);
    *resid = 0;
    return 0;
}

/* Record logical 4K span violations without actually touching unmapped memory. */
static uintptr_t traced_src, traced_dst;
static size_t copy_bytes, copy_overrun, rebase_overrun;
static uint32_t callback_index;
static uintptr_t callback_dst;
static uint64_t callback_user;
static unsigned callback_count;
static unsigned long shared_region_pager_copied;
static void *host_traced_memcpy(void *dst, const void *src, size_t size)
{
    uintptr_t d = (uintptr_t)dst, s = (uintptr_t)src;
    if ((s >= traced_dst && s < traced_dst + 32768 && s + size > traced_dst + PAGE_SIZE) ||
        (d >= traced_dst && d < traced_dst + 32768 && d + size > traced_dst + PAGE_SIZE)) {
        rebase_overrun++;
    }
    return memcpy(dst, src, size);
}
static void host_traced_bcopy(const void *src, void *dst, size_t size)
{
    assert((uintptr_t)src >= traced_src && (uintptr_t)dst >= traced_dst);
    assert(size <= 16384);
    copy_bytes += size;
    if (size > PAGE_SIZE) copy_overrun += size - PAGE_SIZE;
    memmove(dst, src, size);
}
struct vm_shared_region_slide_info {
    uint32_t si_slide;
    uint64_t si_start, si_end;
    vm_shared_region_slide_info_entry_t si_slide_info_entry;
};
typedef struct vm_shared_region_slide_info *vm_shared_region_slide_info_t;
struct host_pager {
    uint64_t srp_backing_offset;
    vm_shared_region_slide_info_t srp_slide_info;
};
static kern_return_t vm_shared_region_slide_page(vm_shared_region_slide_info_t si,
    uintptr_t dst, uint64_t user, uint32_t index, uint64_t key)
{
    callback_count++;
    callback_index = index;
    callback_dst = dst;
    callback_user = user;
    return KERN_SUCCESS;
}
