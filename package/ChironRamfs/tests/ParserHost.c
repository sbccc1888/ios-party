/* Thin exports around the production parser, for differential Python tests. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "../chiron_ramfs.h"

struct input {
	const uint8_t *bytes;
	size_t size;
	int fail;
};

static int
read_input(void *context, uint64_t offset, void *out, size_t size)
{
	struct input *in = context;
	if (in->fail || offset > in->size || size > in->size - offset) {
		return 1;
	}
	memcpy(out, in->bytes + offset, size);
	return 0;
}

int
parse(const void *bytes, size_t size, uint64_t claimed, int fail)
{
	struct input in = { bytes, size, fail };
	struct cr_volume v;
	return cr_validate(read_input, &in, claimed, &v);
}

int
lookup(const void *bytes, size_t size, uint32_t parent, const char *name,
    uint32_t length, uint32_t *index)
{
	struct input in = { bytes, size, 0 };
	struct cr_volume v;
	int error = cr_validate(read_input, &in, size, &v);
	return error ? error : cr_lookup(&v, parent, name, length, index);
}

int
map(const void *bytes, size_t size, uint32_t index, int64_t offset,
    uint64_t requested, uint32_t block_size, uint64_t *block, uint64_t *run)
{
	struct input in = { bytes, size, 0 };
	struct cr_volume v;
	int error = cr_validate(read_input, &in, size, &v);
	return error ? error : cr_map(&v, index, offset, requested, block_size, block, run);
}

int
read_file(const void *bytes, size_t size, uint32_t index, int64_t offset,
    void *out, size_t requested, size_t *actual)
{
	struct input in = { bytes, size, 0 };
	struct cr_volume v;
	int error = cr_validate(read_input, &in, size, &v);
	return error ? error : cr_read(&v, read_input, &in, index, offset, out, requested, actual);
}
