# DRM VKMS — Virtual KMS Driver

> **Source tree:** `drivers/gpu/drm/vkms/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-04-28
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════╗
║                       USER SPACE                                     ║
║  ┌──────────┐  ┌──────────┐  ┌──────────────────────────────────┐  ║
║  │ KMS test │  │ Weston / │  │ IGT GPU Tools (kms_*, vkms_*)    │  ║
║  │ suites   │  │ Sway     │  │ → CRC-based rendering validation │  ║
║  └────┬─────┘  └────┬─────┘  └──────────┬───────────────────────┘  ║
║       └─────────────┴──────┬─────────────┘                          ║
║                            │  libdrm / KMS ioctls                    ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │
╔════════════════════════════╪════════════════════════════════════════╗
║  KERNEL — vkms.ko (software-only, no real GPU)                      ║
║                                                                      ║
║  Module params (vkms_drv.c:41-49):                                  ║
║    enable_cursor (bool, 0444) — enable cursor plane                 ║
║    enable_writeback (bool, 0444) — enable writeback connector       ║
║    enable_overlay (bool, 0444) — enable overlay planes              ║
║                                                                      ║
║  ┌── vkms_config (vkms_config.h:22) ─────────────────────────┐     ║
║  │  dev_name (char*)                                          │     ║
║  │  planes (list of vkms_config_plane)                        │     ║
║  │  crtcs (list of vkms_config_crtc)                          │     ║
║  │  encoders (list of vkms_config_encoder)                    │     ║
║  │  connectors (list of vkms_config_connector)                │     ║
║  └────────────────────────────────────────────────────────────┘     ║
║                                                                      ║
║  ┌── Display pipeline ────────────────────────────────────────┐     ║
║  │                                                             │     ║
║  │  vkms_plane (vkms_drv.h:153)                               │     ║
║  │    → drm_plane (primary / cursor / overlay)                │     ║
║  │    → vkms_plane_state (L146): frame_info, pixel read funcs │     ║
║  │                                                             │     ║
║  │  vkms_output (vkms_output.c:8 → vkms_output_init)         │     ║
║  │    → CRTC + encoder + connector wiring                     │     ║
║  │                                                             │     ║
║  │  vkms_crtc (vkms_crtc.c)                                   │     ║
║  │    → vblank hrtimer simulation                             │     ║
║  │    → atomic_flush triggers composer_worker                 │     ║
║  │                                                             │     ║
║  │  vkms_connector (vkms_connector.h:13)                      │     ║
║  │    → drm_connector + drm_connector_state                   │     ║
║  │    → always-connected virtual display                      │     ║
║  │                                                             │     ║
║  │  vkms_writeback (vkms_writeback.c)                         │     ║
║  │    → drm_writeback_connector: capture framebuffer to BO    │     ║
║  └────────────────────────────────────────────────────────────┘     ║
║                                                                      ║
║  ┌── Composer (vkms_composer.c) ──────────────────────────────┐     ║
║  │  vkms_composer_worker (L491) — work_struct callback         │     ║
║  │    → blend all planes → compute CRC → signal vblank         │     ║
║  │  vkms_set_crc_source (L615) — enable/disable CRC           │     ║
║  │  vkms_verify_crc_source (L584) — validate CRC source       │     ║
║  │  vkms_set_composer (L599) — arm/disarm composer             │     ║
║  └────────────────────────────────────────────────────────────┘     ║
║                                                                      ║
║  ┌── Formats (vkms_formats.c) ────────────────────────────────┐     ║
║  │  Pixel read/write functions for:                            │     ║
║  │  XRGB8888, ARGB8888, RGB565, XRGB16161616, and more       │     ║
║  │  vkms_writeback_row (L687) — write blended row to WB buf   │     ║
║  └────────────────────────────────────────────────────────────┘     ║
╚════════════════════════════════════════════════════════════════════╝
                     ▲
                     │ No real hardware — all software rendering
                     │ Uses GEM SHMEM for framebuffers
                     │ hrtimer simulates vblank at configured rate
```

---

## 2. Component Details

### Purpose

VKMS (Virtual KMS) is a **software-only** DRM driver for testing the KMS
(Kernel Mode Setting) subsystem without real GPU hardware. It is used by:
- **IGT GPU Tools** for automated KMS testing
- **CI systems** to validate DRM core changes
- **Developers** learning the KMS API

### Key Structs

**vkms_config** (vkms_config.h:22): Describes a virtual display configuration
(planes, CRTCs, encoders, connectors). Created by `vkms_config_default_create()`.

**vkms_frame_info** (vkms_drv.h:41): Per-frame metadata — source/dst rects,
framebuffer, rotation, pixel read functions.

**vkms_plane_state** (vkms_drv.h:146): Extends `drm_plane_state` with
`frame_info` for compositing.

**vkms_writeback_job** (vkms_drv.h:84): Captures blended output to a BO for
CRC verification.

### Composer (CRC engine)

The **composer worker** (`vkms_composer_worker`, vkms_composer.c:491) is the
heart of VKMS. It runs as a `work_struct` triggered by atomic flush:

1. Read all visible plane pixels via format-specific read functions
2. Alpha-blend planes in priority order
3. Compute a CRC32 of the blended output
4. If writeback is active, write pixels to writeback buffer
5. Signal the vblank completion fence

---

## 3. Workflow: Page Flip + CRC

```
 Userspace (IGT)                 Kernel (vkms.ko)
      │                              │
      │  drmModeSetCrtc / AtomicCommit│
      ├─────────────────────────────►│
      │                              │  1. atomic_check → validate
      │                              │  2. atomic_flush
      │                              │     → queue vkms_composer_worker
      │                              │
      │                              │  3. vkms_composer_worker runs:
      │                              │     → read plane pixels
      │                              │     → alpha-blend layers
      │                              │     → compute CRC32
      │                              │     → drm_crtc_add_crc_entry()
      │                              │
      │  drmWaitVBlank()             │  4. hrtimer fires → vblank event
      │◄─────────────────────────────┤
      │                              │
      │  read /sys/.../crc/data      │  5. CRC available for validation
      │◄─────────────────────────────┤
```

---

## 4. Key Source Files

| File | Purpose |
|---|---|
| `vkms_drv.c` | Module init (L260), platform driver, module params |
| `vkms_drv.h` | Core structs: `vkms_frame_info` (L41), `vkms_plane_state` (L146) |
| `vkms_config.c` | Config create/destroy, plane/CRTC/encoder/connector management |
| `vkms_config.h` | `vkms_config` (L22), config structs |
| `vkms_output.c` | Output init (L8): wire CRTC + encoder + connector |
| `vkms_crtc.c` | CRTC: vblank hrtimer, atomic enable/flush |
| `vkms_plane.c` | Plane: atomic check/update, format setup |
| `vkms_connector.c` | Virtual connector (always connected) |
| `vkms_connector.h` | `vkms_connector` (L13) |
| `vkms_composer.c` | Composer worker (L491), CRC source (L615), set_composer (L599) |
| `vkms_formats.c` | Pixel read/write functions, writeback_row (L687) |
| `vkms_writeback.c` | Writeback connector implementation |

---

## References

- `vkms_config.h:22` — `struct vkms_config`
- `vkms_drv.h:41` — `struct vkms_frame_info`
- `vkms_drv.h:146` — `struct vkms_plane_state`
- `vkms_drv.c:260` — `module_init(vkms_init)`
- `vkms_composer.c:491` — `vkms_composer_worker`
- `vkms_output.c:8` — `vkms_output_init`
