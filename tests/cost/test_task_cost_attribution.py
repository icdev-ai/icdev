# CUI // SP-CTI
"""xrv-cost-02 -- per-task cost attribution at reap, and the spend-that-shipped join.

Every database here is a ``tmp_path`` SQLite file reached through
``get_connection(db_path=)``; the ambient board is never touched. Every git and
forge probe is a stub handed in by keyword, so the verdicts are deterministic.
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.agents.adapters.claude_cli import _parse_cli_json  # noqa: E402
from tools.cost import session_cost as sc  # noqa: E402
from tools.cost import task_attribution as ta  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

KANBAN_PY = REPO_ROOT / "tools" / "genesis" / "reflexes" / "kanban.py"
ATTRIB_PY = REPO_ROOT / "tools" / "cost" / "task_attribution.py"


def envelope(*, cost=14.11, model_usage=True, tokens=True, session="sess-1", is_error=False):
    usage = {
        "input_tokens": 1768 if tokens else 0,
        "cache_creation_input_tokens": 317747 if tokens else 0,
        "cache_read_input_tokens": 15726990 if tokens else 0,
        "output_tokens": 76075 if tokens else 0,
        "output_tokens_details": {"thinking_tokens": 31209 if tokens else 0},
    }
    payload = {
        "type": "result", "subtype": "success", "is_error": is_error, "num_turns": 40,
        "duration_ms": 950000, "duration_api_ms": 943557, "session_id": session,
        "total_cost_usd": cost, "usage": usage, "result": "done",
    }
    if model_usage:
        payload["modelUsage"] = {
            "claude-haiku-4-5-20251001": {"inputTokens": 2115, "outputTokens": 18,
                                          "costUSD": 0.002205},
            "claude-fable-5-1": {"inputTokens": 1768, "outputTokens": 76075,
                                 "costUSD": 14.1081175},
        }
    return json.dumps(payload)


def count_rows(db_path: Path, task_id: str | None = None):
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "agent_token_usage" not in tables:
            return 0, []
        sql = "SELECT task_id, cost_estimate_usd, model_id, input_tokens, output_tokens, " \
              "thinking_tokens, duration_ms, agent_id, project_id FROM agent_token_usage"
        rows = conn.execute(sql + (" WHERE task_id = ?" if task_id else ""),
                            (task_id,) if task_id else ()).fetchall()
        return len(rows), rows
    finally:
        conn.close()


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "ledger.db"


# ── the envelope parser gained two keys ───────────────────────────────────


def test_parser_names_the_dominant_model_and_thinking_tokens():
    _text, structured = _parse_cli_json(envelope())
    assert structured["model"] == "claude-fable-5-1"
    assert structured["thinking_tokens"] == 31209
    assert structured["total_cost_usd"] == 14.11


def test_parser_reports_blank_model_when_the_map_is_absent():
    _text, structured = _parse_cli_json(envelope(model_usage=False))
    assert structured["model"] == ""
    assert structured["thinking_tokens"] == 31209


# ── WRITE half: exactly one row, task_id set ──────────────────────────────


def test_fixture_log_produces_exactly_one_row_with_task_id(tmp_path, ledger):
    log = tmp_path / "xrv-t-01.log"
    log.write_text("some stderr warning\n" + envelope() + "\n", encoding="utf-8")
    result = ta.record_task_cost("xrv-t-01", log, project_id="xrv", db_path=ledger)
    assert result["recorded"] is True and result["reason"] == ta.REASON_RECORDED
    n, rows = count_rows(ledger, "xrv-t-01")
    assert n == 1
    (task_id, cost, model, inp, out, think, dur, agent, project) = rows[0]
    assert task_id == "xrv-t-01"
    assert cost == pytest.approx(14.11)
    assert model == "claude-fable-5-1"
    assert inp == 1768 + 317747 + 15726990      # the one input column carries the whole prompt
    assert out == 76075 and think == 31209 and dur == 943557
    assert agent == ta.AGENT_ID and project == "xrv"
    # And the same row is what the READ half sees through get_connection(db_path=).
    with get_connection(db_path=str(ledger)) as conn:
        got = conn.execute(
            "SELECT task_id, cost_estimate_usd FROM agent_token_usage WHERE task_id = %s",
            ("xrv-t-01",)).fetchone()
    assert got["task_id"] == "xrv-t-01"


def test_log_without_envelope_produces_no_row(tmp_path, ledger):
    log = tmp_path / "xrv-t-02.log"
    log.write_text("Task killed at 900s\npartial output without a JSON line\n", encoding="utf-8")
    result = ta.record_task_cost("xrv-t-02", log, db_path=ledger)
    assert result == {"recorded": False, "reason": ta.REASON_NO_ENVELOPE, "task_id": "xrv-t-02"}
    assert count_rows(ledger)[0] == 0


def test_missing_log_produces_no_row(tmp_path, ledger):
    result = ta.record_task_cost("xrv-t-03", tmp_path / "absent.log", db_path=ledger)
    assert result["reason"] == ta.REASON_NO_LOG and result["recorded"] is False
    assert count_rows(ledger)[0] == 0


def test_envelope_with_no_usage_records_nothing_not_a_zero_row(tmp_path, ledger):
    log = tmp_path / "xrv-t-04.log"
    log.write_text(envelope(cost=0.0, tokens=False, is_error=True) + "\n", encoding="utf-8")
    result = ta.record_task_cost("xrv-t-04", log, db_path=ledger)
    assert result["reason"] == ta.REASON_NO_USAGE and result["recorded"] is False
    assert count_rows(ledger)[0] == 0


def test_blank_model_falls_back_to_the_cli_label(tmp_path, ledger):
    log = tmp_path / "xrv-t-05.log"
    log.write_text(envelope(model_usage=False) + "\n", encoding="utf-8")
    result = ta.record_task_cost("xrv-t-05", log, db_path=ledger)
    assert result["model_id"] == ta.FALLBACK_MODEL_ID
    assert count_rows(ledger, "xrv-t-05")[1][0][2] == "claude-cli"


def test_writer_failure_propagates_to_the_caller(tmp_path, ledger):
    """The reflex is what guards; the module must not swallow a failed write."""
    log = tmp_path / "xrv-t-06.log"
    log.write_text(envelope() + "\n", encoding="utf-8")

    def boom(**_kw):
        raise RuntimeError("ledger down")

    with pytest.raises(RuntimeError):
        ta.record_task_cost("xrv-t-06", log, db_path=ledger, log_usage_fn=boom)


# ── READ half: the closed verdict set ─────────────────────────────────────


def _landed(checked=True, landed=False, reason="", confidence=None, commits=None):
    return {"checked": checked, "landed": landed, "reason": reason,
            "confidence": confidence, "commits": commits or []}


def test_every_verdict_is_in_the_closed_set_and_unmeasurable_never_folds():
    cases = {
        "unchecked": (dict(landed=_landed(checked=False, reason="ref not resolvable"),
                           status="done", reverted=False, pr_state=None), "unmeasurable"),
        "shipped": (dict(landed=_landed(landed=True, confidence="merge_ref"),
                         status="done", reverted=False, pr_state="MERGED"), "shipped"),
        "reverted": (dict(landed=_landed(landed=True, confidence="merge_ref"),
                          status="done", reverted=True, pr_state="MERGED"), "reverted"),
        "revert_probe_failed": (dict(landed=_landed(landed=True, confidence="subject"),
                                     status="done", reverted=None, pr_state=None), "unmeasurable"),
        "abandoned": (dict(landed=_landed(), status="cancelled", reverted=False,
                           pr_state="CLOSED"), "abandoned"),
        "abandoned_done_nothing_landed": (dict(landed=_landed(), status="done", reverted=False,
                                               pr_state=ta.PR_STATE_NOT_CONSULTED), "abandoned"),
        "in_flight": (dict(landed=_landed(), status="pr_opened", reverted=False,
                           pr_state="OPEN"), "in_flight"),
        "in_flight_no_forge": (dict(landed=_landed(), status="in_progress", reverted=False,
                                    pr_state=ta.PR_STATE_NOT_CONSULTED), "in_flight"),
        "forge_disagrees_with_git": (dict(landed=_landed(), status="done", reverted=False,
                                          pr_state="MERGED"), "unmeasurable"),
        "not_on_board": (dict(landed=_landed(), status=None, reverted=False,
                              pr_state=None), "unmeasurable"),
        "board_unreadable": (dict(landed=_landed(landed=True), status="done", reverted=False,
                                  pr_state=None, board_readable=False), "unmeasurable"),
    }
    for name, (kwargs, expected) in cases.items():
        got = ta.classify_outcome(**kwargs)
        assert got["verdict"] == expected, (name, got)
        assert got["verdict"] in ta.VERDICTS
        assert got["reasons"], name


@pytest.fixture
def board(tmp_path):
    """A board with kanban_tasks and three tasks; the ledger lives in the same file."""
    db = tmp_path / "board.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'backlog',
            project_id TEXT DEFAULT 'default', executor_url TEXT
        );
        INSERT INTO kanban_tasks (id, title, status, project_id, executor_url) VALUES
            ('xrv-b-01', 'shipped one', 'done', 'xrv', 'https://github.com/o/r/pull/10'),
            ('xrv-b-02', 'abandoned one', 'cancelled', 'xrv', ''),
            ('xrv-b-03', 'in flight', 'in_progress', 'xrv', 'https://github.com/o/r/pull/12');
    """)
    conn.commit()
    conn.close()
    return db


