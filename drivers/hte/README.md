# Linux Kernel Hardware Timestamping Engine (HTE) Subsystem

## Overview

The **Hardware Timestamping Engine (HTE)** subsystem provides a generic
framework for hardware that can timestamp signal/line events (edges on GPIO,
IRQ assertions, peripheral bus transactions) with nanosecond-resolution
hardware clocks — far more precise than software timestamps which suffer
from IRQ latency and scheduling jitter.

Introduced in Linux 5.18 (NVIDIA, Dipen Patel). Currently the only upstream
hardware provider is the **NVIDIA Tegra194** (Xavier) GTE (Generic Timestamping
Engine) which can timestamp GPIO lines and LIC (Legacy Interrupt Controller)
events with a 31.25 MHz clock (32 ns resolution).

Source: `drivers/hte/`

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Consumer (client driver)                      │
│                                                                      │
│  hte_ts_get()            ← get descriptor from DT/platform data     │
│  hte_request_ts_ns()     ← register callback for timestamp events   │
│  hte_enable_ts()         ← start capturing timestamps               │
│  hte_disable_ts()        ← stop capturing                           │
│  hte_ts_put()            ← release descriptor                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ hte_ts_desc
┌──────────────────────────────▼──────────────────────────────────────┐
│                     HTE Core (hte.c)                                 │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  hte_device (per-chip)                                        │   │
│  │  ┌──────────────────────────────────────────────────────┐    │   │
│  │  │  hte_ts_info[nlines]                                  │    │   │
│  │  │  xlated_id | flags | seq | cb | tcb | dropped_ts      │    │   │
│  │  └──────────────────────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  hte_push_ts_ns()   ← called by provider with hardware timestamp    │
│  cb_work workqueue  ← runs sleeping secondary callback (tcb)        │
│  debugfs: /sys/kernel/debug/hte/                                    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ hte_chip + hte_ops
┌──────────────────────────────▼──────────────────────────────────────┐
│               HTE Provider (hte-tegra194.c)                          │
│                                                                      │
│  Tegra194 GTE (Generic Timestamping Engine)                          │
│  ├── AON GTE: timestamps GPIO lines (31.25 MHz, 249 lines)           │
│  └── LIC GTE: timestamps LIC IRQ lines (31.25 MHz, 352 lines)        │
│                                                                      │
│  hte_ops.request()      → configure GTE slice/bit for a line        │
│  hte_ops.enable()       → set TECTRL enable bit in HW               │
│  hte_ops.disable()      → clear TECTRL enable bit                   │
│  hte_ops.get_clk_src_info() → report clock: 31.25 MHz               │
│  IRQ handler → hte_push_ts_ns() → delivers to HTE core              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│         Hardware: Tegra194 GTE registers (MMIO)                      │
│  TEGRA_GTE_TECTRL    enable/disable timestamping per-line            │
│  TEGRA_GTE_TETSCH    timestamp value (lower 32 bits)                 │
│  TEGRA_GTE_TETSCV    timestamp value (upper 32 bits)                 │
│  TEGRA_GTE_TESLICE   event slice register                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Consumer API

A driver that wants hardware timestamps on a signal line:

```c
struct hte_ts_desc desc;

/* Step 1: look up the HTE line from device-tree */
hte_init_line_attr(&desc, line_id, edge_flags, name, gpio_desc);
ret = hte_ts_get(dev, &desc, dt_index);

/* Step 2: register callbacks */
ret = hte_request_ts_ns(&desc, primary_cb, secondary_cb, client_data);

/* Step 3: arm the hardware */
hte_enable_ts(&desc);

/* --- timestamps arrive via primary_cb() on every edge --- */

/* Step 4: release */
hte_disable_ts(&desc);
hte_ts_put(&desc);
```

**Two callbacks:**
- `hte_ts_cb_t cb` — runs in **interrupt/atomic** context; must be fast.
  Returns `HTE_CB_HANDLED` or `HTE_RUN_SECOND_CB`.
