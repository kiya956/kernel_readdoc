# Linux Kernel: PTP (Precision Time Protocol) Clock Subsystem

> Source: `drivers/ptp/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is PTP?

**IEEE 1588 Precision Time Protocol** synchronizes clocks across a network
to sub-microsecond accuracy. Hardware-assisted timestamping (in NICs or
dedicated PTP hardware) is far more accurate than software timestamps because
it captures the exact moment a packet crosses the wire.

The Linux PTP clock subsystem exposes hardware clocks as POSIX clocks
(`/dev/ptp0`, `/dev/ptp1`, ...) and provides the kernel infrastructure for
network drivers to report hardware timestamps.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    USERSPACE                                     │
│  ptp4l (linuxptp)  phc2sys  ts2phc  testptp                     │
│  clock_gettime(CLOCK_TAI)  clock_adjtime()  ioctl(PTP_CLOCK_*)  │
└───────────────────────┬──────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│          UAPI  /dev/ptp<N>  (posix clock + char device)          │
│  PTP_CLOCK_GETCAPS    — capabilities (n_alarms, n_pins, ...)     │
│  PTP_EXTTS_REQUEST    — capture external timestamp on pin        │
│  PTP_PEROUT_REQUEST   — generate periodic output signal          │
│  PTP_SYS_OFFSET       — measure PHC vs system clock offset       │
│  PTP_SYS_OFFSET_PRECISE / _EXTENDED                             │
│  PTP_PIN_SETFUNC      — configure pin (input/output/PPS)        │
│  PTP_ENABLE_PPS       — enable PPS signal output                │
└──────────────────────┬───────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│          PTP CORE  (ptp_clock.c + ptp_chardev.c + ptp_sysfs.c)  │
│                                                                  │
│  ptp_clock_register()    — driver registers its PHC             │
│  ptp_clock_event()       — driver reports timestamp event       │
│  ptp_find_pin()          — look up pin by function/channel      │
│  ptp_vclock             — virtual PTP clock (on top of PHC)     │
│                                                                  │
│  Timestamp queue: ring buffer of struct ptp_extts_event          │
│  poll() wakes userspace when timestamps arrive                   │
└────────────────────────────────────────────────────────────────┘
           │ ptp_clock_info callbacks (adjfine, gettime64, ...)
           ▼
┌──────────────────────────────────────────────────────────────────┐
│              HARDWARE CLOCK DRIVERS                              │
│                                                                  │
│  ptp_ocp.c          — OpenCompute OCP TAP (GPS/GNSS-backed)     │
│  ptp_clockmatrix.c  — Renesas ClockMatrix (IDT)                 │
│  ptp_idt82p33.c     — IDT 82P33xxx series                       │
│  ptp_fc3.c          — Renesas FemtoClock 3                      │
│  ptp_qoriq.c        — NXP QorIQ Ethernet timer                  │
│  ptp_ines.c         — ZHAW InES PTP core (FPGA)                 │
│  ptp_kvm_*.c        — KVM virtual PTP (guest←→host)            │
│  ptp_vmclock.c      — VM clock (VMware/QEMU)                    │
│  ptp_mock.c         — Mock PHC for testing                      │
│                                                                  │
│  NIC-embedded PHCs (registered via their ethernet drivers):      │
│  igb / i210, igc, e1000e — Intel                                 │
│  mlx5, mlx4 — Mellanox/NVIDIA                                   │
│  bnxt — Broadcom                                                 │
│  ixgbe, ice — Intel 10G/25G/100G                                │
└──────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│  PPS subsystem (pps_kernel.c)  — 1 pulse-per-second signal      │
│  GNSS receiver / GPS / rubidium oscillator                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `ptp_clock.c` — Core Registration

```c
struct ptp_clock_info my_phc_ops = {
    .owner     = THIS_MODULE,
    .name      = "my_nic_phc",
    .max_adj   = 500000,      // max freq adjustment in ppb
    .n_ext_ts  = 2,           // 2 external timestamp inputs
    .n_per_out = 1,           // 1 periodic output (PPS-like)
    .n_pins    = 4,           // 4 configurable pins
    .pps       = 1,           // supports PPS output
    .adjfine   = my_adjfine,  // frequency: scaled_ppm units
    .adjtime   = my_adjtime,  // time: delta in ns
    .gettime64 = my_gettime,  // read current PHC time
    .settime64 = my_settime,  // set PHC time (coarse)
    .enable    = my_enable,   // ext_ts / per_out enable/disable
};
ptp = ptp_clock_register(&my_phc_ops, &pdev->dev);
```

**Events from driver → core:**
```c
struct ptp_clock_event evt = {
    .type      = PTP_CLOCK_EXTTS,
    .index     = 0,
    .timestamp = ktime_to_ns(hw_ts),
};
ptp_clock_event(ptp, &evt);  // enqueues in timestamp ring
```

### 3.2 `ptp_chardev.c` — ioctl Dispatch

Implements all `PTP_*` ioctls. Key ones:

| ioctl | Description |
|---|---|
| `PTP_CLOCK_GETCAPS` | Read n_alarms, n_ext_ts, n_per_out, n_pins, max_adj |
| `PTP_EXTTS_REQUEST2` | Enable/disable external timestamp capture on a pin |
| `PTP_PEROUT_REQUEST2` | Configure periodic output (start, period, phase) |
| `PTP_SYS_OFFSET_EXTENDED` | Precise system↔PHC offset measurement (3× read) |
| `PTP_PIN_SETFUNC2` | Configure pin as NONE/EXTTS/PEROUT/PPS |
| `PTP_ENABLE_PPS` | Enable PPS output |

### 3.3 `ptp_vclock.c` — Virtual PTP Clock

A virtual PHC stacked on top of a real PHC. Useful when multiple containers
or VMs each need their own PTP clock namespace while sharing one physical NIC.

### 3.4 `ptp_kvm_common.c` + `ptp_kvm_x86.c` / `ptp_kvm_arm.c`

KVM guests can use the host's PTP clock via a paravirtualized interface
(using `kvm_clock` or `arm_arch_timer`). This avoids network-based PTP
altogether for VM-to-host synchronization.

### 3.5 `ptp_ocp.c` — OpenCompute OCP TAP

Full-featured PTP appliance on PCIe card. Provides:
- GPS/GNSS receiver (time source)
- SMA connectors (external timestamps, PPS in/out)
- Rubidium oscillator option
- IRIG-B input
- Up to 4 PHCs, serial console for NMEA

### 3.6 `ptp_sysfs.c` — Sysfs Attributes

```
/sys/class/ptp/ptp0/
  clock_name          ← driver-provided name
  max_adjustment      ← max freq adj in ppb
  n_alarms, n_ext_ts, n_per_out, n_pins
  pps_available
  extts_enable        ← per-channel enable
  pins/               ← pin name, function, channel
