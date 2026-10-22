# Changelog — switchbot_bot Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **Initial release.** Controls SwitchBot Bot devices over Bluetooth LE as Galileo Switch devices (`EQP-SW-010`), mainly to power a ZWO Seestar on and off with a Bot over its power button.
- **Own BLE layer on `bleak`, not `switchbotpy`.** `switchbot_bot.protocol` re-implements the Bot GATT commands (press `57 01`, on `57 01 01`, off `57 01 02`, password variant `57 11` + CRC32, settings `57 02`) following [switchbotpy](https://github.com/RoButton/switchbotpy) (MIT). switchbotpy itself runs on `pygatt`/`pexpect` over BlueZ's `gatttool`, which only works on Linux, and Galileo supports Windows and macOS. `bleak` is imported lazily, so without it the plugin still loads and the panel says to `pip install bleak`.
- **`SwitchBotAdapter`** — one Bot is one switch; dual-state Bots extend/retract on On/Off, press-mode Bots press for both. The state shown is the last command sent, because a Bot cannot report it.
- **SwitchBot panel** on Equipment > Switches: **Scan for SwitchBots**, **Add by address…** (label, optional password), and per-Bot **On / Off / Press / Check** with battery and mode. Bots are not polled, to spare their batteries. Built-in help in `help/plugin.SwitchBotPlugin.md`.
- **`TC-SBOT-010` … `TC-SBOT-040` tests** for command framing, status codes, settings parsing, switching in both modes, error paths and scan filtering, all against a faked BLE layer (no Bluetooth hardware needed).
