# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""A NodeConnect Master in its own process, for the one-machine two-node test (``test_ncon_two_nodes.py``).

``python master_process.py <database path>`` opens its own database, seeds an Observatory and a Pier, serves the real
Connect Link listener on a free port with the NodeConnect routes, approves every pairing request that arrives, prints
one JSON line ``{"address": ..., "fingerprint": ...}`` and runs until its standard input closes.
"""

import asyncio
import json
import sys
import threading
from pathlib import Path


def main(db_path: str) -> None:
    from galileo.connect_link.audit import AuditLog
    from galileo.connect_link.certs import CertificateStore
    from galileo.connect_link.pairing import PairingService
    from galileo.connect_link.peers import PeerStore
    from galileo.connect_link.secrets_store import MemorySecretStore
    from galileo.connect_link.server import LinkServer
    from galileo.library.database import init_db
    from galileo.replication import RevisionIssuer, register_defaults

    from nodeconnect.apply import Applier
    from nodeconnect.master import MasterService
    from nodeconnect.pairing import PairingDesk
    from nodeconnect.roles import RoleGuard
    from nodeconnect.store import NodeStore
    from nodeconnect.sync import column_fingerprints
    from nodeconnect.wire import MasterEndpoint, register_routes
    register_defaults()
    init_db(Path(db_path))
    peers = PeerStore()
    pairing = PairingService(peers)
    certs = CertificateStore(MemorySecretStore(), "two-node-master")
    server = LinkServer(peers, pairing, certs, AuditLog())
    store = NodeStore()
    desk = PairingDesk(peers, pairing, store, RoleGuard(lambda: None, lambda: 0))
    # Seed as ordinary local edits (tracking marks them dirty), so the Master issues their revisions as it would for
    # anything the person set up on it.
    from galileo.library.models.base import db
    db.execute_sql("INSERT INTO observatories (name, timezone) VALUES ('Home', 'UTC')")
    oid = db.execute_sql("SELECT id FROM observatories WHERE name = 'Home'").fetchone()[0]
    db.execute_sql("INSERT INTO piers (observatory_id, name, connect_on_startup) VALUES (?, 'Pier 1', 1)", (oid,))
    info = {"galileo": "t", "plugin_api": "1", "schema": 36, "protocol": 1}
    endpoint = MasterEndpoint(MasterService(store, RevisionIssuer(lambda: True), Applier(store)), lambda: info,
                              column_fingerprints)
    register_routes(server.routes, endpoint)

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(server.start("127.0.0.1", 0), loop).result(30)
    print(json.dumps({"address": f"https://127.0.0.1:{server.port}", "fingerprint": certs.fingerprint()}), flush=True)

    def approve() -> None:
        import time
        while True:
            for request in desk.pending():
                desk.approve(request["request_id"])
            time.sleep(0.2)

    threading.Thread(target=approve, daemon=True).start()
    sys.stdin.read()                                                      # the test closes stdin to stop us
    asyncio.run_coroutine_threadsafe(server.stop(), loop).result(10)


if __name__ == "__main__":
    main(sys.argv[1])
