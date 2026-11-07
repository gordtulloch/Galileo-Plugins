# MobileConnect (draft)

The server side of the [Galileo MobileConnect](https://github.com/gordtulloch/Galileo-MobileConnect) apps: the `/mobileconnect/v1` routes over Connect Link, operation only, no configuration.

**Status: draft, not published.** The manifest is `plugin.toml.draft` on purpose: `build_plugins.py` and CI publish any directory with a `plugin.toml` to the Marketplace on a push to `main`. Rename it when the plugin should ship. To try it now, copy it to `plugin.toml` locally and use *Options > Plugins > Install from file*.

- **Scope and design:** `docs/plugins/mobileconnect/` in the Galileo repository. The draft's differences from that design are below.
- **The contract:** `api/openapi.yaml` and `api/events.schema.json` in Galileo-MobileConnect are the source of truth, and the apps are generated from them.

## What it does

| Part | What |
|---|---|
| `routes.py` | The routes. Every operation goes through core's shared `Gateway`, so lease, confirmation, idempotency, safety and audit are core's |
| `events.py` | The event stream: the engine's feed, sampled status and alerts |
| `state.py` | Core's status results and state fragments, translated to the state keys the apps read |
| `clients.py`, `ui.py` | Approving apps, roles and revocation, from the Master's Options page only |

## Not in this draft

Spoke Piers through the relay; session and schedule authoring routes; sky streaming; FITS download routes (apps can use Connect Link's own transfer); outreach; the load governor beyond the Gateway's own; idle-lease and disconnect release beyond what core's broker does; a surface for other plugins.

## Differences from the plugin SDD

- **Flat routes with query parameters** (`POST /ops?pier=&name=`), not `/pier/{pier}/ops/{name}`: Connect Link's route table matches exact paths.
- **The core stream** (`/connect/v1/stream`), with the app asking for a snapshot by `POST /snapshot`: the hub has no connection hook.
- **Sampling.** Core publishes few state fragments, so the plugin reads `*.status` operations every 2 s while an app is connected, and sends only what changed. Nothing is read while no app is connected.
- **One stretch value**, as `galileo.preview` has, not black, mid and white points.

## Tests

```bash
PYTHONPATH=../../Galileo pytest      # from this directory, on Python 3.11, with Galileo's dependencies
```

They run on core's real peers, pairing, lease, registry and Gateway, and a real listener; only the devices are fake.
