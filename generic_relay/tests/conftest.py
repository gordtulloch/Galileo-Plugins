# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Shared fixtures for the generic_relay plugin test suite."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "requirement(id): RTM test-case ID this test implements")
    config.addinivalue_line("markers", "priority(level): MVP, P2, or P3")
