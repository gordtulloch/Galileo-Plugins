# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The web process entry point (SDD §3.2; WCON-030, 070, 910).

Started by the Master half as ``python -I -c "...main()"``. Everything it needs arrives as one JSON line on
standard input: nothing is read from files, the environment or the command line, so the service credential
never appears in a process listing. After that, standard input is the control channel: ``shutdown`` (or the
Master half going away, which closes it) ends this process, and a heartbeat line goes out on standard output so
the Master half can tell a hung process from a quiet one.
"""

from __future__ import annotations

import json
import ssl
import sys
import tempfile
import threading
from pathlib import Path
from time import sleep as time_sleep
from typing import Any

HEARTBEAT_S = 3.0


def read_config(stdin) -> dict[str, Any]:
    line = stdin.readline()
    if not line:
        raise SystemExit("no configuration was handed over")
    cfg = json.loads(line)
    for key in ("address", "fingerprint", "token", "bind", "port", "cert_pem", "key_pem", "allowed_hosts"):
        if key not in cfg:
            raise SystemExit(f"configuration is missing {key!r}")
    return cfg


def ssl_context(cert_pem: str, key_pem: str) -> ssl.SSLContext:
    """Python cannot load a key from memory: it goes to a private folder under a throwaway password, is loaded,
    and is deleted."""
    import secrets

    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(key_pem.encode("ascii"), password=None)
    password = secrets.token_hex(16)
    enc = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.BestAvailableEncryption(password.encode("ascii")))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    with tempfile.TemporaryDirectory(prefix="webconnect-") as tmp:
        c, k = Path(tmp) / "c.pem", Path(tmp) / "k.pem"
        c.write_text(cert_pem, encoding="ascii")
        k.write_bytes(enc)
        ctx.load_cert_chain(str(c), str(k), password=password)
    return ctx


def build_app(cfg: dict[str, Any]):
    from webconnect.web.app import WebSettings, create_app
    from webconnect.web.link import HttpLinkClient
    from webconnect.web.session import CookiePolicy
    link = HttpLinkClient(cfg["address"], cfg["fingerprint"], cfg["token"])
    return create_app(link, WebSettings(
        allowed_hosts=tuple(cfg["allowed_hosts"]), idle_s=int(cfg.get("idle_s", 1800)),
        absolute_s=int(cfg.get("absolute_s", 28800)), cookies=CookiePolicy(),
        outreach_subnets=tuple(cfg.get("outreach_subnets", ()))))


def control_loop(stdin, stop: threading.Event) -> None:
    """``shutdown`` or end-of-file (the Master half is gone) stops the process."""
    for line in stdin:
        if line.strip() == "shutdown":
            break
    stop.set()


def heartbeat(stdout, stop: threading.Event, interval: float = HEARTBEAT_S) -> None:
    while not stop.wait(interval):
        try:
            stdout.write("hb\n")
            stdout.flush()
        except (OSError, ValueError):
            stop.set()


def main(stdin=None, stdout=None, make_server=None, sleep=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    cfg = read_config(stdin)
    app = build_app(cfg)
    ctx = ssl_context(cfg["cert_pem"], cfg["key_pem"])
    cfg["key_pem"] = cfg["token"] = ""                          # nothing secret stays in this dict
    if make_server is None:
        import gevent
        from gevent.pywsgi import WSGIServer
        sleep = gevent.sleep                                    # the main loop must yield to the server's greenlets
        # A browser refusing the self-signed certificate aborts its handshake; that is routine, not a fault to print.
        hub = gevent.get_hub()
        hub.NOT_ERROR = (*hub.NOT_ERROR, ssl.SSLError, ConnectionError)

        def make_server(bind, port, app, ctx):
            return WSGIServer((bind, port), app, ssl_context=ctx, log=None)
    server = make_server(cfg["bind"], int(cfg["port"]), app, ctx)
    stop = threading.Event()
    threading.Thread(target=control_loop, args=(stdin, stop), daemon=True).start()
    threading.Thread(target=heartbeat, args=(stdout, stop), daemon=True).start()
    server.start()
    stdout.write(f"ready {server.server_port}\n")
    stdout.flush()
    nap = sleep or time_sleep
    try:
        while not stop.is_set():
            nap(0.2)
    finally:
        server.stop(timeout=2)
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    sys.exit(main())
