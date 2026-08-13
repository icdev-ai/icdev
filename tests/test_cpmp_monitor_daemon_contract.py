# CUI // SP-CTI
"""cpmp_monitor.run() must return the contract the Genesis daemon actually scores.

tools/genesis/daemon.py::_run_reflex_impl_inner reads three keys off a reflex's
return value — `success`, `metric_value`, `details`. cpmp_monitor returned none
of them: it returned `status: "ok"` plus a top-level `cards_created`, which is
the key args/genesis_config.yaml names as its success_metric but is NOT the key
the daemon reads. So `success` defaulted to False and `metric_value` to 0.0 on
every run, base.classify_failure filed each completed sweep as
`reflex_reported_failure: cards_created=0.0`, and eleven consecutive such
"failures" opened the reflex's circuit breaker — degrading 3-hourly CPMP
surveillance to a half-open probe per (doubling) cooldown window.

The failure string is the tell: it names a metric of 0.0 against a `gte 0`
threshold that 0.0 satisfies. The metric was never the problem; the return
shape was.

Verifies:
1. A completed sweep reports success=True.
2. metric_value is present and numeric.
3. metric_value carries the cards_created count the config asks for.
4. details is a dict carrying the sweep counters.
5. The daemon's own scoring expression accepts the result.
6. evaluate_metric passes using the REAL success_metric from genesis_config.yaml.
7. classify_failure is never reached for a clean sweep.
8. A sweep that creates no new cards is still a success (dedup steady state).
9. A hard scan failure reports success=False.
10. ...and carries details.error, so the state row names the real cause.
"""
import pytest

from tools.daemon.base import classify_failure, evaluate_metric
from tools.genesis.reflexes import cpmp_monitor


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Minimal stand-in for a storage connection: no active contracts."""

    def __init__(self, rows=()):
        self._rows = list(rows)
        self.closed = False

    def set_security_context(self, ctx):
        return None

    def execute(self, sql, params=None):
        return _FakeCursor(self._rows)

    def close(self):
        self.closed = True


def _patch_get_connection(monkeypatch, factory):
    """Patch get_connection on EVERY module the reflex could resolve it through.

    `tools.db.storage` and `icdev.tools.db.storage` are two distinct module
    objects, and `from tools.db.storage import get_connection` inside a function
    body resolves by attribute traversal from the `tools.db` package — so it
    reads the icdev module, not the `tools` shim entry in sys.modules. Patching
    only the one you imported in the test leaves the reflex talking to the LIVE
    board, which is how a "unit" test ends up scanning production contracts.
    """
    import importlib

    patched = 0
    for name in ("tools.db.storage", "icdev.tools.db.storage"):
        try:
            monkeypatch.setattr(importlib.import_module(name), "get_connection", factory)
            patched += 1
        except ImportError:
            continue
    assert patched, "no storage module could be patched — the reflex would hit a real DB"


@pytest.fixture
def empty_board(monkeypatch):
    """run() with a reachable DB and zero active contracts — the clean path.

    Zero active contracts means no pass runs, so the sweep exercises the return
    contract without touching kanban_tasks or any board.
    """
    _patch_get_connection(monkeypatch, lambda *a, **k: _FakeConn())
    # _write_memory_log is guarded by a bare except, but stub it anyway so a
    # memory-subsystem hiccup cannot be mistaken for a contract failure.
    monkeypatch.setattr(cpmp_monitor, "_write_memory_log", lambda results: None)
    result = cpmp_monitor.run()
    assert result.get("contracts_scanned") == 0, (
        "fixture leaked to a real database — patch target is wrong"
    )
    return result


@pytest.fixture
def scan_failure(monkeypatch):
    """run() when the contract scan itself raises."""

    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    _patch_get_connection(monkeypatch, _boom)
    return cpmp_monitor.run()


# ---------------------------------------------------------------------------
# The keys the daemon reads
# ---------------------------------------------------------------------------


class TestDaemonReturnContract:
    def test_completed_sweep_reports_success(self, empty_board):
        assert empty_board["success"] is True

    def test_metric_value_is_numeric(self, empty_board):
        assert isinstance(empty_board["metric_value"], float)

    def test_metric_value_is_the_cards_created_count(self, empty_board):
        assert empty_board["metric_value"] == float(empty_board["cards_created"])

    def test_details_is_a_dict_of_the_sweep_counters(self, empty_board):
        details = empty_board["details"]
        assert isinstance(details, dict)
        assert details["contracts_scanned"] == empty_board["contracts_scanned"]
        assert details["cards_created"] == empty_board["cards_created"]

    def test_legacy_status_key_is_preserved(self, empty_board):
        # The CLI (`python -m tools.genesis.reflexes.cpmp_monitor`) prints this.
        assert empty_board["status"] == "ok"


# ---------------------------------------------------------------------------
# The daemon's own scoring, replayed
# ---------------------------------------------------------------------------


def _daemon_score(result):
    """Exactly what _run_reflex_impl_inner does with a reflex's return value."""
    return (
        result.get("success", False),
        result.get("metric_value", 0.0),
        result.get("details", {}),
    )


def _cpmp_success_metric():
    """The real success_metric block, not a copy — a copy cannot drift-detect."""
    from pathlib import Path

    import yaml

    root = Path(cpmp_monitor.__file__).resolve().parents[3]
    cfg = yaml.safe_load((root / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    return cfg["reflexes"]["cpmp_monitor"]["success_metric"]


class TestDaemonScoring:
    def test_daemon_reads_success_true(self, empty_board):
        success, _, _ = _daemon_score(empty_board)
        assert success is True

    def test_metric_passes_the_configured_threshold(self, empty_board):
        _, metric_value, _ = _daemon_score(empty_board)
        assert evaluate_metric(_cpmp_success_metric(), metric_value) is True

    def test_configured_metric_name_matches_a_key_the_reflex_returns(self, empty_board):
        # The config names `cards_created`; metric_value must mirror it, or the
        # state row reports a number that appears nowhere in the reflex.
        name = _cpmp_success_metric()["name"]
        assert name in empty_board
        assert empty_board["metric_value"] == float(empty_board[name])

    def test_clean_sweep_is_never_classified_as_a_failure(self, empty_board):
        success, metric_value, details = _daemon_score(empty_board)
        assert success and evaluate_metric(_cpmp_success_metric(), metric_value)
        # Guard the exact regression: the old return produced this string.
        msg = classify_failure(
            success, details, "cards_created", metric_value, _cpmp_success_metric()
        )
        assert "reflex_reported_failure" not in msg or success

    def test_zero_new_cards_is_still_a_success(self, empty_board):
        # The id-derived dedup in _suggest_kanban_card makes 0 the steady state.
        assert empty_board["cards_created"] == 0
        assert empty_board["success"] is True


# ---------------------------------------------------------------------------
# The failure path must name its own cause
# ---------------------------------------------------------------------------


class TestScanFailureContract:
    def test_scan_failure_reports_success_false(self, scan_failure):
        assert scan_failure["success"] is False

    def test_scan_failure_carries_details_error(self, scan_failure):
        assert "connection refused" in scan_failure["details"]["error"]

    def test_classify_failure_names_the_real_cause(self, scan_failure):
        success, metric_value, details = _daemon_score(scan_failure)
        msg = classify_failure(
            success, details, "cards_created", metric_value, _cpmp_success_metric()
        )
        # Not "reflex_reported_failure: cards_created=0.0" — that string sent
        # anyone debugging the state row after a metric that was never at fault.
        assert "connection refused" in msg
        assert "cards_created=0.0" not in msg