```

---

## 4. Data Flow: Hardware Timestamping (PTP Sync)

```
 NIC hardware                  Kernel PTP core             ptp4l (userspace)
 ────────────                  ───────────────             ─────────────────
 1. PTP Sync packet arrives
    at exact hardware time T_hw
         │
 2. NIC captures T_hw in
    PHC register
         │
 3. NIC interrupt → driver
    reads T_hw from register
    ptp_clock_event(ptp, {
       .type = EXTTS,
       .timestamp = T_hw })
         │
                    4. Enqueue in
                       timestamp ring
                       wake poll()
                              │
                    5. ptp4l reads
                       via read()
                       on /dev/ptp0
                              │
                    6. Compare T_hw
                       vs received time
                       → compute offset
                              │
                    7. clock_adjtime(
                         CLOCK_TAI,
                         {.modes=ADJ_FREQUENCY,
                          .freq=delta_ppb})
                              │
                    8. PTP_ADJ_FREQ
                       ioctl →
                       adjfine(scaled_ppm)
         │
 9. NIC PHC oscillator
    frequency adjusted ◄──────── adjfine() writes hardware register
```

---

## 5. PHC vs System Clock: Offset Measurement

`PTP_SYS_OFFSET_EXTENDED` takes 3 samples in a tight loop:

```
t1 = clock_gettime(CLOCK_REALTIME)   [before PHC read]
phc = ptp_gettime64()                [PHC read]
t2 = clock_gettime(CLOCK_REALTIME)   [after PHC read]
```

Best estimate: `offset = phc - (t1 + t2) / 2`

---

## 6. Summary

The PTP clock subsystem:
1. **Exposes hardware clocks** as POSIX clocks and `/dev/ptp<N>` for
   userspace tools (ptp4l, phc2sys, ts2phc).
2. **Abstracts hardware diversity** — from simple NIC-embedded PHCs to
   full GPS-backed appliances (OCP TAP) — behind a uniform `ptp_clock_info`
   callback interface.
3. **Integrates with PPS** — 1PPS signals from GPS receivers or PHC outputs
   surface as PPS events readable by `ntpd`/`chrony`.
4. **Supports virtualization** — virtual PHCs (vclock) and KVM paravirt
   clocks let VMs achieve accurate time without physical access.
