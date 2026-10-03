# Changelog — vstarget Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **Initial standalone release.** All code migrated from `galileo/plugins/vstarget/` in the main Galileo repository. Internal imports rewritten from `galileo.plugins.vstarget.*` to `vstarget.*`; `entry_point` in `plugin.toml` set to `"vstarget"` so the package installs correctly as a top-level module via `PluginManager.install()`. Both test suites (`test_vst.py` / `test_vst_an.py`) migrated here with matching import updates. `pyproject.toml` added for `pip install -e ".[test]"` development workflow.
