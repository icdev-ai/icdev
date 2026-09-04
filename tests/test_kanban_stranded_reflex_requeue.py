# CUI // SP-CTI
"""kanban_stranded_reflex REQUEUES an orphaned ``validating`` row instead of
only reporting it (kpr-stale-05).

Measured 2026-09-03: fourteen cards (13 rmf-* plus mc-reflex-881c114a) sat in
``validating`` with no branch, no worktree and no worker. All 13 rmf cards were
parked by ``worktree-isolation-guard`` inside one 35-minute window because
``git worktree add`` timed out under concurrent gate runs. The guard is right to
park (fail-closed beats building in the shared checkout) — but ``validating``
was a dead end: the audit REPORTED ``orphan_validating`` and nothing consumed
the report. Lifetime, every exit from a guard park was a human (``manual`` 41,
``cli`` 15) or nothing at all (9). Never automation.

Hermetic: a sqlite board behind the same ``translate_sql`` the runtime uses,
with the git/worktree/lease probes injected. Each test pins one rule the card
states:

  * an orphan is requeued ONCE, through ``requeue_task``, with the guard's own
    parking reason quoted on the transition row;
  * a row parked TWICE by the same guard within 24h is CARDED, not requeued a
    third time — the recurring park is the cause the guard's comment says not
    to hide;
  * a validating row WITH a branch is untouched;
  * a cap of N leaves the (N+1)th reported as ``deferred``, never dropped;
  * the reflex returns ``unmeasurable`` (success True) when the board cannot be
    read.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._sql_compat import translating  # noqa: E402

GUARD = "worktree-isolation-guard"
PARK_REASON = (
    "worktree creation failed; refusing to build in the shared checkout "
    "(see the git worktree add failure logged above)"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            task_type TEXT,
            priority TEXT,
            status TEXT,
            updated_at TEXT,
            scheduled_at TEXT,
            branch_name TEXT,
            failure_count INTEGER DEFAULT 0,
            last_failure_at TEXT,
            last_failure_reason TEXT,
            last_heartbeat_at TEXT
        );
        CREATE TABLE kanban_status_transitions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            from_status TEXT,
            to_status TEXT,
            actor TEXT,
            reason TEXT,
            recorded_at TEXT
        );
        """
    )


@pytest.fixture()
def raw(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "board.db"))
    conn.row_factory = sqlite3.Row
    _schema(conn)
    yield conn
    conn.close()


@pytest.fixture()
def get_conn(raw):
    return lambda: translating(raw, unclosable=True)


def _task(raw, tid, *, status="validating", failure_count=2):
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        "status, updated_at, failure_count, last_failure_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, f"title {tid}", f"desc {tid}", "build", "high", status,
         _now().isoformat(), failure_count, "an earlier failure"),
    )
    raw.commit()


_seq = [0]


def _park(raw, tid, *, actor=GUARD, reason=PARK_REASON, ago=timedelta(hours=1),
          from_status="scheduled"):
    _seq[0] += 1
    raw.execute(
        "INSERT INTO kanban_status_transitions (id, task_id, from_status, "
        "to_status, actor, reason, recorded_at) VALUES (?,?,?,?,?,?,?)",
        (f"kst-{tid}-{_seq[0]}", tid, from_status, "validating", actor, reason,
         (_now() - ago).isoformat()),
    )
    raw.commit()


def _row(raw, tid) -> dict:
    return dict(raw.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone())


def _transitions(raw, tid) -> list:
    return [dict(r) for r in raw.execute(
        "SELECT * FROM kanban_status_transitions WHERE task_id = ? ORDER BY recorded_at",
        (tid,)).fetchall()]


def _findings(*ids) -> dict:
    """The shape ``stranded_audit.audit_stranded_tasks`` hands the reflex."""
    return {
        "default_branch": "main",
        "total": len(ids),
        "stranded": [],
        "orphan_validating": [{"id": i, "status": "validating", "title": f"title {i}"}
                              for i in ids],
        "clean_count": 0,
    }


