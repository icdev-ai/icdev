# CUI // SP-CTI
"""The requeue act has an OWNER that a stranded-audit timeout cannot starve
(mfx-own-03).

kpr-stale-05/06 built the act -- prove -> requeue -> confirm, bounded, audited.
It was wired as a downstream consumer of ``stranded_audit``, and MEASURED on the
live board 2026-09-05 that made it unreachable:

  * ``stranded_audit`` walks every TERMINAL task -- 3,892 ``done`` rows against
    the 2 ``validating`` rows the act cares about -- and compares each genuinely
    divergent branch by patch-id. Median recorded run 300.0s, max 1200.2s
    against a 1200s watchdog.
  * 242 recorded ``kanban_stranded_reflex`` runs carry ``orphan_requeue``
    ZERO times. 5 of those runs are since the act landed (2026-09-04 00:28Z);
    the one that reached a verdict died on ``watchdog_timeout_1200s``.
  * three consecutive failures then opened the reflex's circuit breaker
    (``daemon.base`` SKIPS a reflex whose breaker is open), so the act stopped
    being dispatched at all.
  * of 73 lifetime guard parks across 72 tasks, 63 exits were a HUMAN
    (``manual`` 41, ``cli`` 22), 5 were ``pr_watcher -> done``, 5 are still
    parked, and ``kanban_stranded_reflex`` accounts for EXACTLY ZERO.

So the fix is not another proof -- it is a REACHABLE consumer. The act's own
candidate set is one indexed query (``status = 'validating'``); nothing about it
needs the audit. Each test here pins one half of that:

  * ``board_findings`` derives candidates from ``kanban_tasks`` ALONE;
  * the reflex requeues a proven orphan while ``stranded_audit`` is RAISING --
    the audit's failure mode, reproduced;
  * the reflex module never names ``stranded_audit`` (AST), so the coupling
    cannot come back by edit;
  * an unreadable board is ``unmeasurable`` with ``success: True``, never a
    clean zero and never a breaker-tripping failure;
  * the reflex is registered in BOTH ``daemon.REFLEX_NAMES`` and
    ``args/genesis_config.yaml`` -- the pair nothing asserted for
    ``failure_triage``/``oracle_triage``, which were configured and inert.
"""
from __future__ import annotations

import ast
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
REFLEX = "kanban_requeue_reflex"


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


_seq = [0]


def _task(raw, tid, *, status="validating", failure_count=2):
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        "status, updated_at, failure_count, last_failure_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, f"title {tid}", f"desc {tid}", "build", "high", status,
         _now().isoformat(), failure_count, "an earlier failure"),
    )
    raw.commit()


def _park(raw, tid, *, actor=GUARD, reason=PARK_REASON, ago=timedelta(hours=1)):
    _seq[0] += 1
    raw.execute(
        "INSERT INTO kanban_status_transitions (id, task_id, from_status, "
        "to_status, actor, reason, recorded_at) VALUES (?,?,?,?,?,?,?)",
        (f"kst-{tid}-{_seq[0]}", tid, "scheduled", "validating", actor, reason,
         (_now() - ago).isoformat()),
    )
    raw.commit()


def _row(raw, tid) -> dict:
    return dict(raw.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone())


# -- candidates come from the BOARD, not from the audit ---------------------


def test_board_findings_reads_validating_rows_from_the_board_alone(raw, get_conn):
    from tools.kanban.orphan_requeue import board_findings

    _task(raw, "t-parked")
    _task(raw, "t-done", status="done")
    _task(raw, "t-progress", status="in_progress")

    findings = board_findings(get_conn=get_conn, default_branch="main")

    assert findings["source"] == "board"
    assert [r["id"] for r in findings["orphan_validating"]] == ["t-parked"]
    assert [r["id"] for r in findings["validating_with_branch"]] == ["t-parked"]
    assert findings["default_branch"] == "main"
    assert findings.get("error") is None


def test_board_findings_is_unmeasurable_never_empty_when_the_board_is_unreadable():
    from tools.kanban.orphan_requeue import board_findings

    def boom():
        raise RuntimeError("board unreachable")

    findings = board_findings(get_conn=boom, default_branch="main")

    assert findings["state"] == "unmeasurable"
    assert findings["error"]
    # NEVER a clean empty list: "no candidates" and "could not look" are
    # different answers and only one of them is a clean bill of health.
    assert findings["orphan_validating"] is None
    assert findings["validating_with_branch"] is None


# -- the audit's failure mode, reproduced: it cannot starve the act ---------


def _run(get_conn, **kw):
    """Drive the reflex with the git/lease probes injected and the acts' write
    seams bound to this test's board."""
    from tools.genesis.reflexes import kanban_requeue_reflex as rr

    cards: list = []
    result = rr.run(
        kw.pop("config", {}),
        None,
        get_conn=get_conn,
        branch_exists=kw.pop("branch_exists", lambda tid: False),
        worktree_exists=kw.pop("worktree_exists", lambda tid: False),
        lease_state=kw.pop("lease_state", lambda tid: "free"),
        file_card=lambda spec: (cards.append(spec), spec["id"])[1],
        **kw,
    )
    return result, cards


