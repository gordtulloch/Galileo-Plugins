# Changelog

## [Unreleased]

### Added

- Sky routes (`/sky/catalog`, `/sky/ephemeris`, `/sky/site`, `/sky/constellations`, `/sky/check`, `/sky/identify`) over core's `galileo.skystream`, which nothing was running until now, and the frame size on `/preview` for tile addressing.
- First draft of the plugin: the `/mobileconnect/v1` routes, event stream and state translation over core's Connect Link and Control Surface, and an Options page for approving apps, setting roles and revoking them. Not published: the manifest is `plugin.toml.draft`, so CI neither builds nor lists it.
