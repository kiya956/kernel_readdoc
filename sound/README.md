# Linux Sound Subsystem (ALSA) — Kernel Analysis

> Kernel: noble-linux-oem / oem-6.17-next  
> Source: `sound/`  (not under `drivers/` — has its own top-level tree)

---

## 1. Full Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│                         User Space                                   │
│  PulseAudio / PipeWire  →  ALSA lib (libasound)                     │
│  /dev/snd/controlCN  /dev/snd/pcmCNDNp  /dev/snd/pcmCNDNc          │
│  aplay / arecord / amixer / alsamixer                               │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ ioctl (ALSA userspace API)
┌──────────────────────────▼───────────────────────────────────────────┐
│             ALSA Core  (sound/core/)                                 │
│                                                                      │
│  ┌───────────────┐  ┌────────────────┐  ┌─────────────────────────┐ │
│  │  PCM          │  │  Control       │  │  Timer / Sequencer      │ │
│  │  pcm.c        │  │  control.c     │  │  timer.c  seq/          │ │
│  │  pcm_native.c │  │  (mixers,      │  │                         │ │
│  │  pcm_lib.c    │  │   switches)    │  │  MIDI: rawmidi.c        │ │
│  └───────────────┘  └────────────────┘  └─────────────────────────┘ │
│                                                                      │
│  snd_card  ←──►  snd_device[]  (PCM + control + hwdep + …)          │
│  sound_core.c: /dev/snd/ char devices, minor allocation             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────────────┐
         │                 │                         │
┌────────▼──────┐  ┌───────▼────────────┐  ┌────────▼──────────────┐
│  HD Audio     │  │  ASoC              │  │  USB Audio            │
│  (HDA)        │  │  (SoC / embedded)  │  │  sound/usb/           │
│               │  │                    │  │  card.c  endpoint.c   │
│ sound/hda/    │  │  sound/soc/        │  │  UAC1 / UAC2 / UAC3   │
│  core/        │  │  soc-core.c        │  └───────────────────────┘
│  controller.c │  │  soc-pcm.c         │
│  stream.c     │  │  soc-dapm.c        │
│               │  │  soc-component.c   │
│ Intel HDA PCI │  │  Intel AVS/SOF     │
│ sound/pci/ac97│  │  sound/soc/intel/  │
│ snd-hda-codec │  │  avs/  catpt/      │
│  codecs/      │  │                    │
└────────┬──────┘  └───────┬────────────┘
         │                 │
┌────────▼─────────────────▼────────────────────────────────────────┐
│              Hardware                                             │
│  HDA controller (Intel PCH) ── HDA link ── codec chips           │
│  SoC DAI (I2S/TDM/DMIC) ── DMIC / amplifier / codec             │
│  USB isochronous endpoint ── USB audio device                    │
└───────────────────────────────────────────────────────────────────┘
```

---

## 2. ALSA Core Concepts

### 2.1 `snd_card` — the Sound Card Abstraction
```c
struct snd_card {
    int   number;                  // card index (0, 1, …)
    char  id[16];                  // e.g. "PCH", "USB"
    char  longname[80];
    struct list_head devices;      // snd_device list
    struct snd_mixer_oss *mixer_oss;
    // proc, power, components …
};
```
Created with `snd_card_new()`, published with `snd_card_register()`.

### 2.2 PCM — Pulse Code Modulation (`sound/core/pcm*.c`)
The main audio I/O interface.

```
snd_pcm
  └── snd_pcm_str[2]  (playback stream, capture stream)
        └── snd_pcm_substream[]  (sub-device instances)
              └── snd_pcm_runtime  (ring buffer, hw_params, state)
