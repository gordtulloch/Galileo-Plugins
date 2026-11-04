# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Shared fixtures for the WebConnect plugin test suite."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "requirement(id): RTM test-case ID this test implements")
    config.addinivalue_line("markers", "priority(level): MVP, P2, or P3")
    config.addinivalue_line("markers", "integration: needs a second process or an external service")


@pytest.fixture
def link_db(tmp_path):
    """A migrated database (peers, audit, settings)."""
    from galileo.library.database import init_db
    from galileo.library.models.base import db as database
    init_db(tmp_path / "w.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)
