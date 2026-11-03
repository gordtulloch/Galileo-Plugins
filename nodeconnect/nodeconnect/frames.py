# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Send new frames soon after they are taken instead of at the next interval (NCON-420).

The imaging service publishes ``FrameCapturedEvent`` when a frame is saved. The Library catalogues and hashes it a
little later, and the transfer scan only queues catalogued frames, so reacting at once would find nothing. This waits
until captures have paused for ``delay_s`` (a run of exposures is one wait, not one per frame), then asks the sync
worker for a pass; the pass's own scan queues whatever has been catalogued by then, and anything not yet is caught by
the next pass. The event never carries a frame into the queue directly, so a wrong or repeated event cannot queue a
file the Library does not hold, and nothing here can fail a capture: the handler only arms a timer.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DELAY_S = 30.0


class FrameWatcher:
    def __init__(self, nudge: Callable[[], None], bus: Any = None, delay_s: float = DEFAULT_DELAY_S) -> None:
        self._nudge, self._bus, self._delay = nudge, bus, delay_s
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        from galileo.bus import FrameCapturedEvent, get_bus
        self._bus = self._bus or get_bus()
        with self._lock:
            if self._running:
                return
            self._running = True
        self._bus.subscribe(FrameCapturedEvent, self._on_frame)

    def stop(self) -> None:
        from galileo.bus import FrameCapturedEvent
        with self._lock:
            self._running = False
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if self._bus is not None:
            self._bus.unsubscribe(FrameCapturedEvent, self._on_frame)

    def _on_frame(self, _event: Any) -> None:
        with self._lock:
            if not self._running:
                return
            if self._timer is not None:
                self._timer.cancel()                               # captures are still arriving: wait for a pause
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
            if not self._running:
                return
        try:
            self._nudge()
        except Exception:
            logger.exception("NodeConnect: could not start a transfer pass after a capture")


__all__ = ["DEFAULT_DELAY_S", "FrameWatcher"]
