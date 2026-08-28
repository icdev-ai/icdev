"""A finished project card must CLEAR without a human remembering anything. CUI // SP-CTI

THE REPORT THIS FIXES (operator, 2026-08-28): "lately project cards don't clear
automatically after all tasks are implemented." Measured on the live board, three cards were
visible and every one was held by a row that is not outstanding work:

    wire     4/5   held by wire-gate-00, an in_progress MANUAL-GATE SENTINEL
    ftl      92/93 held by ftl-gate-04, the same
    task_qa  8/9   held by a `validating` row (correctly open -- the control case)

TWO DEFECTS, one fix each, both in `_compute_project_progress`:

1. GATE SENTINELS COUNTED AS WORK. The orphan pass has excluded `<prefix>gate-NN` ids from
   day one ("a sentinel holds the card, it is not work, and it does not belong in a progress
   figure") -- but the reserved `gate` epic's own LIKE pattern matched them in the EPIC
   counts, so a card whose every real task had finished stayed visible until a human
   remembered to release the sentinel. MANUAL-only cards all carry one, which is why the
   report says "lately".

2. TERMINAL STATUSES THAT ARE NOT THE LITERAL 'done'. The epic counts recognised only
   `status = 'done'` as complete while the orphan predicate treated
   done/decomposed/cancelled/merged as closed -- so a cancelled or decomposed row held its
   card visible forever with nothing left to do. The two predicates are now one tuple.

`validating` and `pr_opened` stay OPEN deliberately: verification can fail, and an unmerged
PR is not landed work. The tests pin that too, so this fix cannot creep into hiding cards
with genuinely outstanding rows.

These tests drive the REAL endpoint (/api/projects/progress) against a temp DB seeded under
a prefix reserved for tests via the registered projects.yaml -- the same fixture pattern
tests/test_app.py documents.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def client(icdev_db, monkeypatch):
    """Authenticated client over the app singleton, storage pinned to the temp DB."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    from tools.dashboard.app import app

    app.config["TESTING"] = True
    with app.test_client() as tc:
        with tc.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield tc


def _pick_project() -> tuple[str, str]:
    """(task_prefix, a non-gate epic key) from a real registered card.

    The endpoint computes over the committed args/projects.yaml, so the fixture rows are
    seeded under a REAL card's prefix. `ftl` is the stable choice -- it has carried a `gate`
    epic and dozens of others since the FTL programme began. The precondition is asserted so
    a future registry change fails HERE with a message, not downstream with a mystery.
    """
    raw = yaml.safe_load((_ROOT / "args" / "projects.yaml").read_text(encoding="utf-8"))
    projects = raw["projects"] if isinstance(raw, dict) and "projects" in raw else raw
    card = next(p for p in projects if p.get("key") == "ftl")
    keys = [e["key"] for e in card.get("epics", [])]
    assert "gate" in keys, "the ftl card lost its reserved `gate` epic"
    work_key = next(k for k in keys if k != "gate")
    return card["task_prefix"], work_key


def _seed(db_path, rows):
    import sqlite3

    conn = sqlite3.connect(db_path)
    for tid, status in rows:
        conn.execute(
            "INSERT OR REPLACE INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
            (tid, f"fixture {tid}", status),
        )
    conn.commit()
    conn.close()


def _card(client, key: str):
    body = client.get("/api/projects/progress").get_json()
    assert body is not None and "projects" in body
    return next((p for p in body["projects"] if p["key"] == key), None)


# --------------------------------------------------------------------------- #
# defect 1: a gate sentinel may not hold a finished card open
# --------------------------------------------------------------------------- #
def test_a_card_whose_only_open_row_is_a_gate_sentinel_clears(client, icdev_db):
    prefix, ek = _pick_project()
    _seed(icdev_db, [
        (f"{prefix}{ek}-9001", "done"),
        (f"{prefix}{ek}-9002", "done"),
        (f"{prefix}gate-90", "in_progress"),        # the sentinel, deliberately unreleased
    ])
    assert _card(client, "ftl") is None, (
        "a MANUAL-GATE sentinel held a finished card on screen -- the sentinel is not work"
    )


def test_a_gate_sentinel_does_not_hold_the_card_but_open_work_does(client, icdev_db):
    """The sentinel must be neutral in BOTH directions: with real work still open the card
    shows regardless of the gate, and the gate adds nothing to the totals."""
    prefix, ek = _pick_project()
    _seed(icdev_db, [
        (f"{prefix}{ek}-9001", "done"),
        (f"{prefix}{ek}-9002", "backlog"),
        (f"{prefix}gate-90", "in_progress"),
    ])
    card = _card(client, "ftl")
    assert card is not None, "open work must keep the card visible"
    assert card["total_tasks"] == 2, "the sentinel leaked into the totals"
    assert card["done_tasks"] == 1


# --------------------------------------------------------------------------- #
# defect 2: terminal statuses beyond the literal 'done' must close
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("terminal", ["decomposed", "cancelled", "merged"])
def test_a_terminal_status_cannot_hold_a_card_open(client, icdev_db, terminal):
    prefix, ek = _pick_project()
    _seed(icdev_db, [
        (f"{prefix}{ek}-9001", "done"),
        (f"{prefix}{ek}-9002", terminal),
    ])
    assert _card(client, "ftl") is None, (
        f"a {terminal!r} row is not outstanding work and must not hold the card open -- "
        "the orphan predicate has said so all along; the epic counts now agree"
    )


@pytest.mark.parametrize("open_status", ["validating", "pr_opened", "in_progress",
                                         "scheduled", "backlog", "failed"])
def test_genuinely_open_statuses_still_hold_the_card(client, icdev_db, open_status):
    """The other direction, so this fix cannot creep: verification can fail and an unmerged
    PR is not landed work. A card with any of these rows stays on screen."""
    prefix, ek = _pick_project()
    _seed(icdev_db, [
        (f"{prefix}{ek}-9001", "done"),
        (f"{prefix}{ek}-9002", open_status),
    ])
    card = _card(client, "ftl")
    assert card is not None, f"a {open_status!r} row is outstanding work; the card hid it"


# --------------------------------------------------------------------------- #
# the two predicates are one
# --------------------------------------------------------------------------- #
def test_the_epic_counts_and_the_orphan_predicate_share_one_closed_set():
    """The root cause was two hand-maintained copies of 'what counts as closed'. The orphan
    query now interpolates the SAME `_closed_statuses` tuple the epic counts read; a second
    literal list reappearing is this defect regrowing."""
    src = (_ROOT / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert src.count('_closed_statuses = ("done", "decomposed", "cancelled", "merged")') == 1
    assert "NOT IN ('done', 'decomposed', 'cancelled', 'merged')" not in src, (
        "a literal copy of the closed set reappeared beside the named tuple"
    )
