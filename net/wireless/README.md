# Linux Kernel cfg80211 — Wireless Configuration API

## Overview

**cfg80211** is the Linux kernel's **wireless configuration subsystem**. It
provides the unified API between userspace wireless tools (wpa_supplicant, iw,
NetworkManager) and wireless device drivers. cfg80211 handles:

- **Regulatory domain** management (allowed channels/power per country)
- **Scanning** (active and passive scan requests, results, BSS management)
- **Connection management** (connect, associate, authenticate, disconnect)
- **AP/P2P/mesh mode** operation
- **Key management** (WEP, WPA/WPA2/WPA3 keys)
- **Power management** and **QoS**

Drivers implement `struct cfg80211_ops` and can be either **mac80211-based**
(softMAC — kernel handles MLME) or **fullmac** (firmware handles MLME).

Source: `net/wireless/`, `include/net/cfg80211.h`, `include/uapi/linux/nl80211.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│   wpa_supplicant          iw dev wlan0 scan                     │
│     ├── WPA/WPA2/WPA3     iw dev wlan0 connect MyAP             │
│     ├── 802.1X/EAP        hostapd (AP mode)                     │
│     └── P2P/Wi-Fi Direct  NetworkManager / connman              │
└──────────────────────────────┬──────────────────────────────────┘
                               │  Generic Netlink (NL80211)
┌──────────────────────────────▼──────────────────────────────────┐
│                     NL80211 (nl80211.c)                          │
│                                                                 │
│  Generic Netlink family "nl80211"                                │
│  Commands: NL80211_CMD_TRIGGER_SCAN, _CONNECT, _AUTHENTICATE,   │
│            _ASSOCIATE, _START_AP, _SET_KEY, _REG_CHANGE, ...     │
│  Events:   NL80211_CMD_SCAN_ABORTED, _CONNECT_RESULT,           │
│            _DISCONNECT, _REG_CHANGE, _NEW_STATION, ...           │
│  nl80211_send_scan_msg() — notify scan results to userspace      │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     CFG80211 CORE (core.c, scan.c, mlme.c)       │
│                                                                 │
│  struct wiphy — represents a physical wireless device            │
│  struct wireless_dev (wdev) — per-interface (STA, AP, P2P, etc.) │
│                                                                 │
│  Scan:     cfg80211_scan_done(), BSS list management             │
│  Connect:  cfg80211_connect_result(), cfg80211_disconnected()    │
│  Mgmt:     cfg80211_rx_mgmt() — receive management frames       │
│  Reg:      regulatory_hint(), regulatory domain updates          │
│  BSS:      cfg80211_put_bss(), cfg80211_inform_bss()             │
│  Info:     cfg80211_get_drvinfo() — ethtool driver info          │
└──────────────────────────────┬──────────────────────────────────┘
                               │ struct cfg80211_ops callbacks
              ┌────────────────┴─────────────────────┐
              │                                      │
┌─────────────▼────────────────┐  ┌──────────────────▼──────────┐
│    mac80211 (softMAC)         │  │    fullmac driver            │
│    net/mac80211/              │  │    (firmware-based MLME)     │
│                               │  │                              │
│  Kernel handles:              │  │  Firmware handles:           │
│  - MLME state machine         │  │  - Scan, auth, assoc         │
│  - Rate control               │  │  - Key installation          │
│  - Aggregation (A-MPDU)       │  │  - Power save                │
│  - Power save                 │  │                              │
│  - TX/RX queues               │  │  Examples: brcmfmac, mwifiex│
│                               │  │  wil6210, wcn36xx            │
│  Examples: iwlwifi, ath9k,    │  │                              │
│  ath10k, mt76, rtw88/rtw89    │  │                              │
└─────────────┬────────────────┘  └──────────────┬──────────────┘
              │                                   │
┌─────────────▼───────────────────────────────────▼──────────────┐
│                     WIRELESS HARDWARE                            │
│   WiFi chipset  ──  antenna  ──  802.11 radio (2.4/5/6 GHz)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. NL80211 — Netlink Interface (`nl80211.c`)

The userspace API for all wireless operations:

- **Scan**: `NL80211_CMD_TRIGGER_SCAN` → driver scans → `cfg80211_scan_done()`
  → `nl80211_send_scan_msg()` notifies userspace.
- **Connect**: `NL80211_CMD_CONNECT` → driver authenticates and associates →
  `cfg80211_connect_result()` reports success/failure.
- **AP mode**: `NL80211_CMD_START_AP` → driver beacons → stations associate.
- **Regulatory**: `NL80211_CMD_REG_CHANGE` notifies channel/power updates.

### 2. Scan Flow

```
userspace (iw scan)
    │
    ▼ NL80211_CMD_TRIGGER_SCAN
cfg80211: validate request, build cfg80211_scan_request
    │
    ▼ ops->scan()
driver: program hardware to scan channels
    │
    ▼ cfg80211_inform_bss() per BSS found
cfg80211: add/update BSS entries in internal list
    │
    ▼ cfg80211_scan_done()
cfg80211 → nl80211: NL80211_CMD_NEW_SCAN_RESULTS
    │
    ▼
