/* Executes actual overlaid mockfs translation units with host API adapters.
 * Device reads use bounded fixture bytes. VM/pagein is deliberately unavailable.
 */
#include "KernelShim.h"
#include <miscfs/mockfs/mockfs.h>
#include <miscfs/mockfs/mockfs_vnops.h>

int mockfs_mountroot(mount_t, vnode_t, vfs_context_t);
int mockfs_root(mount_t, vnode_t *, vfs_context_t);
int mockfs_unmount(mount_t, int, vfs_context_t);
int mockfs_lookup(struct vnop_lookup_args *);
int mockfs_getattr(struct vnop_getattr_args *);
int mockfs_read(struct vnop_read_args *);
int mockfs_blockmap(struct vnop_blockmap_args *);
int mockfs_reclaim(struct vnop_reclaim_args *);
int mockfs_pagein(struct vnop_pagein_args *);
extern const struct vnodeopv_entry_desc mockfs_vnodeop_entries[];
extern struct vfsops mockfs_vfsops;
#define DESC(n) struct vnodeop_desc vnop_##n##_desc
DESC(default); DESC(lookup); DESC(create); DESC(open); DESC(mknod); DESC(close);
DESC(access); DESC(getattr); DESC(setattr); DESC(read); DESC(write); DESC(ioctl);
DESC(select); DESC(mmap); DESC(fsync); DESC(remove); DESC(link); DESC(rename);
DESC(mkdir); DESC(rmdir); DESC(symlink); DESC(readlink); DESC(inactive); DESC(reclaim);
DESC(strategy); DESC(pathconf); DESC(advlock); DESC(pagein); DESC(pageout); DESC(blktooff);
DESC(offtoblk); DESC(blockmap); DESC(readdir);
DESC(bwrite); DESC(copyfile);
#undef DESC
int host_allocations, host_fail_allocation = -1;
static int read_error, residual_error;
static uint8_t *image;
static size_t image_size;
static vnode_t vnodes[CR_MAX_NODES + 4];
static uint32_t vnode_count;
static uint64_t cluster_size, pagein_size;

void *
host_allocate(size_t size)
{
	if (host_fail_allocation == 0) {
		return NULL;
	}
	if (host_fail_allocation > 0) {
		--host_fail_allocation;
	}
	void *p = calloc(1, size);
	assert(p);
	++host_allocations;
	return p;
}

void
host_free(void *p)
{
	assert(p && host_allocations > 0);
	--host_allocations;
	free(p);
}

int
buf_meta_bread(vnode_t device __unused, daddr64_t block, int size, void *cred __unused, buf_t *out)
{
	*out = calloc(1, sizeof(**out));
	assert(*out && size == CR_PAGE && block >= 0);
	if (read_error) {
		return EIO;
	}
	size_t block_size = device->v_mount->mnt_devblocksize;
	uint64_t offset = (uint64_t)block * block_size;
	if (offset > image_size || (uint64_t)size > image_size - offset) {
		return EIO;
	}
	memcpy((*out)->bytes, image + offset, (size_t)size);
	(*out)->residual = residual_error ? 1 : 0;
	return 0;
}

int
vnode_create(int flavor __unused, size_t size __unused, struct vnode_fsparam *p, vnode_t *out)
{
	assert(vnode_count < sizeof(vnodes) / sizeof(vnodes[0]));
	*out = calloc(1, sizeof(**out));
	assert(*out);
	(*out)->v_data = p->vnfs_fsnode;
	(*out)->v_mount = p->vnfs_mp;
	(*out)->v_type = p->vnfs_vtype;
	(*out)->host_filesize = p->vnfs_filesize;
	vnodes[vnode_count++] = *out;
	return 0;
}

int
vflush(mount_t mount __unused, vnode_t exclude __unused, int flags __unused)
{
	while (vnode_count) {
		vnode_t vp = vnodes[--vnode_count];
		struct vnop_reclaim_args args = { vp };
		assert(mockfs_reclaim(&args) == 0);
		free(vp);
	}
	return 0;
}

