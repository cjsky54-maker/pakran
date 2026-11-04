# Camera Integration Guide

This document explains how the camera layer works, the HIK "HikCamera" UVC
thermal stream support, and where to plug in further interfaces.

## 1. Architecture

```
CameraManager (Qt, main thread)
    └── _AcquisitionWorker (QThread, target-rate pacing)
            └── CameraInterface (backend, one of):
                    ├── SimulatedCamera   synthetic radiometric data
                    ├── UvcCamera         HikCamera raw stream + visual fallback
                    └── HikmicroSdkCamera OFFICIAL SDK adapter (integration point)
```

- The worker reads frames and emits `frameReady(ThermalFrame)`.
- Pacing is target-rate: real cameras block inside `read()` for one frame
  interval, so the worker only sleeps for the leftover time. A HikCamera
  UVC device therefore streams at its native **25 Hz**.
- On read errors the worker closes the device and retries every second
  until the configurable **reconnection timeout** expires, then reports
  `disconnected`.
- The GUI never blocks on camera I/O.

## 2. The contract every backend must honor

Implement `app/camera/camera_interface.py`:

| Method | Requirement |
|---|---|
| `open()` | Raise `CameraError` with a **detailed, user-readable** message on failure |
| `close()` | Never raise |
| `is_open()` | True while the device is open |
| `read_frame()` | Return a `ThermalFrame`; raise `CameraError` on failure |
| `set_emissivity(v)` | Apply or safely no-op |

`ThermalFrame` carries two independent streams:

- `matrix` — float32 NumPy array in **degrees Celsius**, shape (H, W),
  or `None` when the backend cannot provide radiometry.
- `image` — uint8 RGB array for display.

**Hard rule:** if the backend cannot provide true per-pixel temperatures it
must set `matrix=None`. The application then disables all
temperature-dependent features automatically. Estimating temperatures from
image colors is forbidden.

Also provide a `CameraInfo` (key, name, kind, is_radiometric, is_simulated,
details) and a discovery function so the device dialog can list the device.

## 3. HIK "HikCamera" UVC thermal stream (implemented)

HIKMICRO pocket-class cameras (USB VID **2BDF**, "HikCamera"; includes the
Mini2 family and OEM variants such as the HTI HT-203U) expose their thermal
data through the **standard UVC video stream** — the same interface HIKMICRO
Analyzer uses. `UvcCamera` requests the stream with
`CAP_PROP_CONVERT_RGB = 0` (raw bytes, no lossy conversion) and parses every
frame with `app/camera/hik_frame_parser.py`.

### 3.1 Wire layouts (256×192 core, YUYV 256×392 @ 25 fps)

**Default stream mode (200,704 bytes):**

| Bytes | Content |
|---|---|
| 0 – 98,303 | 256×192 LE u16 **raw sensor counts — NOT temperatures** |
| 98,304 – 100,351 | parameters / metadata / padding (refreshes ~2.5 Hz) |
| 100,352 – 198,655 | 256×192 YUYV thermal preview |
| 198,656 – 200,703 | camera-computed temperature summaries / padding |

**Calibrated stream mode 8 (201,248 bytes; OS stacks may truncate to the
advertised 200,704 — the temperature plane ends at byte 102,943, well within
both sizes):**

| Bytes | Content |
|---|---|
| 0 – 4,639 | proprietary header |
| 4,640 – 102,943 | 256×192 LE u16 **temperature codes** |
| 102,944 – 201,247 | 256×192 YUYV thermal preview |

Temperature conversion (confirmed against the manufacturer app):

```
celsius = code / 64.0 - 50.0
```

### 3.2 Safety design (why raw counts are never shown as °C)

A frame is only accepted as radiometric when it passes **all** gates for
several consecutive frames:

1. metadata-window zero fraction (default-mode metadata is mostly padding);
2. every temperature code within −50…550 °C, ≥95 % within −30…250 °C,
   plausible spread;
3. **metadata-window continuity** — in mode 8 the "metadata" window is rows
   183–186 of the temperature plane and correlates ≥0.50 with the adjacent
   rows; default-mode parameters do not;
4. preview luminance correlates ≥0.75 with the temperature plane;
5. two consecutive valid frames (confirmation).

Anything ambiguous falls back to the default layout: live preview only,
camera-reported min/max temperatures logged as informational values.
**Temperatures are never estimated from colors or raw counts.**

### 3.3 Enabling the calibrated stream (mode 8)

The camera boots into the default mode. Switching it to mode 8 requires a
UVC control request to the camera's vendor unit (unit ID 10), documented for
firmware 4.09 (see references):

```
# after streaming has started:
SET_CUR  selector 5, data 03 05        # select stream-configuration command
GET_LEN  selector 3  -> length         # control payload length
SET_CUR  selector 3, data 01 08 + zero padding to length
GET_CUR  selector 3  -> verify byte 0 == 01, byte 1 == 08
```

(bmRequestType 0x21/0xA1, wValue = selector << 8, wIndex = 10 << 8; control
protocol version readable via selector 4, returns `2.0\0`.)

Sending these requests from Python on Windows goes through the UVC driver's
extension-unit KS-property interface (DirectShow `IAMKsPropertySet` /
Media Foundation `IKsControl`) and is not yet implemented here.

**Workaround that works today:** HIKMICRO Analyzer switches the camera to
the calibrated mode and the mode persists while the camera stays connected.
Open Analyzer once, close it, then connect in this application — the
calibrated stream is detected and parsed automatically (measurements and
tests become available). The application banner shows this hint whenever a
HikCamera is connected in default mode.

