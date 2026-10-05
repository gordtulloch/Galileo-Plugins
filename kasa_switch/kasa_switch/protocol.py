# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""TP-Link Kasa "legacy" local-network protocol — a minimal, dependency-free
re-implementation of the wire format documented by
https://github.com/p-doyle/Python-KasaSmartPowerStrip (and the wider
pyHS100/python-kasa community reverse-engineering effort).

Every Kasa device (single plugs like the HS100/HS103/HS105 and multi-outlet
strips like the HS300/KP303/EP40) exchanges JSON commands over a plain TCP
socket on port 9999, obfuscated with a one-byte-feedback XOR "cipher" keyed
on 171. This is not encryption — TP-Link themselves describe it as
"autokey XOR" — so no cryptography dependency is needed, only ``socket`` and
``json`` from the standard library. Implemented locally (rather than
depending on the third-party ``python-kasa`` package) both to keep this
plugin's dependency footprint to just ``galileo`` and to avoid any import
collision with that package's own top-level ``kasa`` module.
"""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

_INITIAL_KEY = 171
_DEFAULT_PORT = 9999
_DEFAULT_TIMEOUT_S = 5.0


def encrypt(data: str) -> bytes:
    """Encode *data* (a JSON string) into a Kasa wire payload, length-prefixed
    with a big-endian uint32 as the protocol requires."""
    key = _INITIAL_KEY
    payload = bytearray()
    for ch in data.encode("utf-8"):
        key = ch ^ key
        payload.append(key)
    return struct.pack(">I", len(payload)) + bytes(payload)


def decrypt(data: bytes) -> str:
    """Decode a Kasa wire payload (without its length prefix) back to a JSON string."""
    key = _INITIAL_KEY
    out = bytearray()
    for ch in data:
        out.append(ch ^ key)
        key = ch
    return out.decode("utf-8")


def send_command(
    host: str,
    command: dict[str, Any],
    *,
    port: int = _DEFAULT_PORT,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send *command* (a plain dict, e.g. ``{"system": {"get_sysinfo": {}}}``)
    to the Kasa device at *host* and return its parsed JSON response.

    Opens a fresh TCP connection per call — these devices don't expose (or
    need) a persistent session, and a short-lived socket is simplest to keep
    correct under Galileo's fault-isolation rules (ARCH-060): a device that
    drops off the network fails this one call with a clear exception rather
    than leaving a stale connection for the next command to trip over.
    """
    request = encrypt(json.dumps(command))
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        length_bytes = _recv_exact(sock, 4)
        (length,) = struct.unpack(">I", length_bytes)
        body = _recv_exact(sock, length)
    return json.loads(decrypt(body))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *sock*, raising if the peer closes early."""
    chunks = bytearray()
    while len(chunks) < n:
        chunk = sock.recv(n - len(chunks))
        if not chunk:
            raise ConnectionError("Kasa device closed the connection unexpectedly")
        chunks.extend(chunk)
    return bytes(chunks)
