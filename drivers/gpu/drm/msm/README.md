# MSM DRM Driver — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/msm/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-05-13
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════╗
║                    USER SPACE                                    ║
║  ┌─────────────────────────────────────────────────────────────┐ ║
║  │  Mesa/freedreno (turnip for Vulkan, fd for Gallium3D/GL)   │ ║
║  │  libdrm (msm DRM specific ioctl wrappers)                   │ ║
║  └──────────────────────────┬──────────────────────────────────┘ ║
╚═════════════════════════════╪════════════════════════════════════╝
                              │  /dev/dri/card0 /dev/dri/renderD128
╔═════════════════════════════╪════════════════════════════════════╗
║         KERNEL — DRM Core   │                                     ║
║  ┌──────────────────────────▼──────────────────────────────────┐ ║
║  │  drm_ioctl() dispatcher → msm_ioctl_*() handlers            │ ║
║  └──────────────────────┬──────────────────────────────────────┘ ║
║                         │                                        ║
║  ┌──────────────────────▼──────────────────────────────────────┐ ║
║  │  MSM DRM Driver — msm_drv.c                                 │ ║
║  │  ┌────────────────────────────────────────────────────────┐ │ ║
║  │  │  msm_drm_private (global driver state)                  │ │ ║
║  │  │  ├── msm_kms (display mode setting)                     │ │ ║
║  │  │  ├── msm_gpu (3D rendering engine)                      │ │ ║
║  │  │  └── LRU GEM management (pinned/willneed/dontneed)      │ │ ║
║  │  └────────────────────────────────────────────────────────┘ │ ║
║  └───────┬─────────────────────────────────────────┬───────────┘ ║
║           │                                         │              ║
║  ┌────────▼────────┐                  ┌────────────▼──────────┐  ║
║  │  Display (KMS)  │                  │  GPU (Adreno)          │  ║
║  │  ┌────────────┐ │                  │  ┌──────────────────┐  │  ║
║  │  │ DPU (dpu1) │ │                  │  │ adreno_gpu (base)│  │  ║
║  │  │ MDP4/MDP5  │ │                  │  │ ├── a2xx_gpu     │  │  ║
║  │  │ DSI (dsi)  │ │                  │  │ ├── a3xx_gpu     │  │  ║
║  │  │ DP (dp)    │ │                  │  │ ├── a4xx_gpu     │  │  ║
║  │  │ HDMI (hdmi)│ │                  │  │ ├── a5xx_gpu     │  │  ║
║  │  └────────────┘ │                  │  │ ├── a6xx_gpu     │  │  ║
║  └─────────────────┘                  │  │ └── gen7 (newer) │  │  ║
║                                        │  └──────────────────┘  │  ║
║                                        └───────────────────────┘  ║
╚═══════════════════════════════════════════════════════════════════╝
                              │  MMIO / DMA / IRQ
╔═════════════════════════════╪═════════════════════════════════════╗
║         HARDWARE             ▼                                     ║
║  [ Adreno GPU Core ]  [ MDP/DPU Display Controller ]  [ MDSS ]    ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## 2. Layer-by-layer Component Explanation

### Layer 0 — Hardware

| Component | Role |
|---|---|
| Adreno GPU Core | 3D rendering, compute, and shader execution |
| MDP/DPU | Display controller — scanout, composition, blending |
| MDSS (Mobile Display SubSystem) | Top-level hardware block managing display pipeline |
| DSI/DP/HDMI PHY | Physical layer for display interfaces |

---

### Layer 1 — MSM DRM Driver Core (`msm_drv.c`, `msm_drv.h`)

Central driver module that binds all sub-components using the **component** framework.

#### Key Data Structure — `struct msm_drm_private` (`msm_drv.h:73`)

```c
struct msm_drm_private {
    struct drm_device *dev;        // DRM core device
    struct msm_kms    *kms;        // Display mode-setting
    struct msm_gpu    *gpu;        // 3D GPU engine
    int (*kms_init)(struct drm_device *dev);  // KMS init callback
    // GEM LRU management:
    struct {
        struct drm_gem_lru unbacked;
        struct drm_gem_lru pinned;
        struct drm_gem_lru willneed;
        struct drm_gem_lru dontneed;
    } lru;
};
```

#### Initialization Flow (`msm_drv.c:1108-1123`)

```
module_init(msm_drm_register)
  └─ msm_mdp_register()     // MDP display
  └─ msm_dpu_register()     // DPU display
  └─ msm_dsi_register()     // DSI controller
  └─ msm_hdmi_register()    // HDMI
  └─ msm_dp_register()      // DisplayPort
  └─ adreno_register()      // Adreno GPU
  └─ msm_mdss_register()    // MDSS
```

Each sub-driver registers a **platform driver**. When DT matching triggers probe,
they add themselves as **component masters/slaves** (`msm_drv.c:1073`):

```c
component_master_add_with_match(master_dev, &msm_drm_ops, match);
```

When all components are assembled, `msm_drm_bind()` → `msm_drm_init()`:

```
msm_drm_init()
  ├─ drm_dev_alloc() + drmm_mode_config_init()
  ├─ component_bind_all()     // Wire up all sub-devices
  ├─ msm_gem_shrinker_init() // Set up GEM shrinker
  ├─ msm_drm_kms_init()      // Initialize display pipeline
  ├─ drm_dev_register()      // Register with DRM core
  └─ msm_drm_kms_post_init() // Post-init display setup
```

---

### Layer 2 — Display Subsystem (KMS)

Display is split across several sub-drivers selected at runtime depending on the SoC:

