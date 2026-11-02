# NodeConnect (in development)

Node-to-node configuration sync and history/image transfer to a designated Master.
Scope: [`docs/plugins/nodeconnect/PSD.md`](https://github.com/gordtulloch/Galileo/blob/main/docs/plugins/nodeconnect/PSD.md) in the Galileo repository.

Built so far: the pure logic modules (`roles`, `codec`, `conflict`, `compat`) with tests. Not built: the plugin shell, pairing, sync engine, apply step, snapshots/reports, transfer worker and UI.
This folder deliberately has no `plugin.toml`, so `build_plugins.py` does not build or list it until the plugin is installable.

Run the tests from this folder with Galileo installed: `pytest tests`.
