# Linux Kernel fs-verity — File Integrity Verification

## Overview

**fs-verity** (filesystem verity) provides **read-only, file-based integrity
protection** using a **Merkle tree** stored alongside the file. When a file is
opened and read, the kernel transparently verifies each page against the Merkle
tree. A root hash (or digital signature) anchors the tree to a trusted policy.

Used for: Android system images, ChromeOS rootfs, FIPS-verified binaries,
firmware blobs. Supported by ext4, f2fs, btrfs, and erofs.

Source: `fs/verity/`, `include/linux/fsverity.h`, `include/uapi/linux/fsverity.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│   ioctl(FS_IOC_ENABLE_VERITY, &params)  ─── enable verity      │
│   ioctl(FS_IOC_MEASURE_VERITY, &digest) ─── read root hash     │
│   ioctl(FS_IOC_READ_VERITY_METADATA)    ─── read Merkle tree   │
└─────────────────────────────┬───────────────────────────────────┘
                              │ ioctl
┌─────────────────────────────▼───────────────────────────────────┐
│                   FS-VERITY CORE                                │
│                                                                 │
│  ┌─────────────────────┐    ┌───────────────────────────────┐  │
│  │  enable.c           │    │  open.c                       │  │
│  │                     │    │                               │  │
│  │  FS_IOC_ENABLE:     │    │  On file open:                │  │
│  │  Build Merkle tree  │    │  Load fsverity_descriptor     │  │
│  │  from file data,    │    │  Build fsverity_info (in-mem) │  │
│  │  store to FS via    │    │  Verify root hash against     │  │
│  │  fsverity_ops,      │    │  built-in or keyring sig      │  │
│  │  set S_VERITY flag. │    └───────────────────────────────┘  │
│  └─────────────────────┘                                       │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  verify.c  (hot path — called on every page read)       │   │
│  │                                                         │   │
│  │  fsverity_verify_blocks():                              │   │
│  │   1. Compute hash of data block                        │   │
│  │   2. Walk Merkle tree upward, hashing at each level    │   │
│  │   3. Compare computed root hash with stored root hash  │   │
│  │   4. Return error if mismatch → I/O error to user      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────┐  ┌────────────────┐  ┌────────────────┐  │
│  │  hash_algs.c     │  │  signature.c   │  │  measure.c     │  │
│  │  SHA-256/SHA-512 │  │  PKCS#7 sig    │  │  MEASURE_VERITY│  │
│  │  CRC-32          │  │  verification  │  │  ioctl handler │  │
│  └──────────────────┘  └────────────────┘  └────────────────┘  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ fsverity_operations callbacks
┌──────────────────────────────▼──────────────────────────────────┐
│                  FILESYSTEM  (ext4 / f2fs / btrfs / erofs)      │
│                                                                 │
│  .begin_enable_verity()   ── evict inline data, set up space   │
│  .end_enable_verity()     ── persist verity descriptor          │
│  .get_verity_descriptor() ── read descriptor at open time      │
│  .read_merkle_tree_page() ── supply Merkle tree pages           │
│  .write_merkle_tree_block()── store Merkle tree blocks          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                BLOCK / STORAGE  (read-only)                     │
│  File data pages  ──  Merkle tree pages  ──  verity descriptor  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Merkle Tree Structure

The Merkle tree is stored **within the filesystem** (e.g., in ext4 verity
metadata blocks). Layout:

```
Level 2 (root): H( H(H0||H1) || H(H2||H3) || … )  ← root_hash
Level 1:        H(H0||H1), H(H2||H3), …
Level 0:        H(block_0), H(block_1), …
Data:           block_0, block_1, block_2, …
```

Each hash covers `block_size` bytes (default: filesystem block size, 4 KB).
Hash algorithm: SHA-256 (default), SHA-512, or CRC-32.

### 2. Enabling verity (`enable.c`)

```
ioctl(fd, FS_IOC_ENABLE_VERITY, &params)
  → fsverity_ioctl_enable()
    → ops->begin_enable_verity()
    → build Merkle tree: hash all data blocks, then hash level by level
    → compute root hash
    → optionally verify/embed PKCS#7 signature
    → write fsverity_descriptor to filesystem
    → ops->end_enable_verity()
    → set S_VERITY flag on inode
    → file is now read-only and verified
