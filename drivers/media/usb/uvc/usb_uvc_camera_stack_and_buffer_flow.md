# Linux USB UVC Camera: Complete Software Stack and Buffer Flow

This document describes a typical Ubuntu USB UVC webcam using PipeWire's SPA V4L2 backend and V4L2 MMAP capture buffers. It focuses on the control path, data path, kernel interfaces, queue callbacks, and frame-buffer ownership.

## 1. Complete USB UVC Camera Software Stack

```text
                         USERSPACE
+-------------------------------------------------------------+
|                                                             |
|  Camera Application                                         |
|        |                                                    |
|        | PipeWire client API                                |
|        v                                                    |
|  PipeWire                                                   |
|        |                                                    |
|        | SPA Node / Buffer interface                        |
|        v                                                    |
|  SPA V4L2 Plugin (libspa-v4l2.so)                            |
|        |                                                    |
|        | open(), ioctl(), mmap(), poll()                     |
|        v                                                    |
|  /dev/video0                                                |
|                                                             |
+------------------------- SYSCALL ---------------------------+
                         KERNEL SPACE
+-------------------------------------------------------------+
|                                                             |
|  VFS                                                        |
|   |                                                         |
|   | file_operations                                         |
|   v                                                         |
|  V4L2 Core                                                  |
|   |                                                         |
|   | video_device->fops / ioctl_ops                          |
|   |                                                         |
|   +------------------------+                                |
|   |                        |                                |
|   v                        v                                |
|  videobuf2 (vb2)        uvcvideo                             |
|  ----------------       ----------------------              |
|  Buffer allocation      USB camera probing                  |
|  Queue management       UVC format negotiation              |
|  Buffer states          Camera controls                     |
|  QBUF / DQBUF           USB video streaming                  |
|  Wait queues            Frame assembly                      |
|   |                        |                                |
|   |   buf_queue()          |                                |
|   +----------------------->|                                |
|   |                        |                                |
|   |   vb2_buffer_done()    |                                |
|   |<-----------------------+                                |
|                            |                                |
|                            v                                |
|                         USB Core                            |
|                            |                                |
|                            v                                |
|                         xhci_hcd                            |
|                            |                                |
+----------------------------|--------------------------------+
                        HARDWARE
                             |
                       xHCI Controller
                             |
                          USB Bus
                             |
                       USB Webcam
                      +-----------+
                      | Sensor    |
                      | ISP       |
                      | Firmware  |
                      | USB I/F   |
                      +-----------+
```

**Notes:** The arrows between vb2 and `uvcvideo` show callbacks and function calls—not the physical path of image pixels. `uvcvideo` participates in both the V4L2/media and USB subsystems. WirePlumber performs device-monitoring and policy functions, not per-frame image transport. The V4L2 core bridges generic VFS operations to `video_device->fops` and `ioctl_ops`.

## 2. Camera Initialization and Configuration

### 2.1 Device detection and registration

```text
USB CAMERA CONNECTED
        |
        v
xHCI detects device connection
        |
        v
USB Core enumerates USB device
        |
        +-- Read USB descriptors
        +-- Identify UVC interfaces
        +-- Match registered USB driver
        |
        v
uvcvideo.probe()
        |
        +-- Parse camera descriptors
        +-- Discover camera formats
        +-- Discover resolutions / frame intervals
        +-- Discover camera controls
        +-- Initialize driver streaming structures
        |
        +-- Initialize vb2 queue
        |       |
        |       +-- vb2_queue_init()
        |       +-- Register vb2_ops callbacks
        |
        +-- Initialize video_device
        |       |
        |       +-- vdev->fops
        |       +-- vdev->ioctl_ops
        |
        +-- video_register_device()
                    |
                    v
              /dev/video0
                    |
                    v
             udev notification
                    |
                    v
         WirePlumber discovers camera
                    |
                    v
         PipeWire camera node available
```

Note that `/dev/video0` is an example; the device number can be different and cameras can expose multiple video nodes. WirePlumber discovery and node creation depend on system configuration.

### 2.2 Format and camera configuration

Example request: 1280×720, YUYV, 30 FPS.

