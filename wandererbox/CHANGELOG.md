# Changelog — wandererbox Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **Initial release.** Switch profiles for three Wanderer Astro products whose INDI drivers use vendor-specific properties instead of INDI's standard power interface: the **WandererBox Pro V3**, the **WandererBox Plus V3** and the **Wanderer Dew Terminator**. Installed, they appear on Galileo's **Equipment > Switches** page like any other power box, with the vendor's port names:
  - **Pro V3** — three USB 3.1 ports individually, the two USB 2.0 groups of three, the adjustable 5–13.2 V pair (with its voltage), the two 12 V groups, three dew heaters, and the 19 V mini-PC and normally-open 12 V ports shown as not switchable.
  - **Plus V3** — all USB ports as one switch, the adjustable 5–13.2 V port (with its voltage), the three 12 V ports as one switch, one dew heater, and the normally-open 12 V port shown as not switchable.
  - **Dew Terminator** — three dew heaters, each usable as a heater (duty) or a plain switch.
  - Readings for input voltage, the currents each model measures, the temperature probes it has (a missing probe shows as no reading rather than −127 °C), ambient humidity and temperature, and dew point. Dew heater duty is shown as 0–100 %.
- **The plugin is data only.** It has no panel, settings or connection of its own, so it cannot disturb the INDI connection; Galileo core shows and drives the devices. It needs a Galileo with switch-profile support (`PLUG-210`) and logs a warning, rather than failing, on an older one.
- **A device is identified by the properties its driver offers, not by its INDI name**, because the Plus V3 driver reports the same default name as the Pro V3 driver. Two boxes of different Wanderer models on one INDI server are therefore not supported.
- **Not covered:** the WandererBox Lite V3, which the vendor states has no control functions, and the dew mode selectors, setpoints and Calibrate of these drivers, which appear in the Switches page's **Other** section with the driver's own labels. The drivers were read from source (the author's fork of INDI) and the profiles are tested against recorded property sets; they have not yet been tried against a real box.