```

After enabling, the file **cannot be written**. `open(O_WRONLY)` returns `-EPERM`.

### 3. On-open verification (`open.c`)

When a verity file is opened:
1. `fsverity_file_open()` checks `S_VERITY` flag.
2. Reads `fsverity_descriptor` via `ops->get_verity_descriptor()`.
3. Builds in-memory `fsverity_info` (Merkle tree parameters, root hash, hash algo).
4. If built-in signature support is enabled, verifies PKCS#7 signature against
   the `.fs-verity` keyring.
5. Stores `fsverity_info` in `inode->i_verity_info`.

### 4. Per-page verification (`verify.c`) — Hot Path

Called by the filesystem's `->readahead()` after reading each data page from disk:

```c
fsverity_verify_blocks(folio, start, len)
  → for each 4KB block:
      hash = sha256(block_data)
      walk Merkle tree upward, verifying each level
      compare computed root == stored root_hash
      if mismatch: fsverity_err() → mark folio error
```

Hash blocks are cached using page cache with a dedicated inode
(`fsverity_get_merkle_tree_page()`), so repeated verification of the same level
is fast.

### 5. Signature Verification (`signature.c`)

Optional feature (`CONFIG_FS_VERITY_BUILTIN_SIGNATURES`): the `fsverity_descriptor`
may include a PKCS#7 signature over the root hash. Verified against the
`.fs-verity` IMA keyring on open.

---

## Enable / Read Flow

```
Userspace                  fs-verity core         Filesystem
    │                           │                      │
    │  ioctl ENABLE_VERITY       │                      │
    │ ─────────────────────────►│  fsverity_ioctl_     │
    │                           │    enable()           │
    │                           │  ops->begin()  ──────►│
    │                           │  build Merkle tree     │
    │                           │  ops->write_block()──►│ store tree
    │                           │  compute root_hash    │
    │                           │  ops->end()    ──────►│ persist descriptor
    │◄──────────────────────────│ 0 (success)           │
    │                           │                      │
    │  read(fd, buf, len)        │                      │
    │ ─────────────────────────►│ (via VFS readahead)  │
    │                           │                      │ read data page
    │                           │◄─────────────────────│
    │                           │  fsverity_verify_    │
    │                           │    blocks()           │
    │                           │  ✓ hash matches root  │
    │◄──────────────────────────│ data returned to user │
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `fsverity_info` | In-memory, per-inode verity state (hash algo, root hash, tree params) |
| `fsverity_descriptor` | On-disk descriptor stored by filesystem |
| `fsverity_operations` | Filesystem vtable for Merkle tree I/O |
| `fsverity_hash_alg` | Hash algorithm descriptor (SHA-256, SHA-512, etc.) |
| `fsverity_enable_arg` | UAPI: parameters for `FS_IOC_ENABLE_VERITY` |

## Key Source Files

| File | Purpose |
|---|---|
| `fs/verity/enable.c` | `FS_IOC_ENABLE_VERITY` ioctl, Merkle tree construction |
| `fs/verity/open.c` | Open-time descriptor load and `fsverity_info` build |
| `fs/verity/verify.c` | Per-page hash verification (hot path) |
| `fs/verity/hash_algs.c` | SHA-256/SHA-512/CRC-32 hash algorithm wrappers |
| `fs/verity/signature.c` | PKCS#7 signature verification |
| `fs/verity/measure.c` | `FS_IOC_MEASURE_VERITY` ioctl |
| `fs/verity/read_metadata.c` | `FS_IOC_READ_VERITY_METADATA` ioctl |
| `include/linux/fsverity.h` | Kernel API and `fsverity_operations` |
| `include/uapi/linux/fsverity.h` | User API (ioctls and structs) |

---

## Usage

```bash
# Enable verity on a file (file must be on ext4/f2fs with verity enabled)
fsverity enable /path/to/file

# Measure (read root hash)
fsverity measure /path/to/file

# Sign a file for enforced signature verification
fsverity sign /path/to/file /path/to/file.sig --key=key.pem --cert=cert.pem

# Check if verity is enabled
stat /path/to/file | grep Flags   # shows 'V' for verity
```

---

## Analogy

fs-verity is like **tamper-evident packaging with a seal**:

- Each **data block** is like a paragraph in a document.
- The **Merkle tree** is a chain of checksums: paragraph → section → chapter →
  book digest.
- The **root hash** is the seal on the outside of the package.
- Every time a page is read, the kernel **re-checks the seal** on that specific
  paragraph against the trusted root. If someone modified a single byte on disk,
  the hash chain breaks and the read fails.
- Enabling verity is like **laminating the document**: it becomes read-only and
  tamper-evident forever.

---

## References

- `include/uapi/linux/fsverity.h` — UAPI ioctls
- `Documentation/filesystems/fsverity.rst` — Design documentation
- `fs/verity/` — Implementation
- `fsverity-utils` userspace tool
