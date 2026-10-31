# WebConnect

Browser control of a running Galileo observatory — the web counterpart of the MobileConnect apps. **Status: in design; there is no code here yet.**

WebConnect is built in this folder (the plugin, its Flask applications and the Debian packaging) together with the core facilities in the [Galileo](https://github.com/gordtulloch/Galileo) repository. There is no separate WebConnect repository: the former `Galileo-WebConnect` repository was retired on 2026-10-31 and held only a preliminary scope document.

## Where the design is

All in the Galileo repository, under [`docs/plugins/webconnect/`](https://github.com/gordtulloch/Galileo/tree/main/docs/plugins/webconnect):

| Document | What it covers |
|---|---|
| `PSD.md` | Scope. Section 6.5 is the deployment on Astroberry OS: behind Astroberry's Caddy front door, beside Astroberry Manager |
| `SRS.md` | 120 numbered requirements (`WCON-*`) |
| `SDD.md` | Design: the Master half, the web half, the Astroberry and self-hosted hosting profiles |
| `RTM.md` | Requirement → design → test traceability |

## Two hosting profiles

- **Astroberry** (primary) — a systemd service behind Astroberry's Caddy, at `https://<device name>.local/galileo/`, with a Galileo entry in Astroberry Manager. Distributed as a Debian package; packaging and the APT repository are shared with the [galileo-astroberry](https://github.com/gordtulloch/galileo-astroberry) project.
- **Self-hosted** — for a Master that is not an Astroberry system: the plugin supervises the process, which serves its own HTTPS listener. Distributed as a plugin ZIP.

## Planned layout

```
webconnect/
  plugin.toml          plugin manifest (identity WCON)
  webconnect/          Python package: Master half, web half, outreach application
  debian/              Debian packaging for the Astroberry profile
```

Nothing above exists yet. When `plugin.toml` is added, `build_plugins.py` will start building the ZIP and a `plugins.json` entry will list it.
