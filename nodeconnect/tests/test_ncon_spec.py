# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The NodeConnect requirements that are properties of the whole plugin, not of one module: notifications (340), what is
not synchronised (470), where a frame lives and how it comes back (480, 490), options in the database (500), help
(510) and the non-functional ones (NFR-010, -020, -040)."""

import re
from pathlib import Path

import pytest
from galileo.library.models.base import db as database

PACKAGE = Path(__file__).resolve().parents[1] / "nodeconnect"


@pytest.fixture
def db(tmp_path):
    from galileo.library.database import init_db
    init_db(tmp_path / "nc.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)


class Settings(dict):
    def set(self, key, value):
        self[key] = value


def _source(*names):
    return {p.name: p.read_text(encoding="utf-8") for p in PACKAGE.glob("*.py") if not names or p.name in names}


# -- NCON-340 ---------------------------------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-340")
@pytest.mark.priority("P2")
def test_tc_ncon_340_alerts_for_a_failing_sync_a_waiting_pairing_and_a_conflict():
    """NCON-340: a sync failing for the configured time is reported once per run of failures; a new pairing request is
    reported once; a resolved conflict is reported; each is individually switchable; sending never raises."""
    from nodeconnect import alerts
    from nodeconnect.sync import BACKOFF, IDLE, SyncResult

    sent, clock = [], [1000.0]
    settings = Settings({alerts.FAILING_AFTER_MIN: 10})
    a = alerts.Alerts(settings, sent.append, lambda: clock[0])

    a.sync_result(SyncResult(BACKOFF, "no route"))
    clock[0] += 5 * 60
    a.sync_result(SyncResult(BACKOFF, "no route"))
    assert sent == []                                                    # not failing for long enough yet
    clock[0] += 6 * 60
    a.sync_result(SyncResult(BACKOFF, "no route"))
    a.sync_result(SyncResult(BACKOFF, "no route"))
    assert len(sent) == 1 and "failing for 11 minute" in sent[0] and "no route" in sent[0]      # once, not per retry
    a.sync_result(SyncResult(IDLE))                                       # it recovered...
    a.sync_result(SyncResult(BACKOFF, "again"))
    clock[0] += 11 * 60
    a.sync_result(SyncResult(BACKOFF, "again"))
    assert len(sent) == 2                                                 # ...so a new failure is a new report

    a.sync_result(SyncResult(IDLE, conflicts=2))
    assert "2 conflicting change(s)" in sent[-1]

    before = len(sent)
    a.pairing_requests(["r1"])
    a.pairing_requests(["r1"])
    a.pairing_requests(["r1", "r2"])
    assert len(sent) == before + 2 and all("waiting to pair" in m for m in sent[before:])

    settings.update({alerts.SYNC_FAILING: False, alerts.PAIRING_REQUEST: False, alerts.CONFLICT: False})
    quiet = alerts.Alerts(settings, sent.append, lambda: clock[0])
    count = len(sent)
    quiet.sync_result(SyncResult(BACKOFF, "x"))
    clock[0] += 3600
    quiet.sync_result(SyncResult(BACKOFF, "x"))
    quiet.sync_result(SyncResult(IDLE, conflicts=1))
    quiet.pairing_requests(["r9"])
    assert len(sent) == count                                              # every switch off: silence

    def broken(_detail):
        raise RuntimeError("smtp down")
    alerts.Alerts(Settings(), broken).pairing_requests(["r1"])            # does not raise

    assert alerts.failing_after_s(Settings({alerts.FAILING_AFTER_MIN: 0})) == 60.0
    assert alerts.failing_after_s(Settings({alerts.FAILING_AFTER_MIN: 10**6})) == alerts.MAX_FAILING_AFTER_MIN * 60.0
    assert alerts.failing_after_s(Settings({alerts.FAILING_AFTER_MIN: "x"})) == alerts.DEFAULT_FAILING_AFTER_MIN * 60.0


# -- NCON-470 ---------------------------------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-470")
@pytest.mark.priority("MVP")
def test_tc_ncon_470_independent_libraries_and_catalog_edits_are_not_merged():
    """NCON-470: no table of the image catalog is replicated, so two independently grown Libraries are never merged
    and user-edited catalog fields are never reconciled."""
    from nodeconnect import repl

    replicated = {s.table for s in repl.specs()}
    catalog = {"fitsFile", "fitsSession", "fits_file", "fits_session", "fitsfile", "fitssession"}
    assert replicated and not (replicated & catalog)
    assert not any(t.lower().startswith("fits") for t in replicated)


# -- NCON-480 / 490 -----------------------------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-480")
@pytest.mark.priority("MVP")
def test_tc_ncon_480_a_frame_is_on_master_only_after_a_verified_receipt(monkeypatch):
    """NCON-480: the Library records Local or On Master; the plugin marks a frame On Master through the Library's own
    verified call and no other route."""
    from galileo.library.core import location

    from nodeconnect.transfer import LibraryAdapter

    calls = []
    monkeypatch.setattr(location, "mark_on_master", lambda *a: calls.append(a))
    LibraryAdapter(lambda: False).mark_on_master("frame-1", "master-1", "ab" * 32)
    assert calls == [("frame-1", "master-1", "ab" * 32)]
    assert {location.LOCAL, location.ON_MASTER} == {"local", "on_master"}
    sources = _source()
    writes = [n for n, text in sources.items() if re.search(r"fitsFileLocation\s*=[^=]|\.update\([^)]*fitsFileLocation", text)]
    assert not writes                                                          # the plugin only reads the column


@pytest.mark.requirement("TC-NCON-490")
@pytest.mark.priority("MVP")
def test_tc_ncon_490_the_library_can_fetch_an_on_master_frame_while_paired(monkeypatch, tmp_path):
    """NCON-490: pairing installs the Library's retriever (by content hash, from this node's Master); unpairing removes
    it, after which an On Master frame says plainly that nothing can fetch it."""
    from galileo.library.core import location

    from nodeconnect import transfer

    fetched = []

    class Conn:
        def __init__(self, creds):
            self.creds = creds

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def download(self, sha256, dest):
            fetched.append((sha256, dest))

    monkeypatch.setattr(transfer, "_Connection", Conn)
    monkeypatch.setattr(location, "_retriever", None)
    transfer.install_retriever(object())
    assert location._retriever is not None
    import asyncio
    asyncio.run(location._retriever("ab" * 32, tmp_path / "f.fits"))
    assert fetched == [("ab" * 32, tmp_path / "f.fits")]
    transfer.remove_retriever()
    assert location._retriever is None


# -- NCON-500 / 510 -----------------------------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-500")
@pytest.mark.priority("MVP")
def test_tc_ncon_500_options_live_in_the_database_and_are_clamped():
    """NCON-500: the plugin writes no settings file and uses no QSettings; every stored value is read through a clamp."""
    from nodeconnect import alerts, reports, transfer
    from nodeconnect.runtime import Runtime

    bad = {"QSettings", "configparser", "ConfigParser", "json.dump(", ".ini", "open("}
    for name, text in _source().items():
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", '"""')))
        for token in bad:
            assert token not in code or (name, token) in {("transfer.py", "open(")}, (name, token)

    assert reports.retention(Settings({"report_retention": 5})) == reports.MIN_RETENTION
    assert alerts.failing_after_s(Settings({alerts.FAILING_AFTER_MIN: -3})) == 60.0
    limits = transfer.limits_from(Settings({"transfer_from_hour": 99, "transfer_to_hour": 7, "remove_after_receipt": "yes"}))
    assert limits.window == (99 % 24, 7) and limits.remove_after_receipt is True
    assert transfer.limits_from(Settings({"transfer_from_hour": 3, "transfer_to_hour": 3})).window is None
    runtime = Runtime.__new__(Runtime)
    runtime.settings = Settings()
    Runtime.set_sync_interval(runtime, 1)
    assert runtime.settings["sync_interval_s"] == 30.0                    # the floor


