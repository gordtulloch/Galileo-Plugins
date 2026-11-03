# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Two nodes, two processes, one real TLS connection (TC-NCON-120, -140, -150 on a single machine).

The Master runs in its own process with its own database and the real Connect Link listener
(``master_process.py``); the spoke is this process with its own database. They pair through the real pairing flow and
sync over the pinned, token-authenticated transport. This is the closest to two computers that one computer allows;
the same test on two machines on a LAN remains to be done (``hardware``)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from galileo import settings_store
from galileo.connect_link.secrets_store import MemorySecretStore
from galileo.library.database import init_db
from galileo.library.models.base import db as database

from nodeconnect.apply import Applier, ApplyGate
from nodeconnect.link import LinkError, MasterLink, https_transport, open_transport
from nodeconnect.pairing import CredentialStore, PairingClient
from nodeconnect.roles import RoleGuard
from nodeconnect.store import NodeStore
from nodeconnect.sync import IDLE, SpokeSync

HELPER = Path(__file__).parent / "master_process.py"
INFO = {"galileo": "t", "plugin_api": "1", "schema": 36, "protocol": 1}


@pytest.fixture
def master(tmp_path):
    proc = subprocess.Popen([sys.executable, str(HELPER), str(tmp_path / "master.db")], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": str(HELPER.parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")})
    try:
        line = proc.stdout.readline()
        assert line, proc.stderr.read()
        yield json.loads(line)
    finally:
        proc.stdin.close()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def spoke_db(tmp_path):
    init_db(tmp_path / "spoke.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)


@pytest.mark.integration
@pytest.mark.requirement("TC-NCON-120")
@pytest.mark.priority("MVP")
def test_tc_ncon_120_a_spoke_pairs_with_a_master_in_another_process_and_syncs(master, spoke_db):
    """NCON-120 / NCON-030 / NCON-140: a spoke with its own database pairs with a Master in another process and receives
    the Master's Observatory and Pier over TLS; a second pass is quiet."""
    creds_store = CredentialStore(MemorySecretStore(), settings_store.get_setting, settings_store.set_setting,
                                  settings_store.delete_setting)
    client = PairingClient(open_transport, creds_store, RoleGuard(lambda: None, lambda: 0), "Laptop", "linux")
    pending = client.request(master["address"])
    assert pending.fingerprint == master["fingerprint"]                      # the certificate shown is the one that answers
    import time
    end = time.time() + 20
    state = client.poll(pending)
    while state == "pending" and time.time() < end:
        time.sleep(0.3)
        state = client.poll(pending)
    assert state == "approved"
    creds = creds_store.load()

    store = NodeStore()
    applier = Applier(store)
    gate = ApplyGate(store, applier, lambda: False, lambda fn: None)
    sync = SpokeSync(store, applier, gate, creds.master_id, lambda: INFO)
    link = MasterLink(https_transport(creds.address, creds.token, creds.fingerprint))

    first = sync.run_once(link)
    assert first.state == IDLE and first.pulled >= 2, first
    rows = [r[0] for r in database.execute_sql("SELECT name FROM piers").fetchall()]
    assert rows == ["Pier 1"]
    assert [r[0] for r in database.execute_sql("SELECT name FROM observatories").fetchall()] == ["Home"]

    second = sync.run_once(link)
    assert second.state == IDLE and second.pulled == 0 and second.pushed == 0

    bad = MasterLink(https_transport(creds.address, "forged.token", creds.fingerprint))
    with pytest.raises(LinkError) as refused:
        bad.handshake(INFO, {})
    assert refused.value.status == 401
