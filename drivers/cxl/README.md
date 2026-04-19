# Linux Kernel: CXL (Compute Express Link) Subsystem

> Source: `drivers/cxl/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is CXL?

**Compute Express Link** is a high-speed, cache-coherent interconnect built on
top of PCIe 5.0+ physical layer. It lets CPUs and accelerators share memory
with hardware-enforced coherence, removing the need for explicit software
synchronization of shared data.

Three sub-protocols share the same wire:

| Protocol | Purpose |
|---|---|
| **CXL.io** | PCIe-compatible I/O transactions (config space, MMIO, DMA) |
| **CXL.cache** | Accelerator caches host memory; CPU snoops device cache |
| **CXL.mem** | CPU directly accesses device-attached memory (DRAM on card) |

Three device types:

| Type | Protocols | Use case |
|---|---|---|
| **Type 1** | CXL.io + CXL.cache | Cache-coherent accelerator, no local memory |
| **Type 2** | CXL.io + CXL.cache + CXL.mem | GPU/FPGA with local memory |
| **Type 3** | CXL.io + CXL.mem | Memory expander (DRAM/NVM add-in card) |

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                         USERSPACE                                │
│  ndctl / cxl-cli  (manage regions, memdevs, firmware, events)   │
│  numactl / memkind  (NUMA-aware allocation on CXL memory)        │
└──────────────────┬───────────────────────────────────────────────┘
                   │ ioctl / sysfs / devdax
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                    UAPI / char-dev layer                         │
│  /dev/cxl/mem<N>  → CXL_MEM_SEND_COMMAND (mailbox pass-through) │
│  /sys/bus/cxl/    → region/decoder/port attributes              │
│  /sys/bus/nd/     → nvdimm bridge (PMEM regions)                │
└──────┬────────────────────────────┬───────────────────────────────┘
       │                            │
       ▼                            ▼
┌──────────────┐          ┌──────────────────────────┐
│ cxl_mem.ko   │          │  libnvdimm / nd_region   │
│ (mem.c)      │          │  (PMEM namespace → block │
│ Type-3 memdev│          │   dev or DAX device)     │
│ char-dev ops │          └──────────────────────────┘
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│                    CXL CORE  (core/)                             │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐           │
│  │ port.c      │  │ region.c     │  │ memdev.c      │           │
│  │ Port/decoder│  │ Region create│  │ cxl_memdev    │           │
│  │ tree mgmt   │  │ & interleave │  │ lifecycle     │           │
│  └─────────────┘  └──────────────┘  └───────────────┘           │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐           │
│  │ mbox.c      │  │ hdm.c        │  │ regs.c        │           │
│  │ Mailbox cmd │  │ HDM decoder  │  │ MMIO register │           │
│  │ interface   │  │ SPA→DPA map  │  │ discovery     │           │
│  └─────────────┘  └──────────────┘  └───────────────┘           │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐           │
│  │ pmu.c       │  │ edac.c/ras.c │  │ features.c    │           │
│  │ Perf monitor│  │ ECC/poison   │  │ CXL 3.0+      │           │
│  │ unit        │  │ reporting    │  │ device features│          │
│  └─────────────┘  └──────────────┘  └───────────────┘           │
└──────┬───────────────────────────────────────────┬───────────────┘
       │                                           │
       ▼                                           ▼
┌──────────────┐                        ┌──────────────────────┐
│ cxl_pci.ko   │                        │ cxl_acpi.ko          │
│ (pci.c)      │                        │ (acpi.c)             │
│ PCI endpoint │                        │ CEDT/CFMWS topology  │
│ driver       │                        │ ACPI-based root port │
└──────┬───────┘                        └──────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│          PCIe 5.0 / CXL Hardware                                 │
│  Host Bridge → CXL Switch → CXL Memory Device                   │
│  (HDM Decoders at each level)                                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `acpi.c` — Topology Discovery

Parses ACPI **CEDT** (CXL Early Discovery Table):
- **CHBS** (CXL Host Bridge Structure) — enumerates host bridges
- **CFMWS** (CXL Fixed Memory Window Structure) — static SPA windows
- **CXIMS** — XOR interleave maps for address translation

Builds the root CXL port and registers `cxl_root_decoder` objects for each
CFMWS window, including the HPA→SPA translation function.

### 3.2 `pci.c` — PCIe Endpoint Driver

Binds to CXL-capable PCIe devices. Responsibilities:
- Discover **DVSEC** (Designated Vendor-Specific Extended Capability) ranges
- Map CXL component registers and device registers via `regs.c`
- Enable CXL protocol on the link (CXL 1.1 vs 2.0 vs 3.0 negotiation)
- Set up mailbox and register the device with CXL core

### 3.3 `mem.c` — Memory Device (Type-3)

Registers `/dev/cxl/mem<N>`. Handles:
- `CXL_MEM_SEND_COMMAND` ioctl — pass raw mailbox commands to device
- Memory info (capacity, partition, health)
- Firmware update, security commands

### 3.4 `core/mbox.c` — Mailbox Interface

CXL devices expose a **Mailbox** register set for management commands.
Key operations (`enum cxl_opcode`):

| Opcode | Command |
|---|---|
| `0x0100` | GET_EVENT_RECORD (RAS events) |
| `0x0400` | GET_SUPPORTED_LOGS |
| `0x4000` | IDENTIFY (device info) |
| `0x4100` | GET_PARTITION_INFO |
| `0x4200` | GET_LSA / SET_LSA (label storage) |
| `0x4300` | GET_HEALTH_INFO |
| `0x4600` | GET_POISON_LIST |

### 3.5 `core/hdm.c` — HDM Decoders

**Host-managed Device Memory** decoders map a System Physical Address (SPA)
window to Device Physical Address (DPA) space. Each port in the hierarchy has
N decoders, programmed from root → switch → endpoint.

Address translation for interleaved systems:
```
SPA (system physical address)
  │  interleave_ways, interleave_granularity
  ▼