int
cluster_read(vnode_t vp, struct uio *u, uint64_t size, int flags __unused)
{
	cluster_size = size;
	if (u->offset < 0) {
		return EINVAL;
	}
	if ((uint64_t)u->offset >= size) {
		return 0;
	}
	size_t n = u->resid;
	if ((uint64_t)n > size - (uint64_t)u->offset) {
		n = (size_t)(size - (uint64_t)u->offset);
	}
	/* Contract adapter only: actual production blockmap is used for each page.
	 * This is not cluster_read's VM/cache implementation.
	 */
	while (n) {
		off_t page = u->offset & ~(off_t)(CR_PAGE - 1);
		size_t within = (size_t)(u->offset - page);
		size_t part = CR_PAGE - within;
		if (part > n) {
			part = n;
		}
		daddr64_t block = -1;
		size_t run = 0;
		int poff = -1;
		struct vnop_blockmap_args args = { vp, page, CR_PAGE, &block, &run, &poff };
		int error = mockfs_blockmap(&args);
		if (error) {
			return error;
		}
		assert(run >= CR_PAGE);
		uint64_t at = (uint64_t)block * vp->v_mount->mnt_devblocksize + within;
		assert(at <= image_size && part <= image_size - at);
		uiomove((caddr_t)(image + at), (int)part, u);
		n -= part;
	}
	return 0;
}

int
cluster_pagein(vnode_t vp __unused, upl_t pl __unused, upl_offset_t po __unused,
    off_t fo __unused, size_t size __unused, uint64_t file_size, int flags __unused)
{
	pagein_size = file_size;
	return EIO; /* No fake pagein/VM qualification. */
}

static int
find(vnode_t parent, const char *name, vnode_t *out, int op, int flags)
{
	/* Literal storage cannot be modified by the new lookup implementation. */
	struct componentname c = { (char *)name, (int)strlen(name), op, flags };
	struct vnop_lookup_args a = { parent, out, &c, NULL };
	return mockfs_lookup(&a);
}

static int
directory(vnode_t vp, struct uio *u, int *eof, int *count)
{
	for (const struct vnodeopv_entry_desc *p = mockfs_vnodeop_entries; p->opve_op; ++p) {
		if (p->opve_op == &vnop_readdir_desc) {
			struct vnop_readdir_args a = { vp, u, 0, eof, count };
			return ((int (*)(struct vnop_readdir_args *))p->opve_impl)(&a);
		}
	}
	return ENOTSUP;
}

static void
read_check(vnode_t vp, const char *prefix, uint64_t size)
{
	uint8_t buffer[10032];
	assert(size < sizeof(buffer));
	memset(buffer, 0xa5, sizeof(buffer));
	struct uio u = { 0, sizeof(buffer), buffer };
	struct vnop_read_args a = { vp, &u, 0 };
	assert(mockfs_read(&a) == 0 && cluster_size == size);
	assert(u.offset == (off_t)size && u.resid == sizeof(buffer) - size);
	assert(!memcmp(buffer, prefix, strlen(prefix)) && buffer[size] == 0xa5);
	u.offset = (off_t)size;
	assert(mockfs_read(&a) == 0 && u.offset == (off_t)size);
	assert(vp->host_filesize == size);
}

