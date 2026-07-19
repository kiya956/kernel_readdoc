# DisplayPort / USB-C / MST Handbook

> This document summarizes our discussion on DisplayPort MST, AUX, DPCD,
> HPD, the Linux DRM MST driver, and the underlying USB-C / Thunderbolt
> hardware architecture. It is intended to grow into a complete reference.

------------------------------------------------------------------------

# Chapter 1 - Protocol Concepts (Session 1)

## 1.1 Layered Architecture

    Linux DRM MST Driver
            │
            ▼
    MST Sideband Protocol
            │
            ▼
    DPCD Registers / Buffers
            │
            ▼
    AUX Protocol
            │
            ▼
    AUX Physical Wires

Important distinctions:

-   **AUX** is the transport protocol.
-   **DPCD** is an address space.
-   **ESI** is a status register within DPCD.
-   **MST Sideband** is an upper-layer protocol carried through AUX
    accesses.

------------------------------------------------------------------------

## 1.2 DisplayPort Channels

    DisplayPort Link
    ├── Main Link
    │     ├── Video
    │     ├── Audio
    │     └── MST Payload
    │
    └── AUX
          ├── DPCD Read/Write
          ├── I2C-over-AUX
          ├── Link Training
          └── MST Sideband Messages

------------------------------------------------------------------------

## 1.3 AUX Protocol

Each AUX transaction consists of:

    Request
        │
        ▼
    Reply

Reply types:

-   ACK
-   NACK
-   DEFER

Maximum payload per AUX transaction:

    16 bytes

Large transfers are split into multiple AUX transactions.

------------------------------------------------------------------------

## 1.4 DPCD

DPCD is simply a register space.

Examples:

-   Receiver capability
-   Link status
-   ESI
-   MST request/reply buffers

DPCD itself is **not** a protocol.

------------------------------------------------------------------------

## 1.5 ESI

ESI (Event Status Indicator) is stored in DPCD.

HPD IRQ tells the source:

> "Go read ESI."

Typical flow:

    Branch
        │
    writes reply
        │
    sets ESI bit
        │
    HPD IRQ
        │
    GPU reads ESI through AUX

HPD carries **no data**.

------------------------------------------------------------------------

## 1.6 MST Sideband

Relationship:

    MST Message
          │
          ▼
    Stored into DPCD sideband buffer
          │
          ▼
    Transferred through AUX

Typical downstream request:

    Source

    ↓

    Write DOWN_REQ

    ↓

    Branch processes request

    ↓

    Branch writes DOWN_REP

    ↓

    HPD IRQ

    ↓

    Read ESI

    ↓

    Read DOWN_REP

------------------------------------------------------------------------

## 1.7 Topology Discovery

Example topology:

    GPU
     │
     ▼
    Branch A
    ├── Monitor A
    └── Branch B
          ├── Monitor B
          └── Monitor C

Discovery:

1.  Detect MST capability.
2.  Send LINK_ADDRESS to root.
3.  Create Branch A object.
4.  Discover downstream branches recursively.
5.  Build topology tree.

------------------------------------------------------------------------

## 1.8 Why Maintain a Topology Tree?

The topology tree is **not required by hardware**.

Its advantages are:

-   Cache discovery results.
-   Maintain persistent object identity.
-   Allow incremental add/remove.
-   Simplify connector management.
-   Generate route addresses (RAD).

Hardware only needs the final configuration.

------------------------------------------------------------------------

## 1.9 Route Address (RAD)

Examples:

    Root branch
    RAD = []

    Branch behind Port 2
    RAD = [2]

    Branch behind A.Port2 → B.Port3
    RAD = [2,3]

------------------------------------------------------------------------

## 1.10 Driver Objects

Important Linux structures:

    drm_dp_mst_topology_mgr
    drm_dp_mst_branch
    drm_dp_mst_port

They represent:

-   MST manager
-   Branch devices
-   Downstream ports

------------------------------------------------------------------------

## 1.11 Suggested Study Order

1.  Topology discovery
2.  Sideband message transmission
3.  Connector creation
4.  Payload allocation
5.  ACT handling
6.  Hotplug removal
7.  Thunderbolt / USB4 integration

------------------------------------------------------------------------

# Chapter 2 - Hardware Architecture

## 2.1 System Diagram

> For a USB-C DP/MST dock there are **three independent paths**, not one
> cable of parallel wires. Note in particular that HPD for USB-C DP Alt
> Mode is *not* a raw wire from the dock to the GPU HPD pin — it is
> reported over the CC/PD/UCSI **control path** and turned into a logical
> hotplug event in software. (See §2.4 for the native-DP contrast.)

