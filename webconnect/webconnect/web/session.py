# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Server-side sessions, CSRF and cookie policy (SDD §3.4; WCON-830, 1000, 1010, 1040).

The device credential (the browser's Connect Link token) proves "this is an approved browser" and grants nothing
by itself; a sign-in turns it into a session held in this process's memory. A restart therefore needs one click
to sign in again, and nothing is written to disk.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CookiePolicy:
    """Names and attributes per hosting profile (WCON-1460): ``__Host-`` with path ``/`` when self-hosted."""

    prefix: str = "__Host-"
    path: str = "/"

    @property
    def device(self) -> str:
        return f"{self.prefix}wc_device"

    @property
    def session(self) -> str:
        return f"{self.prefix}wc_session"

    @property
    def pairing(self) -> str:
        return f"{self.prefix}wc_pairing"

    def attrs(self, max_age: int | None = None) -> dict:
        return {"httponly": True, "secure": True, "samesite": "Strict", "path": self.path, "max_age": max_age}


@dataclass
class Session:
    sid: str
    client: str                  # the browser's peer id on the Master
    name: str
    role: str
    csrf: str
    created: float
    last_seen: float
    streams: list = field(default_factory=list)


class SessionStore:
    def __init__(self, idle_s: float, absolute_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._idle, self._abs, self._clock = idle_s, absolute_s, clock
        self._by_sid: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, client: str, name: str, role: str) -> Session:
        """A fresh identifier every time, so a sign-in never reuses one an attacker could have planted."""
        now = self._clock()
        s = Session(secrets.token_urlsafe(32), client, name, role, secrets.token_urlsafe(32), now, now)
        with self._lock:
            self._by_sid[s.sid] = s
        return s

    def get(self, sid: str) -> Session | None:
        if not sid:
            return None
        now = self._clock()
        with self._lock:
            s = self._by_sid.get(sid)
            if s is None:
                return None
            if now - s.last_seen > self._idle or now - s.created > self._abs:
                del self._by_sid[sid]
                return None
            s.last_seen = now
            return s

    def alive(self, sid: str) -> bool:
        """Whether the session still stands, without counting as activity: an open event stream must not keep a
        forgotten tab signed in."""
        with self._lock:
            s = self._by_sid.get(sid)
            if s is None:
                return False
            now = self._clock()
            if now - s.last_seen > self._idle or now - s.created > self._abs:
                del self._by_sid[sid]
                return False
            return True

    def end(self, sid: str) -> Session | None:
        with self._lock:
            return self._by_sid.pop(sid, None)

    def end_client(self, client: str) -> list[Session]:
        """Every session of one browser (it was revoked, or its role changed)."""
        with self._lock:
            gone = [s for s in self._by_sid.values() if s.client == client]
            for s in gone:
                del self._by_sid[s.sid]
        return gone

    def __len__(self) -> int:
        return len(self._by_sid)


def csrf_ok(session: Session, presented: str) -> bool:
    return bool(presented) and hmac.compare_digest(session.csrf, presented)
