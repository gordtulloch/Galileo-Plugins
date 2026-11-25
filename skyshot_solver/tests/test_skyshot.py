# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""skyshot_solver tests: TC-SKY-010 … TC-SKY-070 (requirements in docs/plugins/skyshot_solver/SRS.md).

No network and no Skyshot program: downloads are served from memory and the program is replaced by
a fake process.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from skyshot_solver import backend, data
from skyshot_solver.settings import SkyshotSettings

BUNDLE = "skyshot-full-lgq-test"


class _Store:
    """Stands in for ``PluginSettings``."""

    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        return True


def _tar(members: dict[str, bytes], bundle_id: str = BUNDLE) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _good_archive() -> tuple[bytes, bytes]:
    bundle_json = json.dumps({"bundle_id": BUNDLE}).encode()
    return _tar({f"{BUNDLE}/bundle.json": bundle_json, f"{BUNDLE}/index.bin": b"stars",
                 f"{BUNDLE}/LICENSE-DATA.txt": b"data terms"}), bundle_json


def _release_text(archive: bytes, bundle_json: bytes, base_url: str = "https://example.org/dl/") -> str:
    return json.dumps({
        "schema_version": 1, "release_id": "test", "base_url": base_url, "engine_versions": ["0.1.0"],
        "presets": {"full": {"index": BUNDLE}},
        "artifacts": [{
            "bundle_id": BUNDLE, "archive": f"{BUNDLE}.tar.gz", "bytes": len(archive),
            "sha256": hashlib.sha256(archive).hexdigest(),
            "manifest_sha256": hashlib.sha256(bundle_json).hexdigest(),
        }],
    })


class _Resp(io.BytesIO):
    status = 200

    def geturl(self):
        return "https://example.org/dl/x"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def serve(monkeypatch):
    """Serve ``payload`` for any download."""
    state = {"payload": b""}
    monkeypatch.setattr("galileo.net.urlopen_http", lambda req, timeout=None, data=None: _Resp(state["payload"]))
    return state


@pytest.mark.requirement("TC-SKY-030")
@pytest.mark.priority("MVP")
def test_tc_sky_030_manifest_is_validated():
    """SKY-030: the release manifest is accepted only when it is https, well-formed and for this engine."""
    archive, bundle_json = _good_archive()
    release = data.parse_release(_release_text(archive, bundle_json))
    assert [slot for slot, _ in release.plan("full")] == ["index"]
    with pytest.raises(data.DataError, match="secure"):
        data.parse_release(_release_text(archive, bundle_json, base_url="http://example.org/dl/"))
    broken = json.loads(_release_text(archive, bundle_json))
    broken["artifacts"][0]["sha256"] = "zz"
    with pytest.raises(data.DataError, match="malformed"):
        data.parse_release(json.dumps(broken))
    with pytest.raises(data.DataError):
        release.plan("no-such-preset")


@pytest.mark.requirement("TC-SKY-040")
@pytest.mark.priority("MVP")
def test_tc_sky_040_install_verifies_and_never_overwrites(tmp_path, serve):
    """SKY-040: an index is installed only after its checksum matches, and an installed one is left alone."""
    archive, bundle_json = _good_archive()
    release = data.parse_release(_release_text(archive, bundle_json))
    serve["payload"] = archive
    installed = data.install_preset(release, "full", tmp_path)
    assert installed == ["index"]
    index = data.index_path(tmp_path, "test")
    assert index is not None and (index / "index.bin").read_bytes() == b"stars"
    assert not list((tmp_path / "test").glob(".unpack-*"))
    (index / "index.bin").write_bytes(b"edited")
    assert data.install_preset(release, "full", tmp_path) == []          # already installed: nothing to do
    assert (index / "index.bin").read_bytes() == b"edited"