| Subsystem | Files | SoC Support |
|---|---|---|
| DPU (Display Processor Unit) | `disp/dpu1/` | Modern Snapdragon (DPU >= 1.x) |
| MDP4 | `disp/mdp4/` | APQ8064, MSM8960 |
| MDP5 | `disp/mdp5/` | MSM8x74, APQ8084 |
| DSI | `dsi/` | DSI host + PHY |
| DP | `dp/` | DisplayPort (USB-C alt mode) |
| HDMI | `hdmi/` | Legacy HDMI |

The `msm_kms_funcs` vtable (`msm_kms.h:25`) abstracts display hardware:

```c
struct msm_kms_funcs {
    int (*hw_init)(struct msm_kms *kms);
    int (*enable_vblank)(struct msm_kms *, struct drm_crtc *);
    void (*flush_commit)(struct msm_kms *, unsigned crtc_mask);
    void (*wait_flush)(struct msm_kms *, unsigned crtc_mask);
    void (*complete_commit)(struct msm_kms *, unsigned crtc_mask);
    // ...
};
```

---

### Layer 3 — GPU Subsystem (Adreno)

The `msm_gpu_funcs` vtable (`msm_gpu.h:47`) abstracts GPU generations:

```c
struct msm_gpu_funcs {
    int (*hw_init)(struct msm_gpu *gpu);
    void (*submit)(struct msm_gpu *gpu, struct msm_gem_submit *submit);
    void (*flush)(struct msm_gpu *gpu, struct msm_ringbuffer *ring);
    irqreturn_t (*irq)(struct msm_gpu *irq);
    int (*pm_suspend)(struct msm_gpu *gpu);
    int (*pm_resume)(struct msm_gpu *gpu);
    u64 (*gpu_busy)(struct msm_gpu *gpu, unsigned long *out_sample_rate);
    // ...
};
```

**Adreno GPU hierarchy** (`msm_gpu.h:34-45`):

```
msm_gpu (base class)
  └─ adreno_gpu (adreno-specific)
       ├── a2xx_gpu (legacy)
       ├── a3xx_gpu
       ├── a4xx_gpu
       ├── a5xx_gpu
       ├── a6xx_gpu (with GMU — Graphics Management Unit)
       └── gen7_gpu (latest)
```

GPU power management (`msm_gpu.c`):
```
msm_gpu_pm_resume()
  ├─ enable_pwrrail()  → regulator_enable(gpu_reg, gpu_cx)
  ├─ enable_clk()      → clk_bulk_prepare_enable() + dev_pm_opp_set_rate()
  ├─ enable_axi()      → clk_prepare_enable(ebi1_clk)
  └─ msm_devfreq_resume()

msm_gpu_pm_suspend()
  ├─ msm_devfreq_suspend()
  ├─ disable_axi()
  ├─ disable_clk()
  └─ disable_pwrrail()
```

GPU on first open (`msm_drv.c:267-275`):
```
msm_open()
  └─ load_gpu() → adreno_load_gpu()   // lazy GPU init
  └─ context_init()                    // per-fd context
```

---

## 3. Workflow: GPU Command Submission

```
Userspace (Mesa/freedreno)
  │
  │  DRM_IOCTL_MSM_GEM_SUBMIT
  ▼
msm_ioctl_gem_submit()                     [msm_gem_submit.c]
  │
  ├─ msm_submitqueue_get()                 [msm_submitqueue.c]
  ├─ msm_gem_submit_new()                  [msm_gem_submit.c]
  ├─ msm_gem_submit_bo_list()              // pin BOs + map IOVA
  ├─ msm_gem_submit_parse()                // validate cmdstream
  │
  └─ msm_gpu_submit()                      [msm_gpu.c]
       │
       ├─ msm_gpu_hw_init()                // init GPU if needed
       ├─ msm_ringbuffer_submit()          [msm_ringbuffer.c]
       │    └─ gpu->funcs->submit()        // adreno_submit()
       │
       ├─ gpu->funcs->flush()              // ringbuffer flush
       │    (Kick the CP — Command Processor)
       │
       └─ Submit complete — IRQ pending
            │
            ▼
         adreno_irq_handler()              [adreno_gpu.c]
            │
            ├─ a6xx_gpu->funcs->irq()
            └─ msm_fence_signal()          [msm_fence.c]
                 └─ Wake waiting userspace
```

---

## 4. Key Source Files

| File | Purpose |
|---|---|
| `drivers/gpu/drm/msm/msm_drv.c` | Main driver — init, ioctls, component binding |
| `drivers/gpu/drm/msm/msm_drv.h` | Core data structures (`msm_drm_private`) |
| `drivers/gpu/drm/msm/msm_kms.h` | KMS vtable (`msm_kms_funcs`) |
| `drivers/gpu/drm/msm/msm_gpu.c` | GPU lifecycle — PM resume/suspend, HW init, submit |
| `drivers/gpu/drm/msm/msm_gpu.h` | GPU vtable (`msm_gpu_funcs`) |
| `drivers/gpu/drm/msm/msm_gem.c` | GEM object management |
| `drivers/gpu/drm/msm/msm_gem_submit.c` | Command submission ioctl handler |
| `drivers/gpu/drm/msm/msm_ringbuffer.c` | Ringbuffer management |
| `drivers/gpu/drm/msm/msm_atomic.c` | Atomic modeset commit |
| `drivers/gpu/drm/msm/msm_fence.c` | Fence/syncobj signaling |
| `drivers/gpu/drm/msm/adreno/adreno_gpu.c` | Adreno GPU ops |
| `drivers/gpu/drm/msm/disp/dpu1/` | DPU display driver |
| `drivers/gpu/drm/msm/dsi/dsi_host.c` | DSI host controller |
| `drivers/gpu/drm/msm/dp/dp_display.c` | DisplayPort driver |
