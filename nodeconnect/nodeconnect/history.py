# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Session history and metrics to the Master (NCON-400, ADR-NCON-006).

Core's ``SessionHistory`` exports its frame records as transfer records keyed by
``(origin_node_id, local_key)`` and imports them as an upsert, so a record received twice, or again
after a partly finished earlier transfer, is one record, and two nodes' histories never collide. This
module only moves them: the spoke sends everything newer than the last timestamp the Master
acknowledged (kept per Master in core's sync state) in batches, and the Master hands each batch to
``import_records``. A failure leaves the cursor where it was, so the next pass resends.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from nodeconnect.wire import API, PLUGIN, WireError

logger = logging.getLogger(__name__)

BATCH = 500
CURSOR = "history_since"
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class HistoryPush:
    def __init__(self, history: Any, store: Any, master_id: str, node_id: str, send: Transport,
                 batch: int = BATCH) -> None:
        self._history, self._store, self._master = history, store, master_id
        self._node, self._send, self._batch = node_id, send, batch

    def run(self) -> int:
        """Send what the Master has not acknowledged; returns the number of records sent."""
        since = self._store.peer_state_get(self._master, CURSOR, "")
        records = sorted(self._history.export_records(self._node, since),
                         key=lambda r: r["frame"]["timestamp"])
        sent = 0
        for i in range(0, len(records), self._batch):
            part = records[i:i + self._batch]
            reply = self._send(f"{API}/history", {"records": part})
            if not reply.get("ok"):
                break
            sent += len(part)
            self._store.peer_state_set(self._master, CURSOR, part[-1]["frame"]["timestamp"])
        return sent


class HistoryEndpoint:
    """The Master's side: upserts a node's records into its own history."""

    def __init__(self, history: Any, node_id: Callable[[], str]) -> None:
        self._history, self._node_id = history, node_id

    def receive(self, body: dict[str, Any]) -> dict[str, Any]:
        records = body.get("records")
        if not isinstance(records, list):
            raise WireError("records must be a list")
        counts = self._history.import_records(self._node_id(), [r for r in records if isinstance(r, dict)])
        return {"ok": True, **counts}


def register_history_route(routes, endpoint: HistoryEndpoint) -> None:
    import asyncio

    from aiohttp import web

    async def handler(peer, request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise WireError("body must be a JSON object")
            return await asyncio.to_thread(endpoint.receive, body)
        except WireError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)
        except ValueError:
            return web.json_response({"error": "bad request"}, status=400)
    routes.register(PLUGIN, "POST", f"{API}/history", "data.push", handler)
