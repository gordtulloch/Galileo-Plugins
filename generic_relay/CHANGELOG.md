# Changelog — generic_relay Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **Initial release.** `GenericRelayAdapter` implements Galileo's Switch device port (`galileo.core.devices.DeviceBackend`, `EQP-SW-010`) for relay boards that toggle each relay via a plain HTTP GET rather than any structured API — the common pattern on cheap ESP8266/Arduino-based Ethernet relay boards. Each relay's on/off URL is built from a user-supplied `str.format()` template with `{ip}`/`{port}` (zero-based relay index)/`{state}` (0/1) placeholders, defaulting to `http://{ip}/30000/{port}{state}`, which reproduces `http://10.0.0.101/30000/01` (relay 1 on) / `http://10.0.0.101/30000/00` (relay 1 off) exactly. Boards with a different URL scheme are supported by editing the template, not the code.
- **No readback assumed.** Most boards in this class have no endpoint to ask "is relay 3 on?" — `switches[i].state` reflects the last state this adapter commanded, not a polled hardware readout.
- **Internet Relay panel** (`generic_relay.ui.build_generic_relay_page`) — a tabbed view, one tab per configured board, each a table of that board's relays with a click-to-toggle status cell (On/Off). New boards are added via the "+" button in the tab bar's corner (prompts for host/IP, relay count, and the toggle URL template, pre-filled with the default); closing a tab removes that board without affecting the others. Unlike the Kasa panel there is no poll timer, since there is nothing to poll. The device list (host, relay count, URL template, label) persists across restarts via `QSettings` under the `Galileo` organisation.
- **`TC-RELAY-010` … `TC-RELAY-040` tests added** covering the default template's exact reproduction of the reference example, zero-based relay indexing, custom templates (including `str.format()` field specs like zero-padding), toggle behaviour across multiple relays, the unknown-relay-name error path, and an HTTP failure propagating rather than being swallowed.
