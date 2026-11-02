# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The ``/nodeconnect/v1`` wire (NCON-NFR-030; NCON-050, 120, 140, 150).

Three layers, kept apart so each can be tested alone:

* JSON forms of the sync value types (``*_to_json`` / ``*_from_json``);
* ``MasterEndpoint``: the Master's side of each request as plain ``dict -> dict`` methods, with no
  web framework in sight;
* ``register_routes``: hangs the endpoint on core's Connect Link route registry, one route per
  operation, each declaring the permission it needs (``config.read`` to read, ``config.write`` to
  push). The calling node is always the authenticated principal; a request body never names who
  it is, so a node cannot speak for another.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from nodeconnect import conflict
from nodeconnect.apply import Change
from nodeconnect.codec import PAGE_SIZE
from nodeconnect.master import MasterService, Pushed, PushResult

PLUGIN = "nodeconnect"
API = f"/{PLUGIN}/v1"


# -- JSON forms ----------------------------------------------------------------------------------
def change_to_json(c: Change) -> dict[str, Any]:
    return {"table": c.table, "op": c.op, "key": c.key, "wire": c.wire, "rev": c.rev}


def change_from_json(d: dict[str, Any]) -> Change:
    return Change(str(d["table"]), str(d["op"]), list(d["key"]), d.get("wire"), int(d.get("rev", 0)))


def pushed_to_json(p: Pushed) -> dict[str, Any]:
    return {"table": p.table, "op": p.op, "key": p.key, "wire": p.wire,
            "base_rev": p.base_rev, "modified": p.modified}


def pushed_from_json(d: dict[str, Any]) -> Pushed:
    return Pushed(str(d["table"]), str(d["op"]), list(d["key"]), d.get("wire"),
                  int(d["base_rev"]), float(d["modified"]))


def result_to_json(r: PushResult) -> dict[str, Any]:
    return {"table": r.table, "key": r.key, "status": r.status, "winner": r.winner,
            "rev": r.rev, "op": r.op, "wire": r.wire}


def result_from_json(d: dict[str, Any]) -> PushResult:
    return PushResult(str(d["table"]), list(d["key"]), str(d["status"]), str(d["winner"]),
                      int(d["rev"]), str(d.get("op", "upsert")), d.get("wire"))


class WireError(ValueError):
    """A request the Master refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# -- the Master's side -----------------------------------------------------------------------------
class MasterEndpoint:
    def __init__(
        self, master: MasterService, local_info: Callable[[], dict[str, Any]],
        fingerprints: Callable[[], dict[str, str]], is_master: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time, page_size: int = PAGE_SIZE,
    ) -> None:
        self._master, self._info, self._fingerprints = master, local_info, fingerprints
        self._is_master, self._clock, self._page = is_master, clock, page_size

    def _guard(self) -> None:
        if not self._is_master():
            raise WireError("This node is a spoke of another Master and does not serve nodes.", 409)

    def handshake(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Versions, column fingerprints and the two clock readings the node needs for its offset."""
        self._guard()
        received = self._clock()
        if isinstance(body.get("info"), dict):
            self._master.record_info(node_id, body["info"])
        return {"info": self._info(), "fingerprints": self._fingerprints(),
                "received": received, "sent": self._clock()}

    def pull(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Changes above the node's revisions, a table at a time in pages of at most ``page_size``.
        A page that does not finish its table reports the revision of its last change, so a node
        that stops part-way resumes from the last point it really applied."""
        self._guard()
        since = {str(k): int(v) for k, v in (body.get("since") or {}).items()}
        tables = body.get("tables")
        pages: list[dict[str, Any]] = []
        for entry in self._master.changes_since(since, list(tables) if tables is not None else None):
            changes = entry["changes"]
            if not changes:
                pages.append({"table": entry["table"], "rev": entry["rev"], "changes": []})
                continue
            for i in range(0, len(changes), self._page):
                chunk = changes[i:i + self._page]
                last = i + self._page >= len(changes)
                pages.append({"table": entry["table"], "rev": entry["rev"] if last else chunk[-1].rev,
                              "changes": [change_to_json(c) for c in chunk]})
        return {"pages": pages}

    def push(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._guard()
        try:
            pushed = [pushed_from_json(d) for d in body.get("changes", [])]
            off = body.get("offset") or {}
            offset = conflict.ClockOffset(float(off.get("seconds", 0.0)), float(off.get("error", 0.0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise WireError(f"malformed push: {exc.__class__.__name__}") from exc
        try:
            results = self._master.receive_push(node_id, pushed, offset)
        except ValueError as exc:                                  # a table the Master does not replicate
            raise WireError(str(exc)) from exc
        return {"results": [result_to_json(r) for r in results]}

    def ack(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._guard()
        revs = {str(k): int(v) for k, v in (body.get("revs") or {}).items()}
        self._master.record_ack(node_id, revs)
        return {"ok": True}


# -- Connect Link routes ------------------------------------------------------------------------------
OPERATIONS: tuple[tuple[str, str, str], ...] = (
    # (path suffix, method on the endpoint, permission)
    ("handshake", "handshake", "config.read"),
    ("pull", "pull", "config.read"),
    ("push", "push", "config.write"),
    ("ack", "ack", "config.read"),
)


def register_routes(routes, endpoint: MasterEndpoint) -> None:
    """Register the NodeConnect routes on core's ``RouteRegistry``."""
    from aiohttp import web

    def make(method_name: str):
        method = getattr(endpoint, method_name)

        async def handler(request, principal):
            try:
                body = await request.json() if request.can_read_body else {}
                if not isinstance(body, dict):
                    raise WireError("body must be a JSON object")
                # json.dumps up front: a result that cannot be serialised is our bug, not a 200
                return web.Response(text=json.dumps(method(principal.peer_id, body)),
                                    content_type="application/json")
            except WireError as exc:
                return web.json_response({"error": str(exc)}, status=exc.status)
            except ValueError:
                return web.json_response({"error": "bad request"}, status=400)
        return handler

    for suffix, method_name, permission in OPERATIONS:
        routes.register(PLUGIN, "POST", f"{API}/{suffix}", permission, make(method_name))
