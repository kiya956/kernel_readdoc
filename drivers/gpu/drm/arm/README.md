# ARM DRM Display Subsystem (`drivers/gpu/drm/arm/`)

## Overview

ARM provides three distinct DRM display controller drivers in the upstream
kernel:

| Driver | Hardware | Use case |
|---|---|---|
| **HDLCD** | ARM High-Definition LCD Controller | AArch64 FVP / Versatile Express dev boards |
| **Mali-DP** | Mali Display Processor DP500/DP550/DP650 | Mid-range SoCs, AFBC support |
| **Komeda** | Mali-D71 / Komeda display engine | High-end SoCs, multi-pipeline, writeback |

All three follow the standard DRM/KMS atomic model: `drm_crtc`, `drm_plane`,
`drm_encoder`, `drm_connector`, and the atomic commit helpers.

---

## Subsystem Stack

```
  Userspace (KMS/DRM ioctls: drmModeSetCrtc, drmModeAtomicCommit …)
       │
  ─────┴──────────────────────────────────────────────────────────────
       │
  DRM Core (drm_ioctl, drm_atomic_helper, drm_gem_dma_helper)
       │
  ┌────┴───────────────────────────────────────────────────────────┐
  │                  ARM DRM Layer                                  │
  │                                                                 │
  │  ┌────────────┐   ┌──────────────────┐   ┌──────────────────┐  │
  │  │   HDLCD    │   │    Mali-DP       │   │     Komeda       │  │
  │  │ hdlcd_drv  │   │  malidp_drv      │   │  komeda_drv      │  │
  │  │ hdlcd_crtc │   │  malidp_crtc     │   │  komeda_kms      │  │
  │  └─────┬──────┘   │  malidp_planes   │   │  komeda_pipeline │  │
  │        │          │  malidp_hw       │   │  komeda_plane    │  │
  │        │          └──────┬───────────┘   └──────┬───────────┘  │
  └────────┼─────────────────┼────────────────────  ┼──────────────┘
           │                 │                       │
  ┌────────▼─────────────────▼───────────────────────▼──────────┐
  │            Platform / Device Layer                           │
  │  platform_device, of_node, clk, iomem registers, DMA        │
  └────────────────────────────────────────────────────────────-─┘
           │                 │                       │
  ┌────────▼──┐    ┌─────────▼──┐          ┌────────▼──────────┐
  │  HDLCD HW │    │  Mali-DP HW│          │  D71/Komeda HW    │
  │ (AXI DMA, │    │ (AFBC, CSC,│          │ (pipelines, comp, │
  │  timing)  │    │  writeback)│          │  scaler, IPS,WB)  │
  └───────────┘    └────────────┘          └───────────────────┘
           │                 │                       │
  ┌────────▼─────────────────▼───────────────────────▼──────────┐
  │         DRM Bridge Chain → Panel / Monitor                   │
  └──────────────────────────────────────────────────────────────┘
```

---

## Component Deep Dives

### 1. HDLCD (`hdlcd_drv.c`, `hdlcd_crtc.c`)

Simple display controller found on ARM FVP and Versatile Express boards.

```
  HDLCD Hardware
  ──────────────
  AXI Master DMA   ←──── framebuffer (DMA-contiguous GEM)
       │
  Pixel Pipeline
  │  pixel_format registers
  │  timing registers (h/v sync, porches)
  │  gamma LUT
       │
  Video output → DRM Bridge → Monitor
```

Key registers (`hdlcd_regs.h`):
- `HDLCD_REG_FB_BASE` — framebuffer base address
- `HDLCD_REG_COMMAND` — enable/disable display
- `HDLCD_REG_INT_STATUS/MASK` — vsync / underrun / bus-error IRQs
- Timing: H/V display, porches, sync polarities

IRQ handling: counts underrun/DMA-end/bus-error in debugfs counters.

### 2. Mali-DP (`malidp_drv.c`, `malidp_hw.c`, `malidp_planes.c`)

Display processor supporting DP500, DP550, DP650 variants.

