# Linux Kernel Unix Domain Sockets (AF_UNIX) — Local IPC

## Overview

**Unix domain sockets** (`AF_UNIX` / `AF_LOCAL`) provide high-performance
inter-process communication on the same host. Unlike TCP/UDP, data never
touches the network stack — it is copied directly between socket buffers.

Key features:
- **Filesystem paths** (`/var/run/docker.sock`) or **abstract namespace** (`\0name`)
- **SOCK_STREAM** (connection-oriented), **SOCK_DGRAM** (connectionless),
  **SOCK_SEQPACKET** (connection-oriented, message boundaries)
- **File descriptor passing** via `SCM_RIGHTS` ancillary messages
- **Peer credential passing** via `SCM_CREDENTIALS` (pid, uid, gid)
- **SO_PEERCRED** / **SO_PEERPIDFD** for connected sockets

Source: `net/unix/`, `include/net/af_unix.h`.

Used by: systemd, D-Bus, Docker, X11/Wayland, PostgreSQL, MySQL, gpg-agent.

---

## Subsystem Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         USERSPACE                                │
│                                                                  │
│  Process A                          Process B                    │
│  ┌─────────────────────┐            ┌──────────────────────┐     │
│  │ socket(AF_UNIX,     │            │ socket(AF_UNIX,      │     │
│  │   SOCK_STREAM, 0)   │            │   SOCK_STREAM, 0)    │     │
│  │ bind("/path/sock")  │            │ connect("/path/sock") │     │
│  │ listen(backlog)     │            │                      │     │
│  │ accept() ──────────────────────────► connected pair     │     │
│  │                     │            │                      │     │
│  │ sendmsg(fd,         │            │ recvmsg(fd,          │     │
│  │   SCM_RIGHTS, fds)  │───data+fds──►  SCM_RIGHTS, fds)  │     │
│  │ sendmsg(fd,         │            │ recvmsg(fd,          │     │
│  │   SCM_CREDENTIALS)  │──creds────►│   SCM_CREDENTIALS)   │     │
│  └─────────────────────┘            └──────────────────────┘     │
│                                                                  │
│  socketpair(AF_UNIX, SOCK_STREAM, 0, sv)  ← anonymous pair      │
└──────────────────────────┬───────────────────────────────────────┘
                           │  syscall interface
┌──────────────────────────▼───────────────────────────────────────┐
│                    AF_UNIX SOCKET LAYER                          │
│                   (net/unix/af_unix.c)                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  struct unix_sock  (extends struct sock)                 │    │
│  │  ├── peer          → connected peer unix_sock            │    │
│  │  ├── addr          → struct unix_address (path/abstract) │    │
│  │  ├── oob_skb       → out-of-band data (SOCK_STREAM)     │    │
│  │  ├── inflight      → SCM_RIGHTS in-flight fd count       │    │
│  │  └── lock          → per-socket mutex                    │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌────────────────────┐ ┌────────────────┐ ┌─────────────────┐  │
│  │ SOCK_STREAM ops    │ │ SOCK_DGRAM ops │ │ SOCK_SEQPACKET  │  │
│  │ unix_stream_       │ │ unix_dgram_    │ │ unix_seqpacket_ │  │
│  │   sendmsg()        │ │   sendmsg()    │ │   sendmsg()     │  │
│  │   recvmsg()        │ │   recvmsg()    │ │   recvmsg()     │  │
│  │   connect()        │ │   connect()    │ │   connect()     │  │
│  └────────────────────┘ └────────────────┘ └─────────────────┘  │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Ancillary Data (net/unix/scm.c + net/core/scm.c)         │ │
│  │  SCM_RIGHTS  → pass file descriptors between processes     │ │
│  │  SCM_CREDENTIALS → pass (pid, uid, gid) credentials       │ │
│  │  SO_PEERCRED → retrieve connected peer credentials         │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Garbage Collector (net/unix/garbage.c)                    │ │
│  │  unix_gc() — detect and collect cycles of in-flight fds    │ │
│  │  Prevents fd reference leaks from SCM_RIGHTS              │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────────┐
│                   VFS / FILESYSTEM                               │
│  Socket file in filesystem (inode, dentry)                       │
│  Abstract namespace: no filesystem entry, \0-prefixed            │
│  /proc/net/unix — lists all open unix sockets                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Data Structures

