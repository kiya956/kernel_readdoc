# Linux PCI Subsystem — Kernel Driver Analysis

> Kernel: noble-linux-oem / oem-6.17-next  
> Source: `drivers/pci/`

---

## 1. Full Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                     User Space                                  │
│   lspci / setpci / sysfs (/sys/bus/pci/)                        │
│   /proc/bus/pci/  →  mmap() BAR regions                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ syscall / sysfs
┌──────────────────────────▼──────────────────────────────────────┐
│              Linux PCI Core  (drivers/pci/)                     │
│                                                                 │
│  ┌────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │  Driver    │  │  Device     │  │  Bus / Resource Mgmt     │ │
│  │  Model     │  │  Probe      │  │                          │ │
│  │pci-driver.c│  │  probe.c    │  │ bus.c  setup-bus.c       │ │
│  │            │  │  remove.c   │  │ setup-res.c  search.c    │ │
│  └────────────┘  └─────────────┘  └──────────────────────────┘ │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  ┌─────────────┐  │
│  │  Config  │  │  Power   │  │ Interrupts │  │  PCIe Svcs  │  │
│  │  Space   │  │  Mgmt    │  │            │  │             │  │
│  │ access.c │  │  pci.c   │  │ irq.c      │  │  pcie/      │  │
│  │          │  │ pci-acpi │  │ msi/       │  │  AER DPC    │  │
│  └──────────┘  └──────────┘  └────────────┘  │  ASPM PME   │  │
│                                               │  PTM portdrv│  │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  └─────────────┘  │
│  │  SR-IOV  │  │  ATS     │  │  P2P DMA   │                   │
│  │  iov.c   │  │  ats.c   │  │ p2pdma.c   │                   │
│  └──────────┘  └──────────┘  └────────────┘                   │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  ┌─────────────┐  │
│  │  VPD     │  │  DOE     │  │  VGA Arb   │  │  Hotplug    │  │
│  │  vpd.c   │  │  doe.c   │  │ vgaarb.c   │  │  hotplug/   │  │
│  └──────────┘  └──────────┘  └────────────┘  └─────────────┘  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│         Host Bridge / Platform Controllers (drivers/pci/        │
│                        controller/)                             │
│  DWC (DesignWare)  Cadence  Hyperv  Loongson  Tegra  Thunder   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                   Hardware: PCIe Fabric                         │
│                                                                 │
│  CPU ──── Root Complex ──── Switch ──── Endpoint Device        │
│                 │                            │                  │
│            (Root Port)              (NVMe / GPU / NIC ...)      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Layer-by-Layer Explanation

### 2.1 Hardware — PCIe Fabric
- **Root Complex (RC)**: CPU-side PCI host; exposes one or more **Root Ports**.  
- **Switch**: upstream + N downstream ports; allows fan-out.  
- **Endpoint**: leaf device (GPU, NVMe, NIC).  
- Communication uses **TLPs** (Transaction Layer Packets): memory read/write, I/O, config, and messages.

### 2.2 Host Bridge / Platform Controllers (`controller/`)
- Platform-specific glue that maps the CPU's address space to the PCIe config space.  
- `pci-host-generic.c` handles DT-based generic host bridges.  
- `dwc/` is the widely-used Synopsys DesignWare IP core.  
- Exposes `pci_ops` (`read` / `write`) consumed by the core layer.

### 2.3 PCI Core — Config Space (`access.c`, `ecam.c`)
- **ECAM** (Enhanced Config Access Mechanism): memory-mapped 4 KB config window per device (PCIe r6.0 §7.2.2).  
- `pci_read_config_*()` / `pci_write_config_*()` are the safe wrappers.  
- Bus locking prevents torn reads on multi-byte accesses.

### 2.4 Device Enumeration (`probe.c`, `bus.c`)
| Step | Function | What happens |
|------|----------|--------------|
| 1 | `pci_scan_root_bus_bridge()` | Register host bridge, allocate `pci_bus` for bus 0 |
| 2 | `pci_scan_bus()` | Walk slots 0–31; read Vendor/Device ID |
| 3 | `pci_setup_device()` | Parse BARs, capabilities, class code |
| 4 | `pci_assign_resource()` | Allocate MMIO / IO port windows |
| 5 | `pci_bus_add_devices()` | Publish to sysfs, trigger driver binding |

### 2.5 Driver Model (`pci-driver.c`)
- `pci_register_driver()` registers `struct pci_driver` with `pci_bus_type`.  
- Matching via `struct pci_device_id[]` (vendor/device/class tuples).  
- `probe()` / `remove()` / `shutdown()` callbacks.  
- Dynamic IDs: `pci_add_dynid()` — allows `new_id` sysfs binding at runtime.

### 2.6 Power Management (`pci.c`, `pci-acpi.c`)
- **D-states**: D0 (full on) → D1 → D2 → D3hot → D3cold.  
- `pci_set_power_state()` issues PCI PM capability writes.  
- ACPI `_PS0`–`_PS3` methods invoked through `pci-acpi.c`.  
- Runtime PM: `pm_runtime_get()` / `pm_runtime_put()` trigger D3 entry on idle.

### 2.7 Interrupts (`irq.c`, `msi/`)
| Mechanism | Kernel object | Notes |
|-----------|--------------|-------|
| Legacy INTx | virtual IRQ via `pci_intx()` | shared, slow |
| MSI | `msi_alloc_desc()` | 1–32 vectors per device |
| MSI-X | `msi_alloc_desc()` | up to 2048 vectors, per-vector mask |
- `pci_alloc_irq_vectors()` is the modern unified API.

