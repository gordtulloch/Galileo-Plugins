# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Master push (NCON-150): tell connected nodes there is something to pull, without waiting for their interval.

Both halves send only a *hint* (``{"type": "pull_hint"}``) over Connect Link's event stream; the node then pulls
through the ordinary, revisioned route (``/nodeconnect/v1/pull``). A hint carries no data, so a lost or replayed one
costs one extra pull and never a lost or doubled change, and a node that was offline simply pulls when it next
starts or reconnects (NCON-120). Nodes never talk to one another: only the Master publishes.

* :class:`HintBroadcaster` (Master): publish a hint to every paired, un-revoked node except the one that caused it.
* :class:`MasterWatch` (Master): notice the Master's own edits (rows marked dirty) and hint all nodes once per change.
* :class:`HintListener` (spoke): keep a stream open to the Master and call back on each hint, reconnecting with a
  growing delay.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections.abc import Callable, Iterable
from typing import Any

from galileo.connect_link.server import API as LINK_API

logger = logging.getLogger(__name__)

HINT = {"type": "pull_hint"}
WATCH_INTERVAL_S = 10.0


class HintBroadcaster:
    def __init__(self, streams: Callable[[], Any], node_ids: Callable[[], Iterable[str]],
                 post: Callable[[Callable[[], Any]], Any] | None = None) -> None:
        """*streams* gives core's ``StreamHub`` (or ``None`` while the listener is not running); *node_ids* the peer
        ids of paired, un-revoked nodes. The hub belongs to the listener's event loop: code already on that loop (a
        route handler) calls ``notify`` directly, any other thread uses ``notify(from_thread=True)``, which runs it
        through *post* (call a function on that loop and wait)."""
        self._streams, self._node_ids, self._post = streams, node_ids, post

    def notify(self, except_node: str = "", from_thread: bool = False) -> int:
        """Hint every node but *except_node*. Returns how many had a stream to receive it (the others catch up
        by pulling when they connect)."""
        if from_thread:
            if self._post is None:
                return 0
            return int(self._post(lambda: self._send(except_node)) or 0)
        return self._send(except_node)

    def _send(self, except_node: str) -> int:
        hub = self._streams()
        if hub is None:
            return 0
        sent = 0
        for node_id in self._node_ids():
            if node_id != except_node and hub.publish(node_id, dict(HINT)) is not None:
                sent += 1
        return sent


class MasterWatch:
    """The Master's own edits mark rows dirty but publish no event, so look for them. A hint goes out when the set of
    dirty rows changes, not for as long as rows stay dirty."""

    def __init__(self, broadcaster: HintBroadcaster, signature: Callable[[], object],
                 interval_s: float = WATCH_INTERVAL_S, on_tick: Callable[[], None] | None = None) -> None:
        """*on_tick* runs on every look, for other Master-side housekeeping that polls (waiting pairing requests)."""
        self._b, self._signature, self._interval, self._on_tick = broadcaster, signature, interval_s, on_tick
        self._last: object = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> bool:
        """One look; ``True`` when it hinted the nodes."""
        if self._on_tick is not None:
            try:
                self._on_tick()
            except Exception:
                logger.exception("NodeConnect: Master housekeeping failed")
        try:
            now = self._signature()
        except Exception:
            logger.exception("NodeConnect: could not look for the Master's own edits")
            return False
        changed = now != self._last and bool(now)
        self._last = now
        if changed:
            self._b.notify(from_thread=True)
        return changed

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._last = self._safe_signature()
        self._thread = threading.Thread(target=self._loop, name="nodeconnect-watch", daemon=True)
        self._thread.start()

    def _safe_signature(self) -> object:
        try:
            return self._signature()
        except Exception:                                          # noqa: BLE001
            return None

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.check()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)


def dirty_signature() -> tuple[tuple[str, int], ...]:
    """What identifies the Master's pending local edits: the count of dirty rows per replicated table."""
    from galileo.replication import registry, tracking
    counts = {spec.table: len(tracking.dirty_rows(spec.table)) for spec in registry.specs()}
    return tuple(sorted((t, n) for t, n in counts.items() if n))


class HintListener:
    """The spoke's stream to the Master. Runs on its own thread and event loop; *on_hint* is called from that thread."""

    def __init__(self, address: str, token: str, fingerprint: str, on_hint: Callable[[], None],
                 retry_s: tuple[float, float] = (2.0, 120.0)) -> None:
        self._address, self._token, self._fingerprint = address.rstrip("/"), token, fingerprint
        self._on_hint, self._retry = on_hint, retry_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self.connected = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="nodeconnect-hints", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        loop, thread, self._thread = self._loop, self._thread, None
        if loop is not None:
            loop.call_soon_threadsafe(lambda: [t.cancel() for t in asyncio.all_tasks(loop)])
        if thread is not None and thread is not threading.current_thread():
            thread.join(10)

    def _run(self) -> None:
        loop = self._loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._main())
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()
            self._loop = None

    async def _main(self) -> None:
        delay = self._retry[0]
        while not self._stopping.is_set():
            try:
                await self._listen()
                delay = self._retry[0]                             # a connection that worked resets the back-off
            except asyncio.CancelledError:
                raise
            except Exception as exc:                               # noqa: BLE001 - the Master may be down; try again
                logger.info("NodeConnect: hint stream to the Master lost (%s)", exc)
            self.connected.clear()
            if self._stopping.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(self._retry[1], delay * 2)

    async def _listen(self) -> None:
        import aiohttp
        digest = bytes.fromhex(self._fingerprint.replace(":", ""))
        url = self._address.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"{LINK_API}/stream"
        async with aiohttp.ClientSession() as session, session.ws_connect(
                url, headers={"Authorization": f"Bearer {self._token}"}, ssl=aiohttp.Fingerprint(digest),
                heartbeat=20.0) as ws:
            self.connected.set()
            self._on_hint()                                        # catch up on whatever happened while disconnected
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = msg.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if (isinstance(data, dict) and data.get("type") == "pull_hint") or payload.get("type") == "resync":
                    self._on_hint()


def fingerprint_of(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


__all__ = ["HINT", "HintBroadcaster", "HintListener", "MasterWatch", "dirty_signature", "fingerprint_of"]