```text
Camera Application
        |
        v
PipeWire format negotiation
        |
        v
SPA V4L2 backend
        |
        +-- open("/dev/video0")
        +-- VIDIOC_QUERYCAP
        +-- VIDIOC_ENUM_FMT
        +-- VIDIOC_S_FMT
        +-- VIDIOC_S_PARM
        |
        v
VFS / V4L2 Core
        |
        v
uvcvideo V4L2 callbacks
        |
        +-- Validate requested format
        +-- Select resolution / frame interval
        +-- Prepare UVC streaming parameters
        |
        v
UVC VideoStreaming negotiation
(PROBE / COMMIT during stream setup)
        |
        v
USB control transfers
        |
        v
Camera firmware
        |
        +-- Configure sensor
        +-- Configure internal ISP
        +-- Configure streaming format
        |
        v
Camera ready for streaming
```

**Nuance:** Format-selection operations and actual UVC VideoStreaming PROBE/COMMIT transactions are not necessarily performed at the same moment. Exact ordering varies by implementation and camera.

## 3. Complete V4L2 Buffer Lifecycle

The diagram below uses `V4L2_MEMORY_MMAP`, with two example buffers. `poll(fd)` acts on the **camera device file descriptor**, not on a buffer FD. `VIDIOC_DQBUF` is initiated by **userspace**, not by the kernel.

```text
 USERSPACE             V4L2 / vb2             uvcvideo            USB
     |                      |                     |                |
     | VIDIOC_REQBUFS       |                     |                |
     |--------------------->|                     |                |
     |                      | Allocate buffers    |                |
     |<---------------------|                     |                |
     |                      |                     |                |
     | VIDIOC_QUERYBUF      |                     |                |
     |--------------------->|                     |                |
     |<-- offset / length --|                     |                |
     |                      |                     |                |
     | mmap()               |                     |                |
     |--------------------->|                     |                |
     |<-- mapped address ---|                     |                |
     |                      |                     |                |
     | VIDIOC_QBUF (B0)     |                     |                |
     |--------------------->|                     |                |
     |                      | Mark buffer queued  |                |
     |                      |                     |                |
     | VIDIOC_QBUF (B1)     |                     |                |
     |--------------------->|                     |                |
     |                      |                     |                |
     | VIDIOC_STREAMON      |                     |                |
     |--------------------->|                     |                |
     |                      |                     |                |
     |                      | buf_queue(B0)       |                |
     |                      |-------------------->|                |
     |                      |                     | Add B0 to list |
     |                      |                     |                |
     |                      | buf_queue(B1)       |                |
     |                      |-------------------->|                |
     |                      |                     | Add B1 to list |
     |                      |                     |                |
     |                      | start_streaming()   |                |
     |                      |-------------------->|                |
     |                      |                     | Submit URBs    |
     |                      |                     |--------------->|
     |                      |                     |                |
     | poll(fd)             |                     |                |
     |--------------------->|                     |                |
     |     WAITING          |                     |                |
     |                      |                     |                |
     |                      |                     |<--- USB data --|
     |                      |                     |                |
     |                      |                     | URB completion |
     |                      |                     | Parse payloads |
     |                      |                     | Fill buffer B0 |
     |                      |                     |                |
     |                      | vb2_buffer_done(B0) |                |
     |                      |<--------------------|                |
     |                      |                     |                |
     |                      | Mark B0 DONE        |                |
     |                      | Add B0 to done_list |                |
     |                      | wake_up()           |                |
     |                      |                     |                |
     |<-- poll() returns ---|                     |                |
     |                      |                     |                |
     | VIDIOC_DQBUF         |                     |                |
     |--------------------->|                     |                |
     |                      | Remove B0 from      |                |
     |                      | completed queue     |                |
     |<-- B0 metadata ------|                     |                |
     |                      |                     |                |
     | Read mapped B0       |                     |                |
     | Process image        |                     |                |
     |                      |                     |                |
     | VIDIOC_QBUF (B0)     |                     |                |
     |--------------------->|                     |                |
     |                      | buf_queue(B0)       |                |
     |                      |-------------------->|                |
     |                      |                     | Reuse B0       |
     |                      |                     |                |
     |                   REPEAT FOR EVERY FRAME                    |
```

