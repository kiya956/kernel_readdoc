# Linux Input Subsystem — Kernel Driver Analysis

> Kernel: noble-linux-oem / oem-6.17-next  
> Source: `drivers/input/`

---

## 1. Full Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                     User Space                                   │
│   libinput  /  X11 evdev  /  Wayland compositor                 │
│   /dev/input/eventN  (read() / poll())                          │
│   /dev/input/mouseN  (mousedev)                                 │
└──────────────────────────┬───────────────────────────────────────┘
                           │ read / ioctl / poll
┌──────────────────────────▼───────────────────────────────────────┐
│             Input Handlers  (character devices)                  │
│                                                                  │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────────────────┐ │
│  │  evdev       │  │  mousedev     │  │  joydev / joystick    │ │
│  │  evdev.c     │  │  mousedev.c   │  │  joydev.c             │ │
│  │  /dev/input/ │  │  /dev/input/  │  │  /dev/input/jsN       │ │
│  │  eventN      │  │  mouseN       │  │                       │ │
│  └──────────────┘  └───────────────┘  └───────────────────────┘ │
│         ▲                  ▲                   ▲                  │
│         └──────────────────┴───────────────────┘                  │
│                            │                                     │
│  ┌─────────────────────────▼──────────────────────────────────┐  │
│  │              Input Core  (input.c, input-mt.c)             │  │
│  │                                                            │  │
│  │  input_dev  ←──►  input_handle  ←──►  input_handler       │  │
│  │  (device)          (per conn)           (evdev/mousedev)   │  │
│  │                                                            │  │
│  │  input_event() → input_handle_event() → filter → sync     │  │
│  └─────────────────────────▲──────────────────────────────────┘  │
│                            │                                     │
│         ┌──────────────────┼─────────────────────────┐          │
│         │                  │                         │          │
│  ┌──────▼──────┐  ┌────────▼───────┐  ┌─────────────▼───────┐  │
│  │  Keyboard   │  │  Mouse /       │  │  Touchscreen /       │  │
│  │  Drivers    │  │  Touchpad      │  │  Tablet              │  │
│  │             │  │  Drivers       │  │  Drivers             │  │
│  │ atkbd.c     │  │ mouse/alps.c   │  │ touchscreen/         │  │
│  │ keyboard/   │  │ mouse/synaptics│  │ atmel_mxt_ts.c       │  │
│  │ applespi.c  │  │ rmi4/          │  │ hid-multitouch.c     │  │
│  └──────┬──────┘  └────────┬───────┘  └──────────┬──────────┘  │
│         │                  │                      │             │
└─────────┼──────────────────┼──────────────────────┼─────────────┘
          │                  │                      │
    ┌─────▼────┐      ┌──────▼──────┐         ┌────▼──────────────┐
    │  serio   │      │  I2C / SPI  │         │  HID              │
    │  (PS/2)  │      │  bus        │         │  (USB/BT/I2C-HID) │
    └──────────┘      └─────────────┘         └───────────────────┘
```

---

## 2. Layer-by-Layer Explanation

### 2.1 Hardware → Low-Level Drivers
- **PS/2 (serio)**: `atkbd.c` for keyboard, `psmouse` for mouse — communicate via `serio_register_driver()`.  
- **USB HID**: `drivers/hid/hid-input.c` bridges HID reports to input events.  
- **I2C-HID**: Modern laptop touchpads (Precision Touchpad spec) — `drivers/hid/i2c-hid/`.  
- **RMI4**: Synaptics touchpads using the RMI protocol — `rmi4/`.  
- **ALPS / Synaptics / Elan**: Individual PS/2 touchpad drivers with gesture support.  
- **Touchscreens**: `atmel_mxt_ts`, `goodix`, `edt-ft5x06`, etc. via I2C/SPI.

### 2.2 Input Core (`input.c`, `input-mt.c`)
- Central hub connecting device drivers to handlers.  
- `struct input_dev`: capabilities bitmap (EV_KEY, EV_ABS, EV_REL, EV_SYN, ...).  
- `input_register_device()` → publishes to sysfs, connects matching handlers.  
- `input_event(dev, type, code, value)` → queues event, calls all connected handlers.  
- **Multi-touch** (`input-mt.c`): `input_mt_init_slots()`, ABS_MT_POSITION_X/Y tracking.

### 2.3 Event Types

| Type | Hex | Meaning | Example codes |
|------|-----|---------|---------------|
| EV_SYN | 0x00 | Synchronization | SYN_REPORT |
| EV_KEY | 0x01 | Key / button press | KEY_A, BTN_LEFT |
| EV_REL | 0x02 | Relative axis | REL_X, REL_Y, REL_WHEEL |
| EV_ABS | 0x03 | Absolute axis | ABS_X, ABS_MT_POSITION_X |
| EV_MSC | 0x04 | Miscellaneous | MSC_SCAN (raw scancode) |
| EV_SW  | 0x05 | Switch | SW_LID, SW_TABLET_MODE |
| EV_LED | 0x11 | LED | LED_CAPSL, LED_NUML |
| EV_FF  | 0x15 | Force feedback | FF_RUMBLE |

### 2.4 evdev Handler (`evdev.c`)
- Creates `/dev/input/eventN` character device.  
- Maintains per-client ring buffer of `struct input_event` (type + code + value + timestamp).  
- `read()` blocks until events; `poll()` for select/epoll.  
- `EVIOCGBIT` ioctl returns capability bitmap; `EVIOCGNAME` returns device name.

### 2.5 mousedev Handler (`mousedev.c`)
- Creates `/dev/input/mouseN` + `/dev/input/mice` (merged stream).  
- Translates EV_REL/EV_KEY into PS/2-compatible byte protocol.  
- Used by legacy apps that read /dev/input/mice directly (X11 without evdev, console).

### 2.6 Multi-Touch Protocol
```
Slot 0: ABS_MT_TRACKING_ID=5  ABS_MT_POSITION_X=300  ABS_MT_POSITION_Y=200
Slot 1: ABS_MT_TRACKING_ID=6  ABS_MT_POSITION_X=700  ABS_MT_POSITION_Y=400
SYN_REPORT

