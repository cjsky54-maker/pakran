# DriverThermalQC

Windows desktop application for **thermal inspection and quality control of
electronic driver PCBs**, built for Pakran. It connects to a HIKMICRO Mini2 V2
thermal camera, displays the live thermal image, measures per-region
temperatures, runs automated **Standard** and **Quick** thermal tests,
evaluates PASS/FAIL against per-region maximum allowed ΔT, stores every test
in SQLite, and exports engineering reports to CSV.

The UI is entirely English (LTR), displays dates in the **Solar Hijri
(Jalali) calendar** (e.g. `1405/08/12`) and times in 24-hour format.

---

## Current status

**Phase 1 (complete):** full application foundation — GUI, camera layer with
discovery/reconnection, simulated camera, preset management (immutable,
soft-delete/restore), region editor, SQLite database with snapshots, reports
page with filters and CSV export, backup/restore, rotating logs, automated
test suite (85 tests), PyInstaller packaging.

**Phase 2 (integration point ready, pending SDK):** real radiometric matrix
acquisition from the Mini2 V2. HIKMICRO does not publish a public SDK for
this camera; see `docs/FEASIBILITY.md` and `docs/CAMERA_INTEGRATION.md`.
Until a verified radiometric interface is connected, the application never
fabricates temperatures: all temperature-dependent operations are disabled
when the active camera is not radiometric.

See `docs/FEATURES.md` for the complete implemented/pending feature lists.

## Installation (from source)

Requires Python 3.11+.

```bat
python -m pip install -r requirements.txt
python main.py
```

Useful flags:

| Flag | Effect |
|---|---|
| `--simulated` | attach the clearly-labeled synthetic camera (development) |
| `--data-dir PATH` | override the data directory (default `%LOCALAPPDATA%\DriverThermalQC`) |

## Building the standalone EXE

Option A — on a Windows PC:

```bat
build_windows.bat
```

Option B — automatically via GitHub Actions (Actions → *Build Windows EXE* →
*Run workflow*). The EXE is uploaded as an artifact and attached to a draft
release when a `v*` tag is pushed.

The EXE is a single file, runs from a normal user account, and stores all
data in `%LOCALAPPDATA%\DriverThermalQC`:

```
DriverThermalQC/
    database/thermal_qc.db       main SQLite database (WAL mode)
    database/backups/            automatic exit backup (latest only)
    settings/settings.json       user settings
    logs/app.log                 rotating application log
    exports/                     default CSV export folder
```

## Using the application

1. **Connect the camera** — the app auto-detects a single supported camera
   at startup; use *Reconnect Camera* for the device selection dialog
   (devices, status, refresh, connect).
2. **Select or create a Preset** — the header combo lists active presets
   (device code + version). *Create New Preset* opens the editor: draw
   rectangular regions on the live image, set each region's *Maximum
   Allowed ΔT*, save. Presets are immutable once saved; use *Copy Preset*
   (Settings) to derive a new one. Deleting is soft; restore from Settings.
3. **Live view** — ambient temperature (matrix minimum) above the region
   table; per-region Min/Max/ΔT/Status update continuously; hover the image
   to read the temperature under the cursor; Play/Pause only freezes the
   display, never acquisition.
4. **Quick Test** — one instantaneous snapshot, evaluated immediately and
   saved. The result appears next to the button and in the result panel.
5. **Standard Test** — confirmation dialog (preset, region limits,
   thresholds) → baseline capture → *WAITING FOR TEMPERATURE RISE* until a
   region exceeds its baseline Max by the rise threshold → scheduled
   sampling (5/10/15 s interval) through T60 → the **T60 sample alone**
   decides PASS/FAIL. *Emergency Stop* saves collected data as INCOMPLETE.
   Camera loss: reconnection is attempted for the configured timeout;
   missed samples stay blank (schedule is never shifted); if reconnection
   fails the test ends INCOMPLETE.
6. **Reports** — searchable/sortable table with filters (type, result,
   preset, Jalali date range), test details, permanent deletion (type
   `DELETE`), manual CSV export.
7. **Settings** — rise threshold, waiting timeout, sample interval,
   reconnection timeout, emissivity; preset management; open logs folder.
   Locked while a test runs.

## Measurement model

```
Ambient      = min(entire temperature matrix)          per frame
Region Min/Max = min/max over every pixel in the ROI   no filtering
DeltaT       = Region_Max - Ambient                    per region, per sample
PASS         DeltaT <= Maximum Allowed DeltaT          equality passes
Overall      = PASS iff every region passes at T60
```

Temperatures are Celsius; display shows one decimal, calculations keep full
precision. `TIMEOUT` and `INCOMPLETE` are not thermal failures.

## Project structure

```
main.py                 entry point (argparse: --simulated, --data-dir)
app/
    core/               constants, settings, Jalali calendar, services
    camera/             CameraInterface, simulated/UVC/HIKMICRO-SDK backends,
                        CameraManager (worker thread + auto-reconnect)
    thermal/            ThermalFrame, ambient, region calculator
    testing/            state machine, Standard Test controller, Quick Test
    database/           SQLite manager, models, migrations
    reports/            report queries, CSV exporter
    backup/             atomic backup / restore
    logging/            rotating file logging
    ui/                 main window, camera view, preset editor,
                        reports page, settings page, dialogs
tests/                  85 automated tests (logic + GUI smoke)
docs/                   feasibility report, camera integration guide,
                        features, reference documents
resources/              icons / styles (placeholders)
DriverThermalQC.spec    PyInstaller configuration
build_windows.bat       local build script
.github/workflows/      CI build of the Windows EXE
```

## Testing

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Covers: region Min/Max/Ambient/ΔT, PASS/FAIL boundary (equality passes),
rise trigger, waiting timeout, sample scheduling for 5/10/15 s, T60
decision, missing-T60 handling, camera loss/missed samples, emergency stop,
quick test, preset immutability, soft delete/restore, snapshots, crash
recovery, backup/restore, CSV multi-region schema, Jalali conversion, and a
full GUI smoke test against the simulated camera.

## Known limitations (v1)

- No zoom/pan on the live image (by design for v1).
- No charts in reports (by design for v1).
- Presets cannot be edited after saving (by design; copy instead).
- Radiometric measurements require a radiometric camera backend; with a
  visual-only UVC device the image is shown but tests are disabled.
- The HIKMICRO Mini2 V2 radiometric adapter awaits official SDK access.
- The EXE must be built on Windows (or via the included GitHub Actions
  workflow); PyInstaller does not cross-compile.

## License / third-party notice

Internal Pakran engineering tool. HIKMICRO, Analyzer and Mini2 are
trademarks of Hangzhou Microimage Software Co., Ltd. This project does not
reverse-engineer or redistribute HIKMICRO software; camera integration uses
documented interfaces only (see `docs/CAMERA_INTEGRATION.md`).