def test_reflex_requeues_while_the_stranded_audit_is_raising(raw, get_conn, monkeypatch):
    """The measured failure: the audit dies on its watchdog. The act must not."""
    import tools.kanban.stranded_audit as sa

    def exploded(*a, **k):
        raise RuntimeError("watchdog_timeout_1200s")

    monkeypatch.setattr(sa, "run", exploded, raising=False)
    monkeypatch.setattr(sa, "audit_stranded_tasks", exploded, raising=False)

    _task(raw, "t-orphan")
    _park(raw, "t-orphan")

    result, cards = _run(get_conn)

    assert result["success"] is True
    act = result["details"]["orphan_requeue"]
    assert act["state"] == "acted"
    assert act["requeued"] == ["t-orphan"]
    assert cards == []
    assert _row(raw, "t-orphan")["status"] == "scheduled"

    # The row names the reflex that ACTUALLY acted. Stamping it
    # `kanban_stranded_reflex` -- which no longer runs the act and whose breaker
    # is open -- would send a reader to an impossible row, which is
    # misattribution one layer inside a card about ownership.
    moves = [dict(r) for r in raw.execute(
        "SELECT * FROM kanban_status_transitions WHERE task_id = ? AND to_status = ?",
        ("t-orphan", "scheduled")).fetchall()]
    assert len(moves) == 1
    assert moves[0]["actor"] == REFLEX
    # ...and it still quotes the guard's own parking reason verbatim.
    assert GUARD in moves[0]["reason"]
    assert "worktree creation failed" in moves[0]["reason"]


def test_reflex_module_never_names_the_stranded_audit():
    """Structural, because a re-coupling would still pass the test above on a
    board where the audit happens to be fast."""
    from tools.genesis.reflexes import kanban_requeue_reflex as rr

    src = Path(rr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module or ''}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert not any("stranded_audit" in n for n in names), (
        "the requeue act must not depend on the whole-board stranded audit")


def test_reflex_reports_unmeasurable_without_failing_when_the_board_is_unreadable():
    from tools.genesis.reflexes import kanban_requeue_reflex as rr

    def boom():
        raise RuntimeError("board unreachable")

    result = rr.run({}, None, get_conn=boom)

    # An unreadable board is not a reflex FAILURE: marking it one is what trips
    # the breaker that made the act unreachable in the first place.
    assert result["success"] is True
    for key in ("orphan_requeue", "empty_checkout_requeue"):
        assert result["details"][key]["state"] == "unmeasurable"
        assert result["details"][key]["candidates"] is None
        assert result["details"][key]["error"]


def test_clean_board_is_clean_and_not_unmeasurable(raw, get_conn):
    _task(raw, "t-live", status="in_progress")

    result, _cards = _run(get_conn)

    assert result["success"] is True
    assert result["details"]["orphan_requeue"]["state"] == "clean"
    assert result["details"]["orphan_requeue"]["candidates"] == 0


def test_cap_defers_the_remainder_by_name(raw, get_conn):
    for n in range(3):
        _task(raw, f"t-cap-{n}")
        _park(raw, f"t-cap-{n}", ago=timedelta(hours=3 - n))

    result, _cards = _run(get_conn, config={"max_requeues_per_run": 2})

    act = result["details"]["orphan_requeue"]
    assert len(act["requeued"]) == 2
    # Named, never dropped -- and the oldest park is never the one deferred.
    assert act["deferred"] == ["t-cap-2"]


def test_a_row_a_human_parked_is_never_requeued(raw, get_conn):
    _task(raw, "t-manual")
    _park(raw, "t-manual", actor="manual", reason="held for review")

    result, _cards = _run(get_conn)

    act = result["details"]["orphan_requeue"]
    assert act["requeued"] == []
    assert "not_guard_park" in act["refused"][0]["reasons"]
    assert _row(raw, "t-manual")["status"] == "validating"


# -- registered where the daemon actually dispatches from -------------------


def test_reflex_is_registered_in_both_the_daemon_list_and_the_config():
    from tools.genesis import daemon as _d

    assert REFLEX in _d.REFLEX_NAMES, "the daemon dispatches from REFLEX_NAMES"

    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    block = (cfg.get("reflexes") or {}).get(REFLEX)
    assert block, "a reflex the daemon dispatches must be configured"
    assert block.get("enabled") is True
    # Minutes, not a day: a park is answered on the cadence the park happens on.
    assert 0 < int(block["interval_seconds"]) <= 3600


def test_reflex_module_mirrors_byte_identically():
    a = ROOT / "tools" / "genesis" / "reflexes" / "kanban_requeue_reflex.py"
    b = ROOT / "icdev" / "tools" / "genesis" / "reflexes" / "kanban_requeue_reflex.py"
    assert b.exists(), "a reflex must exist in the packaged mirror too"
    assert a.read_bytes() == b.read_bytes()