### 3.3.1 Device identification and bounded opens (v1.0.2)

The device-selection dialog runs a **deep discovery pass in a background
thread**: every openable UVC index is briefly probed with the raw-stream
settings (MSMF, YUY2 256×392, no RGB conversion, short read timeout) and
frames are run through `HikStreamClassifier`. A HIKMICRO camera is therefore
listed as **"HIKMICRO Thermal Camera (USB)"** with an honest *Radiometric*
value:

- calibrated stream → *Yes* ("calibrated, per-pixel temperatures available"),
- default stream → *No* + the Analyzer instructions in the status column.

Since v1.0.3 the dialog opens instantly with a native-call-free list, then
fills in USB cameras in **two background stages**: a shallow enumeration
(brief open per index) lists every device that answers within a second or
two, and the deep probe above afterwards refines the names and the
*Radiometric* column (15-30 s). The whole sequence is cancellable —
Connect/Cancel/close aborts it between native calls — and the camera that
is currently streaming is skipped entirely (its live info is reused), so a
Refresh can never disturb an active stream. When the pass ends with no
cameras (or fails), the dialog says so instead of searching forever.

The mode-8 calibrated frame is 201,248 bytes, but a 256×392 YUY2 request
makes Windows deliver only the advertised 200,704: the frame arrives 544
bytes short with the temperature plane intact and just the preview tail
cut. Mode-8 candidates are therefore **padded to the full size before
validation** (they still have to pass every statistical gate), so the
truncated stream still yields the full temperature matrix and preview.

Opening a camera is **strictly bounded and always off the GUI thread**:

- the open runs inside the acquisition worker thread — the interface shows
  "Connecting..." and never freezes (a failed open previously blocked the
  GUI for ~30 s and looked like a crash);
- MSMF `OPEN_TIMEOUT_MSEC` / `READ_TIMEOUT_MSEC` bound stalled negotiations;
- the raw probe reads at most 5 frames on two backends (MSMF, DirectShow);
- the visual fallback **retries 3×** with 1.5 s delay — right after the raw
  probe releases the device, the OS may need a moment before the same
  device can be reopened (previously surfaced as a spurious
  "did not open" error);
- the reconnection loop **reopens** the camera after a read error instead
  of spinning on a closed device until the deadline.

### 3.4 References

- Krastanov/ht-203u-linux (CC0): HT-203U USB protocol notes, frame layouts,
  mode-8 command sequence, temperature formula — the primary source for
  the constants in `app/core/constants.py`.
- HIKMICRO Analyzer behavior on the same USB stream.

## 4. Completing the HIKMICRO SDK adapter (`app/camera/hikmicro_camera.py`)

The file already contains the full scaffolding:

1. Install the official SDK per the manufacturer's license. Make its Python
   bindings importable (module name `hikmicro_thermal_sdk`,
   `hikmicro_sdk`, or set `HIKMICRO_SDK_MODULE` / place the package on
   `PYTHONPATH`). `sdk_available()` then returns True.
2. In `HikmicroSdkCamera`:
   - `open()`: enumerate SDK devices, open by `device_index`, apply
     emissivity from settings.
   - `read_frame()`: pull the radiometric frame; convert to °C float32
     (verify the SDK's native units and scale!), build the display image
     (SDK-rendered or colormap), return `ThermalFrame(matrix=…, image=…,
     source="hikmicro-sdk")`.
   - `close()`: release the device.
   - `set_emissivity()`: map to the SDK parameter (verify supported range;
     default 0.95).
3. Extend `discover_hikmicro_cameras()` to enumerate SDK-visible devices
   with real names.
4. Verify against real hardware: resolution (expected 256×192), frame
   rate, coordinate convention (x/y origin), temperature sanity against a
   known reference **before** PASS/FAIL decisions are trusted.

No GUI, database or test-workflow changes are required — everything above
already consumes `ThermalFrame`.

## 5. Behavioral reference: HIKMICRO Analyzer (Client 2.2.2)

Used **only** as a UX/behavior reference, never as a code dependency:

- USB camera detection at startup; single camera auto-connects, multiple
  cameras open a selection dialog (our `DeviceSelectionDialog`).
- Live thermal view with palette; hover/click temperature readout (our
  cursor readout on the image widget).
- Region-based measurement with min/max cursors (our region table).

## 6. Testing the integration

- Keep `SimulatedCamera` workflows green: `python -m pytest`.
- `tests/test_hik_frame_parser.py` builds byte-exact synthetic frames for
  both wire layouts and verifies the safety gates (default frames with
  dense metadata, decorrelated previews, out-of-range codes and truncated
  frames are all rejected).
- `tests/test_uvc_hik_camera.py` replays synthetic frames through a fake
  `cv2.VideoCapture` and verifies the full pipeline (raw open probe →
  parsing → device info updates → `ThermalFrame` contents → visual
  fallback for plain webcams).
- Add a hardware smoke script once the SDK is present:
  connect, stream 100 frames, assert shape/finite values/monotonic
  timestamps, compare a blackbody reading within ±2 °C.
- The GUI smoke test (`tests/test_app_smoke.py`) constructs the whole
  application with the simulated camera and must keep passing unchanged
  after the real adapter lands.

## 7. Policy

No Analyzer injection/memory reading, no undocumented DLLs, no UI
automation of Analyzer, no redistribution of HIKMICRO binaries. The HikCamera
UVC stream is read through the operating system's documented video stack,
and the frame layout is parsed from publicly documented protocol notes
(CC0-licensed research, credited above).
