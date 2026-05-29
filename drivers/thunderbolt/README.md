# Thunderbolt / USB4 Driver — Initialization Flow

> **Source tree:** `drivers/thunderbolt/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-05-29
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────┐
│                     Userspace                           │
│  (sysfs: /sys/bus/thunderbolt, udev, bolt daemon)       │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│               tb_bus_type  (domain.c)                   │
│         Thunderbolt bus & device model layer             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  tb_domain    │  │  tb_switch   │  │  tb_xdomain  │  │
│  │  (domain.c)   │  │  (switch.c)  │  │  (xdomain.c) │  │
│  └──────┬───────┘  └──────────────┘  └──────────────┘  │
│         │                                               │
│  ┌──────▼──────────────────────────────────────────┐    │
│  │         Connection Manager (CM)                  │    │
│  │  ┌────────────────┐    ┌─────────────────────┐  │    │
│  │  │ Software CM    │ OR │ ICM (firmware CM)    │  │    │
│  │  │ (tb.c)         │    │ (icm.c)              │  │    │
│  │  │ tb_cm_ops      │    │ icm_fr/ar/tr/icl_ops │  │    │
│  │  └────────────────┘    └─────────────────────┘  │    │
│  └─────────────────────────────────────────────────┘    │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│             Control Channel (ctl.c)                     │
│          tb_ctl — TX/RX config space packets            │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│        NHI — Native Host Interface (nhi.c)              │
│   PCI driver, ring buffers, MSI-X, DMA                  │
│   struct tb_nhi, struct tb_ring                         │
└───────────────────────┬─────────────────────────────────┘
                        │
              ┌─────────▼─────────┐
              │  PCI / Hardware    │
              │  Thunderbolt Host  │
              │  Controller        │
              └───────────────────┘
```

---

## Initialization Flow (ASCII Call Graph)

```
rootfs_initcall(nhi_init)                          ← nhi.c:1630
  │
  ├─► tb_domain_init()                             ← domain.c:874
  │     ├─► tb_debugfs_init()
  │     ├─► tb_acpi_init()
  │     ├─► tb_xdomain_init()                      (register xdomain protocol)
  │     └─► bus_register(&tb_bus_type)              (register "thunderbolt" bus)
  │
  └─► pci_register_driver(&nhi_driver)             ← nhi.c:1618
        │
        └─► nhi_probe(pdev, id)                    ← nhi.c:1382 (per PCI device)
              │
              ├─ nhi_imr_valid(pdev)               validate firmware image
              ├─ pcim_enable_device(pdev)           enable PCI device
              ├─ devm_kzalloc → struct tb_nhi      allocate NHI context
              ├─ pcim_iomap_region(pdev,0)          map MMIO BAR0
              ├─ ioread32 → nhi->hop_count          read path (hop) count
              ├─ alloc tx_rings[] / rx_rings[]      ring buffer arrays
              ├─ nhi_check_quirks(nhi)              apply HW quirks
              ├─ nhi_check_iommu(nhi)               check IOMMU presence
              ├─ nhi_reset(nhi)                     host controller reset
              ├─ nhi_init_msi(nhi)                  setup MSI-X interrupts
              ├─ dma_set_mask_and_coherent(64-bit)
              ├─ pci_set_master(pdev)
              ├─ nhi->ops->init(nhi)                (optional, e.g. icl_nhi_ops)
              │
              ├─► nhi_select_cm(nhi)               ← nhi.c:1306
              │     │
              │     ├─ if tb_acpi_is_native():
              │     │     └─► tb_probe(nhi)         ← tb.c:3436  (Software CM)
              │     │           ├─ tb_domain_alloc(nhi)
              │     │           │    ├─ kzalloc(struct tb)
              │     │           │    ├─ ida_alloc (domain index)
              │     │           │    ├─ alloc_ordered_workqueue("thunderboltN")
              │     │           │    ├─ tb_ctl_alloc(nhi)         ← control channel
              │     │           │    └─ device_initialize(&tb->dev)
              │     │           ├─ tb->cm_ops = &tb_cm_ops
              │     │           ├─ INIT tunnel_list, dp_resources
              │     │           └─ tb_acpi_add_links(nhi)
              │     │
              │     └─ else (firmware CM or pre-USB4):
              │           ├─► icm_probe(nhi)        ← icm.c:2463  (Firmware CM)
              │           │     ├─ tb_domain_alloc(nhi)
              │           │     ├─ select ops by PCI device ID
              │           │     │    (icm_fr_ops / icm_ar_ops / icm_tr_ops / ...)
              │           │     └─ tb->cm_ops = &icm_XX_ops
              │           └─ fallback: tb_probe(nhi) if icm_probe fails
              │
              ├─► tb_domain_add(tb, host_reset)    ← domain.c:435
              │     ├─ tb_ctl_start(tb->ctl)        start control channel RX/TX
              │     ├─ tb->cm_ops->driver_ready(tb)  notify CM driver is ready
              │     ├─ device_add(&tb->dev)          register domain device
              │     │
              │     └─► tb->cm_ops->start(tb, reset)
              │           │
              │           │  *** Software CM path (tb_start) — tb.c:3056 ***
              │           ├─ tb_switch_alloc(tb, 0)       alloc root switch
              │           ├─ tb_switch_configure(root)    configure root router
              │           ├─ tb_switch_add(root)          register root switch
              │           ├─ tb_switch_tmu_configure()    set TMU low-res mode
              │           ├─ tb_switch_tmu_enable()       enable TMU
              │           ├─ tb_switch_reset() (if reset && USB4v1)
              │           ├─ tb_scan_switch(root)         discover downstream
              │           ├─ tb_discover_tunnels(tb)      find boot tunnels
              │           ├─ tb_discover_dp_resources(tb)
              │           ├─ tb_create_usb3_tunnels(root) create USB3 tunnels
              │           ├─ tb_add_dp_resources(root)    add DP IN adapters
              │           ├─ tb_switch_enter_redrive(root)
              │           └─ tcm->hotplug_active = true   enable hotplug events
              │           │
              │           │  *** ICM (firmware) path (icm_start) — icm.c:2181 ***
              │           ├─ tb_switch_alloc(tb, 0)       alloc root switch
              │           ├─ icm->set_uuid(tb)            set switch UUID
              │           └─ tb_switch_add(root)          register root switch
              │
              ├─ nhi_display_notifier_register(nhi) register display hotplug notifier
              ├─ device_wakeup_enable(&pdev->dev)
              └─ pm_runtime_allow / autosuspend     enable runtime PM
```

