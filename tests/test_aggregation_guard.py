#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the Aggregation Guard / mosaic-effect detector (prop-sec-03..06, prop-vv-01 slice).

Covers: rule co-occurrence firing/non-firing, match spec (all / at_least N),
derived classification lattice, guard_result action decisions (derive/warn/
block), and the append-only aggregation_events audit write.
"""

import importlib
import sqlite3
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Temporary SQLite DB with the aggregation_events + document_aggregation_findings tables."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE aggregation_events (
            id TEXT PRIMARY KEY,
            occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
            user_id TEXT,
            tenant_id TEXT,
            surface TEXT,
            rule_name TEXT,
            derived_classification TEXT NOT NULL,
            surface_ceiling TEXT,
            action TEXT NOT NULL DEFAULT 'derive' CHECK (action IN ('derive', 'warn', 'block')),
            element_summary TEXT,
            classification TEXT NOT NULL DEFAULT 'CUI'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE document_aggregation_findings (
            id TEXT PRIMARY KEY,
            surface TEXT NOT NULL,
            document_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            derived_classification TEXT NOT NULL,
            matched_elements TEXT,
            content_signature TEXT NOT NULL,
            resolution TEXT CHECK (resolution IN ('override')),
            resolved_by TEXT,
            resolved_at TEXT,
            resolution_comment TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.row_factory = sqlite3.Row
    conn.commit()

    class _Wrapper:
        """Adapts sqlite3 %s-less positional params to the %s style used by tools.db.storage."""

        def __init__(self, raw):
            self._raw = raw

        def execute(self, sql, params=None):
            sql = sql.replace("%s", "?")
            cur = self._raw.execute(sql, params or [])
            return cur

        def commit(self):
            self._raw.commit()

        def close(self):
            self._raw.close()

    wrapped = _Wrapper(conn)
    # tools.* vs icdev.tools.* are distinct module objects for from-imports —
    # patch via importlib + setattr, not pytest's string-path form (which
    # resolves through the icdev.tools shim and can't find submodule attrs).
    storage_module = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage_module, "get_connection", lambda: wrapped)
    yield wrapped
    conn.close()


# ---------------------------------------------------------------------------
# Rule co-occurrence
# ---------------------------------------------------------------------------


def test_single_condition_alone_does_not_fire():
    from tools.security.aggregation_guard import evaluate_rules

    result_set = [{"element_id": "s1", "entities": [{"entity_type": "DOLLAR_AMOUNT", "score": 0.8}]}]
    assert evaluate_rules(result_set) == []


def test_all_conditions_across_elements_fires_scg_agg_003():
    from tools.security.aggregation_guard import evaluate_rules

    result_set = [
        {"element_id": "s1", "entities": [{"entity_type": "PROTECTED_ORG", "score": 0.9}]},
        {"element_id": "s2", "entities": [{"entity_type": "DOLLAR_AMOUNT", "score": 0.8}]},
        {"element_id": "s3", "entities": [{"entity_type": "DOD_CONTRACT", "score": 0.9}]},
    ]
    fired = evaluate_rules(result_set)
    ids = [r["rule_id"] for r in fired]
    assert "SCG-AGG-003" in ids
    rule = next(r for r in fired if r["rule_id"] == "SCG-AGG-003")
    assert set(rule["matched_elements"]) == {"s1", "s2", "s3"}
    assert rule["action"] == "warn"
    assert rule["derive"] == "CUI"


def test_at_least_n_match_spec():
    from tools.security.aggregation_guard import evaluate_rules

    # SCG-AGG-002 requires at_least 2 of {AGENCY_NAME, PROGRAM_NAME, delivery_date regex}
    two_of_three = [
        {"element_id": "a", "entities": [{"entity_type": "AGENCY_NAME", "score": 0.8}]},
        {"element_id": "b", "entities": [{"entity_type": "PROGRAM_NAME", "score": 0.8}]},
    ]
    fired = evaluate_rules(two_of_three)
    ids = [r["rule_id"] for r in fired]
    assert "SCG-AGG-002" in ids

    one_of_three = [{"element_id": "a", "entities": [{"entity_type": "AGENCY_NAME", "score": 0.8}]}]
    fired_one = evaluate_rules(one_of_three)
    assert "SCG-AGG-002" not in [r["rule_id"] for r in fired_one]


def test_field_cooccurrence_structured_rule():
    from tools.security.aggregation_guard import evaluate_rules

    result_set = [
        {"element_id": "row1", "fields": {"capture_strategy": "..."}},
        {"element_id": "row2", "fields": {"ptw_assessment": "..."}},
        {"element_id": "row3", "fields": {"incumbent_price": "..."}},
    ]
    fired = evaluate_rules(result_set)
    assert "SCG-AGG-101" in [r["rule_id"] for r in fired]


# ---------------------------------------------------------------------------
# Derived classification lattice
# ---------------------------------------------------------------------------


def test_derived_classification_is_max_of_elements_and_fired_rules():
    from tools.security.aggregation_guard import compute_derived_classification

    elements = [{"element_id": "a", "classification": "CUI", "entities": []}]
    assert compute_derived_classification(elements) == "CUI"

    # SCG-AGG-002 derives SECRET when it fires
    elements_secret = [
        {"element_id": "a", "classification": "CUI", "entities": [{"entity_type": "AGENCY_NAME", "score": 0.8}]},
        {"element_id": "b", "classification": "CUI", "entities": [{"entity_type": "PROGRAM_NAME", "score": 0.8}]},
    ]
    assert compute_derived_classification(elements_secret) == "SECRET"


def test_ts_sci_alias_normalization():
    from tools.security.aggregation_guard import _clearance_order

    assert _clearance_order("TS") == _clearance_order("TOP SECRET")
    assert _clearance_order("TS//SCI") == _clearance_order("TOP SECRET//SCI")
    assert _clearance_order("TS//SCI") > _clearance_order("SECRET")


# ---------------------------------------------------------------------------
# guard_result — action decision + audit write
# ---------------------------------------------------------------------------


def test_guard_result_derive_when_within_ceiling(tmp_db):
    from tools.security.aggregation_guard import guard_result

    result_set = [
        {"element_id": "a", "entities": [{"entity_type": "PROTECTED_ORG", "score": 0.9}]},
        {"element_id": "b", "entities": [{"entity_type": "DOLLAR_AMOUNT", "score": 0.8}]},
        {"element_id": "c", "entities": [{"entity_type": "DOD_CONTRACT", "score": 0.9}]},
    ]
    result = guard_result(result_set, ctx={"surface_ceiling": "CUI"}, surface="test/derive")
    assert result["derived"] == "CUI"
    assert result["action"] == "derive"
    assert result["events_written"] == 1

    rows = tmp_db.execute("SELECT * FROM aggregation_events").fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "derive"


def test_guard_result_blocks_when_exceeds_ceiling(tmp_db):
    from tools.security.aggregation_guard import guard_result

    result_set = [
        {"element_id": "a", "entities": [{"entity_type": "AGENCY_NAME", "score": 0.8}]},
        {"element_id": "b", "entities": [{"entity_type": "PROGRAM_NAME", "score": 0.8}]},
    ]
    result = guard_result(result_set, ctx={"surface_ceiling": "CUI"}, surface="test/block")
    assert result["derived"] == "SECRET"
    assert result["action"] == "block"

    rows = tmp_db.execute("SELECT * FROM aggregation_events WHERE surface='test/block'").fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "block"
    assert rows[0]["rule_name"] == "SCG-AGG-002"


def test_guard_result_warn_action_does_not_block(tmp_db):
    from tools.security.aggregation_guard import guard_result

    result_set = [
        {"element_id": "a", "entities": [{"entity_type": "PROTECTED_ORG", "score": 0.9}]},
        {"element_id": "b", "entities": [{"entity_type": "DOLLAR_AMOUNT", "score": 0.8}]},
        {"element_id": "c", "entities": [{"entity_type": "DOD_CONTRACT", "score": 0.9}]},
    ]
    # SCG-AGG-003 derives CUI with action=warn; a ceiling of PUBLIC would be exceeded
    result = guard_result(result_set, ctx={"surface_ceiling": "PUBLIC"}, surface="test/warn")
    assert result["action"] == "warn"


def test_volume_throttle_no_silent_cap(tmp_db):
    from tools.security.aggregation_guard import guard_result

    result_set = [{"element_id": str(i), "entities": []} for i in range(5)]
    result = guard_result(result_set, ctx={"max_rows_per_call": 3}, surface="test/throttle")
    assert result["throttled"] is True
    assert result["throttle_reason"]  # non-empty — must explain what was withheld


# ---------------------------------------------------------------------------
# Static config check
# ---------------------------------------------------------------------------


def test_classification_aggregation_yaml_loads_and_has_expected_rules():
    from tools.security.aggregation_guard import _load_rules

    config = _load_rules()
    rule_ids = {r["id"] for r in config.get("rules", [])}
    structured_ids = {r["id"] for r in config.get("structured_rules", [])}
    assert {"SCG-AGG-001", "SCG-AGG-002", "SCG-AGG-003", "SCG-AGG-004"}.issubset(rule_ids)
    assert {"SCG-AGG-101", "SCG-AGG-102", "SCG-AGG-103"}.issubset(structured_ids)
