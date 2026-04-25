# L2TP — Layer 2 Tunneling Protocol

## Overview

**L2TP (Layer 2 Tunneling Protocol)** encapsulates PPP or Ethernet frames
inside UDP/IP tunnels.  The kernel implementation supports both **L2TPv2**
(RFC 2661 — PPP sessions only) and **L2TPv3** (RFC 3931 — Ethernet, HDLC,
Frame-Relay, ATM pseudowires).

L2TP operates in two layers:

- **Tunnel** — a UDP (or IP) connection between two L2TP peers
- **Session** — a virtual circuit multiplexed inside a tunnel, carrying one
  PPP or Ethernet pseudowire

The kernel `net/l2tp/` subsystem handles:
- Tunnel lifecycle (create / destroy / keepalive)
- Session management (bind / unbind / statistics)
- Data-plane encapsulation and decapsulation
- Netlink control plane (`l2tp_netlink.c`)
- PPP integration (`l2tp_ppp.c`), Ethernet pseudowires (`l2tp_eth.c`)
- Optional IP encapsulation (`l2tp_ip.c`, `l2tp_ip6.c`)

Source: `net/l2tp/`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER SPACE                                   │
│  pppd / xl2tpd / iproute2 "ip l2tp"                                │
│  ↕ Netlink (GENL family "l2tp")                                    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                   L2TP NETLINK CONTROL PLANE                        │
│        l2tp_netlink.c — l2tp_nl_cmd_tunnel_create/get/delete        │
│                         l2tp_nl_cmd_session_create/get/delete        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                    L2TP CORE  (l2tp_core.c)                         │
│                                                                     │
│  struct l2tp_tunnel        struct l2tp_session                      │
│  ┌──────────────────┐      ┌──────────────────────┐                │
│  │ tunnel_id        │      │ session_id           │                │
│  │ peer_tunnel_id   │      │ peer_session_id      │                │
│  │ sock (UDP/IP)    │      │ tunnel (backptr)     │                │
│  │ session list     │      │ recv_skb / send_skb  │                │
│  │ version (2 or 3) │      │ pseudowire type      │                │
│  └──────────────────┘      └──────────────────────┘                │
│                                                                     │
│  l2tp_tunnel_create()  — allocate tunnel, bind to UDP socket        │
│  l2tp_session_create() — allocate session within a tunnel           │
│  l2tp_recv_common()    — RX: validate header, lookup session,       │
│                          strip L2TP header, deliver to session       │
│  l2tp_xmit_skb()       — TX: build L2TP header, send via UDP/IP    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                     FRAME ON THE WIRE                               │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ Ethernet Header (14 B)                                        │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │ IP Header (20 B)                                              │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │ UDP Header (8 B)  dst port 1701 (v2) or dynamic (v3)         │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │ L2TP Header                                                   │  │
│  │   v2: T|L|S|O flags, tunnel-id, session-id, Ns/Nr (12+ B)   │  │
│  │   v3: session-id (4 B) + optional cookie                     │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │ L2TP Session Payload                                          │  │
│  │   PPP frame (v2)  or  Ethernet/HDLC pseudowire (v3)          │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## L2TPv2 vs L2TPv3

| Feature | L2TPv2 (RFC 2661) | L2TPv3 (RFC 3931) |
|---|---|---|
| Payload | PPP only | Ethernet, HDLC, ATM, Frame-Relay |
| Encapsulation | UDP only (port 1701) | UDP or raw IP (proto 115) |
| Session ID | 16-bit | 32-bit |
| Tunnel ID | 16-bit | 32-bit (control connection ID) |
| Cookie | Not supported | 4 or 8 byte anti-spoofing cookie |
| Header size | 12+ bytes (flags, tunnel, session, Ns/Nr) | 4+ bytes (session ID + cookie) |
| Kernel modules | `l2tp_ppp` | `l2tp_eth`, `l2tp_ip`, `l2tp_ip6` |

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `struct l2tp_tunnel` | `l2tp_core.h` | Represents one L2TP tunnel (UDP/IP socket + session list) |
| `struct l2tp_session` | `l2tp_core.h` | One session inside a tunnel (pseudowire endpoint) |
| `struct l2tp_net` | `l2tp_core.c` | Per-netns L2TP state (tunnel IDR, session IDR) |
| `struct pppol2tp_session` | `l2tp_ppp.c` | PPP-specific session data (PPP channel) |
| `struct l2tp_eth` | `l2tp_eth.c` | Ethernet pseudowire net_device |

