# mac80211 — Linux Software MAC Layer for WiFi

## Overview

`mac80211` is the Linux kernel's software **MAC (Media Access Control)** layer
for IEEE 802.11 (WiFi).  It sits between the configuration layer (`cfg80211` /
`nl80211`) and the hardware-specific WiFi driver, implementing everything that
can be done in software rather than in firmware or hardware:

- **MLME** (MAC Layer Management Entity) — association, authentication,
  scanning, power-save negotiation.
- **Frame TX / RX** — queueing, sequence numbering, fragmentation / reassembly.
- **Encryption** — WEP, TKIP, CCMP (WPA2), GCMP (WPA3) key management and
  per-frame encrypt / decrypt.
- **A-MPDU / A-MSDU aggregation** — block-ack session setup and teardown,
  reorder buffer management.
- **Rate control** — pluggable algorithm (minstrel_ht by default) that picks
  the best MCS/rate for each station.
- **Power-save** — tracking station doze states, buffering frames for sleeping
  clients (AP mode).

Drivers only need to implement the `struct ieee80211_ops` callbacks for
hardware-specific tasks (register/start/stop, configure channel, push frames
to the radio, read frames from the radio).

---

## Architecture Diagram

```
 ┌──────────────────────────────────────────────────────────────┐
 │                      User-space (iw, wpa_supplicant)        │
 └────────────────────────────┬─────────────────────────────────┘
                              │  nl80211 / cfg80211 netlink
 ┌────────────────────────────▼─────────────────────────────────┐
 │                        cfg80211                              │
 │        (regulatory, scan results, BSS list, wiphy mgmt)      │
 └────────────────────────────┬─────────────────────────────────┘
                              │  cfg80211_ops  →  mac80211_ops
 ┌────────────────────────────▼─────────────────────────────────┐
 │                        mac80211                              │
 │   ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────────┐  │
 │   │  TX path │  │  RX path │  │  MLME  │  │  Rate ctrl   │  │
 │   └────┬─────┘  └────▲─────┘  └────────┘  └──────────────┘  │
 │        │             │                                       │
 │   ┌────▼─────────────┴─────────────────────────────────────┐ │
 │   │  Crypto (CCMP/GCMP/TKIP)   Aggregation (A-MPDU)       │ │
 │   └────┬─────────────▲────────────────────────────────────-┘ │
 └────────┼─────────────┼──────────────────────────────────────-┘
          │             │   struct ieee80211_ops callbacks
 ┌────────▼─────────────┴──────────────────────────────────────┐
 │              WiFi Driver  (e.g. iwlwifi, ath11k, mt76)      │
 │                     ieee80211_ops implementation             │
 └────────────────────────────┬─────────────────────────────────┘
                              │   PCIe / USB / SDIO
 ┌────────────────────────────▼─────────────────────────────────┐
 │                    WiFi Hardware / Firmware                   │
 └──────────────────────────────────────────────────────────────┘
```

---

## TX Path (Transmit)

When the network stack hands a packet to a WiFi interface the following
call-chain executes inside mac80211:

```
net_device->ndo_start_xmit
  └─► ieee80211_subif_start_xmit()          [net/mac80211/tx.c]
        ├─ classify / select hardware queue
        ├─ add 802.11 header (convert from 802.3)
        └─► ieee80211_tx()                   [net/mac80211/tx.c]
              ├─ TX handlers (sequence number, fragmentation,
              │               encryption, A-MPDU check)
              ├─ rate_control_get_rate()      [rate selection]
              └─► drv_tx()                   [driver_ops → ieee80211_ops.tx]
                    └─► hardware enqueue
```

Key TX handlers (executed in order via `ieee80211_tx_handlers`):

| Handler | Purpose |
|---|---|
| `ieee80211_tx_h_dynamic_ps` | Dynamic power-save handling |
| `ieee80211_tx_h_check_assoc` | Verify association state |
| `ieee80211_tx_h_ps_buf` | Buffer frames for sleeping STAs |
| `ieee80211_tx_h_select_key` | Choose encryption key |
| `ieee80211_tx_h_michael_mic_add` | TKIP Michael MIC |
| `ieee80211_tx_h_sequence` | Assign sequence numbers |
| `ieee80211_tx_h_fragment` | Fragment oversize frames |
| `ieee80211_tx_h_encrypt` | Per-frame encryption (CCMP/GCMP/TKIP) |
| `ieee80211_tx_h_rate_ctrl` | Attach rate-control info |

---

## RX Path (Receive)

The driver calls `ieee80211_rx()` (or `ieee80211_rx_napi()`) to hand received
frames up to mac80211:

