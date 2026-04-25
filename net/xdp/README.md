# XDP (eXpress Data Path) Subsystem

## Overview

eXpress Data Path (XDP) is a programmable, high-performance, kernel-based
packet processing framework. XDP hooks into the network driver's receive path
**before** the kernel allocates an `sk_buff`, enabling early-stage decisions
such as drop, forward, redirect, or pass-to-stack at near line-rate speeds.

XDP programs are written in restricted C, compiled to BPF bytecode, and
attached to a network device via `bpf()` syscall or `bpf_xdp_link_attach()`.
The driver invokes the XDP program for every received packet, and the program
returns a verdict that determines the packet's fate.

## Architecture

```
                          ┌──────────────────────────────────────────┐
                          │              User Space                  │
                          │  ┌────────┐  ┌─────────┐  ┌──────────┐  │
                          │  │ AF_XDP │  │  iproute │  │ bpftool  │  │
                          │  │  (xsk) │  │    ip    │  │          │  │
                          │  └───▲────┘  └────┬─────┘  └────┬─────┘  │
                          └─────┼─────────────┼─────────────┼────────┘
         ═══════════════════════╪═════════════╪═════════════╪══════════
                          ┌─────┼─────────────┼─────────────┼────────┐
                          │     │  Kernel      │             │        │
                          │     │             ▼             ▼        │
                          │     │      ┌─────────────────────────┐   │
                          │     │      │   BPF subsystem         │   │
                          │     │      │  (prog load / attach)   │   │
                          │     │      └────────────┬────────────┘   │
                          │     │                   │                │
                          │     │                   ▼                │
   ┌─────┐   RX    ┌─────┴─────┴───────────────────────────┐       │
   │ NIC ├────────►│         Network Driver (e.g. mlx5)     │       │
   └─────┘         │                                        │       │
                   │  1. DMA packet into page/xdp_buff      │       │
                   │  2. Call XDP program (BPF_PROG_RUN)     │       │
                   │  3. Act on verdict                      │       │
                   │                                        │       │
                   │  ┌───────────────────────────────────┐ │       │
                   │  │         XDP Program                │ │       │
                   │  │  ┌─────────┐                      │ │       │
                   │  │  │xdp_buff │──► inspect / modify   │ │       │
                   │  │  └─────────┘          │            │ │       │
                   │  │                       ▼            │ │       │
                   │  │              ┌────────────────┐    │ │       │
                   │  │              │  Return Verdict │    │ │       │
                   │  │              └───────┬────────┘    │ │       │
                   │  └──────────────────────┼─────────────┘ │       │
                   └─────────────────────────┼───────────────┘       │
                                             │                       │
                  ┌──────────────────────────┼──────────────────┐    │
                  │          ┌───────────────┼───────────────┐  │    │
                  │          ▼               ▼               ▼  │    │
                  │   ┌──────────┐   ┌────────────┐   ┌──────┐ │    │
                  │   │ XDP_PASS │   │ XDP_DROP   │   │XDP_TX│ │    │
                  │   │          │   │            │   │      │ │    │
                  │   │ build    │   │ free page  │   │ send │ │    │
                  │   │ sk_buff  │   │ silently   │   │ back │ │    │
                  │   │ → stack  │   │            │   │ out  │ │    │
                  │   └──────────┘   └────────────┘   │ same │ │    │
                  │                                   │ NIC  │ │    │
                  │          ┌─────────────────┐      └──────┘ │    │
                  │          ▼                 ▼               │    │
                  │   ┌──────────────┐  ┌───────────┐          │    │
                  │   │XDP_REDIRECT  │  │XDP_ABORTED│          │    │
                  │   │              │  │           │          │    │
                  │   │ forward to   │  │ drop +    │          │    │
                  │   │ other dev /  │  │ trace     │          │    │
                  │   │ CPU / AF_XDP │  │ exception │          │    │
                  │   └──────────────┘  └───────────┘          │    │
                  └────────────────────────────────────────────┘    │
                          │                                          │
                          └──────────────────────────────────────────┘
```

### Verdict Summary