@pytest.mark.requirement("TC-SKY-040")
@pytest.mark.priority("MVP")
def test_tc_sky_040_corrupt_download_is_discarded(tmp_path, serve):
    """SKY-040: an archive that does not match its published checksum is discarded and nothing is installed."""
    archive, bundle_json = _good_archive()
    release = data.parse_release(_release_text(archive, bundle_json))
    serve["payload"] = archive[:-1] + bytes([archive[-1] ^ 1])           # same size, one bit flipped
    with pytest.raises(data.DataError, match="checksum"):
        data.install_preset(release, "full", tmp_path)
    assert data.index_path(tmp_path, "test") is None
    assert not list((tmp_path / "download-cache" / "test").glob("*"))


@pytest.mark.requirement("TC-SKY-040")
@pytest.mark.priority("MVP")
@pytest.mark.parametrize("name", [f"{BUNDLE}/../escape", "elsewhere/bundle.json", f"{BUNDLE}/sub/deep.bin"])
def test_tc_sky_040_unsafe_archive_layout_is_refused(tmp_path, serve, name):
    """SKY-040: an archive with a traversal path or the wrong layout is refused without writing outside the stage."""
    bundle_json = json.dumps({"bundle_id": BUNDLE}).encode()
    archive = _tar({f"{BUNDLE}/bundle.json": bundle_json, name: b"x"})
    release = data.parse_release(_release_text(archive, bundle_json))
    serve["payload"] = archive
    with pytest.raises(data.DataError, match="layout"):
        data.install_preset(release, "full", tmp_path)
    assert not (tmp_path / "escape").exists() and data.index_path(tmp_path, "test") is None


@pytest.mark.requirement("TC-SKY-050")
@pytest.mark.priority("P2")
def test_tc_sky_050_cancel_keeps_partial_download(tmp_path, monkeypatch):
    """SKY-050: cancelling stops the download and keeps what arrived so the next try can resume."""
    import threading
    archive, bundle_json = _good_archive()
    release = data.parse_release(_release_text(archive, bundle_json))
    monkeypatch.setattr("galileo.net.urlopen_http", lambda req, timeout=None, data=None: _Resp(archive))
    monkeypatch.setattr(data, "_CHUNK", 8)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(data.Cancelled):
        data.install_preset(release, "full", tmp_path, cancel=cancel)
    assert (tmp_path / "download-cache" / "test" / f"{BUNDLE}.tar.gz.part").is_file()


# --- backend -----------------------------------------------------------------------------------

def _wcs_file(path: Path, crpix: tuple[float, float], crval=(150.0, 30.0), scale_deg=0.0005) -> Path:
    from astropy.io import fits
    header = fits.Header()
    header["WCSAXES"] = 2
    header["CTYPE1"], header["CTYPE2"] = "RA---TAN", "DEC--TAN"
    header["CRPIX1"], header["CRPIX2"] = crpix
    header["CRVAL1"], header["CRVAL2"] = crval
    header["CD1_1"], header["CD1_2"], header["CD2_1"], header["CD2_2"] = -scale_deg, 0.0, 0.0, scale_deg
    fits.PrimaryHDU(header=header).writeto(path, overwrite=True)
    return path


@pytest.mark.requirement("TC-SKY-020")
@pytest.mark.priority("MVP")
def test_tc_sky_020_result_is_the_image_centre(tmp_path):
    """SKY-020: the reported position is the image centre, even when Skyshot's reference pixel is a corner."""
    frame = backend._Frame(width=1000, height=800)
    centred = backend.result_from_wcs(_wcs_file(tmp_path / "a.wcs", crpix=(500.5, 400.5)), frame)
    assert centred.success and centred.ra_deg == pytest.approx(150.0, abs=1e-6)
    assert centred.dec_deg == pytest.approx(30.0, abs=1e-6)
    assert centred.scale_arcsec_px == pytest.approx(1.8, rel=1e-3) and centred.cd is not None
    corner = backend.result_from_wcs(_wcs_file(tmp_path / "b.wcs", crpix=(1.0, 1.0)), frame)
    assert corner.success and (corner.ra_deg, corner.dec_deg) != pytest.approx((150.0, 30.0), abs=1e-3)
    assert corner.cd is None                                  # not referenced to the centre, so not offered


