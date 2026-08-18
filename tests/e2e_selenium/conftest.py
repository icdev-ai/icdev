# CUI // SP-CTI
"""Shared pytest configuration for tests/e2e_selenium/.

Registers the ``e2e_selenium`` marker and provides a session-scoped
``skip_if_no_server`` autouse fixture that skips the entire module unless the
dashboard at ``ICDEV_DASHBOARD_URL`` is *verifiably serving this checkout*.

Why an identity probe and not a port check (rem-e2e-01)
------------------------------------------------------
The original fixture opened a TCP connection to the port and ran the suite if
anything answered. Run from a git worktree on a developer machine, "anything"
is the dashboard already running on :5050 from the **main** checkout — so the
suite silently measured a different tree than the one under test. That is worse
than either clean outcome: a green run would be meaningless and a red run is
noise. Measured 2026-08-16, the two GraphRAG modules gave 12 failed / 6 passed
from a worktree and 18 skipped from the main checkout, for the same command.

The fix asks ``GET /health`` for ``checkout_id`` — a hash of the repo root the
*server* is importing (``tools/observability/health_blueprint.py``) — and skips
unless it equals this checkout's. A run that cannot reach the right dashboard
now skips rather than failing, and skips for a reason it can name.

The alternative — having the fixture start a dashboard from the checkout under
test on a free port — was rejected deliberately, and measured before rejecting:

  * It does not remove the need for this probe, it *adds* to it. Something must
    still confirm the port you connected to is the dashboard you launched and
    not the one already on :5050, and that confirmation is exactly ``checkout_id``.
  * It is not free: a cold dashboard from this tree took ~21s to bind (measured
    2026-08-16), on every session, for a suite whose own runtime is ~24s.
  * It does not by itself make a run meaningful, because what these GraphRAG
    assertions consume is indexed *data*, not just code. A dashboard started
    from this worktree reported ``db: ok`` and still held 0 nodes for 2 of the 6
    required graphs — the prerequisite is environment state that launching a
    process cannot conjure. ``require_graph_populated`` below is what makes that
    case an honest named skip instead of a fake failure.

So the probe is the fix, and starting a dashboard stays the operator's job —
which the skip message tells them to do.

Ported specs covered:
  - test_dashboard_health.py   (G4)
  - test_activity_usage.py     (G4)
  - test_agents_monitoring.py  (G4)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Ensure the project root (parent of icdev/) is first on sys.path so
# `import tools` resolves correctly regardless of how pytest is invoked
# (subprocess, worktree, CI). Only the project root is inserted — not
# icdev/ itself — so imports use the canonical project-root tools/ package.
_WORKTREE_ROOT = str(Path(__file__).resolve().parents[2])
if _WORKTREE_ROOT not in sys.path:
    sys.path.insert(0, _WORKTREE_ROOT)

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")

def local_checkout_id() -> str:
    """The ``checkout_id`` a dashboard started from THIS checkout would report.

    Computed by the same function the server exposes, so the two cannot drift
    apart — but applied to *this file's* path rather than to wherever ``import
    tools`` happened to land. That distinction is load-bearing: under pytest
    ``tools`` can resolve to the shared checkout (an earlier ``sys.modules``
    entry, or a stray ``.pth``), and calling the no-argument ``_checkout_id()``
    would then return the SHARED tree's id — which matches the dashboard already
    running from it, so the probe would wave the suite through against the wrong
    tree. ``conftest.__file__`` is unambiguously in the checkout under test.

    Empty string when it cannot be determined, which callers must treat as
    "cannot verify" rather than as a match.
    """
    try:
        from tools.observability.health_blueprint import checkout_id_for

        return checkout_id_for(__file__)
    except Exception:
        # Includes the pre-rem-e2e-01 module, which has no ``checkout_id_for``.
        # Returning "" makes the caller skip, which is the safe verdict.
        return ""


def dashboard_checkout_id(url: str, timeout: float = 10.0) -> tuple[str | None, str]:
    """Return ``(checkout_id, reason)`` for the dashboard at *url*.

    ``checkout_id`` is None when the dashboard could not be identified; *reason*
    then says why, so the resulting skip names a cause instead of "no server".
    """
    health_url = url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(health_url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, f"{health_url} returned HTTP {exc.code}"
    except Exception as exc:
        return None, f"{health_url} unreachable ({type(exc).__name__}: {exc})"

    checkout_id = str(body.get("checkout_id") or "")
    if not checkout_id:
        return None, (
            f"{health_url} reported no checkout_id — the running dashboard predates "
            "the identity probe (rem-e2e-01) or was started from a tree with no git "
            "marker, so which checkout it serves cannot be verified"
        )
    return checkout_id, ""


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e_selenium: mark a test as a Selenium E2E test (requires running dashboard)",
    )


@pytest.fixture(scope="session", autouse=True)
def skip_if_no_server() -> None:  # type: ignore[return]
    """Skip all e2e_selenium tests unless the dashboard is serving THIS checkout."""
    local = local_checkout_id()
    if not local:
        pytest.skip(
            "cannot compute this checkout's identity (no .git marker), so the "
            "dashboard at {} cannot be verified to serve it".format(BASE_URL),
            allow_module_level=True,
        )

    remote, reason = dashboard_checkout_id(BASE_URL)
    if remote is None:
        pytest.skip(
            f"ICDEV™ dashboard not usable at {BASE_URL}: {reason}. Start the "
            "dashboard from this checkout or set ICDEV_DASHBOARD_URL before "
            "running e2e_selenium tests.",
            allow_module_level=True,
        )

    if remote != local:
        pytest.skip(
            f"the dashboard at {BASE_URL} is serving a DIFFERENT checkout "
            f"(checkout_id {remote} != {local}). Running against it would measure "
            "another tree — start a dashboard from this checkout and point "
            "ICDEV_DASHBOARD_URL at it.",
            allow_module_level=True,
        )


def _kg_node_count(graph_id: str) -> int | None:
    """Nodes the DASHBOARD holds for *graph_id*, or None if it cannot be asked.

    Asks the server, not a local ``get_connection()``. The test process must
    not answer this question for itself: ``tests/conftest.py`` forces
    ``ICDEV_STORAGE_BACKEND=sqlite`` for the whole suite, so a local connection
    reads a different database than the dashboard under test — measured
    2026-08-16, that reported "0 nodes" for graphs the dashboard was serving 65
    rows from, which would skip a test that can genuinely catch a regression.

    ``/api/knowledge-graph/graph/<id>`` reads kg_nodes directly and is not the
    ``graph_rag.retrieve`` path these modules assert on, so it stays an
    independent precondition rather than a circular one.
    """
    url = f"{BASE_URL.rstrip('/')}/api/knowledge-graph/graph/{graph_id}"
    try:
        with urllib.request.urlopen(url, timeout=30.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    nodes = body.get("nodes")
    if isinstance(nodes, list):
        return len(nodes)
    # A graph_id canvas_indexer has never written answers 200 with
    # {"status": "error", "message": "Graph <id> not found."} and no node list.
    # Never indexed and indexed-but-empty are the same unmet prerequisite, so
    # report 0 for it. Any OTHER error shape stays None — "I could not tell"
    # must not be rounded down to "it is empty".
    if body.get("status") == "error" and "not found" in str(body.get("message", "")).lower():
        return 0
    return None


@pytest.fixture(scope="session")
def require_graph_populated():
    """Skip a test whose knowledge-graph prerequisite has never been indexed.

    "kg_nodes populated for each graph_id" is a documented prerequisite of the
    GraphRAG modules, and an unindexed graph is the single largest difference
    between a developer machine and a fresh checkout. Asserting >= 1 node
    against an empty graph measures the absence of the indexer, not the
    behaviour under test, so the honest verdict is a named skip.

    This does NOT weaken the assertion it guards: when the graph does hold
    nodes and retrieval still returns none, the test fails exactly as before —
    which is the regression these modules exist to catch.
    """

    def _require(graph_id: str) -> int:
        count = _kg_node_count(graph_id)
        if count is None:
            pytest.skip(
                f"cannot reach the database to verify KG graph '{graph_id}' is "
                "populated, so an empty-graph result could not be told apart "
                "from a retrieval regression"
            )
        if count == 0:
            pytest.skip(
                f"KG graph '{graph_id}' holds 0 nodes — run "
                "tools/knowledge_graph/canvas_indexer.py (documented prerequisite)"
            )
        return count

    return _require


_TOUR_SUPPRESS_JS = (
    "localStorage.setItem('icdev_tour_completed', '1');"
    "localStorage.setItem('icdev_tour_last_step', '999');"
    "var el = document.getElementById('icdev-tour-welcome');"
    "if (el) { el.classList.remove('visible'); el.style.display='none'; el.remove(); }"
    "document.querySelectorAll('[class*=\"tour\"]').forEach(function(e){"
    "  e.style.display='none'; e.remove();"
    "});"
)


def suppress_tour(driver) -> None:  # type: ignore[type-arg]
    """Suppress the ICDEV tour overlay on the current page. Call after every navigation."""
    try:
        driver.execute_script(_TOUR_SUPPRESS_JS)
    except Exception:
        pass
# CUI // SP-CTI
