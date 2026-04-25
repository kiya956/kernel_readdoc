# Linux Kernel TLS (kTLS) — In-Kernel TLS Record Layer

## Overview

**Kernel TLS (kTLS)** offloads the TLS record layer from userspace into the
kernel. After a userspace TLS library (OpenSSL, GnuTLS) completes the
handshake, it passes the negotiated keys via `setsockopt()` so the kernel
can encrypt/decrypt TLS records directly. This enables:

- **sendfile()** over TLS — zero-copy from page cache to encrypted wire
- **Hardware TLS offload** — NICs encrypt inline (Mellanox ConnectX-6+, Intel E810)
- **splice()** and **MSG_ZEROCOPY** optimisations
- Reduced user–kernel context switches for bulk data transfer

Source: `net/tls/`, `include/net/tls.h`, `include/uapi/linux/tls.h`.

Supported ciphers: AES-128-GCM, AES-256-GCM, CHACHA20-POLY1305, SM4 (since 5.11+).

---

## Subsystem Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         USERSPACE                                │
│                                                                  │
│  Application (nginx, HAProxy, kTLS-aware app)                    │
│   1. socket(AF_INET, SOCK_STREAM)                                │
│   2. connect() / accept()                                        │
│   3. SSL_do_handshake()  ← userspace TLS library (OpenSSL)       │
│   4. setsockopt(SOL_TCP, TCP_ULP, "tls")   ← install kTLS ULP   │
│   5. setsockopt(SOL_TLS, TLS_TX, &crypto_info)  ← TX keys       │
│   6. setsockopt(SOL_TLS, TLS_RX, &crypto_info)  ← RX keys       │
│   7. sendfile(fd, sock, ...)  / send() / splice()                │
└──────────────────────────────┬───────────────────────────────────┘
                               │  setsockopt() / sendmsg() / recvmsg()
┌──────────────────────────────▼───────────────────────────────────┐
│                       TCP ULP LAYER                              │
│                    (net/tls/tls_main.c)                           │
│                                                                  │
│  tcp_register_ulp("tls")                                         │
│  tls_init()  → allocates tls_context, replaces sk_proto          │
│  tls_sk_proto_close() → cleanup on socket close                  │
└──────────────────────────────┬───────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────┐
│  SOFTWARE PATH   │ │  DEVICE OFFLOAD  │ │  INLINE CRYPTO       │
│ (tls_sw.c)       │ │ (tls_device.c)   │ │ (tls_device.c)       │
│                  │ │                  │ │                      │
│ tls_sw_sendmsg() │ │ tls_device_      │ │ NIC encrypts in HW   │
│ tls_sw_recvmsg() │ │   sendmsg()      │ │ via ndo_tls_dev_add  │
│ tls_sw_splice()  │ │ tls_device_      │ │                      │
│                  │ │   decaps()       │ │ ConnectX-6 Dx        │
│ AES-NI / AEAD    │ │                  │ │ Intel E810           │
│ crypto subsystem │ │ Fallback to SW   │ │                      │
└──────────────────┘ └──────────────────┘ └──────────────────────┘
          │                    │
          ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                       TCP STACK                                  │
│           tcp_sendmsg_locked() / tcp_recvmsg()                   │
│           SKB with encrypted TLS records                         │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Data Structures

### `struct tls_context` (`include/net/tls.h`)
Per-socket TLS state. Stored in `inet_csk(sk)->icsk_ulp_data`.

| Field               | Description                                      |
|----------------------|--------------------------------------------------|
| `crypto_send`        | Union: crypto info for TX (AES-GCM params, IV)   |
| `crypto_recv`        | Union: crypto info for RX                        |
| `priv_ctx_tx`        | SW or device TX context pointer                  |
| `priv_ctx_rx`        | SW or device RX context pointer                  |
| `sk_proto`           | Saved original `struct proto` ops                |
| `tx_conf` / `rx_conf`| Enum: TLS_SW, TLS_HW, TLS_HW_RECORD             |
| `partially_sent_record` | Resume point for partial sends                |

### `struct tls_sw_context_tx` / `struct tls_sw_context_rx`
Software encryption/decryption state.

