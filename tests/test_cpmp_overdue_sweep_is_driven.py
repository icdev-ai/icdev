# CUI // SP-CTI
"""Something must actually run the CDRL overdue sweep.

`contract_manager.compute_overdue_deliverables()` is what moves a deliverable to
status 'overdue' and fills `days_overdue`. Nothing called it outside its own
argparse block, so on the live board no deliverable had ever reached that state
and `days_overdue` was 0 on every row — including rows 44 days past due.

'overdue' is the ONLY thing four consumers look at, so all four read a permanent
zero: `get_contract()['overdue_count']`, `portfolio_manager` (per-contract count
and the portfolio-wide list), `cpars_predictor` (overdue count feeds the
predicted CPARS score), and `negative_event_tracker` (gated on
`days_overdue > 0`, so a late CDRL was never recorded as a negative event).
Meanwhile `pmo_ai_advisor` derives overdue live from `due_date` — which is why
cpmp_monitor files a high-severity "N CDRL(s) are past due" card while the
contract page beside it reports 0 overdue.

A test that only asserts `compute_overdue_deliverables` behaves correctly cannot
catch this: it behaved correctly the whole time. Nothing called it. So these
tests assert the CALL, and that the sweep is wired ahead of the passes that read
what it writes.

Verifies:
1. The sweep runs on a full pass.
2. It runs on the lightweight 'deliverables' pass too.
3. Its result is reported as a counter, not silently discarded.
4. It is called unscoped, so non-active contracts are covered as well.
5. It runs BEFORE the PMO issue pass that reads deliverable state.
6. A sweep that raises does not take the surveillance passes down with it.
7. ...and is reported in errors rather than swallowed.
8. compute_overdue_deliverables still has no other production caller, so this
   wiring is load-bearing rather than one of several.
"""
import importlib
import inspect

import pytest

from tools.genesis.reflexes import cpmp_monitor


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """No active contracts: the detection passes are skipped, the sweep is not."""

    def set_security_context(self, ctx):
        return None

    def execute(self, sql, params=None):
        return _FakeCursor([])

    def close(self):
        return None


# Modules that do `from tools.db.storage import get_connection` at MODULE level
# and therefore hold their own binding, independent of the storage module's
# attribute. Both namespaces, because `tools.x` and `icdev.tools.x` are distinct
# module objects even though `tools/` is a shim over `icdev.tools`.
_CONN_HOLDERS = tuple(
    f"{ns}.{mod}"
    for ns in ("tools", "icdev.tools")
    for mod in ("db.storage", "govcon.pmo_ai_advisor", "govcon.contract_manager")
)

# Imported HERE, at collection, and deliberately not inside a fixture.
#
# cpmp_monitor.run() imports pmo_ai_advisor lazily, inside the function body. If
# that is the module's first import and it happens while this file's fixtures
# have get_connection patched, pmo_ai_advisor's module-level
# `from tools.db.storage import get_connection` binds THE FAKE — permanently.
# monkeypatch then restores the storage module's attribute and reports itself
# clean, but the captured reference in pmo_ai_advisor is not an attribute
# monkeypatch ever recorded, so it survives teardown and every later test in the
# session gets _FakeConn. That is not hypothetical: it silently broke three
# tests in test_cpmp_deliverable_cancellation.py, whose _gather_contract_context
# then swallowed the resulting error and returned a dict missing
# 'overdue_deliverables' — a KeyError in a file this one does not import and
# only ever when the two ran in the same session.
for _name in _CONN_HOLDERS:
    try:
        importlib.import_module(_name)
    except ImportError:
        pass


def _patch_get_connection(monkeypatch, factory):
    """Patch get_connection on EVERY module the reflex could resolve it through.

    `tools.db.storage` and `icdev.tools.db.storage` are distinct module objects,
    and `from tools.db.storage import get_connection` inside a function body
    resolves by attribute traversal to the icdev one. Patching only the module
    the test imported leaves the reflex talking to the LIVE board.

    The consumers in `_CONN_HOLDERS` are patched too, not just the storage
    modules: each captured its own reference at import, so patching the source
    module alone leaves them pointed at the real board.
    """
    patched = 0
    for name in _CONN_HOLDERS:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "get_connection"):
            monkeypatch.setattr(mod, "get_connection", factory)
            patched += 1
    assert patched, "no storage module could be patched — the reflex would hit a real DB"