userspace: reads BSS list via NL80211_CMD_GET_SCAN
```

### 3. Connect / Associate Flow

```
userspace (wpa_supplicant)
    │
    ▼ NL80211_CMD_CONNECT (or AUTHENTICATE + ASSOCIATE)
cfg80211: validate, call ops->connect() or ops->auth() + ops->assoc()
    │
    ▼
driver/firmware: 802.11 auth + assoc exchange
    │
    ▼ cfg80211_connect_result()
cfg80211: update wdev state, notify nl80211
    │
    ▼ NL80211_CMD_CONNECT
userspace: connection established, begin 4-way handshake (WPA)
```

### 4. Regulatory Domain (`reg.c`)

- **CRDA / wireless-regdb**: Provides per-country channel/power rules.
- **`regulatory_hint()`**: Drivers or userspace hint the current country code.
- **Self-managed regulatory**: Some drivers (e.g., Intel) manage their own
  regulatory database from firmware.
- Channels are enabled/disabled and max TX power is set per regulatory domain.

### 5. BSS Management (`scan.c`)

- `struct cfg80211_bss`: Represents a discovered access point.
- `cfg80211_inform_bss()`: Drivers report found BSSes.
- `cfg80211_put_bss()`: Release reference to a BSS entry.
- BSS entries are aged out if not refreshed within a timeout.

### 6. Wiphy Registration (`core.c`)

- `wiphy_new_nm()`: Allocate a new wiphy with a given name.
- `wiphy_register()`: Register the wiphy with cfg80211 and create sysfs entries.
- Creates `/sys/class/ieee80211/phyN/` with device attributes.

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct wiphy` | Physical wireless device: bands, channels, capabilities, regulatory |
| `struct wireless_dev` | Per-interface: type (STA/AP/P2P/mesh), state, current BSS |
| `struct cfg80211_ops` | Driver vtable: scan, connect, auth, start_ap, set_key, etc. |
| `struct cfg80211_scan_request` | Scan parameters: channels, SSIDs, IEs, flags |
| `struct cfg80211_bss` | Discovered BSS: BSSID, frequency, signal, IEs |
| `struct cfg80211_connect_resp_params` | Connect result: status, BSS, IEs |
| `struct ieee80211_channel` | Channel: frequency, flags, max power |
| `struct ieee80211_supported_band` | Band: channels, rates, HT/VHT/HE capabilities |

## Key Functions

| Function | Purpose |
|---|---|
| `cfg80211_scan_done()` | Notify cfg80211 that a scan has completed |
| `cfg80211_connect_result()` | Report connection success/failure to cfg80211 |
| `cfg80211_rx_mgmt()` | Pass received management frame to cfg80211 |
| `cfg80211_disconnected()` | Notify cfg80211 of a disconnection event |
| `regulatory_hint()` | Hint regulatory domain (country code) |
| `cfg80211_put_bss()` | Release reference to a BSS entry |
| `cfg80211_inform_bss()` | Report a discovered BSS to cfg80211 |
| `nl80211_send_scan_msg()` | Send scan result notification to userspace |
| `wiphy_new_nm()` | Allocate new wiphy with name |
| `wiphy_register()` | Register wiphy with cfg80211 |
| `cfg80211_get_drvinfo()` | ethtool driver info callback |

## Key Source Files

| File | Purpose |
|---|---|
| `net/wireless/core.c` | cfg80211 module init, wiphy registration |
| `net/wireless/nl80211.c` | NL80211 Netlink interface |
| `net/wireless/scan.c` | Scan management and BSS list |
| `net/wireless/mlme.c` | MLME (auth/assoc/deauth) handling |
| `net/wireless/sme.c` | Software SME (connect/disconnect) |
| `net/wireless/reg.c` | Regulatory domain management |
| `net/wireless/util.c` | Utility functions |
| `net/wireless/chan.c` | Channel/bandwidth validation |
| `net/wireless/ethtool.c` | ethtool integration |
| `include/net/cfg80211.h` | Main cfg80211 API header |
| `include/uapi/linux/nl80211.h` | NL80211 UAPI constants |

---

## Analogy

cfg80211 is like a **universal remote control protocol for WiFi radios**:

- **wiphy** is the actual radio hardware — each has different buttons and
  capabilities (supported bands, features, antenna count).
- **cfg80211** is the universal remote standard — it defines a common set of
  commands (scan, connect, set key) that all radios must understand.
- **nl80211** is the IR blaster — it carries commands from your couch
  (userspace) to the radio using a standard signal format (Netlink).
- **mac80211 drivers** are radios where the remote does most of the work
  (the kernel handles timing, retries, and state).
- **fullmac drivers** are smart radios with their own brain (firmware) — the
  remote just says "connect" and the radio figures out the details.
- **Regulatory domain** is like broadcast licensing — you can only use certain
  frequencies at certain power levels depending on your country.

---

## References

- `include/net/cfg80211.h` — cfg80211 API
- `include/uapi/linux/nl80211.h` — NL80211 UAPI
- `Documentation/networking/regulatory.rst`
- `net/wireless/` — cfg80211 implementation
- `net/mac80211/` — softMAC implementation
- IEEE 802.11 specification
