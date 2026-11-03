# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Shared fixtures for the NodeConnect plugin test suite."""


def pytest_configure(config):
    config.addinivalue_line("markers", "requirement(id): RTM test-case ID this test implements")
    config.addinivalue_line("markers", "priority(level): MVP, P2, or P3")
    config.addinivalue_line("markers", "integration: needs a second process or an external service")


import pytest


@pytest.fixture(autouse=True)
def _core_replication_registry():
    """Core registers its replicable tables at start-up; register them here (idempotent)."""
    from galileo.replication import register_defaults
    register_defaults()
    yield


@pytest.fixture
def settings_db(tmp_path):
    """A migrated database for tests that only need the settings store."""
    from galileo.library.database import init_db
    from galileo.library.models.base import db as database
    init_db(tmp_path / "s.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