class _FakeProc:
    def __init__(self, code: int, stdout: bytes = b"", stderr: bytes = b"", write: Path | None = None):
        self.returncode, self._out, self._err, self._write = code, stdout, stderr, write

    async def communicate(self):
        if self._write is not None:
            self._write.write_bytes(b"")
        return self._out, self._err

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _backend(tmp_path: Path) -> backend.SkyshotBackend:
    """A backend whose program and index are in the plugin's fixed places under *tmp_path*."""
    exe = tmp_path / "program" / backend._EXE_NAMES[0]
    exe.parent.mkdir()
    exe.write_text("")
    index = tmp_path / data.RELEASE_ID / "index"
    index.mkdir(parents=True)
    (index / "bundle.json").write_text("{}")
    return backend.SkyshotBackend(SkyshotSettings(_Store(mode="deep", deadline_s=12), root=tmp_path))


@pytest.mark.requirement("TC-SKY-010")
@pytest.mark.priority("MVP")
async def test_tc_sky_010_solve_runs_the_program_and_reads_the_wcs(tmp_path, monkeypatch):
    """SKY-010: a solve runs ``skyshot solve`` with the index, a fresh WCS path, the limit and the mode."""
    import numpy as np
    from astropy.io import fits
    from galileo.platesolve import SolverParams
    frame = tmp_path / "f.fits"
    fits.PrimaryHDU(np.zeros((800, 1000), dtype=np.uint16)).writeto(frame)
    seen: list[list[str]] = []

    async def fake_start(cmd):
        seen.append(cmd)
        wcs = Path(cmd[cmd.index("--wcs") + 1])
        _wcs_file(wcs.with_name("tmp.wcs"), crpix=(500.5, 400.5))
        proc = _FakeProc(0)

        async def communicate():
            wcs.write_bytes(wcs.with_name("tmp.wcs").read_bytes())
            return b"{}", b""
        proc.communicate = communicate
        return proc

    monkeypatch.setattr("galileo.platesolve_solvers._start_process", fake_start)
    result = await _backend(tmp_path).solve(frame, (10.0, 20.0), SolverParams())
    assert result.success and result.ra_deg == pytest.approx(150.0, abs=1e-6)
    cmd = seen[0]
    assert cmd[1] == "solve" and cmd[cmd.index("--deadline-ms") + 1] == "12000" and cmd[cmd.index("--mode") + 1] == "deep"
    assert "--index" in cmd and not Path(cmd[cmd.index("--wcs") + 1]).exists()      # temp folder removed, hint unused


@pytest.mark.requirement("TC-SKY-010")
@pytest.mark.priority("MVP")
async def test_tc_sky_010_failures_give_a_reason(tmp_path, monkeypatch):
    """SKY-010: exit 1 reads as 'no solution' (with Skyshot's status), exit 2 shows its error text."""
    from galileo.platesolve import SolverParams
    outcomes = iter([_FakeProc(1, stdout=b'{"solution": {"status": "ambiguous"}}'), _FakeProc(2, stderr=b"bad index")])

    async def fake_start(cmd):
        return next(outcomes)

    monkeypatch.setattr("galileo.platesolve_solvers._start_process", fake_start)
    solver = _backend(tmp_path)
    unsolved = await solver.solve(tmp_path / "f.fits", None, SolverParams())
    assert not unsolved.success and "No solution found" in unsolved.failure_reason and "ambiguous" in unsolved.failure_reason
    errored = await solver.solve(tmp_path / "f.fits", None, SolverParams())
    assert not errored.success and errored.failure_reason == "bad index"


