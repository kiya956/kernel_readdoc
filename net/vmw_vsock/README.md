# VMware/Virtio Virtual Socket Subsystem (`net/vmw_vsock`)

## Overview

VSOCK (Virtual Socket, `AF_VSOCK`) provides efficient host↔guest communication
for virtual machines without requiring IP networking. It uses a CID
(Context ID) addressing scheme — each VM gets a unique CID, and the host is
always CID 2. VSOCK supports both stream (SOCK_STREAM) and datagram
(SOCK_DGRAM) modes.

Three transport backends exist:
- **virtio-vsock** — for KVM/QEMU guests (most common)
- **VMware VMCI** — for VMware guests
- **Hyper-V** — for Hyper-V guests
- **vsock-loopback** — for local testing (host↔host)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              Application (AF_VSOCK socket)           │
│         socket(AF_VSOCK, SOCK_STREAM, 0)             │
├─────────────────────────────────────────────────────┤
│                VSOCK Core (af_vsock)                  │
│  ┌────────────────────────────────────────────────┐  │
│  │            struct vsock_sock                    │  │
│  │     (CID, port, state, transport pointer)       │  │
│  └──────────────────┬─────────────────────────────┘  │
│                     │                                │
│         ┌───────────┼───────────┐                    │
│         │           │           │                    │
│  ┌──────▼──────┐ ┌──▼────────┐ ┌▼────────────────┐  │
│  │  virtio     │ │  VMCI     │ │  Hyper-V        │  │
│  │  transport  │ │  transport│ │  transport      │  │
│  └──────┬──────┘ └──┬────────┘ └┬────────────────┘  │
├─────────┼───────────┼───────────┼────────────────────┤
│  virtio │     VMCI  │    VMBus  │                    │
│  device │     device│    channel│                    │
└─────────┼───────────┼───────────┼────────────────────┘
          │           │           │
    ──────┴───────────┴───────────┴──── Hypervisor
```

## Connection Workflow

```
  Guest (CID=3)                        Host (CID=2)
      │                                    │
      │  connect(CID=2, port=1234)         │
      │                                    │
      │──── REQUEST ──────────────────────►│
      │                                    │  accept()
      │◄──── RESPONSE ────────────────────│
      │                                    │
      │     [VSOCK connection established] │
      │                                    │
      │════ DATA (stream) ════════════════►│
      │◄═══ DATA (stream) ════════════════│
      │                                    │
      │──── SHUTDOWN ─────────────────────►│
      │◄──── RST ─────────────────────────│
      │                                    │

  Addressing: (CID, Port) — no IP needed
    VMADDR_CID_HOST  = 2
    VMADDR_CID_ANY   = -1
    VMADDR_CID_LOCAL = 1
```

## Key Structures

| Structure                  | Description                                          |
|----------------------------|------------------------------------------------------|
| `struct vsock_sock`        | VSOCK socket — CID, port, transport ops binding      |
| `struct vsock_transport`   | Transport operations table — send, recv, connect     |
| `struct virtio_vsock`      | Virtio transport device state                        |
| `struct virtio_vsock_pkt`  | Virtio packet — header + data for transport          |
| `struct sockaddr_vm`       | VSOCK address — (CID, port) pair                     |

## Key Functions

| Function                         | Description                                  |
|----------------------------------|----------------------------------------------|
| `vsock_stream_sendmsg()`         | Send data on VSOCK stream socket             |
| `vsock_stream_recvmsg()`         | Receive data on VSOCK stream socket          |
| `vsock_connect()`                | Initiate VSOCK connection to CID:port        |
| `vsock_accept()`                 | Accept incoming VSOCK connection              |
| `virtio_transport_recv_pkt()`    | Virtio: process received packet              |
| `virtio_transport_send_pkt()`    | Virtio: transmit a packet                    |
| `vsock_assign_transport()`       | Select transport backend for a socket        |
| `vsock_stream_connect()`         | Stream-specific connect logic                |

## Analogy

VSOCK is like an **intercom system in an apartment building**. Each apartment
(VM) has a unique number (CID), and the building lobby (host) is always
number 2. To talk to the lobby, you press the intercom button (connect to
CID 2, port N) — no phone number or IP address needed. The building's wiring
(transport backend) can be upgraded (virtio, VMCI, Hyper-V) without changing
how residents use the intercom.

## Source Files

| File                              | Purpose                              |
|-----------------------------------|--------------------------------------|
| `net/vmw_vsock/af_vsock.c`        | Core AF_VSOCK socket implementation  |
| `net/vmw_vsock/virtio_transport.c` | Virtio transport (guest side)       |
| `net/vmw_vsock/virtio_transport_common.c` | Shared virtio helpers        |
| `net/vmw_vsock/vmci_transport.c`   | VMware VMCI transport               |
| `net/vmw_vsock/hyperv_transport.c` | Hyper-V transport                   |
| `net/vmw_vsock/vsock_loopback.c`   | Loopback transport for testing      |
