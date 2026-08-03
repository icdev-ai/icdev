# CUI // SP-CTI
"""Shared fixtures for tests/cortex/.

icdev_logger.get_logger() sets ``propagate=False`` on its loggers, which
blocks pytest's caplog handler (attached to the root logger). Re-enable
propagation on the cortex search_service logger for the duration of each
test so caplog assertions work.
"""
from __future__ import annotations

import pytest

from tools.cortex import metrics, search_service


@pytest.fixture(autouse=True)
def _propagate_search_service_logs():
    logger = search_service.logger
    old = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = old


@pytest.fixture(autouse=True)
def _reset_metrics_memo():
    """metrics.summarize() memoizes across calls; drop it around every test.

    The memo key folds in ICDEV_DB_PATH so tests pointing at their own tmp_path
    DB already miss each other, but a test that does NOT repoint the DB would
    otherwise inherit a previous test's rollup.
    """
    metrics.reset_memo()
    yield
    metrics.reset_memo()