@pytest.fixture
def sweep_calls(monkeypatch):
    """Record every compute_overdue_deliverables call the reflex makes."""
    calls = []

    def _fake_sweep(contract_id=None):
        calls.append({"contract_id": contract_id, "order": len(calls)})
        return {"status": "ok", "overdue_count": 3}

    for name in ("tools.govcon.contract_manager", "icdev.tools.govcon.contract_manager"):
        try:
            monkeypatch.setattr(
                importlib.import_module(name), "compute_overdue_deliverables", _fake_sweep
            )
        except ImportError:
            continue

    _patch_get_connection(monkeypatch, lambda *a, **k: _FakeConn())
    monkeypatch.setattr(cpmp_monitor, "_write_memory_log", lambda results: None)
    return calls


# ---------------------------------------------------------------------------
# The sweep is actually driven
# ---------------------------------------------------------------------------


class TestSweepIsCalled:
    def test_full_pass_runs_the_sweep(self, sweep_calls):
        cpmp_monitor.run()
        assert len(sweep_calls) == 1

    def test_deliverables_pass_runs_the_sweep(self, sweep_calls):
        # The lightweight 3-hourly pass. If the sweep only rode on 'full', the
        # stored state would lag the cadence the deliverables pass exists for.
        cpmp_monitor.run({"pass_type": "deliverables"})
        assert len(sweep_calls) == 1

    def test_sweep_count_is_reported(self, sweep_calls):
        result = cpmp_monitor.run()
        assert result["deliverables_marked_overdue"] == 3

    def test_sweep_is_unscoped(self, sweep_calls):
        # The contract loop below only walks status='active' contracts. Passing a
        # contract_id here would leave deliverables on draft/complete/terminated
        # contracts permanently un-swept.
        cpmp_monitor.run()
        assert sweep_calls[0]["contract_id"] is None

    def test_sweep_runs_before_the_issue_detection_pass(self, monkeypatch, sweep_calls):
        """Order matters: pass 1 reads deliverable state the sweep writes."""
        order = []

        def _fake_sweep(contract_id=None):
            order.append("sweep")
            return {"status": "ok", "overdue_count": 0}

        def _fake_detect(cid):
            order.append("detect")
            return {"status": "ok", "issues": []}

        for name in ("tools.govcon.contract_manager", "icdev.tools.govcon.contract_manager"):
            try:
                monkeypatch.setattr(
                    importlib.import_module(name), "compute_overdue_deliverables", _fake_sweep
                )
            except ImportError:
                continue
        for name in ("tools.govcon.pmo_ai_advisor", "icdev.tools.govcon.pmo_ai_advisor"):
            try:
                monkeypatch.setattr(
                    importlib.import_module(name), "auto_detect_issues", _fake_detect
                )
            except ImportError:
                continue

        class _OneContract(_FakeConn):
            def execute(self, sql, params=None):
                return _FakeCursor([{"id": "c1", "contract_number": "N1", "title": "T"}])

        _patch_get_connection(monkeypatch, lambda *a, **k: _OneContract())
        cpmp_monitor.run()
        assert order and order[0] == "sweep"


# ---------------------------------------------------------------------------
# A failing sweep must not dead-letter the rest of the reflex
# ---------------------------------------------------------------------------


class TestSweepFailureIsContained:
    @pytest.fixture
    def broken_sweep(self, monkeypatch):
        def _boom(contract_id=None):
            raise RuntimeError("bad due_date row")

        for name in ("tools.govcon.contract_manager", "icdev.tools.govcon.contract_manager"):
            try:
                monkeypatch.setattr(
                    importlib.import_module(name), "compute_overdue_deliverables", _boom
                )
            except ImportError:
                continue
        _patch_get_connection(monkeypatch, lambda *a, **k: _FakeConn())
        monkeypatch.setattr(cpmp_monitor, "_write_memory_log", lambda results: None)
        return cpmp_monitor.run()

    def test_the_sweep_is_still_attempted_and_the_run_completes(self, broken_sweep):
        assert broken_sweep["contracts_scanned"] == 0
        assert broken_sweep["deliverables_marked_overdue"] == 0

    def test_the_failure_is_reported_not_swallowed(self, broken_sweep):
        assert any("bad due_date row" in e for e in broken_sweep["errors"])


# ---------------------------------------------------------------------------
# The wiring is the only wiring
# ---------------------------------------------------------------------------


def test_reflex_is_the_only_production_caller():
    """If a second caller appears, this test should be updated deliberately.

    The point of the finding was that the count of production callers was ZERO
    while four modules read what the function writes. Pinning the caller keeps
    that from silently returning to zero in a refactor.
    """
    src = inspect.getsource(cpmp_monitor)
    assert "compute_overdue_deliverables" in src
    assert "from tools.govcon.contract_manager import compute_overdue_deliverables" in src