- `hte_ts_sec_cb_t tcb` — runs in **process context** (workqueue); safe to
  sleep, do I2C, etc.

### 2. HTE Core (`hte.c`)

- Maintains a global `hte_devices` list (one per registered chip).
- Each chip registers `nlines` timestamp slots (`hte_ts_info[]`).
- `hte_push_ts_ns()` is called from the provider's IRQ handler with a
  `struct hte_ts_data` carrying the hardware timestamp and edge direction.
- Core invokes `cb` immediately; if `HTE_RUN_SECOND_CB`, schedules `cb_work`.
- Tracks `dropped_ts` (atomic) when callbacks are too slow.
- Exposes per-line debugfs: request count, dropped count, sequence number.

### 3. Provider: hte-tegra194 (NVIDIA Xavier)

Tegra194 has two GTE blocks:

| Block | Lines | Purpose |
|-------|-------|---------|
| AON GTE | 249 lines | GPIO bank timestamps |
| LIC GTE | 352 lines | Legacy IRQ controller timestamps |

Each "line" maps to a *slice* (32-bit word) and a *bit* within that slice in
the GTE's event enable registers. The provider's `xlate_of()` callback converts
a DT phandle+args into `(slice, bit)`.

**IRQ flow:**
```
GTE hardware detects edge on line N
    → asserts IRQ to CPU
    → tegra194_hte_isr()
        → reads TETSCH/TETSCV (hardware timestamp)
        → calls hte_push_ts_ns(chip, xlated_id, &ts_data)
            → HTE core invokes consumer cb
```

### 4. Key Data Structures

| Struct | Role |
|--------|------|
| `hte_chip` | Provider registration: ops, nlines, xlate callbacks |
| `hte_ops` | request / release / enable / disable / get_clk_src_info |
| `hte_device` | Core-side device, owns `hte_ts_info[]` array |
| `hte_ts_info` | Per-line: xlated_id, callback ptrs, dropped count |
| `hte_ts_desc` | Consumer handle: line attrs (id, edge, name, gpio) |
| `hte_ts_data` | Timestamp payload: `ts_ns`, `raw`, `edge` |
| `hte_clk_info` | Clock source: hz, type (internal/external) |

---

## Data Flow Diagram

```
Hardware edge on GPIO line
         │
         ▼
  ┌──────────────────┐
  │  GTE hardware    │  captures ts = TETSCH:TETSCV (clock cycles)
  │  (31.25 MHz)     │  converts to ns internally
  └──────┬───────────┘
         │ IRQ
         ▼
  ┌──────────────────────────────────────────────────┐
  │  tegra194_hte_isr() [interrupt context]          │
  │  reads timestamp registers                       │
  │  hte_push_ts_ns(chip, id, &hte_ts_data)          │
  └──────┬───────────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────────────────┐
  │  HTE Core hte_push_ts_ns() [interrupt context]   │
  │  lookup hte_ts_info by xlated_id                 │
  │  call consumer cb(ts_data, cl_data)              │
  └──────┬──────────────────┬────────────────────────┘
         │ HTE_CB_HANDLED   │ HTE_RUN_SECOND_CB
         ▼                  ▼
     done            schedule cb_work
                           │
                           ▼
                  ┌──────────────────────┐
                  │  workqueue [process] │
                  │  consumer tcb()      │
                  └──────────────────────┘
```

---

## Files

| File | Purpose |
|------|---------|
| `hte.c` | Core: device registration, consumer API, push, debugfs |
| `hte-tegra194.c` | Tegra194 GTE provider (AON + LIC engines) |
| `hte-tegra194-test.c` | In-kernel self-test (KUnit-style) for Tegra194 |

**Key headers:**
- `include/linux/hte.h` — full consumer + provider API

---

## HackMD Export

Title: **Linux Kernel HTE (Hardware Timestamping Engine) Subsystem**

```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel HTE Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`hte_trace_test.py`](hte_trace_test.py) for bpftrace-based verification.