@pytest.mark.requirement("TC-SKY-070")
@pytest.mark.priority("P2")
def test_tc_sky_070_settings_are_clamped_and_readiness_needs_program_and_index(tmp_path, monkeypatch):
    """SKY-070: bad stored settings fall back to safe values, and the solver is ready only with both program and index."""
    settings = SkyshotSettings(_Store(mode="bogus", deadline_s="x"))
    assert settings.mode == "auto" and settings.deadline_s == 30
    settings.deadline_s = 99999
    assert settings.deadline_s == 600
    monkeypatch.setattr(backend, "_EXE_NAMES", ("no-such-skyshot",))                      # nothing on PATH or in cargo's folder
    nothing = backend.SkyshotBackend(SkyshotSettings(_Store(), root=tmp_path / "empty"))
    assert not nothing.is_available() and "skyshot program" in nothing.missing_message()
    if engine.build_for(engine.current_target()) is None:                                  # nothing to download: say where to put one
        assert str(tmp_path / "empty" / "program") in nothing.missing_message()
    else:
        assert "Download Skyshot program" in nothing.missing_message()
    monkeypatch.undo()
    assert _backend(tmp_path).is_available()


@pytest.mark.requirement("TC-SKY-060")
@pytest.mark.priority("P2")
def test_tc_sky_060_options_page_shows_readiness_and_saves(tmp_path, monkeypatch):
    """SKY-060: the Options page says whether Skyshot is ready (program and index found) and saves its settings."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])          # noqa: F841 -- must stay alive for the widgets
    from skyshot_solver.ui import SettingsPage

    store = _Store()
    monkeypatch.setattr(backend, "_EXE_NAMES", ("no-such-skyshot",))                      # nothing on PATH or in cargo's folder
    page = SettingsPage(SkyshotSettings(store, root=tmp_path))
    assert page.status.text().startswith("Not ready") and "not installed" in page.status.text()
    assert str(tmp_path) in page.where.text() and str(tmp_path / "program") in page.where.text()
    for gone in ("exe_edit", "root_edit", "index_edit"):                                    # no path is ever asked for
        assert not hasattr(page, gone)
    program = tmp_path / "program" / "no-such-skyshot"
    program.parent.mkdir()
    program.write_text("")
    (tmp_path / data.RELEASE_ID / "index").mkdir(parents=True)
    (tmp_path / data.RELEASE_ID / "index" / "bundle.json").write_text("{}")
    page.deadline_spin.setValue(45)
    page.save()
    assert store.values["deadline_s"] == 45
    assert page.status.text().startswith("Ready")


# --- prebuilt program (SKY-080, SKY-090) --------------------------------------------------------

import types
import zipfile

from skyshot_solver import engine


def _engine_archive(target: str, extra: dict[str, bytes] | None = None, root: str | None = None) -> tuple[str, bytes]:
    """An archive laid out like upstream's packager makes: one root folder holding the program,
    licences and a third_party folder; .zip for Windows, .tar.gz otherwise."""
    exe = engine.exe_name(target)
    root = root or f"skyshot-0.1.0-{target}"
    members = {f"{root}/{exe}": b"#!fake", f"{root}/LICENSE": b"Apache", f"{root}/third_party/NOTICE": b"n", **(extra or {})}
    if "windows" in target:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, payload in members.items():
                zf.writestr(name, payload)
        return f"{root}.zip", buf.getvalue()
    return f"{root}.tar.gz", _tar(members)


def _builds(target: str, name: str, payload: bytes, **over) -> types.SimpleNamespace:
    entry = {"archive": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), **over}
    return types.SimpleNamespace(ENGINE_VERSION="0.1.0", UPSTREAM_COMMIT="a" * 40, RELEASE_TAG="t",
                                 RELEASE_BASE_URL="https://github.com/o/r/releases/download/t/", BUILDS={target: entry})


@pytest.mark.requirement("TC-SKY-080")
@pytest.mark.priority("P2")
def test_tc_sky_080_platform_detection_and_pinned_builds():
    """SKY-080: each supported computer maps to its build target, and a build is offered only when pinned."""
    assert engine.current_target("win32", "AMD64") == "x86_64-pc-windows-msvc"
    assert engine.current_target("linux", "x86_64") == "x86_64-unknown-linux-gnu"
    assert engine.current_target("linux", "aarch64") == "aarch64-unknown-linux-gnu"     # Raspberry Pi 5
    assert engine.current_target("darwin", "arm64") == "aarch64-apple-darwin"
    assert engine.current_target("darwin", "x86_64") is None and engine.current_target("freebsd", "amd64") is None
    name, payload = _engine_archive("x86_64-unknown-linux-gnu")
    pinned = _builds("x86_64-unknown-linux-gnu", name, payload)
    assert engine.build_for("x86_64-unknown-linux-gnu", pinned)["sha256"] == hashlib.sha256(payload).hexdigest()
    assert engine.build_for("aarch64-apple-darwin", pinned) is None
    pinned.RELEASE_BASE_URL = "https://evil.example/"                                    # only GitHub releases are trusted
    assert engine.build_for("x86_64-unknown-linux-gnu", pinned) is None


@pytest.mark.requirement("TC-SKY-080")
@pytest.mark.priority("P2")
@pytest.mark.parametrize("target", ["x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"])
def test_tc_sky_080_download_verifies_against_the_pinned_hash_and_installs(tmp_path, serve, target):
    """SKY-080: the program is installed only from an archive matching the SHA-256 pinned in the plugin
    (zip on Windows, tar.gz elsewhere); the licences travel with it; an installed program is reused."""
    name, payload = _engine_archive(target)
    builds = _builds(target, name, payload)
    serve["payload"] = payload
    exe = engine.download_engine(tmp_path, target=target, builds=builds, verify=None)
    assert exe.name == engine.exe_name(target) and exe.read_bytes() == b"#!fake"
    assert (exe.parent / "LICENSE").is_file() and (exe.parent / "third_party" / "NOTICE").is_file()
    assert engine.installed_executable(tmp_path, target, builds) == exe
    serve["payload"] = b"must not be fetched again"
    assert engine.download_engine(tmp_path, target=target, builds=builds, verify=None) == exe


@pytest.mark.requirement("TC-SKY-080")
@pytest.mark.priority("P2")
def test_tc_sky_080_tampered_download_is_discarded(tmp_path, serve):
    """SKY-080: an archive that does not match the pinned SHA-256 is discarded and nothing is installed,
    even if the release page and the file agree with each other."""
    target = "x86_64-unknown-linux-gnu"
    name, payload = _engine_archive(target)
    builds = _builds(target, name, payload)
    serve["payload"] = payload[:-1] + bytes([payload[-1] ^ 1])
    with pytest.raises(data.DataError, match="checksum"):
        engine.download_engine(tmp_path, target=target, builds=builds, verify=None)
    assert engine.installed_executable(tmp_path, target, builds) is None
    with pytest.raises(data.DataError, match="No prebuilt"):
        engine.download_engine(tmp_path, target="aarch64-apple-darwin", builds=builds, verify=None)


@pytest.mark.requirement("TC-SKY-090")
@pytest.mark.priority("P2")
@pytest.mark.parametrize("bad", [
    {"skyshot-0.1.0-x86_64-unknown-linux-gnu/../../escape": b"x"},
    {"/abs/path": b"x"},
    {"other-root/skyshot": b"x"},
])
def test_tc_sky_090_unsafe_archive_is_refused(tmp_path, serve, bad):
    """SKY-090: an archive with a path that leaves its folder, an absolute path or a second top-level folder is refused."""
    target = "x86_64-unknown-linux-gnu"
    name, payload = _engine_archive(target, extra=bad)
    builds = _builds(target, name, payload)
    serve["payload"] = payload
    with pytest.raises(data.DataError):
        engine.download_engine(tmp_path, target=target, builds=builds, verify=None)
    assert not (tmp_path / "escape").exists() and engine.installed_executable(tmp_path, target, builds) is None


@pytest.mark.requirement("TC-SKY-090")
@pytest.mark.priority("P2")
def test_tc_sky_090_links_are_refused_and_the_program_must_run(tmp_path, serve):
    """SKY-090: a symbolic link in the archive is refused, and a program that will not start is not kept."""
    target = "x86_64-unknown-linux-gnu"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        link = tarfile.TarInfo(f"skyshot-0.1.0-{target}/skyshot")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
    payload = buf.getvalue()
    builds = _builds(target, f"skyshot-0.1.0-{target}.tar.gz", payload)
    serve["payload"] = payload
    with pytest.raises(data.DataError, match="link"):
        engine.download_engine(tmp_path, target=target, builds=builds, verify=None)

    name, good = _engine_archive(target)
    builds = _builds(target, name, good)
    serve["payload"] = good
    with pytest.raises(data.DataError, match="will not run|did not start"):
        engine.download_engine(tmp_path / "second", target=target, builds=builds)           # the fake file cannot run
    assert engine.installed_executable(tmp_path / "second", target, builds) is None


@pytest.mark.requirement("TC-SKY-090")
@pytest.mark.priority("P2")
def test_tc_sky_090_lookup_order_is_program_folder_then_downloaded_then_path(tmp_path, monkeypatch):
    """SKY-090: the program is looked for in fixed places: the plugin's program folder, then the downloaded
    program, then PATH; nothing is ever configured by path."""
    target = engine.current_target()
    if target is None:
        pytest.skip("no build target on this platform")
    builds = types.SimpleNamespace(ENGINE_VERSION="0.1.0", UPSTREAM_COMMIT="b" * 40, RELEASE_BASE_URL="", BUILDS={})
    monkeypatch.setattr(engine, "engine_builds", builds)
    monkeypatch.setattr(engine, "installed_executable",
                        lambda root, target=None, builds=builds: next(iter(sorted(engine.engine_root(root, builds).glob("*/*"))), None))
    monkeypatch.setattr(backend, "_EXE_NAMES", ("no-such-skyshot",))
    assert backend.find_executable(tmp_path) == ""
    downloaded = engine.engine_root(tmp_path, builds) / "skyshot-0.1.0-x" / "no-such-skyshot"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_text("")
    assert backend.find_executable(tmp_path) == str(downloaded)
    placed = tmp_path / "program" / "no-such-skyshot"
    placed.parent.mkdir()
    placed.write_text("")
    assert backend.find_executable(tmp_path) == str(placed)                                  # a program put there by hand wins


@pytest.mark.requirement("TC-SKY-010")
@pytest.mark.priority("P2")
@pytest.mark.skipif("SKYSHOT_REAL_DIR" not in __import__("os").environ,
                    reason="set SKYSHOT_REAL_DIR to a built skyshot-solver checkout with its synthetic demo to run")
async def test_tc_sky_010_real_binary_solves_the_synthetic_demo():
    """SKY-010/SKY-020 against a real Skyshot build: a FITS frame rendered from the demo star list solves to the
    demo's known position and scale. Opt-in; needs ``cargo build --release`` and the ``synthetic_demo`` example."""
    import os

    import numpy as np
    from astropy.io import fits
    from galileo.platesolve import SolverParams
    src = Path(os.environ["SKYSHOT_REAL_DIR"])
    exe = src / "target" / "release" / ("skyshot.exe" if os.name == "nt" else "skyshot")
    engine.verify_runs(exe)
    star_list = json.loads((src / "demo" / "stars.json").read_text())
    w, h = star_list["width"], star_list["height"]
    img = np.random.default_rng(1).normal(100, 3, (h, w)).astype(np.float32)
    for s in star_list["stars"]:
        x0, y0 = int(s["x"]) - 6, int(s["y"]) - 6
        if x0 < 0 or y0 < 0 or x0 + 13 > w or y0 + 13 > h:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x0 + 13), np.arange(y0, y0 + 13))
        img[y0:y0 + 13, x0:x0 + 13] += 4000 * np.exp(-((gx - s["x"]) ** 2 + (gy - s["y"]) ** 2) / (2 * 1.3 ** 2))
    frame = src / "demo" / "frame.fits"
    fits.PrimaryHDU(img).writeto(frame, overwrite=True)
    import shutil
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="skyshot_real_"))
    (root / "program").mkdir()
    shutil.copy2(exe, root / "program" / exe.name)
    shutil.copytree(src / "demo" / "index", root / "index")
    solver = backend.SkyshotBackend(SkyshotSettings(_Store(), root=root))
    result = await solver.solve(frame, None, SolverParams())
    assert result.success, result.failure_reason
    assert result.ra_deg == pytest.approx(83.8, abs=0.005) and result.dec_deg == pytest.approx(-5.4, abs=0.005)
    assert result.scale_arcsec_px == pytest.approx(3.0, rel=0.01)


