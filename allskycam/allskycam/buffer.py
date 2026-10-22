# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The rolling set of recent images behind the animation (ASC-050).

Frames are kept as their encoded JPEG/PNG bytes rather than decoded pixmaps: a 3000 px all-sky
frame is ~2 MB encoded but ~36 MB decoded, so keeping 100 decoded frames would cost gigabytes.
A byte cap bounds the encoded total too, dropping the oldest frames first.
"""

from __future__ import annotations

from collections import deque

from allskycam.client import Frame

DEFAULT_MAX_BYTES = 256 * 1024 * 1024
MIN_FRAMES_KEPT = 2


class FrameBuffer:
    def __init__(self, maxlen: int, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._maxlen = max(1, maxlen)
        self._max_bytes = max_bytes
        self._frames: deque[Frame] = deque()
        self._bytes = 0

    def append(self, frame: Frame) -> None:
        self._frames.append(frame)
        self._bytes += len(frame.data)
        self._trim()

    def resize(self, maxlen: int) -> None:
        """Change how many frames are kept; keeps the newest if that means dropping some."""
        self._maxlen = max(1, maxlen)
        self._trim()

    def clear(self) -> None:
        self._frames.clear()
        self._bytes = 0

    def frames(self) -> list[Frame]:
        """Oldest first."""
        return list(self._frames)

    @property
    def latest(self) -> Frame | None:
        return self._frames[-1] if self._frames else None

    @property
    def maxlen(self) -> int:
        return self._maxlen

    @property
    def total_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._frames)

    def _trim(self) -> None:
        while len(self._frames) > self._maxlen:
            self._drop_oldest()
        while self._bytes > self._max_bytes and len(self._frames) > MIN_FRAMES_KEPT:
            self._drop_oldest()

    def _drop_oldest(self) -> None:
        self._bytes -= len(self._frames.popleft().data)
