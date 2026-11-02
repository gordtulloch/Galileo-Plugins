# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The spoke's end of the wire: ``MasterLink`` implements ``sync.MasterLinkPort`` (NCON-120, 140).

It speaks the ``/nodeconnect/v1`` JSON of ``wire`` through a ``Transport``, a single callable that
sends one POST and returns the decoded JSON body. Keeping HTTPS, the bearer token and the pinned
certificate inside the transport leaves this class free of network code, and lets the tests put the
Master on the other end of a function call.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
import urllib.parse
from collections.abc import Callable
from typing import Any

from nodeconnect import conflict
from nodeconnect.master import Pushed, PushResult
from nodeconnect.wire import (
    API,
    change_from_json,
    pushed_to_json,
    result_from_json,
)

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class LinkError(Exception):
    """The Master refused or could not be reached; the message is shown to the user."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class MasterLink:
    def __init__(self, transport: Transport) -> None:
        self._send = transport

    def handshake(self, info: dict[str, Any], fingerprints: dict[str, str]) -> dict[str, Any]:
        return self._send(f"{API}/handshake", {"info": info, "fingerprints": fingerprints})

    def pull(self, since: dict[str, int], tables: list[str]) -> list[dict[str, Any]]:
        reply = self._send(f"{API}/pull", {"since": since, "tables": tables})
        return [{"table": p["table"], "rev": int(p["rev"]), "tomb": int(p.get("tomb", 0)),
                 "changes": [change_from_json(c) for c in p["changes"]]} for p in reply["pages"]]

    def push(self, pushed: list[Pushed], offset: conflict.ClockOffset) -> list[PushResult]:
        reply = self._send(f"{API}/push", {
            "changes": [pushed_to_json(p) for p in pushed],
            "offset": {"seconds": offset.seconds, "error": offset.error}})
        return [result_from_json(r) for r in reply["results"]]

    def ack(self, revs: dict[str, int]) -> None:
        self._send(f"{API}/ack", {"revs": revs})


def https_transport(base_url: str, token: str, fingerprint: str, timeout: float = 30.0) -> Transport:
    """A transport over HTTPS to the Master with the node's bearer token. The Master's certificate is
    self-signed, so it is trusted by **pinned SHA-256 fingerprint** (the one shown at pairing), not by
    a CA: the connection is opened, the certificate compared, and nothing (least of all the token) is
    sent unless it matches (NCON-060)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    expected = fingerprint.replace(":", "").lower()
    parts = urllib.parse.urlsplit(base_url)

    def send(path: str, body: dict[str, Any]) -> dict[str, Any]:
        conn = http.client.HTTPSConnection(parts.hostname or "", parts.port or 443, timeout=timeout, context=ctx)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True) if conn.sock else None       # type: ignore[union-attr]
            if der is None or hashlib.sha256(der).hexdigest() != expected:
                raise LinkError("The Master's certificate is not the one that was approved at pairing.")
            conn.request("POST", (parts.path.rstrip("/") + path), json.dumps(body).encode(),
                         {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
        except LinkError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise LinkError(f"Cannot reach the Master: {exc}") from exc
        finally:
            conn.close()
        if resp.status != 200:
            raise LinkError(_reason(data, resp.reason), resp.status)
        return json.loads(data)
    return send


def _reason(data: bytes, fallback: str) -> str:
    try:
        return str(json.loads(data).get("error", fallback))
    except (ValueError, AttributeError):
        return fallback


def open_transport(address: str, timeout: float = 15.0):
    """A transport for the unauthenticated pairing requests: the certificate is not yet trusted, so
    each reply carries the fingerprint of the certificate that actually answered, and the pairing
    client compares it with the one the Master reports for itself."""
    from nodeconnect.pairing import Reply
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    parts = urllib.parse.urlsplit(address)

    def send(method: str, path: str, body: dict[str, Any] | None) -> Reply:
        conn = http.client.HTTPSConnection(parts.hostname or "", parts.port or 443, timeout=timeout, context=ctx)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True) if conn.sock else None       # type: ignore[union-attr]
            conn.request(method, parts.path.rstrip("/") + path,
                         json.dumps(body).encode() if body is not None else None,
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise LinkError(f"Cannot reach the Master: {exc}") from exc
        finally:
            conn.close()
        try:
            parsed = json.loads(data) if data else {}
        except ValueError:
            parsed = {}
        return Reply(resp.status, parsed if isinstance(parsed, dict) else {},
                     hashlib.sha256(der).hexdigest() if der else "")
    return send
