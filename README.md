# Galileo Plugins

First-party plugins for [Galileo](https://github.com/gordtulloch/Galileo), the cross-platform astrophotography imaging application. Plugins add domain-specific capabilities without touching the core application — distributed as ZIP packages, installed through **Options > Plugins** inside Galileo.

---

## Plugins in this repository

| Plugin | Description | Version |
|--------|-------------|---------|
| [vstarget](#vstarget) | AAVSO variable-star planning and photometric analysis | 1.0.0 |

---

## How distribution works

```
push to main
     │
     ▼
GitHub Actions (build-plugin.yml)
  runs build_plugins.py
     │
     ├─ builds  <plugin>/dist/galileo-plugin-<name>-<version>.zip
     └─ updates plugins.json  ◄── served from raw.githubusercontent.com
                                        │
                                        ▼
                               Galileo's MarketplaceClient
                               (Options > Plugins > Marketplace)
```

Every push to `main` that touches a plugin's source or manifest triggers the CI pipeline, which rebuilds the affected plugin's ZIP and updates the `plugins.json` index at the repo root. Galileo's Plugin Marketplace reads that index directly via `raw.githubusercontent.com` — no separate website, no manual upload.

### Repository layout

```
Galileo-Plugins/
├── build_plugins.py        Generic builder — discovers all plugins, builds ZIPs, updates plugins.json
├── plugins.json            Marketplace index (auto-generated — do not edit by hand)
├── .github/
│   └── workflows/
│       └── build-plugin.yml   CI: rebuild on every push that touches plugin source
└── vstarget/               One directory per plugin
    ├── plugin.toml         Galileo manifest (name, version, API range, entry_point)
    ├── pyproject.toml      Python package metadata and dependencies
    ├── build_zip.py        Convenience shim → delegates to ../build_plugins.py
    ├── dist/               Built ZIPs (committed by CI; do not edit by hand)
    ├── vstarget/           Python package (same name as the plugin)
    └── tests/
```

---

## Installing a plugin

### From the Galileo Plugin Marketplace (recommended)

1. Open Galileo and go to **Options > Plugins > Marketplace**.
2. Find the plugin you want and click **Install**.
3. Restart Galileo. The plugin's panel appears in the sidebar on the next launch.

### From a local ZIP file

1. Download the ZIP from [`vstarget/dist/`](vstarget/dist/) or build it locally (see below).
2. In Galileo go to **Options > Plugins > Installed** and click **Install from file…**
3. Select the ZIP. Restart Galileo.

### From source (development)

```bash
# Clone and install the plugin package in editable mode:
git clone https://github.com/gordtulloch/Galileo-Plugins.git
cd Galileo-Plugins/vstarget
pip install -e ".[test]"
```

Galileo's plugin loader will find the package on `sys.path`. You can also symlink or copy the `vstarget/` source directory into Galileo's plugin data folder (`get_plugins_dir()`) alongside a `plugin.toml`.

---

## Building plugins

### CI (automatic)

The workflow in [`.github/workflows/build-plugin.yml`](.github/workflows/build-plugin.yml) triggers on any push to `main` that changes a `*/plugin.toml` or `*/**/*.py` file. It runs:

```bash
python build_plugins.py
```

then commits the updated ZIPs and `plugins.json` back to the branch with the message `ci: rebuild plugin ZIPs and update plugins.json [skip ci]`.

### Locally

```bash
# Build every plugin and update plugins.json:
python build_plugins.py

# Build one plugin by directory name:
python build_plugins.py vstarget

# From inside a plugin directory (convenience shim):
python vstarget/build_zip.py
```

Output ZIPs land in `<plugin>/dist/`. `plugins.json` is updated at the repo root. Commit and push both — the Marketplace picks up the new version immediately.

### How `build_plugins.py` works

1. Scans the repo root for subdirectories containing `plugin.toml`.
2. For each, reads `name` and `version` from the manifest.
3. Finds the Python package directory named after the plugin (convention: `<plugin_dir>/<name>/`).
4. Packs `plugin.toml` at the archive root + all `.py` files under the package into `<plugin_dir>/dist/galileo-plugin-<name>-<version>.zip`.
5. Writes or updates the matching entry in `plugins.json` with a `raw.githubusercontent.com` download URL.

---

## VSTarget

**Variable-star target planning and photometric analysis** — a first-party Galileo plugin (requirements `VST-*` and `VST-AN-*` in [`docs/plugins/vstarget/`](https://github.com/gordtulloch/Galileo/tree/main/docs/plugins/vstarget)).

VSTarget registers two independent panels inside Galileo's **Science** section:

| Panel | Nav level | Description |
|-------|-----------|-------------|
| Variable Stars | Primary | Download AAVSO targets, build observation plans, export ACP scripts |
| VS Analysis | Secondary | Photometry, transformation coefficients, AAVSO report export |

### What it does

#### Planning (`vstarget.planning`)

- **Sync from AAVSO** — downloads the current target list from the AAVSO Target Tool API, with optional section filtering (`VST-010`).
- **Sort and filter** — sort by priority, magnitude range, or type; filter to targets observable tonight from your configured site (`VST-020`, `VST-030`).
- **Import from file** — load targets from a CSV export (`VST-040`).
- **Build observation plans** — per-filter exposure plans (`filter`, `count`, `exposure_s`, `interval_s`, `binning`) for any target (`VST-050`).
- **Export ACP script** — write an iTelescope ACP `.txt` observing script, targets sorted by RA (`VST-060`).
- **Persist plans** — save and reload observation plans as JSON (`VST-070`).
- **Simbad fallback** — resolve any target name to coordinates via CDS Simbad (`VST-080`).
- **Submit to scheduler** — push a plan into Galileo's multi-night scheduler as a `SchedulerJob` (`VST-090`).

#### Analysis (`vstarget.analysis`)

- **Plate solve** — solve a FITS frame using Galileo's ASTAP backend (`VST-AN-020`).
- **Frame stacking** — mean-stack frames with `astroalign` registration; falls back to unregistered mean if alignment fails (`VST-AN-030`). CPU-bound work dispatched to Galileo's `ProcessPoolExecutor`.
- **Aperture photometry** — ensemble differential magnitude via `photutils`, comparison stars projected through WCS (`VST-AN-040`).
- **AAVSO report export** — AAVSO WebObs Extended-format CSV (`VST-AN-050`).
- **Transformation coefficients** — least-squares fit of Tbv/Tv colour transform from standard-field observations (`VST-AN-060`).
- **Apply transformations** — correct multi-filter `PhotometryResult` sets; passes lone-filter results through unchanged (`VST-AN-070`).
- **Exposure time calculator** — estimate exposure from target magnitude, filter, SNR, and sky brightness (`VST-AN-080`).
- **Finder chart** — DSS image via `astroquery.skyview`, annotated, returned as PNG bytes (`VST-AN-090`).

### Package layout

```
vstarget/
├── plugin.toml                  Galileo plugin manifest
├── pyproject.toml               Build metadata and dependencies
├── build_zip.py                 Shim → ../build_plugins.py vstarget
├── dist/                        Built ZIPs (committed by CI)
├── vstarget/
│   ├── __init__.py              VSTPlugin + VSTAnalysisPlugin (PluginBase subclasses)
│   ├── ui.py                    Qt panel widgets (build_vst_page, build_vst_analysis_page)
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
│       ├── transform_generator.py  compute_transformation_coefficients
│       └── transform_apply.py   apply_transformation
└── tests/
    ├── conftest.py              Shared fixtures (sample FITS, repo tree)
    ├── test_vst.py              TC-VST-010 … TC-VST-090
    └── test_vst_an.py           TC-VST-AN-020 … TC-VST-AN-100
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `galileo` | Host app — `PluginBase`, `SchedulerJob`, `PlateSolver`, `run_cpu` |
| `astropy` | FITS I/O, WCS projection, image normalisation |
| `astroquery` | AAVSO Target Tool, CDS Simbad, DSS SkyView |
| `photutils` | Aperture photometry |
| `astroalign` | Frame registration (GIL-bound — dispatched to process pool) |
| `numpy` | Array maths, polynomial fitting |
| `pandas` | CSV import/export |
| `matplotlib` | Chart rendering (finder charts, light curves) |
| `requests` | HTTP (AAVSO API) |

Requires Python ≥ 3.11.

### Development setup

```bash
cd Galileo-Plugins/vstarget
pip install -e ".[test]"
pytest                          # full suite
pytest -k "vst_010"             # single test by name fragment
pytest tests/test_vst.py        # planning tests only
pytest tests/test_vst_an.py     # analysis tests only
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — `async def test_...` functions run without `@pytest.mark.asyncio`.

Every test is tagged with its RTM requirement ID and priority:

```python
@pytest.mark.requirement("TC-VST-040")
@pytest.mark.priority("MVP")
def test_tc_vst_040_import_from_file(): ...
```

---

## Writing a new plugin

### 1. Create the directory structure

Add a new top-level directory following this layout (the Python package directory **must** have the same name as the `name` field in `plugin.toml`):

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

All fields except `nav_level` and `entry_point` are required. `api_min`/`api_max` must bracket Galileo's current plugin API version (currently `"1"`).

### 3. Subclass `PluginBase`

```python
from galileo.plugins import PluginBase, PluginContext

class MyPlugin(PluginBase):
    name = "MyPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "secondary"   # matches nav_level in plugin.toml
    panel_label = "My Plugin"   # nav button label

    def activate(self, ctx: PluginContext) -> None:
        # Optional: register a device backend or sequencer instruction type.
        # ctx.register_device_backend(category, MyAdapterClass)
        # ctx.register_instruction_type(MyInstructionClass)
        pass

    def deactivate(self) -> None:
        pass

    def build_page(self):
        """Return a QWidget for this panel, or None for a generic placeholder."""
        from PySide6.QtWidgets import QLabel
        return QLabel(f"{self.panel_label}")
```

`activate` and `deactivate` are abstract — both must be implemented. `build_page` defaults to `None` (shows a placeholder).

### 4. `PluginContext` API

| Method | Purpose |
|--------|---------|
| `ctx.register_device_backend(category, cls)` | Add a new device adapter for a `DeviceCategory` (`PLUG-010`, `ARCH-070`) |
| `ctx.register_instruction_type(cls)` | Register a new `SequencerNode` instruction type (`PLUG-020`) |
| `ctx.get_service(name)` | Retrieve a named core service granted to this plugin at load time |

### 5. Nav levels

| `panel_level` | Where it appears |
|---------------|-----------------|
| `"primary"` | Replaces any same-labelled stub in the Science submenu; shown as a full sub-panel icon |
| `"secondary"` | Appended as an additional sub-panel inside Science |

### 6. CI and distribution

Push your new `myplugin/` directory to `main`. The CI pipeline detects the new `plugin.toml`, builds `myplugin/dist/galileo-plugin-myplugin-1.0.0.zip`, adds an entry to `plugins.json`, and commits both back. The plugin then appears in Galileo's Marketplace for all users.

To build locally before pushing:

```bash
python build_plugins.py myplugin
```

To bump a version: update `version` in `plugin.toml`, commit, push. CI handles the rest.

---

## `plugins.json` format

The index is a JSON array. Each entry:

```json
{
  "name": "vstarget",
  "description": "AAVSO variable-star target planning and photometric analysis (VST + VST-AN)",
  "version": "1.0.0",
  "tier": "first_party",
  "author": "Gord Tulloch",
  "download_url": "https://raw.githubusercontent.com/gordtulloch/Galileo-Plugins/main/vstarget/dist/galileo-plugin-vstarget-1.0.0.zip"
}
```

`build_plugins.py` regenerates this file automatically. Do not edit it by hand.

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
| `nav_level` | string | no | `"primary"` or `"secondary"` (default: `"primary"`) |
| `entry_point` | string | no | Importable package name; defaults to `galileo.plugins.<name>` |

---

## Licence

All plugins in this repository are released under the [GNU General Public License v3.0 or later](https://www.gnu.org/licenses/gpl-3.0.html), matching Galileo's own licence.
