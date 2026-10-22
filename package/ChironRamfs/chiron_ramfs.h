/* CHRAMFS v1: bounded read-only experiment, independent of N4KROOT.
 * The same parser is used by the kernel overlay and the host boundary tests.
 * Callers provide uint*_t/int64_t/size_t and a bounded, exact-read callback.
 */
#ifndef CHIRON_RAMFS_H
#define CHIRON_RAMFS_H

#define CR_PAGE 4096U
#define CR_MAX_IMAGE (32U * 1024U * 1024U)
#define CR_MAX_NODES 128U
#define CR_MAX_NAME 79U
#define CR_MAX_DEPTH 16U
#define CR_MAX_PATH 1023U
#define CR_HEADER 64U
#define CR_RECORD 128U
#define CR_DIR 1U
#define CR_FILE 2U
#define CR_INVALID 1
#define CR_IO 2
#define CR_NOTFOUND 3

typedef int (*cr_reader)(void *, uint64_t, void *, size_t);

struct cr_entry {
	uint32_t parent, kind, mode, uid, gid, name_len;
	uint64_t offset, size;
	char name[80];
};

struct cr_volume {
	uint64_t image_size, data_offset;
	uint32_t count;
	struct cr_entry entries[CR_MAX_NODES];
};

static inline uint32_t
cr_u32(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
	       (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static inline uint64_t
cr_u64(const uint8_t *p)
{
	return cr_u32(p) | (uint64_t)cr_u32(p + 4) << 32;
}

static inline int
cr_equal(const void *left, const void *right, size_t size)
{
	const uint8_t *a = left, *b = right;
	for (size_t i = 0; i < size; ++i) {
		if (a[i] != b[i]) {
			return 0;
		}
	}
	return 1;
}

static inline uint64_t
cr_align(uint64_t n)
{
	/* Only called after n has been bounded by CR_MAX_IMAGE. */
	return (n + CR_PAGE - 1) & ~(uint64_t)(CR_PAGE - 1);
}

static inline int
cr_zero(cr_reader read, void *context, uint64_t offset, uint64_t size)
{
	uint8_t bytes[128];
	while (size) {
		size_t n = size > sizeof(bytes) ? sizeof(bytes) : (size_t)size;
		if (read(context, offset, bytes, n)) {
			return CR_IO;
		}
		for (size_t i = 0; i < n; ++i) {
			if (bytes[i]) {
				return CR_INVALID;
			}
		}
		offset += n;
		size -= n;
	}
	return 0;
}

static inline int
cr_name_valid(const char *name, uint32_t length)
{
	if (!length || length > CR_MAX_NAME ||
	    (length == 1 && name[0] == '.') ||
	    (length == 2 && name[0] == '.' && name[1] == '.')) {
		return 0;
	}
	for (uint32_t i = 0; i < length; ++i) {
		uint8_t c = (uint8_t)name[i];
		if (c < 32 || c > 126 || c == '/' || c == '\\') {
			return 0;
		}
	}
	return 1;
}

static inline int
cr_validate(cr_reader read, void *context, uint64_t device_size, struct cr_volume *v)
{
	uint8_t h[CR_HEADER], record[CR_RECORD];
	uint64_t cursor, metadata_end;
	uint32_t path_lengths[CR_MAX_NODES], depths[CR_MAX_NODES];
	int error;
	/* A failed parse never publishes a partly valid volume. */
	v->count = 0;
	if (device_size < CR_PAGE || device_size > CR_MAX_IMAGE ||
	    (device_size & (CR_PAGE - 1))) {
		return CR_INVALID;
	}
	if (read(context, 0, h, sizeof(h))) {
		return CR_IO;
	}
	uint32_t count = cr_u32(h + 20);
	metadata_end = CR_HEADER + (uint64_t)count * CR_RECORD;
	if (!cr_equal(h, "CHRAMFS", 8) || cr_u32(h + 8) != 1 ||
	    cr_u32(h + 12) != CR_HEADER || cr_u32(h + 16) != CR_RECORD ||
	    !count || count > CR_MAX_NODES || cr_u64(h + 24) != device_size ||
	    cr_u64(h + 32) != CR_HEADER || cr_u64(h + 40) != cr_align(metadata_end)) {
		return CR_INVALID;
	}
	for (uint32_t i = 48; i < CR_HEADER; ++i) {
		if (h[i]) {
			return CR_INVALID;
		}
	}
	cursor = cr_u64(h + 40);
	if (cursor > device_size) {
		return CR_INVALID;
	}
	for (uint32_t i = 0; i < count; ++i) {
		struct cr_entry *e = &v->entries[i];
		if (read(context, CR_HEADER + (uint64_t)i * CR_RECORD, record, sizeof(record))) {
			return CR_IO;
		}
		e->parent = cr_u32(record);
		e->kind = cr_u32(record + 4);
		e->mode = cr_u32(record + 8);
		e->uid = cr_u32(record + 12);
		e->gid = cr_u32(record + 16);
		e->name_len = cr_u32(record + 24);
		e->offset = cr_u64(record + 32);
		e->size = cr_u64(record + 40);
		for (uint32_t n = 0; n < sizeof(e->name); ++n) {
			e->name[n] = (char)record[48 + n];
		}
		if (cr_u32(record + 20) || cr_u32(record + 28) || (e->mode & ~0777U) ||
		    (e->kind != CR_DIR && e->kind != CR_FILE) || e->name_len > CR_MAX_NAME) {
			return CR_INVALID;
		}
		for (uint32_t n = e->name_len; n < sizeof(e->name); ++n) {
			if (e->name[n]) {
				return CR_INVALID;
			}
		}
		if (!i) {
			if (e->parent || e->kind != CR_DIR || e->name_len) {
				return CR_INVALID;
			}
			path_lengths[i] = 0;
			depths[i] = 0;
		} else {
			if (e->parent >= i || v->entries[e->parent].kind != CR_DIR ||
			    !cr_name_valid(e->name, e->name_len)) {
				return CR_INVALID;
			}
			path_lengths[i] = path_lengths[e->parent] + 1 + e->name_len;
			depths[i] = depths[e->parent] + 1;
			if (path_lengths[i] > CR_MAX_PATH || depths[i] > CR_MAX_DEPTH) {
				return CR_INVALID;
			}
			for (uint32_t j = 1; j < i; ++j) {
				struct cr_entry *other = &v->entries[j];
				if (other->parent == e->parent && other->name_len == e->name_len &&
				    cr_equal(other->name, e->name, e->name_len)) {
					return CR_INVALID;
				}
			}
		}
		if (e->kind == CR_DIR) {
			if (e->offset || e->size) {
				return CR_INVALID;
			}
		} else {
			/* Canonical contiguous extents prohibit overlap, gaps and wraparound.
			 * An empty file consumes no pages but still has an exact offset.
			 */
			if (e->offset != cursor || e->size > device_size - cursor) {
				return CR_INVALID;
			}
			uint64_t end = cursor + e->size;
			uint64_t next = cr_align(end);
			if (next > device_size) {
				return CR_INVALID;
			}
			error = cr_zero(read, context, end, next - end);
			if (error) {
				return error;
			}
			cursor = next;
		}
	}
	if (cursor != device_size) {
		return CR_INVALID;
	}
	error = cr_zero(read, context, metadata_end, cr_u64(h + 40) - metadata_end);
	if (error) {
		return error;
	}
	v->image_size = device_size;
	v->data_offset = cr_u64(h + 40);
	v->count = count;
	return 0;
}

static inline int
cr_lookup(const struct cr_volume *v, uint32_t parent, const char *name,
    uint32_t length, uint32_t *index)
{
	if (parent >= v->count || v->entries[parent].kind != CR_DIR) {
		return CR_INVALID;
	}
	if (length == 1 && name[0] == '.') {
		*index = parent;
		return 0;
	}
	if (length == 2 && name[0] == '.' && name[1] == '.') {
		*index = v->entries[parent].parent;
		return 0;
	}
	if (!cr_name_valid(name, length)) {
		return CR_INVALID;
	}
	for (uint32_t i = 1; i < v->count; ++i) {
		const struct cr_entry *e = &v->entries[i];
		if (e->parent == parent && e->name_len == length && cr_equal(e->name, name, length)) {
			*index = i;
			return 0;
		}
	}
	return CR_NOTFOUND;
}

static inline int
cr_map(const struct cr_volume *v, uint32_t index, int64_t offset,
    uint64_t requested, uint32_t block_size, uint64_t *block, uint64_t *run)
{
	if (index >= v->count || v->entries[index].kind != CR_FILE ||
	    offset < 0 || !block_size || CR_PAGE % block_size || !requested) {
		return CR_INVALID;
	}
	const struct cr_entry *e = &v->entries[index];
	uint64_t allocated = cr_align(e->size);
	if ((uint64_t)offset >= allocated || (uint64_t)offset % block_size ||
	    requested > allocated - (uint64_t)offset) {
		return CR_INVALID;
	}
	*block = (e->offset + (uint64_t)offset) / block_size;
	*run = allocated - (uint64_t)offset;
	return 0;
}

static inline int
cr_read(const struct cr_volume *v, cr_reader read, void *context,
    uint32_t index, int64_t offset, void *out, size_t requested, size_t *actual)
{
	*actual = 0;
	if (index >= v->count || v->entries[index].kind != CR_FILE || offset < 0) {
		return CR_INVALID;
	}
	const struct cr_entry *e = &v->entries[index];
	if ((uint64_t)offset >= e->size) {
		return 0;
	}
	uint64_t remaining = e->size - (uint64_t)offset;
	size_t n = remaining < requested ? (size_t)remaining : requested;
	if (n && read(context, e->offset + (uint64_t)offset, out, n)) {
		return CR_IO;
	}
	*actual = n;
	return 0;
}
#endif
