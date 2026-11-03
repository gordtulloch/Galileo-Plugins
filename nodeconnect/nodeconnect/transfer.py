# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Moving a node's FITS frames to the Master (NCON-410, 420, 430, 440, 450, 460).

The queue is core's durable ``sync_transfer_queue`` (through ``SyncStore``), keyed by the frame's content
hash, so a frame is queued once however often it is scanned for and survives restarts. ``scan`` queues
every local frame the Master is not known to hold; ``TransferWorker.drain`` sends them one at a time
through core's resumable, checksummed ``TransferClient`` (which streams from disk in chunks and does not
send an object the Master already holds). A frame is only called *verified* when the upload returns the
hash the catalog holds; only then, and only if the user switched it on, is the local copy removed and
the catalog row set to *On Master* (NCON-460). Nothing here ever raises into a session (NCON-530).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

QUEUED, ACTIVE, DONE, FAILED, RETRY = "queued", "active", "done", "failed", "retry"
UP = "up"
FRAME_PRIORITY = 9                  # history and catalog rows (small) are queued with a lower number
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class LocalFrame:
    frame_id: str
    path: Path
    sha256: str
    size: int


class LibraryPort(Protocol):
    def local_frames(self) -> list[LocalFrame]: ...
    def frame_for_hash(self, sha256: str) -> LocalFrame | None: ...
    def in_use(self, frame_id: str) -> bool: ...                # referenced by a running session
    def mark_on_master(self, frame_id: str, holder: str, verified_hash: str) -> None: ...
    def delete_local_copy(self, frame_id: str) -> bool: ...


class UploaderPort(Protocol):
    async def upload(self, path: Path, *, progress: Callable[[int, int], None] | None = None) -> str: ...
    async def ingest(self, sha256: str, name: str) -> None: ...      # ask the Master to catalogue the object


@dataclass
class Limits:
    """User limits on image transfer (NCON-450). Configuration replication ignores them."""
    window: tuple[int, int] | None = None           # allowed hours [start, end), local time; None = any time
    remove_after_receipt: bool = False              # NCON-460, off by default

    def open_now(self, now: datetime | None = None) -> bool:
        if self.window is None:
            return True
        hour, (start, end) = (now or datetime.now()).hour, self.window  # noqa: DTZ005 - local wall-clock hours
        return start <= hour < end if start < end else hour >= start or hour < end


class LibraryAdapter:
    """The real Library: local frames of the catalog, via the models and ``library.core.location``."""

    def __init__(self, busy: Callable[[], bool] = lambda: False) -> None:
        self._busy = busy

    @staticmethod
    def _frame(row) -> LocalFrame | None:
        path = Path(row.fitsFileName or "")
        if not row.fitsFileHash or not path.is_file():
            return None
        return LocalFrame(row.fitsFileId, path, row.fitsFileHash.lower(), path.stat().st_size)

    def local_frames(self) -> list[LocalFrame]:
        from galileo.library.models import fitsFile
        rows = fitsFile.select().where((fitsFile.fitsFileLocation == "local") & (fitsFile.fitsFileHash.is_null(False))
                                       & ((fitsFile.fitsFileSoftDelete == False) | fitsFile.fitsFileSoftDelete.is_null()))
        return [f for f in (self._frame(r) for r in rows) if f is not None]

    def frame_for_hash(self, sha256: str) -> LocalFrame | None:
        from galileo.library.models import fitsFile
        row = fitsFile.select().where((fitsFile.fitsFileHash == sha256) & (fitsFile.fitsFileLocation == "local")).first()
        return self._frame(row) if row is not None else None

    def in_use(self, frame_id: str) -> bool:
        return self._busy()                          # while a session runs, no frame is removed

    def mark_on_master(self, frame_id: str, holder: str, verified_hash: str) -> None:
        from galileo.library.core import location
        location.mark_on_master(frame_id, holder, verified_hash)

    def delete_local_copy(self, frame_id: str) -> bool:
        from galileo.library.core import location
        return location.delete_local_copy(frame_id)


def scan(store: Any, library: LibraryPort, peer_id: str) -> int:
    """Queue every local frame for the Master (first pairing, and on demand); returns how many were new."""
    new = 0
    for frame in library.local_frames():
        known = _queued(store, peer_id, frame.sha256)
        store.transfer_enqueue(peer_id, UP, frame.sha256, frame.size, FRAME_PRIORITY)
        new += 0 if known else 1
    return new


def _queued(store: Any, peer_id: str, sha: str) -> bool:
    from galileo.library.models.base import db
    return db.execute_sql("SELECT 1 FROM sync_transfer_queue WHERE peer_id = ? AND direction = ? AND object_hash = ?",
                          (peer_id, UP, sha)).fetchone() is not None


def queue_counts(store: Any, peer_id: str = "") -> dict[str, int]:
    """Pending transfers and bytes, for the status view (NCON-330)."""
    from galileo.library.models.base import db
    row = db.execute_sql(
        "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM sync_transfer_queue WHERE state IN ('queued','retry','active') "
        "AND direction = 'up' AND (? = '' OR peer_id = ?)", (peer_id, peer_id)).fetchone()
    return {"items": int(row[0]), "bytes": int(row[1])}


