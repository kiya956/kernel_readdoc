# exportfs — NFS File Handle Export API

## Overview

`fs/exportfs/` provides the **kernel infrastructure for exporting filesystems
over NFS** (and other network filesystem protocols).  The core problem it solves
is: **how to convert between a VFS dentry/inode and a compact, persistent
"file handle"** that a remote client can use to re-access a file across reboots,
unmounts, and dentry-cache evictions.

Key functions:
- **`exportfs_encode_fh()`** — converts a dentry to a binary file handle
  (stored in NFS `fhandle` or `struct knfsd_fh`)
- **`exportfs_decode_fh()`** — converts a file handle back to a `struct path`,
  reconnecting disconnected dentries if necessary
- **`export_operations`** — per-filesystem vtable for custom encode/decode

Filesystems that support exporting implement `s_export_op` in their superblock.
Common implementations: ext4, xfs, btrfs, tmpfs, nfsd's `svcfh`.

Source: `fs/exportfs/expfs.c`, `include/linux/exportfs.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                     NFS SERVER (nfsd)                          │
│  nfsd_dispatch() → fh_verify() → fh_compose()                 │
│  Client mounts NFS share; kernel receives file handle in ops   │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│             EXPORTFS CORE  (fs/exportfs/expfs.c)               │
│                                                                 │
│  exportfs_encode_fh(dentry, fid, max_len, connectable)        │
│   → calls s_export_op->encode_fh()                            │
│   → default: inode number + generation + parent inode          │
│                                                                 │
│  exportfs_decode_fh(mnt, fid, fh_len, fileid_type, accept_fn) │
│   → calls s_export_op->fh_to_dentry()                        │
│   → may call s_export_op->get_parent() to reconnect path       │
│   → handles DCACHE_DISCONNECTED dentries                       │
│                                                                 │
│  Helper functions:                                             │
│   exportfs_get_name()  — get filename for a dentry from parent │
│   reconnect_path()     — walk up to root, reconnecting dcache  │
│   find_acceptable_alias() — check alias dentries for acceptability│
└──────────────────────────────┬─────────────────────────────────┘
                               │ export_operations vtable
┌──────────────────────────────▼─────────────────────────────────┐
│        FILESYSTEM EXPORT_OPS  (per filesystem)                 │
│                                                                 │
│  struct export_operations {                                    │
│   .encode_fh     — inode → compact file handle bytes          │
│   .fh_to_dentry  — file handle bytes → dentry (or ERR_PTR)    │
│   .fh_to_parent  — file handle → parent dentry                │
│   .get_parent    — dentry → parent dentry (for reconnect)      │
│   .get_name      — (dir, child) → filename string             │
│   .find_acceptable_alias — custom alias selection logic        │
│  }                                                             │
│                                                                 │
│  Default fallbacks in expfs.c cover most simple filesystems    │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│                     VFS / DCACHE                               │
│  struct dentry — path component with d_inode + d_parent       │
│  DCACHE_DISCONNECTED — dentry not yet reattached to tree       │
└────────────────────────────────────────────────────────────────┘
```

---

## File Handle Encoding Example

```
NFS client opens /export/home/user/file.txt

Server calls: exportfs_encode_fh(dentry_of_file.txt, fid, &max_len, 1)
  → s_export_op->encode_fh() (ext4 default):
      fid->i32.ino    = inode number of file.txt
      fid->i32.gen    = inode generation counter
      fid->i32.parent_ino = parent directory inode
      fid->i32.parent_gen = parent generation
      returns FILEID_INO32_GEN_PARENT

Client stores: opaque 16-byte file handle

Client reconnects after server reboot:
  exportfs_decode_fh(mnt, fid, len, FILEID_INO32_GEN_PARENT, acceptable)
    → ext4_fh_to_dentry(): sb->s_export_op->fh_to_dentry()
    → ilookup5(): find or create inode from ino+gen
    → if dentry DISCONNECTED: reconnect_path() walks up to root
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `export_operations` | Per-filesystem vtable for encode/decode |
| `fid` (union) | File handle payload: ino+gen, UUID, or custom |
| `knfsd_fh` | NFS server file handle (version + `fid` payload) |

---

## Key Source Files

| File | Purpose |
|---|---|
| `fs/exportfs/expfs.c` | Core: encode/decode, reconnect_path, get_name |
| `include/linux/exportfs.h` | `export_operations`, `fid` types, constants |
| `fs/nfsd/nfsfh.c` | NFS server file handle management (uses exportfs) |

---

## Analogy

exportfs is like a **library card catalog** for remote file access:

- Each book (file) gets a short **catalog number** (file handle) that fits on
  a library card.  The number is permanent — it survives power outages.
- When a patron (NFS client) presents a catalog number, the librarian
  (exportfs_decode_fh) looks it up, finds the book on the shelf (dentry), and
  hands it over.
- If the book was temporarily removed for reshelving (DCACHE_DISCONNECTED),
  the librarian **reconnects** it to its proper shelf location before handing
  it over.
- The **export_operations vtable** is the library's custom lookup rules —
  different library sections (filesystems) may have different catalog formats.

---

## References

- `fs/exportfs/expfs.c` — implementation
- `include/linux/exportfs.h` — API
- `Documentation/filesystems/nfs/exporting.rst`
