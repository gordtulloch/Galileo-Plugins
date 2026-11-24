# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Download and install Skyshot's star-pattern indexes (SKY-030 … SKY-060).

Skyshot publishes its indexes as a release: a ``release.json`` manifest naming each archive (size and
SHA-256) and the *presets* that combine them (``full``, ``full-deep``, ``complete``, …). This module
follows the same rules as upstream's ``tools/install_data.py``:

* every archive is checked against the manifest's size and SHA-256 before anything is unpacked;
* an archive must hold one flat folder of regular files (no links, no ``..``, no extra levels);
* an installed index is never overwritten or merged into — a slot that already exists is left alone.

Nothing here touches Qt, so it is tested without a display.

Attribution: the install rules above follow those of Skyshot's ``tools/install_data.py`` and
``tools/fetch_data.py`` (Copyright 2026 Adam Jesionkiewicz, Apache License 2.0,
https://github.com/ashtree74/skyshot-solver). This is an independent reimplementation in Galileo's
own structure; no code from those files is included. See Galileo's THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import ssl
import tarfile
import threading
import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# The release this plugin version was written against. Newer data needs a plugin update, because the
# index format is tied to the engine version (``ENGINE_VERSION``).
RELEASE_ID = "2026.10.17.2"
ENGINE_VERSION = "0.1.0"
RELEASE_URL = (
    "https://raw.githubusercontent.com/ashtree74/skyshot-solver/main/provenance/data-releases/"
    f"{RELEASE_ID}/release.json"
)

# Presets offered on the Options page, and what each one adds. Slot names come from the manifest.
PRESET_FULL = "full"
PRESET_DEEP = "full-deep"

_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_CHUNK = 1 << 20
_TIMEOUT_S = 60

# (bytes done, bytes total, what is happening) — called from the worker thread.
Progress = Callable[[int, int, str], None]


class DataError(Exception):
    """A download or install failed; the message is shown to the user as is."""


class Cancelled(DataError):
    """The user cancelled."""


@dataclass(frozen=True)
class Artifact:
    bundle_id: str
    archive: str
    size: int
    sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class Release:
    release_id: str
    base_url: str
    engine_versions: tuple[str, ...]
    presets: dict[str, dict[str, str]]          # preset -> {slot: bundle_id}
    artifacts: dict[str, Artifact]              # bundle_id -> artifact

    def plan(self, preset: str) -> list[tuple[str, Artifact]]:
        """The ``(slot, artifact)`` pairs *preset* installs."""
        if preset not in self.presets:
            raise DataError(f"This release has no {preset!r} preset.")
        out = []
        for slot, bundle_id in self.presets[preset].items():
            if not _NAME.match(slot) or bundle_id not in self.artifacts:
                raise DataError(f"The release manifest is inconsistent for {slot!r}.")
            out.append((slot, self.artifacts[bundle_id]))
        return out


def parse_release(text: str) -> Release:
    """Validate and read a ``release.json``. Raises :class:`DataError` on anything unexpected."""
    try:
        raw = json.loads(text)
        if raw.get("schema_version") != 1:
            raise DataError("The release manifest has a schema this plugin does not know.")
        base_url = str(raw["base_url"])
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or not base_url.endswith("/"):
            raise DataError("The release manifest's download address is not a secure (https) folder.")
        engines = tuple(str(v) for v in raw["engine_versions"])
        artifacts: dict[str, Artifact] = {}
        for item in raw["artifacts"]:
            artifact = Artifact(
                bundle_id=str(item["bundle_id"]), archive=str(item["archive"]), size=int(item["bytes"]),
                sha256=str(item["sha256"]), manifest_sha256=str(item.get("manifest_sha256") or ""),
            )
            if (not _NAME.match(artifact.bundle_id) or not _NAME.match(artifact.archive)
                    or artifact.size <= 0 or not _SHA256.match(artifact.sha256)):
                raise DataError(f"The release manifest entry {artifact.bundle_id!r} is malformed.")
            if artifact.bundle_id in artifacts:
                raise DataError(f"The release manifest lists {artifact.bundle_id!r} twice.")
            artifacts[artifact.bundle_id] = artifact
        presets = {str(name): {str(s): str(b) for s, b in slots.items()} for name, slots in raw["presets"].items()}
        return Release(str(raw["release_id"]), base_url, engines, presets, artifacts)
    except DataError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DataError(f"The release manifest could not be read: {exc}") from exc


def fetch_release(url: str = RELEASE_URL) -> Release:
    """Download and validate the release manifest."""
    release = parse_release(_get_text(url))
    if ENGINE_VERSION not in release.engine_versions:
        raise DataError(f"This data is not for Skyshot {ENGINE_VERSION}; update the Skyshot plugin.")
    return release


_OPEN_ATTEMPTS = 4
_sleep = time.sleep                                      # replaced in tests


def _open(url: str, headers: dict[str, str]):
    """Open *url* (an https response to use as a context manager), trying a few times when the
    connection or its TLS handshake fails. Needed because GitHub's download hosts sometimes present a
    certificate chain Python on Windows cannot complete ("unable to get issuer certificate") on one
    attempt and accept it on the next. A server error (4xx/5xx) is not retried, and nothing here
    skips certificate verification."""
    from galileo.net import http_request, urlopen_http
    last: Exception | None = None
    for attempt in range(_OPEN_ATTEMPTS):
        try:
            return urlopen_http(http_request(url, headers=headers), timeout=_TIMEOUT_S)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ssl.SSLError, ConnectionError, TimeoutError) as exc:
            last = exc
            logger.info("Opening %s failed (attempt %d of %d): %s", url, attempt + 1, _OPEN_ATTEMPTS, exc)
            if attempt + 1 < _OPEN_ATTEMPTS:
                _sleep(1.5 * (attempt + 1))
    assert last is not None
    raise last