**Ordering note:** Queued buffers can be held by vb2 before streaming starts and delivered to the driver when the stream is started. The precise ordering of the `buf_queue()` and `start_streaming()` callbacks depends on the vb2 startup path. In USB UVC capture, image data may arrive in multiple URBs, and the driver assembles a frame into V4L2 buffers rather than treating one URB as one frame.

## 4. Buffer Ownership and Kernel Structures

```text
                 USERSPACE
                     |
             struct v4l2_buffer
                     |
                     +-- index
                     +-- timestamp
                     +-- sequence
                     +-- bytesused
                     +-- m.offset (MMAP)
                     |
                     | VIDIOC_QBUF
                     v
              V4L2 / videobuf2
                     |
              struct vb2_queue
                     |
             +-------+-------+
             |               |
             v               v
       struct vb2_buffer  struct vb2_buffer
              B0               B1
             |               |
             +-------+-------+
                     |
              buf_queue()
                     |
                     v
                 uvcvideo
                     |
              Driver buffer list
                     |
              USB frame capture
                     |
              vb2_buffer_done()
                     |
                     v
                vb2 done_list
                     |
               VIDIOC_DQBUF
                     |
                     v
                  USERSPACE
```

In a V4L2 videobuf2 driver, `struct vb2_v4l2_buffer` embeds `struct vb2_buffer` and adds V4L2-specific metadata. `struct vb2_queue` manages generic streaming-buffer lifecycle. The camera driver typically also maintains its own list of buffers made available through `buf_queue()`.

### Responsibility table

| Action | Responsible component |
|---|---|
| Create and configure the `vb2_queue` | Camera driver (`uvcvideo`) |
| Allocate and track buffer objects | vb2 plus its selected memory allocator |
| Hand available buffers to the driver | vb2 calls the driver's `buf_queue()` |
| Receive USB payloads and assemble frames | `uvcvideo` |
| Determine when a frame is complete | `uvcvideo` |
| Mark a buffer done and wake waiters | vb2, after `vb2_buffer_done()` |
| Wait for frame readiness | Userspace `poll()` / `epoll()` or blocking `DQBUF` |
| Dequeue a completed buffer | Userspace calls `VIDIOC_DQBUF` |
| Return a buffer for reuse | Userspace calls `VIDIOC_QBUF` |

## 5. Key Takeaways

- The USB camera generally includes a sensor, ISP, firmware, and USB interface; Linux controls it using standardized UVC interfaces.
- `uvcvideo` is a USB driver that also registers V4L2 video devices and operations.
- V4L2 Core exposes the device API; **videobuf2** manages the generic capture-buffer lifecycle.
- A camera driver configures its queue and receives ready buffers via callbacks. It does **not** continuously poll a global vb2 queue.
- The camera device `fd` comes from `open("/dev/videoX")`. It is distinct from any buffer reference or DMA-BUF FD.
- `QBUF` gives a buffer to the capture pipeline. `DQBUF` is a **userspace ioctl** that retrieves information about a completed buffer.
- `poll()` or blocking `DQBUF` waits for vb2 readiness; the kernel wakes waiters after the driver reports completion.
- For MMAP capture, `DQBUF` returns metadata; image memory is already mapped into userspace. It does not ordinarily copy the full image frame.
- A buffer must not be returned to capture (`QBUF`) until its current consumer no longer needs its original contents.

## 6. Relevant Linux Source Files

| File | Subject |
|---|---|
| `drivers/media/usb/uvc/uvc_driver.c` | USB matching/probe and device registration |
| `drivers/media/usb/uvc/uvc_v4l2.c` | V4L2 operations and camera configuration |
| `drivers/media/usb/uvc/uvc_queue.c` | vb2 queue callbacks and UVC buffer handling |
| `drivers/media/usb/uvc/uvc_video.c` | USB video transfers and UVC payload handling |
| `drivers/media/common/videobuf2/videobuf2-core.c` | Generic vb2 queue and buffer states |
| `drivers/media/v4l2-core/v4l2-ioctl.c` | V4L2 ioctl dispatch |
| `include/uapi/linux/videodev2.h` | Userspace V4L2 API and buffer structures |

