# Linux Kernel: remoteproc — Remote Processor Framework

> Source: `drivers/remoteproc/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is remoteproc?

**remoteproc** manages the lifecycle of co-processors (DSP, MCU, image
signal processors) from Linux: load firmware, boot the processor, handle
crashes, and shut it down cleanly. It pairs with **rpmsg** for the
communication channel once the remote core is running.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    USERSPACE                                     │
│  /sys/class/remoteproc/remoteproc0/                             │
│    state  firmware  coredump  name  recovery                    │
│  echo "start" > state    (boot the remote core)                 │
│  echo "stop"  > state    (shut it down)                         │
│  cat coredump            (retrieve crash dump)                   │
└───────────────────────┬──────────────────────────────────────────┘
                        │ sysfs
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│           REMOTEPROC CORE  (remoteproc_core.c)                  │
│                                                                  │
│  rproc_alloc()     — allocate rproc object + private data       │
│  rproc_add()       — register with sysfs, create class device   │
│  rproc_boot()      — load firmware + boot remote core           │
│  rproc_shutdown()  — halt + unload remote core                  │
│  rproc_coredump()  — capture remote memory on crash             │
│                                                                  │
│  State machine:                                                  │
│  OFFLINE → RUNNING → CRASHED → OFFLINE (auto-recovery)          │
│  OFFLINE → ATTACHED (pre-booted by bootloader)                  │
│                                                                  │
│  Resource Table: ELF ".resource_table" section in firmware       │
│  ├─ RSC_VDEV   → create VirtIO device → rpmsg channels          │
│  ├─ RSC_CARVEOUT → map reserved DDR carveout for remote         │
│  ├─ RSC_DEVMEM  → map I/O memory into remote address space      │
│  └─ RSC_TRACE   → remote stdout ring buffer (debugfs readable)  │
└──────────────┬──────────────────────────────────────────────────┘
               │ rproc_ops callbacks
               ▼
┌──────────────────────────────────────────────────────────────────┐
│              PLATFORM DRIVERS (37 drivers)                      │
│                                                                  │
│  mtk_scp.c         — MediaTek SCP (sensor co-processor)        │
│  omap_remoteproc.c — TI OMAP DSP / IPU                         │
│  pru_rproc.c       — TI PRU (Programmable Real-time Unit)       │
│  imx_rproc.c       — NXP i.MX M4/M7 cores                      │
│  qcom_q6v5_*.c     — Qualcomm ADSP/MDSP/SLPI (Q6 DSP)         │
│  da8xx_remoteproc  — TI DA8xx DSP                               │
│  stm32_rproc.c     — STM32 Cortex-M4                           │
│  xlnx_r5_remoteproc— Xilinx R5 on Zynq/Versal                  │
└──────────────────────────────────────────────────────────────────┘
               │  boot/reset, IOMMU map, mailbox kick
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  DSP / MCU / IPU hardware  +  carveout memory (reserved DDR)    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Boot Sequence

```
 echo "start" > /sys/class/remoteproc/remoteproc0/state
       │
 rproc_boot(rproc)
       │
 1. request_firmware("myfw.elf")
       │
 2. rproc_parse_fw():
    ├─ load ELF segments into carveout / devmem regions
    ├─ parse .resource_table:
    │    RSC_VDEV  → alloc VirtIO device
    │    RSC_CARVEOUT → CMA/reserved mem
    │    RSC_TRACE → map trace buffer
       │
 3. rproc_enable_iommu()  — map carveout into IOMMU
       │
 4. ops->start(rproc)     — release reset / kick watchdog
       │
 5. Remote core boots, announces rpmsg channels via name service
       │
 6. rpmsg_device created → rpmsg driver probe() called
       │
 State: OFFLINE → RUNNING
```

---

## 4. Resource Table

```c
/* Embedded in DSP firmware ELF as .resource_table section */
struct resource_table {
    u32 ver;        /* = 1 */
    u32 num;        /* number of entries */
    u32 reserved[2];
    u32 offset[];   /* byte offsets to each entry */
};

/* Entry types: */
RSC_CARVEOUT  /* request a physically contiguous memory block */
RSC_DEVMEM    /* map an I/O region into remote address space */
RSC_TRACE     /* ring buffer for remote printk/printf */
RSC_VDEV      /* VirtIO device (→ rpmsg channels) */
RSC_VENDOR_START .. RSC_VENDOR_END  /* vendor extensions */
```

---

## 5. Crash Recovery

```
 Remote core crashes (watchdog timeout / abort)
       │
 Platform driver: ops->kick_crash() or interrupt
       │
 rproc_report_crash(rproc, RPROC_FATAL_ERROR)
       │
 ├─ rproc_coredump(): dump carveout memory
 │    → /sys/class/remoteproc/remoteproc0/coredump (devcoredump)
       │
 ├─ teardown rpmsg devices
 ├─ ops->stop(rproc)
       │
 └─ if recovery enabled: rproc_boot() again (auto-restart)
       State: RUNNING → CRASHED → OFFLINE → RUNNING
```

---

## 6. Sysfs Interface

```
/sys/class/remoteproc/remoteproc0/
  state           ← "offline" | "running" | "crashed" (r/w: start/stop)
  firmware        ← firmware filename (r/w)
  name            ← human-readable name
  coredump        ← "disabled" | "default" | "inline" (crash dump mode)
  recovery        ← "enabled" | "disabled"
/sys/kernel/debug/remoteproc/remoteproc0/
  trace0          ← remote core stdout ring buffer
  resource_table  ← parsed resource table dump
  carveout_memories
```

---

## 7. Summary

remoteproc:
1. **Firmware lifecycle** — load ELF, map memory, start/stop/recover remote
   cores cleanly from a single `echo start/stop > state` or kernel API call.
2. **Resource Table** — firmware self-describes its memory needs and VirtIO
   devices; no static configuration in the kernel driver.
3. **Crash safety** — coredump capture + optional auto-recovery make
   DSP/MCU crashes survivable without a full system reboot.
4. **Tight rpmsg integration** — RSC_VDEV entries automatically create
   VirtIO rpmsg buses, so communication channels appear as soon as the
   remote core is running.
