# Linux Kernel: RPMsg (Remote Processor Messaging) Subsystem

> Source: `drivers/rpmsg/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is RPMsg?

**RPMsg** is the inter-processor communication (IPC) framework for sending
messages between the Linux application CPU and remote processors (DSP, MCU,
modem, sensor hub). It sits on top of transport backends:

- **VirtIO** — shared ring buffers in RAM (QEMU, remoteproc)
- **Qualcomm GLINK/SMD** — shared memory channels (modem, ADSP, SLPI)
- **MediaTek RPMsg** — tinySYS / SCP (sensor/audio co-processor)

RPMsg exposes a **bus** where each channel appears as a device, and rpmsg
drivers bind to named channels just like platform drivers bind to DT nodes.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    USERSPACE                                     │
│  /dev/rpmsg_ctrl0  (create/destroy channels)                    │
│  /dev/rpmsg<N>     (read/write messages on a channel)           │
└───────────────────────┬──────────────────────────────────────────┘
                        │  read/write/ioctl
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│       RPMSG CHAR  (rpmsg_char.c + rpmsg_ctrl.c)                 │
│  rpmsg_ctrl: RPMSG_CREATE_EPT_IOCTL / RPMSG_DESTROY_EPT_IOCTL  │
│  rpmsg_char: per-endpoint file → read()/write() to remote proc  │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│            RPMSG CORE  (rpmsg_core.c + rpmsg_ns.c)              │
│                                                                  │
│  rpmsg_create_ept()   — create local receive endpoint           │
│  rpmsg_send()         — send message to remote endpoint         │
│  rpmsg_sendto()       — send to specific dst address             │
│  rpmsg_trysend()      — non-blocking send (returns -ENOMEM)     │
│                                                                  │
│  rpmsg_ns.c: Name Service — announce/destroy channel names      │
│    Remote processor announces "gps_channel" on addr 0x400       │
│    → kernel creates rpmsg_device, probe() called                │
└──────────┬──────────────────────────────────────────────────────┘
           │  rpmsg_ops callbacks (send, create_ept, ...)
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                 TRANSPORT BACKENDS                               │
│                                                                  │
│  virtio_rpmsg_bus.c                                             │
│  ├─ VirtIO vring-based transport                                │
│  ├─ Two vrings: TX (host→remote) + RX (remote→host)             │
│  ├─ Used with remoteproc (DSP/MCU firmware loading)             │
│  └─ Works with QEMU virtio-rpmsg device                         │
│                                                                  │
│  qcom_glink_smem.c / qcom_glink_rpm.c                          │
│  ├─ Qualcomm GLINK over SMEM (shared memory)                   │
│  ├─ Channels: modem ↔ apps, ADSP ↔ apps, SLPI ↔ apps          │
│  └─ qcom_glink_ssr.c: subsystem restart notification           │
│                                                                  │
│  qcom_smd.c                                                     │
│  ├─ Legacy Qualcomm Shared Memory Driver                        │
│  └─ Older platforms (MSM8x series)                              │
│                                                                  │
│  mtk_rpmsg.c                                                    │
│  └─ MediaTek SCP (Sensor Control Processor) IPC                 │
└──────────┬──────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Shared Memory / VirtIO ring / Hardware mailbox                  │
│  Remote Processor: DSP / MCU / Modem / SCP                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `rpmsg_core.c` — Bus and API

The RPMsg **bus** matches `rpmsg_driver` to `rpmsg_device` by channel name:

```c
/* Driver side */
static struct rpmsg_driver my_driver = {
    .drv.name = "my_rpmsg_driver",
    .id_table = my_id_table,       // { .name = "my_channel" }
    .probe    = my_probe,
    .callback = my_rx_callback,    // called on every received message
    .remove   = my_remove,
};
module_rpmsg_driver(my_driver);

/* In probe: */
ept = rpmsg_create_ept(rpdev, my_rx_callback, priv,
                       (struct rpmsg_channel_info){ .src = RPMSG_ADDR_ANY });

/* Send: */
rpmsg_send(ept, &msg, sizeof(msg));
```

### 3.2 `rpmsg_ns.c` — Name Service

When the remote processor boots, it broadcasts channel announcements:
```
NS message: { name="gps_channel", addr=0x400, flags=ALIVE }
```
The kernel receives this, creates an `rpmsg_device`, and calls `driver_match`
→ `probe()`. On channel teardown, the remote sends a `DESTROY` announcement.

### 3.3 `virtio_rpmsg_bus.c` — VirtIO Transport

Uses two VirtIO queues (vrings):
- **RX vring** — remote→host messages; kernel allocates buffers, remote fills them
- **TX vring** — host→remote messages; kernel fills buffers, kicks the vring

Buffer pool (typically 512 × 512-byte buffers) is allocated in the VirtIO
device memory region (usually remoteproc carveout memory).

### 3.4 `qcom_glink_smem.c` — Qualcomm GLINK

GLINK is Qualcomm's newer IPC protocol over SMEM (shared DDR carveout).
Features:
- **Intents** — pre-allocated receive buffers; sender must request intent first
- **Intent requests** — dynamic buffer allocation negotiation
- **SSR (Subsystem Restart)** — automatic channel cleanup on remote crash

GLINK channels expose as `rpmsg_device` so generic rpmsg drivers work
unchanged across virtio and GLINK backends.

### 3.5 `rpmsg_char.c` + `rpmsg_ctrl.c` — Userspace Access

`/dev/rpmsg_ctrl0` accepts:
- `RPMSG_CREATE_EPT_IOCTL` → creates `/dev/rpmsg<N>` bound to a dst address
- `RPMSG_DESTROY_EPT_IOCTL` → tears down endpoint

`/dev/rpmsg<N>` is a standard char device:
- `write()` — sends message to remote
- `read()` / `poll()` — receives messages from remote (blocking)

---

## 4. Data Flow: Linux → DSP Message

```
 Linux driver (rpmsg client)       VirtIO transport         DSP firmware
 ──────────────────────────        ────────────────         ────────────
 1. rpmsg_send(ept, data, len)
         │
 2. virtio_rpmsg_send()
    get TX buffer from free list
    copy data into buffer
    add to TX vring
         │
 3. virtqueue_kick() ─────────────► 4. Doorbell interrupt to DSP
                                         │
                                    5. DSP firmware reads TX vring
                                       processes message
                                       writes reply to RX vring
                                       kicks RX vring
         │
 6. RX interrupt ◄───────────────────── 5. (DSP kicks RX vring)
         │
 7. virtio_rpmsg_rx_work:
    drain RX vring
    call endpoint->cb(data, len)
         │
 8. my_rx_callback(rpdev, data, len)
    process DSP response
```

---

## 5. Sysfs / Devfs Layout

```
/sys/bus/rpmsg/devices/
  virtio0.rpmsg-openamp-demo.-1.0/    ← rpmsg channel device
    name       → "rpmsg-openamp-demo"
    src        → source address
    dst        → destination address

/dev/rpmsg_ctrl0    ← control: create/destroy endpoints
/dev/rpmsg0         ← endpoint: read/write messages
```

---

## 6. Summary

RPMsg:
1. **Decouples transport** — the same rpmsg driver works over VirtIO, GLINK,
   SMD, or MediaTek IPC without code changes.
2. **Name Service** — channels self-discover at runtime as the remote
   processor announces them, no static configuration required.
3. **Userspace bridge** — `/dev/rpmsg*` lets user programs communicate with
   firmware running on DSP/MCU without writing kernel drivers.
4. **Integrates with remoteproc** — rpmsg channels appear automatically when
   remoteproc boots a co-processor firmware image.
