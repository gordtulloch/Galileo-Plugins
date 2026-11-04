# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""How the web half talks to its Master (WCON-090, SDD §3.0 ``LinkClientPort``).

Only Connect Link's documented wire API is used: the unauthenticated pairing endpoints, and the plugin's
``/webconnect/v1`` routes with the service credential and, per request, the browser being acted for. The
Master's certificate is pinned by fingerprint; nothing else is trusted. The web half imports nothing from
Galileo.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

ACTING = "X-Acting-Client"
PLUGIN_API = "/webconnect/v1"
CONNECT_API = "/connect/v1"


class LinkError(Exception):
    """The Master could not be reached or answered something unusable."""


class LinkClient(Protocol):
    def pair_request(self, name: str, platform: str) -> dict[str, Any]: ...
    def pair_poll(self, request_id: str) -> dict[str, Any]: ...
    def call(self, method: str, route: str, *, client: str = "", body: dict[str, Any] | None = None,
             query: dict[str, str] | None = None, source: str = "") -> dict[str, Any]: ...
    def reachable(self) -> bool: ...


class _PinnedHTTPS(http.client.HTTPSConnection):
    """An HTTPS connection that accepts exactly one certificate, identified by its SHA-256 fingerprint."""

    def __init__(self, host: str, port: int, fingerprint: str, timeout: float) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE                 # replaced by the fingerprint check below
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(host, port, timeout=timeout, context=ctx)
        self._want = fingerprint.replace(":", "").lower()

    def connect(self) -> None:
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        if not der or hashlib.sha256(der).hexdigest() != self._want:
            self.close()
            raise LinkError("The Master's certificate is not the one this web application was paired with.")


class HttpLinkClient:
    def __init__(self, address: str, fingerprint: str, service_token: str, timeout: float = 10.0) -> None:
        parts = urlsplit(address)
        if parts.scheme != "https" or not parts.hostname:
            raise LinkError("The Master's address must be https://host:port")
        self._host, self._port = parts.hostname, parts.port or 443
        self._fingerprint, self._token, self._timeout = fingerprint, service_token, timeout

    def _send(self, method: str, path: str, headers: dict[str, str], body: dict[str, Any] | None) -> dict[str, Any]:
        conn = _PinnedHTTPS(self._host, self._port, self._fingerprint, self._timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            if data is not None:
                headers = {**headers, "Content-Type": "application/json"}
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(4_000_000)
            status = resp.status
        except (OSError, http.client.HTTPException) as exc:
            raise LinkError(f"The Master could not be reached: {exc}") from exc
        finally:
            conn.close()
        try:
            out = json.loads(raw or b"{}")
        except ValueError as exc:
            raise LinkError("The Master sent something that is not JSON.") from exc
        if not isinstance(out, dict):
            raise LinkError("The Master sent an unexpected answer.")
        out.setdefault("http", status)
        return out

    def pair_request(self, name: str, platform: str) -> dict[str, Any]:
        return self._send("POST", f"{CONNECT_API}/pair", {},
                          {"plugin": "webconnect", "name": name, "kind": "browser", "platform": platform})

    def pair_poll(self, request_id: str) -> dict[str, Any]:
        return self._send("GET", f"{CONNECT_API}/pair/{request_id}", {}, None)

    def call(self, method: str, route: str, *, client: str = "", body: dict[str, Any] | None = None,
             query: dict[str, str] | None = None, source: str = "") -> dict[str, Any]:
        path = PLUGIN_API + route + (("?" + urlencode(query)) if query else "")
        headers = {"Authorization": f"Bearer {self._token}"}
        if client:
            headers[ACTING] = client
        if source:
            headers["X-Client-Address"] = source
        out = self._send(method, path, headers, body)
        if out.get("http") == 401:
            raise LinkError("The Master refused this web application's credential.")
        return out

    def reachable(self) -> bool:
        try:
            return self._send("GET", f"{CONNECT_API}/hello", {}, None).get("http", 0) < 500
        except LinkError:
            return False
