# vboxsf — VirtualBox Shared Folders Guest Filesystem

## Overview

**vboxsf** is a Linux kernel filesystem driver that mounts directories
shared by a **VirtualBox host** inside a Linux guest VM.  It communicates
with the VirtualBox Guest Additions via the **VBoxGuest** hypervisor
communication channel (HGCM — Host–Guest Communication Manager), presenting
the host directory as a standard VFS mount point.

Key features:
- Full read/write access to host directories from the guest
- Page-cache backed reads and writes (page_cache_alloc / a_ops)
- Optional symlink follow on the host side
- NLS (National Language Support) for filename encoding translation
- Mount options: `uid`, `gid`, `ttl`, `dmode/fmode`, `dmask/fmask`, `nls`

Source: `fs/vboxsf/`

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                   User Space (ls, cat, cp …)                     │
└───────────────────────────┬──────────────────────────────────────┘
                            │  syscalls: open/read/write/stat/…
┌───────────────────────────▼──────────────────────────────────────┐
│                   VFS Layer                                      │
│   vboxsf_dir_inode_operations, vboxsf_file_operations            │
│   vboxsf_inode_operations, vboxsf_super_operations               │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│              fs/vboxsf/  (kernel module)                         │
│                                                                  │
│  super.c    — fill_super, statfs, remount                        │
│  dir.c      — lookup, mkdir, rmdir, create, rename, readdir      │
│  file.c     — open, release, read_iter, write_iter, mmap, fsync  │
│  utils.c    — inode alloc/fill, path conversion, NLS helpers     │
│  vboxsf_wrappers.c — thin wrappers over VBox HGCM calls          │
└───────────────────────────┬──────────────────────────────────────┘
                            │  vbox_call_vmmdev() / HGCM messages
┌───────────────────────────▼──────────────────────────────────────┐
│              drivers/virt/vboxguest/  (VBoxGuest driver)         │
│              vbox_hgcm_call() — marshal and send to hypervisor   │
└───────────────────────────┬──────────────────────────────────────┘
                            │  VirtualBox HGCM protocol (shared memory ring)
┌───────────────────────────▼──────────────────────────────────────┐
│              VirtualBox Hypervisor (host OS)                     │
│              Shared Folder service — reads/writes host FS        │
└──────────────────────────────────────────────────────────────────┘
```

---

## File Operation Sequence (read example)

```
guest read(fd, buf, n)
   │
   ▼  VFS
vboxsf_file_read_iter(iocb, iter)
   │
   ├─ filemap_read() — try page cache first
   │     │  cache miss ─────────────────────────────────────┐
   │     ▼                                                  │
   │  vboxsf_readpage() ──────────────────────────────────► │
   │     │                                                  │
   │     ▼  vboxsf_wrappers.c                               │
   │  vboxsf_read_phys_cont()                               │
   │     │                                                  │
   │     ▼  drivers/virt/vboxguest/                         │
   │  vbox_hgcm_call(SHFL_FN_READ, offset, len, physbuf) ◄──┘
   │     │
   │     ▼  hypervisor ring → host VBoxSF.so
   │  host reads file, DMA into guest pages
   │
   └─ copy to user buffer
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `vboxsf_sbi` | `vfsmod.h` | Superblock private: share handle, NLS, options |
| `vboxsf_inode` | `vfsmod.h` | Inode private: SHFL handle, mtime/size cache |
| `vboxsf_fs_context` | `super.c` | Mount options from fs_context |
| `shfl_fsobjinfo` | `shfl_hostintf.h` | Host file metadata (stat equivalent) |
| `shfl_string` | `shfl_hostintf.h` | Host-side UTF-16 path string |

---

## Key Source Files

| File | Purpose |
|---|---|
| `fs/vboxsf/super.c` | Module init, fill_super, statfs |
| `fs/vboxsf/dir.c` | Directory ops: lookup, readdir, create/unlink |
| `fs/vboxsf/file.c` | File ops: read/write, mmap, fsync |
| `fs/vboxsf/utils.c` | Inode creation, NLS, path helpers |
| `fs/vboxsf/vboxsf_wrappers.c` | HGCM call wrappers |
| `fs/vboxsf/shfl_hostintf.h` | HGCM protocol structures |

---

## Analogy

vboxsf is like a **hotel room telephone service**:

- The guest (Linux VM) picks up the phone (issues a VFS syscall).
- The hotel operator (VBoxGuest HGCM layer) translates the request into the
  hotel's internal messaging system.
- The concierge at the front desk (VirtualBox host SF service) does the
  actual work on the host filesystem.
- The result comes back through the same channel — the guest never directly
  touches the host's disk, just like a hotel guest never enters the kitchen.

---

## References

- `fs/vboxsf/` — full implementation
- `drivers/virt/vboxguest/` — HGCM transport
- `include/linux/vbox_utils.h` — exported helpers
- VirtualBox Guest Additions manual, chapter "Shared Folders"
