# Linux USB Subsystem — Kernel Driver Analysis

> Kernel: noble-linux-oem / oem-6.17-next  
> Source: `drivers/usb/`

---

## 1. Full Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                         User Space                                  │
│   libusb  /  usbfs  (/dev/bus/usb/)  /  class device nodes         │
│   /sys/bus/usb/  /  usbmon (pcap-like tracing)                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ ioctl / read / write
┌──────────────────────────────▼──────────────────────────────────────┐
│              USB Core  (drivers/usb/core/)                          │
│                                                                     │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────┐    │
│  │  Driver    │  │  URB Engine  │  │   Hub / Enumeration      │    │
│  │  Model     │  │              │  │                          │    │
│  │ driver.c   │  │  urb.c       │  │  hub.c   port.c          │    │
│  │ usb.c      │  │  message.c   │  │  config.c  devices.c     │    │
│  └────────────┘  └──────────────┘  └──────────────────────────┘    │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │   HCD Framework  (hcd.c  hcd-pci.c)                         │   │
│  │   usb_hcd  ←→  hc_driver ops  ←→  host controller driver   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │  sysfs   │  │  devio   │  │  usbmon  │  │  USB Power (pm)  │   │
│  │ sysfs.c  │  │ devio.c  │  │ mon/     │  │  generic.c       │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
         ┌─────────────────────┼───────────────────────┐
         │                     │                       │
┌────────▼────────┐  ┌─────────▼──────────┐  ┌────────▼────────────┐
│  Host Controller│  │  Gadget/Device     │  │  USB Type-C         │
│  Drivers        │  │  side  (gadget/)   │  │  (typec/ / tcpm/)   │
│  host/          │  │                    │  │                     │
│  xhci-hcd  ─USB3│  │  composite.c       │  │  PD negotiation     │
│  ehci-hcd  ─USB2│  │  function/         │  │  Alt modes (DP/TBT) │
│  ohci-hcd  ─USB1│  │  udc-core.c        │  │  mux/retimer        │
│  dwc3  dwc2     │  │                    │  │                     │
└────────┬────────┘  └────────────────────┘  └─────────────────────┘
         │
┌────────▼─────────────────────────────────────────────────────────┐
│                Hardware: USB Fabric                               │
│                                                                  │
│  [xHCI / EHCI / OHCI controller MMIO]                           │
│       │                                                          │
│   Root Hub  ──  Hub  ──  Hub  ──  Device                        │
│              (Tier 1)  (Tier 2)  (HID / Storage / Net / ...)    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Layer-by-Layer Explanation

### 2.1 Hardware — USB Fabric
- **xHCI** (eXtensible Host Controller Interface): USB 3.x/4.x — one controller handles SuperSpeed + Hi-Speed.  
- **EHCI** (Enhanced HCI): USB 2.0 Hi-Speed only (480 Mb/s).  
- **OHCI/UHCI**: USB 1.1 Full/Low Speed (12/1.5 Mb/s) — legacy.  
- Topology: tree of hubs, max 7 tiers, 127 devices per bus.

### 2.2 Host Controller Driver (HCD) Framework (`hcd.c`)
- `struct usb_hcd` — kernel representation of one HC instance.  
- `struct hc_driver` — ops vtable: `reset`, `start`, `stop`, `urb_enqueue`, `urb_dequeue`, `get_frame_number`, `hub_control`, etc.  
- `usb_add_hcd()` registers with usbcore; triggers root-hub enumeration.  
- xHCI (`xhci-hcd.c`): ring-buffer-based (Transfer Ring, Command Ring, Event Ring); streams, isochronous scheduling.

### 2.3 USB Core Driver Model (`usb.c`, `driver.c`)
- `usb_register()` wraps `driver_register()` with `usb_bus_type`.  
- Matching via `struct usb_device_id[]` (VID/PID / class / subclass / protocol).  
- `probe(interface, id)` / `disconnect(interface)` callbacks per **interface** (not device).  
- Dynamic IDs: `/sys/bus/usb/drivers/<drv>/new_id`.

### 2.4 URB Engine (`urb.c`, `message.c`)
- **URB** (USB Request Block) — the unit of I/O (analogous to `struct bio` in block or `struct sk_buff` in net).

| URB field | Meaning |
|-----------|---------|
| `pipe` | encodes address + EP + direction + transfer type |
| `transfer_buffer` | DMA-able data buffer |
| `complete` | callback on completion |
| `status` | result code (0 = success) |
| `actual_length` | bytes transferred |

- `usb_submit_urb()` → HCD `urb_enqueue()` → hardware → interrupt → `usb_hcd_giveback_urb()` → `complete()`.  
- Synchronous wrappers: `usb_bulk_msg()`, `usb_control_msg()`.

### 2.5 Hub & Enumeration (`hub.c`, `config.c`, `devices.c`)
| Step | Function | Action |
|------|----------|--------|
| 1 | `hub_port_connect()` | Detect connect event on port |
| 2 | `usb_new_device()` | Allocate `usb_device`, assign address (SET_ADDRESS) |
| 3 | `usb_get_device_descriptor()` | Read bDeviceClass, idVendor, idProduct |
| 4 | `usb_choose_configuration()` | Select best configuration |
| 5 | `usb_set_configuration()` | Issue SET_CONFIGURATION |
| 6 | `usb_parse_configuration()` | Parse interface/endpoint descriptors |
| 7 | `device_add()` → driver probe | Publish to sysfs, trigger driver bind |

