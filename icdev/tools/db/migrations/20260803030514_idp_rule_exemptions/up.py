# CUI // SP-CTI
"""Migration 20260803030514 — rule-level scorecard exemptions with approval.

## Why exemptions exist at all

A scorecard with one unfair rule does not get one unfair rule fixed; it gets
the whole scorecard ignored, and an ignored scorecard is worse than none
because it still reads as governance. Exemptions are the pressure-release
valve: a component can be taken out of ONE rule without being taken out of the
scorecard.

## Why this table is an event log rather than a state row

An exemption is an authority claim — "this component does not have to satisfy
this rule" — and the only thing that makes such a claim reviewable later is
knowing **who** granted it and **why**. A mutable row cannot answer that: an
UPDATE overwrites the approver, and a DELETE erases the fact that anyone ever
waived anything. So every state change appends a row, and the current state of
an exemption is its highest-``event_index`` row. Registered in
``APPEND_ONLY_TABLES`` (.claude/hooks/pre_tool_use.py) alongside the other
audit surfaces, per CLAUDE.md.

That choice also removes the read-modify-write race between the dashboard, the
CLI and the scheduled recorder: appending is safe from all three at once.

## event_index, not a timestamp, orders the log

``recorded_at`` is an ISO string and two events for the same exemption can land
in the same microsecond (an auto-approval writes ``requested`` and ``approved``
back to back). Ordering the log by time would then make "which state is
current" non-deterministic. ``event_index`` is a per-exemption counter computed
at insert time — ``MAX(event_index) + 1`` for that ``exemption_key`` — so the
latest row is unambiguous on both backends.

## approved_by is NOT NULL-able in spirit, not in DDL

A ``requested`` event genuinely has no approver yet, so the column must allow
NULL. The invariant that matters — an exemption only *applies* when it names
both an approver and a reason — is enforced in
``tools/idp/exemptions.py`` and in ``tools/idp/scorecard.py`` at evaluation
time, where an unattributed exemption is reported as inert rather than
silently waiving the rule.
"""
from __future__ import annotations

from tools.db.storage import get_connection, table_exists

_TABLE = "idp_rule_exemptions"
_TAG = "[20260803030514_idp_rule_exemptions]"

# Asserted against exemptions.INSERT_COLUMNS by tests/test_idp_exemptions.py:
# an INSERT naming a column the live schema lacks raises, gets swallowed by the
# caller, and reports success while persisting nothing (CLAUDE.md).
_DDL = """
CREATE TABLE IF NOT EXISTS idp_rule_exemptions (
    id                TEXT PRIMARY KEY,
    exemption_key     TEXT NOT NULL,
    scorecard_key     TEXT NOT NULL,
    rule_identifier   TEXT NOT NULL,
    component_key     TEXT NOT NULL,
    event             TEXT NOT NULL,
    status            TEXT NOT NULL,
    event_index       INTEGER NOT NULL DEFAULT 1,
    reason            TEXT NOT NULL,
    requested_by      TEXT NOT NULL,
    decided_by        TEXT,
    decision_reason   TEXT,
    auto_approved     INTEGER NOT NULL DEFAULT 0,
    self_approved     INTEGER NOT NULL DEFAULT 0,
    expires_on        TEXT,
    notified          TEXT,
    recorded_at       TEXT NOT NULL,
    tenant_id         TEXT,
    classification    TEXT DEFAULT 'CUI // SP-CTI',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEXES = (
    # "What is the current state of this exemption?" — the hot read.
    (
        "idx_ixmp_key_index",
        "CREATE INDEX IF NOT EXISTS idx_ixmp_key_index "
        "ON idp_rule_exemptions (exemption_key, event_index)",
    ),
    # "Which exemptions apply to this scorecard right now?" — evaluation time.
    (
        "idx_ixmp_scorecard",
        "CREATE INDEX IF NOT EXISTS idx_ixmp_scorecard "
        "ON idp_rule_exemptions (scorecard_key, component_key, rule_identifier)",
    ),
    # "Show me every waiver anyone granted" — the review read.
    (
        "idx_ixmp_recorded",
        "CREATE INDEX IF NOT EXISTS idx_ixmp_recorded "
        "ON idp_rule_exemptions (recorded_at)",
    ),
)


def up(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        if table_exists(conn, _TABLE):
            print(f"{_TAG} {_TABLE} already present")
        else:
            conn.execute(_DDL)
            print(f"{_TAG} created {_TABLE}")

        for name, sql in _INDEXES:
            try:
                conn.execute(sql)
            except Exception as exc:  # noqa: BLE001 — an index is not worth failing on
                print(f"{_TAG} index {name} skipped: {exc}")
        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    up()
