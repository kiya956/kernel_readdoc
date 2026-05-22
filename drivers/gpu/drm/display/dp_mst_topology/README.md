# DRM DP MST Topology — Full Initialization Flow

> **Source tree:** `drivers/gpu/drm/display/drm_dp_mst_topology.c`, `drivers/gpu/drm/i915/display/intel_dp*.c`,
> `drivers/usb/typec/altmodes/displayport.c`, `drivers/usb/typec/tcpm/tcpm.c`
> **Kernel:** noble-linux-oem
> **Date:** 2026-05-22
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## Table of Contents

0. [USB-C DP Alt Mode Negotiation (Phase 0)](#0-usb-c-dp-alt-mode-negotiation-phase-0)
1. [Unified Flow: HPD → DPCD → DSC/FEC → Training → MST Topology → Pixels](#1-unified-flow)
2. [Key Data Structures](#2-key-data-structures)
3. [Phase 1: Hotplug Detection & DPCD Read](#3-phase-1-hotplug-detection--dpcd-read)
4. [Phase 2: MST Detection & Topology Enable](#4-phase-2-mst-detection--topology-enable)
5. [Phase 3: Topology Discovery (Sideband)](#5-phase-3-topology-discovery-sideband)
6. [Phase 4: Atomic Compute — DSC/FEC/BW Decision](#6-phase-4-atomic-compute--dscfecbw-decision)
7. [Phase 5: Atomic Commit — Training, FEC, Payloads, Pixels](#7-phase-5-atomic-commit--training-fec-payloads-pixels)
8. [Topology Maintenance (HPD IRQ)](#8-topology-maintenance-hpd-irq)
9. [Refcounting Model](#9-refcounting-model)
10. [Appendix: DP AUX Channel](#10-appendix-dp-aux-channel)

---

## 0. USB-C DP Alt Mode Negotiation (Phase 0)

When connecting a display via USB-C (including USB-C to HDMI adapters), the
connection starts as plain USB. DP Alt Mode is negotiated over the USB-C CC
(Configuration Channel) pins using USB Power Delivery VDM messages — this
happens **before** any DP signaling exists.

### USB-C Connector Pin Roles

```
USB-C Connector: 24 pins
━━━━━━━━━━━━━━━━━━━━━━━━
  CC1, CC2          ← Configuration Channel (orientation detect, PD messaging)
  TX1+/-, RX1+/-    ← SuperSpeed lane pair 1 (USB 3.x or DP lanes)
  TX2+/-, RX2+/-    ← SuperSpeed lane pair 2 (USB 3.x or DP lanes)
  D+, D-            ← USB 2.0 (always stays USB, never reassigned)
  VBUS, GND         ← Power
  SBU1, SBU2        ← Sideband Use (become AUX CH+/- in DP Alt Mode)
```

### Negotiation Flow

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  Step 0: Cable Plugged In — Everything is USB                   │
 │                                                                  │
 │  CC pins detect cable presence + orientation                    │
 │  tcpm.c state: SNK_UNATTACHED → SNK_ATTACH_WAIT                │
 │  (drivers/usb/typec/tcpm/tcpm.c:37-155)                        │
 │                                                                  │
 │  All SuperSpeed pairs = USB 3.x                                 │
 │  SBU pins = unused                                              │
 │  No DP anything yet                                             │
 └──────────────────────────┬───────────────────────────────────────┘
                            │ CC detects valid connection
                            ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  Step 1: USB PD Negotiation (over CC pins)                      │
 │                                                                  │
 │  tcpm.c: SNK_DISCOVERY → SNK_READY (or SRC_READY)              │
 │  ├─ Exchange Source/Sink capabilities                           │
 │  ├─ Negotiate voltage/current (5V/9V/15V/20V)                  │
 │  └─ Establish USB data connection (USB 3.x works now)           │
 │                                                                  │
 │  ▲ At this point: full USB 3.x running on all SS lanes          │
 └──────────────────────────┬───────────────────────────────────────┘
                            │ PD contract established
                            ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  Step 2: Alt Mode Discovery (Structured VDMs over CC)           │
 │                                                                  │
 │  tcpm_pd_svdm()  tcpm.c:1985-2205                               │
 │  vdm_state_machine_work()  tcpm.c:2745-2767                     │
 │                                                                  │
 │  ① DISCOVER_IDENTITY                                            │
 │     → "who are you?" (get VID/PID of partner)                   │
 │                                                                  │
 │  ② DISCOVER_SVIDS                                               │
 │     → "what alt modes do you support?"                          │
 │     ← partner replies with SVID list                            │
 │       e.g. [0xFF01 = DisplayPort, 0x8087 = Thunderbolt]         │
 │                                                                  │
 │  ③ DISCOVER_MODES (for SVID 0xFF01)                             │
 │     → "what DP capabilities / pin assignments?"                 │
 │     ← partner replies: supported pins, signaling, role          │
 │                                                                  │
 │  All of this is over CC pins — NOT USB data, NOT DP AUX         │
 └──────────────────────────┬───────────────────────────────────────┘
                            │ DP alt mode discovered
                            ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  Step 3: Enter DP Alt Mode + Configure Pins                     │
 │                                                                  │
 │  dp_altmode_activate()  displayport.c:497-516                   │
 │                                                                  │
 │  ④ ENTER_MODE (SVID=0xFF01)                                     │
 │     → typec_altmode_enter() → "activate DP alt mode"            │
 │     ← ACK                                                       │
 │                                                                  │
 │  ⑤ DP STATUS_UPDATE                                              │
 │     → "what's your HPD state, multi-function preference?"       │
 │     ← partner reports connection status                         │
 │                                                                  │
 │  ⑥ DP CONFIGURE — pin assignment selection                      │
 │     dp_altmode_configure()  displayport.c:99-155                │
 │                                                                  │
 │   ┌───────────────────────────────────────────────────────────┐  │
 │   │  Pin Assignment Options:                                  │  │
 │   │                                                           │  │
 │   │  Pin C: 4 DP lanes + no USB 3.x  (default, line 140)     │  │
 │   │         maximum DP bandwidth (HBR3 × 4 = 32.4 Gbps)      │  │
 │   │                                                           │  │
 │   │  Pin D: 2 DP lanes + USB 3.x     (multi-function)        │  │
 │   │         half DP bandwidth, but USB 3.x data preserved     │  │
 │   │                                                           │  │
 │   │  Pin E: 4 DP lanes + no USB 3.x  (different mapping)     │  │
 │   │                                                           │  │
 │   │  Selection logic (displayport.c:131-148):                 │  │
 │   │    if partner prefers multi-func → Pin D                  │  │
 │   │    else if Pin C available → Pin C (default)              │  │
 │   │    else → whatever is mutually supported                  │  │
 │   └───────────────────────────────────────────────────────────┘  │
 │                                                                  │
 │  → CONFIGURE VDM sent → USB-C mux hardware switches pins       │
 └──────────────────────────┬───────────────────────────────────────┘
                            │ pins physically reassigned
                            ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  Step 4: Pins Reassigned — GPU Takes Over                       │
 │                                                                  │
 │  BEFORE (USB mode):            AFTER (DP Alt Mode, Pin C):      │
 │    TX1/RX1 = USB 3.x SS        TX1/RX1 = DP Main Link L0,L1   │
 │    TX2/RX2 = USB 3.x SS        TX2/RX2 = DP Main Link L2,L3   │
 │    SBU1/2  = unused             SBU1/2  = DP AUX CH+/CH-       │
 │    D+/D-   = USB 2.0            D+/D-   = USB 2.0 (kept!)     │
 │                                                                  │
 │  AFTER (DP Alt Mode, Pin D — multi-function):                   │
 │    TX1/RX1 = USB 3.x SS  (kept!)                               │
 │    TX2/RX2 = DP Main Link L0,L1  (only 2 DP lanes)             │
 │    SBU1/2  = DP AUX CH+/CH-                                    │
 │    D+/D-   = USB 2.0            (kept!)                        │
 │                                                                  │
 │  i915 TC port detects DP Alt Mode:                              │
 │    icl_tc_phy_hpd_live_status()  intel_tc.c:511-541             │
 │    → reads PORT_TX_DFLEXDPSP → TC_LIVE_STATE_TC bit set         │
 │    → tc->mode = TC_PORT_DP_ALT    intel_tc.c:34                │
 │    → intel_tc_port_in_dp_alt_mode() returns true                │
 │       intel_tc.c:108-116                                        │
 │                                                                  │
 │  From here: standard DP flow                                    │
 │  → HPD detected → DPCD read via AUX (SBU pins)                 │
 │  → Link training on main link lanes                             │
 │  → MST topology discovery (if MST)                             │
 │  → Pixels flow                                                  │
 │  (see Phase 1-5 below)                                          │
 └──────────────────────────────────────────────────────────────────┘
```

### USB-C to HDMI Adapters

A USB-C to HDMI cable/adapter contains a **DP-to-HDMI protocol converter**
chip inside. The USB-C side negotiates DP Alt Mode as above, then the adapter
translates DP signaling to HDMI on the output. The GPU only ever speaks DP —
it does not know about HDMI.

### Channel Summary

| Phase | Physical Channel | Protocol | Who's Talking |
|-------|-----------------|----------|---------------|
| Cable detect | CC pins | USB-C spec | PHY hardware |
| PD negotiation | CC pins | USB PD | TCPM ↔ partner PD |
| Alt mode discovery | CC pins | Structured VDM | TCPM ↔ partner, SVID 0xFF01 |
| Pin reassignment | CC pins | CONFIGURE VDM | Mux hardware switches |
| DPCD / link config | SBU pins (= AUX) | DP AUX (1 Mbps) | **GPU** ↔ display |
| Video data | TX/RX SS lanes (= DP) | DP Main Link | **GPU** ↔ display |
| USB 2.0 (always) | D+/D- | USB 2.0 | USB host ↔ device |

### Key Source Files

| File | Role |
|------|------|
| `drivers/usb/typec/tcpm/tcpm.c` | Type-C Port Manager: PD + VDM state machine |
| `drivers/usb/typec/altmodes/displayport.c` | DP Alt Mode driver (SVID 0xFF01) |
| `drivers/gpu/drm/i915/display/intel_tc.c` | i915 Type-C port mode detection |
| `drivers/gpu/drm/i915/display/intel_dp_aux.c` | AUX transactions (over SBU pins) |

---

## 1. Unified Flow

Below is the **complete end-to-end sequence** from cable plug-in to pixels on
screen for an i915 MST setup. Each box indicates where DPCD, DSC, FEC,
link training, and MST topology events happen.

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                        HARDWARE EVENT: Cable Plug-In                       │
 └─────────────────────────┬───────────────────────────────────────────────────┘
                           │ long HPD
                           ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │  Phase 1: HPD Detection & DPCD Read                                        │
 │                                                                            │
 │  intel_hpd_irq_handler()            intel_hotplug.c:586                    │
 │    └─ i915_digport_work_func()      intel_hotplug.c:389                    │
 │       └─ intel_dp_hpd_pulse()       intel_dp.c:6325                        │
 │          long HPD → returns IRQ_NONE → triggers full detect                │
 │                                                                            │
 │  i915_hotplug_work_func()           intel_hotplug.c:467                    │
 │    └─ intel_ddi_hotplug()           intel_ddi.c:4756                       │
 │       └─ intel_dp_detect()          intel_dp.c:5827                        │
 │          │                                                                 │
 │          ├─[DPCD] intel_dp_detect_dpcd()            intel_dp.c:5517        │
 │          │        └─ intel_dp_get_dpcd()             intel_dp.c:4414       │
 │          │           ├─ intel_dp_init_lttpr_and_dprx_caps()                │
 │          │           │  intel_dp_link_training.c:270                       │
 │          │           │  └─ drm_dp_read_dpcd_caps()  :294                   │
 │          │           │     ▲ Reads DPCD 0x0000-0x000F (receiver caps)      │
 │          │           │     ▲ Reads eDP DPCD if applicable                  │
 │          │           ├─ drm_dp_read_sink_count()    :4438                  │
 │          │           └─ drm_dp_read_downstream_info():4460                 │
 │          │                                                                 │
 │          ├─[DSC/FEC CAPS] intel_dp_detect_dsc_caps() intel_dp.c:4179      │
 │          │   └─ intel_dp_get_dsc_sink_cap()          intel_dp.c:4141      │
 │          │      ├─ reads DP_DSC_SUPPORT (0x0060)     :4125-4138           │
 │          │      └─ reads DP_FEC_CAPABILITY (0x0090)  :4160-4167           │
 │          │      ▲ Now we know: sink DSC decompressor caps + FEC capable?   │
 │          │                                                                 │
 │          └─[MST DETECT] intel_dp_mst_detect()       intel_dp.c:4494       │
 │                         └─ drm_dp_read_mst_cap()    :4501                 │
 │                            ▲ Reads DP_MSTM_CAP (DPCD 0x0021 bit 0)       │
 └─────────────────────────────┬───────────────────────────────────────────────┘
                               │ sink supports MST?
                               ▼ YES
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │  Phase 2: MST Topology Enable                                              │
 │                                                                            │
 │  intel_dp_mst_configure()           intel_dp.c:4517                        │
 │    ├─ intel_dp_mst_prepare_probe()  intel_dp_mst.c:2087                    │
 │    └─ drm_dp_mst_topology_mgr_set_mst(mgr, true)                          │
 │       drm_dp_mst_topology.c:3644                                           │
 │       ├─ re-reads DPCD into mgr->dpcd  :3658-3664                         │
 │       ├─ creates root branch (mst_primary) at LCT=1  :3666-3676           │
 │       ├─ writes DP_MSTM_CTRL = MST_EN|UP_REQ_EN|UPSTREAM_IS_SRC :3678     │
 │       │  ▲ Sink now knows: "you are MST source, enable sideband"           │
 │       ├─ clears payload ID table  :3685-3686                               │
 │       └─ queue_work(probe_work)   :3688                                    │
 │          ▲ Topology discovery starts asynchronously                        │
 └─────────────────────────────┬───────────────────────────────────────────────┘
                               │ probe_work fires
                               ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │  Phase 3: Topology Discovery (Sideband Messages)                           │
 │                                                                            │
 │  drm_dp_mst_link_probe_work()       drm_dp_mst_topology.c:2654            │
 │    ├─ CLEAR_PAYLOAD_ID_TABLE (once) :2665-2692                             │
 │    └─ drm_dp_check_and_send_link_address(root)  :2694                      │
 │       └─ drm_dp_send_link_address(branch)  :2917                           │
 │          ├─ build LINK_ADDRESS sideband msg  :1167                         │
 │          ├─ queue TX, wait for reply  :2875, :1265                         │
 │          ├─ validate GUID  :2952-2958                                      │
 │          └─ for each port in reply:                                        │
 │             drm_dp_mst_handle_link_address_port()  :2347                   │
 │               ├─ create/update drm_dp_mst_port                             │
 │               ├─[FEC per-port] ENUM_PATH_RESOURCES → port->fec_capable     │
 │               │  drm_dp_send_enum_path_resources()  :3020                  │
 │               │  reads ACK reply → port->full_pbn + port->fec_capable      │
 │               ├─ drm_dp_port_set_pdt()  :2069                             │
 │               │  └─ branch? create child mstb, recurse LINK_ADDRESS       │
 │               │  └─ end-device? register sideband I²C                     │
 │               └─ output sink? create DRM connector:                       │
 │                  mst_topology_add_connector()  intel_dp_mst.c:1726         │
 │                  ├─ alloc intel_connector                                  │
 │                  ├─ bind connector->mst.port / connector->mst.dp           │
 │                  ├─[DSC per-port] read DSC decompression caps :1752-1753   │
 │                  │  drm_dp_mst_dsc_aux_for_port() + get_dsc_sink_cap()    │
 │                  └─ attach all fake MST encoders :1759-1766               │
 │                                                                            │
 │  Result: topology tree populated, DRM connectors created for each sink     │
 │                                                                            │
 │   mgr                                                                      │
 │    └─ mst_primary (root branch)                                            │
 │       ├─ port 1 ──► connector (monitor A)   ← DSC/FEC caps known          │
 │       └─ port 2 ──► child branch                                           │
 │                     ├─ port 1 ──► connector (monitor B)                    │
 │                     └─ port 2 ──► connector (monitor C)                    │
 └─────────────────────────────┬───────────────────────────────────────────────┘
                               │ userspace sets mode
                               ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │  Phase 4: Atomic Compute — DSC / FEC / Bandwidth Decision                  │
 │                                                                            │
 │  mst_stream_compute_config()        intel_dp_mst.c:641                     │
 │    │                                                                       │
 │    ├─ negotiate link params (rate, lanes)                                  │
 │    │                                                                       │
 │    ├─[DSC DECISION] bandwidth-driven:                                      │
 │    │  "can we fit without DSC?"                                            │
 │    │  ├─ NO DSC attempt first  :677-689                                    │
 │    │  │  try intel_dp_compute_config_limits(..., dsc=false)                │
 │    │  │  if -EINVAL → dsc_needed = true                                   │
 │    │  ├─ if dsc_needed or joiner_needs_dsc or force_dsc_en:               │
 │    │  │  intel_dp_dsc_compute_config()  intel_dp.c:2362                   │
 │    │  │  └─[FEC DECISION]                                                 │
 │    │  │    pipe_config->fec_enable =                                      │
 │    │  │      intel_dp_needs_8b10b_fec(dsc=true)  intel_dp.c:2342          │
 │    │  │    ▲ Rule: DP(not eDP) + 8b/10b link + DSC → FEC required        │
 │    │  │    ▲ UHBR (128b/132b) has built-in FEC, no explicit enable        │
 │    │  │    ▲ eDP DSC does not require FEC                                 │
 │    │  └─ DSC path  :698-729                                               │
 │    │                                                                       │
 │    ├─[PBN/VCPI] intel_dp_mtp_tu_compute_config()  intel_dp_mst.c:257     │
 │    │  ├─ set mst_state->pbn_div  :290-291                                 │
 │    │  ├─ drm_dp_mst_update_slots()  :293                                  │
 │    │  │  8b/10b: 63 slots, start=1 │ 128b/132b: 64 slots, start=0        │
 │    │  ├─ re-evaluate fec_enable  :301                                     │
 │    │  └─ drm_dp_atomic_find_time_slots()  :406                            │
 │    │     → payload{pbn, time_slots}                                       │
 │    │                                                                       │
 │    └─[BW CHECK] intel_dp_mst_atomic_check_link()  intel_dp_mst.c:895     │
 │       └─ drm_dp_mst_atomic_check_mgr()                                   │
 │          ├─ recurse tree: sum PBN vs port->full_pbn                       │
 │          ├─ check total slots / max_payloads                              │
 │          └─ assign VCPI from payload_mask                                 │
 │          if -ENOSPC: try reducing bpp  :856-875                           │
 └─────────────────────────────┬───────────────────────────────────────────────┘
                               │ atomic check passes
                               ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │  Phase 5: Atomic Commit — Link Training, FEC Enable, Payloads, Pixels      │
 │                                                                            │
 │  hsw_crtc_enable()                  intel_display.c:1651                    │
 │    └─ intel_encoders_pre_enable()   intel_display.c:1358                    │
 │                                                                            │
 │  ── SST DP path ──────────────────────────────────────────────────────────  │
 │  intel_ddi_pre_enable_dp()          intel_ddi.c:2953                       │
 │    └─ tgl_ddi_pre_enable_dp() (example)  intel_ddi.c:2739                 │
 │       ├─ intel_dp_configure_protocol_converter()  intel_dp.c:4049         │
 │       ├─ intel_dp_sink_enable_decompression()     intel_dp.c:3502         │
 │       ├─[FEC SINK] intel_dp_sink_set_fec_ready(true)  intel_ddi.c:2854   │
 │       │  ▲ Tell sink: "FEC coming" — BEFORE training                      │
 │       ├─[TRAINING] intel_dp_start_link_train()                            │
 │       │  intel_dp_link_training.c:1638                                    │
 │       │  ├─ Clock Recovery (CR) training                                  │
 │       │  └─ Channel Equalization (EQ) training                            │
 │       ├─ intel_dp_stop_link_train()                                       │
 │       │  intel_dp_link_training.c:1144                                    │
 │       ├─[FEC HW] intel_ddi_enable_fec()   intel_ddi.c:2873               │
 │       │  ▲ Enable source-side FEC — AFTER training                        │
 │       └─ UHBR only: drm_dp_dpcd_write_payload()  :2877                   │
 │                                                                            │
 │  ── MST path ─────────────────────────────────────────────────────────────  │
 │  mst_stream_pre_enable()            intel_dp_mst.c:1197                    │
 │    ├─ first stream only:                                                   │
 │    │  primary_encoder->pre_enable()  :1231                                │
 │    │  └─ intel_ddi_pre_enable_dp() (same as SST above)                    │
 │    │     ▲ LINK TRAINING happens here (once for all MST streams)          │
 │    │     ▲ FEC sink ready + FEC HW enable also here                       │
 │    ├─ intel_mst_reprobe_topology()  :1184                                 │
 │    │  └─ drm_dp_mst_topology_queue_probe()  :1191                         │
 │    ├─[PAYLOAD 1] drm_dp_add_payload_part1()  :1237                        │
 │    │  ├─ assign vc_start_slot  :3329                                      │
 │    │  ├─ bump mgr->payload_count  :3331                                   │
 │    │  └─ program source payload table (DFP)  :3345                        │
 │    └─ intel_dp_sink_enable_decompression()  :1243                          │
 │                                                                            │
 │  mst_stream_enable()                intel_dp_mst.c:1292                    │
 │    ├─ set VC payload alloc  :1327-1333                                    │
 │    ├─ wait for ACT handled  :1334                                         │
 │    ├─[PAYLOAD 2] drm_dp_add_payload_part2()  :1338                        │
 │    │  └─ send ALLOCATE_PAYLOAD sideband msg to remote branch/sink         │
 │    ├─ intel_enable_transcoder()  :1349                                    │
 │    │  ▲ Pixels start flowing!                                             │
 │    └─ intel_crtc_vblank_on()  :1362                                       │
 │                                                                            │
 │  ═══════════════════════════════════════════════════════════════════════    │
 │  SEQUENCE SUMMARY (MST):                                                   │
 │                                                                            │
 │   1. FEC sink ready   ─┐                                                   │
 │   2. Link training     ├─ main link (once)                                 │
 │   3. FEC HW enable    ─┘                                                   │
 │   4. Payload part1     ─┐                                                  │
 │   5. ACT               ├─ per-stream                                       │
 │   6. Payload part2     ─┘                                                  │
 │   7. Enable transcoder ── pixels flow                                      │
 │                                                                            │
 └─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Key Data Structures

### MST Core (DRM)

```
include/drm/display/drm_dp_mst_helper.h
```

| Struct | Line | Role |
|--------|------|------|
| `drm_dp_mst_port` | :93 | One MST port: may be a sink or link to child branch |
| `drm_dp_mst_branch` | :197 | Branch device in MST tree. `rad[]`+`lct` = sideband address |
| `drm_dp_mst_topology_mgr` | :639 | Top-level manager per source DP port |
| `drm_dp_mst_topology_state` | :593 | Atomic state: payloads, VCPI, slot geometry |
| `drm_dp_mst_atomic_payload` | :535 | Per-stream payload: PBN, slots, VCPI, DSC flag |
| `drm_dp_mst_topology_cbs` | :513 | Driver callbacks: `add_connector`, `poll_hpd_irq` |

### MST Port (abbreviated)

```c
/* include/drm/display/drm_dp_mst_helper.h:93 */
struct drm_dp_mst_port {
    struct kref topology_kref;    /* "alive in topology" refcount */
    struct kref malloc_kref;      /* "memory still valid" refcount */
    u8 port_num;
    bool input, mcs, ddps, ldps;
    u8 pdt;                       /* Peer Device Type */
    u16 full_pbn;                 /* max bandwidth (from ENUM_PATH_RESOURCES) */
    struct drm_dp_mst_branch *mstb;     /* child branch (if branching port) */
    struct drm_dp_mst_branch *parent;   /* parent branch */
    struct drm_connector *connector;    /* DRM connector (if sink) */
    bool fec_capable;             /* from ENUM_PATH_RESOURCES ACK */
};
```

### Topology Manager (abbreviated)

```c
/* include/drm/display/drm_dp_mst_helper.h:639 */
struct drm_dp_mst_topology_mgr {
    struct drm_dp_aux *aux;
    int max_payloads;
    struct drm_dp_mst_branch *mst_primary;   /* root branch */
    u8 dpcd[DP_RECEIVER_CAP_SIZE];
    bool mst_state;
    u8 payload_count, next_start_slot;
    struct work_struct work;              /* probe work */
    struct work_struct delayed_destroy_work;
    struct work_struct up_req_work;       /* process upstream requests */
};
```

### i915 Side

```c
/* intel_display_types.h:1795 */
struct intel_dp {
    struct {
        struct intel_dp_mst_encoder *stream_encoders[I915_MAX_PIPES];
        struct drm_dp_mst_topology_mgr mgr;
        int active_streams;
    } mst;
};

/* intel_display_types.h:564 */
struct intel_connector {
    struct {
        struct drm_dp_mst_port *port;
        struct intel_dp *dp;
    } mst;
};
```

---

## 3. Phase 1: Hotplug Detection & DPCD Read

```
HPD pin asserts (long pulse)
  │
  ▼
intel_hpd_irq_handler()                    intel_hotplug.c:586
  └─ schedule i915_digport_work_func()     intel_hotplug.c:389
     └─ intel_dp_hpd_pulse()              intel_dp.c:6325
        │ long HPD → intel_dp_dpcd_set_probe(true)
        │            intel_dp_read_dprx_caps()
        │            return IRQ_NONE → triggers full detect
        ▼
i915_hotplug_work_func()                   intel_hotplug.c:467
  └─ intel_ddi_hotplug()                  intel_ddi.c:4756
     └─ intel_dp_detect()                intel_dp.c:5827
        ├─ intel_dp_detect_dpcd()
        │  └─ intel_dp_get_dpcd()        intel_dp.c:4414
        │     ├─ DPCD 0x0000-0x000F: receiver caps (rate, lanes, etc.)
        │     ├─ LTTPR caps (if repeaters present)
        │     ├─ sink count
        │     └─ downstream port info
        ├─ intel_dp_detect_dsc_caps()    intel_dp.c:4179
        │  └─ reads DP_DSC_SUPPORT (0x0060) + DP_FEC_CAPABILITY (0x0090)
        └─ intel_dp_mst_detect()         intel_dp.c:4494
           └─ drm_dp_read_mst_cap()     reads DPCD 0x0021 bit 0
```

**What we know after Phase 1:**
- Link rate and lane count capabilities
- Whether sink supports DSC decompression
- Whether sink supports FEC
- Whether sink supports MST

---

## 4. Phase 2: MST Detection & Topology Enable

```
intel_dp_detect() continues...
  └─ intel_dp_mst_configure()            intel_dp.c:4517
     ├─ intel_dp_mst_prepare_probe()     intel_dp_mst.c:2087
     │  (prepares source-side for sideband messaging)
     └─ drm_dp_mst_topology_mgr_set_mst(mgr, true)
        drm_dp_mst_topology.c:3644
        ├─ re-read DPCD caps into mgr->dpcd
        ├─ create root branch device (mst_primary)
        │  lct=1, rad[0]=0
        ├─ write DP_MSTM_CTRL register:
        │  DP_MST_EN | DP_UP_REQ_EN | DP_UPSTREAM_IS_SRC
        ├─ clear payload ID table
        └─ queue probe_work → Phase 3
```

---

## 5. Phase 3: Topology Discovery (Sideband)

```
drm_dp_mst_link_probe_work()
  │
  ├─ CLEAR_PAYLOAD_ID_TABLE (first time)
  │
  └─ drm_dp_check_and_send_link_address(root_branch)
     │
     └─ For each unprobed branch:
        drm_dp_send_link_address(branch)
          │
          ├─ Build LINK_ADDRESS sideband request
          ├─ Queue TX → wait reply
          ├─ Validate branch GUID
          │
          └─ For each port in reply:
             drm_dp_mst_handle_link_address_port()
               │
               ├─ Create/update drm_dp_mst_port
               │
               ├─ ENUM_PATH_RESOURCES
               │  → port->full_pbn (max bandwidth)
               │  → port->fec_capable
               │
               ├─ drm_dp_port_set_pdt()
               │  ├─ PDT=branch → alloc child mstb, recurse
               │  └─ PDT=end-device → register sideband I²C
               │
               └─ Output sink? → driver callback:
                  mst_topology_add_connector()
                    ├─ alloc intel_connector
                    ├─ read per-port DSC decompression caps
                    └─ attach all fake MST encoders

Resulting topology tree:

    intel_dp
      └─ mst.mgr
         └─ mst_primary (root branch, LCT=1)
            ├─ port[1] ── input (upstream, no connector)
            ├─ port[2] ── output sink ── connector "monitor A"
            │              full_pbn=2520, fec_capable=true
            │              DSC caps read
            └─ port[3] ── branch (MST hub)
                           ├─ port[1] ── connector "monitor B"
                           │              full_pbn=1260
                           └─ port[2] ── connector "monitor C"
                                          full_pbn=1260
```

---

## 6. Phase 4: Atomic Compute — DSC/FEC/BW Decision

When userspace requests a mode set:

```
mst_stream_compute_config()              intel_dp_mst.c:641
  │
  ├─ Negotiate link params (rate, lanes)
  │
  ├─ [DSC DECISION] bandwidth-driven:
  │   1. Try without DSC first
  │      intel_dp_compute_config_limits(..., dsc=false)
  │   2. If insufficient bandwidth → dsc_needed = true
  │   3. Also forced if: joiner_needs_dsc or force_dsc_en
  │   4. If dsc_needed:
  │      intel_dp_dsc_compute_config()    intel_dp.c:2362
  │        └─ [FEC DECISION]
  │           fec_enable = intel_dp_needs_8b10b_fec()
  │           ┌────────────────────────────────────────┐
  │           │ FEC Rules:                             │
  │           │  • DP + 8b/10b + DSC  → FEC required  │
  │           │  • UHBR (128b/132b)   → built-in FEC  │
  │           │  • eDP + DSC          → FEC not needed │
  │           └────────────────────────────────────────┘
  │
  ├─ [PBN/VCPI COMPUTATION]
  │   intel_dp_mtp_tu_compute_config()    intel_dp_mst.c:257
  │     ├─ pbn_div = drm_dp_get_vc_payload_bw(rate, lanes)
  │     ├─ drm_dp_mst_update_slots()
  │     │   8b/10b:   63 avail slots, start=1
  │     │   128b/132b: 64 avail slots, start=0
  │     ├─ PBN = drm_dp_calc_pbn_mode(clock, bpp)
  │     └─ time_slots = ceil(PBN / pbn_div)
  │        → stored in drm_dp_mst_atomic_payload
  │
  └─ [BANDWIDTH VALIDATION]
     intel_dp_mst_atomic_check_link()     intel_dp_mst.c:895
       └─ drm_dp_mst_atomic_check_mgr()
          ├─ Recurse topology tree:
          │   sum downstream PBN ≤ port->full_pbn?
          ├─ Total slots ≤ available?
          ├─ Payload count ≤ max_payloads?
          └─ Assign VCPI IDs from payload_mask bitmap
          If -ENOSPC → try reducing bpp (:856-875)
```

---

## 7. Phase 5: Atomic Commit — Training, FEC, Payloads, Pixels

```
hsw_crtc_enable()                        intel_display.c:1651
  └─ intel_encoders_pre_enable()

MST commit sequence (per CRTC):

  mst_stream_pre_enable()                intel_dp_mst.c:1197
    │
    ├─ [FIRST STREAM ONLY] primary_encoder->pre_enable()  :1231
    │   └─ intel_ddi_pre_enable_dp()     intel_ddi.c:2953
    │      ├─ configure protocol converter
    │      ├─ enable sink decompression
    │      │
    │      ├─ ① FEC SINK READY
    │      │  intel_dp_sink_set_fec_ready(true)  :2854
    │      │  ▲ writes DPCD 0x0120 to tell sink "FEC coming"
    │      │
    │      ├─ ② LINK TRAINING
    │      │  intel_dp_start_link_train()  :2866
    │      │  ├─ Clock Recovery (CR) phase
    │      │  │  adjust voltage swing & pre-emphasis
    │      │  └─ Channel Equalization (EQ) phase
    │      │     adjust EQ, verify symbol lock
    │      │  intel_dp_stop_link_train()   :2873
    │      │
    │      └─ ③ FEC HW ENABLE
    │         intel_ddi_enable_fec()       :2873
    │         ▲ source-side FEC engine activated
    │
    ├─ reprobe topology
    │  drm_dp_mst_topology_queue_probe()  :1191
    │
    ├─ ④ PAYLOAD PART 1
    │  drm_dp_add_payload_part1()         :1237
    │  ├─ assign vc_start_slot
    │  ├─ increment payload_count
    │  └─ program source-side payload table (DFP registers)
    │
    └─ enable sink decompression (DSC)    :1243

  mst_stream_enable()                    intel_dp_mst.c:1292
    │
    ├─ ⑤ TRIGGER ACT
    │  set VC payload alloc              :1327-1333
    │  wait for ACT handled              :1334
    │
    ├─ ⑥ PAYLOAD PART 2
    │  drm_dp_add_payload_part2()        :1338
    │  └─ send ALLOCATE_PAYLOAD sideband message
    │     to remote branch/sink along the topology
    │
    ├─ ⑦ ENABLE TRANSCODER
    │  intel_enable_transcoder()          :1349
    │  ▲ Pixels start flowing through the VC!
    │
    └─ intel_crtc_vblank_on()            :1362
```

**Disable is the reverse:**
```
mst_stream_post_disable()                intel_dp_mst.c
  ├─ drm_dp_remove_payload_part1()       :1077
  │  tear down remote + DFP allocation
  ├─ wait ACT
  ├─ drm_dp_remove_payload_part2()       :1089
  │  compact start slots, clear bookkeeping
  └─ last stream: primary_encoder->post_disable()
     → disable FEC, power down link
```

---

## 8. Topology Maintenance (HPD IRQ)

After initial setup, short HPD pulses maintain the topology:

```
short HPD IRQ
  └─ intel_dp_hpd_pulse()               intel_dp.c:6325
     └─ intel_dp_check_mst_status()     intel_dp.c:5109
        └─ intel_dp_mst_hpd_irq()      intel_dp.c:5064
           └─ drm_dp_mst_hpd_irq_handle_event()  topology.c:4232
              ├─ DOWN_REP_MSG_RDY → parse sideband reply
              └─ UP_REQ_MSG_RDY  → parse upstream request
                 └─ queue up_req_work
                    drm_dp_mst_process_up_req()  :4052
                    ├─ CONNECTION_STATUS_NOTIFY
                    │  → handle_conn_stat() → reprobe/hotplug
                    └─ RESOURCE_STATUS_NOTIFY
                       → logged (TODO in this tree)
```

---

## 9. Refcounting Model

Two separate reference counts protect MST objects:

```
┌──────────────────────────────────────────────────────────────────┐
│  topology_kref: "this object is part of the live MST topology"  │
│  → internal to MST helpers                                      │
│  → when 0: object removed from topology, queued for destroy     │
│                                                                  │
│  malloc_kref: "this memory allocation must remain valid"         │
│  → held by payloads, atomic state, driver references            │
│  → when 0: memory actually freed                                │
│                                                                  │
│  Why two? A port can be unplugged (topology_kref=0) while a     │
│  payload teardown still references it (malloc_kref>0).          │
│                                                                  │
│  payload ──(malloc_ref)──► port ──(malloc_ref)──► parent branch │
└──────────────────────────────────────────────────────────────────┘
```

---

## Quick Reference: Timing of Each Decision

| Event | When | Where |
|-------|------|-------|
| **DPCD read** | hotplug detect | `intel_dp_get_dpcd()` intel_dp.c:4414 |
| **DSC caps read** | hotplug detect (SST) / connector create (MST) | `intel_dp_detect_dsc_caps()` / `mst_topology_add_connector()` |
| **FEC caps read** | hotplug detect (SST) / ENUM_PATH_RESOURCES (MST) | `intel_dp_get_dsc_sink_cap()` / `drm_dp_send_enum_path_resources()` |
| **MST detected** | hotplug detect | `intel_dp_mst_detect()` intel_dp.c:4494 |
| **Topology discovery** | after MST enable (async) | `drm_dp_mst_link_probe_work()` |
| **DSC decision** | atomic compute_config | `intel_dp_dsc_compute_config()` intel_dp.c:2362 |
| **FEC decision** | atomic compute_config | `intel_dp_needs_8b10b_fec()` intel_dp.c:2342 |
| **PBN/VCPI calc** | atomic compute_config | `intel_dp_mtp_tu_compute_config()` intel_dp_mst.c:257 |
| **BW validation** | atomic check | `drm_dp_mst_atomic_check_mgr()` |
| **FEC sink ready** | commit pre_enable | `intel_dp_sink_set_fec_ready()` |
| **Link training** | commit pre_enable | `intel_dp_start_link_train()` |
| **FEC HW enable** | commit pre_enable (after training) | `intel_ddi_enable_fec()` |
| **Payload program** | commit pre_enable + enable | `drm_dp_add_payload_part1/2()` |
| **Pixels flow** | commit enable | `intel_enable_transcoder()` |

---

## 10. Appendix: DP AUX Channel

The DP AUX (Auxiliary) channel is a dedicated low-speed sideband bus on the
DisplayPort connector — it is **not** USB. On native DP connectors it has its
own pins; on USB-C it reuses the SBU1/SBU2 pins after DP Alt Mode is entered.

### Physical Layer

```
Native DP Connector:    AUX_CH+, AUX_CH-  (dedicated pins)
USB-C in DP Alt Mode:   SBU1, SBU2        (repurposed as AUX_CH+/-)

Speed: 1 Mbps, Manchester-encoded, half-duplex differential pair
Max payload per transaction: 16 bytes data
```

### Transaction Types

| Type | Purpose | Example |
|------|---------|---------|
| **Native AUX Read** | Read DPCD registers | Read receiver caps (0x0000) |
| **Native AUX Write** | Write DPCD registers | Set link rate, lane count |
| **I2C-over-AUX Read** | Read I2C device via AUX | Read EDID from monitor |
| **I2C-over-AUX Write** | Write I2C device via AUX | Rare, some HDCP use |

### i915 AUX Implementation

```
drm_dp_dpcd_read(aux, addr, buf, len)         DRM helper
  └─ aux->transfer()                          vtable callback
     = intel_dp_aux_transfer()                intel_dp_aux.c:483
       └─ intel_dp_aux_xfer()                intel_dp_aux.c:237
          ├─ grab AUX power domain wakeref
          ├─ write request into GPU MMIO registers:
          │   ch_ctl  = DP_AUX_CH_CTL         (control: start, size, timeout)
          │   ch_data = DP_AUX_CH_DATA[0-4]   (up to 20 bytes payload)
          ├─ kick hardware: set SEND bit
          ├─ poll/wait for DONE bit
          └─ read reply from ch_data registers
```

The GPU has a dedicated **AUX controller** per DP port — the driver writes
addresses and data into MMIO registers, the controller handles Manchester
encoding and the electrical protocol. No software bit-banging.