### 2.8 PCIe Services (`pcie/`)
| File | Feature |
|------|---------|
| `aer.c` | Advanced Error Reporting — correctable/uncorrectable |
| `dpc.c` | Downstream Port Containment — isolates failed ports |
| `aspm.c` | Active State Power Management — L0s / L1 / L1.x substates |
| `pme.c` | Power Management Events — wake signalling |
| `portdrv.c` | PCIe Port Service driver framework |
| `ptm.c` | Precision Time Measurement — hardware timestamping |
| `rcec.c` | Root Complex Event Collector |

### 2.9 SR-IOV (`iov.c`)
- Single-Root I/O Virtualization: one Physical Function (PF) spawns up to 256 Virtual Functions (VFs).  
- `pci_enable_sriov(dev, numvfs)` → enumerates VF BARs on the bus.  
- Used heavily by NIC/NVMe for VM passthrough.

### 2.10 ATS / IOMMU (`ats.c`)
- Address Translation Services: device asks IOMMU for IOVA → PA translation, caches in on-device TLB.  
- `pci_enable_ats()` / `pci_disable_ats()`.

### 2.11 P2P DMA (`p2pdma.c`)
- Allows two PCIe endpoints to DMA directly without CPU/memory involvement (e.g., GPU → NVMe).  
- `pci_p2pdma_distance()` checks topology; `pci_alloc_p2pmem()` allocates from BAR.

### 2.12 Hotplug (`hotplug/`)
- ACPI-based: `acpiphp_core.c` handles _EJ0 / _PS0 ACPI methods.  
- Native PCIe: `pciehp` driver in `hotplug/pciehp*`.  
- Slot presence detect → `pci_scan_slot()` → driver bind / unbind.

### 2.13 VGA Arbitration (`vgaarb.c`)
- Legacy ISA VGA resource conflict arbitration among multiple GPUs.  
- Kernel arbitrates 0xA0000 frame buffer and I/O port 0x3b0–0x3df.

---

## 3. Data-Flow Diagram — Device Enumeration

```
Firmware (ACPI/DT)
       │
       │ pci_acpi_scan_root()
       ▼
pci_register_host_bridge()
       │
       │ pci_scan_root_bus()
       ▼
  pci_scan_bus(bus 0)
  ┌────────────────────────────────────────────────────┐
  │ for each slot 0..31                                │
  │   pci_scan_slot()                                  │
  │     read VendorID → FF FF? skip                    │
  │     pci_setup_device()                             │
  │       parse BAR, capabilities, subsys ID          │
  │     if bridge: pci_scan_bridge()                  │
  │       recurse into secondary bus                  │
  └────────────────────────────────────────────────────┘
       │
       │ pci_assign_unassigned_root_bus_resources()
       │   walks resource tree, assigns MMIO windows
       ▼
  pci_bus_add_devices()
       │
       ├─► kobject_uevent(KOBJ_ADD) → udev
       └─► device_attach() → pci_bus_match() → driver probe()
```

---

## 4. Data-Flow Diagram — MSI Interrupt Setup

```
Driver calls pci_alloc_irq_vectors(dev, 1, 32, PCI_IRQ_MSI)
       │
       ▼
msi_capability_init()
  │  Write MSI Capability: enable bit + message count
  │  Allocate irq_desc via irq_domain
  │  Write Message Address (APIC address) + Data (vector)
  ▼
Device sends MSI write on PCIe fabric
       │
       ▼
LAPIC (or MSI controller) triggers CPU vector
       │
       ▼
handle_edge_irq() → driver ISR (irq_handler_t)
```

---

## 5. Key Data Structures

```c
struct pci_dev {         // one per physical function
    struct pci_bus *bus;
    u16 vendor, device;
    u8  revision, class[3];
    struct resource resource[DEVICE_COUNT_RESOURCE]; // BARs
    struct pci_driver *driver;
    // power, msi, ats, sriov, aer ...
};

struct pci_bus {         // one per PCI bus number
    struct pci_dev *self;   // bridge device
    struct list_head devices;
    struct pci_ops *ops;    // config space r/w
};

struct pci_driver {      // registered by device drivers
    const struct pci_device_id *id_table;
    int  (*probe)(struct pci_dev *, const struct pci_device_id *);
    void (*remove)(struct pci_dev *);
    int  (*suspend)(struct pci_dev *, pm_message_t);
    int  (*resume)(struct pci_dev *);
};
```

---

## 6. Important Source Files

| File | Role |
|------|------|
| `pci.c` | Core helpers, PM, PME poll |
| `probe.c` | Bus scan, device setup, BAR sizing |
| `pci-driver.c` | Driver registration, probe/remove, dynid |
| `bus.c` | Bus resource list management |
| `access.c` | Config space read/write primitives |
| `setup-bus.c` | Resource assignment across bridges |
| `irq.c` | INTx routing, legacy IRQ |
| `msi/msi.c` | MSI/MSI-X allocation and teardown |
| `pcie/aer.c` | AER error handler |
| `pcie/aspm.c` | Link state power management |
| `iov.c` | SR-IOV VF enumeration |
| `hotplug/pciehp_core.c` | Native PCIe hotplug |
| `quirks.c` | Per-device errata workarounds (~5000 lines) |

---

## 7. bpftrace / Python Test Case

See [`test_pci_workflow.py`](test_pci_workflow.py) in this directory.

The test attaches bpftrace probes to verify the enumeration, driver bind,
config-space access, power state, and MSI allocation flows.
