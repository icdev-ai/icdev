"""Seed kanban_tasks for Oracle-gap fix plan (3 atomic tasks).

Gaps addressed:
  oracle-fix-03  /analysis route missing from start.md Pages list
  oracle-fix-04  tools/trading/signal_decay.py does not exist (tests broken)
  oracle-fix-05  sg_intelligence_briefs_war_council has no SELECT/read path

Run:
    python tools/db/seeds/seed_oracle_fix_plan.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

INSERT_SQL = """
INSERT INTO kanban_tasks
    (id, title, description, task_type, priority, status,
     scheduled_at, executor_type, dispatch_source,
     depends_on_task_id, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO NOTHING
"""

TASKS = [
    (
        "oracle-fix-03",
        "Add /analysis to start.md Pages list",
        (
            "The /analysis route is registered in tools/dashboard/app.py:8359 but "
            "is absent from the Pages list in .claude/commands/start.md. "
            "This causes the internal-awareness route_not_listed oracle gap.\n\n"
            "Steps:\n"
            "1. Open .claude/commands/start.md.\n"
            "2. Locate the '- Pages:' line inside the Dashboard bullet.\n"
            "3. Insert `/analysis` in alphabetical order within that list.\n"
            "4. Verify the file saves cleanly — no other edits.\n\n"
            "Acceptance: grep '/analysis' .claude/commands/start.md returns a match."
        ),
        "chore",
        "medium",
        "scheduled",
        NOW,
        "claude_cli",
        "oracle_fix_plan",
        None,
        NOW,
        NOW,
    ),
    (
        "oracle-fix-04",
        "Create tools/trading/signal_decay.py",
        (
            "tests/test_signal_decay.py imports DECAY_LAMBDA, WEIGHT_FLOOR, "
            "compute_signal_decay, and batch_decay from tools.trading.signal_decay — "
            "the module does not exist, so all 5 tests fail with ImportError.\n\n"
            "Create tools/trading/signal_decay.py with:\n"
            "  DECAY_LAMBDA = 0.05   # exponential half-life constant\n"
            "  WEIGHT_FLOOR = 0.01   # minimum weight at extreme age\n\n"
            "  def compute_signal_decay(age_days, decay_lambda=DECAY_LAMBDA) -> float:\n"
            "    weight = math.exp(-decay_lambda * age_days)\n"
            "    return max(weight, WEIGHT_FLOOR)\n"
            "    # special-case age_days=0 → always 1.0\n\n"
            "  def batch_decay(signals: list[dict]) -> list[dict]:\n"
            "    for s in signals:\n"
            "      s['signal_decay_weight'] = compute_signal_decay(s['age_days'])\n"
            "    return signals\n\n"
            "Steps:\n"
            "1. Write the module exactly matching the contract above.\n"
            "2. Run: python -m pytest tests/test_signal_decay.py -v\n"
            "3. All 5 tests must pass.\n\n"
            "Acceptance: pytest exit code 0 with 5 passed."
        ),
        "build",
        "high",
        "scheduled",
        NOW,
        "claude_cli",
        "oracle_fix_plan",
        "oracle-fix-03",
        NOW,
        NOW,
    ),
    (
        "oracle-fix-05",
        "Add get_war_council_briefs() read path to war_council.py",
        (
            "tools/strategos/war_council.py inserts into sg_intelligence_briefs_war_council "
            "(_save_brief, line ~327) but has no SELECT path — an orphan write-only table.\n\n"
            "Add a read function directly below _save_brief:\n\n"
            "  def get_war_council_briefs(\n"
            "      theater: str | None = None,\n"
            "      limit: int = 20,\n"
            "      conn=None,\n"
            "  ) -> list[dict]:\n"
            "    '''Return recent war council briefs, optionally filtered by theater.'''\n"
            "    _conn = conn or get_connection()\n"
            "    try:\n"
            "      sql = 'SELECT * FROM sg_intelligence_briefs_war_council'\n"
            "      params: list = []\n"
            "      if theater:\n"
            "        sql += ' WHERE theater = ?'\n"
            "        params.append(theater)\n"
            "      sql += ' ORDER BY created_at DESC LIMIT ?'\n"
            "      params.append(limit)\n"
            "      cur = _conn.execute(sql, params)\n"
            "      cols = [c[0] for c in cur.description]\n"
            "      return [dict(zip(cols, row)) for row in cur.fetchall()]\n"
            "    except Exception:\n"
            "      return []\n"
            "    finally:\n"
            "      if conn is None:\n"
            "        _conn.close()\n\n"
            "Steps:\n"
            "1. Insert the function after _save_brief in war_council.py.\n"
            "2. Export it from the module (it should be importable).\n"
            "3. Run: python -c \"from tools.strategos.war_council import get_war_council_briefs; "
            "print('OK')\"\n\n"
            "Acceptance: import succeeds and function returns list (possibly empty)."
        ),
        "build",
        "medium",
        "scheduled",
        NOW,
        "claude_cli",
        "oracle_fix_plan",
        "oracle-fix-04",
        NOW,
        NOW,
    ),
]


def main() -> None:
    conn = get_connection()
    for task in TASKS:
        conn.execute(INSERT_SQL, task)
    conn.commit()

    # Verify
    ids = [t[0] for t in TASKS]
    cur = conn.cursor()
    cur.execute(
        f"SELECT id, status FROM kanban_tasks WHERE id IN ({','.join('?' * len(ids))})",
        ids,
    )
    rows = cur.fetchall()
    conn.close()

    print(f"Seeded {len(rows)}/{len(TASKS)} oracle-fix tasks:")
    for r in rows:
        print(f"  {r[0]}  [{r[1]}]")


if __name__ == "__main__":
    main()