static void
test_new(mount_t mp)
{
	assert(mockfs_mountroot(mp, mp->mnt_devvp, NULL) == 0);
	assert(((mockfs_mount_t)mp->mnt_data)->ramfs);
	assert(mp->mnt_flag & MNT_RDONLY);
	vnode_t root, usr, lib, sbin, dyld, launch, other, empty, ignored;
	assert(mockfs_root(mp, &root, NULL) == 0);
	assert(find(root, "usr", &usr, LOOKUP, 0) == 0 && usr != root);
	assert(find(usr, "lib", &lib, LOOKUP, 0) == 0 && lib != usr);
	assert(find(root, "sbin", &sbin, LOOKUP, 0) == 0 && sbin != root);
	assert(find(lib, "dyld", &dyld, LOOKUP, ISLASTCN) == 0);
	assert(find(sbin, "launchd", &launch, LOOKUP, ISLASTCN) == 0);
	assert(find(sbin, "dyld", &other, LOOKUP, ISLASTCN) == 0 && other != dyld);
	assert(find(lib, "empty", &empty, LOOKUP, ISLASTCN) == 0);
	assert(find(root, "dyld", &ignored, LOOKUP, ISLASTCN) == ENOENT);
	assert(find(root, "lib", &ignored, LOOKUP, ISLASTCN) == ENOENT);
	assert(find(usr, ".", &ignored, LOOKUP, 0) == 0 && ignored == usr);
	assert(find(lib, "..", &ignored, LOOKUP, 0) == 0 && ignored == usr);
	assert(find(root, "..", &ignored, LOOKUP, 0) == 0 && ignored == root);
	assert(find(dyld, "foo", &ignored, LOOKUP, 0) == ENOTDIR);
	assert(find(lib, "dyld", &ignored, CREATE, ISLASTCN) == EROFS);
	assert(find(root, "usr", &ignored, CREATE, 0) == 0 && ignored == usr);
	assert(find(lib, "../dyld", &ignored, LOOKUP, 0) == EINVAL);
	struct vnode_attr va;
	memset(&va, 0, sizeof(va));
	va.active = 0x1234;
	struct vnop_getattr_args attr = { dyld, &va };
	assert(mockfs_getattr(&attr) == 0);
	assert(va.active == 0x1234 && va.va_mode == 0550 && va.va_uid == 501 && va.va_gid == 20);
	assert(va.va_data_size == 9001 && va.va_data_alloc == 12288 && va.va_fileid != va.va_parentid);
	uint64_t dyld_id = va.va_fileid;
	attr.a_vp = other;
	assert(mockfs_getattr(&attr) == 0 && va.va_fileid != dyld_id);
	read_check(dyld, "DYLD\n", 9001);
	read_check(launch, "LAUNCHD\n", 8);
	read_check(other, "OTHER\n", 6);
	read_check(empty, "", 0);
	daddr64_t block;
	size_t run;
	int poff = -1;
	struct vnop_blockmap_args mapping = { dyld, 8192, 4096, &block, &run, &poff };
	assert(mockfs_blockmap(&mapping) == 0 && run == 4096 && poff == -1);
	mapping.a_size = 4097;
	assert(mockfs_blockmap(&mapping) == EINVAL);
	mapping.a_foffset = -1;
	assert(mockfs_blockmap(&mapping) == EINVAL);
	mapping.a_foffset = INT64_MAX;
	assert(mockfs_blockmap(&mapping) == EINVAL);
	mapping.a_vp = empty;
	mapping.a_foffset = 0;
	mapping.a_size = 1;
	assert(mockfs_blockmap(&mapping) == EINVAL);
	struct vnop_pagein_args pagein = { dyld, NULL, 0, 8192, 4096, 0 };
	assert(mockfs_pagein(&pagein) == EIO && pagein_size == 9001);
	uint8_t listing[2048];
	int eof, count;
	struct uio u = { 0, sizeof(listing), listing };
	assert(directory(lib, &u, &eof, &count) == 0 && eof && count == 4);
	struct dirent d;
	size_t at = 0;
	const char *expected[] = { ".", "..", "dyld", "empty" };
	for (unsigned i = 0; i < 4; ++i) {
		memset(&d, 0, sizeof(d));
		memcpy(&d, listing + at, offsetof(struct dirent, d_name));
		assert(d.d_reclen >= offsetof(struct dirent, d_name) + d.d_namlen + 1);
		assert(!strcmp((char *)listing + at + offsetof(struct dirent, d_name), expected[i]));
		at += d.d_reclen;
	}
	assert(at == (size_t)u.offset);
	u.out = listing;
	u.resid = sizeof(listing);
	assert(directory(lib, &u, &eof, &count) == 0 && eof && count == 0);
	u.offset = 1;
	assert(directory(lib, &u, &eof, &count) == EINVAL);
	u.offset = 0;
	u.resid = 1;
	assert(directory(lib, &u, &eof, &count) == EINVAL);
	/* Pagination: first call returns only ".", resumes at exactly "..". */
	u.offset = 0;
	u.out = listing;
	u.resid = (offsetof(struct dirent, d_name) + 2 + 3) & ~3U;
	assert(directory(lib, &u, &eof, &count) == 0 && !eof && count == 1);
	u.out = listing;
	u.resid = sizeof(listing);
	assert(directory(lib, &u, &eof, &count) == 0 && eof && count == 3);
	struct vfs_attr fs = {0};
	assert(mockfs_vfsops.vfs_getattr(mp, &fs, NULL) == 0 && fs.f_files == 10);
	assert(mockfs_unmount(mp, MNT_FORCE, NULL) == 0);
	assert(!mp->mnt_data && host_allocations == 0 && vnode_count == 0);
}

