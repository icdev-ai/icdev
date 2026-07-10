# CUI // SP-CTI
"""Shared fixtures for the Cortex facade test suite.

``icdev_logger.get_logger`` sets ``propagate = False`` on its loggers, so
records never reach the root logger where pytest's ``caplog`` handler
listens. Re-enable propagation on the cortex loggers for the duration of
each test so exception-isolation and timeout tests can assert on log text.
"""
import logging

import pytest

_CORTEX_LOGGER_NAMES = ("icdev.cortex.api", "icdev.cortex.search")


@pytest.fixture(autouse=True)
def _cortex_loggers_propagate_to_caplog():
    loggers = [logging.getLogger(name) for name in _CORTEX_LOGGER_NAMES]
    previous = [lg.propagate for lg in loggers]
    for lg in loggers:
        lg.propagate = True
    yield
    for lg, prev in zip(loggers, previous):
        lg.propagate = prev
