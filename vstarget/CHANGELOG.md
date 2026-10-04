# Changelog — vstarget Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **`README.md` rewritten to document the CI/CD pipeline.** New sections: repo layout diagram, "How distribution works" flowchart (push → CI → `plugins.json` → Marketplace), updated "Building plugins" covering both CI and local use of `build_plugins.py`, `plugins.json` format reference, and updated "Writing a new plugin" step 6 covering CI-based distribution. Package layout updated to include `ui.py`, `build_zip.py`, and `dist/`.

- **`build_plugins.py` added at the repo root** — generic builder that discovers all plugin directories (any subdirectory containing `plugin.toml`), builds a distribution ZIP for each, and updates the shared `plugins.json` index. Triggered by CI on every push; also usable locally (`python build_plugins.py` or `python build_plugins.py <name>`).

- **`.github/workflows/build-plugin.yml` added** — GitHub Actions workflow that runs `build_plugins.py` on every push to `main` touching `*/plugin.toml` or `*/**/*.py`, then commits the updated ZIPs and `plugins.json` back to the branch.

- **`build_zip.py` converted to a convenience shim.** Delegates to the new repo-level `build_plugins.py`; run `python build_zip.py` from within `vstarget/` as before for local dev. The actual packaging logic now lives in `../build_plugins.py` so it is shared by all plugins in the repo. Run `python build_zip.py` from the repo root to produce `dist/galileo-plugin-vstarget-<version>.zip`, ready to install via Options > Plugins > Install from file or the Marketplace. Reads the version from `plugin.toml`, bundles `plugin.toml` at the archive root plus every `.py` under `vstarget/` (excluding `__pycache__`), and accepts an optional output directory argument.

- **`vstarget/ui.py` — Qt panel pages for both plugin sub-panels.** `build_vst_page()` returns a Variable Star Planning widget with AAVSO target download (by observer section), a filterable target table, and an observation plan builder with ACP script export. `build_vst_analysis_page()` returns a VS Analysis widget with FITS folder selection, photometry parameters, a results table, and AAVSO report export. `VSTPlugin.build_page()` and `VSTAnalysisPlugin.build_page()` in `vstarget/__init__.py` now call these builders instead of returning `None`, so the Science sub-panels show real content rather than a generic placeholder.

- **Initial standalone release.** All code migrated from `galileo/plugins/vstarget/` in the main Galileo repository. Internal imports rewritten from `galileo.plugins.vstarget.*` to `vstarget.*`; `entry_point` in `plugin.toml` set to `"vstarget"` so the package installs correctly as a top-level module via `PluginManager.install()`. Both test suites (`test_vst.py` / `test_vst_an.py`) migrated here with matching import updates. `pyproject.toml` added for `pip install -e ".[test]"` development workflow.
