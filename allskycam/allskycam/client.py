# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""HTTP client for an indi-allsky web server (ASC-010, ASC-020).

indi-allsky has no dedicated REST API, but its ``/latestimage`` route answers with a 302 to the
newest processed image of a camera (``?camera_id=N``, default: the latest camera). Following that
redirect is the whole protocol. The redirect target is a timestamped file name, so it doubles as
a cheap "is this new?" check: when it matches the previous fetch the body is never downloaded.

TLS certificates are deliberately not verified: indi-allsky installs normally use a self-signed
certificate, and the connection is to the user's own camera on their own network.

Unauthenticated access only. If the indi-allsky install has ``AUTH_ALL_VIEWS`` on, the redirect
lands on its login page and :class:`AllSkyError` says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

LATEST_PATH = "/latestimage"
MAX_IMAGE_BYTES = 40 * 1024 * 1024     # refuse anything bigger than this (a runaway or wrong URL)
_CHUNK = 64 * 1024
_MAX_REDIRECTS = 5


class AllSkyError(Exception):
    """A fetch failed in a way worth showing the user (the message is user-readable)."""


@dataclass(frozen=True)
class Frame:
    """One image as downloaded: the encoded bytes (JPEG/PNG), where they came from, and when."""
    data: bytes
    url: str
    fetched_at: float


def normalize_base_url(text: str) -> str:
    """The indi-allsky web root from whatever the user typed.

    Adds ``http://`` when there is no scheme, drops a trailing slash, and drops a pasted
    ``/latestimage`` so either the root or the full endpoint works. Raises ``ValueError`` for
    anything that is not an http(s) URL with a host.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Enter the address of your indi-allsky server.")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{text!r} is not an http:// or https:// address.")
    path = parts.path.rstrip("/")
    if path.lower().endswith(LATEST_PATH):
        path = path[: -len(LATEST_PATH)]
    return f"{parts.scheme}://{parts.netloc}{path}"


def latest_image_url(base_url: str, camera_id: int = 0) -> str:
    url = normalize_base_url(base_url) + LATEST_PATH
    return f"{url}?camera_id={int(camera_id)}" if camera_id else url


class AllSkyClient:
    """Fetches the newest image from one indi-allsky server."""

    def __init__(
        self,
        base_url: str,
        camera_id: int = 0,
        timeout: float = 15.0,
        max_bytes: int = MAX_IMAGE_BYTES,
        session: Any = None,
    ) -> None:
        self.url = latest_image_url(base_url, camera_id)
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._session = session

    def fetch_latest(self, known_url: str | None = None) -> Frame | None:
        """The newest image, or ``None`` when it is the one at *known_url* (nothing new).

        Redirects are followed by hand so that an unchanged image is recognised from the ``Location``
        header alone, before any request for the image itself is made.

        Raises :class:`AllSkyError` for every failure: unreachable server, HTTP error, a login
        page instead of an image, an oversized or non-image response.
        """
        import requests
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session = self._session or requests
        url = self.url
        response = None
        try:
            for _hop in range(_MAX_REDIRECTS + 1):
                response = session.get(url, timeout=self.timeout, stream=True, allow_redirects=False, verify=False)
                location = response.headers.get("Location") if 300 <= response.status_code < 400 else None
                if not location:
                    break
                url = urljoin(str(getattr(response, "url", url)) or url, location)
                response.close()
                response = None
                if known_url is not None and url == known_url:
                    return None
            else:
                raise AllSkyError(f"Too many redirects from {self.url}")

            if response.status_code >= 400:
                raise AllSkyError(
                    f"indi-allsky answered HTTP {response.status_code} for {url}"
                    + (" (no image has been captured yet?)" if response.status_code >= 500 else "")
                )
            content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                if "html" in content_type:
                    raise AllSkyError(
                        "indi-allsky returned a web page instead of an image. Either the address is wrong "
                        "(it should be the indi-allsky root, e.g. http://host/indi-allsky) or the server "
                        "requires a login, which this plugin does not support."
                    )
                raise AllSkyError(f"indi-allsky returned {content_type or 'an unknown type'}, not an image.")
            if known_url is not None and url == known_url:
                return None

            data = bytearray()
            for chunk in response.iter_content(chunk_size=_CHUNK):
                data.extend(chunk)
                if len(data) > self.max_bytes:
                    raise AllSkyError(f"The image is larger than {self.max_bytes // (1024 * 1024)} MB; giving up.")
            if not data:
                raise AllSkyError("indi-allsky returned an empty image.")
            return Frame(data=bytes(data), url=url, fetched_at=time.time())
        except requests.RequestException as exc:
            raise AllSkyError(f"Could not reach {url}: {exc}") from exc
        finally:
            if response is not None:
                response.close()
