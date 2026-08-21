# CUI // SP-CTI
"""A claim verifier nobody runs reads as INERT, never as clean (autonomy-act-01).

The claim verifier (rem-hyg-17) was not registered with capability_consumption
-- the gate that exists to catch "declared but never consumed". These tests pin
the `verified_claim` class: declared units are the registered claims,
consumption is a MEASURED verdict on a daemon-dispatched run read back out of
genesis_audit.details, and an `unmeasurable` verdict is attempted-and-measured-
nothing, reported apart and never counted.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness.claim_verifier import Claim  # noqa: E402

capcon = importlib.import_module("tools.awareness.capability_consumption")
claims_mod = importlib.import_module("tools.awareness.claims")

NOW = datetime.now(timezone.utc)
IN_WINDOW = (NOW - timedelta(days=1)).isoformat()
OUT_OF_WINDOW = (NOW - timedelta(days=400)).isoformat()
SINCE = NOW - timedelta(days=30)

DDL = """CREATE TABLE genesis_audit (
    id TEXT PRIMARY KEY, event_type TEXT NOT NULL, reflex_name TEXT,
    risk_tier TEXT, details TEXT, success INTEGER, duration_ms INTEGER,
    metric_name TEXT, metric_value REAL, gkp_id TEXT, created_at TEXT NOT NULL)"""


def _claim(claim_id):
    return Claim(claim_id=claim_id, description="d" * 50,
                 reported=lambda: 1, derived=lambda: 1)


@pytest.fixture(autouse=True)
def three_claims(monkeypatch):
    monkeypatch.setattr(claims_mod, "REGISTRY", [_claim("a"), _claim("b"), _claim("c")])


@pytest.fixture
def conn_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    made = []

    def _make(rows=(), create_table=True, name="va.db"):
        path = tmp_path / name
        raw = sqlite3.connect(str(path))
        try:
            if create_table:
                raw.execute(DDL)
                for i, (reflex_name, details, created_at) in enumerate(rows):
                    raw.execute(
                        "INSERT INTO genesis_audit (id, event_type, reflex_name, details, created_at) "
                        "VALUES (?, 'genesis.reflex.completed', ?, ?, ?)",
                        (f"r{i}", reflex_name, details, created_at),
                    )
            raw.commit()
        finally:
            raw.close()
        from tools.db.storage import get_connection

        conn = get_connection(db_path=str(path))
        made.append(conn)
        return conn

    yield _make
    for c in made:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def _probe(conn, since=SINCE):
    return capcon.probe_verified_claim(conn, since, 0, 40)


def _details(verdicts, **extra):
    return json.dumps({"status": "ok", "verdicts": verdicts, **extra})


# --------------------------------------------------------------------------- #
def test_the_class_is_registered_and_configured():
    """Absent from PROBES or the yaml it is measured by nothing -- the defect."""
    assert "verified_claim" in capcon.PROBES
    cfg = capcon.load_config()
    assert (cfg.get("classes") or {}).get("verified_claim", {}).get("enabled") is True
    assert any(c.get("capability_class") == "verified_claim"
               for c in cfg.get("known_inert_cases") or [])


def test_a_verifier_nobody_has_run_is_inert_not_clean(conn_factory):
    """The headline: table present, zero rows -> measured, every claim inert."""
    res = _probe(conn_factory())
    assert res.telemetry_available is True
    assert res.declared == 3
    assert res.consumed == 0 and res.inert == 3
    assert res.inert_units == ["a", "b", "c"]
    assert res.extra["cycles_in_window"] == 0


def test_a_measured_verdict_is_consumption_and_unmeasurable_is_not(conn_factory):
    rows = [(capcon.CLAIM_VERIFIER_REFLEX,
             _details({"a": "agrees", "b": "disagrees", "c": "unmeasurable"}), IN_WINDOW)]
    res = _probe(conn_factory(rows))
    assert res.consumed == 2 and res.inert == 1
    assert res.inert_units == ["c"]
    assert res.events == 2
    assert res.extra["unmeasurable_events"] == {"c": 1}
    assert res.extra["attempted_never_measured"] == ["c"], (
        "reached-and-broken must not read the same as never reached"
    )
    assert res.extra["cycles_in_window"] == 1


def test_a_disagreement_counts_as_consumption_the_same_as_an_agreement(conn_factory):
    """Consumption is 'the claim was VERIFIED', not 'the claim was fine'."""
    rows = [(capcon.CLAIM_VERIFIER_REFLEX, _details({"a": "disagrees"}), IN_WINDOW)]
    res = _probe(conn_factory(rows))
    assert res.consumed == 1 and "a" not in res.inert_units


def test_rows_outside_the_window_do_not_count(conn_factory):
    rows = [(capcon.CLAIM_VERIFIER_REFLEX, _details({"a": "agrees"}), OUT_OF_WINDOW)]
    conn = conn_factory(rows)
    assert _probe(conn).consumed == 0
    # ...but a lifetime window sees it -- the split the liveness gate relies on.
    assert _probe(conn, since=NOW - timedelta(days=36500)).consumed == 1


def test_another_reflexes_rows_are_never_read_as_verification(conn_factory):
    rows = [("cache_regression_reflex", _details({"a": "agrees"}), IN_WINDOW)]
    res = _probe(conn_factory(rows))
    assert res.consumed == 0 and res.extra["cycles_in_window"] == 0


def test_malformed_details_are_counted_not_silently_skipped(conn_factory):
    rows = [
        (capcon.CLAIM_VERIFIER_REFLEX, "{not json", IN_WINDOW),
        (capcon.CLAIM_VERIFIER_REFLEX, json.dumps({"status": "ok"}), IN_WINDOW),
        (capcon.CLAIM_VERIFIER_REFLEX, _details({"a": "agrees"}), IN_WINDOW),
    ]
    res = _probe(conn_factory(rows))
    assert res.consumed == 1
    assert res.extra["unparseable_rows"] == 2
    assert res.extra["cycles_in_window"] == 1


def test_a_verdict_for_an_unregistered_claim_is_surfaced_not_dropped(conn_factory):
    rows = [(capcon.CLAIM_VERIFIER_REFLEX, _details({"a": "agrees", "zz": "agrees"}), IN_WINDOW)]
    res = _probe(conn_factory(rows))
    assert res.declared == 3
    assert res.extra["undeclared_units_observed"] == ["zz"]


def test_a_missing_audit_table_is_unmeasurable_never_zero(conn_factory):
    res = _probe(conn_factory(create_table=False))
    assert res.telemetry_available is False
    assert "genesis_audit" in (res.unmeasured_reason or "")
    assert res.declared == 0 and res.inert == 0


def test_the_declared_units_are_the_live_registry(monkeypatch, conn_factory):
    """Declared must track REGISTRY, not a frozen list -- a new claim must be
    able to show up inert on its first day."""
    monkeypatch.setattr(claims_mod, "REGISTRY", [_claim("only")])
    res = _probe(conn_factory())
    assert res.declared == 1 and res.inert_units == ["only"]


def test_the_probe_cross_references_the_dispatcher(conn_factory):
    res = _probe(conn_factory())
    assert res.extra["reflex_registered"] is True, (
        "a claim can only be consumed if the reflex that verifies it is dispatchable"
    )


def test_collect_reports_the_class_measurable_on_an_empty_audit_table(conn_factory):
    report = capcon.collect(conn=conn_factory(), config={
        "window_days": 30, "inert_threshold": 0, "max_listed_units": 40,
        "classes": {"verified_claim": {"enabled": True}},
    }, only=["verified_claim"])
    cls = report["classes"][0]
    assert cls["telemetry_available"] is True
    assert cls["declared"] == 3 and cls["consumed"] == 0
    assert report["totals"]["fully_inert_classes"] == ["verified_claim"]