HPA (host-bridge physical address)
  │  XOR map (CXIMS from ACPI)
  ▼
DPA (device physical address)
```

### 3.6 `core/region.c` — Memory Regions

A **region** is the kernel's view of a contiguous SPA window mapped to one
or more CXL memory devices. Regions can be:
- **PMEM** — persistent memory (backed by nvdimm namespace)
- **RAM** — volatile memory added to system NUMA node

Region creation flow:
1. Userspace (cxl-cli) writes `create-region` sysfs
2. Kernel walks port tree, programs HDM decoders root → endpoint
3. On success: nvdimm bridge or hotplug memory added to NUMA node

### 3.7 `core/edac.c` + `core/ras.c` — Error Handling

CXL devices report errors via:
- **AER** (Advanced Error Reporting) — PCIe AER interrupt → `trace_cxl_aer_*`
- **Event Records** — polled from mailbox or interrupt-driven
  - General Media Event (`trace_cxl_general_media`)
  - DRAM Event (`trace_cxl_dram`)
  - Memory Module Event (`trace_cxl_memory_module`)
  - Poison List (`trace_cxl_poison`)

### 3.8 `core/pmu.c` — Performance Monitoring

CXL PMU (CXL 3.0+): Exposes counters for bandwidth, latency, and utilization
of CXL links. Registers as a `perf_pmu` — accessible via `perf stat`.

### 3.9 `port.c` — Port Driver

Binds to `cxl_port` devices. Manages upstream/downstream port connections,
dport enumeration, and endpoint path tracing (used by region programming).

### 3.10 `security.c` — Media Security

Wraps nvdimm security ops:
- Passphrase set/change/disable
- Secure erase / sanitize
- Replay-protected memory block (RPMB) management

---

## 4. Key Data Structures

```c
struct cxl_port {
    struct device dev;
    struct device *uport_dev;    // PCIe host bridge or switch upstream
    struct device *host_bridge;  // ACPI host bridge
    int id;
    struct list_head dports;     // downstream ports
    struct list_head endpoints;  // attached memdevs
    struct list_head regions;    // active regions through this port
    struct cxl_dport *parent_dport;
    int nr_dports;
    int depth;                   // distance from root
    struct xarray decoder_ida;
    struct cxl_regs regs;        // MMIO register map
};