---

## Key Data Structures

| Struct | File | Role |
|--------|------|------|
| `struct tb_nhi` | `nhi.h` | NHI PCI device context (MMIO, rings, hop_count) |
| `struct tb` | `tb.h` | Domain — holds nhi, ctl, root_switch, cm_ops, workqueue |
| `struct tb_ctl` | `ctl.h` | Control channel — TX/RX rings for config packets |
| `struct tb_ring` | `nhi.h` | Ring buffer for DMA transfers |
| `struct tb_cm_ops` | `tb.h:506` | Connection manager vtable (start/stop/suspend/hotplug…) |
| `struct tb_switch` | `tb.h` | Switch/router representation (ports, config, NVM) |
| `struct tb_port` | `tb.h` | Port on a switch (adapters: PCIe, USB3, DP) |

---

## Connection Manager Selection Logic (`nhi_select_cm`)

```
nhi_select_cm(nhi)   ← nhi.c:1306
  │
  ├── tb_acpi_is_native()?
  │     YES ──► tb_probe(nhi)       → Software CM  (USB4, kernel manages everything)
  │     NO  ──► icm_probe(nhi)      → Firmware CM   (Intel ICM, FW manages topology)
  │               └── fail? ──► tb_probe(nhi)  → fallback to Software CM
```

**Software CM** (`tb.c`): kernel controls the full topology — discovers
switches, creates tunnels (PCIe/DP/USB3), manages hotplug, TMU, CLx.

**Firmware CM / ICM** (`icm.c`): Intel Connection Manager firmware handles
topology; kernel receives events via NHI mailbox. Used on older pre-USB4
Intel platforms (Falcon Ridge, Alpine Ridge, Titan Ridge).

---

## Key Source Files

| File | Purpose |
|------|---------|
| `nhi.c` | PCI driver, probe, MSI-X, ring management, PM ops |
| `nhi_ops.c` | Per-generation NHI ops (e.g. `icl_nhi_ops`) |
| `domain.c` | `tb_domain_init/alloc/add`, bus type registration |
| `ctl.c` | Control channel — config space read/write over rings |
| `tb.c` | Software connection manager — topology, tunnels, hotplug |
| `icm.c` | Intel firmware connection manager |
| `switch.c` | Switch/router alloc, configure, add, port scanning |
| `tunnel.c` | PCIe / DP / USB3 / DMA tunnel creation & management |
| `xdomain.c` | Cross-domain (peer-to-peer) protocol |
| `tmu.c` | Time Management Unit configuration |
| `clx.c` | CL state (power saving) management |
| `usb4.c` | USB4 router operations |
| `acpi.c` | ACPI integration, _OSC native check |