```

Key PCM operations (driver vtable `snd_pcm_ops`):

| Callback | Triggered by |
|----------|-------------|
| `open` | app opens /dev/snd/pcmC0D0p |
| `hw_params` | `SNDRV_PCM_IOCTL_HW_PARAMS` — set rate/format/channels |
| `prepare` | `SNDRV_PCM_IOCTL_PREPARE` — reset DMA position |
| `trigger(START)` | `SNDRV_PCM_IOCTL_START` — start DMA |
| `trigger(STOP)` | `SNDRV_PCM_IOCTL_DROP` — stop DMA |
| `pointer` | returns current DMA position (for period tracking) |
| `close` | app closes device |

**Period interrupts**: DMA raises interrupt every `period_size` frames → `snd_pcm_period_elapsed()` → wakes up blocked `write()`.

### 2.3 Control Interface (`sound/core/control.c`)
- `snd_kcontrol` — one mixer element (volume, mute, switch, enum).
- `SNDRV_CTL_IOCTL_ELEM_LIST` / `_READ` / `_WRITE` — amixer/alsamixer use these.
- Drivers register controls with `snd_ctl_add(card, snd_ctl_new1(&kctl_template, priv))`.

### 2.4 Ring Buffer Model
```
          PCM ring buffer (kernel DMA memory)
  ┌─────────────────────────────────────��───┐
  │  frame 0 │ frame 1 │ … │ frame N-1      │
  └─────────────────────────────────────────┘
   ▲                        ▲
  appl_ptr                hw_ptr
  (app write pos)         (DMA read pos)

  available = buffer_size - (appl_ptr - hw_ptr)
  period_elapsed fires when hw_ptr crosses period boundary
```

---

## 3. HD Audio (HDA) Architecture

```
  CPU (PCH) ──────────── HDA Link (serial, 48 MHz) ───────────── Codec chip
  ┌──────────────────┐                                    ┌─────────────────┐
  │  HDA Controller  │  ← Intel PCH (0x8086:*)            │  HD Audio Codec │
  │  sound/hda/core/ │    CORB: send verbs (commands)     │  Realtek ALC295 │
  │  controller.c    │    RIRB: receive responses          │  IDT 92HD87     │
  │                  │    BDL: Buffer Descriptor List      │  Conexant CX20  │
  │  DMA streams:    │         (DMA ring for PCM data)     └─────────────────┘
  │  playback/record │
  └──────────────────┘

  snd-hda-intel (PCI driver, sound/pci/)
    → snd-hda-core  (controller + stream DMA)
      → snd-hda-codec (codec abstraction layer)
        → codec patch (patch_realtek.c / patch_conexant.c / …)
          → snd_card + PCM + controls
```

- **CORB/RIRB**: Command Outbound Ring Buffer / Response Inbound Ring Buffer — verb-based codec communication.
- **Verbs**: 12-bit commands (e.g., `GET_PIN_SENSE`, `SET_AMP_GAIN`, `SET_CONNECT_SEL`).
- **Widgets**: functional nodes inside codec (input/output mux, mixer, pin, ADC, DAC).
- **DAPM** (Dynamic Audio Power Management): disables unused widget paths to save power.

Intel-specific:
- **AVS** (`sound/soc/intel/avs/`): modern driver for Skylake+ DSP-based HDA.
- **SOF** (Sound Open Firmware): `sound/soc/intel/` — DSP firmware offload via IPC.
- **NHLT** (Non-HDA Link Table): ACPI table describing DMIC/SSP topology.

---

## 4. ASoC — ALSA System on Chip (`sound/soc/`)

Designed for embedded platforms where CPU, codec, and DAI are separate components.

```
snd_soc_card  (machine driver — glues everything)
  │
  ├── snd_soc_dai_link[]   (CPU DAI ←──► codec DAI)
  │     cpu_component  →  I2S/TDM controller (SoC side)
  │     codec_component → codec chip (e.g., rt5682, max98357)
  │
  ├── snd_soc_dapm_widget[]  (DAPM graph nodes)
  └── snd_soc_dapm_route[]   (graph edges)
