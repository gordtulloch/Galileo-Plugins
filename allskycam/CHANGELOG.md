# Changelog — allskycam Galileo plugin

All notable changes to this plugin are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).

## [Unreleased]

### Added

- **Initial release.** Shows the newest image from an indi-allsky all-sky camera in its own top-level Galileo sidebar section (`panel_level = "top"`, custom lens-and-horizon icon) and in an optional dockable animation pane.
- **No indi-allsky changes needed.** indi-allsky has no REST API, but its existing `/latestimage` route 302-redirects to the newest processed image of a camera (`?camera_id=N`). `AllSkyClient` follows that redirect by hand, so an image whose address is unchanged since the last check is skipped without being downloaded; failures (unreachable server, HTTP error, a login page, a non-image or oversized response) become readable messages in the status line rather than exceptions.
- **Latest / Animation / Settings tabs.** *Latest* shows the image as downloaded with its fetch time; *Animation* loops the last *n* images at a chosen speed (the timer runs only while the view is visible); *Settings* holds the options below.
- **Options** (stored in Galileo's database under `plugin.AllSkyCamPlugin`, via `ctx.settings()`): server address, camera number, check interval (10 s – 1 h), images in animation (2 – 120), **Pause during daytime** with a Sun-altitude limit, animation speed, and **Show animation pane**. **Test connection** checks the form's values off the UI thread without saving them. Out-of-range or corrupt stored values are clamped rather than trusted.
- **Optional daytime pause.** Computed locally from the selected Observatory's latitude/longitude using Galileo's solar position, not indi-allsky's day/night flag. If the Observatory has no location the plugin keeps fetching and says why.
- **Memory-bounded animation.** Frames are held as compressed JPEG/PNG bytes and decoded (downscaled) only when shown; a 256 MB cap drops the oldest frames first.
- **Inert when disabled.** Disabling the plugin in Options > Plugins stops fetching and closes the pane; enabling resumes both.
- **In-app help** for every control (`help/plugin.AllSkyCamPlugin.md`).
- **`TC-ASC-010` … `TC-ASC-090` tests** (53), including an end-to-end run against a local HTTP server that behaves like indi-allsky's redirect, and an audit that every control has a help entry.
- Requires a Galileo with top-level and dockable plugin panels, `ctx.settings()` and `ctx.site_location()` (`PLUG-130` … `PLUG-160`).