def _seed(db, task_id, cost, tmp_path):
    log = tmp_path / f"{task_id}.log"
    log.write_text(envelope(cost=cost, session=f"s-{task_id}") + "\n", encoding="utf-8")
    assert ta.record_task_cost(task_id, log, db_path=db)["recorded"]


def test_join_is_unmeasurable_when_landed_check_reports_unchecked(board, tmp_path):
    _seed(board, "xrv-b-01", 3.0, tmp_path)

    def landed_stub(ids, repo_root=None, branch=None, ref=None):
        return {t: _landed(checked=False, reason="ref origin/main not resolvable") for t in ids}

    report = ta.task_report("xrv-b-01", db_path=board, consult_forge=False,
                            landed_fn=landed_stub)
    assert report["verdict"] == "unmeasurable"
    assert report["reasons"] == ["landed_check:ref origin/main not resolvable"]
    assert report["cost_usd"] == pytest.approx(3.0)   # the cost is still measured
    assert report["cost_state"] == "measured" and report["dispatches"] == 1


def test_task_report_carries_cost_and_a_closed_set_verdict(board, tmp_path):
    _seed(board, "xrv-b-01", 3.0, tmp_path)
    _seed(board, "xrv-b-01", 2.5, tmp_path)   # a second dispatch of the same card

    def landed_stub(ids, repo_root=None, branch=None, ref=None):
        return {t: _landed(landed=True, confidence="merge_ref",
                           commits=[{"sha": "abc123def", "subject": "Merge pull request #10",
                                     "evidence": "merge_ref"}]) for t in ids}

    calls = {}

    def revert_stub(shas, repo_root=None, target="", task_ids=None):
        calls["shas"] = shas
        return {t: False for t in shas}

    report = ta.task_report("xrv-b-01", db_path=board, consult_forge=True,
                            landed_fn=landed_stub, revert_fn=revert_stub,
                            pr_state_fn=lambda n: "MERGED")
    assert report["verdict"] == "shipped" and report["verdict"] in ta.VERDICTS
    assert report["cost_usd"] == pytest.approx(5.5) and report["dispatches"] == 2
    assert report["pr_state"] == "MERGED" and report["pr_url"].endswith("/pull/10")
    assert calls["shas"] == {"xrv-b-01": ["abc123def"]}


