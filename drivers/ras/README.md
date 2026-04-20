# Linux Kernel RAS (Reliability, Availability, Serviceability) Subsystem

## Overview

The **RAS** subsystem provides a unified framework for detecting, collecting,
classifying, and reporting hardware errors from:

- **x86 MCE** (Machine Check Exception) — CPU and memory controller errors
- **ACPI CPER** (Common Platform Error Record) — UEFI firmware-reported errors
- **ARM hardware errors** — ARM processor error architecture
- **PCIe AER** (Advanced Error Reporting)
- **Memory failures** — physical page poisoning

The subsystem's core deliverable is a set of **tracepoints** that feed error
events to userspace tools (`mcelog`, `rasdaemon`, `edac-util`) without those
tools needing to poll hardware registers directly.

Source: `drivers/ras/`

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Userspace tools                                   │
│                                                                      │
│  rasdaemon   mcelog   edac-util   cper_decode                        │
│  (subscribe to tracepoints via perf/tracefs)                         │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ tracefs: /sys/kernel/tracing/events/ras/
┌──────────────────────────▼──────────────────────────────────────────┐
│             RAS Tracepoints (include/ras/ras_event.h)                │
│                                                                      │
│  mc_event          — Memory controller correctable/uncorrectable err │
│  arm_event         — ARM processor hardware error                    │
│  non_standard_event— Non-standard CPER section                      │
│  aer_event         — PCIe AER correctable/uncorrectable error        │
│  memory_failure_event — page poison / HWPoison event                │
│  extlog_mem_event  — Extended error log from BIOS (Intel)            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────────┐
│                   RAS Core (ras.c)                                   │
│                                                                      │
│  log_non_standard_event()  → trace_non_standard_event()             │
│  log_arm_hw_error()        → trace_arm_event()                      │
│  amd_atl_register_decoder() / amd_convert_umc_mca_addr_to_sys_addr()│
│  ras_debugfs_init()        → /sys/kernel/debug/ras/                  │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
       ┌───────────────────┼────────────────────┐
       ▼                   ▼                    ▼
┌────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐
│  CEC (cec.c)   │ │  AMD ATL         │ │  AMD FMPM (fmpm.c)        │
│                │ │  (amd/atl/)      │ │                          │
│ Correctable    │ │  Address Trans-  │ │  Firmware Memory Poison  │
│ Error Collector│ │  lation Library  │ │  Manager                 │
│                │ │  UMC → SPA       │ │                          │
│ Tracks CE count│ │  (Zen4+)         │ │  AMD CMCI / NVDIMM       │
│ per PFN page   │ │                  │ │  bad pages tracking      │
│ → HWPoison     │ │                  │ │                          │
└────────────────┘ └──────────────────┘ └──────────────────────────┘
       │                   │
       ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│           Hardware Error Sources                                     │
│                                                                      │
│  x86 MCE       ACPI/CPER      ARM hardware err    PCIe AER          │
│  (arch/x86/)   (drivers/acpi/)(arch/arm64/)       (drivers/pci/)    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. RAS Tracepoints (`include/ras/ras_event.h`)

The tracepoints are the primary API. Hardware error handlers call these
directly so rasdaemon (or any perf subscriber) can log them.

| Tracepoint | Source | Fires When |
|------------|--------|-----------|
| `ras:mc_event` | EDAC, MCE | Memory controller CE/UCE reported |
| `ras:arm_event` | arch/arm64 | ARM CPU hardware error interrupt |
| `ras:non_standard_event` | ACPI GHES | Unknown CPER section type received |
| `ras:aer_event` | PCIe AER | PCIe correctable/uncorrectable error |
| `ras:memory_failure_event` | mm/memory-failure | Page poisoned / HWPoison kill |
| `ras:extlog_mem_event` | Intel BIOS extlog | BIOS extended error log entry |

### 2. CEC — Correctable Error Collector (`cec.c`)

**Problem:** A single correctable ECC error (CE) on a DRAM page is normal
and handled by hardware. But *repeated* CEs on the same physical page indicate
a failing DIMM — and the page should be retired before it uncorrects.

**CEC solution:** Maintain a sorted array (fits in one page, 512 × 8-byte
entries) mapping PFN → error count:

```
Entry layout: [63..12: PFN | 11..10: generation | 9..0: count]
```

- On each CE: `cec_add_elem(pfn)` — insert or increment count
- When count reaches `action_threshold` (default: 1023):
  `memory_failure(pfn)` — poison the page, SIGBUS any users
- **Spring cleaning**: periodically decay all generation bits to evict stale
  entries (LRU-like behavior in O(page_size) time)