``` text
                              CPU / SoC
+--------------------------------------------------------------------------------+
|                                                                                |
|  +------------------+        (1) DP data path                                  |
|  | Display Engine   |                                                          |
|  | (CRTC/Pipes/DC)  |                                                          |
|  +--------+---------+                                                          |
|           | Main Link / AUX                                                    |
|           v                                                                    |
|  +------------------+     +-------------------------+                          |
|  | DP AUX Ctrl      |---->| Type-C mux / retimer    |----+                     |
|  +------------------+     | (or USB4/DP Prot Adpt)  |    | DP lanes + AUX/SBU  |
|           ^               +-------------------------+    |                     |
|           | reads TC/FIA/PHY live-status registers       |                     |
|           | (i915 tc_phy_hpd_live_status)                |                     |
|                                                          |                     |
|  +------------------+        (2) USB data path           |                     |
|  | xHCI USB Host    |----------------------------------->|  USB2/USB3          |
|  +------------------+                                     |                     |
|                                                          |                     |
|   +---------------+          (3) Type-C control path      |                     |
|   | EC            |                                       |                     |
|   +-------+-------+                                       |                     |
|           | UCSI  (interrupt / ACPI / mailbox)            |                     |
|   +-------v-------+                                       |                     |
|   | TCPC / PD Ctl |<----------------------------+         |                     |
|   +-------+-------+   CC1/CC2 / PD / DP Alt Mode |         |                     |
|           |          status: HPD_STATE, IRQ_HPD |         |                     |
+-----------|----------------------------------------------|---------------------+
            |                                     |         |
            | to Linux UCSI / Type-C /            |         |
            | DP-altmode -> DRM OOB hotplug       |         |
                                                  |         |
      +-------------------------------------------+---------v----------+
      |                    USB-C Connector                             |
      |   CC1/CC2   |   SBU1/SBU2 (AUX)   |   High-speed TX/RX lanes    |
      +-----------------------------+----------------------------------+
                                    |
==================== Cable ==========|=======================================
                                    |
      +-----------------------------v-------------------------+
      |                     Dock / MST Hub                    |
      | +-----------+     +----------------------+            |
      | | PD Ctrl   |     | USB4 Router          |            |
      | | (reports  |     +----------+-----------+            |
      | |  HPD via  |                | DP Tunnel              |
      | |  CC/PD)   |       +--------v---------+              |
      | +-----------+       | MST Hub/Branch   |              |
      |                     +----+---------+---+              |
      |                          |         |                  |
      +--------------------------|---------|------------------+
                                 |         |
                           +-----v--+ +----v---+
                           |Monitor1| |Monitor2|
                           +--------+ +--------+
```

The three paths:

    1. DP data path : GPU DP lanes/AUX -> mux/retimer -> dock MST hub
    2. USB data path: dock USB devices -> xHCI
    3. Control path : CC/PD/DP Alt Mode/HPD status -> EC/PD/UCSI -> Linux

## 2.2 Signal Ownership

  Signal      Purpose                    Owner
  ----------- -------------------------- --------------------
  Main Link   Video/Audio/MST payload    GPU Display Engine
  AUX         DPCD, Link Training, MST   AUX Controller (via SBU switch)
  HPD (USB-C) DP Alt Mode HPD_STATE/     Dock -> CC/PD -> EC/UCSI -> DRM
              IRQ_HPD, reported as a     (a control-path status, not a
              logical hotplug event      raw wire to the GPU)
  HPD (native) Physical hotplug wire     Sink / MST Branch -> GPU HPD pin
  CC          USB-C orientation & PD     TCPC
  UCSI        EC ↔ OS                    EC

## 2.3 Notes

-   AUX transports DPCD accesses (over the SBU pins in DP Alt Mode).
-   DPCD is an address space.
-   ESI is a DPCD register.
-   HPD IRQ tells the GPU to read ESI.
-   MST sideband messages are stored in DPCD buffers and transferred
    over AUX.
-   **UCSI status is not DPCD ESI.** UCSI/DP-altmode reports whether the
    USB-C DP path is alive and whether the partner signalled HPD
    high/low/IRQ; the GPU still reads DPCD ESI over AUX to learn what
    changed in the MST topology.

## 2.4 HPD: native DP wire vs. USB-C control path

The single `HPD` line in older diagrams is only accurate for **native
DisplayPort**, where HPD is a real low-speed signal wire from the
sink/hub back to the GPU HPD input pin:

``` text
Native DP
+-------------------+                         +-------------------+
| GPU / DP Source   |                         | Monitor / MST Hub |
| HPD input    <----+---- HPD physical wire --| HPD output        |
| AUX CH      <---->+---- AUX +/-  ----------->| DPCD / AUX        |
| Main Link TX ----+---- DP lanes ----------->| Main Link RX      |
+-------------------+                         +-------------------+

  "GPU sees HPD low"  ≈  "the HPD wire level is low"
```

For **USB-C DP Alt Mode**, do *not* model HPD as a raw wire to the GPU.
The HPD information travels the control path instead:

``` text
Dock / MST hub
    -> USB-C CC/PD / DP Alt Mode status (HPD_STATE, IRQ_HPD)
    -> EC / PD / UCSI controller
    -> Linux UCSI / Type-C / DP-altmode driver
    -> drm_connector_oob_hotplug_event()   (logical hotplug)
    -> GPU / i915 hotplug + detect path
```

Additionally, i915 validates the Type-C path in hardware via
`tc_phy_hpd_live_status()` (TC/FIA/PHY live-status bits such as
`TC_PORT_DP_ALT` / `TC_PORT_TBT_ALT` / `TC_PORT_LEGACY`). This is a
third, separate source from UCSI status and from DPCD ESI:

    UCSI status  : "USB-C connector / DP Alt Mode / HPD state changed"
    TC live bits : "is the Type-C display path usable by i915 right now?"
    DPCD ESI     : "the MST hub has a sideband message ready" (over AUX)
