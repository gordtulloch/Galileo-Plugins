# Changelog — vstarget Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **`vstarget/ui.py` — Qt panel pages for both plugin sub-panels.** `build_vst_page()` returns a Variable Star Planning widget with AAVSO target download (by observer section), a filterable target table, and an observation plan builder with ACP script export. `build_vst_analysis_page()` returns a VS Analysis widget with FITS folder selection, photometry parameters, a results table, and AAVSO report export. `VSTPlugin.build_page()` and `VSTAnalysisPlugin.build_page()` in `vstarget/__init__.py` now call these builders instead of returning `None`, so the Science sub-panels show real content rather than a generic placeholder.

- **Initial standalone release.** All code migrated from `galileo/plugins/vstarget/` in the main Galileo repository. Internal imports rewritten from `galileo.plugins.vstarget.*` to `vstarget.*`; `entry_point` in `plugin.toml` set to `"vstarget"` so the package installs correctly as a top-level module via `PluginManager.install()`. Both test suites (`test_vst.py` / `test_vst_an.py`) migrated here with matching import updates. `pyproject.toml` added for `pip install -e ".[test]"` development workflow.
