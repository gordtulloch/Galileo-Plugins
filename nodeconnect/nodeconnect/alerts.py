# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Notifications (NCON-340): tell the person through Galileo's notification service when a sync has been failing for
a while, when a pairing request is waiting on the Master, and when a conflict was resolved.

Each of the three is a switch on NodeConnect's Options page (stored in the plugin's settings) and none of them is on
by surprise: they default on, because each means something needs a person. They go out as session messages
(``NotificationEvent.SESSION_MESSAGE``) over whatever channels Options > Notifications has set up, so e-mail, SMS and
webhook recipients are configured once, there. A failing sync is reported once per run of failures, not at every retry,
and again only after a success in between. Sending never raises into a sync.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

SYNC_FAILING = "notify_sync_failing"
PAIRING_REQUEST = "notify_pairing_request"
CONFLICT = "notify_conflict"
FAILING_AFTER_MIN = "notify_failing_after_min"
DEFAULT_FAILING_AFTER_MIN = 30
MIN_FAILING_AFTER_MIN = 1
MAX_FAILING_AFTER_MIN = 24 * 60


def failing_after_s(settings: Any) -> float:
    """How long a sync must have been failing before it is reported, in seconds, clamped to a sane range."""
    try:
        minutes = float(settings.get(FAILING_AFTER_MIN, DEFAULT_FAILING_AFTER_MIN))
    except (AttributeError, TypeError, ValueError):
        minutes = DEFAULT_FAILING_AFTER_MIN
    return max(MIN_FAILING_AFTER_MIN, min(MAX_FAILING_AFTER_MIN, minutes)) * 60.0


def default_send(detail: str) -> None:
    """Send *detail* through the notification service as configured in Options > Notifications."""
    from galileo.notify import NotificationEvent, NotificationService
    from galileo.notify_settings import configure_service, load_notify_settings
    service = NotificationService()
    configure_service(service, load_notify_settings(), None)
    asyncio.run(service.emit(NotificationEvent.SESSION_MESSAGE, detail))


class Alerts:
    def __init__(self, settings: Any, send: Callable[[str], None] = default_send,
                 clock: Callable[[], float] = time.time) -> None:
        self._settings, self._send, self._clock = settings, send, clock
        self._failing_since: float | None = None
        self._reported = False
        self._seen_requests: set[str] = set()

    def _on(self, key: str) -> bool:
        return bool(self._settings.get(key, True)) if self._settings is not None else True

    def _emit(self, detail: str) -> None:
        try:
            self._send("NodeConnect: " + detail)
        except Exception:
            logger.exception("NodeConnect: could not send a notification")

    def sync_result(self, result: Any) -> None:
        """Feed every sync outcome here (the sync worker's ``on_result``)."""
        state = getattr(result, "state", "")
        if state == "backoff":
            now = self._clock()
            if self._failing_since is None:
                self._failing_since = now
            if (not self._reported and self._on(SYNC_FAILING)
                    and now - self._failing_since >= failing_after_s(self._settings)):
                self._reported = True
                minutes = int((now - self._failing_since) // 60)
                self._emit(f"sync with the Master has been failing for {minutes} minute(s): {getattr(result, 'message', '')}")
            return
        self._failing_since, self._reported = None, False         # it worked: the next failure starts a new count
        if getattr(result, "conflicts", 0) and self._on(CONFLICT):
            self._emit(f"{result.conflicts} conflicting change(s) were resolved during the last sync; "
                       "the replaced values are kept in Recent syncs and can be undone.")

    def pairing_requests(self, request_ids: Iterable[str]) -> None:
        """Feed the Master's waiting pairing requests here; each new one is reported once."""
        waiting = set(request_ids)
        new = waiting - self._seen_requests
        self._seen_requests = waiting
        if new and self._on(PAIRING_REQUEST):
            self._emit(f"{len(new)} computer(s) are waiting to pair. Approve or reject them under Options > NodeConnect.")


__all__ = ["CONFLICT", "DEFAULT_FAILING_AFTER_MIN", "FAILING_AFTER_MIN", "PAIRING_REQUEST", "SYNC_FAILING", "Alerts",
           "default_send", "failing_after_s"]
