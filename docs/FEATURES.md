# Feature Status

## Implemented and tested (Phase 1)

### Application shell
- English LTR UI, Fusion style, fixed-size live image (512×384), no zoom/pan
- Header: title, preset selector, camera status LED (green/red), Reconnect
  Camera, Reports / Settings navigation
- Jalali date display (`1405/08/12`) with 24-hour time; UTC storage
- Window size remembered; close-during-test confirmation
- Offscreen-tested GUI smoke suite; 85 automated tests passing

### Camera layer
- `CameraInterface` abstraction with worker-thread acquisition
- Auto-detection at startup (single device auto-connect, multiple → dialog)
- Device selection dialog: devices, interface, radiometric flag, status,
  refresh, connect
- Idle auto-reconnection every 5 s while disconnected
- Configurable reconnection timeout; detailed USB/SDK error messages
- Backends: Simulated (synthetic radiometric, clearly labeled), UVC
  (visual-only, measurements disabled), HIKMICRO SDK integration point
- Emissivity passthrough (settings → camera)
- Play/Pause display freeze (acquisition continues); tests force live view

### Presets
- Creation editor: draw rectangles, auto IDs (A, B, C… AA), unique fixed
  colors, per-region Max Allowed ΔT, ≥1 region required, save/clear/delete
- Immutability after save (no edit API exists)
- Copy preset (new internal ID; source untouched)
- Soft delete + restore (original ID, geometry, colors, limits preserved)
- Historical tests keep full preset snapshots

### Measurement & tests
- Ambient = matrix minimum per frame (no Ambient ROI)
- Region Min/Max over all ROI pixels; ΔT = Max − Ambient; equality passes
- Quick Test: instantaneous snapshot, immediate PASS/FAIL, saved, result
  next to button + result panel
- Standard Test: confirmation dialog, baseline capture, waiting for rise
  (configurable threshold/timeout), monotonic-clock scheduling 5/10/15 s,
  T60-only decision, TIMEOUT / INCOMPLETE semantics
- Emergency Stop (no final snapshot), abort on application close
- Camera loss during test: reconnect window, blank missed slots, unchanged
  schedule, INCOMPLETE on give-up
- Result panel with per-region failure details and Copy Failure Text

### Data
- SQLite (WAL, FKs, transactions, migrations, indexes)
- Tests/samples/region-measurements with preset + settings snapshots
- Crash recovery: unfinished tests → INCOMPLETE on next start
- Startup integrity check with explicit restore flow; corrupt DB preserved
- Automatic exit backup (atomic, latest-only) with validation
- Reports page: search, filters (type/result/preset/Jalali date range),
  sorting, multi-select, details dialog, permanent delete (type DELETE),
  manual CSV export (union schema, blanks for missing regions/slots,
  overwrite confirmation)
- Rotating logs (2 MB × 3) + Open Logs Folder; settings lock during tests

## Pending official SDK / radiometric access (Phase 2)

- Live radiometric matrix from the Mini2 V2 (needs HIKMICRO OEM SDK —
  request via techsupport; adapter slot ready in
  `app/camera/hikmicro_camera.py`)
- Real-hardware verification of resolution (256×192), frame rate, units,
  emissivity range
- Optional RJPEG file-based radiometric workflow (capture → USB copy →
  parse) as a manual complement
- Windows EXE built via GitHub Actions (workflow included; run it or push
  a `v*` tag)

## Deliberately excluded in v1 (per specification)

- Region move/resize after creation; preset editing after save
- Zoom/pan; report charts; automatic CSV synchronization; installer
- Distance / reflected temperature / humidity UI; palette selection;
  manual temperature scale; multi-monitor selection; operator name field