@pytest.mark.requirement("TC-NCON-510")
@pytest.mark.priority("MVP")
def test_tc_ncon_510_every_control_on_the_page_has_a_help_entry():
    """NCON-510: each button, check box, group and labelled field on the Options page has a heading in the plugin's
    help file (the screen-wide audit needs the whole application; this checks the plugin's own half)."""
    ui = (PACKAGE / "ui.py").read_text(encoding="utf-8")
    help_text = (PACKAGE / "help" / "plugin.NodeConnect.md").read_text(encoding="utf-8")
    headings = {h.strip().lower().rstrip(":") for h in re.findall(r"^## (.+)$", help_text, re.MULTILINE)}
    labels = set(re.findall(r'(?:QPushButton|QCheckBox|QGroupBox)\("([^"]+)"\)', ui))
    labels |= {m.rstrip(":") for m in re.findall(r'addRow\("([^"]+:)"', ui)}
    labels |= set(re.findall(r'addRow\(QLabel\("([^"]+)"\)', ui))
    # headings may be the label or a shorter form of it; a few controls are explained under a neighbour
    explained_elsewhere = {"transfer from", "transfer to"}                    # shown as the label, headed separately
    missing = sorted(label for label in labels
                     if label.lower().rstrip(":") not in headings and label.lower() not in explained_elsewhere
                     and not any(label.lower().rstrip(":") in h for h in headings))
    assert not missing, f"controls with no help heading: {missing}"


