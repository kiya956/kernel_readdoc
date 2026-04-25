# Linux Kernel AF_PACKET — Raw Network Access via Packet Sockets

## Overview

AF_PACKET provides **direct Layer-2 (data-link) access** to network interfaces,
bypassing the kernel's protocol stack. It is the foundation for tools such as
`tcpdump`, `Wireshark`, `libpcap`, and `nmap`. Applications open a raw packet
socket and receive (or inject) complete Ethernet frames, including headers.

Three capture modes are supported:

| Mode | API | Copy | Performance |
|------|-----|------|-------------|
| **Plain** | `recvmsg()` / `sendmsg()` | per-packet kernel→user copy | Baseline |
| **TPACKET_V1/V2** | `mmap()` ring buffer | zero-copy via shared ring | ~2–3× faster |
| **TPACKET_V3** | `mmap()` + variable-length blocks | zero-copy, block-level retirement | Best for high-rate capture |

Key capabilities:

- Capture every frame on a given interface (promiscuous mode).
- Inject arbitrary L2 frames for packet crafting.
- Attach classic BPF / eBPF filters to reduce copy overhead.
- Fanout across multiple sockets for multi-threaded capture.

## Subsystem Stack

```
 ┌───────────────────────────────────────────────────────────────────┐
 │                        User Space                                │
 │                                                                  │
 │   tcpdump / libpcap          raw socket app        packet craft  │
 │       │                           │                     │        │
 │   recvmsg() or mmap()        recvmsg()             sendmsg()    │
 └───────┬───────────────────────────┬─────────────────────┬────────┘
         │                           │                     │
 ════════╪═══════════════════════════╪═════════════════════╪═════════
         │              Kernel Space │                     │
         ▼                           ▼                     ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │                      Socket Layer (AF_PACKET)                    │
 │                                                                  │
 │  packet_create()     packet_bind()      packet_setsockopt()      │
 │  packet_poll()       packet_release()   packet_getsockopt()      │
 └──────────┬────────────────────────────────────────┬──────────────┘
            │  RX path                               │  TX path
            ▼                                        ▼
 ┌─────────────────────────────┐          ┌─────────────────────────┐
 │       packet_rcv()          │          │    packet_sendmsg()     │
 │  (plain per-packet path)    │          │   ┌───────────────────┐ │
 │         – or –              │          │   │ tpacket_snd()     │ │
 │       tpacket_rcv()         │          │   │ (mmap TX ring)    │ │
 │  (TPACKET mmap ring path)   │          │   └───────────────────┘ │
 └──────────┬──────────────────┘          └────────────┬────────────┘
            │                                          │
            ▼                                          ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │                      BPF Filter Engine                           │
 │          run_filter()  —  classic BPF / eBPF program             │
 └──────────┬───────────────────────────────────────────────────────┘
            │
            ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │                    Network Device (netdev)                       │
 │                                                                  │
 │   dev_queue_xmit()          netif_receive_skb()                  │
 │        TX                         RX                             │
 └──────────────────────────┬───────────────────────────────────────┘
                            │
                            ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │                        NIC / Driver                              │
 │              e.g. e1000e, ixgbe, virtio-net                      │
 └───────────────────────────────────────────────────────────────────┘
```

### TPACKET_V3 Ring Buffer Layout

```
  mmap'd region (shared between kernel and user space)
 ┌──────────────────────────────────────────────────────────────┐
 │  Block 0               Block 1               Block 2   ...  │
 │ ┌──────────────────┐  ┌──────────────────┐  ┌────────────┐  │
 │ │ tpacket_block_desc│  │ tpacket_block_desc│  │    ...     │  │
 │ │  .version         │  │  .version         │  │            │  │
 │ │  .offset_to_first │  │  .offset_to_first │  │            │  │
 │ │  .num_pkts        │  │  .num_pkts        │  │            │  │
 │ │                   │  │                   │  │            │  │
 │ │ ┌──────────────┐  │  │ ┌──────────────┐  │  │            │  │
 │ │ │tpacket3_hdr  │  │  │ │tpacket3_hdr  │  │  │            │  │
 │ │ │  .tp_snaplen │  │  │ │  .tp_snaplen │  │  │            │  │
 │ │ │  .tp_sec     │  │  │ │  .tp_sec     │  │  │            │  │
 │ │ │  .tp_nsec    │  │  │ │  .tp_nsec    │  │  │            │  │
 │ │ │  [pkt data]  │  │  │ │  [pkt data]  │  │  │            │  │
 │ │ └──────────────┘  │  │ └──────────────┘  │  │            │  │
 │ │ ┌──────────────┐  │  │ ┌──────────────┐  │  │            │  │
 │ │ │tpacket3_hdr  │  │  │ │tpacket3_hdr  │  │  │            │  │
 │ │ │  [pkt data]  │  │  │ │  [pkt data]  │  │  │            │  │
 │ │ └──────────────┘  │  │ └──────────────┘  │  │            │  │
 │ │       ...         │  │       ...         │  │            │  │
 │ └──────────────────┘  └──────────────────┘  └────────────┘  │
 └──────────────────────────────────────────────────────────────┘

 Kernel writes packets into the current block.
 When a block is full (or a timeout fires), it is "retired" —
 the kernel sets TP_STATUS_USER and user space can read it.
 User space returns blocks by setting TP_STATUS_KERNEL.
```

