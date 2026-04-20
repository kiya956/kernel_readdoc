# Android Binder IPC Subsystem

## Overview

The Android Binder is a kernel-level IPC (Inter-Process Communication) mechanism
originally designed for Android, now upstream in Linux. It enables high-performance
remote procedure calls (RPC) between processes with:

- Zero-copy data transfer via shared memory mapping
- Object-oriented reference passing across process boundaries
- Synchronous and asynchronous transaction modes
- Security context propagation and death notifications

Source: `drivers/android/` (binder.c, binder_alloc.c, binderfs.c)

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Userspace                                     │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │  Client App  │   │ Service App  │   │   Service Manager        │ │
│  │  (Caller)    │   │  (Callee)    │   │   (Context Manager)      │ │
│  └──────┬───────┘   └──────┬───────┘   └───────────┬──────────────┘ │
│         │ libbinder        │ libbinder              │ /dev/binder    │
│         │ (Android SDK)    │ (Android SDK)          │                │
└─────────┼──────────────────┼────────────────────────┼────────────────┘
          │ ioctl()          │ ioctl()                │ ioctl()
          ▼ mmap()           ▼ mmap()                 ▼ mmap()
┌─────────────────────────────────────────────────────────────────────┐
│                    VFS Layer                                         │
│   open("/dev/binder") → binder_open()                               │
│   mmap()              → binder_mmap()                               │
│   ioctl()             → binder_ioctl()                              │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                    Binder Core (binder.c)                            │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    binder_proc                                │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │   │
│  │  │binder_thread│  │ binder_node  │  │    binder_ref      │  │   │
│  │  │  (per-thread│  │ (service obj │  │  (client handle    │  │   │
│  │  │   context)  │  │  endpoint)   │  │   to remote node)  │  │   │
│  │  └──────┬──────┘  └──────┬───────┘  └────────────────────┘  │   │
│  │         │                │                                    │   │
│  │  ┌──────▼────────────────▼──────────────────────────────┐    │   │
│  │  │            binder_transaction                         │    │   │
│  │  │  from_proc → to_proc, buffer, code, flags             │    │   │
│  │  └───────────────────────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  binder_alloc (binder_alloc.c)                │   │
│  │  vm_start ──► [buffer pool: free_buffers + allocated_buffers] │   │
│  │  mmap window shared between sender kernel and receiver user   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  binderfs (binderfs.c)                        │   │
│  │  Virtual filesystem: /dev/binderfs/<name>                     │   │
│  │  Per-IPC-namespace binder device nodes (containers/Android)   │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│              Memory Management (mm subsystem)                        │
│  vm_mmap(), vm_insert_page(), zap_page_range()                       │
│  Binder maps receiver's buffer pool into kernel VA                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Userspace: libbinder / AIDL

Android apps use `libbinder` (or Rust/Java wrappers) which communicates
with the kernel via three syscalls:

| Syscall | Purpose |
|---------|---------|
| `open("/dev/binder")` | Create a binder_proc context |
| `mmap(4MB)` | Map the shared buffer pool into process VA space |
| `ioctl(BINDER_WRITE_READ)` | Submit transactions / receive replies |

### 2. VFS Entry Points (binder.c)

```c
static const struct file_operations binder_fops = {
    .open           = binder_open,
    .mmap           = binder_mmap,
    .unlocked_ioctl = binder_ioctl,
    .compat_ioctl   = compat_binder_ioctl,
    .poll           = binder_poll,
    .release        = binder_release,
    .flush          = binder_flush,
};
```

- **`binder_open()`**: Allocates `binder_proc`, links thread/pid, adds to global `binder_procs` list.
- **`binder_mmap()`**: Calls `binder_alloc_mmap_handler()` to set up the zero-copy buffer region.
- **`binder_ioctl()`**: Dispatch hub for all operations (write/read, thread management, version negotiation).

### 3. Core Data Structures

| Struct | Role |
|--------|------|
| `binder_proc` | Per-process context: threads, nodes, refs, todo list |
| `binder_thread` | Per-thread transaction stack and work queue |
| `binder_node` | A service endpoint exported by a process |
| `binder_ref` | A client-side handle referencing a remote `binder_node` |
| `binder_transaction` | An in-flight IPC call: from_proc→to_proc, buffer |
| `binder_buffer` | Memory region in the zero-copy pool for one transaction |

### 4. binder_alloc: Zero-Copy Buffer Management

The key performance feature: **receiver's buffer pool is mapped read-only into
the kernel VA**, so the kernel can `copy_from_user()` the sender's data directly
into the receiver's mapped memory. Only **one copy** happens (sender→kernel),
receiver reads from its own VMA.

```
Sender writes:       [sender user VA] ──copy_from_user──► [kernel VA = receiver mmap]
Receiver reads:                                            [receiver user VA]  ↑same pages
```