```
driver interrupt / NAPI poll
  └─► ieee80211_rx() / ieee80211_rx_napi()     [net/mac80211/rx.c]
        └─► __ieee80211_rx_handle_packet()
              ├─ RX handlers (in order):
              │   ├─ ieee80211_rx_h_check_dup     — duplicate detection
              │   ├─ ieee80211_rx_h_decrypt        — decrypt frame
              │   ├─ ieee80211_rx_h_check_more_data
              │   ├─ ieee80211_rx_h_sta_process    — update STA statistics
              │   ├─ ieee80211_rx_h_defragment     — reassemble fragments
              │   ├─ ieee80211_rx_h_michael_mic_verify — TKIP MIC check
              │   ├─ ieee80211_rx_h_amsdu          — de-aggregate A-MSDU
              │   └─ ieee80211_rx_h_data           — convert to 802.3
              │
              ├─ management frames ──► ieee80211_sta_rx_queued_mgmt()
              │                         └─ MLME processing (assoc, auth, beacon)
              │
              └─ data frames ──► netif_receive_skb()
                                  └─ into the network stack
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `struct ieee80211_hw` | `include/net/mac80211.h` | Per-hardware context exposed to drivers; holds capabilities, queues, wiphy pointer |
| `struct ieee80211_local` | `net/mac80211/ieee80211_i.h` | Internal per-hardware state; embeds `ieee80211_hw`, workqueues, scan state |
| `struct ieee80211_vif` | `include/net/mac80211.h` | Virtual interface (STA, AP, mesh, monitor); one per netdev |
| `struct ieee80211_sub_if_data` | `net/mac80211/ieee80211_i.h` | Internal VIF state; embeds `ieee80211_vif`, links to `local` |
| `struct sta_info` | `net/mac80211/sta_info.h` | Per-station context: RX/TX stats, aggregation sessions, keys, rates |
| `struct ieee80211_key` | `net/mac80211/key.h` | Encryption key (CCMP/GCMP/TKIP/WEP); per-STA or per-VIF |
| `struct ieee80211_ops` | `include/net/mac80211.h` | Driver callback table (tx, start, stop, config, add_interface …) |
| `struct rate_control_ref` | `net/mac80211/rate.h` | Handle to the active rate-control algorithm |
| `struct tid_ampdu_rx` | `net/mac80211/sta_info.h` | Per-TID RX A-MPDU reorder buffer |
| `struct tid_ampdu_tx` | `net/mac80211/sta_info.h` | Per-TID TX A-MPDU aggregation state |

---

## Key Functions

| Function | File | Description |
|---|---|---|
| `ieee80211_alloc_hw()` | `main.c` | Allocate `ieee80211_hw` + driver private area |
| `ieee80211_register_hw()` | `main.c` | Register the device with cfg80211 / mac80211; creates netdevs |
| `ieee80211_unregister_hw()` | `main.c` | Tear down and unregister |
| `ieee80211_free_hw()` | `main.c` | Free the hw structure |
| `ieee80211_rx()` | `rx.c` | Main RX entry point for drivers |
| `ieee80211_rx_napi()` | `rx.c` | NAPI-aware RX entry point |
| `ieee80211_tx()` | `tx.c` | Internal TX processing |
| `ieee80211_subif_start_xmit()` | `tx.c` | `ndo_start_xmit` callback |
| `ieee80211_key_alloc()` | `key.c` | Allocate a new encryption key |
| `ieee80211_key_link()` | `key.c` | Install key into STA / VIF |
| `ieee80211_sta_rx_queued_mgmt()` | `mlme.c` | Process queued management frames (STA mode) |
| `rate_control_get_rate()` | `rate.c` | Ask rate-control algorithm for TX rate |
| `ieee80211_start_tx_ba_session()` | `agg-tx.c` | Initiate A-MPDU TX aggregation |
| `ieee80211_process_addba_request()` | `agg-rx.c` | Handle incoming ADDBA request |

---

## Frame Processing Details

### Encryption

mac80211 manages crypto keys (`struct ieee80211_key`) and applies per-frame
encryption / decryption unless the hardware advertises the
`IEEE80211_HW_SW_CRYPTO_CONTROL` flag.

```
TX: plaintext skb → ieee80211_tx_h_select_key → ieee80211_tx_h_encrypt
                     (choose key)                (CCMP_encrypt / GCMP_encrypt)

RX: ciphertext skb → ieee80211_rx_h_decrypt → plaintext skb
                      (CCMP_decrypt / GCMP_decrypt)