## Layer-by-Layer Explanation

### 1. User-Space Interface

Applications call `socket(AF_PACKET, SOCK_RAW | SOCK_DGRAM, htons(ETH_P_ALL))`
to create a packet socket. `SOCK_RAW` includes the Ethernet header;
`SOCK_DGRAM` strips it. The socket can then be `bind()`-ed to a specific
interface via `struct sockaddr_ll`.

For high-performance capture, `setsockopt(PACKET_RX_RING)` configures a
TPACKET ring buffer, which is then `mmap()`-ed into user space.

### 2. Socket Layer (AF_PACKET family)

`packet_create()` allocates a `struct packet_sock`, registers the socket in
the protocol handler table, and sets up the receive hook. `packet_bind()`
attaches the socket to a specific `netdev`. `packet_setsockopt()` handles
ring-buffer configuration, fanout setup, BPF filter attachment, and more.

### 3. Receive Path

When a frame arrives at a network device, `netif_receive_skb()` walks the
list of registered packet types. For AF_PACKET sockets the handler is either:

- **`packet_rcv()`** — copies the `sk_buff` into the socket's receive queue
  for later `recvmsg()` retrieval.
- **`tpacket_rcv()`** — writes packet metadata + data directly into the
  next slot in the TPACKET mmap ring. No `recvmsg()` needed; user space
  reads the ring directly.

Both paths run the socket's BPF filter (if any) before accepting the packet.

### 4. Transmit Path

`packet_sendmsg()` builds an `sk_buff` from user-space data and hands it to
`dev_queue_xmit()`. When a TX ring is configured, `tpacket_snd()` reads
pre-built frames from the mmap ring instead.

### 5. BPF Filtering

Classic BPF programs (compiled by libpcap from tcpdump filter expressions)
are attached via `SO_ATTACH_FILTER`. The kernel runs `bpf_prog_run_clear_cb()`
on each incoming `sk_buff`; packets that fail the filter are dropped before
any copy into user space.

### 6. Fanout

`PACKET_FANOUT` distributes incoming packets across a group of AF_PACKET
sockets using hash, round-robin, CPU, or eBPF-based strategies. This enables
multi-threaded capture without lock contention on a single ring.

## Key Data Structures

| Structure | Header / Source | Purpose |
|-----------|----------------|---------|
| `struct packet_sock` | `net/packet/internal.h` | Per-socket state: protocol, ifindex, mmap rings, BPF filter, fanout group |
| `struct tpacket3_hdr` | `include/uapi/linux/if_packet.h` | Per-packet header in TPACKET_V3 ring: timestamp, snaplen, VLAN info |
| `struct tpacket_block_desc` | `include/uapi/linux/if_packet.h` | Block descriptor in TPACKET_V3: block status, number of packets, offsets |
| `struct packet_ring_buffer` | `net/packet/internal.h` | Ring geometry: frame/block sizes, counts, head/tail pointers |
| `struct tpacket_req3` | `include/uapi/linux/if_packet.h` | User-space request to configure a TPACKET_V3 ring via `setsockopt()` |
| `struct sockaddr_ll` | `include/uapi/linux/if_packet.h` | L2-level socket address: protocol, ifindex, hardware address |
| `struct packet_fanout` | `net/packet/internal.h` | Fanout group: type, hash, member list, eBPF program |
| `struct packet_type` | `include/linux/netdevice.h` | Protocol handler registration (ties AF_PACKET to `netif_receive_skb`) |