---

## Key Functions

| Function | File | Purpose |
|---|---|---|
| `l2tp_tunnel_create()` | `l2tp_core.c` | Create tunnel, register in netns |
| `l2tp_tunnel_delete()` | `l2tp_core.c` | Tear down tunnel and all sessions |
| `l2tp_session_create()` | `l2tp_core.c` | Allocate session within a tunnel |
| `l2tp_session_delete()` | `l2tp_core.c` | Remove session |
| `l2tp_recv_common()` | `l2tp_core.c` | RX path: header parse → session lookup → deliver |
| `l2tp_xmit_skb()` | `l2tp_core.c` | TX path: build L2TP header → udp_sendmsg/ip_local_out |
| `l2tp_nl_cmd_tunnel_create()` | `l2tp_netlink.c` | Netlink: create tunnel |
| `l2tp_nl_cmd_session_create()` | `l2tp_netlink.c` | Netlink: create session |
| `l2tp_nl_cmd_tunnel_get()` | `l2tp_netlink.c` | Netlink: dump tunnel info |
| `pppol2tp_connect()` | `l2tp_ppp.c` | Bind PPPoL2TP socket to session |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/l2tp/l2tp_core.c` | Tunnel/session lifecycle, RX/TX data path |
| `net/l2tp/l2tp_core.h` | Core structures and helpers |
| `net/l2tp/l2tp_netlink.c` | Generic Netlink control plane |
| `net/l2tp/l2tp_ppp.c` | PPPoL2TP — L2TPv2 PPP pseudowire |
| `net/l2tp/l2tp_eth.c` | L2TPv3 Ethernet pseudowire |
| `net/l2tp/l2tp_ip.c` | L2TPv3 over raw IPv4 |
| `net/l2tp/l2tp_ip6.c` | L2TPv3 over raw IPv6 |
| `net/l2tp/l2tp_debugfs.c` | Debugfs tunnel/session listing |

---

## Tracing and Debugging

### Debugfs

```bash
# Mount debugfs (if not already mounted)
mount -t debugfs none /sys/kernel/debug

# List tunnels and sessions
cat /sys/kernel/debug/l2tp/tunnels
```

### procfs (L2TPv2)

```bash
cat /proc/net/pppol2tp
```

### bpftrace probes

```bash
# Trace L2TP packet receive
bpftrace -e 'kprobe:l2tp_recv_common { printf("l2tp rx tunnel=%d\n", arg1); }'

# Trace L2TP packet transmit
bpftrace -e 'kprobe:l2tp_xmit_skb { printf("l2tp tx session=%p\n", arg0); }'

# Trace tunnel creation
bpftrace -e 'kprobe:l2tp_tunnel_create { printf("tunnel create fd=%d\n", arg1); }'

# Trace session creation
bpftrace -e 'kprobe:l2tp_session_create { printf("session create\n"); }'

# Trace netlink commands
bpftrace -e 'kprobe:l2tp_nl_cmd_tunnel_get { printf("nl tunnel get\n"); }'
```

### iproute2 management

```bash
# Create an L2TPv3 UDP tunnel
ip l2tp add tunnel tunnel_id 1 peer_tunnel_id 1 \
    encap udp local 192.168.1.1 remote 192.168.1.2 \
    udp_sport 5000 udp_dport 5000

# Create a session (Ethernet pseudowire)
ip l2tp add session tunnel_id 1 session_id 1 peer_session_id 1

# Show tunnels / sessions
ip l2tp show tunnel
ip l2tp show session
```

---

## References

- `net/l2tp/` — kernel source
- RFC 2661 — L2TPv2 specification
- RFC 3931 — L2TPv3 specification
- `Documentation/networking/l2tp.rst` — kernel documentation
