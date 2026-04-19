# Linux Kernel: Interconnect (ICC) Subsystem

> Source: `drivers/interconnect/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the Interconnect Framework?

Modern SoCs have complex on-chip buses (Network-on-Chip, DDRSS, Fabric)
connecting CPU, GPU, camera, display, and memory controllers. Different
workloads need different bandwidth guarantees — a 4K video decode needs
more DDR bandwidth than idle.

The **interconnect (ICC) framework** lets drivers declare their bandwidth
requirements (`avg_bw`, `peak_bw` in kBps) on named paths between SoC nodes.
The framework aggregates all requests on a shared bus and programs the
hardware (clock rates, QoS arbiters) to satisfy the peak demand.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                     CONSUMERS (kernel drivers)                   │
│  Display, Camera, GPU, DSP, UFS, PCIe                           │
│  of_icc_get(dev, "cpu-mem")  →  icc_set_bw(path, avg, peak)    │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│               ICC CORE  (core.c + bulk.c + icc-clk.c)           │
│                                                                  │
│  icc_get() / of_icc_get()   — resolve DT phandle → icc_path     │
│  icc_set_bw()               — record request, aggregate, commit  │
│  icc_enable() / disable()   — pause/resume a path's BW request  │
│  icc_set_tag()              — mark path with traffic class tag   │
│                                                                  │
│  Aggregation: for each node, sum avg_bw from all requesters,     │
│               take max of peak_bw from all requesters            │
└──────────────┬───────────────────────────────────────────────────┘
               │  provider callbacks: set() / aggregate() / get_bw()
               ▼
┌──────────────────────────────────────────────────────────────────┐
│              PROVIDER DRIVERS                                    │
│                                                                  │
│  qcom/                  imx/               samsung/  mediatek/   │
│  ├─ icc-rpmh.c          ├─ imx8m-blk-ctl  └─ exynos └─ mt8183   │
│  │  BCM voter + RPMh    ├─ imx8mp          (AXI bus)  (smi-bus)  │
│  ├─ icc-rpm.c                                                    │
│  │  RPM message-based                                            │
│  └─ bcm-voter.c                                                  │
│     Bus Clock Manager voting                                     │
│                                                                  │
│  icc-clk.c  — generic: BW→clock rate via clk_set_rate()         │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│         SoC Hardware: NoC / AXI Fabric / DDR Scheduler          │
│  QoS registers, bandwidth throttle, priority arbitration         │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `core.c` — Framework Core

Central state machine managing all providers and paths.

Key flows:

**Path creation (`icc_set_bw` flow):**
1. Consumer calls `icc_set_bw(path, avg_kBps, peak_kBps)`
2. Core stores `(avg_bw, peak_bw)` in each `icc_req` along the path
3. For each node on path: `aggregate(node)` — sum avg, max peak across all requesters
4. Provider's `set()` callback receives the aggregated values
5. Provider programs hardware (RPMh message / clock rate / QoS register)

**Path lookup (`of_icc_get`):**
- Reads `interconnects` + `interconnect-names` DT properties
- Resolves phandles to provider → calls `provider->xlate()` to get node IDs
- Finds shortest path between src and dst using BFS over icc_node graph

### 3.2 `bulk.c` — Bulk Path Management

Helper for drivers with multiple paths — `icc_bulk_get()` / `icc_bulk_set_bw()`
/ `icc_bulk_enable()`. Similar pattern to `clk_bulk_*`.

### 3.3 `icc-clk.c` — Generic Clock-Backed ICC

For simple buses where bandwidth maps directly to clock frequency:
```
bw_kBps = clk_freq × bus_width_bytes
```
Registers an icc_provider that calls `clk_set_rate()` in `set()`.

### 3.4 `qcom/icc-rpmh.c` — Qualcomm BCM/RPMh

The main Qualcomm implementation. Qualcomm uses **Bus Clock Manager (BCM)**
hardware blocks, each managing a set of interconnect nodes.

Flow:
1. `icc_set_bw()` → aggregate per-BCM vote
2. `bcm-voter.c` collects votes and sends one **RPMh** (Resource Power Manager)
   message per BCM in a single **sleep/wake-up set** command
3. RPMh programs the DDRSS/NoC hardware in the always-on domain

### 3.5 `qcom/icc-rpm.c` — Legacy Qualcomm RPM

Older platforms (MSM8x series) use the synchronous RPM (not RPMh).
Same voting model but via SMD (Shared Memory Device) channel.

---

## 4. Key Data Structures

```c
/* One endpoint on the bus fabric */
struct icc_node {
    int id;                      // provider-assigned ID (from DT cells)
    const char *name;            // e.g., "MASTER_MDP0", "SLAVE_EBI1"
    struct icc_provider *provider;
    struct list_head node_list;  // in provider's node list
    struct list_head adj_list;   // neighbors (next hops)
    u32 avg_bw;                  // aggregated avg bandwidth (kBps)
    u32 peak_bw;                 // aggregated peak bandwidth (kBps)
    struct hlist_head req_list;  // all icc_req for this node
    void *data;                  // provider-private (BCM, clock, etc.)
};

