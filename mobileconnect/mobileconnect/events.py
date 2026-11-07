# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""What reaches the apps' event stream: the engine's feed, sampled status, and alerts.

Runs on Connect Link's event loop, where ``StreamHub.publish`` must be called. It only works while an app is
connected, so a node with the plugin enabled and nobody watching does nothing but check once in a while.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from collections.abc import Callable
from typing import Any

from mobileconnect import PLUGIN
from mobileconnect.state import (
    SAMPLED,
    SAMPLED_SLOWLY,
    TRACE_KEY,
    StateTracker,
    now_text,
    plain,
    translate,
    translate_observable,
)

logger = logging.getLogger(__name__)

SAMPLE_S = 2.0
SLOW_EVERY = 15                       # ticks between the occasional reads (30 s)
FEED_WAIT_S = 1.0
ALERT_LIMIT = 200
SAMPLER = f"{PLUGIN}:sampler"         # the principal on status reads: read-class, so it never needs a lease
OP_EVENTS = {"OperationStarted": "op.started", "OperationProgress": "op.progress", "OperationFinished": "op.finished"}
ALERT_SEVERITY = {"DeviceErrorEvent": "warning", "DeviceDisconnectedEvent": "warning"}


class EventPump:
    def __init__(self, surface: Callable[[], Any], describe_holder: Callable[[str], tuple[str | None, str | None]],
                 broadcast: Callable[[dict[str, Any]], Any], watching: Callable[[], bool]) -> None:
        self._surface, self._describe = surface, describe_holder
        self._broadcast, self._watching = broadcast, watching
        self.tracker = StateTracker()
        self.alerts: deque[dict[str, Any]] = deque(maxlen=ALERT_LIMIT)
        self._tasks: list[asyncio.Task] = []
        self._ticks = 0
        self._unsafe: dict[str, bool] = {}
        self._wake = asyncio.Event()

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        if not self._tasks:
            self._tasks = [asyncio.ensure_future(self._feed_loop()), asyncio.ensure_future(self._sample_loop())]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def wake(self) -> None:
        """An app has just connected: don't wait out the idle sleep."""
        self._wake.set()

    async def _idle(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), SAMPLE_S)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    # -- sending -------------------------------------------------------------------------------
    def _send_changes(self, pier: str, keys: dict[str, Any]) -> None:
        for key, value in self.tracker.update(pier, keys).items():
            self._broadcast({"type": "delta", "pier": pier, "key": key, "value": value, "as_of": now_text()})

    def raise_alert(self, severity: str, message: str, pier: str = "", important: bool = False) -> None:
        alert = {"type": "alert", "id": secrets.token_hex(6), "severity": severity, "time": now_text(),
                 "message": message, "important": important}
        if pier:
            alert["pier"] = pier
        self.alerts.append(alert)
        self._broadcast(alert)

    # -- the engine's feed ---------------------------------------------------------------------
    async def _feed_loop(self) -> None:
        feed, after = None, 0
        while True:
            svc = self._surface()
            current = getattr(svc, "feed", None)
            if current is None:
                feed = None
                await self._idle()
                continue
            if current is not feed:
                feed, after = current, current.last_id
            if not self._watching():
                # Keep the place, so what happens as an app connects still reaches it.
                after = current.last_id
                await self._idle()
                continue
            try:
                news = await asyncio.to_thread(feed.read, after, FEED_WAIT_S)
                after = news["last"]
                if news.get("gap"):                  # the feed moved past us: the next samples put it right
                    logger.info("MobileConnect fell behind the engine's feed")
                for entry in news["entries"]:
                    self.handle_feed_entry(entry)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MobileConnect could not read the engine's feed")
                await asyncio.sleep(SAMPLE_S)

    def handle_feed_entry(self, entry: dict[str, Any]) -> None:
        kind = entry.get("type")
        if kind == "state":
            self._send_changes(str(entry["pier"]), translate_observable(entry["key"], entry.get("value")))
        elif kind == "lease":
            holder, name = self._describe(entry.get("holder") or "")
            self._broadcast({"type": "lease", "pier": str(entry["pier"]), "holder": holder, "holder_name": name})
        elif kind == "event":
            self._handle_bus_event(entry.get("name", ""), entry.get("payload") or {})

    def _handle_bus_event(self, name: str, payload: dict[str, Any]) -> None:
        phase = OP_EVENTS.get(name)
        if phase:
            event = {"type": phase, "correlation_id": payload.get("correlation_id", ""),
                     "pier": payload.get("pier"), "operation": payload.get("operation")}
            if payload.get("fraction") is not None:
                event["progress"] = payload["fraction"]
            if payload.get("message"):
                event["message"] = payload["message"]
            if phase == "op.finished":
                event["outcome"] = payload.get("outcome", "ok")
                if payload.get("reason"):
                    event["reason"] = payload["reason"]
            self._broadcast(event)
        elif name in ALERT_SEVERITY:
            what = payload.get("device") or payload.get("category") or "A device"
            detail = payload.get("error") or payload.get("message") or name.removeprefix("Device").removesuffix("Event").lower()
            self.raise_alert(ALERT_SEVERITY[name], f"{what}: {detail}", str(payload.get("pier") or ""))

    # -- sampling ------------------------------------------------------------------------------
    async def _sample_loop(self) -> None:
        while True:
            await asyncio.sleep(SAMPLE_S)
            try:
                if self._watching():
                    await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MobileConnect could not sample state")

    async def sample_once(self) -> None:
        svc = self._surface()
        if svc is None:
            return
        slow = self._ticks % SLOW_EVERY == 0
        self._ticks += 1
        for pier in [str(p["id"]) for p in svc.piers()]:
            for operation in (*SAMPLED, *(SAMPLED_SLOWLY if slow else ())):
                await self._sample(svc, pier, operation)

    async def _sample(self, svc: Any, pier: str, operation: str) -> None:
        if svc.registry.spec(operation) is None:
            return
        outcome = await svc.registry.invoke(operation, {}, pier, SAMPLER)
        if outcome.status != "ok":
            device = operation.split(".")[0]
            if operation in ("mount.status", "camera.status", "rotator.status", "dome.status", "focus.status", "filter.names"):
                self._send_changes(pier, {f"{device}.connected": False})
            return
        value = plain(outcome.value)
        keys = translate(operation, value)
        if operation == "guiding.status":
            for sample in self.tracker.new_trace_samples(pier, value):
                self._broadcast({"type": "delta", "pier": pier, "key": TRACE_KEY, "value": sample, "as_of": now_text()})
        elif operation == "safety.status":
            self._alert_on_safety(pier, keys)
        self._send_changes(pier, keys)

    def _alert_on_safety(self, pier: str, keys: dict[str, Any]) -> None:
        unsafe = keys.get("safety.overall") == "unsafe"
        if unsafe and not self._unsafe.get(pier):
            why = keys.get("safety.inputs", {}).get("Safety monitor", "unsafe")
            self.raise_alert("critical", f"Unsafe: {why}", pier, important=True)
        self._unsafe[pier] = unsafe


__all__ = ["EventPump"]
