# Changelog

## [Unreleased]

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