def test_task_with_no_rows_still_gets_a_verdict_and_cost_none(board):
    report = ta.task_report("xrv-b-03", db_path=board, consult_forge=False,
                            landed_fn=lambda ids, **kw: {t: _landed() for t in ids})
    assert report["cost_usd"] is None and report["cost_state"] == "no_rows"
    assert report["verdict"] == "in_flight"


def test_survey_on_an_empty_board_is_unmeasurable_never_zero_dollars(board):
    report = ta.survey_by_verdict(db_path=board)
    assert report["state"] == "unmeasurable"
    assert report["reason"] == "no_attributed_rows"
    assert report["total_cost_usd"] is None
    assert report["by_verdict"] is None
    assert report["shipped_cost_share_pct"] is None


def test_survey_sums_cost_per_verdict_and_rates_are_none_over_nothing_measured(board, tmp_path):
    _seed(board, "xrv-b-01", 4.0, tmp_path)
    _seed(board, "xrv-b-02", 1.0, tmp_path)
    _seed(board, "xrv-b-03", 2.0, tmp_path)

    def landed_stub(ids, repo_root=None, branch=None, ref=None):
        out = {}
        for t in ids:
            if t == "xrv-b-01":
                out[t] = _landed(landed=True, confidence="merge_ref",
                                 commits=[{"sha": "abc123def", "evidence": "merge_ref",
                                           "subject": "Merge"}])
            else:
                out[t] = _landed()
        return out

    report = ta.survey_by_verdict(db_path=board, landed_fn=landed_stub,
                                  revert_fn=lambda shas, **kw: {t: False for t in shas})
    assert report["state"] == "measured" and report["tasks"] == 3
    bv = report["by_verdict"]
    assert set(bv) == set(ta.VERDICTS)
    assert bv["shipped"]["cost_usd"] == pytest.approx(4.0) and bv["shipped"]["tasks"] == 1
    assert bv["abandoned"]["cost_usd"] == pytest.approx(1.0)
    assert bv["in_flight"]["cost_usd"] == pytest.approx(2.0)
    assert bv["reverted"]["cost_usd"] is None and bv["reverted"]["tasks"] == 0
    assert bv["unmeasurable"]["cost_usd"] is None
    assert report["total_cost_usd"] == pytest.approx(7.0)
    assert report["shipped_cost_share_pct"] == pytest.approx(57.1)   # floored, not rounded
    assert report["forge_consulted"] is False

    # Every task unmeasurable: the share is None, never 0.0.
    unmeasured = ta.survey_by_verdict(
        db_path=board, landed_fn=lambda ids, **kw: {t: _landed(checked=False, reason="x")
                                                    for t in ids})
    assert unmeasured["by_verdict"]["unmeasurable"]["tasks"] == 3
    assert unmeasured["shipped_cost_share_pct"] is None
    assert unmeasured["measured_cost_usd"] == 0.0


