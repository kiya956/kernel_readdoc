# DRM Bridge Subsystem

## Overview

The DRM bridge subsystem (`drivers/gpu/drm/bridge/`) provides a flexible
abstraction layer for display signal converters that sit between a GPU
encoder and the final display connector. A bridge translates one signal
type to another (e.g. DSI → eDP, HDMI → DP), or simply passes through
while providing detection, EDID reading, or HPD notification.

Bridges are **not** `drm_mode_object` entities — userspace never sees them
directly. They exist solely to extend the encoder chain with additional
hardware stages.

---

## Subsystem Stack

```
  Userspace (KMS ioctls)
        │
  ──────┴─────────────────────────────────────────────────────
        │
  DRM Core (drm_mode_config, drm_atomic_helper)
        │
  ┌─────▼──────┐
  │  drm_crtc  │  — scanout engine / timing generator
  └─────┬──────┘
        │  pixel data
  ┌─────▼──────┐
  │ drm_encoder│  — digital signal producer (HDMI TX, DSI host, DP source)
  └─────┬──────┘
        │  encoder bridge_chain list
  ┌─────▼──────────────────────────────────────────┐
  │            Bridge Chain                        │
  │                                                │
  │  ┌──────────┐   ┌──────────┐   ┌───────────┐  │
  │  │ Bridge A │──▶│ Bridge B │──▶│ Bridge C  │  │
  │  │(DSI mux) │   │(DSI→DP) │   │(DP panel) │  │
  │  └──────────┘   └──────────┘   └───────────┘  │
  │       │               │               │        │
  │  funcs->attach   funcs->enable  funcs->detect  │
  └────────────────────────────────────────────────┘
        │  physical bus (I2C / SPI / auxiliary / OF)
  ┌─────▼──────┐
  │  drm_panel │  — backlight, timing, power sequencing
  └─────┬──────┘
        │  LVDS / eDP / MIPI-DSI pixel stream
  ┌─────▼──────┐
  │  Display   │  — LCD, OLED, external monitor
  └────────────┘
```

Key source files:
- `drivers/gpu/drm/drm_bridge.c`        — core: add/remove/attach/chain ops
- `drivers/gpu/drm/drm_bridge_connector.c` — generic drm_connector on top of bridge chain
- `include/drm/drm_bridge.h`            — `drm_bridge`, `drm_bridge_funcs`, `drm_bridge_ops`
- `drivers/gpu/drm/bridge/`             — concrete bridge drivers

---

## Key Data Structures

### `struct drm_bridge` (`include/drm/drm_bridge.h:1112`)

```c
struct drm_bridge {
    struct drm_private_obj  base;       // atomic state owner
    struct drm_device      *dev;        // owning DRM device
    struct drm_encoder     *encoder;    // upstream encoder
    struct list_head        chain_node; // position in bridge_chain
    struct device_node     *of_node;    // DT node for OF lookup
    struct list_head        list;       // global bridge registry
    const struct drm_bridge_funcs *funcs;
    enum drm_bridge_ops     ops;        // DETECT | EDID | HPD | MODES | HDMI …
    int                     type;       // DRM_MODE_CONNECTOR_* at output
    bool                    pre_enable_prev_first; // DSI ordering flag
    struct i2c_adapter     *ddc;        // for EDID reads
    struct kref             refcount;
};
```

### `struct drm_bridge_funcs` (`include/drm/drm_bridge.h:63`)

Operations a bridge driver implements:

| Callback | Purpose |
|---|---|
| `attach` / `detach` | chain linkage; attach next bridge here |
| `mode_valid` / `mode_fixup` | timing negotiation |
| `pre_enable` / `enable` | power-up in encoder→display order |
| `disable` / `post_disable` | power-down in display→encoder order |
| `get_modes` | enumerate display modes |
| `detect` | hot-plug detection |
| `edid_read` | read EDID blob |
| `hpd_enable` / `hpd_disable` / `hpd_notify` | HPD interrupt management |
| `atomic_*` | atomic variants of all of the above |

### `enum drm_bridge_ops`

```c
DRM_BRIDGE_OP_DETECT       = BIT(0)  // can detect connected display
DRM_BRIDGE_OP_EDID         = BIT(1)  // can read EDID
DRM_BRIDGE_OP_HPD          = BIT(2)  // supports hot-plug interrupts
DRM_BRIDGE_OP_MODES        = BIT(3)  // can enumerate modes without EDID
DRM_BRIDGE_OP_HDMI         = BIT(4)  // full HDMI sink (infoframes, etc.)
DRM_BRIDGE_OP_HDMI_AUDIO   = BIT(5)  // HDMI audio
DRM_BRIDGE_OP_DP_AUDIO     = BIT(6)  // DP audio
```

