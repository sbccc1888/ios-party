/* Included by the explicitly enabled mockfs vfsops overlay. */
struct chiron_ramfs_reader {
	vnode_t device;
	vfs_context_t context;
	uint64_t size;
	uint32_t block_size;
};

static int
chiron_ramfs_device_read(void *opaque, uint64_t offset, void *output, size_t length)
{
	struct chiron_ramfs_reader *r = opaque;
	uint8_t *out = output;
	if (offset > r->size || length > r->size - offset ||
	    !r->block_size || CR_PAGE % r->block_size) {
		return EIO;
	}
	while (length) {
		uint64_t page = offset & ~(uint64_t)(CR_PAGE - 1);
		size_t within = (size_t)(offset - page);
		size_t n = CR_PAGE - within;
		buf_t bp = NULL;
		if (n > length) {
			n = length;
		}
		if (page > r->size || CR_PAGE > r->size - page) {
			return EIO;
		}
		int error = buf_meta_bread(r->device, page / r->block_size, CR_PAGE,
		    vfs_context_ucred(r->context), &bp);
		if (!error && (!bp || buf_resid(bp))) {
			error = EIO;
		}
		if (!error) {
			memcpy(out, (uint8_t *)buf_dataptr(bp) + within, n);
		}
		if (bp) {
			buf_brelse(bp);
		}
		if (error) {
			return error;
		}
		offset += n;
		out += n;
		length -= n;
	}
	return 0;
}

static void
chiron_ramfs_destroy_nodes(mockfs_mount_t m)
{
	struct chiron_ramfs_mount *r = m->ramfs;
	m->mockfs_root = NULL;
	/* Nodes have independent array ownership; never use legacy three-child adoption. */
	for (uint32_t i = r->volume.count; i > 0; --i) {
		mockfs_fsnode_t node = r->nodes[i - 1];
		if (node) {
			node->parent = NULL;
			if (mockfs_fsnode_destroy(node)) {
				panic("chiron_ramfs_destroy_nodes: live or invalid node");
			}
		}
	}
	kfree_type(struct chiron_ramfs_nodes, r->node_store);
	kfree_type(struct chiron_ramfs_mount, r);
	m->ramfs = NULL;
}

static int
chiron_ramfs_mountroot(mount_t mp, vnode_t rvp, vfs_context_t ctx, int *handled)
{
	struct chiron_ramfs_reader reader = {
		.device = rvp, .context = ctx,
		.size = rvp->v_specinfo->si_devsize, .block_size = mp->mnt_devblocksize,
	};
	uint8_t magic[8];
	*handled = 0;
	int error = chiron_ramfs_device_read(&reader, 0, magic, sizeof(magic));
	if (error) {
		*handled = 1;
		return error;
	}
	if (!cr_equal(magic, "CHRAMFS", 8)) {
		return 0; /* Preserve the original single-file and N4KROOT implementations. */
	}
	*handled = 1;
	struct chiron_ramfs_mount *r = kalloc_type(struct chiron_ramfs_mount, Z_WAITOK | Z_ZERO);
	if (!r) {
		return ENOMEM;
	}
	error = cr_validate(chiron_ramfs_device_read, &reader, reader.size, &r->volume);
	if (error) {
		kfree_type(struct chiron_ramfs_mount, r);
		return error == CR_IO ? EIO : EINVAL;
	}
	r->node_store = kalloc_type(struct chiron_ramfs_nodes, Z_WAITOK | Z_ZERO);
	if (!r->node_store) {
		kfree_type(struct chiron_ramfs_mount, r);
		return ENOMEM;
	}
	r->nodes = r->node_store->entries;
	mockfs_mount_t m = kalloc_type(struct mockfs_mount, Z_WAITOK | Z_ZERO);
	if (!m) {
		kfree_type(struct chiron_ramfs_nodes, r->node_store);
		kfree_type(struct chiron_ramfs_mount, r);
		return ENOMEM;
	}
	lck_mtx_init(&m->mockfs_mnt_mtx, &mockfs_mtx_grp, LCK_ATTR_NULL);
	m->ramfs = r;
	/* The width-safe device I/O path is mandatory for all new-format extents. */
	m->mockfs_memory_backed = FALSE;
	mp->mnt_data = (typeof(mp->mnt_data))m;
	for (uint32_t i = 0; i < r->volume.count; ++i) {
		const struct cr_entry *e = &r->volume.entries[i];
		uint8_t type = i == 0 ? MOCKFS_ROOT : (e->kind == CR_FILE ? MOCKFS_FILE : MOCKFS_RAM_DIR);
		error = mockfs_fsnode_create(mp, type, &r->nodes[i]);
		if (error) {
			chiron_ramfs_destroy_nodes(m);
			lck_mtx_destroy(&m->mockfs_mnt_mtx, &mockfs_mtx_grp);
			kfree_type(struct mockfs_mount, m);
			mp->mnt_data = NULL;
			return error;
		}
		r->nodes[i]->ramfs_index = i;
		r->nodes[i]->size = e->size;
		r->nodes[i]->backing_offset = e->offset;
		r->nodes[i]->parent = i ? r->nodes[e->parent] : NULL;
	}
	m->mockfs_root = r->nodes[0];
	mp->mnt_flag |= MNT_RDONLY;
	return 0;
}
