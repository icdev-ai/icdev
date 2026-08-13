# CUI // SP-CTI
"""cpmp_monitor must satisfy the GenesisDaemon reflex contract and read the
key names detect_noncompliance actually returns.

Two independent defects, both silent:

1. `run()` returned its bare `results` dict, with no `success` / `metric_value`
   / `details`. GenesisDaemon._run_reflex_impl_inner does
   `success = result.get("success", False)`, so EVERY run — including a clean
   sweep with nothing to report — was scored a failure and the metric was read
   as 0.0 instead of cards_created. The live state row showed 11 runs, 0
   successes, 11 failures and `circuit_breaker_open = 1`: the reflex had been
   switched off entirely without anything going red.

2. Pass 3 read `nc.get("noncompliance")` and `finding["issue_type"]` /
   `finding["subcontractor_name"]`. detect_noncompliance returns its list under
   `findings`, keyed `category` / `company_name` / `sub_id`. The list was
   therefore always empty and no [SUBCON] card has ever been filed — 0 on the
   board against 14 [CPMP] cards from the separate pmo_ai_advisor path.

The dedup_key was keyed off the same two phantom fields, so it evaluated to a
constant per contract; had the list ever been non-empty, one contract's
flowdown, cybersecurity and cmmc findings would have collapsed into one card.
"""
import importlib
import inspect

import pytest

DAEMON_CONTRACT_KEYS = ("success", "metric_value", "details")

# Keys detect_noncompliance actually emits, and the phantom ones it never has.
REAL_FINDING_KEYS = ("category", "company_name", "sub_id", "severity", "description")
PHANTOM_FINDING_KEYS = ("issue_type", "subcontractor_name")


@pytest.fixture
def monitor():
    return importlib.import_module("tools.genesis.reflexes.cpmp_monitor")


class _FakeCursor:
    """Stands in for a storage connection returning zero active contracts."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.closed = False

    def set_security_context(self, _ctx):
        return None

    def execute(self, *_args, **_kwargs):
        return _FakeCursor(self._rows)

    def close(self):
        self.closed = True


# `tools.db.storage` and `icdev.tools.db.storage` are DISTINCT module objects
# (the root tools/ package is a shim), and the reflex's
# `from tools.db.storage import get_connection` resolves to the icdev one.
# Patching only the name the source mentions leaves the real connection in
# place and the test silently exercises the live database instead of the fake.
_STORAGE_MODULES = ("tools.db.storage", "icdev.tools.db.storage")


def _patch_get_connection(monkeypatch, factory):
    for name in _STORAGE_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "get_connection", factory)


# ---------------------------------------------------------------------------
# 1. The daemon contract
# ---------------------------------------------------------------------------


def test_run_returns_the_daemon_contract_keys(monitor, monkeypatch):
    """A clean sweep must return success/metric_value/details, not bare results."""
    _patch_get_connection(monkeypatch, lambda *a, **k: _FakeConn([]))
    result = monitor.run()

    for key in DAEMON_CONTRACT_KEYS:
        assert key in result, f"reflex return is missing {key!r} — daemon reads it off this dict"


def test_clean_run_is_scored_a_success_by_the_daemons_own_read(monitor, monkeypatch):
    """Reproduces the exact expression in GenesisDaemon._run_reflex_impl_inner."""
    _patch_get_connection(monkeypatch, lambda *a, **k: _FakeConn([]))
    result = monitor.run()

    # This is the daemon's line verbatim. Before the fix it evaluated to False
    # on every single run, which is what tripped the circuit breaker.
    assert result.get("success", False) is True


def test_metric_value_carries_cards_created(monitor, monkeypatch):
    """The configured success_metric is cards_created; it must actually arrive."""
    _patch_get_connection(monkeypatch, lambda *a, **k: _FakeConn([]))
    result = monitor.run()

    assert result["metric_value"] == result["details"]["cards_created"]


def test_details_carries_the_results_payload(monitor, monkeypatch):
    _patch_get_connection(monkeypatch, lambda *a, **k: _FakeConn([]))
    details = monitor.run()["details"]

    for key in ("contracts_scanned", "cards_created", "subcon_alerts", "errors"):
        assert key in details


def test_db_failure_returns_the_contract_shape_not_a_bare_error(monitor, monkeypatch):
    """The error path is read by the same daemon line and needs the same keys."""
    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    _patch_get_connection(monkeypatch, _boom)
    result = monitor.run()

    for key in DAEMON_CONTRACT_KEYS:
        assert key in result
    assert result["success"] is False
    assert "db down" in result["error"]


# ---------------------------------------------------------------------------
# 2. The detect_noncompliance key contract
# ---------------------------------------------------------------------------


def test_detect_noncompliance_returns_findings_not_noncompliance():
    """Pin the producer side, so a rename here fails loudly instead of silently."""
    tracker = importlib.import_module("tools.govcon.subcontractor_tracker")
    src = inspect.getsource(tracker.detect_noncompliance)

    assert '"findings": findings' in src
    assert '"noncompliance"' not in src


def test_findings_use_category_and_company_name():
    tracker = importlib.import_module("tools.govcon.subcontractor_tracker")
    src = inspect.getsource(tracker.detect_noncompliance)

    for key in REAL_FINDING_KEYS:
        assert f'"{key}"' in src, f"detect_noncompliance no longer emits {key!r}"
    for key in PHANTOM_FINDING_KEYS:
        assert f'"{key}"' not in src


def test_monitor_does_not_read_any_phantom_key(monitor):
    """The consumer side must not reference fields the producer never emits.

    Comments are stripped first: the fix documents the old key names in prose,
    and a bare substring check would match that and never be able to pass.
    """
    code_only = "\n".join(
        line.split("#", 1)[0] for line in inspect.getsource(monitor).splitlines()
    )

    assert 'get("noncompliance"' not in code_only
    for key in PHANTOM_FINDING_KEYS:
        assert f"'{key}'" not in code_only, f"cpmp_monitor still reads phantom field {key!r}"
        assert f'"{key}"' not in code_only, f"cpmp_monitor still reads phantom field {key!r}"


def test_monitor_reads_findings_and_keys_dedup_on_category_and_sub_id(monitor):
    src = inspect.getsource(monitor)

    assert 'nc.get("findings", [])' in src
    # dedup must vary per finding; keying on the phantom fields made it a
    # per-contract constant that would collapse distinct findings into one card.
    assert "finding.get('category','noncompliance')" in src
    assert "finding.get('sub_id')" in src
