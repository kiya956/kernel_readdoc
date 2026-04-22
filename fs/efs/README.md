# EFS — Extent File System (SGI IRIX)

## Overview

**EFS (Extent File System)** is a read-only Linux filesystem driver for the
**SGI IRIX** native filesystem format.  EFS was the primary filesystem on SGI
IRIX workstations and servers before XFS replaced it.  The Linux driver
(`fs/efs/`) supports mounting and reading EFS-formatted SGI disk partitions,
which is useful for data migration from old SGI systems.

Key characteristics:
- **Read-only** — Linux EFS support is intentionally read-only (no write)
- **Block-based** with extent allocation — each inode contains a list of
  (start_block, length) extents rather than block pointers
- **Cylinder group** layout — disk divided into cylinder groups for locality
- **SGI partition table support** — reads the SGI volume header to find EFS partitions
- **Short symlinks** stored inline in the inode

Source: `fs/efs/`, `include/linux/efs_fs_sb.h`, `include/linux/efs_vh.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  mount -t efs -o ro /dev/sdX /mnt/sgi                        │
│  ls /mnt/sgi; cat /mnt/sgi/file.txt                           │
└──────────────────────────────┬─────────────────────────────────┘
                               │  VFS
┌──────────────────────────────▼─────────────────────────────────┐
│               VFS LAYER  (inode.c, dir.c, file.c, namei.c)     │
│                                                                 │
│  efs_file_operations — read_iter (extent-based reads)          │
│  efs_dir_operations  — readdir (raw EFS dirent traversal)      │
│  efs_inode_operations — lookup, symlink follow                 │
│  efs_symlink_operations — short inline symlinks                │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│               INODE / EXTENT LAYER  (inode.c)                  │
│                                                                 │
│  efs_iget() — read EFS inode from disk                        │
│   EFS inode: di_extents[] — array of extents (start+len)      │
│   If di_numextents ≤ 12: extents stored directly in inode      │
│   If di_numextents > 12: indirect extent block                 │
│  efs_bmap() — map logical block → physical block via extents   │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            SUPERBLOCK / DISK LAYOUT  (super.c)                 │
│                                                                 │
│  efs_fill_super() — reads EFS superblock (block 1)            │
│  SGI volume header at block 0: lists partition table           │
│  Superblock fields:                                            │
│   fs_size     — total filesystem size in blocks               │
│   fs_firstcg  — first cylinder group block                    │
│   fs_cgfsize  — cylinder group size                           │
│   fs_cgisize  — inode blocks per cylinder group               │
│   fs_magic    — 0x72959ACE (EFS_MAGIC)                        │
└──────────────────────────────┬─────────────────────────────────┘
                               │  buffer_head
┌──────────────────────────────▼─────────────────────────────────┐
│            BLOCK DEVICE  (raw SGI disk / image file)           │
└────────────────────────────────────────────────────────────────┘
```

---

## EFS Disk Layout

```
Block 0:    SGI Volume Header (partition table, disk label)
Block 1:    EFS Superblock
Block 2+:   Cylinder groups, each containing:
              - Cylinder group header (inode bitmap, free extents)
              - Inode table
              - Data blocks (extents)
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `efs_sb_info` | Superblock in-memory: fs size, CG info, magic |
| `efs_inode_info` | Inode in-memory: extent array, type, size |
| `efs_extent` | One extent: (start_block, length) pair |
| `efs_dinode` | On-disk inode format (IRIX/EFS layout) |

---

## Key Source Files

| File | Purpose |
|---|---|
| `fs/efs/super.c` | Superblock read, mount, SGI partition table |
| `fs/efs/inode.c` | Inode read, extent-to-block mapping |
| `fs/efs/dir.c` | Directory entry iteration |
| `fs/efs/file.c` | File read (extent-based block mapping) |
| `fs/efs/namei.c` | Filename lookup |
| `fs/efs/symlink.c` | Inline symlink handling |
| `fs/efs/efs.h` | On-disk format constants |
| `include/linux/efs_vh.h` | SGI volume header format |
| `include/linux/efs_fs_sb.h` | Superblock and CG structures |

---

## Analogy

EFS is like a **museum piece filing cabinet** brought into a modern office:

- The cabinet (EFS disk) uses an older filing convention (extents instead of
  FAT chains or block trees), but the contents are perfectly readable.
- Linux acts as a **translator** — it understands the EFS layout and presents
  it through the modern VFS API without being able to change any files
  (read-only access only).
- The **SGI volume header** at block 0 is like the cabinet's index card:
  "Here's where each partition lives and what filesystem is in each drawer."

---

## References

- `fs/efs/` — full implementation
- `include/linux/efs_fs_sb.h`, `include/linux/efs_vh.h` — on-disk formats
- SGI IRIX EFS documentation (historical)