| Verdict          | Action                                              |
|------------------|------------------------------------------------------|
| `XDP_PASS`       | Continue normal stack processing (allocate sk_buff)  |
| `XDP_DROP`       | Drop the packet immediately — no sk_buff allocated   |
| `XDP_TX`         | Transmit the packet back out the same interface      |
| `XDP_REDIRECT`   | Redirect to another netdev, CPU, or AF_XDP socket    |
| `XDP_ABORTED`    | Drop + trigger `xdp:xdp_exception` tracepoint        |

## XDP Memory Models

### xdp_buff and xdp_frame

The driver fills an `xdp_buff` structure pointing into the raw DMA page.
If the packet must survive beyond the driver NAPI context (e.g. for redirect),
the driver converts `xdp_buff` → `xdp_frame` via `xdp_convert_buff_to_frame()`.

- **xdp_buff**: Transient per-packet context valid only during XDP program execution.
  Contains `data`, `data_end`, `data_meta`, `data_hard_start` pointers plus
  `rxq` (receive queue info).
- **xdp_frame**: Headroom-resident metadata that travels with the page for
  cross-device or cross-CPU forwarding.

### Page Pool

Drivers using `page_pool` (recommended) allocate RX pages from a per-queue
page pool, enabling efficient recycling. XDP leverages this for zero-copy
packet forwarding via `XDP_TX` and `XDP_REDIRECT`.

Key API:
- `page_pool_create()` — allocate a new page pool
- `page_pool_put_page()` — return a page for recycling
- `page_pool_release_page()` — release without recycling

### AF_XDP (XDP Sockets / xsk)

AF_XDP provides a high-performance path from NIC directly to user space,
bypassing the kernel networking stack entirely. It uses shared UMEM rings
(FILL, COMPLETION, RX, TX) between kernel and user space.

Key components:
- **xsk_buff_pool**: Kernel-side pool managing UMEM frames
- **XDP_REDIRECT + bpf_redirect_map(XSKMAP)**: Steers packets to AF_XDP sockets
- **xdpsock** (sample): Reference user-space AF_XDP application

## Key Kernel Structures

### struct xdp_buff (include/net/xdp.h)
```c
struct xdp_buff {
    void *data;
    void *data_end;
    void *data_meta;
    void *data_hard_start;
    struct xdp_rxq_info *rxq;
    struct xdp_txq_info *txq;
    u32 frame_sz;
    u32 flags;
};
```

### struct xdp_frame (include/net/xdp.h)
```c
struct xdp_frame {
    void *data;
    u16 len;
    u16 headroom;
    u32 metasize;
    u32 frame_sz;
    u32 flags;
    struct xdp_mem_info mem;
    struct net_device *dev_rx;
};
```

### struct xdp_rxq_info (include/net/xdp.h)
```c
struct xdp_rxq_info {
    struct net_device *dev;
    u32 queue_index;
    u32 reg_state;
    struct xdp_mem_info mem;
    unsigned int napi_id;
    u32 frag_size;
};
```

### struct xsk_buff_pool (include/net/xdp_sock_drv.h)
Manages the UMEM chunk allocation for AF_XDP zero-copy mode. Tracks
free frames, handles fill/completion rings, and provides DMA mapping.

## Key Functions

| Function                    | File                      | Purpose                                              |
|-----------------------------|---------------------------|------------------------------------------------------|
| `xdp_do_redirect()`        | `net/core/filter.c`      | Execute XDP_REDIRECT — forward to map target         |
| `xdp_do_flush()`           | `net/core/filter.c`      | Flush batched redirects at end of NAPI poll          |
| `bpf_xdp_link_attach()`    | `net/core/dev.c`         | Attach XDP BPF program to a network device           |
| `xdp_convert_buff_to_frame()` | `include/net/xdp.h`   | Convert transient xdp_buff to persistent xdp_frame   |
| `xdp_rxq_info_reg()`       | `net/core/xdp.c`        | Register an RX queue for XDP                         |
| `xdp_rxq_info_unreg()`     | `net/core/xdp.c`        | Unregister an RX queue                               |
| `xdp_return_frame()`       | `net/core/xdp.c`        | Return an xdp_frame to its memory allocator          |
| `xsk_rcv()`                | `net/xdp/xsk.c`         | Deliver packet to AF_XDP socket                      |
| `xsk_tx_peek_desc()`       | `net/xdp/xsk.c`         | Peek next TX descriptor from AF_XDP socket           |
| `page_pool_create()`       | `net/core/page_pool.c`  | Create a page pool for driver RX                     |

