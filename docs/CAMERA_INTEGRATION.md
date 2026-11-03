# Camera Integration Guide

This document explains how the camera layer works and exactly where to plug
in the real HIKMICRO Mini2 V2 radiometric interface when it becomes
available.

## 1. Architecture

```
CameraManager (Qt, main thread)
    └── _AcquisitionWorker (QThread, ~10–15 fps)
            └── CameraInterface (backend, one of):
                    ├── SimulatedCamera   synthetic radiometric data
                    ├── UvcCamera         visual-only stream (OpenCV)
                    └── HikmicroSdkCamera OFFICIAL SDK adapter (integration point)
```

- The worker reads frames and emits `frameReady(ThermalFrame)`.
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

## 3. Completing the HIKMICRO SDK adapter (`app/camera/hikmicro_camera.py`)

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

## 4. Behavioral reference: HIKMICRO Analyzer (Client 2.2.2)

Used **only** as a UX/behavior reference, never as a code dependency:

- USB camera detection at startup; single camera auto-connects, multiple
  cameras open a selection dialog (our `DeviceSelectionDialog`).
- Live thermal view with palette; hover/click temperature readout (our
  cursor readout on the image widget).
- Region-based measurement with min/max cursors (our region table).

## 5. Testing the integration

- Keep `SimulatedCamera` workflows green: `python -m pytest`.
- Add a hardware smoke script once the SDK is present:
  connect, stream 100 frames, assert shape/finite values/monotonic
  timestamps, compare a blackbody reading within ±2 °C.
- The GUI smoke test (`tests/test_app_smoke.py`) constructs the whole
  application with the simulated camera and must keep passing unchanged
  after the real adapter lands.

## 6. Policy

No reverse engineering, no Analyzer injection/memory reading, no
undocumented DLLs, no UI automation of Analyzer, no redistribution of
HIKMICRO binaries. Documented interfaces only.
