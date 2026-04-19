# Linux Kernel: GNSS Receiver Subsystem

> Source: `drivers/gnss/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the GNSS subsystem?

The **GNSS subsystem** provides a uniform `/dev/gnss<N>` character device
for any GNSS (GPS/GLONASS/Galileo/BeiDou) receiver regardless of physical
transport (UART serial, USB, SPI, I2C).

Userspace reads **NMEA sentences** or vendor-specific binary frames via
`read()`. Optional `write()` sends commands to the receiver (e.g., UBX
protocol configuration for u-blox chips).

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│  USERSPACE: gpsd / chrony / ptp4l / custom apps                 │
│  read(/dev/gnss0)  → NMEA: "$GNGGA,..." / binary UBX frames    │
│  write(/dev/gnss0) → UBX config commands (u-blox)              │
└───────────────────────┬──────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│  GNSS CORE  (core.c)                                            │
│  gnss_allocate_device() + gnss_register_device()               │
│  /dev/gnss<N>: 4096-byte FIFO, poll(), stream_open()           │
│  gnss_receive_buf() → enqueue data → wake read()               │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  TRANSPORT DRIVERS                                              │
│  serial.c  — UART/TTY-attached receivers (most common)         │
│  usb.c     — USB CDC-ACM / vendor class GPS dongles            │
│  ubx.c     — u-blox UBX binary protocol framing               │
│  sirf.c    — SiRF Star protocol                                │
│  mtk.c     — MediaTek MT3333 protocol                          │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Key API

```c
/* Driver side */
gdev = gnss_allocate_device(sizeof_priv, &my_ops, parent_dev);
gdev->type = GNSS_TYPE_NMEA;   /* or GNSS_TYPE_SIRF / GNSS_TYPE_UBX */
gnss_register_device(gdev);

/* Feed received data into the FIFO (from UART/USB rx) */
gnss_receive_buf(gdev, buf, len);   /* wakes read() */
```

---

## 4. Summary

The GNSS subsystem provides a clean `read()`-based interface to any receiver
transport, letting gpsd and other tools work identically across UART, USB,
and SPI-attached modules. The 4096-byte kernel FIFO handles burst NMEA data
without userspace needing real-time scheduling.
