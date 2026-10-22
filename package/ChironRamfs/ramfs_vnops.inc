/* Included by mockfs_vnops.c; ordinary read/pagein retain exact fsnode sizes. */
#include <sys/dirent.h>
#include <sys/uio.h>

static int
chiron_ramfs_lookup(struct vnop_lookup_args *ap)
{
	mockfs_fsnode_t node = ap->a_dvp->v_data;
	struct chiron_ramfs_mount *r = ((mockfs_mount_t)node->mnt->mnt_data)->ramfs;
	struct componentname *cnp = ap->a_cnp;
	uint32_t index;
	*ap->a_vpp = NULL;
	if (r->volume.entries[node->ramfs_index].kind != CR_DIR) {
		return ENOTDIR;
	}
	if (cnp->cn_nameiop != LOOKUP && (cnp->cn_flags & ISLASTCN)) {
		return EROFS;
	}
	if (cnp->cn_namelen <= 0) {
		return EINVAL;
	}
	int error = cr_lookup(&r->volume, node->ramfs_index, cnp->cn_nameptr,
	    (uint32_t)cnp->cn_namelen, &index);
	if (error) {
		return error == CR_NOTFOUND ? ENOENT : EINVAL;
	}
	return mockfs_fsnode_vnode(r->nodes[index], ap->a_vpp);
}

static int
chiron_ramfs_getattr(struct vnop_getattr_args *ap)
{
	mockfs_fsnode_t node = ap->a_vp->v_data;
	struct chiron_ramfs_mount *r = ((mockfs_mount_t)node->mnt->mnt_data)->ramfs;
	const struct cr_entry *e = &r->volume.entries[node->ramfs_index];
	struct vnode_attr *vap = ap->a_vap;
	uint32_t links = e->kind == CR_DIR ? 2 : 1;
	for (uint32_t i = 1; i < r->volume.count; ++i) {
		if (e->kind == CR_DIR && r->volume.entries[i].parent == node->ramfs_index &&
		    r->volume.entries[i].kind == CR_DIR) {
			++links;
		}
	}
	/* Preserve the caller's requested/active attribute mask. */
	VATTR_RETURN(vap, va_type, e->kind == CR_DIR ? VDIR : VREG);
	VATTR_RETURN(vap, va_mode, e->mode);
	VATTR_RETURN(vap, va_uid, e->uid);
	VATTR_RETURN(vap, va_gid, e->gid);
	VATTR_RETURN(vap, va_fileid, node->ramfs_index + 1);
	VATTR_RETURN(vap, va_parentid, e->parent + 1);
	VATTR_RETURN(vap, va_nlink, links);
	VATTR_RETURN(vap, va_total_size, e->size);
	VATTR_RETURN(vap, va_data_size, e->size);
	VATTR_RETURN(vap, va_total_alloc, cr_align(e->size));
	VATTR_RETURN(vap, va_data_alloc, cr_align(e->size));
	VATTR_RETURN(vap, va_iosize, CR_PAGE);
	return 0;
}

static int
chiron_ramfs_blockmap(struct vnop_blockmap_args *ap)
{
	mockfs_fsnode_t node = ap->a_vp->v_data;
	struct chiron_ramfs_mount *r = ((mockfs_mount_t)node->mnt->mnt_data)->ramfs;
	uint64_t block, run;
	int error = cr_map(&r->volume, node->ramfs_index, ap->a_foffset, ap->a_size,
	    node->mnt->mnt_devblocksize, &block, &run);
	if (error) {
		return EINVAL; /* Invalid ranges are rejected, never turned into a panic. */
	}
	if (ap->a_bpn) {
		*ap->a_bpn = (daddr64_t)block;
	}
	if (ap->a_run) {
		*ap->a_run = (size_t)run;
	}
	/* a_poff is an unused void * in the pinned XNU interface. */
	return 0;
}

static int
chiron_ramfs_readdir(struct vnop_readdir_args *ap)
{
	mockfs_fsnode_t node = ap->a_vp->v_data;
	struct chiron_ramfs_mount *r = ((mockfs_mount_t)node->mnt->mnt_data)->ramfs;
	uint32_t parent = node->ramfs_index;
	off_t position = 0, start = uio_offset(ap->a_uio);
	int emitted = 0;
	if (ap->a_eofflag) {
		*ap->a_eofflag = 0;
	}
	if (ap->a_numdirent) {
		*ap->a_numdirent = 0;
	}
	if (r->volume.entries[parent].kind != CR_DIR) {
		return ENOTDIR;
	}
	if (start < 0 || ap->a_flags & (VNODE_READDIR_EXTENDED | VNODE_READDIR_REQSEEKOFF)) {
		return EINVAL;
	}
	/* Byte offsets must identify a record boundary; never skip a partial record. */
	for (uint32_t slot = 0; slot < r->volume.count + 2; ++slot) {
		uint32_t index;
		const char *name;
		uint32_t length;
		if (slot < 2) {
			index = slot ? r->volume.entries[parent].parent : parent;
			name = slot ? ".." : ".";
			length = slot ? 2 : 1;
		} else {
			index = slot - 2;
			if (!index || r->volume.entries[index].parent != parent) {
				continue;
			}
			name = r->volume.entries[index].name;
			length = r->volume.entries[index].name_len;
		}
		struct dirent d;
		bzero(&d, sizeof(d));
		d.d_ino = index + 1;
		d.d_type = r->volume.entries[index].kind == CR_DIR ? DT_DIR : DT_REG;
		d.d_namlen = length;
		d.d_reclen = (uint16_t)((offsetof(struct dirent, d_name) + length + 1 + 3) & ~3U);
		memcpy(d.d_name, name, length);
		if (position < start && start < position + d.d_reclen) {
			return EINVAL;
		}
		if (position >= start) {
			if (uio_resid(ap->a_uio) < d.d_reclen) {
				if (!emitted) {
					return EINVAL;
				}
				return 0;
			}
			int error = uiomove((caddr_t)&d, d.d_reclen, ap->a_uio);
			if (error) {
				return error;
			}
			++emitted;
			if (ap->a_numdirent) {
				*ap->a_numdirent = emitted;
			}
			uio_setoffset(ap->a_uio, position + d.d_reclen);
		}
		position += d.d_reclen;
	}
	if (start > position) {
		return EINVAL;
	}
	if (ap->a_eofflag) {
		*ap->a_eofflag = 1;
	}
	return 0;
}
