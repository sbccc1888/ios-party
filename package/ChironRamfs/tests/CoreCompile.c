#include <stddef.h>
#include <stdint.h>
#include "../chiron_ramfs.h"

_Static_assert(sizeof(struct cr_volume) < 32768, "bounded parser allocation");

int
chiron_ramfs_compile_probe(cr_reader reader, void *context, uint64_t length, struct cr_volume *v)
{
	return cr_validate(reader, context, length, v);
}
