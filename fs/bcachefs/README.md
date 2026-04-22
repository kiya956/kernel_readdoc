# bcachefs — Copy-on-Write B-Tree Filesystem

## Overview

**bcachefs** is a modern Linux filesystem that grew from the **bcache**
block-layer caching project.  It is a fully copy-on-write (CoW) filesystem with
a focus on performance and reliability, providing:

- **Multiple storage tiers** (SSD cache + HDD backing, or all-flash pools)
- **Copy-on-write B-trees** for all metadata (extents, inodes, dirents, xattrs,
  alloc info, …)
- **Per-extent checksums and compression** (lz4, gzip, zstd)
- **Erasure coding** (Reed-Solomon for multiple devices)
- **Snapshots** at filesystem granularity
- **Replication** across multiple devices (RAID 1/10)
- **Encryption** (ChaCha20-Poly1305)
- **Online fsck** capability

Source: `fs/bcachefs/`, `include/uapi/linux/bcachefs.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  mkfs.bcachefs  bcachefs  mount -t bcachefs  fstab             │
└──────────────────────────────┬─────────────────────────────────┘
                               │  VFS calls
┌──────────────────────────────▼─────────────────────────────────┐
│                   VFS / POSIX LAYER                            │
│  file_operations  inode_operations  super_operations           │
│  (fs.c, file.c, dir.c, xattr.c, acl.c, ioctl.c)               │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│               I/O PATH  (io_read.c, io_write.c, iomap.c)       │
│                                                                 │
│  Buffered I/O:  folio_start_write → bcachefs extents           │
│  Direct I/O:    kiocb → direct_write_add_folio                 │
│  Compression:   compress.c (lz4/gzip/zstd inline)              │
│  Encryption:    encrypt.c  (per-key ChaCha20-Poly1305)         │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│               BTREE ENGINE                                     │
│                                                                 │
│  btree_iter.c      — cursor iteration over B-tree keys         │
│  btree_update.c    — insert/update/delete via transactions      │
│  btree_trans_commit.c — atomic multi-key transaction commit    │
│  btree_io.c        — read/write B-tree nodes from disk         │
│  btree_gc.c        — garbage collector / mark-and-sweep        │
│  btree_cache.c     — LRU node cache (in-memory B-tree pages)   │
│  btree_key_cache.c — key-level read-copy-update cache          │
│  btree_locking.c   — six-lock per-node locking                 │
│                                                                 │
│  B-trees (btree IDs):                                          │
│  EXTENTS  INODES  DIRENTS  XATTRS  ALLOC  FREESPACE            │
│  QUOTAS   STRIPES SNAPSHOTS LRUS   BUCKET_GENS  …              │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│               JOURNAL  (journal.c, journal_io.c)               │
│                                                                 │
│  Append-only journal: batches all B-tree updates               │
│  journal_buf → journal_write → on-disk journal entries         │
│  journal_reclaim.c — flush and free old journal space          │
│  journal_seq_blacklist.c — skip corrupted seq ranges           │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│         ALLOCATOR  (alloc_foreground.c, alloc_background.c)    │
│                                                                 │
│  Bucket allocator: divides devices into buckets (~1 MB each)   │
│  alloc_foreground.c — synchronous bucket allocation for writes │
│  alloc_background.c — background GC, discard, invalidation     │
│  Tiering: hot/warm/cold buckets moved between storage tiers    │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│         MULTI-DEVICE / REPLICATION  (sb-members.c, replicas.c) │
│                                                                 │
│  Multiple member devices (bch_dev) within one bch_fs           │
│  erasure_code.c — Reed-Solomon stripe rebuild                  │
│  rebalance.c    — background data migration between devices    │
└──────────────────────────────┬─────────────────────────────────┘
                               │  struct bio
┌──────────────────────────────▼─────────────────────────────────┐
│              BLOCK DEVICE / STORAGE                            │
│  NVMe SSD  ─  SATA HDD  ─  loop  ─  any block device          │
└────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. VFS Layer (`fs.c`, `file.c`, `dir.c`)

Standard `file_operations` / `inode_operations` / `super_operations` callbacks.
Mount via `bch2_mount()` → reads superblock → replays journal → opens btrees.

### 2. I/O Path (`io_read.c`, `io_write.c`)

- **Write**: `bch2_write()` → compress → encrypt → allocate extents (via allocator)
  → issue `struct bch_write_op` bio → write checksum to B-tree.
- **Read**: `bch2_read()` → look up extent in EXTENTS B-tree → validate checksum
  → decompress → decrypt → return to VFS.

### 3. B-Tree Engine (`btree_*.c`)

All filesystem metadata lives in B-trees.  Keys are `struct bpos` (inode +
offset + snapshot).  Transactions (`btree_trans`) allow atomic multi-tree
updates with lock coupling and conflict detection.

### 4. Journal (`journal.c`, `journal_io.c`)

Before any B-tree mutation is committed to disk, it is written to the journal
for crash recovery.  The journal is an append-only log of `jset` records;
`journal_reclaim` frees space after B-trees flush.

### 5. Allocator (`alloc_foreground.c`, `alloc_background.c`)

Devices are divided into fixed-size **buckets**.  Each bucket has a generation
counter and a dirty/cached/free state.  Background GC marks unreachable buckets
and moves data to enable bucket reuse.

### 6. Checksums and Compression (`checksum.c`, `compress.c`)

Per-extent checksums (crc32c, crc64, xxhash, sha256) stored in the EXTENTS
B-tree.  Compression reduces physical extents; the B-tree key maps logical →
physical with the compressed size.

### 7. Encryption (`encrypt.c`)

Whole-filesystem encryption: ChaCha20-Poly1305 per extent.  Keys derived from
a passphrase stored in the superblock.

### 8. Snapshots (`snapshot.c`)

Snapshot IDs embedded in every B-tree key.  Snapshots are efficient:
unchanged extents are shared across snapshot generations via CoW.

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `bch_fs` | Top-level filesystem state (all B-trees, journal, devices) |
| `bch_dev` | Per-device state (buckets, alloc info, I/O queues) |
| `btree_trans` | Atomic multi-key B-tree transaction |
| `btree_iter` | Cursor for iterating B-tree keys |
| `bkey` | Minimal B-tree key: inode + offset + snapshot + type |
| `bch_extent_ptr` | Pointer to physical location of data on a device |
| `journal_buf` | In-flight journal write buffer |
| `bch_write_op` | Pending write operation (contains bio + extents) |

---

## Key Source Files

| File | Purpose |
|---|---|
| `fs/bcachefs/fs.c` | VFS super/inode operations |
| `fs/bcachefs/io_write.c` | Write path |
| `fs/bcachefs/io_read.c` | Read path |
| `fs/bcachefs/btree_iter.c` | B-tree cursor |
| `fs/bcachefs/btree_trans_commit.c` | Transaction commit |
| `fs/bcachefs/journal.c` | Journal core |
| `fs/bcachefs/alloc_foreground.c` | Bucket allocation |
| `fs/bcachefs/compress.c` | Inline compression |
| `fs/bcachefs/encrypt.c` | Encryption |
| `fs/bcachefs/snapshot.c` | Snapshot management |
| `fs/bcachefs/bcachefs_format.h` | On-disk format definitions |

---

## Analogy

bcachefs is like a **multi-master document collaboration system**:

- **B-trees** are the versioned document store — every key has a snapshot ID,
  so old versions coexist with new ones without copying unchanged content.
- The **journal** is the "unsaved changes" buffer — changes are durable as
  soon as they hit the journal, even before the B-tree pages are written.
- The **allocator** is the disk space manager — it carves up devices into
  equal-sized buckets, tracks their liveness, and reuses them like a carousel.
- **Checksums per extent** are the file integrity verifier — every read is
  validated, so bit rot is detected immediately.

---

## References

- `fs/bcachefs/bcachefs.h` — filesystem overview comment
- `fs/bcachefs/bcachefs_format.h` — on-disk structures
- https://bcachefs.org/
- `Documentation/filesystems/bcachefs/`
