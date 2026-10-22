# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Background fetching and shared state for the AllSkyCam plugin (ASC-020, ASC-050, ASC-060).

One :class:`AllSkyService` per plugin feeds every view (the sidebar page and the dock pane), so an
image is downloaded once however many places show it. Downloads run on a worker thread -- never the
UI thread -- and at most one is in flight; a tick that arrives while one is running is skipped.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from allskycam import cycle
from allskycam.buffer import FrameBuffer
from allskycam.client import AllSkyClient, AllSkyError
from allskycam.config import AllSkyConfig
from allskycam.cycle import CycleResult, run_cycle

logger = logging.getLogger(__name__)


class _ResultSignal(QObject):
    done = Signal(object)


class _Job(QRunnable):
    """Runs *fn* on a pool thread and delivers its return value on the signal's (UI) thread."""

    def __init__(self, fn: Callable[[], Any], signal: _ResultSignal) -> None:
        super().__init__()
        self._fn = fn
        self._signal = signal

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # never let a pool thread die silently
            logger.exception("AllSkyCam background job failed")
            result = CycleResult(cycle.ERROR, f"Unexpected error: {exc}")
        try:
            self._signal.done.emit(result)
        except RuntimeError:
            pass        # the service was destroyed (app exit, plugin removed) while this job ran


class AllSkyService(QObject):
    frame_added = Signal(object)        # the new Frame
    status_changed = Signal(str)
    config_changed = Signal()
    test_finished = Signal(bool, str)   # (ok, message) from test_connection

    def __init__(
        self,
        settings: Any,
        site_provider: Callable[[], Any] | None = None,
        cycle_fn: Callable[..., CycleResult] = run_cycle,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._site_provider = site_provider
        self._cycle_fn = cycle_fn
        self._config = AllSkyConfig.from_dict(settings.all())
        self.buffer = FrameBuffer(self._config.frame_count)
        self._known_url: str | None = None
        self._status = ""
        self._running = False
        self._busy = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)         # one fetch + one connection test at a time
        self._fetch_signal = _ResultSignal()
        self._fetch_signal.done.connect(self._on_fetch_done)
        self._test_signal = _ResultSignal()
        self._test_signal.done.connect(self._on_test_done)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.fetch_now)
        self._set_status(self._idle_status())

    # --- state ---------------------------------------------------------------------------

    @property
    def config(self) -> AllSkyConfig:
        return copy.copy(self._config)

    @property
    def status(self) -> str:
        return self._status

    @property
    def running(self) -> bool:
        return self._running

    def _set_status(self, text: str) -> None:
        self._status = text
        self.status_changed.emit(text)

    def _idle_status(self) -> str:
        if not self._config.configured:
            return "Not configured — enter your indi-allsky address under Options > AllSkyCam."
        return "Waiting for the first image…"

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        """Begin fetching now and then every configured interval (a no-op if already running)."""
        if self._running:
            return
        self._running = True
        self._timer.start(self._config.interval_s * 1000)
        QTimer.singleShot(0, self.fetch_now)

    def stop(self) -> None:
        """Stop fetching. A download already in flight finishes but its result is discarded."""
        self._running = False
        self._timer.stop()

    def apply_config(self, config: AllSkyConfig) -> None:
        """Adopt and persist new options. A different server or camera starts the animation over."""
        old = self._config
        self._config = copy.copy(config)
        self._settings.update(self._config.to_dict())
        if (old.url, old.camera_id) != (config.url, config.camera_id):
            self.buffer.clear()
            self._known_url = None
        self.buffer.resize(config.frame_count)
        if self._running:
            self._timer.start(config.interval_s * 1000)
        self.config_changed.emit()
        if not config.configured:
            self._set_status(self._idle_status())
        elif self._running:
            self.fetch_now()

    def set_show_dock(self, visible: bool) -> None:
        """Remember whether the dock pane is open, without touching anything else."""
        if self._config.show_dock == visible:
            return
        self._config.show_dock = visible
        self._settings.set("show_dock", visible)
        self.config_changed.emit()

    # --- fetching ------------------------------------------------------------------------

    def fetch_now(self) -> None:
        if not self._running or self._busy:
            return
        if not self._config.configured:
            self._set_status(self._idle_status())
            return
        config = copy.copy(self._config)
        location = None
        if config.pause_daytime and self._site_provider is not None:
            try:
                location = self._site_provider()
            except Exception:
                logger.exception("Site-location provider failed")
        known = self._known_url
        self._busy = True
        self._pool.start(_Job(lambda: self._cycle_fn(config, location, known), self._fetch_signal))

    def _on_fetch_done(self, result: CycleResult) -> None:
        self._busy = False
        if not self._running:
            return
        now = datetime.now().astimezone().strftime("%H:%M:%S")
        if result.kind == cycle.FRAME and result.frame is not None:
            self._known_url = result.frame.url
            self.buffer.append(result.frame)
            self._set_status(f"Updated {now} — {len(self.buffer)} image(s) held")
            self.frame_added.emit(result.frame)
        elif result.kind == cycle.UNCHANGED:
            self._set_status(f"No new image at {now}")
        elif result.kind == cycle.PAUSED:
            self._set_status(result.message)
        else:
            self._set_status(f"Error at {now}: {result.message}")

    # --- connection test -----------------------------------------------------------------

    def test_connection(self, config: AllSkyConfig) -> None:
        """Try *config* once (not saved, not added to the animation) and report via ``test_finished``."""
        def _try() -> tuple[bool, str]:
            try:
                frame = AllSkyClient(config.url, config.camera_id).fetch_latest(None)
            except (AllSkyError, ValueError) as exc:
                return False, str(exc)
            except Exception as exc:  # noqa: BLE001 -- a pool thread must report, not die
                return False, f"Unexpected error: {exc}"
            if frame is None:
                return False, "indi-allsky returned no image."
            return True, f"Connected — received a {len(frame.data) / 1024:.0f} kB image."

        self._pool.start(_Job(_try, self._test_signal))

    def _on_test_done(self, result: Any) -> None:
        if isinstance(result, tuple):
            self.test_finished.emit(*result)
        else:                                   # _Job's last-resort CycleResult for an unexpected crash
            self.test_finished.emit(False, getattr(result, "message", "Test failed"))