## Key Functions

| Function | Source | Role |
|----------|--------|------|
| `packet_create()` | `net/packet/af_packet.c` | Create AF_PACKET socket, allocate `packet_sock` |
| `packet_bind()` | `net/packet/af_packet.c` | Bind socket to a network interface |
| `packet_rcv()` | `net/packet/af_packet.c` | Plain receive handler — copies skb to socket queue |
| `tpacket_rcv()` | `net/packet/af_packet.c` | TPACKET receive handler — writes into mmap ring |
| `packet_sendmsg()` | `net/packet/af_packet.c` | Transmit path entry — build skb and send |
| `tpacket_snd()` | `net/packet/af_packet.c` | TPACKET transmit — read frames from mmap TX ring |
| `packet_setsockopt()` | `net/packet/af_packet.c` | Handle socket options (rings, fanout, BPF, etc.) |
| `packet_getsockopt()` | `net/packet/af_packet.c` | Query socket options |
| `packet_poll()` | `net/packet/af_packet.c` | `poll()`/`select()` readiness check |
| `packet_release()` | `net/packet/af_packet.c` | Close socket, free rings, unregister handler |
| `run_filter()` | `net/packet/af_packet.c` | Execute BPF filter on incoming skb |
| `fanout_demux_hash()` | `net/packet/af_packet.c` | Hash-based fanout distribution |

## Common Operations

### Capture all traffic on eth0

```bash
# tcpdump uses AF_PACKET + TPACKET_V3 under the hood
sudo tcpdump -i eth0 -nn -c 100

# Equivalent libpcap flow:
#   socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL))
#   bind(fd, &sockaddr_ll{sll_ifindex=eth0}, ...)
#   setsockopt(fd, SOL_PACKET, PACKET_RX_RING, &req3, ...)
#   mmap(...)
#   poll() → read ring blocks
```

### Capture with a BPF filter

```bash
sudo tcpdump -i eth0 'tcp port 80' -w capture.pcap
# libpcap compiles "tcp port 80" → cBPF, attaches via SO_ATTACH_FILTER
```

### Inject a raw frame (Python)

```python
import socket, struct
ETH_P_ALL = 0x0003
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind(("eth0", 0))
frame = b'\xff' * 6 + b'\x00' * 6 + struct.pack('!H', 0x0800) + b'\x00' * 46
s.send(frame)
```

## Key Source Files

| File | Description |
|------|-------------|
| `net/packet/af_packet.c` | Core implementation: socket ops, RX/TX paths, ring management |
| `net/packet/internal.h` | Internal structures: `packet_sock`, `packet_ring_buffer`, `packet_fanout` |
| `include/uapi/linux/if_packet.h` | UAPI: `sockaddr_ll`, `tpacket_req3`, `tpacket3_hdr`, constants |
| `include/linux/filter.h` | BPF filter definitions used by `SO_ATTACH_FILTER` |
| `net/core/filter.c` | BPF program execution (`bpf_prog_run_clear_cb`) |
| `net/core/dev.c` | `netif_receive_skb()` — feeds frames to packet type handlers |
| `tools/testing/selftests/net/` | Kernel self-tests for packet socket features |

## Analogy

Think of AF_PACKET as a **wire tap on a telephone line**. Normally, calls
(IP packets) are routed through a switchboard (the kernel's protocol stack)
to the correct phone (application socket). A wire tap (AF_PACKET socket)
sits at the physical wire level and records every signal that passes — before
the switchboard even sees it. The TPACKET mmap ring is like a recording
reel: the tap writes continuously onto the reel, and the analyst (user-space
application) reads from it at their own pace. If the analyst falls behind,
old blocks are retired and overwritten — just like a security camera loop.

## References

- `man 7 packet` — AF_PACKET socket interface documentation
- `man 8 tcpdump` — Primary consumer of AF_PACKET
- `Documentation/networking/packet_mmap.rst` — Kernel docs on TPACKET / mmap rings
- `include/uapi/linux/if_packet.h` — UAPI header (definitive constant/struct reference)
- [libpcap source](https://github.com/the-tcpdump-group/libpcap) — Reference AF_PACKET user
- [Linux kernel networking: packet_mmap](https://www.kernel.org/doc/html/latest/networking/packet_mmap.html)
- LWN article: *Packet fanout* — `https://lwn.net/Articles/518043/`