### 2.6 Transfer Types
| Type | Direction | Guarantee | Use case |
|------|-----------|-----------|---------|
| Control | Both | Yes | Setup / enumeration |
| Bulk | Both | Yes (retry) | Storage, CDC |
| Interrupt | In (or Out) | Bounded latency | HID, mouse, keyboard |
| Isochronous | Both | Bandwidth | Audio, video |

### 2.7 Power Management (`generic.c`, `driver.c`)
- USB selective suspend: `usb_autosuspend_device()` after idle timeout → D2/D3.  
- Remote wakeup: device asserts resume signalling; hub notifies root.  
- USB 3.x link states: U0 (active) → U1 → U2 → U3 (suspend).

### 2.8 USB Gadget / Device-side (`gadget/`)
- `udc-core.c` manages UDC (USB Device Controller).  
- `composite.c` / `configfs/` build multi-function gadgets (e.g., ACM + RNDIS + MTP).  
- Functions: `f_acm.c`, `f_mass_storage.c`, `f_hid.c`, `f_uac2.c`, etc.

### 2.9 USB Type-C (`typec/`, `typec/tcpm/`)
- **TCPM** (Type-C Port Manager): PD (Power Delivery) state machine.  
- Handles: cable orientation, role swap (DFP↔UFP), Alt Modes (DisplayPort, Thunderbolt).  
- `mux/` and `retimer/` drivers configure the physical signal path.

### 2.10 usbmon (`mon/`)
- Kernel-side packet capture — `/sys/kernel/debug/usb/usbmon/`.  
- Wireshark can decode captures with `usbmon` support.

---

## 3. Data-Flow Diagram — Device Enumeration

```
Port connect event (hub interrupt URB completes)
       │
       ▼
hub_port_connect()
  │  Reset port (USB reset signalling, 10 ms)
  │  usb_new_device()
  │    usb_get_device_descriptor() → control URB → GET_DESCRIPTOR
  │    usb_choose_configuration()
  │    usb_set_configuration() → control URB → SET_CONFIGURATION
  │    usb_parse_configuration()
  │      → for each interface: allocate usb_interface
  │    device_add(usb_interface)
  │      usb_probe_interface()
  │        usb_match_id() → driver found
  │        driver.probe(intf, id)
  └─► device ready in /sys/bus/usb/devices/
```

---

## 4. Data-Flow Diagram — Bulk Transfer (e.g. USB Storage)

```
Driver calls usb_bulk_msg(dev, pipe, buf, len, &actual, timeout)
       │
       ▼
usb_submit_urb(urb)
  │  validate pipe, set up DMA mapping
  │  hcd->driver->urb_enqueue(hcd, urb)
  │    [xHCI] fill Transfer Ring TRB, ring doorbell
  │    hardware fetches TRB, DMAs data
  │    [xHCI] generates Transfer Event on Event Ring
  │  xhci_irq() → handle_tx_event()
  │  usb_hcd_giveback_urb(hcd, urb, status)
  │    urb->complete() callback (or wakes sleeping caller)
  ▼
Data in urb->transfer_buffer, actual_length set
```

---

## 5. Key Data Structures

```c
struct urb {                   // I/O request
    unsigned int pipe;         // addr + EP + direction + type
    void *transfer_buffer;
    u32  transfer_buffer_length;
    usb_complete_t complete;   // callback
    int  status;
    u32  actual_length;
};

struct usb_device {            // one per physical USB device
    int  devnum;               // 1–127
    enum usb_device_speed speed;
    struct usb_device_descriptor descriptor;
    struct usb_host_config *config;
    struct usb_hcd *bus;
};

struct usb_interface {         // one per interface (driver binds here)
    struct usb_host_interface *altsetting;
    int   num_altsetting;
    struct device dev;
};

struct usb_driver {            // registered by class drivers
    const struct usb_device_id *id_table;
    int  (*probe)(struct usb_interface *, const struct usb_device_id *);
    void (*disconnect)(struct usb_interface *);
    int  (*suspend)(struct usb_interface *, pm_message_t);
    int  (*resume)(struct usb_interface *);
};
```

---

## 6. Important Source Files

| File | Role |
|------|------|
| `core/usb.c` | Core init, bus type, helper library |
| `core/hcd.c` | HCD framework, giveback, root-hub |
| `core/hub.c` | Hub driver, enumeration, port events |
| `core/urb.c` | URB allocation, submit, cancel |
| `core/message.c` | Synchronous control/bulk helpers |
| `core/config.c` | Descriptor parsing |
| `core/driver.c` | Driver registration, probe/disconnect |
| `core/devio.c` | usbfs ioctl interface (libusb) |
| `host/xhci-hcd.c` | xHCI host controller driver |
| `host/xhci-ring.c` | Transfer/Command/Event Ring management |
| `host/ehci-hcd.c` | EHCI host controller driver |
| `gadget/udc-core.c` | UDC framework |
| `typec/tcpm/tcpm.c` | Type-C PD state machine |
| `mon/mon_main.c` | usbmon packet capture |

---

## 7. bpftrace / Python Test Case

See [`test_usb_workflow.py`](test_usb_workflow.py) in this directory.

The test attaches bpftrace probes to verify USB enumeration, URB dispatch,
hub events, driver probe, and power management flows.
