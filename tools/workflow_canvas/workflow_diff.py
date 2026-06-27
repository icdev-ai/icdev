"""Process version diff — compare two workflow definitions step by step."""
from __future__ import annotations
from datetime import datetime, timezone


def diff_workflows(wf_a: dict, wf_b: dict) -> dict:
    """
    Compare two processified workflow dicts.

    Returns a dict with:
      added, removed, changed, unchanged_count, similarity_pct, summary
    """
    steps_a = wf_a.get("steps", [])
    steps_b = wf_b.get("steps", [])

    def key(s: dict) -> str:
        return (s.get("title") or "").lower().strip()

    keys_a = {key(s): s for s in steps_a}
    keys_b = {key(s): s for s in steps_b}

    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    unchanged_count = 0

    for k, s in keys_b.items():
        if k not in keys_a:
            added.append(s)

    for k, s in keys_a.items():
        if k not in keys_b:
            removed.append(s)
        else:
            sb = keys_b[k]
            diffs: list[str] = []
            for field in ("assignee_role", "reviewer_role", "approver_role", "phase"):
                va, vb = s.get(field), sb.get(field)
                if va != vb:
                    diffs.append(f"{field}: '{va}' -> '{vb}'")
            cl_a = set(s.get("checklist") or [])
            cl_b = set(sb.get("checklist") or [])
            if cl_a != cl_b:
                added_cl = cl_b - cl_a
                removed_cl = cl_a - cl_b
                if added_cl:
                    diffs.append(f"checklist added: {sorted(added_cl)}")
                if removed_cl:
                    diffs.append(f"checklist removed: {sorted(removed_cl)}")
            if diffs:
                changed.append({"step_a": s, "step_b": sb, "changes": diffs})
            else:
                unchanged_count += 1

    total = max(len(steps_a), len(steps_b), 1)
    similarity_pct = round(unchanged_count / total * 100)

    return {
        "name_a": wf_a.get("workflow_name") or wf_a.get("name") or "Workflow A",
        "name_b": wf_b.get("workflow_name") or wf_b.get("name") or "Workflow B",
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged_count,
        "similarity_pct": similarity_pct,
        "summary": (
            f"{len(added)} added, {len(removed)} removed, "
            f"{len(changed)} changed, {unchanged_count} unchanged"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def diff_workflow_ids(id_a: str, id_b: str) -> dict:
    """Load two workflows from the DB and diff them."""
    import yaml
    from tools.db.storage import get_connection, sql_placeholder

    conn = get_connection()
    ph = sql_placeholder(conn)
    row_a = conn.execute(
        f"SELECT * FROM studio_workflows WHERE workflow_id = {ph}", (id_a,)
    ).fetchone()
    row_b = conn.execute(
        f"SELECT * FROM studio_workflows WHERE workflow_id = {ph}", (id_b,)
    ).fetchone()
    conn.close()

    if not row_a:
        raise ValueError(f"Workflow {id_a} not found")
    if not row_b:
        raise ValueError(f"Workflow {id_b} not found")

    wf_a = yaml.safe_load(row_a["template_yaml"] or "{}") or {}
    wf_b = yaml.safe_load(row_b["template_yaml"] or "{}") or {}
    return diff_workflows(wf_a, wf_b)