/* A route between two nodes (multiple hops) */
struct icc_path {
    const char *name;
    size_t num_nodes;
    struct icc_req reqs[];       // one per hop
};

/* One consumer's request on one node */
struct icc_req {
    struct icc_node *node;
    struct device *dev;
    bool enabled;
    u32 tag;                     // traffic class (optional)
    u32 avg_bw;                  // kBps average
    u32 peak_bw;                 // kBps peak
};

/* A hardware bus/fabric */
struct icc_provider {
    struct list_head provider_list;
    struct list_head nodes;
    int (*set)(struct icc_node *src, struct icc_node *dst);
    int (*aggregate)(struct icc_node *node, u32 tag,
                     u32 avg_bw, u32 peak_bw,
                     u32 *agg_avg, u32 *agg_peak);
    int (*get_bw)(struct icc_node *node, u32 *avg, u32 *peak);
    struct icc_node *(*xlate)(const struct of_phandle_args *, void *);
    struct device *dev;
};
```

---

## 5. Data Flow: Display Driver Requesting DDR Bandwidth

```
 Display driver (consumer)         ICC core                  Qualcomm RPMh
 ────────────────────────         ──────────                ──────────────
 1. probe():
    path = of_icc_get(dev,
             "mdp-mem")
       │
 2. icc_get() BFS:
    MASTER_MDP → ... → SLAVE_EBI1
    (through fabric nodes)
       │
 3. Frame ready, need BW:
    icc_set_bw(path,
       avg=2000000,   // 2 GB/s avg
       peak=4000000)  // 4 GB/s peak
       │
                    4. aggregate():         5. BCM vote:
                       for each node           MASTER_MDP BCM
                       sum avg, max peak       → RPMh message
                                               → DDR QoS set
       │
 6. DDR scheduler programs ◄────────────────── Hardware responds
    to 4 GB/s guaranteed                       (within microseconds)
       │
 7. Display scans out frame
    at full speed (no throttle)
       │
 8. Idle (no frame):
    icc_set_bw(path, 0, 0)  ──────────────────► DDR clock lowered
```

---

## 6. Device Tree Binding

```dts
/* Provider */
mc: interconnect@1900000 {
    compatible = "qcom,sc8280xp-mc-virt";
    #interconnect-cells = <2>;  /* src_id, dst_id (or just one cell) */
};

gem_noc: interconnect@9100000 {
    compatible = "qcom,sc8280xp-gem-noc";
    #interconnect-cells = <2>;
};

/* Consumer */
mdss: display@ae00000 {
    interconnects = <&gem_noc MASTER_MDP  &mc SLAVE_EBI1>,
                    <&cnoc2  MASTER_MDP  &cnoc2 SLAVE_DISPLAY_CFG>;
    interconnect-names = "mdp0-mem", "mdp-cfg";
};
```

---

## 7. Debugfs

```
/sys/kernel/debug/interconnect/
  interconnect_summary    ← node / avg_bw / peak_bw table
  providers/
    <provider-name>/
      nodes               ← all nodes with current BW
```

---

## 8. Trace Events

| Tracepoint | Fires when |
|---|---|
| `interconnect:icc_set_bw` | Consumer calls `icc_set_bw()` |
| `interconnect:icc_set_bw_end` | `icc_set_bw()` completes |

---

## 9. Summary

The ICC framework:
1. **Decouples** driver bandwidth requirements from hardware programming.
2. **Aggregates** competing requests on shared buses — no driver needs to
   know about other consumers on the same fabric.
3. **Supports heterogeneous hardware** — RPMh (Qualcomm), AXI clock
   scaling (generic), platform-specific NoC controllers — via a clean
   provider callback interface.
4. **Integrates with PM** — paths can be disabled during suspend
   and re-enabled on resume with the same BW constraints.
