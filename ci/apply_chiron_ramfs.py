#!/usr/bin/env python3
"""Explicit experimental CHRAMFS overlay for the pinned patched mockfs only."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package/ChironRamfs"
RELATIVE = Path("bsd/miscfs/mockfs")
BASELINE = {
    "mockfs_fsnode.h": "cbe32910e41513fa6aacd1a23226755c7f234f175f3b26dfaae99ddb1d2830c3",
    "mockfs_fsnode.c": "0947375cc875a38d32dae87dce5759281376464c883f0a70c09c22cebcd70c3b",
    "mockfs_vnops.c": "3b4237530d426258221307b525d8dc386047589f707cc9ace0ac3d35f7677b19",
    "mockfs_vfsops.c": "3eb58dae4dd332a44a9940270104807ba130e7fc40996a8b12b664624ef76a15",
    "mockfs.h": "6fb2606b86bdee6e111946d5307b112ca4a53497317c556b0827a545234ec285",
    "mockfs_vnops.h": "bd01f3683ec094b54aafaa9773b0f9280e2dce64d2c2965ec53b79bab2480d2a",
}
# Fixed generated identities, updated alongside reviewed overlay source.
# Verification never trusts an editable application report as its authority.
OVERLAY = {
    "mockfs_fsnode.h": "31415501e9ebf3bea6c604899e85f00f5579ee59aa80170826994b56ab199725",
    "mockfs_fsnode.c": "40d5cc87818ceec1136f4ac09c5e9029b9c747b305f6faeeeb9ad906c63f763f",
    "mockfs_vnops.c": "5bcc92572c87718baad7a264825b9f5dfcf14e68902a0b2ac3a3501b75e6b136",
    "mockfs_vfsops.c": "006440b773e6a3e45d318a7e918cc8a2fdd82c50052be74f7988ea3fd66dce59",
    "mockfs.h": "7fd33107037a09530353f04950543b43e673b04efb5217f125a352572fbeba6d",
    "mockfs_vnops.h": "bd01f3683ec094b54aafaa9773b0f9280e2dce64d2c2965ec53b79bab2480d2a",
}
INSTALLED = ("chiron_ramfs.h", "ramfs_mount.inc", "ramfs_vnops.inc")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"overlay context changed: {old[:90]}")
    return text.replace(old, new, 1)


def transform(name, text):
    if name == "mockfs.h":
        text = once(text, "#include <sys/kernel_types.h>", """#include <sys/kernel_types.h>
#include <miscfs/mockfs/chiron_ramfs.h>

struct chiron_ramfs_nodes {
	mockfs_fsnode_t entries[CR_MAX_NODES];
};

