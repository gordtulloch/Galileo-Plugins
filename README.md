# Galileo Plugins

This repository contains first-party plugins for [Galileo](https://github.com/gordtulloch/galileo), the cross-platform astrophotography imaging application. Plugins add domain-specific capabilities without modifying the core application — they are distributed as ZIP packages, installed through Options > Plugins inside Galileo, and discovered automatically on the next launch.

---

## Plugins in this repository

| Plugin | Description | Version |
|--------|-------------|---------|
| [vstarget](#vstarget) | AAVSO variable-star planning and photometric analysis | 1.0.0 |

---

## Installing a plugin

### From the Galileo Plugin Marketplace (recommended)

1. Open Galileo and go to **Options > Plugins > Marketplace**.
2. Find the plugin you want and click **Install**.
3. Restart Galileo. The plugin's panel will appear in the sidebar on the next launch.

### From a local ZIP file

1. Download or build the plugin ZIP (see [Building a ZIP](#building-a-zip) below).
2. Go to **Options > Plugins > Installed** and click **Install from file…**
3. Select the ZIP. Restart Galileo.

### From source (development)

```bash
pip install -e "C:/Projects/Galileo-Plugins/vstarget[test]"
```

The package installs as `vstarget` into your active Python environment. Galileo's plugin loader finds it in `sys.path` once the installed-plugin directory is on the path, or you can place the source directory directly under `get_plugins_dir()`.

---

## VSTarget

**Variable-star target planning and photometric analysis** — a first-party Galileo plugin (requirements `VST-*` and `VST-AN-*` in `docs/plugins/vstarget/`).

VSTarget integrates AAVSO variable-star planning directly into Galileo's scheduler and automates the photometry pipeline after each session. It ships as one installable package (`vstarget`) that registers two independent panels:

| Panel | Level | Description |
|-------|-------|-------------|
| Variable Stars | Primary nav | Browse AAVSO targets, build and export observation plans |
| VS Analysis | Secondary nav | Photometry, transformation coefficients, AAVSO report submission |

### What it does

#### Planning (`vstarget.planning`)

- **Sync from AAVSO** — downloads the current target list from the AAVSO Target Tool API, with optional section filtering (`VST-010`).
- **Sort and filter** — sort targets by priority, magnitude range, or type; filter to observable-tonight targets from your configured observing site (`VST-020`, `VST-030`).
- **Import from file** — load targets from a CSV export (`VST-040`).
- **Build observation plans** — assemble per-filter exposure plans (`filter`, `count`, `exposure_s`, `interval_s`, `binning`) for any target (`VST-050`).
- **Export ACP script** — write an iTelescope ACP `.txt` observing script, targets sorted by RA (`VST-060`).
- **Persist plans** — save and reload observation plans as JSON (`VST-070`).
- **Simbad fallback** — resolve any target name to coordinates via CDS Simbad (`VST-080`).
- **Submit to scheduler** — push a plan directly into Galileo's multi-night scheduler as a `SchedulerJob` (`VST-090`).

#### Analysis (`vstarget.analysis`)

- **Plate solve** — solve a FITS frame using Galileo's ASTAP backend (`VST-AN-020`).
- **Frame stacking** — mean-stack a folder of FITS frames with `astroalign` registration; falls back to unregistered mean if alignment fails (`VST-AN-030`). CPU-bound work is dispatched to Galileo's shared `ProcessPoolExecutor` to avoid stalling the UI.
- **Aperture photometry** — measure target and comparison stars by projecting AAVSO comparison-star coordinates through the frame's WCS, then computing an ensemble differential magnitude via `photutils` (`VST-AN-040`).
- **AAVSO report export** — write an AAVSO WebObs Extended-format CSV ready for submission (`VST-AN-050`).
- **Transformation coefficients** — least-squares fit of colour transformation coefficients (Tbv, Tv) from standard-field observations (`VST-AN-060`).
- **Apply transformations** — correct multi-filter `PhotometryResult` sets using stored coefficients; handles B/V and V/R pairs, passes lone-filter results through unchanged (`VST-AN-070`).
- **Exposure time calculator** — estimate required exposure time from target magnitude, filter, desired SNR, and sky brightness (`VST-AN-080`).
- **Finder chart** — fetch a DSS image via `astroquery.skyview`, annotate, and return PNG bytes (`VST-AN-090`).
- **SFTP downloader** — retrieve FITS files from a remote telescope via SFTP (`VST-AN-010`).

### Package layout

```
vstarget/
├── plugin.toml                  Galileo plugin manifest
├── pyproject.toml               Build metadata and dependencies
├── vstarget/
│   ├── __init__.py              VSTPlugin + VSTAnalysisPlugin (PluginBase subclasses)
│   ├── planning/
│   │   ├── __init__.py          VariableStarPlanner, AavsoTargetToolClient, SimbadClient
│   │   ├── models.py            AavsoTarget, ObservationPlan, FilterConfig, TransformationCoefficients
│   │   ├── planner.py           VariableStarPlanner (central planning service)
│   │   ├── aavso_client.py      AAVSO Target Tool API client
│   │   ├── simbad_client.py     CDS Simbad resolver
│   │   ├── script_exporter.py   ACP script writer
│   │   └── database.py          JSON plan persistence
│   └── analysis/
│       ├── __init__.py          VariableStarAnalysis (pipeline coordinator)
│       ├── photometry.py        AperturePhotometryEngine, PhotometryResult
│       ├── exposure.py          ExposureTimeCalculator
│       ├── finder_chart.py      FinderChartRenderer
│       ├── platesolve.py        solve_fits (ASTAP wrapper)
│       ├── stack.py             stack_frames (astroalign + asyncio)
│       ├── report.py            AAVSO WebObs Extended CSV writer
│       ├── sftp_downloader.py   SftpImageRetriever re-export
│       ├── transform_generator.py  compute_transformation_coefficients
│       └── transform_apply.py   apply_transformation
└── tests/
    ├── conftest.py              Shared fixtures (sample FITS, repo tree)
    ├── test_vst.py              TC-VST-010 … TC-VST-090
    └── test_vst_an.py           TC-VST-AN-010 … TC-VST-AN-090
```

### Dependencies

Runtime:

| Package | Purpose |
|---------|---------|
| `galileo` | Host app — `PluginBase`, `SchedulerJob`, `PlateSolver`, `run_cpu`, `SftpImageRetriever` |
| `astropy` | FITS I/O, WCS projection, image normalisation |
| `astroquery` | AAVSO Target Tool, CDS Simbad, DSS SkyView |
| `photutils` | Aperture photometry |
| `astroalign` | Frame registration (GIL-bound — dispatched to process pool) |
| `paramiko` | SFTP image retrieval |
| `numpy` | Array maths, polynomial fitting |
| `pandas` | CSV import/export |
| `matplotlib` | Chart rendering (finder charts, light curves) |
| `requests` | HTTP (AAVSO API) |

Requires Python ≥ 3.11 (matches Galileo's floor — Raspberry Pi OS Bookworm).

### Development setup

```bash
cd C:/Projects/Galileo-Plugins/vstarget
pip install -e ".[test]"
pytest                          # full suite
pytest -k "vst_010"             # single test by ID
pytest tests/test_vst.py        # planning tests only
pytest tests/test_vst_an.py     # analysis tests only
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — async test functions run without `@pytest.mark.asyncio`.

Every test is tagged with its RTM requirement ID and priority:

```python
@pytest.mark.requirement("TC-VST-040")
@pytest.mark.priority("MVP")
def test_tc_vst_040_import_from_file(): ...
```

### Building a ZIP

To produce an installable ZIP for the Galileo Plugin Marketplace or manual install:

```bash
cd C:/Projects/Galileo-Plugins/vstarget
# Include plugin.toml at the ZIP root alongside the vstarget/ package directory.
python -m zipfile -c vstarget-1.0.0.zip plugin.toml vstarget/
```

The resulting `vstarget-1.0.0.zip` can be placed under `Galileo.web/assets/plug-ins/` to make it available through the in-app marketplace.

---

## Writing a new plugin

### 1. Create the package structure

```
myplugin/
├── plugin.toml
├── pyproject.toml
└── myplugin/
    └── __init__.py
```

### 2. Write `plugin.toml`

```toml
name        = "myplugin"
version     = "1.0.0"
api_min     = "1"
api_max     = "1"
author      = "Your Name"
description = "What it does in one sentence."
tier        = "third_party"
nav_level   = "secondary"    # "primary" | "secondary"
entry_point = "myplugin"     # top-level importable package name
```

All fields are required. `api_min`/`api_max` must bracket Galileo's current plugin API version (currently `"1"`).

### 3. Subclass `PluginBase`

```python
from galileo.plugins import PluginBase, PluginContext

class MyPlugin(PluginBase):
    name = "MyPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "secondary"   # where in the nav this panel appears
    panel_label = "My Plugin"   # nav button label

    def activate(self, ctx: PluginContext) -> None:
        # Register device backends, sequencer instructions, etc.
        # ctx.register_device_backend(category, MyAdapterClass)
        # ctx.register_instruction_type(MyInstructionClass)
        pass

    def deactivate(self) -> None:
        pass

    def build_page(self):
        """Return a QWidget for this panel, or None for a placeholder."""
        from PySide6.QtWidgets import QLabel
        return QLabel(f"{self.panel_label} — not yet implemented")
```

`PluginBase` is an abstract class; both `activate` and `deactivate` must be implemented. `build_page` is optional — return `None` to get a placeholder page.

### 4. `PluginContext` API

| Method | Purpose |
|--------|---------|
| `ctx.register_device_backend(category, cls)` | Add a new device adapter for a `DeviceCategory` (`PLUG-010`, `ARCH-070`) |
| `ctx.register_instruction_type(cls)` | Register a new `SequencerNode` instruction type (`PLUG-020`) |
| `ctx.get_service(name)` | Retrieve a named service granted to this plugin at load time |

### 5. Galileo nav levels

| `panel_level` | Where it appears |
|---------------|-----------------|
| `"primary"` | Top-level sidebar button, same row as Star Atlas / Imaging / Planning |
| `"secondary"` | Sub-panel within an existing section (currently Science) |

### 6. Build and distribute

```bash
python -m zipfile -c myplugin-1.0.0.zip plugin.toml myplugin/
```

Users install via **Options > Plugins > Install from file…** or through a marketplace index page.

---

## Plugin manifest reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique plugin identifier (lowercase, no spaces) |
| `version` | string | yes | Semantic version string (`"1.0.0"`) |
| `api_min` | string | yes | Minimum Galileo plugin API version |
| `api_max` | string | yes | Maximum Galileo plugin API version |
| `author` | string | yes | Author name |
| `description` | string | yes | One-line description shown in the Marketplace |
| `tier` | string | yes | `"first_party"` or `"third_party"` |
| `nav_level` | string | no | `"primary"` (default) or `"secondary"` |
| `entry_point` | string | no | Importable package name; defaults to `galileo.plugins.<name>` |

---

## Licence

All plugins in this repository are released under the [GNU General Public License v3.0 or later](https://www.gnu.org/licenses/gpl-3.0.html), matching Galileo's own licence.
