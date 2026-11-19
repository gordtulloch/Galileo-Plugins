# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Download and install the prebuilt Skyshot program (SKY-080, SKY-090).

The program is built by CI from one pinned upstream commit for each supported platform and published
as a release; ``engine_builds.BUILDS`` pins each archive's SHA-256 *inside this plugin*, so a
tampered release page cannot make the plugin run a different program. An archive is unpacked only if
every member is a plain file or folder under one root folder, and the result must run (``--version``)
before it is kept. Nothing here touches Qt.

The program is Skyshot Solver (Copyright 2026 Adam Jesionkiewicz, Apache License 2.0). It is downloaded
unmodified, with its LICENSE, NOTICE and third-party notices, and run as a separate program.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from types import ModuleType

from skyshot_solver import data, engine_builds

logger = logging.getLogger(__name__)

_MAX_FILES = 5000
_MAX_UNPACKED_BYTES = 600_000_000
_CHUNK = 1 << 20
# Release assets are only ever fetched from GitHub releases over https.
_ALLOWED_URL_PREFIX = "https://github.com/"

_TARGETS = {
    ("win32", "amd64"): "x86_64-pc-windows-msvc",
    ("win32", "x86_64"): "x86_64-pc-windows-msvc",
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
    ("linux", "arm64"): "aarch64-unknown-linux-gnu",
    ("darwin", "arm64"): "aarch64-apple-darwin",
}


def current_target(plat: str | None = None, machine: str | None = None) -> str | None:
    """The build target of this computer, or ``None`` for a platform no build is made for."""
    return _TARGETS.get(((plat or sys.platform), (machine or platform.machine()).lower()))


def exe_name(target: str) -> str:
    return "skyshot.exe" if "windows" in target else "skyshot"


def build_for(target: str | None, builds: ModuleType = engine_builds) -> dict | None:
    """The pinned build for *target*, or ``None`` when none has been published."""
    if not target or not builds.RELEASE_BASE_URL.startswith(_ALLOWED_URL_PREFIX):
        return None
    entry = builds.BUILDS.get(target)
    if not entry:
        return None
    try:
        return {"archive": str(entry["archive"]), "bytes": int(entry["bytes"]), "sha256": str(entry["sha256"])}
    except (KeyError, TypeError, ValueError):
        return None


def engine_root(data_root: Path, builds: ModuleType = engine_builds) -> Path:
    """Where this plugin version's program lives: one folder per upstream commit."""
    return data_root / "engine" / f"{builds.ENGINE_VERSION}-{builds.UPSTREAM_COMMIT[:7] or 'unpinned'}"


def installed_executable(data_root: Path, target: str | None = None, builds: ModuleType = engine_builds) -> Path | None:
    """The downloaded program, if one is installed for this platform."""
    target = target or current_target()
    if not target:
        return None
    root = engine_root(data_root, builds)
    if not root.is_dir():
        return None
    for candidate in root.glob(f"*/{exe_name(target)}"):
        if candidate.is_file():
            return candidate
    return None


def _safe_member(name: str) -> PurePosixPath:
    """A member's path relative to the archive, or :class:`data.DataError` for anything that could
    escape the folder it is unpacked into."""
    if "\\" in name or ":" in name or name.startswith("/"):
        raise data.DataError(f"The program archive has an unsafe path ({name!r}); not installed.")
    path = PurePosixPath(name)
    if any(part in ("", ".", "..") for part in path.parts) or path.is_absolute():
        raise data.DataError(f"The program archive has an unsafe path ({name!r}); not installed.")
    return path