| Field (TX)           | Description                                      |
|----------------------|--------------------------------------------------|
| `aead_send`          | AEAD crypto transform (e.g. gcm(aes))            |
| `msg_list`           | Pending plaintext messages                       |
| `tx_lock`            | Serialises TX operations                         |

| Field (RX)           | Description                                      |
|----------------------|--------------------------------------------------|
| `aead_recv`          | AEAD crypto transform for decryption             |
| `rx_list`            | Decrypted records waiting for userspace read      |
| `strp`               | TLS record stream parser                         |

### `struct tls_crypto_info` (`include/uapi/linux/tls.h`)
Userspace-visible structure passed via `setsockopt()`.

```c
struct tls_crypto_info {
    __u16 version;    /* TLS_1_2_VERSION or TLS_1_3_VERSION */
    __u16 cipher_type; /* TLS_CIPHER_AES_GCM_128, ... */
};
/* Followed by cipher-specific fields: IV, key, salt, rec_seq */
```

---

## Key Functions

| Function                  | File            | Purpose                              |
|---------------------------|-----------------|--------------------------------------|
| `tls_init()`              | tls_main.c      | ULP init — allocate tls_context      |
| `tls_set_sw_offload()`    | tls_sw.c        | Setup software crypto for TX or RX   |
| `tls_sw_sendmsg()`        | tls_sw.c        | Encrypt + send TLS records (SW path) |
| `tls_sw_recvmsg()`        | tls_sw.c        | Receive + decrypt TLS records (SW)   |
| `tls_sw_splice_read()`    | tls_sw.c        | splice() for kTLS sockets            |
| `tls_device_sendmsg()`    | tls_device.c    | TX path for device-offloaded TLS     |
| `tls_set_device_offload()` | tls_device.c   | Install device offload for TX        |
| `tls_sk_proto_close()`    | tls_main.c      | Cleanup on socket close              |
| `tls_push_record()`       | tls_sw.c        | Finalize and push one TLS record     |
| `tls_strp_msg_rcv()`      | tls_strp.c      | Stream parser callback for RX        |

---

## Configuration & Tunables

```
CONFIG_TLS=m              # kTLS module
CONFIG_TLS_DEVICE=y       # Enable NIC TLS offload
```

### sysctl / procfs

| Path                                  | Description                       |
|---------------------------------------|-----------------------------------|
| `/proc/modules` (grep tls)            | Check if kTLS module is loaded    |
| `/proc/net/tls_stat`                  | Per-CPU TLS counters (6.2+)       |

### Counters (`/proc/net/tls_stat`)

| Counter                    | Meaning                                     |
|----------------------------|---------------------------------------------|
| `TlsCurrTxSw`              | Active SW TX sockets                        |
| `TlsCurrRxSw`              | Active SW RX sockets                        |
| `TlsCurrTxDevice`          | Active device-offloaded TX sockets          |
| `TlsCurrRxDevice`          | Active device-offloaded RX sockets          |
| `TlsTxSw`                  | Total SW TX sockets ever opened             |
| `TlsRxSw`                  | Total SW RX sockets ever opened             |
| `TlsDecryptError`          | AEAD decryption failures                    |

---

## Typical Workflow

1. **Handshake in userspace** — OpenSSL / GnuTLS negotiates cipher, derives keys
2. **Install ULP** — `setsockopt(sock, SOL_TCP, TCP_ULP, "tls", 3)`
3. **Set TX keys** — `setsockopt(sock, SOL_TLS, TLS_TX, &crypto_info, len)`
   - Kernel calls `tls_set_sw_offload()` or `tls_set_device_offload()`
4. **Set RX keys** (optional) — same for `TLS_RX`
5. **Data transfer** — `sendfile()` / `send()` go through `tls_sw_sendmsg()`
6. **Close** — `tls_sk_proto_close()` flushes pending records, frees crypto

---

## Tracing & Debugging

```bash
# Check kTLS is loaded
grep tls /proc/modules

# TLS statistics
cat /proc/net/tls_stat

# Trace SW encrypt path
bpftrace -e 'kprobe:tls_sw_sendmsg { printf("kTLS TX pid=%d\n", pid); }'

# Trace device offload registration
bpftrace -e 'kprobe:tls_set_device_offload { printf("TLS HW offload\n"); }'
```