Buffer lifecycle:
1. `binder_alloc_new_buf()` — allocate from free_buffers rb-tree
2. Kernel copies transaction data into buffer
3. Receiver gets pointer via BC_TRANSACTION / BR_TRANSACTION
4. Receiver calls `BC_FREE_BUFFER` → `binder_alloc_free_buf()`

### 5. Transaction Flow (BC/BR Commands)

BC_ = "Binder Command" (userspace → kernel)
BR_ = "Binder Return" (kernel → userspace)

```
Client          Kernel              Server
  │                │                  │
  │ BC_TRANSACTION │                  │
  ├───────────────►│                  │
  │                │ enqueue work     │
  │                │ wake server      │
  │                ├─────────────────►│
  │                │                  │ BR_TRANSACTION
  │                │◄─────────────────┤
  │                │ BC_REPLY         │
  │                │◄─────────────────┤
  │ BR_REPLY       │                  │
  │◄───────────────┤                  │
```

### 6. binderfs

`binderfs` is a virtual filesystem (like `procfs`) that provides per-IPC-namespace
binder devices. Used by containers and Android VMs so each namespace gets its own
isolated `/dev/binderfs/binder` devices and a control interface at
`/dev/binderfs/binder-control` for dynamic device creation.

### 7. Lock Ordering

Three-level lock hierarchy (must be acquired in order):
```
outer_lock (protects binder_ref)
    └── node->lock (protects binder_node fields)
            └── inner_lock (protects threads, todo lists, transaction_stack)
```

---

## Transaction Data Flow Diagram

```
  Client process (PID A)                Server process (PID B)
  ┌─────────────────────────┐           ┌──────────────────────────┐
  │                         │           │                          │
  │  write(BC_TRANSACTION)  │           │  read → BR_TRANSACTION   │
  │         │               │           │         ▲                │
  │  binder_ioctl()         │           │         │                │
  │         │               │           │  binder_thread_read()    │
  │  binder_transaction()   │           │         │                │
  │         │               │           │  binder_proc_transaction │
  │  binder_alloc_new_buf() │           │         │                │
  │         │               │           │         │                │
  │  copy_from_user()───────┼───────────┼─────────┘                │
  │  (data→kernel buf)      │           │  [zero-copy: buf is       │
  │                         │           │   in server's mmap]       │
  │  enqueue to_thread todo │           │                          │
  │         │               │           │  wake_up server thread   │
  │  sleep (sync txn)       │           │         │                │
  │         ▲               │           │  process transaction     │
  │         │               │           │         │                │
  │  BR_REPLY               │           │  BC_REPLY                │
  │  woken up               │◄──────────┼─────────┘                │
  └─────────────────────────┘           └──────────────────────────┘
```

---

## Key ioctls

| ioctl | Description |
|-------|-------------|
| `BINDER_WRITE_READ` | Main work ioctl: submit BC commands, receive BR commands |
| `BINDER_SET_MAX_THREADS` | Cap thread pool size |
| `BINDER_SET_CONTEXT_MGR` | Register as service manager (handle 0) |
| `BINDER_THREAD_EXIT` | Clean up binder thread state |
| `BINDER_VERSION` | Negotiate protocol version |
| `BINDER_GET_NODE_DEBUG_INFO` | Debug: inspect node state |
| `BINDER_FREEZE` | Freeze a process (prevent new transactions) |
| `BINDER_GET_FROZEN_INFO` | Query frozen state |

---

## Tracepoints

Available via `tracepoint:binder:*`:

| Tracepoint | Fires When |
|------------|-----------|
| `binder_ioctl` | Any ioctl entry |
| `binder_ioctl_done` | ioctl returns |
| `binder_transaction` | New transaction created |
| `binder_transaction_received` | Transaction received by target |
| `binder_wait_for_work` | Thread blocks waiting for work |
| `binder_txn_latency_free` | Transaction latency measurement |
| `binder_command` | BC command processed |
| `binder_return` | BR command dispatched |
| `binder_update_page_range` | Buffer page range updated |

---

## Files

| File | Purpose |
|------|---------|
| `binder.c` | Core IPC engine: open/mmap/ioctl, transaction engine |
| `binder_alloc.c` | Zero-copy buffer allocator (rb-tree based) |
| `binder_alloc.h` | binder_alloc / binder_buffer structs |
| `binder_internal.h` | All major structs (proc/thread/node/ref/transaction) |
| `binder_trace.h` | TRACE_EVENTs for ftrace/bpftrace |
| `binderfs.c` | Virtual FS for per-namespace binder devices |
| `dbitmap.h` | Descriptor bitmap for ref handle allocation |
| `tests/binder_alloc_kunit.c` | KUnit tests for allocator |

---

## HackMD Export

Title: **Linux Kernel Android Binder IPC Subsystem**

To publish: copy this file to [HackMD](https://hackmd.io) or use the API:
```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel Android Binder IPC Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`binder_trace_test.py`](binder_trace_test.py) in this directory for
bpftrace-based step-by-step verification of the binder IPC transaction flow.
