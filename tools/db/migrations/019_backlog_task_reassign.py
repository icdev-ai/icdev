#!/usr/bin/env python3
"""Migration 019 — Re-assign orphaned backlog tasks to project-scoped IDs.

Renames task-XXXXXXXX / diag-XXXXXXXX backlog tasks to structured IDs that
match entries in args/projects.yaml so they appear in Projects in Flight cards.

Safe to re-run: uses INSERT OR IGNORE + UPDATE WHERE id=old only if old exists.
"""
from tools.db.storage import get_connection

# old_id → new_id
RENAMES = {
    # ── FD-HMD  (FathomDesk Signal Decay) ──────────────────────────────────
    "task-0a4e24a5ed": "fdhmd-impl-01",
    "task-6dbc8b38f5": "fdhmd-impl-02",
    "task-966c31b1e9": "fdhmd-impl-03",
    "task-546ee742be": "fdhmd-vv-01",
    # ── HMD  (Memory System Hippo Decay) ────────────────────────────────────
    "task-43afe1311a": "hmd-impl-01",
    "task-5ba68cdbea": "hmd-impl-02",
    "task-bef85e7e65": "hmd-impl-03",
    "task-50afe766f3": "hmd-vv-01",
    # ── FD-MLT  (FathomDesk Signal Tuner) ───────────────────────────────────
    "task-f9182546b9": "fdmlt-impl-01",
    "task-298f7e5d84": "fdmlt-impl-02",
    "task-a643f7c71a": "fdmlt-impl-03",
    "task-b190641dd9": "fdmlt-vv-01",
    # ── PGT  (PostgreSQL Textsearch) ────────────────────────────────────────
    "task-99972430b7": "pgt-impl-01",
    "task-8c4338d01d": "pgt-test-01",
    "task-f79070650a": "pgt-vv-01",
    # ── CDH-GAP  (Code Health Oracle Gaps) ──────────────────────────────────
    "task-68b7c1840c": "cdh-gap-01",
    "task-e59337e3d5": "cdh-gap-02",
    "task-7a781b240a": "cdh-gap-03",
    "task-9264fb0ef0": "cdh-gap-04",
    "task-99c2cc1d2a": "cdh-gap-05",
    "task-af1edb38c7": "cdh-gap-06",
    "task-b45cff9245": "cdh-gap-07",
    # ── NDC-CHAT  (NDC Chat Context Bridge) ─────────────────────────────────
    "task-5c9f312efc-d1": "ndchat-impl01-d1",
    "task-5c9f312efc-d2": "ndchat-impl01-d2",
    "task-5c9f312efc-d3": "ndchat-impl01-d3",
    "task-5c9f312efc-d4": "ndchat-impl01-d4",
    "task-5c9f312efc-d5": "ndchat-impl01-d5",
    "task-d2e52b3251-d1": "ndchat-impl02-d1",
    "task-d2e52b3251-d2": "ndchat-impl02-d2",
    "task-d2e52b3251-d3": "ndchat-impl02-d3",
    "task-d2e52b3251-d4": "ndchat-impl02-d4",
    "task-d2e52b3251-d5": "ndchat-impl02-d5",
    "task-11dd5a86d8-d1": "ndchat-impl03-d1",
    "task-11dd5a86d8-d2": "ndchat-impl03-d2",
    "task-11dd5a86d8-d3": "ndchat-impl03-d3",
    "task-11dd5a86d8-d4": "ndchat-impl03-d4",
    "task-11dd5a86d8-d5": "ndchat-impl03-d5",
    "task-b27406c814-d1": "ndchat-vv01-d1",
    "task-b27406c814-d2": "ndchat-vv01-d2",
    "task-b27406c814-d3": "ndchat-vv01-d3",
    "task-b27406c814-d4": "ndchat-vv01-d4",
    "task-b27406c814-d5": "ndchat-vv01-d5",
    # ── OSV  (OSV Scanner Integration) ──────────────────────────────────────
    "task-a693713ebc-d1": "osv-impl01-d1",
    "task-a693713ebc-d2": "osv-impl01-d2",
    "task-a693713ebc-d3": "osv-impl01-d3",
    "task-a693713ebc-d4": "osv-impl01-d4",
    "task-a693713ebc-d5": "osv-impl01-d5",
    "task-377ca1e42e-d1": "osv-impl02-d1",
    "task-377ca1e42e-d2": "osv-impl02-d2",
    "task-377ca1e42e-d3": "osv-impl02-d3",
    "task-377ca1e42e-d4": "osv-impl02-d4",
    "task-5e5a2aa372-d1": "osv-vv01-d1",
    "task-5e5a2aa372-d2": "osv-vv01-d2",
    "task-5e5a2aa372-d3": "osv-vv01-d3",
    "task-5e5a2aa372-d4": "osv-vv01-d4",
    "task-5e5a2aa372-d5": "osv-vv01-d5",
    # ── FD-AO  (FathomDesk Reflex Observer) ─────────────────────────────────
    "task-5ba1285120-d1": "fdao-impl01-d1",
    "task-5ba1285120-d2": "fdao-impl01-d2",
    "task-5ba1285120-d3": "fdao-impl01-d3",
    "task-5ba1285120-d4": "fdao-impl01-d4",
    "task-5ba1285120-d5": "fdao-impl01-d5",
    "task-43b0bb8037-d1": "fdao-impl02-d1",
    "task-43b0bb8037-d2": "fdao-impl02-d2",
    "task-43b0bb8037-d3": "fdao-impl02-d3",
    "task-43b0bb8037-d4": "fdao-impl02-d4",
    "task-9851a9a510-d1": "fdao-vv01-d1",
    "task-9851a9a510-d2": "fdao-vv01-d2",
    "task-9851a9a510-d3": "fdao-vv01-d3",
    "task-9851a9a510-d4": "fdao-vv01-d4",
    "task-9851a9a510-d5": "fdao-vv01-d5",
    # ── FD-OBB  (FathomDesk OpenBB Gateway) ─────────────────────────────────
    "task-c57ed057a1-d1": "fdobb-impl02-d1",
    "task-c57ed057a1-d2": "fdobb-impl02-d2",
    "task-c57ed057a1-d3": "fdobb-impl02-d3",
    "task-c57ed057a1-d4": "fdobb-impl02-d4",
    "task-c57ed057a1-d5": "fdobb-impl02-d5",
    "task-a23883ca55-d1": "fdobb-impl03-d1",
    "task-a23883ca55-d2": "fdobb-impl03-d2",
    "task-a23883ca55-d3": "fdobb-impl03-d3",
    "task-a23883ca55-d4": "fdobb-impl03-d4",
    "task-a23883ca55-d5": "fdobb-impl03-d5",
    # d2 is already done so skip; d3-d5 are backlog
    "task-7ff9e62a02-d3": "fdobb-vv01-d3",
    "task-7ff9e62a02-d4": "fdobb-vv01-d4",
    "task-7ff9e62a02-d5": "fdobb-vv01-d5",
    # ── ZBX  (Zerobox Sandbox Coverage) ─────────────────────────────────────
    "task-0892c74c9d-d1": "zbx-impl01-d1",
    "task-0892c74c9d-d2": "zbx-impl01-d2",
    "task-0892c74c9d-d3": "zbx-impl01-d3",
    "task-0892c74c9d-d4": "zbx-impl01-d4",
    "task-0892c74c9d-d5": "zbx-impl01-d5",
    "task-fa09b2ebed-d1": "zbx-impl02-d1",
    "task-fa09b2ebed-d2": "zbx-impl02-d2",
    "task-fa09b2ebed-d3": "zbx-impl02-d3",
    "task-fa09b2ebed-d4": "zbx-impl02-d4",
    "task-fa09b2ebed-d5": "zbx-impl02-d5",
    "task-b4d2fe5db8-d1": "zbx-vv01-d1",
    "task-b4d2fe5db8-d2": "zbx-vv01-d2",
    "task-b4d2fe5db8-d3": "zbx-vv01-d3",
    "task-b4d2fe5db8-d4": "zbx-vv01-d4",
    "task-b4d2fe5db8-d5": "zbx-vv01-d5",
}