def _act(findings, config=None, *, get_conn, branch_exists=None,
         worktree_exists=None, lease_state=None, cards=None):
    from tools.kanban.orphan_requeue import act_on_orphans

    filed = cards if cards is not None else []
    return act_on_orphans(
        findings, config or {},
        get_conn=get_conn,
        branch_exists=branch_exists or (lambda tid: False),
        worktree_exists=worktree_exists or (lambda tid: False),
        lease_state=lease_state or (lambda tid: "free"),
        file_card=lambda spec: (filed.append(spec), spec["id"])[1],
    ), filed


# ── an orphan is requeued ONCE, through the seam, quoting the park ──────────


def test_orphan_is_requeued_once_through_requeue_task(raw, get_conn):
    _task(raw, "t-orphan")
    _park(raw, "t-orphan")

    out, cards = _act(_findings("t-orphan"), get_conn=get_conn)

    assert out["state"] == "acted"
    assert out["requeued"] == ["t-orphan"]
    assert out["carded"] == [] and out["deferred"] == []
    assert cards == []

    row = _row(raw, "t-orphan")
    assert row["status"] == "scheduled"
    assert row["scheduled_at"], "_get_due_tasks ignores a NULL scheduled_at"
    # The requeue seam's guarantees, not a raw UPDATE's: the stale failure
    # reason is cleared (so failure_triage does not read this as a fresh
    # failure) and the failure budget is PRESERVED.
    assert row["last_failure_reason"] is None
    assert row["failure_count"] == 2

    moves = [t for t in _transitions(raw, "t-orphan") if t["to_status"] == "scheduled"]
    assert len(moves) == 1
    assert moves[0]["actor"] == "kanban_stranded_reflex"
    assert moves[0]["from_status"] == "validating"
    assert GUARD in moves[0]["reason"]
    assert "worktree creation failed" in moves[0]["reason"]

    # ONCE: a second run finds the row no longer validating and refuses it.
    again, _ = _act(_findings("t-orphan"), get_conn=get_conn)
    assert again["requeued"] == []
    assert [r["task_id"] for r in again["refused"]] == ["t-orphan"]
    assert "not_validating" in again["refused"][0]["reasons"]
    assert _row(raw, "t-orphan")["status"] == "scheduled"


# ── a twice-parked row is CARDED, not requeued a third time ─────────────────


def test_twice_parked_by_same_guard_within_24h_is_carded_not_requeued(raw, get_conn):
    _task(raw, "t-repark")
    first = "worktree creation failed; first attempt (git worktree add timed out)"
    _park(raw, "t-repark", reason=first, ago=timedelta(hours=5))
    _park(raw, "t-repark", reason=PARK_REASON, ago=timedelta(minutes=30))

    out, cards = _act(_findings("t-repark"), get_conn=get_conn)

    assert out["requeued"] == []
    assert out["carded"] == ["t-repark"]
    assert _row(raw, "t-repark")["status"] == "validating"
    assert not [t for t in _transitions(raw, "t-repark") if t["to_status"] == "scheduled"]

    assert len(cards) == 1
    card = cards[0]
    assert card["status"] == "suggested"
    assert card["idempotency_key"]
    # BOTH parking reasons ride on the card, with the guard named.
    assert first in card["description"]
    assert PARK_REASON in card["description"]
    assert GUARD in card["description"]
    assert "t-repark" in card["description"]


def test_two_parks_outside_24h_do_not_count_as_recurring(raw, get_conn):
    _task(raw, "t-old")
    _park(raw, "t-old", reason="an old park", ago=timedelta(hours=30))
    _park(raw, "t-old", ago=timedelta(minutes=30))

    out, cards = _act(_findings("t-old"), get_conn=get_conn)

    assert out["requeued"] == ["t-old"]
    assert out["carded"] == [] and cards == []


def test_two_parks_by_different_guards_do_not_count_as_recurring(raw, get_conn):
    _task(raw, "t-mixed")
    _park(raw, "t-mixed", actor="repo-aware-guard", reason="external repo: root unset",
          ago=timedelta(hours=2))
    _park(raw, "t-mixed", ago=timedelta(minutes=30))

    out, cards = _act(_findings("t-mixed"), get_conn=get_conn)

    assert out["requeued"] == ["t-mixed"]
    assert out["carded"] == []


