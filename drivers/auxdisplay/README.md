# Linux Kernel auxdisplay Subsystem

## Overview

The **auxdisplay** subsystem drives small auxiliary displays used in embedded
systems, servers, networking equipment, and development boards:

- Character LCDs (HD44780-style, 16×2, 20×4 cells)
- 7-segment and 14-segment LED displays
- Small graphic LCDs (128×64 pixels)

It provides two distinct frameworks:

| Framework | Purpose | User interface |
|-----------|---------|----------------|
| **charlcd** | Character LCD (dot-matrix cells) | `/dev/lcd` (miscdevice) |
| **line-display** | Segmented LED line displays | `/sys/class/linedisp-*/` sysfs |

Source: `drivers/auxdisplay/`

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────────┐
│                     Userspace                                       │
│                                                                     │
│  echo "Hello World" > /dev/lcd          (charlcd)                  │
│  echo "Hello" > /sys/class/.../message  (line-display)             │
└──────────────────────┬─────────────────────────┬───────────────────┘
                       │                         │
         ┌─────────────▼──────────┐  ┌───────────▼────────────────┐
         │  charlcd core          │  │  line-display core          │
         │  (charlcd.c)           │  │  (line-display.c)           │
         │                        │  │                             │
         │  /dev/lcd miscdevice   │  │  linedisp device            │
         │  Single-open policy    │  │  message sysfs attr         │
         │  Escape seq parser     │  │  scroll_step_ms sysfs       │
         │  Reboot notifier       │  │  Timer-based scroll         │
         │  Backlight tempo work  │  │  7seg/14seg char mapping    │
         └──────────┬─────────────┘  └──────────┬──────────────────┘
                    │ charlcd_ops                │ linedisp_ops
      ┌─────────────┼──────────────┐    ┌────────┼──────────────────┐
      ▼             ▼              ▼    ▼        ▼                  ▼
┌──────────┐ ┌───────────┐ ┌─────────┐ ┌──────────┐ ┌────────┐ ┌────────┐
│ hd44780  │ │arm-charlcd│ │  lcd2s  │ │ ht16k33  │ │max6959 │ │seg-led │
│ (GPIO)   │ │(ARM HDLCD)│ │  (I2C)  │ │  (I2C)   │ │ (I2C)  │ │ -gpio  │
└──────────┘ └───────────┘ └─────────┘ └──────────┘ └────────┘ └────────┘
      │
      ▼
┌───────────────────────┐
│  hd44780_common.c     │
│  4-bit / 8-bit GPIO   │
│  timing: E pulse,     │
│  RS/RW control lines  │
└───────────────────────┘

   ┌─────────────────────────────────────────────┐
   │  cfag12864b (128×64 graphic LCD, KS0108)    │
   │  ks0108.c → parallel port controller        │
   │  cfag12864bfb.c → framebuffer device        │
   └─────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. charlcd Core (`charlcd.c`)

Registers a **miscdevice** at `/dev/lcd` with single-open semantics
(`atomic_t charlcd_available`). Writing to the device calls
`charlcd_write()` which processes each byte through a state machine:

**Normal characters** → `ops->print(lcd, c)` → hardware displays the glyph

**Escape sequences** (triggered by byte `27` / ESC char):
Sequences start with ESC then a letter/symbol command:

| Sequence | Action |
|----------|--------|
| `ESC D` | Display ON |
| `ESC d` | Display OFF |
| `ESC C` | Cursor ON |
| `ESC c` | Cursor OFF |
| `ESC B` | Blink ON |
| `ESC b` | Blink OFF |
| `ESC +` | Backlight ON |
| `ESC -` | Backlight OFF |
| `ESC *` | Flash backlight (4 s pulse) |
| `ESC F` | Large font |
| `ESC f` | Small font |
| `ESC N` | Two-line mode |
| `ESC n` | One-line mode |
| `ESC l` | Shift cursor left |
| `ESC r` | Shift cursor right |
| `ESC L` | Shift display left |
| `ESC R` | Shift display right |
| `ESC k` | Kill to end of line |
| `ESC I` | Reinitialize display |
| `ESC LxNNN` | Go to column NNN |
| `ESC LyNNN` | Go to row NNN |
| `ESC G` | Redefine custom character |

**Standard control chars**: `\n` (newline → wrap), `\r` (carriage return),
`\t` (tab → 8 spaces), `\f` (form feed → clear display), `\b` (backspace).

**Backlight tempo**: `charlcd_backlight()` uses a `delayed_work` to keep
the backlight on for `LCD_BL_TEMPO_PERIOD = 4` seconds after each write.

**Reboot notifier**: on `SYS_DOWN` / `SYS_HALT`, prints "System Halted."
or "System shutdown." to the LCD so the front panel shows status during
shutdown.

### 2. charlcd_ops — Hardware Abstraction

```c
struct charlcd_ops {
    void (*backlight)(struct charlcd *lcd, enum charlcd_onoff);
    int  (*print)   (struct charlcd *lcd, int c);
    int  (*gotoxy)  (struct charlcd *lcd, unsigned int x, unsigned int y);
    int  (*home)    (struct charlcd *lcd);
    int  (*clear_display)(struct charlcd *lcd);
    int  (*init_display) (struct charlcd *lcd);
    int  (*shift_cursor) (struct charlcd *lcd, enum charlcd_shift_dir);
    int  (*shift_display)(struct charlcd *lcd, enum charlcd_shift_dir);
    int  (*display) (struct charlcd *lcd, enum charlcd_onoff);
    int  (*cursor)  (struct charlcd *lcd, enum charlcd_onoff);
    int  (*blink)   (struct charlcd *lcd, enum charlcd_onoff);
    int  (*fontsize)(struct charlcd *lcd, enum charlcd_fontsize);
    int  (*lines)   (struct charlcd *lcd, enum charlcd_lines);
    int  (*redefine_char)(struct charlcd *lcd, char *esc);
};
```