def unpack_engine(archive: Path, root_name: str, stage: Path) -> Path:
    """Unpack *archive* (``.zip`` or ``.tar.gz``) into *stage*, refusing links, devices, paths that
    leave the folder, a second top-level folder, and absurd sizes. Returns the unpacked root folder."""
    files: list[tuple[PurePosixPath, Callable[[], object], int]] = []
    total = 0
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise data.DataError("The program archive contains a link; not installed.")
                    rel = _safe_member(info.filename.rstrip("/"))
                    if rel.parts[0] != root_name:
                        raise data.DataError(f"The program archive has an unexpected layout ({info.filename!r}).")
                    if not info.is_dir():
                        files.append((rel, lambda i=info: zf.open(i), info.file_size))
                        total += info.file_size
                _write_members(files, total, stage)
        else:
            with tarfile.open(archive) as tf:
                for member in tf.getmembers():
                    if member.isdir():
                        _safe_member(member.name)
                        continue
                    if not member.isreg():
                        raise data.DataError("The program archive contains a link or special file; not installed.")
                    rel = _safe_member(member.name)
                    if rel.parts[0] != root_name:
                        raise data.DataError(f"The program archive has an unexpected layout ({member.name!r}).")
                    files.append((rel, lambda m=member: tf.extractfile(m), member.size))
                    total += member.size
                _write_members(files, total, stage)
    except (tarfile.TarError, zipfile.BadZipFile) as exc:
        raise data.DataError(f"The program archive is not readable: {exc}") from exc
    root = stage / root_name
    if not root.is_dir():
        raise data.DataError("The program archive is empty.")
    return root


def _write_members(files, total: int, stage: Path) -> None:
    if len(files) > _MAX_FILES or total > _MAX_UNPACKED_BYTES:
        raise data.DataError("The program archive is larger than expected; not installed.")
    seen: set[PurePosixPath] = set()
    for rel, opener, _size in files:
        if rel in seen:
            raise data.DataError(f"The program archive lists {rel} twice; not installed.")
        seen.add(rel)
        dest = stage.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = opener()
        if src is None:
            raise data.DataError(f"The program archive's {rel} cannot be read.")
        with src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out, _CHUNK)


def _free_bytes(path: Path) -> int:
    """Free space on the disk *path* is (or will be) on: that of its nearest existing parent."""
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def verify_runs(exe: Path) -> None:
    """The unpacked program must start and print its version, which also catches a build for the
    wrong CPU or a missing system library."""
    try:
        proc = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise data.DataError(f"The downloaded Skyshot program will not run on this computer: {exc}") from exc
    if proc.returncode != 0 or "skyshot" not in proc.stdout.lower():
        raise data.DataError("The downloaded Skyshot program did not start correctly: "
                             + (proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}")[-200:])


def download_engine(
    data_root: Path, progress: data.Progress | None = None, cancel: threading.Event | None = None,
    target: str | None = None, builds: ModuleType = engine_builds,
    verify: Callable[[Path], None] | None = verify_runs,
) -> Path:
    """Download, check and install the program for this platform; returns the executable. An
    installed program is reused and never overwritten."""
    target = target or current_target()
    build = build_for(target, builds)
    if target is None or build is None:
        raise data.DataError("No prebuilt Skyshot program is published for this computer. Build it from "
                             "github.com/ashtree74/skyshot-solver and set its path instead.")
    existing = installed_executable(data_root, target, builds)
    if existing is not None:
        return existing
    note = progress or (lambda *_: None)
    cache = data_root / "download-cache" / "engine"
    free = _free_bytes(data_root)
    if free < build["bytes"] * 4:
        raise data.DataError(f"Not enough free disk space: about {build['bytes'] * 4 / 1e6:.0f} MB needed.")
    archive = data.download_file(builds.RELEASE_BASE_URL + build["archive"], cache, build["archive"],
                                 build["bytes"], build["sha256"], progress, cancel)
    note(build["bytes"], build["bytes"], f"Unpacking {build['archive']}")
    root_name = build["archive"].removesuffix(".tar.gz").removesuffix(".zip")
    final_parent = engine_root(data_root, builds)
    final_parent.mkdir(parents=True, exist_ok=True)
    stage = final_parent / f".unpack-{root_name}"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        stage.mkdir()
        unpacked = unpack_engine(archive, root_name, stage)
        exe = unpacked / exe_name(target)
        if not exe.is_file():
            raise data.DataError("The program archive has no skyshot executable; not installed.")
        if os.name == "posix":
            exe.chmod(0o755)
        if verify is not None:
            verify(exe)
        destination = final_parent / root_name
        shutil.rmtree(destination, ignore_errors=True)
        unpacked.replace(destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    archive.unlink(missing_ok=True)
    return destination / exe_name(target)