```
  Mali-DP Pipeline
  ─────────────────
  Input Layers (up to 4 smart layers + 1 writeback)
       │
  Scaling (per-layer)
       │
  Compositor (smart layer blending)
       │
  Output Pipeline (CSC, gamma, dithering)
       │
  Video output → HDMI/DSI bridge
       │
  Writeback (optional, capture frame to memory)
```

Key features:
- **AFBC** (Arm Frame Buffer Compression) — lossless tiled compression
- **Writeback connector** — capture composited output back to memory
- Per-hardware variant register maps in `malidp_hw.c`
- Gamma coefficient table programming: `malidp_write_gamma_table()`
- Uses `component` framework for deferred binding with bridge drivers

### 3. Komeda / D71 (`display/komeda/`)

Newer modular display engine with up to 2 independent pipelines.

```
  Komeda Pipeline Architecture
  ─────────────────────────────

  Pipeline 0:                           Pipeline 1:
  ┌──────────────────────────┐          ┌─────────────────────────┐
  │ Layer0 Layer1 Layer2 WB  │          │ Layer0 Layer1 Scaler WB │
  │    ↓      ↓      ↓        │          │    ↓       ↓      ↓     │
  │         Compiz0          │          │       Compiz1           │
  │    ↓ (optional Splitter) │          │    ↓                    │
  │         IPS0             │          │    IPS1                 │
  │    ↓                     │          │    ↓                    │
  │    Timing Controller 0   │          │  Timing Controller 1   │
  └──────────────────────────┘          └─────────────────────────┘
        │                                      │
    Encoder/Bridge                         Encoder/Bridge
```

Component IDs (`komeda_pipeline.h`):
```
LAYER0-3        (0-3)   — input scanout layers
WB_LAYER        (7)     — writeback output layer
SCALER0/1       (8-9)   — per-pipeline scalers
SPLITTER        (12)    — split one pipeline across two outputs
MERGER          (14)    — merge two pipelines
COMPIZ0/1       (16-17) — compositor
IPS0/1          (20-21) — image post-processor (CSC, gamma)
TIMING_CTRLR    (22)    — timing generator (hsync/vsync)
```

`komeda_dev` → `komeda_pipeline` × N → `komeda_component` × M

---

## Atomic Commit Data Flow

```
  drm_atomic_commit()
        │
        ▼
  drm_atomic_helper_check()
  ├── drm_atomic_helper_check_planes()
  │       └── plane->atomic_check()  [komeda_plane_atomic_check]
  │               └── build pipeline state, validate component routing
  └── drm_atomic_helper_check_modeset()
          └── crtc->atomic_check()   [malidp/komeda crtc check]
                  └── validate timing, format, scaling constraints
        │
        ▼
  drm_atomic_helper_commit_tail()
  ├── drm_atomic_helper_commit_modeset_disables()
  │       └── disable old crtcs/encoders/bridges (reverse order)
  ├── drm_atomic_helper_commit_planes()
  │       └── plane->atomic_update() → program HW layer registers
  ├── drm_atomic_helper_commit_modeset_enables()
  │       └── enable crtcs/encoders/bridges (forward order)
  └── drm_atomic_helper_wait_for_vblanks()
          └── wait for HW to latch new config on vsync
```

---

## Key Source Files

| File | Role |
|---|---|
| `hdlcd_drv.c` | HDLCD platform driver, DRM device init, IRQ |
| `hdlcd_crtc.c` | HDLCD CRTC/plane ops, timing programming |
| `hdlcd_regs.h` | HDLCD MMIO register offsets |
| `malidp_drv.c` | Mali-DP platform driver, component framework |
| `malidp_hw.c` | Per-variant HW register maps, ops |
| `malidp_crtc.c` | Mali-DP CRTC, gamma, flip complete |
| `malidp_planes.c` | Mali-DP plane ops, AFBC handling |
| `malidp_mw.c` | Mali-DP writeback connector |
| `display/komeda/komeda_drv.c` | Komeda platform driver |
| `display/komeda/komeda_pipeline.c` | Pipeline/component model |
| `display/komeda/komeda_kms.c` | KMS object creation |
| `display/komeda/d71/` | D71 hardware-specific ops |

---

## Test Cases

See `drm_arm_trace_test.py` in this directory.
