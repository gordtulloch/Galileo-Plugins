# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""AllSkyCam views (ASC-050 … ASC-090): the sidebar page (Latest / Animation tabs) and the
compact dock pane. Both read from the one :class:`allskycam.service.AllSkyService`."""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import UTC, datetime

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from allskycam.client import Frame, normalize_base_url
from allskycam.config import (
    CAMERA_ID_RANGE,
    FPS_RANGE,
    FRAME_COUNT_RANGE,
    INTERVAL_RANGE,
    SUN_ALTITUDE_RANGE,
    AllSkyConfig,
)
from allskycam.service import AllSkyService

logger = logging.getLogger(__name__)

ANIMATION_MAX_EDGE = 1280      # animation frames are decoded no larger than this
STILL_MAX_EDGE = 2400          # the Latest tab keeps more detail
_DECODE_CACHE = 10             # decoded frames kept so a looping animation does not re-decode each time


def decode_frame(data: bytes, max_edge: int = ANIMATION_MAX_EDGE) -> QImage | None:
    """Decode JPEG/PNG bytes, asking the decoder to downscale so a huge all-sky frame is cheap."""
    raw = QByteArray(data)
    buf = QBuffer(raw)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > max_edge:
        reader.setScaledSize(size.scaled(max_edge, max_edge, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return None if image.isNull() else image


def _clock(frame: Frame) -> str:
    return datetime.fromtimestamp(frame.fetched_at, tz=UTC).astimezone().strftime("%H:%M:%S")


class ImageView(QLabel):
    """A label that shows one image scaled to fit, keeping its aspect ratio."""

    def __init__(self, placeholder: str) -> None:
        super().__init__(placeholder)
        self._placeholder = placeholder
        self._image: QImage | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setObjectName("AllSkyImage")

    def set_image(self, image: QImage | None) -> None:
        self._image = image
        self._render()

    def resizeEvent(self, event) -> None:  # Qt signature
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self._image is None:
            self.setPixmap(QPixmap())
            self.setText(self._placeholder)
            return
        scaled = self._image.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.setText("")
        self.setPixmap(QPixmap.fromImage(scaled))


class LatestView(QWidget):
    """The newest image as downloaded (ASC-050)."""

    def __init__(self, service: AllSkyService) -> None:
        super().__init__()
        self._service = service
        self._shown_url: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image = ImageView("No image yet")
        self.caption = QLabel("")
        self.caption.setObjectName("AllSkyCaption")
        layout.addWidget(self.image, 1)
        layout.addWidget(self.caption)
        service.frame_added.connect(self.show_frame)
        service.config_changed.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        latest = self._service.buffer.latest
        if latest is None:
            self._shown_url = None
            self.image.set_image(None)
            self.caption.setText("")
        elif latest.url != self._shown_url:
            self.show_frame(latest)

    def show_frame(self, frame: Frame) -> None:
        self._shown_url = frame.url
        image = decode_frame(frame.data, STILL_MAX_EDGE)
        self.image.set_image(image)
        self.caption.setText(f"Fetched {_clock(frame)} — {frame.url}" if image else "The image could not be decoded")


class AnimationView(QWidget):
    """Loops through the held images (ASC-060). Only runs its timer while visible, so a closed
    dock pane or a background tab costs nothing."""

    def __init__(self, service: AllSkyService) -> None:
        super().__init__()
        self._service = service
        self._index = -1
        self._cache: OrderedDict[str, QImage] = OrderedDict()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image = ImageView("Collecting images…")
        self.caption = QLabel("")
        self.caption.setObjectName("AllSkyCaption")
        layout.addWidget(self.image, 1)
        layout.addWidget(self.caption)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.advance)
        service.config_changed.connect(self._apply_speed)
        self._apply_speed()

    def _apply_speed(self) -> None:
        self._timer.setInterval(max(1, int(1000 / self._service.config.fps)))

    def showEvent(self, event) -> None:  # Qt signature
        super().showEvent(event)
        self._timer.start()
        self.advance()

    def hideEvent(self, event) -> None:  # Qt signature
        super().hideEvent(event)
        self._timer.stop()

    def advance(self) -> None:
        frames = self._service.buffer.frames()
        if len(frames) < 2:
            self._index = -1
            if frames:
                self._show(frames[0], 1, 1)
            else:
                self.image.set_image(None)
                self.caption.setText("")
            return
        self._index = (self._index + 1) % len(frames)
        self._show(frames[self._index], self._index + 1, len(frames))

    def _show(self, frame: Frame, position: int, total: int) -> None:
        image = self._cache.get(frame.url)
        if image is None:
            image = decode_frame(frame.data)
            if image is not None:
                self._cache[frame.url] = image
                while len(self._cache) > _DECODE_CACHE:
                    self._cache.popitem(last=False)
        self.image.set_image(image)
        self.caption.setText(f"Image {position} of {total} — {_clock(frame)}")


class SettingsForm(QWidget):
    """The plugin's options (ASC-040). Nothing takes effect until Save."""

    def __init__(self, service: AllSkyService) -> None:
        super().__init__()
        self._service = service

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://allsky.local/indi-allsky")
        self.camera_spin = QSpinBox()
        self.camera_spin.setRange(*CAMERA_ID_RANGE)
        self.camera_spin.setSpecialValueText("Default")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(*INTERVAL_RANGE)
        self.interval_spin.setSuffix(" s")
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(*FRAME_COUNT_RANGE)
        self.pause_check = QCheckBox("Pause during daytime")
        self.sun_spin = QDoubleSpinBox()
        self.sun_spin.setRange(*SUN_ALTITUDE_RANGE)
        self.sun_spin.setDecimals(1)
        self.sun_spin.setSuffix("°")
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(*FPS_RANGE)
        self.fps_spin.setDecimals(1)
        self.fps_spin.setSuffix(" frames/s")
        self.dock_check = QCheckBox("Show animation pane")

        form = QFormLayout()
        form.addRow("Server address:", self.url_edit)
        form.addRow("Camera number:", self.camera_spin)
        form.addRow("Check every:", self.interval_spin)
        form.addRow("Images in animation:", self.frames_spin)
        form.addRow("", self.pause_check)
        form.addRow("Sun altitude limit:", self.sun_spin)
        form.addRow("Animation speed:", self.fps_spin)
        form.addRow("", self.dock_check)

        self.test_btn = QPushButton("Test connection")
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("AccentButton")
        self.message = QLabel("")
        self.message.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.message)
        layout.addStretch(1)

        self.test_btn.clicked.connect(self._on_test)
        self.save_btn.clicked.connect(self._on_save)
        service.test_finished.connect(self._on_test_finished)
        service.config_changed.connect(self._sync_dock_check)
        self.pause_check.toggled.connect(self.sun_spin.setEnabled)
        self.load()

    def _sync_dock_check(self) -> None:
        """The pane's own close button changes this option behind the form's back; mirror it without
        disturbing anything else the user is editing."""
        self.dock_check.setChecked(self._service.config.show_dock)

    def load(self) -> None:
        """Show the saved options."""
        c = self._service.config
        self.url_edit.setText(c.url)
        self.camera_spin.setValue(c.camera_id)
        self.interval_spin.setValue(c.interval_s)
        self.frames_spin.setValue(c.frame_count)
        self.pause_check.setChecked(c.pause_daytime)
        self.sun_spin.setValue(c.sun_altitude_deg)
        self.sun_spin.setEnabled(c.pause_daytime)
        self.fps_spin.setValue(c.fps)
        self.dock_check.setChecked(c.show_dock)

    def _gather(self) -> AllSkyConfig:
        return AllSkyConfig(
            url=self.url_edit.text().strip(),
            camera_id=self.camera_spin.value(),
            interval_s=self.interval_spin.value(),
            frame_count=self.frames_spin.value(),
            pause_daytime=self.pause_check.isChecked(),
            sun_altitude_deg=self.sun_spin.value(),
            fps=self.fps_spin.value(),
            show_dock=self.dock_check.isChecked(),
        )

    def _say(self, text: str, ok: bool) -> None:
        self.message.setStyleSheet("" if ok else "color: #d9534f;")
        self.message.setText(text)

    def _on_test(self) -> None:
        try:
            normalize_base_url(self.url_edit.text())
        except ValueError as exc:
            self._say(str(exc), False)
            return
        self.test_btn.setEnabled(False)
        self._say("Connecting…", True)
        self._service.test_connection(self._gather())

    def _on_test_finished(self, ok: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        self._say(message, ok)

    def _on_save(self) -> None:
        config = self._gather()
        if config.url:
            try:
                config.url = normalize_base_url(config.url)
            except ValueError as exc:
                self._say(str(exc), False)
                return
        self._service.apply_config(config)
        self._say("Saved.", True)


class SettingsPage(QWidget):
    """The plugin's page under Options (ASC-040): a heading over the :class:`SettingsForm`."""

    def __init__(self, service: AllSkyService) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        heading = QLabel("AllSkyCam")
        heading.setObjectName("SectionHeading")
        self.form = SettingsForm(service)
        layout.addWidget(heading)
        layout.addWidget(self.form, 1)


class AllSkyCamPage(QWidget):
    """The sidebar page: status line, Fetch now, and the Latest / Animation tabs. The options live under Options (ASC-040)."""

    def __init__(self, service: AllSkyService) -> None:
        super().__init__()
        self._service = service
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.status = QLabel(service.status)
        self.status.setWordWrap(True)
        self.fetch_btn = QPushButton("Fetch now")
        header.addWidget(self.status, 1)
        header.addWidget(self.fetch_btn)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.latest = LatestView(service)
        self.animation = AnimationView(service)
        self.tabs.addTab(self.latest, "Latest")
        self.tabs.addTab(self.animation, "Animation")
        layout.addWidget(self.tabs, 1)

        self.fetch_btn.clicked.connect(service.fetch_now)
        service.status_changed.connect(self.status.setText)


class DockPane(QWidget):
    """The compact dockable pane: the animation and a one-line status (ASC-070)."""

    def __init__(self, service: AllSkyService) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.animation = AnimationView(service)
        self.status = QLabel(service.status)
        self.status.setWordWrap(True)
        self.status.setObjectName("AllSkyCaption")
        layout.addWidget(self.animation, 1)
        layout.addWidget(self.status)
        service.status_changed.connect(self.status.setText)
        self.setMinimumWidth(240)