## Source Files

```
net/core/xdp.c              — Core XDP frame handling, rxq registration
net/core/filter.c            — BPF program execution, xdp_do_redirect/flush
net/core/dev.c               — dev_xdp_attach, XDP link management
net/xdp/xsk.c               — AF_XDP socket implementation
net/xdp/xsk_buff_pool.c     — AF_XDP buffer pool management
net/xdp/xsk_queue.h         — AF_XDP ring queue helpers
net/xdp/xdp_umem.c          — UMEM (user memory) region management
net/core/page_pool.c         — Page pool allocator used by XDP-capable drivers
include/net/xdp.h            — xdp_buff, xdp_frame, xdp_rxq_info definitions
include/net/xdp_sock_drv.h   — AF_XDP driver interface (xsk_buff_pool)
include/uapi/linux/bpf.h     — XDP verdict enums, attach types
include/linux/bpf.h           — bpf_xdp_link, bpf_prog_type definitions
kernel/bpf/devmap.c           — DEVMAP for XDP_REDIRECT between devices
kernel/bpf/cpumap.c           — CPUMAP for XDP_REDIRECT across CPUs
kernel/bpf/xskmap.c           — XSKMAP for XDP_REDIRECT to AF_XDP sockets
samples/bpf/xdpsock_user.c    — Sample AF_XDP user-space application
```

## Tracing and Debugging

### Tracepoints

XDP exposes tracepoints under the `xdp` category:

```
xdp:xdp_exception        — fired on XDP_ABORTED
xdp:xdp_bulk_tx          — fired during bulk TX flush
xdp:xdp_redirect          — fired on successful redirect
xdp:xdp_redirect_err      — fired on redirect error
xdp:xdp_redirect_map      — fired on redirect via BPF map
xdp:xdp_redirect_map_err  — fired on redirect map error
xdp:xdp_cpumap_enqueue    — fired when enqueuing to CPUMAP
xdp:xdp_cpumap_harvest    — fired when harvesting from CPUMAP
xdp:xdp_cpumap_kthread    — fired in CPUMAP kthread
xdp:xdp_devmap_xmit       — fired on DEVMAP transmit
```

### bpftrace Examples

#### Trace XDP verdicts per interface
```bash
sudo bpftrace -e '
tracepoint:xdp:xdp_redirect {
    printf("redirect: dev=%d act=%d\n", args->ifindex, args->act);
}
tracepoint:xdp:xdp_exception {
    printf("EXCEPTION: dev=%d act=%d\n", args->ifindex, args->act);
}'
```

#### Count XDP redirect calls
```bash
sudo bpftrace -e '
kprobe:xdp_do_redirect {
    @redirects = count();
}
interval:s:5 { print(@redirects); }'
```

#### Monitor page pool creation
```bash
sudo bpftrace -e '
kprobe:page_pool_create {
    printf("page_pool_create called from %s\n", comm);
    @stacks[kstack] = count();
}'
```

#### Trace AF_XDP socket receive
```bash
sudo bpftrace -e '
kprobe:xsk_rcv {
    printf("xsk_rcv: pid=%d comm=%s\n", pid, comm);
}'
```

#### Monitor XDP program attachment
```bash
sudo bpftrace -e '
kprobe:bpf_xdp_link_attach {
    printf("XDP attach: pid=%d comm=%s\n", pid, comm);
}'
```

### Useful debugfs / sysfs paths

```
/sys/kernel/debug/tracing/events/xdp/       — XDP tracepoints
/sys/class/net/<dev>/xdp/                    — Per-device XDP feature flags
/proc/net/pf_xdp                             — AF_XDP socket info (if available)
```

### Quick health check

```bash
# Verify XDP kernel support
grep -c CONFIG_XDP /boot/config-$(uname -r)

# List loaded XDP programs
bpftool net list

# Show XDP program on a specific device
ip link show dev eth0 | grep xdp

# Check XDP feature flags
ethtool -k eth0 | grep xdp
```