def test_pct_floors_and_refuses_an_empty_denominator():
    assert ta._pct(1, 0) is None
    assert ta._pct(0, 0) is None
    assert ta._pct(9996, 10000) == 99.9
    assert ta._pct(1, 1) == 100.0


# ── the CLI ───────────────────────────────────────────────────────────────


def test_cli_task_prints_json_with_a_closed_set_verdict(board, tmp_path, capsys, monkeypatch):
    _seed(board, "xrv-b-01", 3.0, tmp_path)
    monkeypatch.setattr(ta, "check_landed_bulk",
                        lambda ids, repo_root=None, branch=None, ref=None:
                        {t: _landed(checked=False, reason="stubbed") for t in ids})
    rc = sc.main(["--task", "xrv-b-01", "--json", "--db-path", str(board), "--no-forge"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == "xrv-b-01"
    assert payload["cost_usd"] == pytest.approx(3.0)
    assert payload["verdict"] in ta.VERDICTS and payload["verdict"] == "unmeasurable"


def test_cli_survey_by_verdict_on_an_empty_fixture_board_reports_unmeasurable(board, capsys):
    rc = sc.main(["--survey", "--by-verdict", "--json", "--db-path", str(board)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unmeasurable"
    assert payload["total_cost_usd"] is None and payload["by_verdict"] is None


def test_cli_survey_by_verdict_exits_2_when_the_report_cannot_be_produced(board, capsys,
                                                                            monkeypatch):
    def boom(**_kw):
        raise RuntimeError("no ledger")

    monkeypatch.setattr(ta, "survey_by_verdict", boom)
    rc = sc.main(["--survey", "--by-verdict", "--json", "--db-path", str(board)])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["state"] == "unmeasurable"


# ── structure: the reflex guards the call; the writer is called with task_id ──


def _calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if fname == name:
                yield node


def test_reflex_completion_path_wraps_attribution_in_try_except():
    tree = ast.parse(KANBAN_PY.read_text(encoding="utf-8"))
    check = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_check_completed")
    guarded = []
    for node in ast.walk(check):
        if isinstance(node, ast.Try):
            for call in _calls(node, "record_task_cost"):
                guarded.append(call)
            assert not any(isinstance(h.type, ast.Name) and h.type.id == "BaseException"
                           for h in node.handlers)
    assert guarded, "record_task_cost is not called inside a try in _check_completed"
    every_call = list(_calls(check, "record_task_cost"))
    assert len(every_call) == len(guarded), "an unguarded record_task_cost call exists"
    kw = {k.arg for k in guarded[0].keywords}
    assert "project_id" in kw


def test_attribution_calls_log_usage_with_task_id_and_the_adapters_parser():
    tree = ast.parse(ATTRIB_PY.read_text(encoding="utf-8"))
    writes = [c for c in _calls(tree, "writer")]
    assert writes, "record_task_cost must call the writer"
    src = ATTRIB_PY.read_text(encoding="utf-8")
    assert "task_id=str(task_id)" in src
    assert "cost_estimate_usd=cost" in src
    # ONE parser: the adapter's own, never a second json.loads over the envelope.
    parse_calls = list(_calls(tree, "_parse_cli_json"))
    assert parse_calls, "read_envelope must parse through claude_cli._parse_cli_json"
    loads = [c for c in _calls(tree, "loads")]
    assert not loads, "no second envelope parser"
    # No board mutation: the module never UPDATEs, INSERTs or DELETEs itself.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = node.value.upper().lstrip()
            assert not upper.startswith(("UPDATE ", "INSERT ", "DELETE ")), node.value