```

Software ciphers live in `wpa.c` (TKIP), `aes_ccm.c` / `aes_gcm.c` (CCMP/GCMP).

### A-MPDU Aggregation

Block-ack sessions dramatically improve throughput on 802.11n/ac/ax links.

```
TX aggregation:
  ieee80211_start_tx_ba_session()  →  driver->ampdu_action(START)
  ──► ADDBA Request sent to peer
  ──► peer replies ADDBA Response
  ──► session active: frames queued as A-MPDU sub-frames

RX aggregation:
  ieee80211_process_addba_request()  →  create tid_ampdu_rx
  ──► reorder buffer active
  ──► out-of-order frames held until gap filled or timeout
```

### Rate Control

The default algorithm is **minstrel_ht** (`rc80211_minstrel_ht.c`).  It
samples different MCS rates, measures throughput and packet-error-rate, and
converges on the optimal rate per station.

```
ieee80211_tx_h_rate_ctrl()
  └─► rate_control_get_rate()
        └─► minstrel_ht_get_rate()
              ├─ pick best throughput rate
              ├─ pick best probability rate (fallback)
              └─ fill ieee80211_tx_rate array
```

---

## Common Operations (User-space)

```bash
# List WiFi devices
iw dev

# Show hardware capabilities
iw phy

# Scan for networks
iw dev wlan0 scan

# Connect (open network)
iw dev wlan0 connect MyNetwork

# Show link status
iw dev wlan0 link

# Show station statistics
iw dev wlan0 station dump

# Monitor aggregation / rate
iw dev wlan0 station get <MAC>

# Enable monitor mode
ip link set wlan0 down
iw dev wlan0 set type monitor
ip link set wlan0 up
```

---

## Key Source Files

All paths relative to `net/mac80211/` in the kernel tree:

| File | Purpose |
|---|---|
| `main.c` | Module init, `ieee80211_alloc_hw`, `ieee80211_register_hw` |
| `iface.c` | Virtual interface (netdev) setup and teardown |
| `tx.c` | Transmit path — handlers, queueing, `ieee80211_subif_start_xmit` |
| `rx.c` | Receive path — handlers, `ieee80211_rx`, packet delivery |
| `sta_info.c` | Station table — add / remove / lookup / iterate STAs |
| `mlme.c` | STA-mode MLME — association, authentication, beacons |
| `cfg.c` | `cfg80211_ops` implementation (bridge between cfg80211 and mac80211) |
| `scan.c` | Software scan logic |
| `key.c` | Encryption key management |
| `wpa.c` | TKIP / Michael MIC software implementation |
| `aes_ccm.c` | CCMP (AES-CCM) software encryption |
| `aes_gcm.c` | GCMP (AES-GCM) software encryption |
| `agg-tx.c` | TX A-MPDU aggregation (ADDBA session initiation) |
| `agg-rx.c` | RX A-MPDU aggregation (reorder buffer) |
| `rate.c` | Rate-control framework (algorithm registration / dispatch) |
| `rc80211_minstrel_ht.c` | Minstrel-HT rate-control algorithm |
| `driver-ops.h` | Inline wrappers around `ieee80211_ops` driver callbacks |
| `ieee80211_i.h` | Internal structures (`ieee80211_local`, `ieee80211_sub_if_data`) |
| `debugfs.c` | debugfs entries for runtime inspection |
| `util.c` | Shared helpers (channel context, skb manipulation) |

---

## Analogy

Think of mac80211 as the **air traffic control tower** at an airport:

- **cfg80211** is the national aviation authority — it sets the rules
  (regulatory domain, allowed frequencies) and provides the public interface
  for airlines (user-space).
- **mac80211** is the control tower — it sequences takeoffs and landings
  (TX/RX), manages gates (virtual interfaces), tracks every aircraft on
  the radar (station table), encrypts radio communications (crypto), and
  groups departures into efficient batches (A-MPDU aggregation).
- **The WiFi driver** is the ground crew — it physically moves planes on the
  tarmac (pushes frames into hardware), but all the scheduling and safety
  decisions happen in the tower.
- **Rate control** is the runway assignment algorithm — the tower constantly
  measures conditions (signal strength, error rate) and picks the fastest
  safe runway (MCS rate) for each flight (station).

---

## References

- `include/net/mac80211.h` — Public API and extensive kernel-doc comments
- `Documentation/networking/mac80211-injection.rst` — Monitor-mode injection
- `Documentation/driver-api/80211/mac80211.rst` — Driver-developer guide
- [Kernel Newbies: mac80211](https://kernelnewbies.org/Mac80211)
- [Linux Wireless wiki](https://wireless.wiki.kernel.org/en/developers/documentation/mac80211)
- Johannes Berg, *Understanding mac80211*, Linux Plumbers Conference
