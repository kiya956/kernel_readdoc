# LAPB — Link Access Procedure Balanced

## Overview

**LAPB (Link Access Procedure Balanced)** is the **X.25 data link layer**
protocol — an HDLC-based reliable link protocol that provides error-free,
sequence-guaranteed frame delivery between two directly connected nodes.
LAPB operates as the Layer 2 (data link) component of the X.25 protocol
stack, sitting between the physical layer and the X.25 packet layer.

The Linux kernel `net/lapb/` module provides:
- **Connection-oriented** reliable link setup and teardown (SABM/DISC/UA)
- **Flow control** via sliding-window acknowledgement (RR, RNR)
- **Error recovery** via reject/retransmit (REJ) and timer-based retransmission
- A **state machine** managing link lifecycle transitions
- Integration with X.25 upper-layer and serial/HDLC lower-layer drivers

Source: `net/lapb/`, `include/net/lapb.h`.

---

## How LAPB Works

LAPB uses three categories of frames defined by HDLC:

### I-Frames (Information Frames)
Carry user data with send sequence number N(S) and receive sequence
number N(R) for piggyback acknowledgement.  The sliding window (default
size = 7, extended = 127) controls how many unacknowledged I-frames may
be outstanding.

### S-Frames (Supervisory Frames)
Control flow and error recovery — no user data:

| Frame | Purpose |
|-------|---------|
| **RR** (Receive Ready) | Acknowledge frames up to N(R); ready for more |
| **RNR** (Receive Not Ready) | Acknowledge up to N(R); temporarily busy |
| **REJ** (Reject) | Request retransmission starting from N(R) |

### U-Frames (Unnumbered Frames)
Manage link setup and teardown — no sequence numbers:

| Frame | Purpose |
|-------|---------|
| **SABM** (Set Asynchronous Balanced Mode) | Initiate link connection |
| **SABME** | SABM Extended — request extended (mod-128) mode |
| **DISC** (Disconnect) | Request link disconnection |
| **UA** (Unnumbered Acknowledge) | Confirm SABM or DISC |
| **DM** (Disconnected Mode) | Reject — link is down |
| **FRMR** (Frame Reject) | Protocol violation detected |

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                  X.25 PACKET LAYER (Layer 3)                   │
│  Provides virtual circuits, call setup, data transfer          │
│  Uses lapb_data_request() to send, receives via callbacks      │
└──────────────────────────────────┬─────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────┐
│              LAPB PROTOCOL ENGINE  (net/lapb/)                  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  STATE MACHINE  (lapb_cb.state)                         │   │
│  │                                                         │   │
│  │  DISCONNECTED ──SABM──▶ SETUP ──UA──▶ DATA TRANSFER    │   │
│  │       ▲                                    │            │   │
│  │       │              DISC                  │            │   │
│  │       └──── DISCONNECT ◀───────────────────┘            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  lapb_connect_request()    — initiate SABM                     │
│  lapb_disconnect_request() — initiate DISC                     │
│  lapb_data_request()       — send I-frame with user data       │
│  lapb_data_received()      — process incoming frame            │
│                                                                 │
│  Timers:                                                       │
│    T1 — Retransmission timer (awaiting acknowledgement)        │
│    T2 — Response delay timer (piggyback ack window)            │
│                                                                 │
│  Sequence numbers:                                             │
│    V(S) — send state variable     N(S) — send sequence number  │
│    V(R) — receive state variable  N(R) — receive seq number    │
│    V(A) — last acknowledged frame                              │
└──────────────────────────────────┬─────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────┐
│              PHYSICAL / HDLC LAYER  (serial drivers)           │
│  Transmits raw HDLC frames on serial links                     │
│  Uses lapb_data_received() callback for incoming frames        │
└────────────────────────────────────────────────────────────────┘
```

---

## State Machine

LAPB operates a four-state machine in `lapb_cb.state`:

| State | Value | Description |
|-------|-------|-------------|
| `LAPB_STATE_0` | 0 | **Disconnected** — no link established |
| `LAPB_STATE_1` | 1 | **Setup** — SABM sent, awaiting UA |
| `LAPB_STATE_2` | 2 | **Disconnect** — DISC sent, awaiting UA |
| `LAPB_STATE_3` | 3 | **Data Transfer** — link established, I-frames flow |
| `LAPB_STATE_4` | 4 | **Frame Reject** — FRMR sent, awaiting reset |

State transitions:

```
DISCONNECTED ──connect_request()──▶ SETUP (send SABM, start T1)
SETUP ──────────recv UA──────────▶ DATA TRANSFER (stop T1)
SETUP ──────────recv DM / T1 exp──▶ DISCONNECTED
DATA TRANSFER ──disconnect_req()──▶ DISCONNECT (send DISC, start T1)
DATA TRANSFER ──recv DISC────────▶ DISCONNECTED (send UA)
DISCONNECT ─────recv UA──────────▶ DISCONNECTED (stop T1)
```

---

## Key Data Structures

### `struct lapb_cb` — LAPB Control Block

The central per-link control structure (`include/net/lapb.h`):

| Field | Purpose |
|-------|---------|
| `state` | Current state machine state (0–4) |
| `mode` | Standard (mod-8) or extended (mod-128) mode |
| `window` | Sliding window size (default 7 or 127) |
| `vs` | V(S) — send state variable |
| `vr` | V(R) — receive state variable |
| `va` | V(A) — last acknowledged sequence |
| `t1` | T1 timer value (retransmission) |
| `t2` | T2 timer value (response delay) |
| `n2` | N2 — maximum retry count |
| `n2count` | Current retry counter |
| `t1timer` | T1 timer struct |
| `t2timer` | T2 timer struct |
| `write_queue` | Queue of I-frames awaiting transmission |
| `ack_queue` | Queue of sent I-frames awaiting acknowledgement |
| `callbacks` | Upper-layer callback functions |

---

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `lapb_connect_request()` | `lapb_iface.c` | Initiate link — send SABM, enter SETUP state |
| `lapb_disconnect_request()` | `lapb_iface.c` | Tear down link — send DISC, enter DISCONNECT state |
| `lapb_data_request()` | `lapb_iface.c` | Queue user data as I-frame for transmission |
| `lapb_data_received()` | `lapb_iface.c` | Entry point for frames received from lower layer |
| `lapb_state1_machine()` | `lapb_in.c` | Process frames in SETUP state |
| `lapb_state2_machine()` | `lapb_in.c` | Process frames in DISCONNECT state |
| `lapb_state3_machine()` | `lapb_in.c` | Process frames in DATA TRANSFER state |
| `lapb_state4_machine()` | `lapb_in.c` | Process frames in FRAME REJECT state |
| `lapb_validate_nr()` | `lapb_in.c` | Validate received N(R) against window |
| `lapb_send_control()` | `lapb_out.c` | Transmit supervisory/unnumbered frames |
| `lapb_kick()` | `lapb_out.c` | Transmit queued I-frames within window |
| `lapb_t1timer_expiry()` | `lapb_timer.c` | Handle T1 retransmission timeout |
| `lapb_t2timer_expiry()` | `lapb_timer.c` | Handle T2 response delay timeout |

---

## Key Source Files

| File | Purpose |
|------|---------|
| `net/lapb/lapb_iface.c` | User-facing API: connect, disconnect, data request/received |
| `net/lapb/lapb_in.c` | Incoming frame processing and state machine handlers |
| `net/lapb/lapb_out.c` | Outgoing frame construction and transmission |
| `net/lapb/lapb_subr.c` | Utility functions: decode, queue management |
| `net/lapb/lapb_timer.c` | T1/T2 timer management and expiry handlers |
| `include/net/lapb.h` | Control block structure and constants |

---

## Tracing and Debugging

### Key kprobes for bpftrace

```bash
# Trace incoming frame processing
sudo bpftrace -e 'kprobe:lapb_data_received { printf("LAPB rx frame dev=%p\n", arg0); }'

