# Linux ACPI Subsystem — Kernel Driver Analysis

> Kernel: noble-linux-oem / oem-6.17-next  
> Source: `drivers/acpi/`

---

## 1. Full Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                      User Space                                  │
│  acpi_call  /  acpidump  /  acpidbg  /  /sys/class/thermal/     │
│  /proc/acpi/  /  /sys/firmware/acpi/                            │
└─────────────────────────┬────────────────────────────────────────┘
                          │ sysfs / procfs / netlink
┌─────────────────────────▼────────────────────────────────────────┐
│               ACPI Linux Driver Layer  (drivers/acpi/)           │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │  Bus/Scan    │  │  Driver      │  │  Platform Devices    │   │
│  │  bus.c       │  │  Model       │  │                      │   │
│  │  scan.c      │  │  device_pm.c │  │  battery.c  ac.c     │   │
│  │  device_sysfs│  │              │  │  button.c   dock.c   │   │
│  └──────────────┘  └──────────────┘  │  thermal.c  video.c  │   │
│                                       │  fan.c  processor_*  │   │
│  ┌──────────────┐  ┌──────────────┐  └──────────────────────┘   │
│  │  EC Driver   │  │  APEI        │                              │
│  │  ec.c        │  │  (errors)    │  ┌──────────────────────┐   │
│  │  ec_sys.c    │  │  apei/       │  │  Sleep / Wakeup      │   │
│  └──────────────┘  └──────────────┘  │  sleep.c  wakeup.c   │   │
│                                       └──────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │   ACPICA  — ACPI Component Architecture  (acpica/)       │    │
│  │   Intel open-source ACPI interpreter (AML bytecode)      │    │
│  │   Namespace, Interpreter, Tables, Hardware, Events       │    │
│  └──────────────────────────────────────────────────────────┘    │
└─────────────────────────┬────────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────────┐
│         Platform Firmware Interface                              │
│                                                                  │
│  BIOS/UEFI  ─►  ACPI Tables (DSDT / SSDT / FADT / MADT / ...)  │
│                   stored in physical memory, mapped at boot      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. ACPI Table Hierarchy

```
RSDP ──► RSDT / XSDT
              │
    ┌─────────┼──────────────────────────────────────┐
    │         │                                      │
   FADT      MADT      DSDT ──► SSDT (multiple)    SRAT MCFG BGRT …
   (fixed)  (IRQ/   (Differentiated                (NUMA) (PCIe)
             APIC)   System Desc Table)
              │          │
           CPU,SCI    AML bytecode
           NMI,IRQ    (hardware description
                       + control methods)
```

| Table | Purpose |
|-------|---------|
| FADT | Fixed registers (SCI IRQ, PM1, GPE blocks), sleep/wake ports |
| MADT | APIC / CPU topology |
| DSDT | Main hardware description + _STA _CRS _PRS _INI methods |
| SSDT | Supplementary tables (injected by BIOS or kernel SSDT override) |
| SRAT | NUMA memory affinity |
| MCFG | PCIe ECAM base address |
| BGRT | Boot splash logo |
| APEI tables | BERT / ERST / HEST (hardware error sources) |

---

## 3. Layer-by-Layer Explanation

### 3.1 ACPICA (`acpica/`)
- Intel's reference implementation of the ACPI specification; ~100 KLOC.  
- Parses AML bytecode (ACPI Machine Language) at boot.  
- Builds the **ACPI Namespace** — a tree of objects (devices, methods, regions).  
- Exposes `acpi_evaluate_object()`, `acpi_get_handle()`, `acpi_walk_namespace()` to the rest of the kernel.

### 3.2 ACPI Namespace Scan (`scan.c`)
- `acpi_scan_init()` walks the namespace and creates `acpi_device` for each hardware node.  
- Calls `_STA` (device present?), `_HID` / `_CID` (hardware ID — e.g., `PNP0C0A` = battery).  
- Publishes `acpi_device` to `acpi_bus_type` → driver binding.

### 3.3 ACPI Bus Driver Model (`bus.c`, `device_pm.c`)
- `acpi_bus_register_driver()` wraps kernel driver model.  
- Match by HID/CID strings.  
- Power states: `acpi_device_set_power()` calls `_PS0`–`_PS3`, `_PR0`–`_PR3`.

### 3.4 Embedded Controller (`ec.c`)
- EC is a microcontroller on laptops (handles keyboard, fans, LEDs, battery gauging).  
- Communicates via ACPI EC protocol: I/O ports 0x62 / 0x66 + interrupt (SCI).  
- Provides `acpi_ec_read()` / `acpi_ec_write()` for other drivers.  
- EC-SCI events → ACPI event handler → battery/thermal/button notifications.

### 3.5 ACPI SCI and GPE (`event.c`, `evgpe.c` in ACPICA)
- **SCI** (System Control Interrupt): shared level-triggered IRQ for all ACPI events.  
- **GPE** (General Purpose Event): bitmap register; each bit triggers an AML `_Lxx` / `_Exx` method.  
- GPE 0x17 might mean "lid close" → `_L17()` AML → notify button driver → logind.

