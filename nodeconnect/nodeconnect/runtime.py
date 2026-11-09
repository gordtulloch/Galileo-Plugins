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
import time
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
from nodeconnect.sync import BACKOFF, BLOCKED, SpokeSync, SyncResult
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
        client: Any = None, settings: Any = None,
        make_transfer: Callable[[Credentials], Callable[[], int]] | None = None,
        on_paired: Callable[[Credentials | None], None] | None = None,
        make_history: Callable[[Credentials], Callable[[], int]] | None = None,
        history_endpoint: Any = None, store: Any = None,
        make_hints: Callable[[Credentials, Callable[[], None]], Any] | None = None, watch: Any = None,
        alerts: Any = None,
    ) -> None:
        self._host, self._creds, self._roles = host, creds, roles
        self.desk, self.endpoint = desk, endpoint
        self._make_sync, self._interval = make_sync, interval
        self._make_link = make_link or _https_link
        self._backoff = backoff
        self._make_transfer, self._on_paired = make_transfer, on_paired
        self._make_history, self._history_endpoint = make_history, history_endpoint
        self.client, self.settings = client, settings       # PairingClient (spoke side) and the plugin's settings
        self._make_hints, self._watch = make_hints, watch
        self._hints: Any = None                               # HintListener: the Master's stream, a spoke only
        self._frames: Any = None                              # FrameWatcher: new captures start a transfer pass
        self.alerts = alerts                                  # NCON-340: notifications, or None
        self.last_success: float | None = None                # when a sync last completed (NCON-330)
        self.streams: Any = None
        self.store = store                                  # the NodeStore: run reports and undo for the Options page
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
            if self._watch is not None:
                self._watch.start()                          # hint the nodes when this Master's own settings change

    def stop(self) -> None:
        """Stop the worker and give the listener back (disable, or before a role change)."""
        worker, self.worker = self.worker, None
        frames, self._frames = self._frames, None
        if frames is not None:
            frames.stop()
        hints, self._hints = self._hints, None
        if hints is not None:
            hints.stop()
        if self._watch is not None:
            self._watch.stop()
        if worker is not None:
            worker.stop()
            if self._on_paired:
                self._on_paired(None)
        if self._acquired:
            self._acquired = False
            try:
                self._host.release(PLUGIN)
            except Exception:
                logger.exception("NodeConnect could not release Connect Link")

    def _install(self, server) -> None:
        self.streams = getattr(server, "streams", None)     # the hub the hints are published on
        register_routes(server.routes, self.endpoint)
        transfers = getattr(server, "transfers", None)
        if transfers is not None:
            from nodeconnect.ingest import FrameIngest, register_ingest_route
            register_ingest_route(server.routes, FrameIngest(transfers.object_path))
        if self._history_endpoint is not None:
            from nodeconnect.history import register_history_route
            register_history_route(server.routes, self._history_endpoint)

    def _start_spoke(self) -> None:
        creds = self._creds.load()
        if creds is None:
            return                                           # designated but not yet paired: nothing to sync with
        sync, link = self._make_sync(creds), self._make_link(creds)
        transfer = self._make_transfer(creds) if self._make_transfer else None
        history = self._make_history(creds) if self._make_history else None
        if self._on_paired:
            self._on_paired(creds)

        def one_pass() -> SyncResult:
            result = sync.run_once(link)
            if history is not None:
                try:
                    history()                                # small records first, then the frames
                except Exception:
                    logger.exception("NodeConnect history transfer failed")
            if transfer is not None:
                try:
                    transfer()                               # frames go after configuration, and never fail the pass
                except Exception:
                    logger.exception("NodeConnect frame transfer failed")
            return result
        self.worker = SyncWorker(one_pass, self._interval,
                                 lambda: self._backoff(max(1, sync.failures)),
                                 on_result=self._on_result)
        self.worker.start()
        if transfer is not None:                             # NCON-420: send frames soon after they are taken
            from nodeconnect.frames import FrameWatcher
            self._frames = FrameWatcher(self.worker.sync_now)
            self._frames.start()
        if self._make_hints is not None:                     # NCON-150: the Master tells us when there is something to pull
            self._hints = self._make_hints(creds, self.worker.sync_now)
            self._hints.start()

    def _on_result(self, result: SyncResult) -> None:
        if result.state not in (BACKOFF, BLOCKED):
            self.last_success = time.time()
        if self.alerts is not None:
            self.alerts.sync_result(result)

    def status(self):
        """Where this node stands, for the status view (NCON-330)."""
        from nodeconnect.status import collect
        return collect(self)

    @property
    def master_address(self) -> str:
        return self._creds.designated() or ""

    @property
    def paired(self) -> bool:
        return self._creds.load() is not None

    @property
    def last_result(self) -> SyncResult | None:
        return self.worker.last if self.worker is not None else None

    @property
    def last_error(self) -> str:
        r = self.last_result
        return r.message if r is not None and r.state == BACKOFF else ""

    def sync_interval(self) -> float:
        return float(self._interval())

    def set_sync_interval(self, seconds: float) -> None:
        if self.settings is not None:
            self.settings.set("sync_interval_s", max(30.0, float(seconds)))

    def sync_now(self) -> bool:
        if self.worker is None:
            return False
        self.worker.sync_now()
        return True