struct chiron_ramfs_mount {
	struct cr_volume volume;
	struct chiron_ramfs_nodes *node_store;
	mockfs_fsnode_t *nodes;
};
_Static_assert(sizeof(struct chiron_ramfs_mount) <= 16384, "RAMFS typed allocation size");
_Static_assert(sizeof(struct chiron_ramfs_nodes) <= 16384, "RAMFS node allocation size");""")
        text = once(text, "struct mockfs_mount {", """struct mockfs_mount {
	struct chiron_ramfs_mount *ramfs; /* CHIRON_RAMFS_EXPERIMENT v1 */""")
    elif name == "mockfs_fsnode.h":
        text = once(text, "\tMOCKFS_TEST_FILE\n", "\tMOCKFS_TEST_FILE,\n\tMOCKFS_RAM_DIR\n")
        text = once(text, "struct mockfs_fsnode {", """struct mockfs_fsnode {
	uint32_t ramfs_index; /* CHIRON_RAMFS_EXPERIMENT v1 */""")
    elif name == "mockfs_fsnode.c":
        text = once(text, "\tcase MOCKFS_DEV:\n", "\tcase MOCKFS_RAM_DIR:\n\tcase MOCKFS_DEV:\n")
    elif name == "mockfs_vfsops.c":
        text = once(text, 'static LCK_GRP_DECLARE(mockfs_mtx_grp, "mockfs-mutex");',
                    'static LCK_GRP_DECLARE(mockfs_mtx_grp, "mockfs-mutex");\n'
                    '#include "ramfs_mount.inc" /* CHIRON_RAMFS_EXPERIMENT v1 */')
        signature = "mockfs_mountroot(mount_t mp, vnode_t rvp, __unused vfs_context_t ctx)\n{"
        text = once(text, signature, signature + """
	int ramfs_handled;
	int ramfs_error = chiron_ramfs_mountroot(mp, rvp, ctx, &ramfs_handled);
	if (ramfs_handled) {
		return ramfs_error;
	}
""")
        anchor = "\troot_fsnode = mockfs_mnt->mockfs_root;"
        text = once(text, anchor, """	if (mockfs_mnt->ramfs) {
		chiron_ramfs_destroy_nodes(mockfs_mnt);
		lck_mtx_destroy(&mockfs_mnt->mockfs_mnt_mtx, &mockfs_mtx_grp);
		kfree_type(struct mockfs_mount, mockfs_mnt);
		mp->mnt_data = NULL;
		return 0;
	}
""" + anchor)
        anchor = "\tuint64_t bytes = mp->mnt_devvp->v_specinfo->si_devsize;"
        text = once(text, anchor, """	if (((mockfs_mount_t)mp->mnt_data)->ramfs) {
		nodes = ((mockfs_mount_t)mp->mnt_data)->ramfs->volume.count;
	}
""" + anchor)
    elif name == "mockfs_vnops.c":
        anchor = "#define VOPFUNC int (*)(void *)"
        text = once(text, anchor, anchor + '\n#include "ramfs_vnops.inc" /* CHIRON_RAMFS_EXPERIMENT v1 */')
        for signature, call, vnode in (
            ("mockfs_lookup(struct vnop_lookup_args * ap)", "chiron_ramfs_lookup(ap)", "ap->a_dvp"),
            ("mockfs_getattr(struct vnop_getattr_args * ap)", "chiron_ramfs_getattr(ap)", "ap->a_vp"),
            ("mockfs_blockmap(struct vnop_blockmap_args * ap)", "chiron_ramfs_blockmap(ap)", "ap->a_vp"),
        ):
            anchor = signature + "\n{"
            text = once(text, anchor, anchor + f"""
	if (((mockfs_mount_t){vnode}->v_mount->mnt_data)->ramfs) {{
		return {call};
	}}
""")
        text = once(text, "int(**mockfs_vnodeop_p)(void *);", """static int
mockfs_ramfs_readdir(struct vnop_readdir_args *ap)
{
	if (((mockfs_mount_t)ap->a_vp->v_mount->mnt_data)->ramfs) {
		return chiron_ramfs_readdir(ap);
	}
	return ENOTSUP;
}

int(**mockfs_vnodeop_p)(void *);""")
        text = once(text,
                    ".opve_op = &vnop_readdir_desc, .opve_impl = (VOPFUNC) err_readdir",
                    ".opve_op = &vnop_readdir_desc, .opve_impl = (VOPFUNC) mockfs_ramfs_readdir")
    elif name != "mockfs_vnops.h":
        raise ValueError("unknown baseline file")
    return text


def verify(source):
    directory = Path(source) / RELATIVE
    if not OVERLAY:
        raise ValueError("overlay identities have not been qualified")
    actual = {name: digest((directory / name).read_bytes()) for name in BASELINE}
    if actual != OVERLAY:
        raise ValueError("overlay source identity mismatch")
    for name in INSTALLED:
        if (directory / name).read_bytes() != (PACKAGE / name).read_bytes():
            raise ValueError(f"overlay payload identity mismatch: {name}")
    return {"schema_version": 1, "format": "CHRAMFS", "version": 1,
            "baseline_sha256": BASELINE, "overlay_sha256": actual,
            "payload_sha256": {name: digest((directory / name).read_bytes()) for name in INSTALLED},
            "experimental": True, "legacy_N4KROOT_preserved": True,
            "kernel_compiled": False, "qemu_tested": False, "hardware_tested": False}


def apply(source, *, enable_experimental=False):
    if not enable_experimental:
        raise ValueError("explicit enable_experimental=True is required; baseline is the default")
    directory = Path(source) / RELATIVE
    pending = {}
    # Preflight all identities and transformations before any writes.
    for name, expected in BASELINE.items():
        path = directory / name
        before = path.read_bytes()
        if digest(before) != expected:
            raise ValueError(f"fixed mockfs baseline changed: {name}")
        after = transform(name, before.decode()).encode()
        if OVERLAY and digest(after) != OVERLAY[name]:
            raise ValueError(f"generated overlay identity mismatch: {name}")
        pending[path] = after
    for name in INSTALLED:
        path = directory / name
        if path.exists():
            raise ValueError(f"overlay payload already exists: {name}")
        pending[path] = (PACKAGE / name).read_bytes()
    if not OVERLAY:
        raise ValueError("overlay identities have not been qualified")
    for path, data in pending.items():
        path.write_bytes(data)
    return verify(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="patched XNU source root")
    parser.add_argument("--enable-experimental", action="store_true")
    parser.add_argument("--verify", action="store_true", help="read-only fixed overlay identity check")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.verify and not args.enable_experimental:
        parser.error("application requires --enable-experimental")
    result = verify(args.source) if args.verify else apply(args.source, enable_experimental=True)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
