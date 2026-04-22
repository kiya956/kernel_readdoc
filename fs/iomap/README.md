# Linux Kernel iomap — Generic Block-Mapping Layer

## Overview

**iomap** is a generic filesystem I/O mapping infrastructure introduced to
replace the legacy `buffer_head`-based I/O path. It provides a clean abstraction
for translating file offsets to disk blocks and performing buffered, direct, and
DAX I/O on top of that mapping. Used by **XFS**, **ext4**, **btrfs**, **gfs2**,
**f2fs**, **erofs**, and others.

Source: `fs/iomap/`, `include/linux/iomap.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│   read() / write() / mmap() / fallocate() / fiemap()           │
└────────────────────────────────┬────────────────────────────────┘
                                 │ VFS
┌────────────────────────────────▼────────────────────────────────┐
│                  FILESYSTEM  (XFS / ext4 / btrfs …)             │
│                                                                 │
│  iomap_ops vtable:                                              │
│   .iomap_begin()  ──► translate file offset → disk block       │
│   .iomap_end()    ──► post-I/O cleanup (e.g., unwritten→written)│
│                                                                 │
│  Call sites:                                                    │
│   iomap_file_buffered_write()   iomap_read_folio()             │
│   iomap_dio_rw()                iomap_fiemap()                 │
│   iomap_seek_hole/data()        iomap_swapfile_activate()      │
└──────────────────────┬──────────────────────────────────────────┘
                       │ iomap_iter loop
┌──────────────────────▼──────────────────────────────────────────┐
│                       IOMAP CORE                                │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  struct iomap_iter  (one per read/write call)            │  │
│  │  pos / len / iomap / srcmap                             │  │
│  │  Drives the ->iomap_begin / body / ->iomap_end loop     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │  buffered-io.c   │  │  direct-io.c    │  │  fiemap.c     │  │
│  │                  │  │                 │  │  seek.c       │  │
│  │  Folio-based     │  │  struct          │  │  iomap_iter + │  │
│  │  read/write.     │  │  iomap_dio.      │  │  extent report│  │
│  │  iomap_folio_    │  │  Submits bios   │  └───────────────┘  │
│  │    state tracks  │  │  directly;       │                    │
│  │  per-block dirty │  │  kiocb-based    │  ┌───────────────┐  │
│  │  / uptodate bits │  │  async I/O.     │  │  ioend.c      │  │
│  └──────────────────┘  └─────────────────┘  │  Write I/O    │  │
│                                             │  completion   │  │
│                                             │  (unwritten   │  │
│                                             │   → written)  │  │
│                                             └───────────────┘  │
└──────────────────────┬──────────────────────────────────────────┘
                       │ bio / buffer_head
┌──────────────────────▼──────────────────────────────────────────┐
│              Block Layer  →  Storage / DAX device               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Block Mapping Types

When a filesystem implements `iomap_begin()`, it classifies the range with a
mapping type:

| Type | Value | Meaning |
|---|---|---|
| `IOMAP_HOLE` | 0 | No blocks allocated; read returns zeroes |
| `IOMAP_DELALLOC` | 1 | Delayed allocation; blocks not yet committed |
| `IOMAP_MAPPED` | 2 | Blocks allocated at `iomap.addr` |
| `IOMAP_UNWRITTEN` | 3 | Allocated but unwritten (preallocated); zeroes on read |
| `IOMAP_INLINE` | 4 | Data stored inline in the inode |

### 2. struct iomap

The central descriptor returned by `iomap_begin()`:

```c
struct iomap {
    u64             addr;       // disk block address (or IOMAP_NULL_ADDR)
    loff_t          offset;     // file offset
    u64             length;     // byte length of this mapping
    u16             type;       // IOMAP_MAPPED, IOMAP_HOLE, etc.
    u16             flags;      // IOMAP_F_NEW, IOMAP_F_DIRTY, …
    struct block_device *bdev;  // target block device
    struct dax_device   *dax_dev; // for DAX
    void            *inline_data; // for IOMAP_INLINE
    void            *private;   // filesystem private data
    const struct iomap_folio_ops *folio_ops; // optional per-folio callbacks
};
```

### 3. iomap_iter Loop

All I/O in iomap is driven by an **iteration loop**:

```c
while ((ret = iomap_iter(&iter, ops)) > 0) {
    // iter.iomap contains the mapping for iter.pos
    ret = iomap_read_inline_data(&iter, folio);
    // or submit bio, fill folio, etc.
}
```

`iomap_iter()` calls `ops->iomap_begin()` for each sub-range and
`ops->iomap_end()` after the operation body completes.

### 4. Buffered I/O (`buffered-io.c`)

- **Read**: `iomap_read_folio()` / `iomap_readahead()` — maps the file range,
  fills page cache folios from disk.
- **Write**: `iomap_file_buffered_write()` — maps range (allocating if needed),
  copies user data into dirty folios, marks uptodate bits via
  `iomap_folio_state`.
- **Writeback**: `iomap_writepages()` — iterates dirty folios, creates `iomap_ioend`
  structures, submits bios, and converts unwritten extents on completion.

### 5. Direct I/O (`direct-io.c`)

`iomap_dio_rw()` bypasses the page cache entirely:
1. Calls `iomap_begin()` to get block mapping.
2. Allocates `iomap_dio`, submits bios directly.
3. On completion: calls `iomap_end()` to convert unwritten extents.
4. Supports async I/O (`IOCB_NOWAIT`), write-through (`IOCB_SYNC`).

### 6. ioend and Writeback Completion (`ioend.c`)

An `iomap_ioend` collects a contiguous range of in-flight write bios. On
completion it calls `iomap_end()`, which in XFS converts unwritten extents
to written state — a critical correctness step ensuring data visibility.

### 7. Folio State (`iomap_folio_state`)

For large folios (multi-page), iomap tracks uptodate and dirty state
**per-block** using a bitmap embedded in `iomap_folio_state`, attached to the
folio via `folio->private`. This replaces the per-buffer-head state of the
old path.

---

## I/O Flow — Buffered Write

```
Userspace              VFS / filesystem           iomap core
    │                        │                        │
    │  write(fd, buf, len)   │                        │
    │ ──────────────────────►│                        │
    │                        │  iomap_file_buffered_  │
    │                        │    write()             │
    │                        │ ──────────────────────►│
    │                        │                        │ iomap_iter()
    │                        │                        │ ops->iomap_begin()
    │                        │◄───────────────────────│ (e.g., xfs_write_iomap_begin)
    │                        │  returns IOMAP_MAPPED   │
    │                        │ ──────────────────────►│
    │                        │                        │ grab/lock folio
    │                        │                        │ copy_from_user()
    │                        │                        │ set dirty bits
    │                        │                        │ ops->iomap_end()
    │                        │◄───────────────────────│ (may convert delalloc)
    │◄───────────────────────│ returns bytes written   │
    │                        │                        │
    │  (later: writeback)    │                        │
    │                        │  iomap_writepages()    │
    │                        │ ──────────────────────►│ submit bios
    │                        │                        │ iomap_ioend completion
    │                        │                        │ ops->iomap_end() → unwritten→written
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct iomap` | Block mapping descriptor (type, disk addr, length, flags) |
| `struct iomap_iter` | Iteration state for a single I/O operation |
| `struct iomap_ops` | Filesystem vtable (`iomap_begin`, `iomap_end`) |
| `struct iomap_dio` | In-flight direct I/O operation |
| `struct iomap_ioend` | Writeback I/O completion unit |
| `struct iomap_folio_state` | Per-block uptodate/dirty bitmap for large folios |

## Key Source Files

| File | Purpose |
|---|---|
| `fs/iomap/buffered-io.c` | Buffered read/write/writeback/readahead |
| `fs/iomap/direct-io.c` | Direct I/O (bypasses page cache) |
| `fs/iomap/ioend.c` | Write completion, unwritten→written conversion |
| `fs/iomap/fiemap.c` | Extent reporting (FIEMAP ioctl) |
| `fs/iomap/seek.c` | SEEK_HOLE / SEEK_DATA |
| `fs/iomap/swapfile.c` | Swap file activation |
| `include/linux/iomap.h` | Public API and data structures |

---

## Analogy

iomap is like a **GPS navigation system for data**:

- The **filesystem** (`iomap_begin`) is the GPS: given a file position, it
  tells you the exact physical block address (or that no road exists yet —
  `IOMAP_HOLE`).
- The **iomap core** is the driver following GPS instructions, fetching or
  delivering data to/from that physical location.
- An **unwritten extent** is a reserved parking space — it's yours, but no car
  is there yet; iomap's job after writing is to mark the space as occupied.
- **Direct I/O** is an express courier bypassing the warehouse (page cache).
- **Buffered I/O** warehouses goods (folios) before shipping them to disk in bulk.

---

## References

- `include/linux/iomap.h` — Full API
- `Documentation/filesystems/iomap/` — Design docs
- `fs/xfs/xfs_iomap.c` — XFS iomap_ops implementation (reference)
- `fs/ext4/inode.c` — ext4 iomap_ops implementation
