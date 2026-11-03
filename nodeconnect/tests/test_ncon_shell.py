# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect plugin shell (TC-NCON-010, -310, -520, -530): what runs when the plugin is enabled and
what is left when it is disabled, with Connect Link replaced by a recording host."""

import threading
import time
from pathlib import Path

import pytest
from galileo import settings_store
from galileo.connect_link.secrets_store import MemorySecretStore

import nodeconnect
from nodeconnect.pairing import NODE_PERMISSIONS, Credentials, CredentialStore
from nodeconnect.plugin import NodeConnectPlugin
from nodeconnect.roles import MASTER, SPOKE, STANDALONE, RoleGuard
from nodeconnect.runtime import Runtime, SyncWorker
from nodeconnect.sync import BACKOFF, IDLE, SyncResult


class RecordingHost:
    def __init__(self):
        self.acquired, self.released, self.installers = [], [], []

    def acquire(self, plugin, permissions=(), install=None):
        self.acquired.append((plugin, tuple(permissions)))
        self.installers.append(install)

    def release(self, plugin):
        self.released.append(plugin)


class FakeSync:
    failures = 0

    def __init__(self, results):
        self.results, self.calls = list(results), 0

    def run_once(self, link):
        self.calls += 1
        r = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def make_runtime(designated=None, spokes=0, paired=False, sync=None, interval=0.01):
    creds = CredentialStore(MemorySecretStore(), settings_store.get_setting, settings_store.set_setting,
                            settings_store.delete_setting)
    if designated and paired:
        creds.save(Credentials(designated, "ab" * 32, "tok"))
    elif designated:
        settings_store.set_setting("plugin.NodeConnect", "master_address", designated)
    roles = RoleGuard(creds.designated, lambda: spokes)
    host = RecordingHost()
    sync = sync or FakeSync([SyncResult(IDLE)])
    rt = Runtime(host, creds, roles, desk=None, endpoint=object(), make_sync=lambda c: sync,
                 interval=lambda: interval, make_link=lambda c: object(), backoff=lambda n: 0.01)
    return rt, host, sync


def wait_for(cond, seconds=5.0):
    deadline = time.time() + seconds
    while not cond() and time.time() < deadline:
        time.sleep(0.01)


@pytest.mark.requirement("TC-NCON-310")
@pytest.mark.priority("MVP")
def test_tc_ncon_310_a_master_takes_a_place_on_the_listener_and_disabling_gives_it_back(settings_db):
    """NCON-310 / NCON-010: a node that is not a spoke asks Connect Link for a place with exactly the
    node permissions and an install function; stopping releases it and leaves no thread."""
    rt, host, _ = make_runtime(spokes=2)
    assert rt.role == MASTER
    rt.start()
    assert host.acquired == [("nodeconnect", NODE_PERMISSIONS)] and callable(host.installers[0]) and rt.worker is None
    rt.stop()
    rt.stop()                                                         # stopping twice is harmless
    assert host.released == ["nodeconnect"]


@pytest.mark.requirement("TC-NCON-310")
@pytest.mark.priority("MVP")
def test_tc_ncon_310_a_standalone_node_can_be_asked_to_be_master(settings_db):
    """NCON-020: a node with no Master designated listens, so a spoke can ask to pair with it."""
    rt, host, _ = make_runtime()
    assert rt.role == STANDALONE
    rt.start()
    assert host.acquired


@pytest.mark.requirement("TC-NCON-310")
@pytest.mark.priority("MVP")
def test_tc_ncon_310_a_spoke_syncs_in_the_background_serves_nobody_and_stops_cleanly(settings_db):
    """NCON-310 / NCON-120: a paired spoke starts one worker that syncs at once and then on its
    interval; it asks for no listener; stopping joins the thread and nothing runs afterwards."""
    rt, host, sync = make_runtime(designated="https://m:4400", paired=True)
    assert rt.role == SPOKE
    rt.start()
    wait_for(lambda: sync.calls >= 3)
    assert sync.calls >= 3 and host.acquired == []
    worker = rt.worker
    rt.stop()
    assert not worker.running and not any(t.name == "nodeconnect-sync" for t in threading.enumerate())
    calls = sync.calls
    time.sleep(0.05)
    assert sync.calls == calls


@pytest.mark.requirement("TC-NCON-310")
@pytest.mark.priority("MVP")
def test_tc_ncon_310_a_designated_but_unpaired_spoke_starts_nothing(settings_db):
    """NCON-030 / NCON-310: until the pairing is approved there is nothing to sync with, and no thread."""
    rt, host, sync = make_runtime(designated="https://m:4400", paired=False)
    rt.start()
    assert rt.worker is None and sync.calls == 0 and not host.acquired and not rt.sync_now()


@pytest.mark.requirement("TC-NCON-530")
@pytest.mark.priority("MVP")
def test_tc_ncon_530_a_failing_pass_never_kills_the_worker(settings_db):
    """NCON-530 / NCON-300: an exception out of a pass, or a back-off result, is logged and the worker
    carries on with the next pass."""
    sync = FakeSync([RuntimeError("boom"), SyncResult(BACKOFF, "down"), SyncResult(IDLE)])
    rt, _, _ = make_runtime(designated="https://m:4400", paired=True, sync=sync)
    rt.start()
    wait_for(lambda: sync.calls >= 4)
    assert sync.calls >= 4 and rt.worker.running
    rt.stop()


@pytest.mark.requirement("TC-NCON-530")
@pytest.mark.priority("MVP")
def test_tc_ncon_530_sync_now_wakes_the_worker_before_its_interval():
    """NCON-120: a request to sync now does not wait out the interval."""
    ran = threading.Semaphore(0)

    def run():
        ran.release()
        return SyncResult(IDLE)

    w = SyncWorker(run, lambda: 60.0, lambda: 60.0)
    w.start()
    assert ran.acquire(timeout=5)
    w.sync_now()
    assert ran.acquire(timeout=5)
    w.stop()


@pytest.mark.requirement("TC-NCON-010")
@pytest.mark.priority("MVP")
def test_tc_ncon_010_the_plugin_is_inert_when_it_cannot_start_and_when_disabled(monkeypatch):
    """NCON-010 / NCON-530 / PLUG-060: a failing start leaves the plugin inert and raises nothing;
    enabling and disabling a running one starts and stops its runtime."""
    import nodeconnect.plugin as plugin_module

    def broken(ctx):
        raise RuntimeError("no core")

    monkeypatch.setattr(plugin_module, "build_runtime", broken)
    p = NodeConnectPlugin()
    p.activate(object())
    assert p.runtime is None
    p.on_enabled_changed(False)
    p.deactivate()

    class Rt:
        def __init__(self):
            self.log = []

        def start(self):
            self.log.append("start")

        def stop(self):
            self.log.append("stop")

    monkeypatch.setattr(plugin_module, "build_runtime", lambda ctx: Rt())
    p.activate(object())
    p.on_enabled_changed(False)
    p.on_enabled_changed(True)
    rt = p.runtime
    p.deactivate()
    assert rt.log == ["start", "stop", "start", "stop"] and p.runtime is None
    assert (p.name, p.api_version) == ("NodeConnect", "1")


@pytest.mark.requirement("TC-NCON-520")
@pytest.mark.priority("MVP")
def test_tc_ncon_520_the_manifest_matches_the_plugin_and_the_loader_finds_the_class():
    """NCON-520 / NCON-010: plugin.toml parses with core's own manifest reader, names the package the
    class is found in, and the package exposes the plugin class for the loader."""
    from galileo.plugins import PluginBase, PluginManifest
    text = (Path(__file__).parent.parent / "plugin.toml").read_text(encoding="utf-8")
    manifest = PluginManifest.from_toml_text(text)
    assert manifest.name == "nodeconnect" and manifest.resolved_entry_point == "nodeconnect"
    assert manifest.version == nodeconnect.__version__ and manifest.nav_level == "primary"
    assert issubclass(nodeconnect.NodeConnectPlugin, PluginBase) and nodeconnect.NodeConnectPlugin.name


@pytest.mark.requirement("TC-NCON-330")
@pytest.mark.priority("MVP")
def test_tc_ncon_330_options_page_is_tagged_and_every_control_has_help(qapp, settings_db):
    """NCON-330: the Options page exists and every control on it has an inline-help entry."""
    from galileo import help as help_content
    from galileo.ui.help import audit, tag_help_screen

    from nodeconnect.ui import NodeConnectOptions
    help_content.register_dir(Path(nodeconnect.__file__).parent / "help")
    rt, _host, _sync = make_runtime()
    assert NodeConnectPlugin.options_label == "NodeConnect"
    page = tag_help_screen(NodeConnectOptions(rt), "plugin.NodeConnect")
    assert help_content.load("plugin.NodeConnect") is not None
    assert audit(page) == []


@pytest.mark.requirement("TC-NCON-440")
@pytest.mark.priority("MVP")
def test_tc_ncon_440_transfer_runs_after_each_sync_and_never_fails_it(settings_db):
    """NCON-440/530: frames are sent after each configuration pass; a transfer error cannot fail the pass."""
    rt, _host, sync = make_runtime(designated="https://m:1", paired=True)
    calls, seen = [], []
    def job():
        calls.append(1)
        raise RuntimeError("link down")
    rt._make_transfer = lambda c: job
    rt._on_paired = seen.append
    rt.start()
    wait_for(lambda: len(calls) >= 2)
    rt.stop()
    assert len(calls) >= 2 and sync.calls >= 2                    # the sync kept going despite the errors
    assert seen[0] is not None and seen[-1] is None               # retriever installed, then removed on stop
