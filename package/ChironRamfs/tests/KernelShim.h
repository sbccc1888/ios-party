/* Host-only XNU API stand-ins. This does NOT provide XNU or VM qualification.
 * Generated mockfs .c files are compiled unchanged against these declarations.
 */
#ifndef RAMFS_KERNEL_SHIM_H
#define RAMFS_KERNEL_SHIM_H
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#ifndef __unused
#define __unused __attribute__((unused))
#endif
#define MOCKFS 1
#define FALSE 0
#define TRUE 1
#define Z_WAITOK 0
#define Z_ZERO 0
#define Z_NOFAIL 0
#define LCK_ATTR_NULL 0
#define PAGE_SHIFT 12
#define MNT_RDONLY 1
#define MNT_FORCE 2
#define FORCECLOSE 4
#define VREAD 4
#define VWRITE 2
#define VEXEC 1
#define FWRITE 2
#define LOOKUP 0
#define CREATE 1
#define ISLASTCN 1
#define VREG 1
#define VDIR 2
#define VNFS_CANTCACHE 1
#define VNFS_NOCACHE 2
#define VNCREATE_FLAVOR 1
#define VCREATESIZE sizeof(struct vnode_fsparam)
#define DKIOCGETMEMDEVINFO 1
#define VNODE_READDIR_EXTENDED 1
#define VNODE_READDIR_REQSEEKOFF 2
#define DT_DIR 4
#define DT_REG 8
#define MAXNAMLEN 255
#define bzero(p, n) memset(p, 0, n)
#define LCK_GRP_DECLARE(n, s) int n
typedef int lck_mtx_t;
typedef int boolean_t;
typedef int64_t daddr64_t;
typedef void *vfs_context_t;
typedef void *memory_object_control_t;
typedef void *upl_t;
typedef uint64_t upl_offset_t;
struct mount;
struct vnode;
typedef struct mount *mount_t;
typedef struct vnode *vnode_t;
struct specinfo { uint64_t si_devsize; };
struct mount {
	void *mnt_data;
	vnode_t mnt_devvp;
	uint32_t mnt_devblocksize;
	int mnt_flag;
};
struct vnode {
	void *v_data;
	mount_t v_mount;
	int v_type;
	struct specinfo *v_specinfo;
	uint64_t host_filesize;
};
struct componentname { char *cn_nameptr; int cn_namelen, cn_nameiop, cn_flags; };
struct vnode_fsparam {
	mount_t vnfs_mp;
	int vnfs_vtype;
	const char *vnfs_str;
	vnode_t vnfs_dvp;
	void *vnfs_fsnode;
	int (**vnfs_vops)(void *);
	int vnfs_markroot, vnfs_marksystem, vnfs_rdev;
	uint64_t vnfs_filesize;
	struct componentname *vnfs_cnp;
	int vnfs_flags;
};
typedef struct { int mi_phys, mi_mdev; uint32_t mi_base; uint64_t mi_size; } dk_memdev_info_t;
struct host_buf { uint8_t bytes[4096]; uint32_t residual; vnode_t vnode; };
typedef struct host_buf *buf_t;
struct uio { off_t offset; size_t resid; uint8_t *out; };
typedef struct uio *uio_t;
struct vnode_attr {
	uint64_t active, supported, va_type, va_mode, va_uid, va_gid, va_fileid, va_parentid;
	uint64_t va_nlink, va_total_size, va_total_alloc, va_data_size, va_data_alloc, va_iosize;
};
#define VATTR_RETURN(v, f, n) ((v)->f = (n), (v)->supported++)
struct vfs_attr {
	uint64_t f_bsize, f_iosize, f_blocks, f_bused, f_bfree, f_bavail;
	uint64_t f_files, f_ffree, f_objcount, f_maxobjcount, f_fssubtype;
};
#define VFSATTR_RETURN(v, f, n) ((v)->f = (n))
struct vfsconf { int unused; };
struct vfsops {
	int (*vfs_unmount)(struct mount *, int, vfs_context_t);
	int (*vfs_root)(mount_t, vnode_t *, vfs_context_t);
	int (*vfs_sync)(struct mount *, int, vfs_context_t);
	int (*vfs_getattr)(mount_t, struct vfs_attr *, vfs_context_t);
	int (*vfs_init)(struct vfsconf *);
};
struct vnop_lookup_args { vnode_t a_dvp; vnode_t *a_vpp; struct componentname *a_cnp; vfs_context_t a_context; };
struct vnop_open_args { vnode_t a_vp; int a_mode; };
struct vnop_getattr_args { vnode_t a_vp; struct vnode_attr *a_vap; };
struct vnop_read_args { vnode_t a_vp; struct uio *a_uio; int a_ioflag; };
struct vnop_reclaim_args { vnode_t a_vp; };
struct vnop_strategy_args { buf_t a_bp; };
struct vnop_pagein_args {
	vnode_t a_vp; upl_t a_pl; upl_offset_t a_pl_offset; off_t a_f_offset; size_t a_size; int a_flags;
};
struct vnop_blockmap_args {
	vnode_t a_vp; off_t a_foffset; size_t a_size; daddr64_t *a_bpn; size_t *a_run; void *a_poff;
};
struct vnop_readdir_args {
	vnode_t a_vp; struct uio *a_uio; int a_flags; int *a_eofflag; int *a_numdirent;
};
struct dirent { uint64_t d_ino; uint16_t d_reclen, d_namlen; uint8_t d_type; char d_name[256]; };
struct vnodeop_desc { int unused; };
struct vnodeopv_entry_desc { struct vnodeop_desc *opve_op; int (*opve_impl)(void *); };
struct vnodeopv_desc { int (***opv_desc_vector_p)(void *); const struct vnodeopv_entry_desc *opv_desc_ops; };
#define DESC(n) extern struct vnodeop_desc vnop_##n##_desc
DESC(default); DESC(lookup); DESC(create); DESC(open); DESC(mknod); DESC(close);
DESC(access); DESC(getattr); DESC(setattr); DESC(read); DESC(write); DESC(ioctl);
DESC(select); DESC(mmap); DESC(fsync); DESC(remove); DESC(link); DESC(rename);
DESC(mkdir); DESC(rmdir); DESC(symlink); DESC(readlink); DESC(inactive); DESC(reclaim);
DESC(strategy); DESC(pathconf); DESC(advlock); DESC(pagein); DESC(pageout); DESC(blktooff);
DESC(offtoblk); DESC(blockmap); DESC(readdir);
DESC(bwrite); DESC(copyfile);
#undef DESC
extern int host_allocations, host_fail_allocation;
void *host_allocate(size_t size);
void host_free(void *p);
#define kalloc_type(t, flags) ((t *)host_allocate(sizeof(t)))
#define kfree_type(t, p) host_free(p)
static inline void lck_mtx_init(int *m, int *g, int a) { (void)m; (void)g; (void)a; }
static inline void lck_mtx_destroy(int *m, int *g) { (void)m; (void)g; }
static inline void lck_mtx_lock(int *m) { (void)m; }
static inline void lck_mtx_unlock(int *m) { (void)m; }
#define panic(...) do { fprintf(stderr, __VA_ARGS__); abort(); } while (0)
static inline void *vfs_context_ucred(vfs_context_t c) { return c; }
static inline int VNOP_IOCTL(vnode_t v, int x, caddr_t p, int f, void *c) {
	(void)v; (void)x; (void)p; (void)f; (void)c; return ENOTSUP;
}
int buf_meta_bread(vnode_t, daddr64_t, int, void *, buf_t *);
static inline uintptr_t buf_dataptr(buf_t b) { return (uintptr_t)b->bytes; }
static inline uint32_t buf_resid(buf_t b) { return b->residual; }
static inline void buf_brelse(buf_t b) { free(b); }
static inline vnode_t buf_vnode(buf_t b) { return b->vnode; }
static inline vnode_t vfs_devvp(mount_t m) { return m->mnt_devvp; }
static inline void vnode_put(vnode_t v) { (void)v; }
static inline int buf_strategy(vnode_t v, void *a) { (void)v; (void)a; return ENOTSUP; }
static inline void *vnode_fsnode(vnode_t v) { return v->v_data; }
static inline void vnode_clearfsnode(vnode_t v) { v->v_data = NULL; }
static inline int vnode_get(vnode_t v) { return v ? 0 : EINVAL; }
int vnode_create(int, size_t, struct vnode_fsparam *, vnode_t *);
int vflush(mount_t, vnode_t, int);
static inline memory_object_control_t ubc_getobject(vnode_t v, int f) { (void)f; return v; }
static inline int pager_map_to_phys_contiguous(void *c, int o, uint64_t b, uint64_t s) {
	(void)c; (void)o; (void)b; (void)s; return ENOTSUP;
}
int cluster_read(vnode_t, struct uio *, uint64_t, int);
int cluster_pagein(vnode_t, upl_t, upl_offset_t, off_t, size_t, uint64_t, int);
static inline off_t uio_offset(uio_t u) { return u->offset; }
static inline void uio_setoffset(uio_t u, off_t o) { u->offset = o; }
static inline size_t uio_resid(uio_t u) { return u->resid; }
static inline int uiomove(caddr_t p, int n, uio_t u) {
	memcpy(u->out, p, (size_t)n); u->out += n; u->offset += n; u->resid -= n; return 0;
}
#define ERR(n) static inline int n(void *p) { (void)p; return EROFS; }
ERR(vn_default_error) ERR(err_create) ERR(err_mknod) ERR(err_access) ERR(err_setattr)
ERR(err_write) ERR(err_ioctl) ERR(err_select) ERR(err_remove) ERR(err_link) ERR(err_rename)
ERR(err_mkdir) ERR(err_rmdir) ERR(err_symlink) ERR(err_readlink) ERR(err_pathconf)
ERR(err_advlock) ERR(err_pageout) ERR(err_blktooff) ERR(err_offtoblk)
ERR(err_inactive) ERR(err_readdir) ERR(err_bwrite) ERR(err_copyfile)
#undef ERR
#define NOP(n) static inline int n(void *p) { (void)p; return 0; }
NOP(nop_close) NOP(nop_mmap) NOP(nop_fsync) NOP(nop_inactive)
#undef NOP
#endif
