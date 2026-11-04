# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The web process and its supervision (WCON-030, 040, 060, 070, 910): a real child under ``python -I`` serving
HTTPS, restarted when it dies, and gone when stopped."""

import http.client
import json
import socket
import ssl
import subprocess
import time
from pathlib import Path

import pytest

from webconnect import supervisor as sup
from webconnect.tls import CertificateStore, TlsError, fingerprint_of

PLUGIN_DIR = Path(__file__).resolve().parents[1]


class Mem:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


def config(cert, key, port=0):
    return {"address": "https://127.0.0.1:9", "fingerprint": "00" * 32, "token": "service-secret-token",
            "bind": "127.0.0.1", "port": port, "cert_pem": cert, "key_pem": key,
            "allowed_hosts": ["localhost", "127.0.0.1"]}


def get(port, path="/", host="localhost"):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=5)
    conn.request("GET", path, headers={"Host": host})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, dict(resp.getheaders()), body


@pytest.mark.requirement("TC-WCON-070")
@pytest.mark.priority("MVP")
def test_tc_wcon_070_a_certificate_is_made_once_and_kept(link_db):
    """WCON-070: self-signed on first use, the key in the secret store, a user pair replaces it, a mismatch is refused."""
    secrets, settings = Mem(), Mem()
    store = CertificateStore(secrets, settings, ["observatory", "127.0.0.1"])
    cert, key = store.ensure()
    assert cert.startswith("-----BEGIN CERTIFICATE") and "PRIVATE KEY" in key
    assert store.ensure() == (cert, key) and store.fingerprint() == fingerprint_of(cert)
    assert "PRIVATE" not in settings.d.get("tls_cert", "")                   # the key never goes in settings
    other = CertificateStore(Mem(), Mem(), ["x"])
    other_cert, other_key = other.ensure()
    with pytest.raises(TlsError):
        store.install_user_pair(cert, other_key)                              # key does not match
    assert store.install_user_pair(other_cert, other_key) == fingerprint_of(other_cert)
    assert store.ensure()[0] == other_cert


@pytest.mark.requirement("TC-WCON-030")
@pytest.mark.requirement("TC-WCON-910")
@pytest.mark.priority("MVP")
def test_tc_wcon_030_the_web_half_runs_in_its_own_process_over_https(link_db):
    """WCON-030/910: a separate ``python -I`` process, configured only over stdin, serving HTTPS, gone on stop."""
    pytest.importorskip("gevent")
    cert, key = CertificateStore(Mem(), Mem(), ["localhost", "127.0.0.1"]).ensure()
    cfg = config(cert, key)
    argv = sup.child_command(PLUGIN_DIR)
    assert "service-secret-token" not in " ".join(argv) and "-I" in argv                # nothing secret on the command line
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        proc.stdin.write(json.dumps(cfg) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line.startswith("ready "), line + proc.stderr.read() if proc.poll() is not None else line
        port = int(line.split()[1])
        status, headers, body = get(port)
        assert status == 200 and b"Galileo" in body
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert get(port, host="evil.example")[0] == 421                                    # host check is live
        assert get(port, "/healthz")[0] == 200
        assert get(port, "/api/v1/piers")[0] == 401                                       # a session route, anonymous
        proc.stdin.write("shutdown\n")
        proc.stdin.flush()
        assert proc.wait(10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        pass                                                                                 # no listener left


@pytest.mark.requirement("TC-WCON-040")
@pytest.mark.priority("MVP")
def test_tc_wcon_040_closing_stdin_ends_the_web_process(link_db):
    """WCON-040: if the Master half vanishes, the web process notices end-of-file and exits; it never lingers."""
    pytest.importorskip("gevent")
    cert, key = CertificateStore(Mem(), Mem(), ["localhost"]).ensure()
    proc = subprocess.Popen(sup.child_command(PLUGIN_DIR), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    try:
        proc.stdin.write(json.dumps(config(cert, key)) + "\n")
        proc.stdin.flush()
        assert proc.stdout.readline().startswith("ready")
        proc.stdin.close()
        assert proc.wait(10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


class FakeProc:
    def __init__(self, die_after=None):
        import io
        self.stdin, self.stdout = io.StringIO(), iter(())
        self.returncode = None
        self._die_at = None if die_after is None else time.monotonic() + die_after
        self.terminated = False

    def poll(self):
        if self._die_at is not None and time.monotonic() >= self._die_at:
            self.returncode = 1
        return self.returncode

    def terminate(self):
        self.terminated, self.returncode = True, -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.mark.requirement("TC-WCON-030")
@pytest.mark.priority("MVP")
def test_tc_wcon_030_a_crashing_web_process_is_restarted_with_backoff_and_reported(monkeypatch):
    """WCON-030: a dying child is restarted, the delay grows, and the person is told after repeated failures."""
    monkeypatch.setattr(sup, "BACKOFF_START_S", 0.02)
    monkeypatch.setattr(sup.Supervisor, "_end", lambda self, g: None)
    spawned, alerts = [], []

    def spawn(argv):
        p = FakeProc(die_after=0.0)
        spawned.append(p)
        return p

    s = sup.Supervisor(["x"], lambda: {"a": 1}, spawn=spawn, on_alert=alerts.append)
    s.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not alerts:
        time.sleep(0.05)
    s._stop.set()
    assert len(spawned) >= 3 and alerts and "keeps failing" in alerts[0]
    assert json.loads(spawned[0].stdin.getvalue().splitlines()[0]) == {"a": 1}             # the one-shot hand-off


@pytest.mark.requirement("TC-WCON-030")
@pytest.mark.priority("MVP")
def test_tc_wcon_030_a_silent_child_is_restarted(monkeypatch):
    """WCON-030: no heartbeat within the timeout means the child is replaced."""
    monkeypatch.setattr(sup, "HEARTBEAT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(sup, "BACKOFF_START_S", 0.01)
    spawned = []

    def spawn(argv):
        p = FakeProc()                                                                      # alive but mute
        spawned.append(p)
        return p

    s = sup.Supervisor(["x"], dict, spawn=spawn)
    s.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(spawned) < 2:
        time.sleep(0.05)
    s.stop(grace_s=0.1)
    assert len(spawned) >= 2 and spawned[0].terminated