### 3.6 Battery (`battery.c`)
- HID `PNP0C0A`.  
- Polls `_BST` (Battery Status: state, remaining capacity, rate, voltage).  
- Reads `_BIF` / `_BIX` (Battery Info: design capacity, technology, OEM name).  
- Exposes `/sys/class/power_supply/BAT0/`.

### 3.7 Thermal (`thermal.c`)
- HID `PNP0C0B` (fan), thermal zone `ThermalZone`.  
- Evaluates `_TMP` (current temperature), `_PSV` (passive trip), `_CRT` (critical).  
- Interfaces with `drivers/thermal/` framework (thermal_zone_device).

### 3.8 Processor / CPPC (`processor_*.c`, `cppc_acpi.c`)
- `acpi_processor_init()` → one `acpi_processor` per CPU.  
- **P-states** (frequency/voltage): `_PCT` / `_PSS` / `_PPC`.  
- **C-states** (idle): `_CST` — passed to `cpuidle` driver.  
- **CPPC** (Collaborative Processor Performance Control): ACPI 6.x mechanism for firmware-guided DVFS; used in modern AMD/Intel platforms.

### 3.9 Sleep / Wakeup (`sleep.c`, `wakeup.c`)
| S-state | Meaning | Resume trigger |
|---------|---------|----------------|
| S0 (s2idle) | CPU idle, fabric on | RTC / USB / NIC |
| S3 (suspend-to-RAM) | Power off except RAM | Any wake source |
| S4 (hibernate) | RAM to disk, power off | Power button |
| S5 (soft off) | Powered off | Power button |
- `acpi_suspend_enter()` → write SLP_TYP to PM1_CNT → firmware takes over.  
- On resume: firmware runs `_WAK(Sx)` AML → kernel `acpi_pm_finish()`.

### 3.10 APEI (`apei/`)
- Advanced Platform Error Interface: collects hardware errors (CPU, memory, PCIe).  
- GHES (Generic Hardware Error Source) driver: reads CPER records from firmware-shared buffer.  
- Feeds into Linux EDAC / MCE frameworks.

---

## 4. Data-Flow Diagram — Boot Enumeration

```
kernel start_kernel()
       │
       ▼
acpi_init()
  │  acpi_load_tables()     → map RSDP, parse RSDT/XSDT, load DSDT+SSDTs
  │  acpi_enable()          → write ACPI_ENABLE to SMI_CMD port
  │  acpi_bus_init()
  │    acpi_scan_init()
  │      acpi_walk_namespace(ACPI_TYPE_DEVICE, ...)
  │        for each namespace node:
  │          _STA → enabled?
  │          _HID / _CID → hardware ID
  │          acpi_device_add() → kobject_uevent → driver bind
  │    acpi_pci_root_init()  → discover PCI host bridges
  │    acpi_processor_init() → enumerate CPUs
  └─► ACPI namespace fully scanned; platform devices registered
```

---

## 5. Data-Flow Diagram — SCI Interrupt Path

```
Hardware event (e.g., battery level change)
       │
       ▼
SCI IRQ fires (level-triggered, shared)
       │
       ▼
acpi_irq() → acpi_ev_sci_xrupt_handler()
  │  read PM1_STS: power button? sleep? wake?
  │  read GPE Status: which GPE fired?
  │  clear GPE status
  │  schedule GPE dispatch workqueue
  ▼
acpi_ev_asynch_execute_gpe_method()
  │  evaluate _Lxx() or _Exx() AML
  │  AML calls Notify(device, 0x80)
  ▼
acpi_bus_notify() → device driver notify callback
  │  battery driver → reads _BST → updates power_supply
  │  thermal driver → reads _TMP → updates thermal zone
  └─► user space: udev event / uevent
```

---

## 6. Key Data Structures

```c
struct acpi_device {         // one per namespace object
    acpi_handle handle;      // ACPICA handle
    struct acpi_device_id_list pnp;  // HID + CIDs
    struct acpi_device_power power;  // D-states
    struct acpi_device_wakeup wakeup;
    struct device dev;       // linked into driver model
};

struct acpi_driver {         // ACPI device driver
    const struct acpi_device_id *ids;   // HID match table
    struct acpi_device_ops ops;
      // .add / .remove / .notify / .suspend / .resume
};

// EC transaction
struct transaction {
    const u8 *wdata;   // bytes to write
    u8 *rdata;         // bytes to read
    u8  command;
};
```

---

## 7. Important Source Files

| File | Role |
|------|------|
| `bus.c` | ACPI bus init, SCI init, event dispatch |
| `scan.c` | Namespace walk, acpi_device creation |
| `ec.c` | Embedded Controller driver |
| `battery.c` | Battery status / info (`PNP0C0A`) |
| `thermal.c` | Thermal zone driver |
| `sleep.c` | S-state suspend/resume |
| `processor_driver.c` | ACPI processor driver |
| `cppc_acpi.c` | CPPC performance control |
| `acpica/` | ACPICA interpreter (tables, namespace, AML) |
| `apei/ghes.c` | Generic Hardware Error Source |
| `device_pm.c` | ACPI device power state transitions |
| `pci_root.c` | PCI host bridge discovery |

---

## 8. bpftrace / Python Test Case

See [`test_acpi_workflow.py`](test_acpi_workflow.py) in this directory.