CEC is triggered by MCE correctable errors (`mce_notifier` → `cec_add_elem`).
Controlled via `/sys/kernel/debug/ras/cec/`:
- `action_threshold` — CEs before poisoning (default: max)
- `pfns` — dump the current array

### 3. AMD ATL — Address Translation Library (`amd/atl/`)

On AMD Zen4+ systems, memory errors are reported in **UMC normalized address**
(UMC-NA: the address as seen by the Unified Memory Controller), not in
**System Physical Address** (SPA) space. To call `memory_failure(pfn)`, the
kernel needs the SPA.

AMD ATL translates: UMC-NA → SPA via:
1. **System topology** (`system.c`): reads DF (Data Fabric) configuration
   (DRAM base/limit, interleave rules) from MSRs/PCI config
2. **Denormalization** (`denormalize.c`): un-normalizes the UMC address
   through the DF's DRAM interleave map
3. **Dehashing** (`dehash.c`): reverses address hashing used by AMD's
   interleave algorithms
4. **UMC→SPA** (`umc.c`): final translation to physical address

Registers itself via `amd_atl_register_decoder()` so EDAC/MCE code can call
`amd_convert_umc_mca_addr_to_sys_addr()`.

### 4. AMD FMPM — Firmware Memory Poison Manager (`amd/fmpm.c`)

On AMD systems with CMCI (Corrected Machine Check Interrupt) and NVDIMM
persistent memory, firmware may report poison ranges via ACPI interfaces.
FMPM:
- Reads the firmware's bad-memory list (FW_ATTRIB_MEMLIST ACPI method)
- Calls `memory_failure()` on each poisoned PFN at boot
- Updates the firmware list when new pages are poisoned at runtime

---

## Error Flow Diagram (x86 MCE)

```
Hardware detects DRAM ECC correctable error
          │
          ▼
  CPU MCE interrupt / CMCI interrupt
  (arch/x86/kernel/mce/core.c)
          │
          ├─ severity: corrected
          │    ├─ call cec_add_elem(pfn)     ← CEC tracking
          │    └─ trace_mc_event(...)        ← rasdaemon logs it
          │
          └─ severity: uncorrected
               ├─ memory_failure(pfn)        ← page poisoned
               ├─ trace_mc_event(...)
               └─ panic() if RIPV=0 (no recovery possible)
```

---

## Error Flow Diagram (ACPI GHES / CPER)

```
UEFI firmware detects hardware error
          │
          ▼
  GHES (Generic Hardware Error Source)
  ACPI notification → ghes_notify_hed()
          │
          ├─ CPER section type = memory?
          │    └─ trace_mc_event()
          │
          ├─ CPER section type = PCIe AER?
          │    └─ trace_aer_event()
          │
          ├─ CPER section type = ARM processor?
          │    └─ log_arm_hw_error() → trace_arm_event()
          │
          └─ CPER section type = unknown?
               └─ log_non_standard_event() → trace_non_standard_event()
```

---

## Debugfs Interface

```
/sys/kernel/debug/ras/
├── cec/
│   ├── action_threshold    # CE count before page poison (rw)
│   ├── decay_interval      # spring cleaning interval (rw)
│   └── pfns                # current PFN→count array (ro)
```

---

## rasdaemon Integration

`rasdaemon` is the standard userspace RAS daemon on Ubuntu/RHEL:
```bash
sudo rasdaemon --enable --record
sudo ras-mc-ctl --summary     # show error summary
sudo ras-mc-ctl --errors      # show error log (SQLite DB)
```

It subscribes to `tracefs` events at:
- `/sys/kernel/tracing/events/ras/mc_event/enable`
- `/sys/kernel/tracing/events/ras/aer_event/enable`
- etc.

---

## Files

| File | Purpose |
|------|---------|
| `ras.c` | Core: tracepoint definitions, AMD ATL dispatcher, init |
| `cec.c` | Correctable Error Collector (per-PFN count, spring cleaning) |
| `debugfs.c` | debugfs setup for CEC controls |
| `amd/atl/core.c` | AMD ATL entry point, module init |
| `amd/atl/denormalize.c` | UMC-NA → DF address (main translation logic) |
| `amd/atl/dehash.c` | Reverse DF address hashing |
| `amd/atl/umc.c` | UMC-NA unpacking |
| `amd/atl/system.c` | Read DF topology from hardware |
| `amd/fmpm.c` | Firmware Memory Poison Manager |

**Key headers:**
- `include/ras/ras_event.h` — all tracepoint definitions
- `include/linux/ras.h` — CEC and AMD ATL APIs

---

## HackMD Export

Title: **Linux Kernel RAS (Reliability, Availability, Serviceability) Subsystem**

```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel RAS Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`ras_trace_test.py`](ras_trace_test.py) for bpftrace-based verification.
