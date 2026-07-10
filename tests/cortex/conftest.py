# CUI // SP-CTI
"""Shared fixtures for Cortex tests.

Cortex modules log through ``tools.logging.icdev_logger.get_logger``, which
sets ``propagate = False`` (records go to NDJSON file handlers, not root).
pytest's ``caplog`` captures via a root-logger handler, so propagation must
be re-enabled for the duration of each test.
"""
import logging

import pytest

_CORTEX_LOGGERS = (
    "icdev.cortex.governance",
    "icdev.cortex.search_service",
)


@pytest.fixture(autouse=True)
def _propagate_cortex_logs():
    saved = {}
    for name in _CORTEX_LOGGERS:
        logger = logging.getLogger(name)
        saved[name] = logger.propagate
        logger.propagate = True
    yield
    for name, value in saved.items():
        logging.getLogger(name).propagate = value
