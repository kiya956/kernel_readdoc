# QRTR — Qualcomm IPC Router

## Overview

The QRTR (Qualcomm IPC Router) subsystem implements inter-processor communication
for Qualcomm Snapdragon SoCs in the Linux kernel. It provides a message-passing
framework between the application processor (AP), modem DSP, compute DSP, and
other subsystems within a Qualcomm chipset.

Key features:

- **AF_QIPCRTR sockets** — BSD socket interface for IPC messaging
- **Node-based routing** — Each processor is a node with a unique ID
- **Service discovery** — Name server for locating services by (service, instance)
- **Multiple transports** — SMD, GLINK, MHI (PCIe), TUN

QRTR is essential for Qualcomm-based phones, Chromebooks, and IoT devices where
the modem, WiFi, GPS, and audio DSPs communicate via IPC.

## Kernel Source

- **Directory:** `net/qrtr/`
- **Headers:** `include/linux/qrtr.h`, `include/uapi/linux/qrtr.h`
- **Config:** `CONFIG_QRTR`, `CONFIG_QRTR_MHI`, `CONFIG_QRTR_SMD`, `CONFIG_QRTR_TUN`

## Architecture

```
┌─────────────────────────────────────────────┐
│       User Space (rmtfs, pd-mapper,         │
│       qmi-proxy, modem-manager)             │
│          AF_QIPCRTR sockets                 │
├─────────────────────────────────────────────┤
│            QRTR Core                        │
│  ┌───────────────────────────────────┐      │
│  │  Name Server (NS)                │      │
│  │  Service ↔ (node, port) mapping  │      │
│  ├───────────────────────────────────┤      │
│  │  Router                          │      │
│  │  Node table, endpoint dispatch   │      │
│  ├───────────────────────────────────┤      │
│  │  Socket Layer                    │      │
│  │  qrtr_sendmsg / qrtr_recvmsg    │      │
│  └───────────────────────────────────┘      │
├─────────────────────────────────────────────┤
│         Transport Endpoints                 │
│  ┌─────────┬──────────┬──────────┬────────┐ │
│  │  SMD    │  GLINK   │   MHI    │  TUN   │ │
│  │ shared  │  Qualcomm│  PCIe    │ debug  │ │
│  │ memory  │  FIFO    │  modem   │ /test  │ │
│  └─────────┴──────────┴──────────┴────────┘ │
├─────────────────────────────────────────────┤
│     Remote Processors (Modem, DSP, etc.)    │
└─────────────────────────────────────────────┘
```

## Packet Flow

```
 SEND PATH                              RECEIVE PATH
 ─────────                              ────────────

 User app (e.g. rmtfs)                  Remote processor sends msg
     │                                       │
     ▼                                       ▼
 qrtr_sendmsg()                         qrtr_endpoint_post()
     │                                       │
     ▼                                       ▼
 Resolve destination                    Parse QRTR header
 (node, port) from NS                   (version, type, src, dst)
     │                                       │
     ▼                                  ┌────┴─────┐
 qrtr_node_enqueue()                   │          │
     │                              Local?     Name Server?
     ▼                                  │          │
 Build QRTR header                      ▼          ▼
 (type, src_node,                   qrtr_local  qrtr_ns
  src_port, dst_*)                  _enqueue    _worker
     │                                  │          │
     ▼                                  ▼          ▼
 endpoint->xmit()                   Socket recv  Service
     │                                           lookup/
     ▼                                           register
 Transport TX
 (SMD/GLINK/MHI)
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct qrtr_node` | `net/qrtr/af_qrtr.c` | Remote processor node — holds endpoint ref |
| `struct qrtr_sock` | `net/qrtr/af_qrtr.c` | Per-socket state for AF_QIPCRTR |
| `struct qrtr_endpoint` | `include/linux/qrtr.h` | Transport endpoint (SMD, MHI, etc.) |
| `struct qrtr_ctrl_pkt` | `include/linux/qrtr.h` | Control packet for NS messages |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `qrtr_endpoint_post()` | `net/qrtr/af_qrtr.c` | Receive data from transport endpoint |
| `qrtr_sendmsg()` | `net/qrtr/af_qrtr.c` | Socket sendmsg for AF_QIPCRTR |
| `qrtr_recvmsg()` | `net/qrtr/af_qrtr.c` | Socket recvmsg for AF_QIPCRTR |
| `qrtr_node_enqueue()` | `net/qrtr/af_qrtr.c` | Enqueue message to destination node |
| `qrtr_ns_worker()` | `net/qrtr/ns.c` | Name server worker — handle service lookups |
| `qrtr_endpoint_register()` | `net/qrtr/af_qrtr.c` | Register a transport endpoint |

## Analogy

QRTR is like a **corporate office intercom system**. Each department (processor —
modem, GPS, audio) has its own office number (node ID). Services within each
department have extension numbers (port). The receptionist (name server) keeps a
directory of "who provides what service." When accounting (app processor) needs the
modem department to make a call, they ask the receptionist for the modem's extension,
then dial directly. The intercom wires (SMD, GLINK, MHI) are different physical
connections between offices.
