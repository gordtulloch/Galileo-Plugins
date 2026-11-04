# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Running the web process (SDD §3.2; WCON-030, 040, 070, 910; self-hosted profile).

The web half is a separate operating-system process: a fault in it cannot reach imaging, and gevent's
patching cannot reach Galileo. Configuration and the service credential go to it as one JSON line on its
standard input (never the command line, the environment or a file). A heartbeat comes back; a missing heartbeat
or an exit restarts it with a growing delay, and stopping the plugin sends ``shutdown``, waits briefly, then
ends the process, so a disabled plugin leaves no listener and no process behind.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_S = 15.0
BACKOFF_START_S, BACKOFF_CAP_S = 1.0, 60.0
ALERT_AFTER_FAILURES = 3

# ``-I`` isolates the interpreter from the user's site and environment; the plugin's own folder and its private
# dependency folder (PLUG-200) are added by hand, since isolation ignores PYTHONPATH.
# gevent must patch sockets before anything else imports them, or one slow call to the Master stalls every browser.
# Threads stay real: the stdin reader and heartbeat block on a pipe, which would stall a cooperative scheduler.
BOOT = ("from gevent import monkey; monkey.patch_all(thread=False, subprocess=False); "
        "import sys; sys.path[:0] = {paths!r}; import webconnect.web.__main__ as m; sys.exit(m.main())")


def child_command(plugin_dir: Path, deps_dir: Path | None = None, python: str | None = None) -> list[str]:
    paths = [str(plugin_dir)] + ([str(deps_dir)] if deps_dir else [])
    return [python or sys.executable, "-I", "-c", BOOT.format(paths=paths)]


def default_spawn(argv: Sequence[str]) -> subprocess.Popen:
    return subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1)


class Supervisor:
    """Keeps one web process running while started."""

    def __init__(self, argv: Sequence[str], config: Callable[[], dict[str, Any]],
                 spawn: Callable[[Sequence[str]], Any] = default_spawn,
                 clock: Callable[[], float] = time.monotonic, on_alert: Callable[[str], None] | None = None) -> None:
        self._argv, self._config, self._spawn, self._clock = argv, config, spawn, clock
        self._alert = on_alert
        self._proc: Any = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_beat = 0.0
        self.failures = 0
        self.port = 0
        self.ready = threading.Event()

    # -- state ---------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    # -- lifecycle -------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, name="webconnect-supervisor", daemon=True)
        self._thread.start()

    def stop(self, grace_s: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(grace_s + 2)
        self._end(grace_s)

    # -- internals -------------------------------------------------------------------------------------
    def _launch(self) -> None:
        proc = self._spawn(self._argv)
        proc.stdin.write(json.dumps(self._config()) + "\n")                   # the one-shot hand-off
        proc.stdin.flush()
        with self._lock:
            self._proc = proc
        self._last_beat = self._clock()
        threading.Thread(target=self._read, args=(proc,), name="webconnect-heartbeat", daemon=True).start()

    def _read(self, proc: Any) -> None:
        for line in proc.stdout:
            line = line.strip()
            if line == "hb":
                self._last_beat = self._clock()
            elif line.startswith("ready"):
                self._last_beat = self._clock()
                self.port = int(line.split()[1]) if len(line.split()) > 1 else 0
                self.failures = 0
                self.ready.set()

    def _back_off(self, delay: float, why: str) -> bool:
        """Count a failure, tell the person after a few in a row, wait ``delay``. True if stopped meanwhile."""
        self.failures += 1
        logger.warning("WebConnect web process: %s (%d in a row)", why, self.failures)
        if self.failures == ALERT_AFTER_FAILURES and self._alert is not None:
            self._alert(f"The WebConnect web application keeps failing: {why}.")
        return self._stop.wait(delay)

    def _watch(self) -> None:
        delay = BACKOFF_START_S
        while not self._stop.is_set():
            if self.running:
                if self._clock() - self._last_beat > HEARTBEAT_TIMEOUT_S:
                    self._end(2.0)
                    if self._back_off(delay, "it stopped answering"):
                        break
                    delay = min(delay * 2, BACKOFF_CAP_S)
                    continue
                if self.ready.is_set():
                    delay = BACKOFF_START_S
            else:
                if self._proc is not None:                                       # it exited by itself
                    with self._lock:
                        self._proc = None
                    if self._back_off(delay, "it exited"):
                        break
                    delay = min(delay * 2, BACKOFF_CAP_S)
                try:
                    self.ready.clear()
                    self._launch()
                except Exception as exc:
                    logger.exception("WebConnect web process could not be started")
                    if self._back_off(delay, f"it could not be started ({exc.__class__.__name__})"):
                        break
                    delay = min(delay * 2, BACKOFF_CAP_S)
                    continue
            self._stop.wait(0.5)

    def _end(self, grace_s: float) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        self.ready.clear()
        self.port = 0
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write("shutdown\n")
            proc.stdin.flush()
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        deadline = time.monotonic() + grace_s
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(2)
            except subprocess.TimeoutExpired:
                proc.kill()