# -- non-functional -----------------------------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-NFR-040")
@pytest.mark.priority("MVP")
def test_tc_ncon_nfr_040_no_platform_specific_code():
    """NCON-NFR-040: the plugin has no operating-system branches, no Windows-only or POSIX-only modules; the one use of
    ``platform`` is to report what this computer is called, which is information, not a branch."""
    banned = ("sys.platform", "os.name", "winreg", "ctypes.windll", "import fcntl", "import msvcrt", "import pwd",
              "import grp", "os.fork", "signal.SIGKILL", "/proc/", "C:\\\\")
    for name, text in _source().items():
        for token in banned:
            assert token not in text, f"{name}: {token}"
    uses = {n for n, s in _source().items() if re.search(r"platform\.(system|node)\(", s)}
    assert uses <= {"runtime.py"}


@pytest.mark.requirement("TC-NCON-NFR-020")
@pytest.mark.priority("MVP")
def test_tc_ncon_nfr_020_transfer_never_reads_a_whole_frame_into_memory():
    """NCON-NFR-020: the plugin hands frames to Connect Link's chunked, resumable transfer by path and never reads a frame
    whole, so memory does not grow with frame size or queue length."""
    from galileo.connect_link import transfer as core_transfer

    text = _source("transfer.py")["transfer.py"]
    for token in (".read_bytes()", ".read()", "readall", "b''.join", "b\"\".join"):
        assert token not in text, token
    chunk = getattr(core_transfer, "CHUNK_SIZE", None) or getattr(core_transfer, "CHUNK", None)
    assert chunk is None or chunk <= 16 * 1024 * 1024                      # a small constant number of bounded chunks


@pytest.mark.requirement("TC-NCON-NFR-010")
@pytest.mark.priority("P2")
def test_tc_ncon_nfr_010_an_incremental_change_set_applies_quickly(db):
    """NCON-NFR-010: 500 records apply in well under the 5 s allowed for a whole sync. This measures the part that is
    computation (parse, order, apply, snapshot) on the development machine; the Raspberry Pi 5 figure over a LAN is a
    hardware measurement (``hardware``) still to be taken."""
    import time

    from nodeconnect.apply import UPSERT, Applier, Change
    from nodeconnect.store import NodeStore

    changes = [Change("observatories", UPSERT, ["Home"], {"name": "Home", "timezone": "UTC"}, 1)]
    changes += [Change("piers", UPSERT, ["Home", f"Pier {i}"],
                       {"observatory": "Home", "name": f"Pier {i}", "connect_on_startup": 1}, 2 + i) for i in range(499)]
    started = time.perf_counter()
    report = Applier(NodeStore()).apply(changes)
    elapsed = time.perf_counter() - started
    assert report.outcome == "ok" and report.applied["piers"] == 499
    assert elapsed < 5.0, f"500 records took {elapsed:.1f}s"


# -- the document chain ---------------------------------------------------------------------------------------------
CORE_DOCS = Path(__file__).resolve().parents[3] / "Galileo" / "docs" / "plugins" / "nodeconnect"


@pytest.mark.skipif(not CORE_DOCS.exists(), reason="the core repository's docs are not next to this one")
def test_ncon_doc_chain_every_requirement_has_an_rtm_row_and_every_test_id_has_a_test():
    """SRS, RTM and these tests agree: each NCON requirement has an RTM row, each RTM test case has a test in this
    suite, and no test claims an ID the RTM does not reserve."""
    srs = (CORE_DOCS / "SRS.md").read_text(encoding="utf-8")
    rtm = (CORE_DOCS / "RTM.md").read_text(encoding="utf-8")
    requirements = set(re.findall(r"^\| (NCON-[A-Z0-9-]+) \|", srs, re.MULTILINE))
    rows = set(re.findall(r"^\| `(NCON-[A-Z0-9-]+)` \|", rtm, re.MULTILINE))
    assert requirements and requirements <= rows, sorted(requirements - rows)
    reserved = set(re.findall(r"`(TC-NCON-[A-Z0-9-]+)`", rtm))
    used = set()
    for path in (Path(__file__).parent).glob("test_ncon*.py"):
        used |= set(re.findall(r'requirement\("(TC-NCON-[A-Z0-9-]+)"\)', path.read_text(encoding="utf-8")))
    assert reserved - used == set(), f"no test for {sorted(reserved - used)}"
    assert used - reserved == set(), f"tests for IDs the RTM does not reserve: {sorted(used - reserved)}"


