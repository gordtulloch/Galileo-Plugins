# Changelog — kasa_switch Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **Initial release.** `kasa_switch.protocol` re-implements the TP-Link Kasa local-network wire format (length-prefixed, XOR-"encrypted" JSON over TCP 9999) directly against the standard library, following the protocol documented by [Python-KasaSmartPowerStrip](https://github.com/p-doyle/Python-KasaSmartPowerStrip), rather than depending on the third-party `python-kasa` package — partly to keep the dependency footprint to just `galileo`, and partly to avoid a module-name collision, since that package also claims the top-level `kasa` name (hence this plugin is named `kasa_switch`, not `kasa`).
- **`KasaSwitchAdapter`** implements Galileo's Switch device port (`galileo.core.devices.DeviceBackend`, `EQP-SW-010`) for both a single-outlet plug (HS100/HS103/HS105/KP115, reporting one switch named after the device) and a multi-outlet power strip (HS300/KP303/EP40, reporting one switch per `children` entry, named after each outlet's own Kasa-app alias).
- **Kasa Switches panel** (`kasa_switch.ui.build_kasa_page`) — a tabbed view, one tab per configured device, each a table of that device's switches with a click-to-toggle status cell (On/Off) and a 5-second poll so externally-triggered state changes (the Kasa mobile app, a schedule) show up without a manual refresh. New devices are added via the "+" button in the tab bar's corner (prompts for host/IP and an optional label); closing a tab removes that device without affecting the others. The device list persists across restarts via `QSettings` under the `Galileo` organisation, the same pattern `vstarget.settings` uses.
- **`TC-KASA-010` … `TC-KASA-040` tests added** covering the wire protocol's round-trip and framing, single-plug connect/toggle, power-strip connect/toggle scoped to one child outlet, and the unknown-switch-name error path.