---

## Bridge Chain Lifecycle

### Registration

```
bridge driver probe()
    │
    ├── drm_bridge_add(bridge)       // insert into global bridge_list
    └── return 0
```

### Attachment (encoder driver)

```
encoder driver probe() / bind()
    │
    └── devm_drm_of_get_bridge(dev, node, …)   // find bridge by OF node
            │
            └── drm_bridge_attach(encoder, bridge, prev, flags)
                    │
                    ├── refcount bridge
                    ├── list_add chain_node into encoder->bridge_chain
                    ├── bridge->funcs->attach(bridge, encoder, flags)
                    │       └── (bridge driver attaches next bridge here)
                    └── atomic_reset() if atomic bridge
```

### Enable sequence (atomic commit)

```
drm_atomic_helper_commit_modeset_enables()
    │
    ├── drm_atomic_bridge_chain_pre_enable(first_bridge, state)
    │       iterate forward: Bridge A → B → C
    │       calls atomic_pre_enable (or pre_enable)
    │
    ├── encoder_helper_funcs->atomic_enable(encoder, state)
    │
    └── drm_atomic_bridge_chain_enable(first_bridge, state)
            iterate forward: Bridge A → B → C
            calls atomic_enable (or enable)
```

### Disable sequence (reverse order)

```
drm_atomic_helper_commit_modeset_disables()
    │
    ├── drm_atomic_bridge_chain_disable(first_bridge, state)
    │       iterate REVERSE: C → B → A
    │
    ├── encoder_helper_funcs->atomic_disable(encoder, state)
    │
    └── drm_atomic_bridge_chain_post_disable(first_bridge, state)
            iterate forward: A → B → C
            (unless pre_enable_prev_first reverses sub-sections)
```

---

## Data Flow Diagram

```
  Atomic Commit
  ─────────────────────────────────────────────────────────────

  [drm_atomic_state] ──▶ commit_modeset_enables()
                               │
                   ┌───────────┴──────────────────────────┐
                   │  pre_enable pass (A→B→C, forward)     │
                   │  Bridge A: clock/power on              │
                   │  Bridge B: PLL init, DSI config        │
                   │  Bridge C: reset sequence              │
                   └───────────┬──────────────────────────┘
                               │
                   [encoder atomic_enable]
                               │
                   ┌───────────┴──────────────────────────┐
                   │  enable pass (A→B→C, forward)         │
                   │  Bridge A: start pixel stream          │
                   │  Bridge B: enable video output         │
                   │  Bridge C: assert display EN           │
                   └───────────────────────────────────────┘

  ─────────────────────────────────────────────────────────────

  HPD Flow (async, from IRQ)

  Hardware IRQ ──▶ bridge driver ISR
                       │
                       └── drm_bridge_hpd_notify(bridge, status)
                                   │
                                   └── bridge_connector->hpd_cb()
                                               │
                                               └── drm_kms_helper_hotplug_event()
                                                           │
                                                           └── uevent to userspace
```

---

## Concrete Bridge Drivers (selected)

| Driver | File | Conversion |
|---|---|---|
| TI SN65DSI86 | `ti-sn65dsi86.c` | DSI → eDP (also GPIO expander, backlight PWM) |
| TI SN65DSI83 | `ti-sn65dsi83.c` | DSI → LVDS |
| TI TFP410 | `ti-tfp410.c` | Parallel RGB → HDMI/DVI |
| ADV7511 | `adv7511/` | HDMI transmitter (I2C control) |
| NXP PTN3460 | `nxp-ptn3460.c` | DisplayPort → LVDS |
| aux-bridge | `aux-bridge.c` | Transparent passthrough (chain filler) |
| panel | `panel.c` | Wraps drm_panel as last bridge in chain |
| Samsung DSIM | `samsung-dsim.c` | DSI host controller |

---

## HackMD Export

Title: **Linux Kernel DRM Bridge Subsystem**

```
# Linux Kernel DRM Bridge Subsystem

(copy full README content here)
```

---

## Test Cases

See `drm_bridge_trace_test.py` in this directory for bpftrace-based
step-by-step verification of the bridge chain lifecycle.
