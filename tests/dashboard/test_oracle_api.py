"""Unit tests for tools/dashboard/api/oracle.py helpers.

Covers the pure mappers (_parse_details, _row_to_item, _parse_since).
DB-integration is exercised end-to-end via the E2E hit at
tests/dashboard/e2e_oracle_history.py (requires running dashboard).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.dashboard.api import oracle as mod  # noqa: E402


# --------------------------------------------------------------------------
# _parse_details
# --------------------------------------------------------------------------
def test_parse_details_none():
    assert mod._parse_details(None) == {}


def test_parse_details_dict_passthrough():
    assert mod._parse_details({"a": 1}) == {"a": 1}


def test_parse_details_single_encoded():
    raw = json.dumps({"canvas": "security", "rule_id": "SEC-1"})
    assert mod._parse_details(raw) == {"canvas": "security", "rule_id": "SEC-1"}


def test_parse_details_double_encoded():
    """Audit rows frequently store JSON-as-string (double-encoded)."""
    inner = json.dumps({"canvas": "infra"})
    raw = json.dumps(inner)  # => '"{\\"canvas\\":\\"infra\\"}"'
    assert mod._parse_details(raw) == {"canvas": "infra"}


def test_parse_details_invalid_json():
    assert mod._parse_details("not json {") == {}


def test_parse_details_non_dict_payload():
    assert mod._parse_details(json.dumps([1, 2, 3])) == {}


# --------------------------------------------------------------------------
# _row_to_item
# --------------------------------------------------------------------------
def _row(**kw):
    base = {
        "id": 42,
        "event_type": "vulnerability_resolved",
        "action": "poam.auto_remediate.remediated",
        "actor": "auto_remediator",
        "details": json.dumps({"canvas": "security", "rule_id": "SEC-LOG-002",
                               "diff": "added log edge: host-a -> siem"}),
        "created_at": datetime(2026, 4, 12, tzinfo=timezone.utc),
    }
    base.update(kw)
    return base


def test_row_to_item_approved_on_vuln_resolved():
    item = mod._row_to_item(_row())
    assert item["status"] == "approved"
    assert item["canvas"] == "security"
    assert item["title"].startswith("SEC-LOG-002:")
    assert item["id"] == "42"
    assert "actor" in item


def test_row_to_item_pending_on_decision_made():
    item = mod._row_to_item(_row(event_type="decision_made",
                                  action="poam.auto_remediate.approved"))
    assert item["status"] == "pending"


def test_row_to_item_new_score_triggers_executed_badge():
    """UI treats status=approved + actual_new_score as 'executed'."""
    item = mod._row_to_item(_row(details=json.dumps({
        "canvas": "security", "rule_id": "R1",
        "new_avg_score": 82.5, "old_avg_score": 74.0,
    })))
    assert item["status"] == "approved"
    assert item["actual_new_score"] == 82.5
    assert item["current_score"] == 74.0


def test_row_to_item_handles_missing_details():
    item = mod._row_to_item(_row(details=None, action="kanban.opt_99_shipped"))
    assert item["status"] in ("approved", "pending")
    assert item["canvas"] == ""  # no canvas key


def test_row_to_item_title_truncated():
    long_diff = "x" * 500
    item = mod._row_to_item(_row(details=json.dumps({
        "canvas": "security", "rule_id": "R", "diff": long_diff,
    })))
    assert len(item["title"]) <= 160


def test_row_to_item_created_at_string_input():
    """Rows fetched via raw cursor may give already-stringified timestamps."""
    item = mod._row_to_item(_row(created_at="2026-04-12T00:00:00+00:00"))
    assert item["created_at"] == "2026-04-12T00:00:00+00:00"


# --------------------------------------------------------------------------
# _parse_since
# --------------------------------------------------------------------------
def test_parse_since_default_is_30d_ago():
    result = mod._parse_since(None)
    delta = datetime.now(timezone.utc) - result
    assert 29 <= delta.days <= 31


def test_parse_since_iso_z_suffix():
    result = mod._parse_since("2026-01-01T00:00:00Z")
    assert result.year == 2026 and result.month == 1 and result.day == 1


def test_parse_since_invalid_falls_back():
    result = mod._parse_since("not a date")
    delta = datetime.now(timezone.utc) - result
    assert 29 <= delta.days <= 31


# --------------------------------------------------------------------------
# high-confidence display threshold — named + distinct from the promotion gate
# --------------------------------------------------------------------------
import inspect  # noqa: E402


def test_high_conf_display_threshold_is_named_constant():
    assert mod._HIGH_CONF_DISPLAY_THRESHOLD == 0.85


def test_summary_query_uses_the_constant_not_a_literal():
    """Guards against re-hardcoding 0.85 into the SQL and re-decoupling it."""
    src = inspect.getsource(mod.oracle_summary)
    assert "_HIGH_CONF_DISPLAY_THRESHOLD" in src
    assert "confidence >= 0.85" not in src, "threshold must be parameterized, not inlined"


def test_display_threshold_is_deliberately_not_the_promotion_gate():
    """The tile answers 'strongest queued signals', the gate answers 'what
    becomes a task'. They are different questions; pinning that they differ keeps
    a future edit from collapsing one into the other."""
    import yaml

    aw = yaml.safe_load(
        (BASE_DIR / "args" / "awareness_config.yaml").read_text(encoding="utf-8")
    )
    promotion_gate = float(aw["oracle"]["promotion_threshold"])
    assert promotion_gate == 0.7
    assert mod._HIGH_CONF_DISPLAY_THRESHOLD > promotion_gate, \
        "display threshold is meant to be stricter than the promotion gate"