# -- NCON-330: the status view ----------------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-330")
@pytest.mark.priority("MVP")
def test_tc_ncon_330_status_view_shows_pairing_connection_revision_queues_and_errors(db):
    """NCON-330: the status gathers the Master and pairing state, connection state, last successful sync, revision, the
    configuration and image queues and the last error, and a part that cannot be read leaves the rest intact."""
    from types import SimpleNamespace

    from galileo.replication import SyncStore

    from nodeconnect import status as st
    from nodeconnect.roles import MASTER, SPOKE
    from nodeconnect.store import NodeStore
    from nodeconnect.sync import BACKOFF, IDLE, SyncResult

    store = NodeStore()
    store.peer_set("master-1", "last_rev", {"piers": 7, "devices": 4, "piers#t": 99})
    store.inbox_add("master-1", "piers", ["Home", "East"], "upsert", {"name": "East"}, 3)
    SyncStore().transfer_enqueue("master-1", "up", "ab" * 32, 2_000_000, 5)

    def runtime(result, role=SPOKE, **extra):
        base = {"role": role, "master_address": "https://obs.local:8765", "paired": True, "last_result": result,
                    "last_error": "", "last_success": 1_700_000_000.0, "store": store, "desk": None,
                    "_creds": SimpleNamespace(load=lambda: SimpleNamespace(master_id="master-1"))}
        base.update(extra)
        return SimpleNamespace(**base)

    ok = st.collect(runtime(SyncResult(IDLE, pulled=3, pushed=1)))
    assert ok.connection == st.CONNECTED and ok.revision == 7                       # the tombstone cursor is not a revision
    assert ok.staged_changes == 1 and ok.queue_items == 1 and ok.queue_bytes == 2_000_000
    assert ok.last_success == 1_700_000_000.0 and "3 received, 1 sent" in ok.last_sync
    text = "\n".join(st.lines(ok))
    assert "paired" in text and "Revision: 7" in text and "Images waiting: 1 (2.0 MB)" in text
    assert "Last successful sync: 2023" in text

    failing = st.collect(runtime(SyncResult(BACKOFF, "no route"), last_error="no route"))
    assert failing.connection == st.RETRYING and "Last problem: no route" in "\n".join(st.lines(failing))
    assert st.collect(runtime(SyncResult(IDLE, deferred=True))).connection == st.WAITING
    assert st.collect(runtime(None)).connection == st.IDLE
    assert st.collect(runtime(None, paired=False)).connection == st.NOT_PAIRED
    assert st.collect(runtime(None, paired=False, master_address="")).connection == st.NO_MASTER
    never = st.collect(runtime(None, last_success=None))
    assert "Last successful sync: never" in "\n".join(st.lines(never))

    master = st.collect(runtime(None, role=MASTER, desk=SimpleNamespace(nodes=lambda: [{"name": "Laptop"}])))
    assert master.nodes == [{"name": "Laptop"}] and "Master with 1 paired node" in "\n".join(st.lines(master))

    broken = st.collect(runtime(None, store=SimpleNamespace(
        inbox_pending=lambda: (_ for _ in ()).throw(RuntimeError("db locked")),
        peer_get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")))))
    assert broken.staged_changes == 0 and broken.queue_items == 1                      # one part failed, the rest read


# -- NCON-420: frames go soon after capture ---------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-420")
@pytest.mark.priority("MVP")
def test_tc_ncon_420_a_pause_in_captures_starts_one_transfer_pass():
    """NCON-420: a run of captures causes one pass after they pause, not one per frame; stopping cancels a pending pass
    and unsubscribes; a failing nudge never reaches the capture."""
    import threading

    from galileo.bus import FrameCapturedEvent

    from nodeconnect.frames import FrameWatcher

    class Bus:
        def __init__(self):
            self.handlers = []

        def subscribe(self, event_type, handler):
            assert event_type is FrameCapturedEvent
            self.handlers.append(handler)

        def unsubscribe(self, event_type, handler):
            self.handlers.remove(handler)

    bus, passes, fired = Bus(), [], threading.Event()
    watcher = FrameWatcher(lambda: (passes.append(1), fired.set()), bus=bus, delay_s=0.15)
    watcher.start()
    watcher.start()                                                                   # twice: still one subscription
    assert len(bus.handlers) == 1
    for _ in range(5):
        bus.handlers[0](object())                                                      # five frames in quick succession
    assert fired.wait(3) and passes == [1]
    import time
    time.sleep(0.4)
    assert passes == [1]                                                               # nothing further

    fired.clear()
    bus.handlers[0](object())
    watcher.stop()
    assert bus.handlers == [] and not fired.wait(0.5) and passes == [1]               # the pending pass was cancelled

    def boom():
        raise RuntimeError("worker gone")
    failing = FrameWatcher(boom, bus=Bus(), delay_s=0.01)
    failing.start()
    failing._bus.handlers[0](object())
    time.sleep(0.2)
    failing.stop()
