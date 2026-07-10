# CUI // SP-CTI
"""Shared fixtures for tests/cortex/.

icdev_logger.get_logger() sets ``propagate=False`` on its loggers, which
blocks pytest's caplog handler (attached to the root logger). Re-enable
propagation on the cortex search_service logger for the duration of each
test so caplog assertions work.
"""
from __future__ import annotations

import pytest

from tools.cortex import search_service


@pytest.fixture(autouse=True)
def _propagate_search_service_logs():
    logger = search_service.logger
    old = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = old
