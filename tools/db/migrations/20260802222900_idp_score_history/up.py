# CUI // SP-CTI
"""Migration 20260802222900 — persist a scorecard evaluation per component.

MEASURED 2026-08-02: of 29 tables matching ``%score%|%readiness%|%grade%|
%maturity%|%scorecard%``, 25 hold zero rows. The only score time-series the
platform actually keeps is ``readiness_scores`` (55 rows, keyed to an intake
SESSION, not to a component). Every live per-component signal — canvas health
RAG, canvas compliance posture, platform health — is computed per request and
thrown away, so nothing in the platform can answer "is this component getting
better or worse".

## Why a new table rather than ``developer_scorecards``

``developer_scorecards`` grades a *developer* against five FIXED dimension
columns (code_quality, security, compliance, test_coverage, velocity) and an
A–F letter grade. Scorecard-as-code (idp-score-02) grades a *component* against
an arbitrary, YAML-defined rule set on a YAML-defined ladder — adding a rule is
a config edit. Those two shapes do not overlap:

  * A ladder level (Bronze…Platinum, operator-defined) has nowhere to live in a
    letter_grade CHECK of ('A','B','C','D','F').
  * N config-defined rules cannot be projected onto five hardcoded REAL
    columns without either losing rules or adding a column per rule — which
    would make every YAML rule edit a migration, destroying the whole point.
  * History is append-only; ``developer_scorecards`` is a current-standing
    snapshot with a lifecycle.

So this follows the shape that is already proven for score history in this
repo — ``readiness_scores`` (``tools/requirements/readiness_scorer.py``: one
row per evaluation, plus ``get_score_trend()``) — pointed at components instead
of intake sessions.

## Append-only

Registered in ``APPEND_ONLY_TABLES``. A trend line you can UPDATE is not a
trend line; a wrong data point is corrected by recording a new one.

## window_start is the cadence key, not a uniqueness constraint

``window_start`` is the floor of ``evaluated_at`` to the scorecard's declared
``evaluation.window`` bucket. The recorder uses it to decide whether a run is
DUE (``record_scorecard(if_due=True)``, which is how the scheduled reflex
calls it), so a 3h reflex against a 24h window writes one row per component per
day. It is deliberately NOT a unique index: an operator forcing a re-score
after a fix must be able to record the improvement immediately rather than
waiting out the window.
"""
from __future__ import annotations

from tools.db.storage import get_connection, table_exists

_TABLE = "idp_scorecard_history"
_TAG = "[20260802222900_idp_score_history]"

# Column set is asserted against the recorder's INSERT by
# tests/test_idp_score_history.py so the two cannot drift (CLAUDE.md:
# "every column in an INSERT must exist in the LIVE schema").
_DDL = """
CREATE TABLE IF NOT EXISTS idp_scorecard_history (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    scorecard_key   TEXT NOT NULL,
    component_key   TEXT NOT NULL,
    evaluated_at    TEXT NOT NULL,
    window_start    TEXT NOT NULL,
    window_label    TEXT NOT NULL,
    ladder_level    TEXT,
    level_rank      INTEGER NOT NULL DEFAULT 0,
    score           REAL NOT NULL DEFAULT 0,
    earned_weight   INTEGER NOT NULL DEFAULT 0,
    total_weight    INTEGER NOT NULL DEFAULT 0,
    failing_rules   INTEGER NOT NULL DEFAULT 0,
    rule_outcomes   TEXT,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEXES = (
    # The trend read: one component's series, oldest to newest.
    ("idx_isch_component_evaluated",
     "CREATE INDEX IF NOT EXISTS idx_isch_component_evaluated "
     "ON idp_scorecard_history (scorecard_key, component_key, evaluated_at)"),
    # The due check: has this window already been recorded?
    ("idx_isch_window",
     "CREATE INDEX IF NOT EXISTS idx_isch_window "
     "ON idp_scorecard_history (scorecard_key, window_start)"),
    # One scoring run across every component is one run_id.
    ("idx_isch_run",
     "CREATE INDEX IF NOT EXISTS idx_isch_run ON idp_scorecard_history (run_id)"),
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
