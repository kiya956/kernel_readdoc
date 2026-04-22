# Linux Kernel fsnotify / inotify / fanotify

## Overview

The Linux filesystem notification framework (**fsnotify**) provides a generic
in-kernel infrastructure for watching filesystem events. Two well-known user
APIs are built on top of it:

- **inotify** — per-file/directory watches, widely used by editors, build tools, desktop file managers
- **fanotify** — coarser (mount/filesystem-wide) watches with optional permission decisions; used by antivirus, backup, and audit tools

Source: `fs/notify/` with subdirs `inotify/`, `fanotify/`, `dnotify/`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  inotify_init1() / inotify_add_watch()      fanotify_init() /  │
│  read(inotify_fd) ──► struct inotify_event  fanotify_mark()    │
│                                             read(fanotify_fd)  │
│                                             write(fanotify_fd) │
│                                             (allow/deny)       │
└────────────────────────┬──────────────────────┬────────────────┘
                         │ syscall               │ syscall
┌────────────────────────▼──────────────────────▼────────────────┐
│                   INOTIFY API           FANOTIFY API            │
│              (fs/notify/inotify/)   (fs/notify/fanotify/)       │
│                                                                 │
│  inotify_user.c                     fanotify_user.c            │
│  inotify_fsnotify.c                 fanotify.c                 │
│  (inotify_group_ops,                (fanotify_group_ops,        │
│   inotify_event_ops)                 fanotify_event_ops)        │
└────────────────────────┬──────────────────────┬────────────────┘
                         │                      │
┌────────────────────────▼──────────────────────▼────────────────┐
│                     FSNOTIFY CORE                               │
│               (fs/notify/fsnotify.c + mark.c + group.c)         │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  fsnotify_group  (one per inotify/fanotify fd)           │  │
│  │                                                          │  │
│  │  notification_list ── fsnotify_event queue               │  │
│  │  marks_list        ── all marks owned by this group      │  │
│  │  ops               ── handle_event / free_event          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  fsnotify_mark  (one per watched inode/mountpoint/dir)   │  │
│  │                                                          │  │
│  │  mask      ── event types to watch (IN_CREATE, IN_MODIFY)│  │
│  │  connector ── links mark to object (inode/mount/sb)      │  │
│  │  group     ── back-pointer to owning group               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Global dispatch:  fsnotify() ──► iterate groups on inode       │
│                    fsnotify_parent() ──► also notify parent dir  │
└────────────────────────────────┬────────────────────────────────┘
                                 │ hooks in VFS
┌────────────────────────────────▼────────────────────────────────┐
│                      VFS / filesystem                           │
│                                                                 │
│  fsnotify_create()   fsnotify_delete()   fsnotify_modify()     │
│  fsnotify_rename()   fsnotify_open()     fsnotify_access()     │
│  fsnotify_attrib()   fsnotify_move()     fsnotify_close()      │
│  (called from path_openat, vfs_unlink, vfs_rename, etc.)       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. VFS Hooks

Every significant filesystem operation calls a `fsnotify_*` helper. For example:

```c
// in fs/namei.c
vfs_create() ──► fsnotify_create(dir, dentry)
// in fs/read_write.c
vfs_write()  ──► fsnotify_modify(file)
```

These helpers call `fsnotify()`, which walks the list of marks on the inode
(and optionally its parent) and calls each group's `handle_event` op.

### 2. fsnotify_group

A `struct fsnotify_group` is created for each `inotify_init()` or
`fanotify_init()` call. It contains:

- An **event queue** (`notification_list`) — events produced but not yet `read()`.
- A **mark list** — all `fsnotify_mark` objects associated with this fd.
- `ops` vtable — `handle_event` (add to queue), `free_event`, `free_mark`, etc.

### 3. fsnotify_mark

A `struct fsnotify_mark` anchors a watch to a specific kernel object:

| Connector type | Kernel object | API |
|---|---|---|
| `FSNOTIFY_OBJ_TYPE_INODE` | `struct inode` | inotify watches |
| `FSNOTIFY_OBJ_TYPE_VFSMOUNT` | `struct vfsmount` | fanotify `FAN_MARK_MOUNT` |
| `FSNOTIFY_OBJ_TYPE_SB` | `struct super_block` | fanotify `FAN_MARK_FILESYSTEM` |