# ── the proof refuses everything that is not a branchless guard park ────────


def test_validating_row_with_a_branch_is_untouched(raw, get_conn):
    _task(raw, "t-branch")
    _park(raw, "t-branch")

    out, cards = _act(_findings("t-branch"), get_conn=get_conn,
                      branch_exists=lambda tid: tid == "t-branch")

    assert out["requeued"] == [] and out["carded"] == []
    assert [r["task_id"] for r in out["refused"]] == ["t-branch"]
    assert "branch_exists" in out["refused"][0]["reasons"]
    assert _row(raw, "t-branch")["status"] == "validating"
    assert cards == []


def test_validating_row_with_a_worktree_is_untouched(raw, get_conn):
    _task(raw, "t-wt")
    _park(raw, "t-wt")

    out, _ = _act(_findings("t-wt"), get_conn=get_conn,
                  worktree_exists=lambda tid: True)

    assert out["requeued"] == []
    assert "worktree_exists" in out["refused"][0]["reasons"]
    assert _row(raw, "t-wt")["status"] == "validating"


@pytest.mark.parametrize("state", ["live", "working", None])
def test_live_or_unknown_lease_refuses(raw, get_conn, state):
    _task(raw, "t-lease")
    _park(raw, "t-lease")

    out, _ = _act(_findings("t-lease"), get_conn=get_conn,
                  lease_state=lambda tid: state)

    assert out["requeued"] == []
    reasons = out["refused"][0]["reasons"]
    assert ("lease_live" in reasons) or ("lease_unknown" in reasons)
    assert _row(raw, "t-lease")["status"] == "validating"


def test_park_by_a_human_is_not_a_guard_park(raw, get_conn):
    _task(raw, "t-manual")
    _park(raw, "t-manual", actor="manual", reason="holding for review")

    out, _ = _act(_findings("t-manual"), get_conn=get_conn)

    assert out["requeued"] == []
    assert "not_guard_park" in out["refused"][0]["reasons"]
    assert _row(raw, "t-manual")["status"] == "validating"


def test_no_recorded_park_at_all_refuses(raw, get_conn):
    _task(raw, "t-silent")

    out, _ = _act(_findings("t-silent"), get_conn=get_conn)

    assert out["requeued"] == []
    assert "no_park_recorded" in out["refused"][0]["reasons"]


def test_audit_claim_is_re_derived_not_trusted(raw, get_conn):
    """The audit said 'orphan'; the row says 'done'. The row wins."""
    _task(raw, "t-done", status="done")
    _park(raw, "t-done")

    out, _ = _act(_findings("t-done"), get_conn=get_conn)

    assert out["requeued"] == []
    assert "not_validating" in out["refused"][0]["reasons"]
    assert _row(raw, "t-done")["status"] == "done"


# ── the cap defers, never drops ─────────────────────────────────────────────


def test_cap_of_n_leaves_the_rest_deferred_oldest_park_first(raw, get_conn):
    for i, ago in ((1, 3), (2, 2), (3, 1)):
        _task(raw, f"t-cap-{i}")
        _park(raw, f"t-cap-{i}", ago=timedelta(hours=ago))

    out, _ = _act(_findings("t-cap-3", "t-cap-1", "t-cap-2"),
                  {"max_requeues_per_run": 2}, get_conn=get_conn)

    assert out["max_requeues_per_run"] == 2
    # Oldest park first: the card that has waited longest goes first.
    assert out["requeued"] == ["t-cap-1", "t-cap-2"]
    assert out["deferred"] == ["t-cap-3"]
    assert _row(raw, "t-cap-3")["status"] == "validating"
    assert out["candidates"] == 3


def test_default_cap_is_ten():
    from tools.kanban.orphan_requeue import DEFAULT_MAX_REQUEUES_PER_RUN

    assert DEFAULT_MAX_REQUEUES_PER_RUN == 10


def test_config_block_declares_the_cap():
    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    block = cfg["reflexes"]["kanban_stranded_reflex"]
    assert block["max_requeues_per_run"] == 10


# ── unmeasurable, never a clean zero ────────────────────────────────────────


