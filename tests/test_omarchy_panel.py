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


def _row(day, hour, model, inp, out, task="t-01"):
    return {"task_id": task, "model_id": model, "input_tokens": inp, "output_tokens": out,
            "cost_estimate_usd": 0.5, "created_at": f"2026-10-{day:02d} {hour:02d}:00:00"}


#: Five dispatches over three days and two models. NOW is 2026-10-10 15:00 UTC.
LEDGER = [
    _row(1, 9, "claude-opus", 900, 100),            # outside the 7-day window
    _row(9, 10, "claude-opus", 1000, 200),
    {**_row(9, 11, "qwen3-coder", 400, 100), "created_at": "2026-10-09T11:00:00+00:00"},
    _row(10, 8, "claude-opus", 2000, 500),
    # The other timestamp spelling, and a PG-style datetime -- both naive UTC.
    {"task_id": "t-02", "model_id": "qwen3-coder", "input_tokens": 300, "output_tokens": 50,
     "cost_estimate_usd": 0.0, "created_at": datetime(2026, 10, 10, 12, 30)},
]


def _collect(**over):
    kw = dict(summary_fn=_summary, totals_fn=_totals, pick_fn=_pick, verdict_fn=_verdict,
              rows_fn=lambda: LEDGER, tz=timezone.utc)
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


# --------------------------------------------------------------------------- #
# The local-stats block (omx-panel-04) -- what the panel's providerHasData reads.
# --------------------------------------------------------------------------- #

def _provider_has_data(p):
    """Omarchy 4 ``shell/plugins/agents/Main.qml::providerHasData``, transcribed."""
    def n(v):
        return v or 0
    return (n(p.get("totalPrompts")) > 0 or n(p.get("totalSessions")) > 0
            or n(p.get("activeDays")) > 0 or n(p.get("todayPrompts")) > 0
            or n(p.get("todaySessions")) > 0 or bool(p.get("limits")) or bool(p.get("balance")))


def test_dispatch_history_fills_the_local_stats_block():
    rec = _collect()
    assert _provider_has_data(rec)  # the panel admits the record
    assert rec["hasLocalStats"] is True and rec["hasPromptStats"] is False
    assert rec["stale"] is False
    assert rec["totalSessions"] == 5 and rec["todaySessions"] == 2
    assert rec["totalPrompts"] == 0 and rec["todayPrompts"] == 0  # no per-call rows
    assert rec["activeDays"] == 3
    assert rec["activeDates"] == ["2026-10-01", "2026-10-09", "2026-10-10"]
    assert rec["todayTotalTokens"] == 2500 + 350
    assert rec["todayTokensByModel"] == {"claude-opus": 2500, "qwen3-coder": 350}
    # Upstream shape: seven {date, messageCount} entries, oldest first, tokens.
    assert [d["date"] for d in rec["recentDays"]] == [f"2026-10-{d:02d}" for d in range(4, 11)]
    assert all(set(d) == {"date", "messageCount"} for d in rec["recentDays"])
    assert rec["recentDays"][-2]["messageCount"] == 1700
    assert rec["recentDays"][-1]["messageCount"] == 2850
    assert rec["modelUsage"] == {
        "claude-opus": {"inputTokens": 3900, "outputTokens": 800,
                        "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0},
        "qwen3-coder": {"inputTokens": 700, "outputTokens": 150,
                        "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0},
    }
    assert rec["limits"] == [] and "balance" not in rec  # nothing invented
    json.dumps(rec)


def test_today_is_counted_in_the_given_zone():
    """08:00 UTC on the 10th is still the 9th in UTC-10."""
    from datetime import timedelta
    rec = _collect(tz=timezone(timedelta(hours=-10)))
    # NOW 15:00Z = 05:00 on the 10th; 08:00Z = 22:00 on the 9th, 12:30Z = 02:30 on the 10th.
    assert rec["recentDays"][-1] == {"date": "2026-10-10", "messageCount": 350}
    assert rec["recentDays"][-2] == {"date": "2026-10-09", "messageCount": 1700 + 2500}
    assert rec["todaySessions"] == 1
    assert rec["todayTokensByModel"] == {"qwen3-coder": 350}
    assert rec["activeDates"] == ["2026-09-30", "2026-10-09", "2026-10-10"]


def test_empty_ledger_is_zeros_and_stays_hidden():
    rec = _collect(rows_fn=lambda: [])
    assert rec["stale"] is False and rec["hasLocalStats"] is True
    for key in ("todayPrompts", "todaySessions", "todayTotalTokens", "totalPrompts",
                "totalSessions", "activeDays"):
        assert rec[key] == 0, key
    assert rec["activeDates"] == [] and rec["modelUsage"] == {}
    assert rec["todayTokensByModel"] == {}
    assert len(rec["recentDays"]) == 7 and {d["messageCount"] for d in rec["recentDays"]} == {0}
    # Never dispatched here -> hidden. Omarchy's design, not forced visible.
    assert not _provider_has_data(rec)


def test_unreadable_ledger_is_stale_and_null():
    for rows_fn in (_down, lambda: None):
        rec = _collect(rows_fn=rows_fn)
        assert rec["stale"] is True and rec["hasLocalStats"] is False
        for key in panel.UNREADABLE_LOCAL_STATS:
            assert rec[key] is None, key
        assert rec["usageStatusText"] == panel.STALE_TEXT
