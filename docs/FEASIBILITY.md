# Technical Feasibility Report — HIKMICRO Mini2 V2 Integration

**Document:** Wl-FT-01 · **Date:** 2026-11-03 · **Status:** Phase 1 delivered,
Phase 2 pending verified radiometric access

---

## 1. Target device

| Item | Value |
|---|---|
| Device | HIKMICRO Mini2 V2 (smartphone thermal module) |
| Sensor | 256 × 192 uncooled VOx microbolometer |
| Frame rate | 25 Hz |
| Measurement range | −20 … 400 °C (manufacturer page; some retailers state 350 °C) |
| Accuracy | ±2 °C / ±2 % |
| Connection | USB-C (Android) / Lightning adapter (iPhone) |
| Power | Host-powered (~360 mW), no battery or screen |
| Official PC software | HIKMICRO Analyzer |
| Official mobile app | HIKMICRO Viewer |

Sources: HIKMICRO official product page for Mini2 V2
(hikmicrotech.com/en_us/industrial-products/mini2-v2-thermal-camera-phone-attachment),
Mini2 V2 datasheet (termokamery-hikmicro.cz, 2024-06-19), independent spec
database thethermogram.com (verified September 2026).

## 2. Investigation summary

### 2.1 Official public SDK

- **No public SDK download exists for the Mini2 V2.** The HIKMICRO software
  download center (hikmicrotech.com/en_us/support/download-center/software-download)
  publishes end-user software (Analyzer, firmware, tools); deeper SDK access
  is granted by contacting support (e.g. techsupport.na@hikmicrotech.com),
  typically under OEM/industrial agreements.
- Analyzer itself does **not** expose a documented public API. That Analyzer
  can talk to a camera is not evidence of an integrator-facing SDK.
- Conclusion: an official SDK path exists but requires a manufacturer
  request and acceptance of their license terms. Timeline is outside our
  control.

### 2.2 UVC (standard USB video)

- HIKMICRO documents UVC "cast screen" for **other** series (FT31: "cast
  the screen to a PC by UVC protocol-based client software"; SP series:
  "connect to PC via type-C and cast the real-time live view"). For the
  **Mini2 V2** — a phone-attached module driven by the HIKMICRO Viewer app —
  no official PC UVC mode is documented.
- If the device (or future firmware) does expose UVC, it would carry
  **imagery only**. UVC never carries a radiometric matrix; the project
  rules therefore forbid deriving temperatures from it.
- The application implements a best-effort UVC backend (`UvcCamera`) that
  shows the live image and **disables** all temperature-dependent features.
  Behaviour on the real device must be verified on a Windows PC with the
  camera attached (Device Manager → Cameras).

### 2.3 Radiometric file transfer (RJPEG)

- HIKMICRO's official FAQ confirms: *"The standard radiometric jpeg file
  contains the thermal image, temperature data and the visual image"*,
  analyzed with HIKMICRO Analyzer (hikmicrotech.com FAQ, 2023-04-28).
- Community tooling parses HIKMICRO RJPEG in Python (e.g. the open-source
  `meltyfat` proof-of-concept for the HIKMICRO Pocket 2, which extracts the
  per-pixel temperature matrix from captured images).
- This yields a **file-based, non-live** radiometric workflow: capture RJPEG
  on the device → copy over USB (mass-storage) → parse matrix → evaluate.
  It is legally clean and technically feasible today, but it is not a live
  stream and does not satisfy the automated Standard Test sampling
  schedule without operator action.

### 2.4 Legality constraints (project policy)

Explicitly prohibited and **not implemented**: reverse-engineering the
proprietary USB protocol, injecting code into Analyzer, reading Analyzer's
process memory, depending on undocumented internal DLLs, automating
Analyzer's UI as a substitute API, or redistributing HIKMICRO components.

## 3. What was delivered (Phase 1)

- Full application: GUI, presets, SQLite, tests, reports, backup, logging.
- Camera abstraction (`CameraInterface`) with three backends:
  - **SimulatedCamera** — synthetic 256×192 radiometric source, clearly
    identified everywhere as simulated; used for development and the
    automated test suite.
  - **UvcCamera** — visual-only live stream; measurements disabled.
  - **HikmicroSdkCamera** — clean, documented integration point that
    refuses to connect until official SDK bindings are installed and the
    adapter is completed and verified against real hardware
    (`app/camera/hikmicro_camera.py`).
- The GUI, database and test workflow require **zero changes** when the
  radiometric backend lands — Phase 2 only fills in one class.

## 4. Recommended Phase 2 options (in order)

| Option | Radiometric? | Live? | Risk | Notes |
|---|---|---|---|---|
| A. Official HIKMICRO SDK (request via techsupport) | Yes | Yes | License/timeline outside our control | Preferred end-state; adapter slot ready |
| B. RJPEG file workflow | Yes | No (per-capture) | Low | Good manual fallback; could complement Quick Test |
| C. UVC visual stream | No | Yes | Low | Display-only; already implemented |

**Recommendation:** request the official SDK (Option A) now — cite the
Pocket/Mini series and the intended PC quality-control use case — while
keeping the UVC display path for operator convenience. Do not attempt
protocol reverse-engineering.

## 5. Verification checklist for real hardware

When a Mini2 V2 is available on a Windows PC:

1. Enumerate USB devices (Device Manager): note VID/PID and any camera
   class device.
2. Run the app → device dialog → check whether a UVC stream appears.
3. If SDK access is granted: install bindings, implement
   `HikmicroSdkCamera.read_frame()`, verify actual resolution, frame rate,
   pixel format and temperature units against the datasheet and a blackbody
   or reference measurement before enabling PASS/FAIL decisions.
4. Confirm emissivity range and defaults from the SDK documentation.

No claim of successful camera integration may be made until steps like
these pass on real hardware.
