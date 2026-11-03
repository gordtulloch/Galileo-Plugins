# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The Master's side of frame transfer (NCON-410, 430, 470).

Connect Link's transfer store holds each uploaded object by content hash, already verified. A node then
asks the Master to *ingest* it: the Master copies the object to a scratch folder and registers it through
the Library's normal ingest path (``FileProcessor.registerFitsImage``), which reads the catalog data from
the FITS header and applies the Master's own rules. Nothing is stacked, calibrated or re-measured, and no
two Libraries are merged: a frame the Master's catalog already holds by hash is acknowledged, not added
again. The object stays in the transfer store so it can be served back when a node retrieves it (NCON-490).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nodeconnect.wire import API, PLUGIN, WireError

logger = logging.getLogger(__name__)

# (directory, filename) -> frame id, or a falsy value when the Library refused the file
Registrar = Callable[[str, str], Any]
HashKnown = Callable[[str], bool]


def library_registrar(directory: str, name: str) -> Any:
    from galileo.library.core.file_processing import FileProcessor
    return FileProcessor().registerFitsImage(directory, name, True)


def library_has_hash(sha: str) -> bool:
    from galileo.library.models import fitsFile
    return fitsFile.select().where(fitsFile.fitsFileHash == sha).exists()


class FrameIngest:
    def __init__(self, object_path: Callable[[str], Path], registrar: Registrar = library_registrar,
                 known: HashKnown = library_has_hash, scratch: Path | None = None) -> None:
        self._path, self._register, self._known, self._scratch = object_path, registrar, known, scratch

    def ingest(self, body: dict[str, Any]) -> dict[str, Any]:
        sha = str(body.get("hash", "")).lower()
        name = Path(str(body.get("name") or f"{sha}.fits")).name         # never a path from the node
        if not name.lower().endswith((".fits", ".fit", ".fts")):
            name += ".fits"
        try:
            src = self._path(sha)
        except Exception as exc:
            raise WireError("not a content hash") from exc
        if not src.is_file():
            raise WireError("The Master does not hold that object; upload it first.", 404)
        if self._known(sha):
            return {"ingested": False, "duplicate": True, "hash": sha}
        with tempfile.TemporaryDirectory(dir=self._scratch) as tmp:
            shutil.copyfile(src, Path(tmp) / name)
            frame_id = self._register(tmp, name)
        if not frame_id:
            raise WireError("The Library did not accept that file.", 422)
        return {"ingested": True, "duplicate": False, "hash": sha, "frame_id": str(frame_id)}


def register_ingest_route(routes, ingest: FrameIngest) -> None:
    from aiohttp import web

    async def handler(peer, request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise WireError("body must be a JSON object")
            return await asyncio.to_thread(ingest.ingest, body)
        except WireError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)
        except ValueError:
            return web.json_response({"error": "bad request"}, status=400)
    routes.register(PLUGIN, "POST", f"{API}/frames", "data.push", handler)
