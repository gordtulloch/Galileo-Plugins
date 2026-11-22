# Changelog

## [Unreleased]

### Added

- **The Master's event notices reach the apps (`MCON-330`).** The engine's event feed now carries a `NoticeEvent` for each significant event (an exposure starts, a slew begins or ends, a solve resolves, a focus result, equipment connects or disconnects); the plugin sends each to a connected app as a `notice` stream frame, which the app shows as a brief panel over the top-right corner. Unlike an alert it is not kept or listed by `GET /alerts`. Needs Galileo with event notices (`NOTE-040`) and the apps' matching release. Reinstall the plugin from its zip and restart Galileo. **Checked:** `tests/test_mcon_state.py::test_tc_mcon_330_*`; **not run:** a phone against a live Master.

## [0.1.2] - 2026-11-18

Version bump so the Marketplace offers the Options-page and targets fixes below to installs of 0.1.1.

### Changed

- **The selected app on the MobileConnect Options page is an inverse bar.** The Waiting for approval and Paired apps lists marked the selected row with a faint highlight that was hard to see; the row is now drawn in the page's text colour with its text in the page colour, in any theme and whether or not the list has focus, so it is clear which app Approve, Refuse, Make Viewer, Make Operator or Revoke will act on. Reinstall the plugin from its zip and restart Galileo. **Checked:** the module imports; **not run:** the Options page itself.

### Fixed

- **A saved target with no magnitude or size broke the apps' Targets list.** `GET /mobileconnect/v1/targets` sent `null` for them where `api/openapi.yaml` has 99 (magnitude unknown) and 0 (size unknown), so an app that parses strictly gave up on the whole list ("couldn't give its saved targets"), as it did for any target typed in by hand. Missing values are now sent as the contract's. Reinstall the plugin from its zip and restart Galileo. **Checked:** edit only; **not run:** the plugin tests.

### Fixed

- **Galileo crashed (segmentation fault) while the MobileConnect Options page was open.** The page refreshes its pending-pairing and paired-app lists every 2 seconds by clearing and refilling them while holding Python references to the items; Qt deleting those items under the wrapper crashed the whole desktop (seen in `faulthandler.log` and the macOS crash report as `QListWidget.clear` inside `Shiboken::Object::destroy`). The lists are now rebuilt only when their rows change, with items created by the widget itself. **Checked:** edit only; **not run:** the plugin tests or the desktop. Needs the plugin reinstalled from its zip.

### Added

- **`GET /mobileconnect/v1/sky/labels`.** The name, type (core's `ObjectType`) and size in arcminutes of every deep-sky object, and the name of every star that has a proper name, by their index in the sky catalog stream, so the apps' Star Atlas can label them and draw the icon of their type as the desktop does. Fixed for a given Master and asked for once. Reinstall the plugin from its zip and restart Galileo; the apps still work against the old plugin, with unlabelled square markers.

### Added

- **`GET /mobileconnect/v1/targets/thumbnail?name=&size=`.** The survey picture of one saved target as a JPEG (the same DSS cutout, sized to the object, that the desktop's Targets tiles use, from the desktop's cache when it has it), for the apps' Targets tiles. `size` is pixels (64-400, default 200). 404 when the target isn't saved or no picture could be had (no internet). Reinstall the plugin from its zip and restart Galileo.

- **`flip.deferred` and `flip.deferred_until` state keys**, relayed from `flip.status`, for the apps' new Defer the flip button. Core's `focus.autofocus` progress key needs no change: unknown core keys already pass through under their own name.
- **`GET /mobileconnect/v1/targets` and `DELETE /mobileconnect/v1/targets?name=`.** The Master's saved targets (Planning > Targets, saved from Search or What's Up Tonight and replicated between nodes) for the apps' new Targets screen: name, designations, J2000 position, type, magnitude, size, and a `version` that changes with the list. Any paired app reads it; removing needs control. Acting on an entry uses the existing `mount.slew` and `solve.centre` operations. Needs a Galileo with the saved targets list; an older one answers an empty list with a reason. Reinstall the plugin from its zip and restart Galileo.

### Changed

- **Version 0.1.1.** No change to the plugin's code: the version moves with NodeConnect 0.1.1 so both are offered together. The "MobileConnect could not start; it stays inert" failure (`KEY_VALUES_MISMATCH`) was a Galileo core bug in the Connect Link certificate store and is fixed in Galileo itself; update Galileo as well.

### Changed

- **Published.** The manifest is now `plugin.toml` (it was `plugin.toml.draft`, which kept the builder and the Marketplace from seeing the plugin), so CI builds the ZIP and lists it in the Marketplace with the next push to main.

### Added

- **`source=solve` on the preview and tile routes.** Serves the frame last captured for plate solving or polar alignment (the Master's `solve:<pier>` frame source), so the app can show polar alignment's frame refreshing. Any other value still means the latest exposure.

### Changed

- Refusals that name the lease now say control, in words an app user reads: "X has control of this Pier", "only an Operator can take control". Routes and fields are unchanged.
- The survey tiles are now Mellinger's all-sky Milky Way mosaic from core (continuous, where the Digitized Sky Survey colour tiles were patchy), so tile orders above 4 are "no such tile".

### Added

- **`GET /preview` takes `source=guide`**: the whole frame PHD2 is guiding on, for choosing the guide star by tapping it (the app sends `guiding.select_star`). Anything else still falls back to `current`. Needs a Galileo with the `guide:<pier>` frame reference; on an older one it is simply 404.
- **Exposure and live-stack progress, and the stack picture.** New state keys: `imaging.exposure_s` and `imaging.exposure_progress` (the exposure under way), and `imaging.stack.count`, `.requested`, `.progress`, `.total_exposure_s` and `.method` (null when no stack is being built, which is how an app knows to drop its bar). `GET /preview` and `/tile` take `source=stack` for the live stack; the default, `current`, is the latest single exposure. The capture operation's new `live_stack` argument reaches the app through the catalogue. Needs a Galileo that has the same change.
- `GET /mobileconnect/v1/sky/survey/tile`: core's Digitized Sky Survey tiles for the app to draw behind the stars, with their credit in `X-Image-Credit`. Core fetches and caches them, so a tile any client has seen is served from this computer. Needs a Galileo with `galileo.planning.sky_survey`; on an older one every tile is simply 404.
- `GET /mobileconnect/v1/sky/catalogs`: which deep-sky objects are in Messier, Caldwell and NGC, as core's `catalogs_of` has it, so the app can offer the same catalog choice as the Star Atlas.

### Fixed

- Sky limits are measured with the catalog already loaded. Timing its first load made a fast computer report itself as too slow, which cut every app to 100 objects a page and 3 requests a second.
- A first fetch of the constellation figures, which can take a minute, no longer holds a request up. It carries on in the background and the app gets them next time.

### Added

- Sky routes (`/sky/catalog`, `/sky/ephemeris`, `/sky/site`, `/sky/constellations`, `/sky/check`, `/sky/identify`) over core's `galileo.skystream`, which nothing was running until now, and the frame size on `/preview` for tile addressing.
- First draft of the plugin: the `/mobileconnect/v1` routes, event stream and state translation over core's Connect Link and Control Surface, and an Options page for approving apps, setting roles and revoking them. Not published: the manifest is `plugin.toml.draft`, so CI neither builds nor lists it.