### 3. HD44780 — Most Common Character LCD

The **HD44780** (Hitachi) is the dominant character LCD controller used
in virtually every 16×2/20×4 LCD module. It uses a parallel GPIO interface:

| Signal | Pins | Purpose |
|--------|------|---------|
| Data bus | 4 or 8 GPIOs | Character/command data |
| RS | 1 GPIO | Register Select (0=cmd, 1=data) |
| RW | 1 GPIO (opt) | Read/Write (usually tied low) |
| E | 1 GPIO | Enable strobe (pulse to latch) |

`hd44780_common.c` provides the 4-bit/8-bit write timing:
1. Set RS and data bits
2. Pulse E high → low (minimum 450 ns)
3. Wait for busy flag or use fixed delays

### 4. line-display Core (`line-display.c`)

For **segmented displays** (7-seg digits, 14-seg alphanumeric). Creates a
device under `/sys/class/linedisp-*/linedisp*/` with:

| sysfs attr | Access | Purpose |
|------------|--------|---------|
| `message` | rw | String to show; longer strings auto-scroll |
| `scroll_step_ms` | rw | Scroll interval in ms (0 = no scroll) |
| `map_seg` | rw | 7/14-segment character map (binary) |

**Scrolling**: a `timer_list` fires every `scroll_rate` jiffies, advances
`scroll_pos` by one, and calls `ops->update(linedisp)` to push the new
character window to hardware. Stops when message fits in `num_chars`.

**Character mapping**: ASCII → segment bitmap via `map_to_7segment.h` or
`map_to_14segment.h` lookup tables. Custom maps can be uploaded via `map_seg`.

### 5. HT16K33 — Holtek I2C LED Controller

Popular I2C controller (used on Adafruit 7/14-segment displays):
- Controls up to 128 individual LED segments via I2C
- Has built-in row/column multiplexing and PWM brightness control
- Implements **both** `line-display` (segment text) **and** `input`
  (matrix keypad) and `leds` (individual LED) frameworks simultaneously
- Supports 7-segment and 14-segment mapping
- Optional key-scanning interrupt via GPIO

### 6. cfag12864b — 128×64 Graphic LCD

Based on the KS0108 controller (parallel port connected):
- `ks0108.c`: low-level controller — selects left/right half (2 × KS0108),
  sets page/column address, writes pixel data
- `cfag12864b.c`: framebuffer-like 1 kB buffer (1 bit per pixel),
  refresh workqueue at configurable Hz
- `cfag12864bfb.c`: full `struct fb_info` framebuffer device so any
  application using `/dev/fb*` can write to it

---

## Write-to-LCD Flow (charlcd / HD44780)

```
write("/dev/lcd", "Hello\n", 6)
         │
         ▼
charlcd_write()   [file op]
  ├─ copy_from_user() byte by byte
  └─ charlcd_write_char(lcd, c) for each byte
              │
              ├─ escape mode?  → build esc_seq.buf
              │                → on sequence complete: dispatch command
              │                   e.g. ESC D → ops->display(lcd, ON)
              │                        ESC LxNNN → ops->gotoxy(lcd, NNN, y)
              │
              ├─ '\n' → ops->gotoxy(lcd, 0, y+1)  [wrap]
              ├─ '\f' → ops->clear_display(lcd)
              │
              └─ printable char → ops->print(lcd, c)
                                        │
                                        ▼
                               hd44780_common_print()
                               ├─ hd44780_write_data(RS=1, data=c)
                               │   ├─ gpiod_set_value(RS, 1)
                               │   ├─ gpiod_set_array_value(data_gpios, c)
                               │   ├─ gpiod_set_value(E, 1)
                               │   ├─ ndelay(450)
                               │   └─ gpiod_set_value(E, 0)
                               └─ advance priv->pos
```

---

## Files

| File | Framework | Hardware |
|------|-----------|---------|
| `charlcd.c` | charlcd core | — |
| `charlcd.h` | charlcd structs/ops | — |
| `hd44780.c` | charlcd | HD44780 GPIO driver (DT-based) |
| `hd44780_common.c` | charlcd | 4-bit/8-bit timing, shared logic |
| `arm-charlcd.c` | charlcd | ARM HDLCD / Versatile board |
| `lcd2s.c` | charlcd | lcd2s I2C LCD (Modtronix) |
| `panel.c` | charlcd | Panel driver (parallel port, legacy) |
| `line-display.c` | line-display core | — |
| `line-display.h` | line-display structs | — |
| `ht16k33.c` | line-display + input + leds | HT16K33 I2C LED controller |
| `max6959.c` | line-display | MAX6959 I2C 7-segment |
| `seg-led-gpio.c` | line-display | Single 7-seg digit via GPIOs |
| `img-ascii-lcd.c` | line-display | MIPS/IMG ASCII LCD |
| `cfag12864b.c` | standalone + FB | 128×64 KS0108 graphic LCD |
| `cfag12864bfb.c` | framebuffer | FB wrapper for cfag12864b |
| `ks0108.c` | standalone | KS0108 parallel LCD controller |

---

## HackMD Export

Title: **Linux Kernel auxdisplay Subsystem**

```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel auxdisplay Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`auxdisplay_trace_test.py`](auxdisplay_trace_test.py) for bpftrace verification.
