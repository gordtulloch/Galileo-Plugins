# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""What the Master half hands the web process at each start (SDD §3.2; WCON-060, 070, 910)."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

from webconnect.config import WebConnectConfig


def lan_address() -> str:
    """The Master's LAN address: the interface a packet to a public address would leave by. A UDP connect
    sends nothing; it only asks the system to choose a route. Falls back to loopback when offline."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                        # TEST-NET-1: never routed, never answered
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def bind_address(cfg: WebConnectConfig) -> str:
    """WCON-060: the configured address, else the LAN interface; every interface only when explicitly allowed."""
    if cfg.bind_address:
        return cfg.bind_address
    return "0.0.0.0" if cfg.wide_bind else lan_address()


def open_address(bind: str) -> str:
    """The host a browser should be sent to: a wildcard bind (every interface) is reached by the LAN address."""
    return lan_address() if bind in ("0.0.0.0", "::") else bind


def allowed_hosts(bind: str) -> list[str]:
    """Names this application answers to (WCON-1060): its own host name, ``.local``, loopback and its address."""
    host = socket.gethostname().lower()
    names = {"localhost", "127.0.0.1", host, f"{host}.local"}
    if bind not in ("0.0.0.0", "::"):
        names.add(bind)
    else:
        names.add(lan_address())                                  # the address people will actually type
        try:
            names.update(info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET))
        except OSError:
            pass
    return sorted(names)


def make_config_provider(cfg: Callable[[], WebConnectConfig], certs, mint: Callable[[], tuple[str, str]],
                         link_address: Callable[[], str], link_fingerprint: Callable[[], str]
                         ) -> Callable[[], dict[str, Any]]:
    """The callable the Supervisor calls at every (re)start. It mints a fresh service credential each time, so
    exactly one is ever valid and none is stored anywhere."""
    def provide() -> dict[str, Any]:
        c = cfg()
        bind = bind_address(c)
        cert_pem, key_pem = certs.ensure()
        _, token = mint()
        return {"address": link_address(), "fingerprint": link_fingerprint(), "token": token, "bind": bind,
                "port": c.port, "cert_pem": cert_pem, "key_pem": key_pem, "allowed_hosts": allowed_hosts(bind),
                "idle_s": c.session_idle_s, "absolute_s": c.session_absolute_s}
    return provide
