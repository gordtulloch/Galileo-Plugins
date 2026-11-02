# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""What runs while the plugin is enabled (NCON-010, NCON-310, NCON-530).

``SyncWorker`` is the one background thread: it runs a sync pass at start, then again after the
configured interval, or after a back-off delay when the last pass failed, and it can be nudged to run
now. ``Runtime`` ties the pieces together and decides what the node is: a **spoke** (a Master is
designated: it syncs, serves nobody, and needs no listener) or otherwise a **Master** (waiting to be
asked: it takes a place on core's Connect Link listener). Disabling stops the thread and gives the
listener back, leaving nothing running (NCON-310); nothing a sync does can raise out of the worker
(NCON-530).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from nodeconnect.pairing import (
    NODE_PERMISSIONS,
    PLUGIN,
    Credentials,
    CredentialStore,
    PairingDesk,
)
from nodeconnect.roles import SPOKE, RoleGuard
from nodeconnect.sync import BACKOFF, SpokeSync, SyncResult
from nodeconnect.wire import MasterEndpoint, register_routes

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 300.0


class SyncWorker:
    def __init__(self, run: Callable[[], SyncResult], interval: Callable[[], float],
                 backoff: Callable[[], float], on_result: Callable[[SyncResult], None] | None = None) -> None:
        self._run, self._interval, self._backoff, self._on_result = run, interval, backoff, on_result
        self._stop = threading.Event()
        self._nudge = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: SyncResult | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nodeconnect-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._nudge.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def sync_now(self) -> None:
        self._nudge.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.last = self._run()
                if self._on_result is not None:
                    self._on_result(self.last)
                wait = self._backoff() if self.last.state == BACKOFF else self._interval()
            except Exception:                              # fault isolation: the worker never dies of a bad pass
                logger.exception("NodeConnect sync pass failed")
                wait = self._backoff()
            if self._stop.is_set():
                break
            self._nudge.wait(wait)
            self._nudge.clear()


class Runtime:
    def __init__(
        self, host: Any, creds: CredentialStore, roles: RoleGuard, desk: PairingDesk, endpoint: MasterEndpoint,
        make_sync: Callable[[Credentials], SpokeSync],
        interval: Callable[[], float] = lambda: DEFAULT_INTERVAL_S,
        make_link: Callable[[Credentials], Any] | None = None,
        backoff: Callable[[int], float] = SpokeSync.backoff_delay,
    ) -> None:
        self._host, self._creds, self._roles = host, creds, roles
        self.desk, self.endpoint = desk, endpoint
        self._make_sync, self._interval = make_sync, interval
        self._make_link = make_link or _https_link
        self._backoff = backoff
        self.worker: SyncWorker | None = None
        self._acquired = False

    @property
    def role(self) -> str:
        return self._roles.role()

    def start(self) -> None:
        """Begin whatever this node's role calls for. Safe to call again."""
        self.stop()
        if self.role == SPOKE:
            self._start_spoke()
        else:
            self._host.acquire(PLUGIN, NODE_PERMISSIONS, self._install)
            self._acquired = True

    def stop(self) -> None:
        """Stop the worker and give the listener back (disable, or before a role change)."""
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.stop()
        if self._acquired:
            self._acquired = False
            try:
                self._host.release(PLUGIN)
            except Exception:
                logger.exception("NodeConnect could not release Connect Link")

    def _install(self, server) -> None:
        register_routes(server.routes, self.endpoint)

    def _start_spoke(self) -> None:
        creds = self._creds.load()
        if creds is None:
            return                                           # designated but not yet paired: nothing to sync with
        sync, link = self._make_sync(creds), self._make_link(creds)
        self.worker = SyncWorker(lambda: sync.run_once(link), self._interval,
                                 lambda: self._backoff(max(1, sync.failures)))
        self.worker.start()

    def sync_now(self) -> bool:
        if self.worker is None:
            return False
        self.worker.sync_now()
        return True


def _https_link(creds: Credentials):
    from nodeconnect.link import MasterLink, https_transport
    return MasterLink(https_transport(creds.address, creds.token, creds.fingerprint))


def build_runtime(ctx) -> Runtime:
    """Wire a Runtime from core's services (the real-world composition; tests build their own)."""
    from galileo import settings_store
    from galileo.connect_link.handshake import local_info
    from galileo.connect_link.host import get_host
    from galileo.connect_link.secrets_store import KeyringSecretStore
    from galileo.replication import (
        RevisionIssuer,
        get_session_busy,
        publish_settings_changed,
        tracking,
    )

    from nodeconnect.apply import Applier, ApplyGate
    from nodeconnect.master import MasterService
    from nodeconnect.store import NodeStore
    from nodeconnect.sync import column_fingerprints

    tracking.reset_apply_mode()                              # a crash mid-apply must not leave tracking off
    host = get_host()
    service = host.service
    store = NodeStore()
    creds = CredentialStore(KeyringSecretStore(), settings_store.get_setting, settings_store.set_setting,
                            settings_store.delete_setting)
    roles = RoleGuard(creds.designated, lambda: len(service.peers.list(PLUGIN)))
    desk = PairingDesk(service.peers, service.pairing, store, roles)
    applier = Applier(store, on_changed=publish_settings_changed)
    master = MasterService(store, RevisionIssuer(lambda: roles.role() != SPOKE), applier)
    endpoint = MasterEndpoint(master, local_info, column_fingerprints, is_master=lambda: roles.role() != SPOKE)
    busy = get_session_busy()

    def make_sync(c: Credentials) -> SpokeSync:
        gate = ApplyGate(store, applier, busy.is_busy, busy.on_idle)
        return SpokeSync(store, applier, gate, c.master_id, local_info)

    settings = ctx.settings()
    return Runtime(host, creds, roles, desk, endpoint, make_sync,
                   lambda: float(settings.get("sync_interval_s", DEFAULT_INTERVAL_S) or DEFAULT_INTERVAL_S))