### `struct unix_sock` (`include/net/af_unix.h`)
Extends `struct sock` for AF_UNIX sockets.

| Field              | Description                                       |
|--------------------|---------------------------------------------------|
| `peer`             | Pointer to connected peer's `unix_sock`           |
| `addr`             | `struct unix_address` — path or abstract name     |
| `path`             | `struct path` — dentry for filesystem sockets     |
| `recvq`            | Receive queue (incoming data / connection requests)|
| `oob_skb`          | Out-of-band urgent data skb (SOCK_STREAM)         |
| `inflight`         | Count of in-flight fds passed via SCM_RIGHTS      |
| `lock`             | Per-socket serialisation                          |

### `struct unix_address`
Variable-length address structure.

| Field              | Description                                       |
|--------------------|---------------------------------------------------|
| `len`              | Address length (including sun_family)             |
| `hash`             | Hash for lookup in unix_socket_table              |
| `name`             | `struct sockaddr_un` — sun_family + sun_path      |
| `refcnt`           | Reference count                                   |

---

## Key Functions

| Function                   | File          | Purpose                                |
|----------------------------|---------------|----------------------------------------|
| `unix_stream_sendmsg()`    | af_unix.c     | Send data on SOCK_STREAM socket        |
| `unix_dgram_sendmsg()`     | af_unix.c     | Send datagram on SOCK_DGRAM socket     |
| `unix_stream_connect()`    | af_unix.c     | Connect STREAM client to server        |
| `unix_stream_recvmsg()`    | af_unix.c     | Receive on STREAM socket               |
| `unix_dgram_recvmsg()`     | af_unix.c     | Receive datagram                       |
| `unix_bind()`              | af_unix.c     | Bind socket to path or abstract addr   |
| `unix_listen()`            | af_unix.c     | Mark socket as listening               |
| `unix_accept()`            | af_unix.c     | Accept incoming connection             |
| `unix_release()`           | af_unix.c     | Socket close / cleanup                 |
| `unix_gc()`                | garbage.c     | Garbage-collect in-flight fd cycles    |
| `scm_send()`               | net/core/scm.c | Parse ancillary data (SCM_RIGHTS, etc)|

---

## SCM_RIGHTS — File Descriptor Passing

The primary superpower of Unix sockets. A process can send open file
descriptors to another process through ancillary messages:

```c
/* Sender */
struct msghdr msg = {};
struct cmsghdr *cmsg;
char buf[CMSG_SPACE(sizeof(int))];
msg.msg_control = buf;
msg.msg_controllen = sizeof(buf);
cmsg = CMSG_FIRSTHDR(&msg);
cmsg->cmsg_level = SOL_SOCKET;
cmsg->cmsg_type = SCM_RIGHTS;
cmsg->cmsg_len = CMSG_LEN(sizeof(int));
*(int *)CMSG_DATA(cmsg) = fd_to_pass;
sendmsg(sock, &msg, 0);
```

Kernel path: `scm_send()` → `scm_fp_copy()` → `unix_attach_fds()` →
fds travel as `SCM_RIGHTS` in the skb's control buffer → receiver calls
`recvmsg()` → `scm_detach_fds()` installs fds in receiver's fd table.

---

## Configuration & Observability

```
CONFIG_UNIX=y     # Always built-in on modern kernels
```

### procfs

| Path               | Description                                    |
|--------------------|------------------------------------------------|
| `/proc/net/unix`   | List all unix sockets (inode, path, state)     |

### Useful commands

```bash
# List unix sockets
cat /proc/net/unix
ss -x -a

# Count unix sockets
ss -x -a | wc -l

# Trace stream send
bpftrace -e 'kprobe:unix_stream_sendmsg { printf("AF_UNIX send pid=%d\n", pid); }'

# Trace fd passing
bpftrace -e 'kprobe:scm_send { printf("SCM send pid=%d\n", pid); }'
```

---

## Tracing & Debugging

```bash
# Trace connection attempts
bpftrace -e 'kprobe:unix_stream_connect {
    printf("unix connect pid=%d comm=%s\n", pid, comm);
}'

# Trace garbage collector
bpftrace -e 'kprobe:unix_gc { printf("UNIX GC triggered\n"); }'

# Monitor socket creation
bpftrace -e 'kprobe:unix_create {
    printf("unix_create type=%d pid=%d\n", arg2, pid);
}'
```
