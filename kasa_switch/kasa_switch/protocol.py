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


def _local_ipv4_addresses() -> list[str]:
    """Every local IPv4 address worth broadcasting from (loopback excluded).

    A single broadcast from an unbound socket leaves through only the OS's
    default-route interface, so on a machine with several adapters (VPN,
    WSL/Hyper-V virtual switches, Wi-Fi plus Ethernet) the one carrying the
    Kasa devices is often never tried.
    """
    addrs: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    try:  # the address the default route would use; no packet is sent
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            addrs.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith(("127.", "169.254.", "0.")))


def discover(*, port: int = _DEFAULT_PORT, timeout: float = 3.0) -> list[tuple[str, dict[str, Any]]]:
    """Find Kasa devices on the local network by UDP broadcast.

    Broadcasts a ``get_sysinfo`` request (the same obfuscation as the TCP
    protocol but without the length prefix) from *each* local IPv4 interface,
    to both the limited broadcast and that interface's assumed /24 broadcast,
    and collects replies until *timeout* seconds have passed. Returns
    ``(host, sysinfo)`` pairs, one per responding device.

    Only devices speaking the legacy port-9999 protocol answer; newer Kasa
    firmware (KLAP/AES) does not and must be added by IP.
    """
    import select
    import time

    request = encrypt(json.dumps({"system": {"get_sysinfo": {}}}))[4:]
    socks: list[socket.socket] = []
    for local in _local_ipv4_addresses() or [""]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((local, 0))
        except OSError:
            sock.close()
            continue
        targets = ["255.255.255.255"]
        if local:
            targets.append(local.rsplit(".", 1)[0] + ".255")
        for target in targets:
            for _ in range(2):  # UDP is lossy; one retry is cheap
                try:
                    sock.sendto(request, (target, port))
                except OSError:
                    break
        socks.append(sock)

    found: dict[str, dict[str, Any]] = {}
    try:
        deadline = time.monotonic() + timeout
        while socks and (remaining := deadline - time.monotonic()) > 0:
            try:
                ready, _, _ = select.select(socks, [], [], remaining)
            except OSError:
                break
            for sock in ready:
                try:
                    data, (host, _port) = sock.recvfrom(4096)
                except OSError:
                    continue
                try:
                    info = json.loads(decrypt(data)).get("system", {}).get("get_sysinfo", {})
                except (ValueError, UnicodeDecodeError):
                    continue
                if info:
                    found[host] = info
    finally:
        for sock in socks:
            sock.close()
    return list(found.items())


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *sock*, raising if the peer closes early."""
    chunks = bytearray()
    while len(chunks) < n:
        chunk = sock.recv(n - len(chunks))
        if not chunk:
            raise ConnectionError("Kasa device closed the connection unexpectedly")
        chunks.extend(chunk)
    return bytes(chunks)