# --- credits and licences (SKY-110) -------------------------------------------------------------

from skyshot_solver import credits


@pytest.mark.requirement("TC-SKY-110")
@pytest.mark.priority("P2")
def test_tc_sky_110_index_licence_travels_with_the_index_and_is_required(tmp_path, serve):
    """SKY-110: an installed index keeps its LICENSE-DATA.txt, and an archive without one is not installed."""
    archive, bundle_json = _good_archive()
    release = data.parse_release(_release_text(archive, bundle_json))
    serve["payload"] = archive
    data.install_preset(release, "full", tmp_path)
    assert (data.index_path(tmp_path, "test") / "LICENSE-DATA.txt").read_bytes() == b"data terms"

    bare = _tar({f"{BUNDLE}/bundle.json": bundle_json, f"{BUNDLE}/index.bin": b"stars"})
    release = data.parse_release(_release_text(bare, bundle_json))
    serve["payload"] = bare
    with pytest.raises(data.DataError, match="LICENSE-DATA"):
        data.install_preset(release, "full", tmp_path / "other")
    assert data.index_path(tmp_path / "other", "test") is None


@pytest.mark.requirement("TC-SKY-110")
@pytest.mark.priority("P2")
def test_tc_sky_110_credits_name_the_author_licences_and_data_sources(tmp_path):
    """SKY-110: the Options page credits Skyshot's author and licence, says the plugin is not endorsed, and names
    the data's separate terms and its catalogue sources."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])          # noqa: F841 -- must stay alive for the widgets
    from skyshot_solver.ui import SettingsPage

    page = SettingsPage(SkyshotSettings(_Store(), root=tmp_path))
    shown = page.credit.text()
    for needle in ("Adam Jesionkiewicz", "Apache License 2.0", "not endorsed", "non-commercial", "ASTAP", "Gaia DR3",
                   credits.SKYSHOT_URL):
        assert needle in shown, needle
    assert page.credit.openExternalLinks()
    assert "Adam Jesionkiewicz" in credits.DOWNLOAD_PROGRAM_NOTICE and "non-commercial" in credits.DOWNLOAD_INDEX_NOTICE
    help_text = (Path(__file__).parent.parent / "skyshot_solver" / "help" / "plugin.SkyshotSolverPlugin.md").read_text(encoding="utf-8")
    assert "## Credits and licences" in help_text and "Adam Jesionkiewicz" in help_text


@pytest.mark.requirement("TC-SKY-110")
@pytest.mark.priority("P2")
def test_tc_sky_110_program_archive_keeps_licence_and_notices(tmp_path, serve):
    """SKY-110: the licence and notice files shipped in the program archive are installed next to the program."""
    target = "x86_64-unknown-linux-gnu"
    name, payload = _engine_archive(target, extra={f"skyshot-0.1.0-{target}/NOTICE": b"Copyright 2026 Adam Jesionkiewicz"})
    serve["payload"] = payload
    exe = engine.download_engine(tmp_path, target=target, builds=_builds(target, name, payload), verify=None)
    assert (exe.parent / "LICENSE").is_file() and (exe.parent / "third_party").is_dir()
    assert b"Adam Jesionkiewicz" in (exe.parent / "NOTICE").read_bytes()


# --- flaky TLS handshakes (SKY-050) -------------------------------------------------------------

import ssl
import urllib.error


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(data, "_sleep", lambda _s: None)


@pytest.mark.requirement("TC-SKY-050")
@pytest.mark.priority("P2")
def test_tc_sky_050_transient_tls_failure_is_retried(tmp_path, monkeypatch, no_sleep):
    """SKY-050: a TLS handshake that fails on the first attempts ("unable to get issuer certificate") and then
    succeeds does not fail the download; certificate checking stays on."""
    archive, bundle_json = _good_archive()
    release = data.parse_release(_release_text(archive, bundle_json))
    calls = []

    contexts = []

    def flaky(req, timeout=None, data=None, context=None):
        calls.append(req.full_url)
        contexts.append(context)
        if len(calls) < 3:
            raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get issuer certificate"))
        return _Resp(archive)

    monkeypatch.setattr("galileo.net.urlopen_http", flaky)
    assert data.install_preset(release, "full", tmp_path) == ["index"]
    assert len(calls) == 3
    # After the first certificate failure the retries verify against certifi's bundle (still verifying).
    assert contexts[0] is None and all(c is not None for c in contexts[1:])
    assert all(c.verify_mode == ssl.CERT_REQUIRED for c in contexts[1:])


@pytest.mark.requirement("TC-SKY-050")
@pytest.mark.priority("P2")
def test_tc_sky_050_retries_are_bounded_and_server_errors_are_not_retried(tmp_path, monkeypatch, no_sleep):
    """SKY-050: a connection that keeps failing gives up with a clear message after a few attempts, and an HTTP
    error from the server is reported at once."""
    archive, bundle_json = _good_archive()
    release = data.parse_release(_release_text(archive, bundle_json))
    attempts = []

    def always_tls(req, timeout=None, data=None, context=None):
        attempts.append(1)
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get issuer certificate"))

    monkeypatch.setattr("galileo.net.urlopen_http", always_tls)
    with pytest.raises(data.DataError, match="issuer certificate"):
        data.install_preset(release, "full", tmp_path)
    assert len(attempts) == data._OPEN_ATTEMPTS

    attempts.clear()

    def not_found(req, timeout=None, data=None):
        attempts.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr("galileo.net.urlopen_http", not_found)
    with pytest.raises(data.DataError, match="404"):
        data.install_preset(release, "full", tmp_path / "b")
    assert len(attempts) == 1


# --- fixed locations (SKY-120) ------------------------------------------------------------------


@pytest.mark.requirement("TC-SKY-120")
@pytest.mark.priority("P2")
def test_tc_sky_120_everything_lives_in_one_fixed_folder_inside_galileos_data_folder(tmp_path, monkeypatch):
    """SKY-120: the program and indexes are kept under a fixed folder inside Galileo's data folder, there is
    no stored path setting, and a program placed in the program folder is found without being told."""
    monkeypatch.setattr("galileo.platform.get_data_dir", lambda: tmp_path)
    store = _Store(executable="C:/elsewhere/skyshot.exe", data_root="D:/elsewhere", index_path="E:/elsewhere")
    settings = SkyshotSettings(store)                                  # older versions stored paths; they are ignored now
    assert settings.data_root == tmp_path / "skyshot"
    assert settings.program_dir == tmp_path / "skyshot" / "program"
    assert engine.engine_root(settings.data_root).parent == tmp_path / "skyshot" / "engine"
    assert data.install_dir(settings.data_root) == tmp_path / "skyshot" / data.RELEASE_ID
    assert settings.resolved_index() is None

    monkeypatch.setattr(backend, "_EXE_NAMES", ("no-such-skyshot",))
    assert backend.find_executable(settings.data_root) == ""
    placed = settings.program_dir / "no-such-skyshot"
    placed.parent.mkdir(parents=True)
    placed.write_text("")
    assert backend.find_executable(settings.data_root) == str(placed)
    index = data.install_dir(settings.data_root) / "index"
    index.mkdir(parents=True)
    (index / "bundle.json").write_text("{}")
    assert settings.resolved_index() == index
    assert backend.SkyshotBackend(settings).is_available()