```

Key ASoC objects:

| Object | Role |
|--------|------|
| `snd_soc_component` | Unified CPU/codec/platform abstraction |
| `snd_soc_dai` | Digital Audio Interface port (I2S / PCM / TDM) |
| `snd_soc_dapm_widget` | Power-managed audio path node |
| `snd_soc_dai_link` | Connects CPU DAI ↔ codec DAI |
| `snd_soc_card` | Machine-level glue; registers `snd_card` |

**DAPM** routes audio signal graph automatically — powers up only the active path:
```
  Microphone → ADC widget → Mux → Mixer → DAC → Speaker
       (DAPM powers each widget on/off based on active route)
```

---

## 5. USB Audio (`sound/usb/`)

- Implements USB Audio Class 1/2/3 (UAC1/UAC2/UAC3).
- `usb_audio_probe()` → parses UAC descriptors → creates `snd_card`.
- Isochronous USB transfers carry PCM frames at fixed packet rate.
- `endpoint.c` manages sync (feedback) and data endpoints.
- Zero-latency path: `snd_usb_substream` → fill iso URBs → submit → completion callback → `snd_pcm_period_elapsed()`.

---

## 6. Data-Flow Diagram — Playback (HDA)

```
Application: write(pcm_fd, samples, count)
       │
       ▼
snd_pcm_write() → snd_pcm_lib_write()
  copy user samples → PCM ring buffer (DMA coherent memory)
  advance appl_ptr
  if (appl_ptr - hw_ptr >= period_size): trigger DMA start
       │
       ▼
HDA DMA stream: reads from ring buffer → HDA link serial stream
       │
       ▼
Codec receives audio data → DAC → analog signal → speaker
       │
HDA period interrupt (every period_size frames)
       ▼
snd_hdac_stream_timecounter_init() → snd_pcm_period_elapsed()
  wake_up() blocked write()
  update hw_ptr
```

---

## 7. Data-Flow Diagram — Capture (DMIC via ASoC)

```
DMIC microphone → PDM signal → Intel DMIC controller
       │
       ▼  (I2S/DMIC DAI link)
snd_soc_dai.capture_dma_data → DMA engine
  dmaengine_pcm: fills PCM ring buffer from DMA
       │
       ▼
Period interrupt → snd_pcm_period_elapsed()
  wake_up() blocked read()
       │
       ▼
Application: read(pcm_fd, buf, count)
  snd_pcm_read() → copy ring buffer → user space
```

---

## 8. Key Data Structures

```c
struct snd_card          // top-level sound card
struct snd_pcm           // PCM device (playback + capture streams)
struct snd_pcm_substream // one open instance of a PCM stream
struct snd_pcm_runtime   // runtime state: ring buffer, hw_ptr, state
struct snd_pcm_ops       // driver callbacks: open/hw_params/trigger/pointer
struct snd_kcontrol      // one mixer control element
struct snd_soc_card      // ASoC machine card
struct snd_soc_dai       // Digital Audio Interface
struct snd_soc_component // codec or CPU DAI component
struct hdac_stream       // HDA DMA stream descriptor
```

---

## 9. Important Source Files

| File | Role |
|------|------|
| `sound/core/pcm.c` | PCM device registration |
| `sound/core/pcm_native.c` | PCM ioctl handler (hw_params, trigger, …) |
| `sound/core/pcm_lib.c` | Ring buffer management, period elapsed |
| `sound/core/control.c` | Mixer control framework |
| `sound/core/init.c` | snd_card create/register/free |
| `sound/hda/core/controller.c` | HDA controller (CORB/RIRB/DMA) |
| `sound/hda/core/stream.c` | HDA DMA stream management |
| `sound/pci/ac97/` | AC'97 legacy PCI audio |
| `sound/soc/soc-core.c` | ASoC card/component registration |
| `sound/soc/soc-dapm.c` | DAPM power graph |
| `sound/soc/soc-pcm.c` | ASoC PCM open/hw_params/trigger |
| `sound/soc/intel/avs/` | Intel AVS DSP driver |
| `sound/usb/card.c` | USB Audio Class probe |
| `sound/usb/endpoint.c` | Isochronous URB management |

---

## 10. bpftrace / Python Test Case

See [`test_sound_workflow.py`](test_sound_workflow.py) in this directory.