static void
test_legacy(mount_t mp, int two_file)
{
	assert(mockfs_mountroot(mp, mp->mnt_devvp, NULL) == 0);
	assert(!((mockfs_mount_t)mp->mnt_data)->ramfs);
	vnode_t root, usr, launch, child;
	assert(mockfs_root(mp, &root, NULL) == 0);
	/* Legacy lookup writes a temporary terminator, so use writable names here. */
	char user_name[] = "usr", launch_name[] = "launchd", child_name[] = "probe.bin";
	assert(find(root, user_name, &usr, LOOKUP, 0) == 0 && usr == root);
	assert(find(root, launch_name, &launch, LOOKUP, ISLASTCN) == 0);
	if (two_file) {
		assert(find(root, child_name, &child, LOOKUP, ISLASTCN) == 0 && launch != child);
		read_check(launch, "LAUNCHD\n", 8);
		read_check(child, "CHILD\n", 6);
	} else {
		assert(launch->host_filesize == image_size);
	}
	assert(mockfs_unmount(mp, MNT_FORCE, NULL) == 0 && host_allocations == 0);
}

static void
load(const char *path)
{
	free(image);
	FILE *f = fopen(path, "rb");
	assert(f && fseek(f, 0, SEEK_END) == 0);
	long size = ftell(f);
	assert(size > 0 && size <= CR_MAX_IMAGE);
	rewind(f);
	image_size = (size_t)size;
	image = malloc(image_size);
	assert(image && fread(image, 1, image_size, f) == image_size);
	assert(fclose(f) == 0);
}

static int
memory_read(void *context __unused, uint64_t offset, void *out, size_t size)
{
	if (offset > image_size || size > image_size - offset) {
		return 1;
	}
	memcpy(out, image + offset, size);
	return 0;
}

int
main(int argc, char **argv)
{
	if (argc > 2 && !strcmp(argv[1], "--reject")) {
		struct cr_volume *v = malloc(sizeof(*v));
		assert(v);
		for (int i = 2; i < argc; ++i) {
			load(argv[i]);
			assert(cr_validate(memory_read, NULL, image_size, v) != 0);
			assert(v->count == 0);
		}
		free(v);
		free(image);
		puts("PASS: production C parser rejects malformed corpus under sanitizers");
		return 0;
	}
	assert(argc == 4);
	struct mount m = {0};
	struct specinfo info = {0};
	struct vnode device = { .v_mount = &m, .v_specinfo = &info };
	m.mnt_devvp = &device;
	load(argv[1]);
	info.si_devsize = image_size;
	for (unsigned block_size = 512; block_size <= 4096; block_size *= 8) {
		m.mnt_devblocksize = block_size;
		test_new(&m);
	}
	m.mnt_devblocksize = 512;
	for (int failure = 0; failure < 2; ++failure) {
		host_fail_allocation = failure;
		assert(mockfs_mountroot(&m, &device, NULL) == ENOMEM && !m.mnt_data);
		assert(host_allocations == 0);
	}
	host_fail_allocation = -1;
	read_error = 1;
	assert(mockfs_mountroot(&m, &device, NULL) == EIO && !m.mnt_data);
	read_error = 0;
	residual_error = 1;
	assert(mockfs_mountroot(&m, &device, NULL) == EIO && !m.mnt_data);
	residual_error = 0;
	image[8] = 2;
	assert(mockfs_mountroot(&m, &device, NULL) == EINVAL && !m.mnt_data && !host_allocations);
	load(argv[2]);
	info.si_devsize = image_size;
	test_legacy(&m, 1);
	load(argv[3]);
	info.si_devsize = image_size;
	test_legacy(&m, 0);
	free(image);
	puts("PASS: actual overlay mount/lookup/getattr/readdir/read/blockmap/unmount dispatch;"
	    " legacy N4KROOT and single-file paths preserved; host adapters only, VM unavailable");
	return 0;
}
