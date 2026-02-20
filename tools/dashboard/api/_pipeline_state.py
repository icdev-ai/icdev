# CUI // SP-CTI
"""
Shared pipeline state for build/test background jobs.

This module exists as a separate file to prevent double-import issues.
Flask/werkzeug can reimport the intake blueprint module, creating
duplicate module-level dicts. By isolating state here, the dicts
remain stable across reimports.
"""

import threading

BUILD_JOBS = {}  # session_id -> job dict
BUILD_LOCK = threading.Lock()

TEST_JOBS = {}  # session_id -> job dict
TEST_LOCK = threading.Lock()