class TransferWorker:
    def __init__(self, store: Any, library: LibraryPort, uploader: UploaderPort, peer_id: str, holder: str,
                 limits: Callable[[], Limits] = Limits, session_busy: Callable[[], bool] = lambda: False) -> None:
        self._store, self._lib, self._up = store, library, uploader
        self._peer, self._holder, self._limits, self._busy = peer_id, holder, limits, session_busy
        self.last_error = ""

    async def drain(self, max_items: int = 1000) -> int:
        """Send queued frames until the queue is empty, the window closes or something fails; returns how
        many frames were verified. A frame that cannot be sent now stays queued (``retry``), up to
        ``MAX_ATTEMPTS``; a network failure stops the pass so the queue waits for the link."""
        sent = 0
        for _ in range(max_items):
            if self._busy() or not self._limits().open_now():
                break                                           # transfer yields to a session (NCON-440)
            job = self._store.transfer_claim(self._peer)
            if job is None or job["direction"] != UP:
                break
            outcome = await self._send(job)
            if outcome == DONE:
                sent += 1
            elif outcome == RETRY:
                break
        return sent

    async def _send(self, job: dict[str, Any]) -> str:
        sha = job["object_hash"]
        frame = await asyncio.to_thread(self._lib.frame_for_hash, sha)
        if frame is None:
            self._store.transfer_update(job["id"], FAILED)      # gone, changed, or already On Master
            return FAILED
        try:
            got = await self._up.upload(frame.path)
        except Exception as exc:  # noqa: BLE001 - the link dropped, or the Master refused
            self.last_error = f"{frame.path.name}: {exc}"
            logger.warning("NodeConnect frame transfer failed: %s", self.last_error)
            self._store.transfer_update(job["id"], FAILED if job["attempts"] >= MAX_ATTEMPTS else RETRY)
            return RETRY
        if got.lower() != sha.lower():
            self.last_error = f"{frame.path.name}: the Master reported a different hash"
            self._store.transfer_update(job["id"], FAILED)
            return FAILED
        try:
            await self._up.ingest(sha, frame.path.name)
        except Exception as exc:  # noqa: BLE001 - uploaded but not catalogued: try again, never remove
            self.last_error = f"{frame.path.name}: the Master could not catalogue it ({exc})"
            logger.warning("NodeConnect ingest failed: %s", self.last_error)
            self._store.transfer_update(job["id"], FAILED if job["attempts"] >= MAX_ATTEMPTS else RETRY)
            return RETRY
        self._store.transfer_update(job["id"], DONE)
        await asyncio.to_thread(self._maybe_remove, frame, sha)
        return DONE

    def _maybe_remove(self, frame: LocalFrame, sha: str) -> None:
        if not self._limits().remove_after_receipt:
            return
        try:
            if self._lib.in_use(frame.frame_id):
                return
            self._lib.mark_on_master(frame.frame_id, self._holder, sha)
            self._lib.delete_local_copy(frame.frame_id)
        except Exception:
            logger.exception("NodeConnect could not free the local copy of %s", frame.path)


def limits_from(settings: Any) -> Limits:
    """The user's transfer limits from the plugin settings, clamped (NCON-450, 460, 500)."""
    def hour(key: str) -> int | None:
        v = settings.get(key, None)
        return int(v) % 24 if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    start, end = hour("transfer_from_hour"), hour("transfer_to_hour")
    window = (start, end) if start is not None and end is not None and start != end else None
    return Limits(window, bool(settings.get("remove_after_receipt", False)))


class _Connection:
    """An aiohttp session to the Master that trusts exactly the certificate pinned at pairing."""

    def __init__(self, creds: Any) -> None:
        self._creds = creds

    async def __aenter__(self):
        import aiohttp
        digest = bytes.fromhex(self._creds.fingerprint.replace(":", ""))
        self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=aiohttp.Fingerprint(digest)))
        from galileo.connect_link import TransferClient
        root = self._creds.address.rstrip("/")

        class Channel(TransferClient):
            async def ingest(inner, sha256: str, name: str) -> None:
                async with inner._s.post(f"{root}/nodeconnect/v1/frames", json={"hash": sha256, "name": name},
                                         headers=inner._h) as r:
                    if r.status != 200:
                        body = await r.json(content_type=None)
                        raise RuntimeError(str((body or {}).get("error", r.status)))
        return Channel(self._session, root + "/connect/v1", {"Authorization": f"Bearer {self._creds.token}"})

    async def __aexit__(self, *exc) -> None:
        await self._session.close()


def make_transfer_job(store: Any, library: LibraryPort, creds: Any, settings: Any,
                      session_busy: Callable[[], bool]) -> Callable[[], int]:
    """One scan-and-drain pass for the spoke's sync worker; returns frames verified."""
    def job() -> int:
        async def go() -> int:
            scan(store, library, creds.master_id)
            async with _Connection(creds) as client:
                worker = TransferWorker(store, library, client, creds.master_id, creds.master_id,
                                        lambda: limits_from(settings), session_busy)
                return await worker.drain()
        return asyncio.run(go())
    return job


def install_retriever(creds: Any) -> None:
    """Let the Library fetch an *On Master* frame from this node's Master (NCON-490)."""
    from galileo.library.core import location

    async def fetch(sha256: str, dest: Path) -> None:
        async with _Connection(creds) as client:
            await client.download(sha256, dest)
    location.set_retriever(fetch)


def remove_retriever() -> None:
    from galileo.library.core import location
    location.set_retriever(None)