# Stale diagnostic subtasks from resolved debug sessions — mark done
STALE_DIAG = [
    "diag-99fa4a8af3-d4-d1",
    "diag-99fa4a8af3-d4-d2",
    "diag-99fa4a8af3-d4-d3",
    "diag-99fa4a8af3-d4-d4",
    "diag-b820ec12c7-d5",
]


def up(conn=None):
    conn = get_connection()
    renamed = 0
    skipped = 0
    stale_closed = 0

    # Step 1: rename task IDs and update dependency chains
    for old_id, new_id in RENAMES.items():
        exists = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = ?", (old_id,)
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        already = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = ?", (new_id,)
        ).fetchone()
        if already:
            print(f"  SKIP (target exists): {old_id} → {new_id}")
            skipped += 1
            continue
        conn.execute(
            "UPDATE kanban_tasks SET id = ? WHERE id = ?", (new_id, old_id)
        )
        # Update any task that depended on the old ID
        conn.execute(
            "UPDATE kanban_tasks SET depends_on_task_id = ? "
            "WHERE depends_on_task_id = ?",
            (new_id, old_id),
        )
        renamed += 1

    # Step 2: close stale diagnostic subtasks
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for did in STALE_DIAG:
        r = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = ? AND status = 'backlog'",
            (did,),
        ).fetchone()
        if r:
            conn.execute(
                "UPDATE kanban_tasks SET status='done', completed_at=?, updated_at=?, "
                "last_failure_reason=? WHERE id=?",
                (now, now, "stale: parent diagnostic session resolved", did),
            )
            stale_closed += 1

    conn.commit()
    conn.close()
    print(f"Migration 019 complete: {renamed} renamed, {skipped} skipped, "
          f"{stale_closed} stale diag tasks closed.")


if __name__ == "__main__":
    up()