struct cxl_decoder {
    struct device dev;
    int id;
    struct range hpa_range;      // system physical address window
    int interleave_ways;         // 1/2/4/8/16
    int interleave_granularity;  // 256B/512B/1KB/...
    enum cxl_decoder_type target_type;   // RAM or PMEM
    struct cxl_region *region;
    int (*commit)(struct cxl_decoder *cxld);
    void (*reset)(struct cxl_decoder *cxld);
};

struct cxl_memdev {
    struct device dev;
    struct cdev cdev;            // /dev/cxl/mem<N>
    struct cxl_dev_state *cxlds; // device state (mailbox, capacity, ...)
    struct cxl_port *endpoint;   // leaf port in port tree
    int id;
    int depth;
};

struct cxl_region {
    struct device dev;
    int id;
    enum cxl_decoder_type type;  // RAM or PMEM
    struct cxl_region_params params; // SPA, interleave, targets[]
};
```

---

## 5. Data Flow: Region Creation → CPU Memory Access

```
 cxl-cli (userspace)             Kernel CXL core
 ───────────────────             ────────────────
 1. cxl create-region
    write region attrs
    to sysfs                  2. cxl_region_attach()
         │                         │ walk port tree root → endpoint
         │                         │ program HDM decoders:
         │                         │   root decoder: CFMWS window
         │                         │   switch decoder: subset
         │                         │   endpoint decoder: DPA range
         │                    3. decoders committed (MMIO write)
         │
         │                    4a. PMEM: nvdimm bridge created
         │                        → libnvdimm namespace → /dev/pmem0
         │
         │                    4b. RAM: memory_hotplug_add_range()
         │                        → new NUMA node or extra capacity
         │
 5. CPU accesses address in
    SPA window  ─────────────► CXL hardware translates SPA→DPA
                                sends CXL.mem read to device DRAM
                                data returns over PCIe 5.0 (~64 GB/s)
```

---

## 6. Trace Events

| Tracepoint | Fires when |
|---|---|
| `cxl:cxl_aer_uncorrectable_error` | AER uncorrectable error on CXL port |
| `cxl:cxl_aer_correctable_error` | AER correctable error |
| `cxl:cxl_overflow` | CXL event log overflow |
| `cxl:cxl_generic_event` | Generic device event record |
| `cxl:cxl_general_media` | Media error (ECC, scrub) |
| `cxl:cxl_dram` | DRAM event (row failure, etc.) |
| `cxl:cxl_memory_module` | Memory module event |
| `cxl:cxl_poison` | Poison record (corrupt address) |

---

## 7. Sysfs Layout

```
/sys/bus/cxl/
  devices/
    root0/                  ← cxl_root (from ACPI CEDT)
      decoder0.0/           ← root decoder (CFMWS window)
        region0/            ← region programmed by user
          target0/          ← endpoint decoder
    port1/                  ← CXL switch port
      port2/                ← downstream switch port
        endpoint3/          ← device endpoint port
          decoder3.0/
    mem0/                   ← Type-3 memory device
      firmware_version
      numa_node
      size
/dev/cxl/mem0               ← ioctl interface (mailbox commands)
```

---

## 8. Summary

The CXL subsystem in Linux provides:

1. **Topology discovery** via ACPI CEDT — root decoders, host bridges, and
   interleave windows established at boot.
2. **Port/decoder tree** — mirrors the hardware hierarchy (root → switches →
   endpoint), with each level owning HDM decoders that translate addresses.
3. **Region management** — userspace-driven workflow that programs the full
   decoder chain and presents the resulting memory as PMEM namespace or
   hot-plugged DRAM.
4. **Error handling & observability** — AER, event records, poison tracking,
   and PMU counters all surface through standard kernel interfaces (perf,
   tracing, EDAC).
