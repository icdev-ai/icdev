# CUI // SP-CTI
"""The ICDEV record for Omarchy's agents panel (omx-panel-01).

Every source is injected: the board summary, the spend ledger, the adapter pick
and its guard verdict. The two properties under test are the CONTRACT (the keys
the upstream collectors print) and HONESTY (an unreadable database yields
``stale: true`` and nulls -- never a zero the database did not say).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from tools.cli import omarchy_panel as panel

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


def _summary(_params):
    return {"lanes": {"in_progress": 3, "backlog": 12, "done": 40}, "total": 55}


def _totals(window_days):
    assert 0 < window_days <= 1.0
    return {
        "a-01": {"rows": 2, "cost_usd": 1.25, "input_tokens": 1000, "output_tokens": 200,
                 "priced_rows": 2, "unpriced_rows": 0},
        "a-02": {"rows": 1, "cost_usd": None, "input_tokens": 500, "output_tokens": 50,
                 "priced_rows": 0, "unpriced_rows": 1},
    }


def _pick():
    return SimpleNamespace(name="opencode_cli")


def _verdict(name):
    return SimpleNamespace(guarded=True, reason=f"{name}: guard_wired=present")


def _down(*_a, **_k):
    raise ConnectionError("could not connect to server")


def _collect(**over):
    kw = dict(summary_fn=_summary, totals_fn=_totals, pick_fn=_pick, verdict_fn=_verdict)
    kw.update(over)
    return panel.collect(NOW, **kw)


def test_record_matches_the_contract_with_real_counts():
    rec = _collect()
    for key in panel.CONTRACT_KEYS:
        assert key in rec, key
    assert rec["id"] == "icdev" and rec["schemaVersion"] == 1
    assert rec["ready"] is True and rec["tierLabel"]  # what makes the panel show it
    assert isinstance(rec["limits"], list)
    assert rec["stale"] is False and rec["authHelpText"] == ""
    assert rec["icdev"]["kanban"] == {"in_progress": 3, "backlog": 12, "total": 55}
    spend = rec["icdev"]["spendToday"]
    # The unpriced task adds NO dollars, and is counted apart.
    assert spend["cost_usd"] == 1.25 and spend["unpriced_dispatches"] == 1
    assert spend["dispatches"] == 3 and spend["tokens"] == 1750
    assert rec["tierLabel"] == "opencode_cli · guard wired"
    assert "3 in progress" in rec["usageStatusText"] and "$1.25 today" in rec["usageStatusText"]
    assert "+1 unpriced" in rec["usageStatusText"]
    json.dumps(rec)  # serialisable


def test_db_down_is_stale_not_zeros():
    rec = _collect(summary_fn=_down, totals_fn=_down)
    assert rec["stale"] is True and rec["limitsStale"] is True
    assert rec["ready"] is True  # still shown, so the outage has somewhere to say so
    assert rec["usageStatusText"] == panel.STALE_TEXT and rec["authHelpText"]
    assert set(rec["icdev"]["kanban"].values()) == {None}
    assert set(rec["icdev"]["spendToday"].values()) == {None}
    assert "0" not in rec["usageStatusText"]


def test_board_summary_error_and_ledger_none_are_stale():
    """The seams' own "unreadable" spellings: an ``error`` key, and ``None``."""
    rec = _collect(summary_fn=lambda _p: {"error": "no such table", "lanes": {}, "total": 0},
                   totals_fn=lambda window_days: None)
    assert rec["stale"] is True
    assert rec["icdev"]["kanban"]["in_progress"] is None
    assert rec["icdev"]["spendToday"]["cost_usd"] is None


def test_one_source_down_is_still_stale():
    rec = _collect(totals_fn=_down)
    assert rec["stale"] is True
    assert rec["icdev"]["kanban"]["in_progress"] == 3  # what WAS read is kept


def test_no_spend_today_is_not_dollars_zero():
    rec = _collect(totals_fn=lambda window_days: {})
    assert rec["stale"] is False
    assert rec["icdev"]["spendToday"]["cost_usd"] is None
    assert "no attributed spend today" in rec["usageStatusText"]
    assert "$0" not in rec["usageStatusText"]


def test_unguarded_and_missing_adapter_labels():
    rec = _collect(verdict_fn=lambda n: SimpleNamespace(guarded=False, reason="unconfirmed"))
    assert rec["tierLabel"] == "opencode_cli · guard NOT wired"
    rec = _collect(verdict_fn=_down)
    assert rec["tierLabel"].endswith("guard unverified")
    assert rec["icdev"]["adapter"]["guard_wired"] is None
    rec = _collect(pick_fn=_down)
    assert rec["tierLabel"] == "no harness" and rec["icdev"]["adapter"]["name"] is None


def test_write_record_is_atomic_and_where_the_panel_reads(tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    d = panel.usage_dir(env)
    assert d == tmp_path / "state" / "omarchy" / "agents" / "usage"
    target = panel.write_record(_collect(), d)
    assert target == d / "icdev.json"
    assert json.loads(target.read_text(encoding="utf-8"))["id"] == "icdev"
    assert b"\r\n" not in target.read_bytes()
    assert [p.name for p in d.iterdir()] == ["icdev.json"]  # no temp file left


def test_usage_dir_defaults_under_home(tmp_path):
    assert panel.usage_dir({}, tmp_path) == tmp_path / ".local" / "state" / "omarchy" / "agents" / "usage"


def test_main_prints_one_json_record_and_writes(monkeypatch, tmp_path, capsys):
    rec = _collect()
    monkeypatch.setattr(panel, "collect", lambda: rec)
    assert panel.main(["--force", "--limits-only", "--write", "--usage-dir", str(tmp_path)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["id"] == "icdev"
    assert json.loads((tmp_path / "icdev.json").read_text(encoding="utf-8")) == printed
