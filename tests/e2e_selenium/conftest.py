# CUI // SP-CTI
"""Shared pytest configuration for tests/e2e_selenium/.

Registers the ``e2e_selenium`` marker and provides a session-scoped
``skip_if_no_server`` autouse fixture that skips the entire module when
the ICDEV™ dashboard is not reachable (e.g. in CI without a running Flask
process).

Ported specs covered:
  - test_dashboard_health.py   (G4)
  - test_activity_usage.py     (G4)
  - test_agents_monitoring.py  (G4)
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

# Ensure the worktree root is first on sys.path so `import tools` resolves
# correctly regardless of how pytest is invoked (subprocess, worktree, CI).
# icdev/ is prepended before the project root so `import tools` resolves via
# icdev.tools first, preventing bare tools/ from shadowing it.
_WORKTREE_ROOT = str(Path(__file__).resolve().parents[2])
_ICDEV_ROOT = str(Path(__file__).resolve().parents[2] / "icdev")
if _WORKTREE_ROOT not in sys.path:
    sys.path.insert(0, _WORKTREE_ROOT)
if _ICDEV_ROOT not in sys.path:
    sys.path.insert(0, _ICDEV_ROOT)

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5000")


def _server_reachable(url: str, timeout: float = 2.0) -> bool:
    """Return True if *url*'s host:port accepts a TCP connection."""
    try:
        # Strip scheme
        host_port = url.split("://", 1)[-1].split("/")[0]
        host, _, port_str = host_port.partition(":")
        port = int(port_str) if port_str else (443 if url.startswith("https") else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e_selenium: mark a test as a Selenium E2E test (requires running dashboard)",
    )


@pytest.fixture(scope="session", autouse=True)
def skip_if_no_server() -> None:  # type: ignore[return]
    """Skip all e2e_selenium tests when the dashboard is not reachable."""
    if not _server_reachable(BASE_URL):
        pytest.skip(
            f"ICDEV™ dashboard not reachable at {BASE_URL}. "
            "Start the dashboard or set ICDEV_DASHBOARD_URL before running e2e_selenium tests.",
            allow_module_level=True,
        )
# CUI // SP-CTI