Each mark carries an event **mask** (e.g., `FS_CREATE | FS_MODIFY`) and an
**ignored mask** for events to suppress.

### 4. inotify

inotify is the classic per-inode watch API:

```
inotify_init1(flags)  ──► returns fd (backed by fsnotify_group)
inotify_add_watch(fd, path, mask) ──► returns watch descriptor (wd)
read(fd) ──► struct inotify_event { wd, mask, cookie, len, name[] }
inotify_rm_watch(fd, wd)
```

Events are queued in the group's `notification_list` and coalesced if the
previous queued event has the same `wd` and `mask`.

### 5. fanotify

fanotify extends inotify with:

- **Mount-wide** and **filesystem-wide** marks (not just per-inode)
- **Permission events** (`FAN_OPEN_PERM`, `FAN_ACCESS_PERM`) — the listener must
  write an allow/deny response before the kernel proceeds
- **`FAN_REPORT_FID`** — reports inode identity, not just an open file descriptor
- **`FAN_REPORT_DIR_FID`** + **`FAN_REPORT_NAME`** — full path info

```
fanotify_init(flags, event_f_flags) ──► fd
fanotify_mark(fd, FAN_MARK_ADD, mask, AT_FDCWD, path)
read(fd) ──► struct fanotify_event_metadata { fd, mask, … }
// for permission events:
write(fd, &response, sizeof(response))  ──► FAN_ALLOW or FAN_DENY
```

### 6. dnotify (legacy)

`fs/notify/dnotify/` implements the older `fcntl(F_NOTIFY)` API that delivers
signals on directory changes. Largely superseded by inotify; kept for
compatibility.

---

## Event Dispatch Flow

```
VFS operation         fsnotify core              inotify group
      │                     │                         │
      │  vfs_create()       │                         │
      │ ───────────────────►│  fsnotify_create()      │
      │                     │  fsnotify()              │
      │                     │  iterate marks on inode  │
      │                     │ ────────────────────────►│ handle_event()
      │                     │                         │ alloc inotify_event
      │                     │                         │ enqueue to notification_list
      │                     │                         │ wake_up(group->notification_waitq)
      │                     │                         │
      │                     │                         │
Userspace process                                      │
  read(inotify_fd) ─────────────────────────────────►│
                                                      │ copy inotify_event to user
                                                      │ (filename follows struct)
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `fsnotify_group` | `include/linux/fsnotify_backend.h` | Per-fd notification group |
| `fsnotify_mark` | `include/linux/fsnotify_backend.h` | Per-object watch |
| `fsnotify_event` | `fs/notify/fsnotify.h` | Generic queued event |
| `inotify_event_info` | `fs/notify/inotify/inotify.h` | inotify-specific event |
| `fanotify_event` | `fs/notify/fanotify/fanotify.h` | fanotify-specific event |

## Key Source Files

| File | Purpose |
|---|---|
| `fs/notify/fsnotify.c` | Core dispatch, VFS hook implementations |
| `fs/notify/mark.c` | Mark lifecycle (alloc, attach, detach, free) |
| `fs/notify/group.c` | Group lifecycle and event queue |
| `fs/notify/notification.c` | Event allocation and queue management |
| `fs/notify/inotify/inotify_user.c` | inotify syscall implementation |
| `fs/notify/inotify/inotify_fsnotify.c` | inotify `fsnotify_group_ops` |
| `fs/notify/fanotify/fanotify_user.c` | fanotify syscall implementation |
| `fs/notify/fanotify/fanotify.c` | fanotify `fsnotify_group_ops` |

---

## Analogy

Think of fsnotify as a **hotel concierge notification board**:

- Each **VFS operation** (file create/modify/delete) is a **guest event** posted
  on the board.
- Each **fsnotify_group** is a **hotel department** (room service, security)
  that has registered interest in certain board entries.
- Each **fsnotify_mark** is a sticky note on a specific room's door saying "notify
  department X when anything happens here".
- **inotify** apps are staff who read notifications passively.
- **fanotify permission events** are like a security guard who must actively
  approve or deny before the guest can proceed (e.g., open a door).

---

## References

- `include/linux/fsnotify_backend.h` — Core data structures and ops
- `include/uapi/linux/inotify.h` — inotify UAPI event flags
- `include/uapi/linux/fanotify.h` — fanotify UAPI
- `Documentation/admin-guide/filesystem-monitoring.rst`