def test_act_is_unmeasurable_when_the_board_cannot_be_read():
    from tools.kanban.orphan_requeue import act_on_orphans

    def _broken():
        raise RuntimeError("connection refused")

    out = act_on_orphans(_findings("t-x"), {}, get_conn=_broken,
                         branch_exists=lambda t: False,
                         worktree_exists=lambda t: False,
                         lease_state=lambda t: "free",
                         file_card=lambda s: s["id"])

    assert out["state"] == "unmeasurable"
    assert out["requeued"] == [] and out["carded"] == [] and out["deferred"] == []
    assert out["error"]


def test_no_orphans_is_clean_not_unmeasurable(raw, get_conn):
    out, _ = _act(_findings(), get_conn=get_conn)
    assert out["state"] == "clean"
    assert out["candidates"] == 0


def test_reflex_returns_unmeasurable_success_true_when_board_unreadable(monkeypatch):
    from tools.genesis.reflexes import kanban_stranded_reflex as reflex
    from tools.kanban import stranded_audit as sa

    def _audit_run(config, state):
        # What stranded_audit.run returns when get_connection() raised.
        return {"success": True, "metric_value": 0.0,
                "details": {"default_branch": "main", "total": 0, "stranded": [],
                            "orphan_validating": [], "clean_count": 0,
                            "error": "connection refused", "cards_filed": []}}

    monkeypatch.setattr(sa, "run", _audit_run)

    result = reflex.run({"max_requeues_per_run": 10}, None)

    assert result["success"] is True
    assert result["details"]["orphan_requeue"]["state"] == "unmeasurable"
    assert result["details"]["orphan_requeue"]["requeued"] == []


def test_reflex_hands_the_audit_findings_and_its_config_to_the_act(monkeypatch):
    from tools.genesis.reflexes import kanban_stranded_reflex as reflex
    from tools.kanban import orphan_requeue as oq
    from tools.kanban import stranded_audit as sa

    findings = {**_findings("t-a"), "cards_filed": []}
    monkeypatch.setattr(sa, "run", lambda config, state: {
        "success": True, "metric_value": 1.0, "details": dict(findings)})

    seen = {}

    def _fake_act(f, config, **kw):
        seen["findings"] = f
        seen["config"] = config
        return {"state": "acted", "requeued": ["t-a"], "carded": [], "deferred": [],
                "refused": [], "candidates": 1, "max_requeues_per_run": 3}

    monkeypatch.setattr(oq, "act_on_orphans", _fake_act)

    result = reflex.run({"max_requeues_per_run": 3}, None)

    assert seen["findings"]["orphan_validating"][0]["id"] == "t-a"
    assert seen["config"]["max_requeues_per_run"] == 3
    assert result["success"] is True
    assert result["details"]["orphan_requeue"]["requeued"] == ["t-a"]
    # The audit's own findings are still there — the act is added beside them.
    assert result["details"]["orphan_validating"][0]["id"] == "t-a"


def test_reflex_survives_an_act_that_raises(monkeypatch):
    """The audit's report must not be lost because the act blew up."""
    from tools.genesis.reflexes import kanban_stranded_reflex as reflex
    from tools.kanban import orphan_requeue as oq
    from tools.kanban import stranded_audit as sa

    monkeypatch.setattr(sa, "run", lambda config, state: {
        "success": True, "metric_value": 1.0, "details": {**_findings("t-a"), "cards_filed": []}})

    def _boom(*a, **k):
        raise RuntimeError("act exploded")

    monkeypatch.setattr(oq, "act_on_orphans", _boom)

    result = reflex.run({}, None)

    assert result["success"] is True
    assert result["details"]["orphan_requeue"]["state"] == "unmeasurable"
    assert "act exploded" in result["details"]["orphan_requeue"]["error"]


# ── structural: the act goes through the seam, never around it ──────────────


def test_act_module_never_writes_kanban_tasks_directly():
    import ast

    src = (ROOT / "tools" / "kanban" / "orphan_requeue.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = node.value.upper()
            assert not ("UPDATE" in upper and "KANBAN_TASKS" in upper), node.value
            assert not ("INSERT" in upper and "KANBAN_TASKS" in upper), node.value
    assert "requeue_task" in src
    assert "create_tasks" in src
