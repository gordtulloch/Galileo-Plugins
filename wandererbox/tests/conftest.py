# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Shared fixtures for the wandererbox plugin test suite."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "requirement(id): RTM test-case ID this test implements")
    config.addinivalue_line("markers", "priority(level): MVP, P2, or P3")


@pytest.fixture(autouse=True)
def clean_profile_registry():
    """The core profile registry is process-wide; start and leave every test with it empty."""
    from galileo.core.switches import SwitchProfileRegistry
    SwitchProfileRegistry.clear()
    yield SwitchProfileRegistry
    SwitchProfileRegistry.clear()