def _get_text(url: str) -> str:
    try:
        with _open(url, {"User-Agent": "Galileo"}) as resp:
            return resp.read(2_000_000).decode("utf-8")
    except (OSError, ValueError) as exc:
        raise DataError(f"Could not fetch {url}: {exc}") from exc


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download_artifact(
    release: Release, artifact: Artifact, cache: Path, progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Fetch *artifact*'s archive into *cache* and return its path once its size and SHA-256 match
    the manifest (see :func:`download_file`)."""
    return download_file(release.base_url + artifact.archive, cache, artifact.archive,
                         artifact.size, artifact.sha256, progress, cancel)


def download_file(
    url: str, cache: Path, name: str, size: int, sha256: str, progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Fetch *url* into ``cache/name`` (resuming an interrupted download) and return its path once its
    size and SHA-256 match. A cached file that matches is reused; one that does not is discarded.
    Redirects must stay on https."""
    cache.mkdir(parents=True, exist_ok=True)
    final = cache / name
    note = progress or (lambda *_: None)
    if final.is_file():
        if final.stat().st_size == size and sha256_of(final) == sha256:
            note(size, size, f"{name}: already downloaded")
            return final
        final.unlink()                                  # corrupt or partial under the final name
    part = cache / (name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if have > size:
        part.unlink()
        have = 0

    headers = {"User-Agent": "Galileo", "Accept-Encoding": "identity"}
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        with _open(url, headers) as resp:
            if urlparse(resp.geturl()).scheme != "https":
                raise DataError("The server redirected the download to an insecure address.")
            if have and getattr(resp, "status", 200) != 206:
                have = 0                                # the server ignored the range; start over
            with open(part, "ab" if have else "wb") as out:
                done = have
                while chunk := resp.read(_CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("Download cancelled.")
                    out.write(chunk)
                    done += len(chunk)
                    if done > size:
                        raise DataError(f"{name} is larger than expected.")
                    note(done, size, f"Downloading {name}")
    except Cancelled:
        raise                                           # keep the .part so a retry resumes
    except (OSError, ValueError) as exc:
        raise DataError(f"Download of {name} failed: {exc}") from exc

    note(size, size, f"Checking {name}")
    if part.stat().st_size != size or sha256_of(part) != sha256:
        part.unlink(missing_ok=True)
        raise DataError(f"{name} did not match its published checksum; it was discarded. Try again.")
    part.replace(final)
    return final


def unpack_archive(archive: Path, artifact: Artifact, stage: Path) -> Path:
    """Unpack *archive* into *stage* and return the folder holding its files. Only a flat folder
    ``<bundle_id>/<file>`` of regular files is accepted; anything else is refused unread."""
    root = stage / artifact.bundle_id
    seen: set[str] = set()
    try:
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            for member in members:
                parts = member.name.split("/")
                if (not member.isreg() or len(parts) != 2 or parts[0] != artifact.bundle_id
                        or parts[1] in ("", ".", "..") or "\\" in member.name or member.name in seen):
                    raise DataError(f"{archive.name} has an unexpected layout ({member.name!r}); not installed.")
                seen.add(member.name)
            root.mkdir(parents=True)
            for member in members:
                src = tar.extractfile(member)
                if src is None:
                    raise DataError(f"{archive.name}: cannot read {member.name!r}.")
                with src, open(root / member.name.split("/")[1], "wb") as out:
                    shutil.copyfileobj(src, out, _CHUNK)
    except tarfile.TarError as exc:
        raise DataError(f"{archive.name} is not a readable archive: {exc}") from exc
    return root


def _check_bundle(root: Path, artifact: Artifact) -> None:
    """The unpacked folder's ``bundle.json`` must be the one the manifest describes."""
    bundle = root / "bundle.json"
    if not bundle.is_file():
        raise DataError(f"{artifact.archive} has no bundle.json; not installed.")
    if not (root / "LICENSE-DATA.txt").is_file():               # the data's own terms must travel with it
        raise DataError(f"{artifact.archive} has no LICENSE-DATA.txt; not installed.")
    if artifact.manifest_sha256 and sha256_of(bundle) != artifact.manifest_sha256:
        raise DataError(f"{artifact.archive}'s bundle.json does not match the release manifest; not installed.")
    try:
        meta = json.loads(bundle.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataError(f"{artifact.archive}'s bundle.json is unreadable: {exc}") from exc
    if isinstance(meta, dict) and meta.get("bundle_id", artifact.bundle_id) != artifact.bundle_id:
        raise DataError(f"{artifact.archive} holds a different bundle than the manifest names; not installed.")


def install_dir(root: Path, release_id: str = RELEASE_ID) -> Path:
    """Where this release's indexes live under the data folder *root*."""
    return root / release_id


def installed_slots(root: Path, release_id: str = RELEASE_ID) -> list[str]:
    base = install_dir(root, release_id)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))


def index_path(root: Path, release_id: str = RELEASE_ID) -> Path | None:
    """The folder to pass as ``--index``: the installed ``index`` slot, if there is one."""
    candidate = install_dir(root, release_id) / "index"
    return candidate if candidate.is_dir() else None


def find_index(folder: Path) -> Path | None:
    """The index folder to pass as ``--index`` given a folder the user named. Accepts the index folder
    itself (it holds ``bundle.json``), a folder with an ``index`` slot in it, a release folder, or the
    download folder above those; ``None`` when no index is found."""
    if not folder.is_dir():
        return None
    if (folder / "bundle.json").is_file():
        return folder
    candidates = [folder / "index", install_dir(folder) / "index"]
    try:
        candidates += sorted(p / "index" for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        pass
    return next((c for c in candidates if c.is_dir()), None)


def disk_needed(release: Release, preset: str, root: Path, cache: Path) -> int:
    """Bytes needed to finish *preset*: each missing slot's archive (unless already cached) plus the
    same again, rounded up, for unpacking it."""
    need = 0
    for slot, artifact in release.plan(preset):
        if (install_dir(root, release.release_id) / slot).exists():
            continue
        cached = (cache / artifact.archive).is_file()
        need += artifact.size * (1.15 if cached else 2.15)
    return int(need)


def install_preset(
    release: Release, preset: str, root: Path, progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> list[str]:
    """Download and install every slot of *preset* that is not installed yet. Returns the slots
    installed now. Safe to repeat: finished slots are skipped, downloads resume, and an installed
    index is never touched."""
    note = progress or (lambda *_: None)
    cache = root / "download-cache" / release.release_id
    target = install_dir(root, release.release_id)
    todo = [(s, a) for s, a in release.plan(preset) if not (target / s).exists()]
    if not todo:
        return []
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    need = disk_needed(release, preset, root, cache)
    if free < need:
        raise DataError(f"Not enough free disk space: about {need / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free.")
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob(".unpack-*"):                # left by an interrupted install
        shutil.rmtree(old, ignore_errors=True)

    installed = []
    for slot, artifact in todo:
        archive = download_artifact(release, artifact, cache, progress, cancel)
        if cancel is not None and cancel.is_set():
            raise Cancelled("Install cancelled.")
        note(artifact.size, artifact.size, f"Unpacking {artifact.archive}")
        stage = target / f".unpack-{artifact.bundle_id}"
        shutil.rmtree(stage, ignore_errors=True)
        try:
            stage.mkdir()
            unpacked = unpack_archive(archive, artifact, stage)
            _check_bundle(unpacked, artifact)
            unpacked.replace(target / slot)             # atomic: a slot is either whole or absent
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        archive.unlink(missing_ok=True)                 # the unpacked copy is what is used from now on
        installed.append(slot)
    (target / "INSTALLATION.json").write_text(json.dumps({
        "schema_version": 1, "release_id": release.release_id, "engine_version": ENGINE_VERSION,
        "slots": installed_slots(root, release.release_id),
    }, indent=2), encoding="utf-8")
    return installed
