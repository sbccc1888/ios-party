/* Phone-only ownership check. UEFI and its USB drivers remain resident. */
struct chiron_memory_window {
	uint64_t magic, revision, base, bytes, status, stage;
};

static int
chiron_window_typed(const uint8_t *map, uint64_t size, uint64_t stride,
    uint64_t base, uint64_t bytes, uint32_t expected_type)
{
	if (stride < sizeof(efi_memory_descriptor) || (stride & 7) || stride > size ||
	    size % stride != 0 || bytes == 0 || ((base | bytes) & 0xfff) ||
	    bytes > UINT64_MAX - base)
		return 0;
	uint64_t end = base + bytes;
	for (uint64_t off = 0; off < size; off += stride) {
		const efi_memory_descriptor *d = (const void *)(map + off);
		if (d->pages > (UINT64_MAX - d->phys_start) / 4096)
			return 0;
		uint64_t high = d->phys_start + d->pages * 4096;
		if (d->phys_start < end && high > base &&
		    (d->type != expected_type || (d->attribute & (1ULL << 63))))
			return 0;
	}
	for (uint64_t cursor = base; cursor < end;) {
		uint64_t next = cursor;
		for (uint64_t off = 0; off < size; off += stride) {
			const efi_memory_descriptor *d = (const void *)(map + off);
			uint64_t high = d->phys_start + d->pages * 4096;
			if (d->type == expected_type && d->phys_start <= cursor && high > next)
				next = high;
		}
		if (next == cursor)
			return 0;
		cursor = next < end ? next : end;
	}
	return 1;
}

static int
chiron_window_owned(const uint8_t *map, uint64_t size, uint64_t stride,
    uint64_t base, uint64_t bytes)
{
	return chiron_window_typed(map, size, stride, base, bytes, EfiLoaderData);
}

static int
chiron_preclaimed_window(const struct payload_info *pi)
{
	static const efi_guid guid = {
		0x71c9274c,0xc205,0x4557,{0x9c,0x34,0xf6,0x9b,0x1a,0x75,0xb4,0xc9}
	};
	static uint8_t map[65536] __attribute__((aligned(8)));
	struct chiron_memory_window *window;
	uint64_t size = sizeof(map), key, stride, claim;
	uint32_t version;
	efi_status status = BS->locate_protocol(&guid, NULL, (void **)&window);
	if (EFI_ERROR(status)) {
		print("chiron: preclaimed memory protocol missing %x\n", status);
		return 0;
	}
	if (window->magic != 0x434857494e444f57ULL || window->revision != 1 ||
	    window->base != 0x100000000ULL || window->bytes != 0xbc000000ULL ||
	    window->bytes > MAX_MEM_SIZE || window->status != 0 || window->stage != 6 ||
	    (pi->forced_base != 0 && pi->forced_base != window->base)) {
		print("chiron: invalid preclaimed window\n");
		return 0;
	}
	status = BS->get_memory_map(&size, map, &key, &stride, &version);
	if (EFI_ERROR(status) ||
	    !chiron_window_typed(map, size, stride, window->base, window->bytes, EfiConventionalMemory)) {
		print("chiron: proposed window is not entirely free %x\n", status);
		return 0;
	}
	claim = window->base;
	status = BS->allocate_pages(AllocateAddress, EfiLoaderData, window->bytes >> 12, &claim);
	print("chiron: claim window %x size %x status %x\n", claim, window->bytes, status);
	if (EFI_ERROR(status))
		return 0;
	if (claim != window->base) {
		BS->free_pages(claim, window->bytes >> 12);
		return 0;
	}
	size = sizeof(map);
	status = BS->get_memory_map(&size, map, &key, &stride, &version);
	if (EFI_ERROR(status) ||
	    !chiron_window_owned(map, size, stride, window->base, window->bytes)) {
		print("chiron: allocated window ownership check failed %x\n", status);
		BS->free_pages(claim, window->bytes >> 12);
		return 0;
	}
	win_base = window->base;
	win_size = window->bytes;
	print("phys window %x size %x, allocator ownership verified\n", win_base, win_size);
	return 1;
}