def _https_link(creds: Credentials):
    from nodeconnect.link import MasterLink, https_transport
    return MasterLink(https_transport(creds.address, creds.token, creds.fingerprint))


async def _on_loop(fn):
    return fn()


def build_runtime(ctx) -> Runtime:
    """Wire a Runtime from core's services (the real-world composition; tests build their own)."""
    from galileo import settings_store
    from galileo.connect_link.server import default_versions
    from galileo.connect_link.host import get_host
    from galileo.connect_link.secrets_store import KeyringSecretStore
    from galileo.replication import (
        RevisionIssuer,
        SyncStore,
        get_session_busy,
        publish_settings_changed,
        tracking,
    )

    from nodeconnect.apply import Applier, ApplyGate
    from nodeconnect.master import MasterService
    from nodeconnect.reports import retention as report_retention
    from nodeconnect.store import NodeStore
    from nodeconnect.sync import column_fingerprints

    tracking.reset_apply_mode()                              # a crash mid-apply must not leave tracking off
    host = get_host()
    service = host.service
    store = NodeStore(retention=lambda: report_retention(settings))
    creds = CredentialStore(KeyringSecretStore(), settings_store.get_setting, settings_store.set_setting,
                            settings_store.delete_setting)
    roles = RoleGuard(creds.designated, lambda: len(service.peers.list(PLUGIN)))
    desk = PairingDesk(service.peers, service.pairing, store, roles)
    applier = Applier(store, on_changed=publish_settings_changed)
    master = MasterService(store, RevisionIssuer(lambda: roles.role() != SPOKE), applier)
    from nodeconnect.alerts import Alerts
    from nodeconnect.push import (
        HintBroadcaster,
        HintListener,
        MasterWatch,
        dirty_signature,
    )
    alerts = Alerts(ctx.settings())
    runtime_ref: list = []                                   # the Runtime owns the stream hub the hints go on
    broadcaster = HintBroadcaster(
        lambda: runtime_ref[0].streams if runtime_ref else None,
        lambda: [n["peer_id"] for n in desk.nodes() if not n["revoked"]],
        post=lambda fn: host.call(_on_loop(fn)))
    def local_info() -> dict:
        return default_versions().as_dict()

    endpoint = MasterEndpoint(master, local_info, column_fingerprints, is_master=lambda: roles.role() != SPOKE,
                              on_committed=lambda node: broadcaster.notify(node))
    busy = get_session_busy()

    def make_sync(c: Credentials) -> SpokeSync:
        gate = ApplyGate(store, applier, busy.is_busy, busy.on_idle)
        return SpokeSync(store, applier, gate, c.master_id, local_info)

    import platform

    from nodeconnect.link import open_transport
    from nodeconnect.pairing import PairingClient

    settings = ctx.settings()
    from galileo.history import get_history
    from galileo.node_identity import get_node_id

    from nodeconnect.history import HistoryEndpoint
    from nodeconnect.transfer import (
        LibraryAdapter,
        install_retriever,
        make_transfer_job,
        remove_retriever,
    )
    library = LibraryAdapter(busy.is_busy)

    def make_transfer(c: Credentials):
        return make_transfer_job(SyncStore(), library, c, settings, busy.is_busy)

    def make_history(c: Credentials):
        from galileo.history import get_history
        from galileo.node_identity import get_node_id

        from nodeconnect.history import HistoryPush
        from nodeconnect.link import https_transport
        send = https_transport(c.address, c.token, c.fingerprint)
        push = HistoryPush(get_history(), SyncStore(), c.master_id, get_node_id(), send)
        return push.run

    def on_paired(c: Credentials | None) -> None:
        if c is not None:
            install_retriever(c)
        else:
            remove_retriever()

    client = PairingClient(open_transport, creds, roles, platform.node() or "Galileo node", platform.system())
    runtime = Runtime(host, creds, roles, desk, endpoint, make_sync,
                   lambda: float(settings.get("sync_interval_s", DEFAULT_INTERVAL_S) or DEFAULT_INTERVAL_S),
                   client=client, settings=settings,
                   make_transfer=make_transfer, on_paired=on_paired,
                   make_history=make_history,
                   history_endpoint=HistoryEndpoint(get_history(), get_node_id), store=store,
                      make_hints=lambda c, nudge: HintListener(c.address, c.token, c.fingerprint, nudge),
                      watch=MasterWatch(broadcaster, dirty_signature,
                                        on_tick=lambda: alerts.pairing_requests(r["request_id"] for r in desk.pending())),
                      alerts=alerts)
    runtime_ref.append(runtime)
    return runtime
