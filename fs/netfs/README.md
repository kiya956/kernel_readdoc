# Linux Kernel netfs — Network Filesystem Support Library

## Overview

**netfs** is a generic helper library for **network-backed filesystems** (NFS,
Ceph, SMB, AFS, 9P, etc.) that need to implement page-cache I/O with optional
local caching (**fscache**). It abstracts the common patterns of:
- Populating page-cache folios from the server
- Writing dirty folios back to the server
- Optionally caching data in a local disk cache (kcache/fscache)
- Handling DIO (direct I/O) for network filesystems

Source: `fs/netfs/`, `include/linux/netfs.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│   read() / write() / mmap() / O_DIRECT                        │
└────────────────────────────────┬────────────────────────────────┘
                                 │ VFS
┌────────────────────────────────▼────────────────────────────────┐
│              NETWORK FILESYSTEM  (NFS / Ceph / SMB / AFS …)    │
│                                                                 │
│  Implements netfs_inode within its own inode:                  │
│    netfs_read_folio()  → reads via netfs library               │
│    netfs_readahead()   → readahead via netfs library            │
│    netfs_buffered_write_iter()  → buffered writes               │
│    netfs_unbuffered_write_iter()→ DIO writes                   │
│                                                                 │
│  Provides:                                                      │
│    netfs_ops:                                                   │
│      .issue_read()  ──► send RPC to server                     │
│      .begin_cache_operation() ──► attach fscache cookie        │
│      .prepare_write()         ──► check write conflicts         │
└──────────────────────┬──────────────────────────────────────────┘
                       │ netfs API
┌──────────────────────▼──────────────────────────────────────────┐
│                       NETFS CORE                                │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  netfs_io_request  (one per read/write call)             │  │
│  │  origin / start / len / subrequests / rolling_buffer     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐  │
│  │  buffered_read.c        │  │  buffered_write.c           │  │
│  │  direct_read.c          │  │  direct_write.c             │  │
│  │  read_single.c          │  │  write_collect.c            │  │
│  │  read_collect.c         │  │  write_issue.c              │  │
│  │                         │  │  write_retry.c              │  │
│  │  Decomposes request      │  │                             │  │
│  │  into subrequests:       │  │  Splits write into chunks;  │  │
│  │  DOWNLOAD_FROM_SERVER   │  │  issues to server / cache   │  │
│  │  READ_FROM_CACHE        │  └─────────────────────────────┘  │
│  │  FILL_WITH_ZEROES       │                                   │
│  └─────────────────────────┘                                   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  fscache integration (fscache_*.c)                      │   │
│  │  Manages cookies, volumes, I/O to local cache device    │   │
│  └─────────────────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────────────────┘
                       │
        ┌──────────────┴────────────────────────┐
        │                                       │
┌───────▼──────────────────┐     ┌──────────────▼────────────────┐
│  NETWORK SERVER           │     │  LOCAL CACHE (fscache)        │
│  NFS / Ceph / SMB / AFS   │     │  CacheFiles on ext4/xfs       │
│  (via ops->issue_read())  │     │  (optional, transparent)      │
└───────────────────────────┘     └───────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. netfs_inode

A network filesystem embeds `struct netfs_inode` in its own inode:

```c
struct nfs_inode {
    struct netfs_inode netfs;   // must be first
    // NFS-specific fields …
};
```

`netfs_inode` holds the `netfs_ops` pointer and per-inode cache cookie.

### 2. netfs_io_request

The central object driving one I/O operation:

| Field | Purpose |
|---|---|
| `origin` | `NETFS_READAHEAD`, `NETFS_READPAGE`, `NETFS_DIO_READ`, `NETFS_WRITEBACK`, … |
| `start` / `len` | Byte range of the operation |
| `subreq_list` | List of `netfs_io_subrequest` fragments |
| `rolling_buffer` | Streaming I/O buffer (for large transfers) |
| `cache` | fscache operation (if caching enabled) |

### 3. Read Path

1. VFS calls `netfs_read_folio()` or `netfs_readahead()`.
2. netfs creates `netfs_io_request` with origin `NETFS_READPAGE/READAHEAD`.
3. Examines the range: splits into subrequests classified as:
   - `NETFS_DOWNLOAD_FROM_SERVER` — no local cache hit
   - `NETFS_READ_FROM_CACHE` — data available in fscache
   - `NETFS_FILL_WITH_ZEROES` — hole in sparse file
4. Issues subrequests concurrently; collects completions.
5. Marks folios uptodate; optionally stores downloaded data to fscache.

### 4. Write Path

1. `netfs_buffered_write_iter()` copies user data into page cache.
2. On writeback: `netfs_writepages()` → `netfs_io_request` with origin
   `NETFS_WRITEBACK`.
3. Splits into subrequests; issues `ops->issue_write()` to server.
4. Optionally writes to fscache in parallel.

### 5. fscache Integration

`fscache` manages **cookies** (per-inode cache handles) and **volumes**
(per-superblock cache namespaces). The netfs library calls:

```c
netfs_inode_init(ctx, &nfs_netfs_ops, false);  // at inode init
fscache_use_cookie(cookie, false);              // on file open
fscache_unuse_cookie(cookie, NULL, NULL);       // on file close
```

Cache I/O uses the same `netfs_io_request` machinery as server I/O.

---

## I/O Flow — Buffered Read with Cache

```
Filesystem (NFS)       netfs core              Server / fscache
      │                    │                        │
      │  netfs_read_folio()│                        │
      │ ──────────────────►│                        │
      │                    │ alloc netfs_io_request  │
      │                    │ check fscache: MISS      │
      │                    │ subreq: DOWNLOAD_FROM_SERVER
      │                    │ ops->issue_read()        │
      │                    │ ───────────────────────►│ RPC to server
      │                    │◄───────────────────────│ data returned
      │                    │ fill folio              │
      │                    │ mark uptodate           │
      │                    │ store to fscache ───────►│ (async)
      │◄───────────────────│ return 0                │
```

---

## Key Source Files

| File | Purpose |
|---|---|
| `fs/netfs/buffered_read.c` | Buffered read / readahead |
| `fs/netfs/buffered_write.c` | Buffered write to page cache |
| `fs/netfs/direct_read.c` | Direct (O_DIRECT) read |
| `fs/netfs/direct_write.c` | Direct (O_DIRECT) write |
| `fs/netfs/read_collect.c` | Subrequest completion aggregation |
| `fs/netfs/write_collect.c` | Write completion handling |
| `fs/netfs/fscache_cookie.c` | fscache cookie lifecycle |
| `fs/netfs/fscache_io.c` | fscache I/O submission |
| `include/linux/netfs.h` | Full API and data structures |

---

## Analogy

netfs is like a **library card catalogue + interlibrary loan system**:

- The **network server** (NFS/Ceph/SMB) is the main library with all the books.
- The **local fscache** is a nearby branch library that keeps popular copies.
- When you `read()` a file, netfs first checks the local branch (cache hit →
  fast); if not there, it orders from the main library (server download) and
  automatically donates a copy to the local branch.
- A `netfs_io_request` is a single library order; subrequests are individual
  book requests within that order, possibly from different sources.
- **Direct I/O** is walking to the main library yourself, bypassing the local
  branch entirely.

---

## References

- `include/linux/netfs.h` — API
- `Documentation/filesystems/netfs_library.rst`
- `fs/netfs/` — Implementation
- `fs/nfs/` — Example consumer (NFS uses netfs)