# Trace outgoing data requests
sudo bpftrace -e 'kprobe:lapb_data_request { printf("LAPB tx request dev=%p\n", arg0); }'

# Trace state machine transitions
sudo bpftrace -e 'kprobe:lapb_state3_machine { printf("LAPB state3 (DATA XFER) frame processing\n"); }'

# Trace T1 timer expiry (retransmission)
sudo bpftrace -e 'kprobe:lapb_t1timer_expiry { printf("LAPB T1 timeout — retransmit\n"); }'

# Trace connection setup
sudo bpftrace -e 'kprobe:lapb_connect_request { printf("LAPB connect request\n"); }'
```

### Kernel config options

| Config | Purpose |
|--------|---------|
| `CONFIG_LAPB` | Enable LAPB protocol support (y/m) |
| `CONFIG_X25` | X.25 packet layer (uses LAPB as data link) |

### Module check

```bash
lsmod | grep lapb
modprobe lapb          # load if built as module
```

---

## Analogy

LAPB is like a **certified letter exchange between two post offices**:

- **SABM** is the formal agreement: "Let's start exchanging certified
  letters."  **UA** is the confirmation: "Agreed."
- Each **I-frame** is a numbered certified letter.  The sender keeps a
  copy (ack_queue) until the recipient signs for it (RR acknowledgement).
- **RNR** means "my mailbox is full — stop sending until I say RR again."
- **REJ** means "letter #5 was damaged — resend from #5 onward."
- **T1 timer** is the "return receipt deadline" — if no acknowledgement
  arrives in time, resend the letter.
- **DISC** is the formal "we're done exchanging letters" notice.

---

## References

- `net/lapb/` — kernel implementation
- `include/net/lapb.h` — control block and API
- ITU-T X.25 — packet-switched network specification
- ISO 7776 — LAPB specification (DTE data link procedures)
- ISO 4335 / ITU-T T.71 — HDLC frame structure