(finger lift on slot 0)
Slot 0: ABS_MT_TRACKING_ID=-1
SYN_REPORT
```
- Protocol B (slot-based, stateful) — standard for modern touchpads/screens.  
- `input_mt_report_slot_state()` handles ID assignment.

### 2.7 Force Feedback (`ff-core.c`, `ff-memless.c`)
- Game controllers: `input_ff_upload()` sends effect to device.  
- Memless: simulates FF via periodic timer for devices that don't buffer effects.

### 2.8 Input Pollers (`input-poller.c`)
- For devices that don't generate interrupts (resistive touchscreens, ADC buttons).  
- `input_setup_polling()` + `input_set_poll_interval()` → workqueue polling.

---

## 3. Data-Flow Diagram — Keypress (PS/2 keyboard)

```
Hardware: key pressed
       │
       ▼
PS/2 controller IRQ
  serio_interrupt() → atkbd_interrupt()
    decode scancode → keycode (via keymap)
    input_event(dev, EV_KEY, KEY_A, 1)   // press
    input_event(dev, EV_SYN, SYN_REPORT, 0)
       │
       ▼
input_handle_event()
  filter repeat/autorepeat
  pass to each input_handle → evdev_event()
    evdev_pass_values()
      spin_lock: append to client ring buffer
      wake_up_interruptible(waitq)
       │
       ▼
User space: read(/dev/input/eventN)
  returns struct input_event {
      .time  = timeval
      .type  = EV_KEY (0x01)
      .code  = KEY_A  (0x1e)
      .value = 1      (press)
  }
  libinput → XKB → compositor → app
```

---

## 4. Data-Flow Diagram — I2C Touchpad (Precision Touchpad)

```
Finger touches pad
       │
ATTN GPIO interrupt
       │
       ▼
i2c_hid_irq()
  Read HID report over I2C
       │
       ▼
hid_input_report()
  hid_process_report()
    hid-multitouch.c: mt_process_slot()
      input_mt_report_slot_state(slot, BTN_TOOL_FINGER, 1)
      input_report_abs(dev, ABS_MT_POSITION_X, x)
      input_report_abs(dev, ABS_MT_POSITION_Y, y)
    input_mt_sync_frame()
    input_sync()
       │
       ▼
evdev ring buffer → /dev/input/eventN
libinput gesture recognition → pointer motion / tap / scroll
```

---

## 5. Key Data Structures

```c
struct input_dev {          // one per physical input device
    const char *name;
    unsigned long evbit[...];   // supported event types
    unsigned long keybit[...];  // supported keys
    unsigned long absbit[...];  // supported absolute axes
    struct input_absinfo absinfo[ABS_CNT];
    struct list_head h_list;    // connected handles
    // mt slots, ff, led, ...
};

struct input_handler {      // evdev / mousedev / joydev
    void (*event)(struct input_handle *, unsigned int, unsigned int, int);
    int  (*connect)(struct input_handler *, struct input_dev *,
                    const struct input_device_id *);
    void (*disconnect)(struct input_handle *);
    const struct input_device_id *id_table;
};

struct input_event {        // userspace packet (8–24 bytes depending on arch)
    struct timeval time;
    __u16 type;
    __u16 code;
    __s32 value;
};
```

---

## 6. Important Source Files

| File | Role |
|------|------|
| `input.c` | Core: register, event dispatch, connect |
| `input-mt.c` | Multi-touch slot tracking |
| `evdev.c` | /dev/input/eventN handler |
| `mousedev.c` | /dev/input/mice handler |
| `keyboard/atkbd.c` | PS/2 keyboard driver |
| `mouse/psmouse-base.c` | PS/2 mouse + protocol dispatch |
| `mouse/alps.c` | ALPS touchpad driver |
| `mouse/synaptics.c` | Synaptics touchpad (PS/2) |
| `rmi4/rmi_driver.c` | Synaptics RMI4 touchpad |
| `touchscreen/atmel_mxt_ts.c` | Atmel maXTouch (OEM touchscreens) |
| `ff-core.c` | Force feedback core |
| `serio/i8042.c` | PS/2 controller (i8042 chip) |

---

## 7. bpftrace / Python Test Case

See [`test_input_workflow.py`](test_input_workflow.py) in this directory.
