from __future__ import annotations

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Wave Planner — server migration wave grouping and dependency visualization.

Groups server migration sessions into sequenced waves based on risk, readiness,
and inter-server dependencies.  All functions are deterministic; no LLM required.
"""

import json
import uuid
from datetime import datetime, timezone


logger = get_logger("icdev.wave_planner")

WAVE_STATUSES = ("planned", "in_progress", "complete", "blocked")
DEP_TYPES = ("network", "application", "database", "auth", "storage")
DEP_DIRECTIONS = ("inbound", "outbound", "bidirectional")


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wave_id() -> str:
    return "wave-" + uuid.uuid4().hex[:10]


# ── Read ─────────────────────────────────────────────────────────────────────

def get_waves(session_id: str) -> list[dict]:
    """Return all waves for a session, ordered by wave_number."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mc_migration_waves WHERE session_id=%s ORDER BY wave_number",
            (session_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM mc_migration_waves LIMIT 0"
        ).description]
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            d["server_ids"] = json.loads(d.get("server_ids_json") or "[]")
            app_names = json.loads(d.get("app_names") or "[]")
            d["app_names"] = app_names
            d["app_count"] = d.get("app_count") or len(app_names)
            result.append(d)
        return result
    finally:
        conn.close()


def get_dependencies(session_id: str) -> list[dict]:
    """Return server dependency edges for a session."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mc_server_dependencies WHERE session_id=%s ORDER BY created_at",
            (session_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM mc_server_dependencies LIMIT 0"
        ).description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


# ── Write ────────────────────────────────────────────────────────────────────

def upsert_wave(session_id: str, wave_data: dict) -> dict:
    """Create or update a wave.  Returns the upserted wave dict."""
    wave_id = wave_data.get("id") or _wave_id()
    now = _now()
    server_ids = wave_data.get("server_ids", wave_data.get("server_ids_json", []))
    if isinstance(server_ids, list):
        server_ids_json = json.dumps(server_ids)
    else:
        server_ids_json = server_ids

    app_names = wave_data.get("app_names", [])
    if isinstance(app_names, list):
        app_names_json = json.dumps(app_names)
    else:
        app_names_json = app_names
        app_names = json.loads(app_names_json or "[]")
    app_count = wave_data.get("app_count", len(app_names))

    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM mc_migration_waves WHERE id=%s AND session_id=%s",
            (wave_id, session_id),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE mc_migration_waves SET
                   wave_number=%s, name=%s, cutover_date=%s, status=%s,
                   server_ids_json=%s, notes=%s, app_names=%s, app_count=%s
                   WHERE id=%s AND session_id=%s""",
                (
                    wave_data.get("wave_number", 1),
                    wave_data.get("name", "Wave"),
                    wave_data.get("cutover_date"),
                    wave_data.get("status", "planned"),
                    server_ids_json,
                    wave_data.get("notes"),
                    app_names_json,
                    app_count,
                    wave_id, session_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO mc_migration_waves
                   (id, session_id, wave_number, name, cutover_date, status,
                    server_ids_json, notes, created_at, classification,
                    app_names, app_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    wave_id, session_id,
                    wave_data.get("wave_number", 1),
                    wave_data.get("name", "Wave"),
                    wave_data.get("cutover_date"),
                    wave_data.get("status", "planned"),
                    server_ids_json,
                    wave_data.get("notes"),
                    now,
                    wave_data.get("classification", "CUI"),
                    app_names_json,
                    app_count,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    wave_data["id"] = wave_id
    wave_data["session_id"] = session_id
    wave_data["created_at"] = now
    wave_data["app_names"] = app_names
    wave_data["app_count"] = app_count
    return wave_data


def delete_wave(wave_id: str, session_id: str) -> bool:
    """Delete a wave.  Returns True if a row was removed."""
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM mc_migration_waves WHERE id=%s AND session_id=%s",
            (wave_id, session_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def upsert_dependency(session_id: str, dep: dict) -> dict:
    """Upsert a server dependency edge."""
    dep_id = dep.get("id") or "dep-" + uuid.uuid4().hex[:10]
    now = _now()
    conn = _conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO mc_server_dependencies
               (id, session_id, source_server_id, target_server_id,
                dep_type, direction, notes, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                dep_id, session_id,
                dep.get("source_server_id", ""),
                dep.get("target_server_id", ""),
                dep.get("dep_type", "network"),
                dep.get("direction", "bidirectional"),
                dep.get("notes"),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    dep["id"] = dep_id
    return dep


def delete_dependency(dep_id: str, session_id: str) -> bool:
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM mc_server_dependencies WHERE id=%s AND session_id=%s",
            (dep_id, session_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Auto-Assign ──────────────────────────────────────────────────────────────

_DEFAULT_WAVES = [
    {"wave_number": 1, "name": "Wave 1 — Rehost (Low Risk)", "status": "planned",
     "notes": "Low-risk servers: direct lift-and-shift to cloud."},
    {"wave_number": 2, "name": "Wave 2 — Replatform (Medium Risk)", "status": "planned",
     "notes": "Medium-risk servers: minor refactoring for cloud compatibility."},
    {"wave_number": 3, "name": "Wave 3 — Refactor (High Risk)", "status": "planned",
     "notes": "High-risk servers: application modernization required."},
]


def auto_assign_waves(session_id: str) -> dict:
    """Auto-assign server migration sessions to waves based on readiness scores.

    Wave 1 (rehost):   readiness >= 70
    Wave 2 (replatform): readiness 40–69
    Wave 3 (refactor): readiness < 40 or unknown

    Returns {"waves": [...], "assignments": {wave_id: [server_ids]}, "total_servers": N}
    """
    conn = _conn()
    try:
        servers = conn.execute(
            "SELECT id, src_hostname, readiness_score FROM mc_srv_sessions ORDER BY readiness_score DESC"
        ).fetchall()
    finally:
        conn.close()

    buckets: dict[int, list[str]] = {1: [], 2: [], 3: []}
    for sid_row in servers:
        srv_id, _hostname, score = sid_row[0], sid_row[1], sid_row[2] or 0
        if score >= 70:
            buckets[1].append(srv_id)
        elif score >= 40:
            buckets[2].append(srv_id)
        else:
            buckets[3].append(srv_id)

    # Delete existing waves and rebuild
    conn = _conn()
    try:
        conn.execute("DELETE FROM mc_migration_waves WHERE session_id=%s", (session_id,))
        conn.commit()
    finally:
        conn.close()

    created_waves = []
    assignments: dict[str, list[str]] = {}
    for tpl in _DEFAULT_WAVES:
        wave_server_ids = buckets[tpl["wave_number"]]
        wave_data = {**tpl, "server_ids": wave_server_ids}
        w = upsert_wave(session_id, wave_data)
        created_waves.append(w)
        assignments[w["id"]] = wave_server_ids

    # Populate app_names and app_count for each wave from server bindings
    conn = _conn()
    try:
        for w in created_waves:
            wave_server_ids = assignments[w["id"]]
            if not wave_server_ids:
                w["app_names"] = []
                w["app_count"] = 0
                continue
            placeholders = ",".join("?" * len(wave_server_ids))
            rows = conn.execute(
                f"SELECT DISTINCT b.app_id, a.name FROM mc_app_server_bindings b"  # nosec B608
                f" JOIN mc_app_inventory a ON a.id = b.app_id"
                f" WHERE b.server_id IN ({placeholders})",
                wave_server_ids,
            ).fetchall()
            app_names = [r[1] for r in rows]
            app_count = len(app_names)
            conn.execute(
                "UPDATE mc_migration_waves SET app_names=%s, app_count=%s WHERE id=%s",
                (json.dumps(app_names), app_count, w["id"]),
            )
            w["app_names"] = app_names
            w["app_count"] = app_count
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "waves": created_waves,
        "assignments": assignments,
        "total_servers": len(servers),
    }


# ── Graph Data (Sigma.js) ─────────────────────────────────────────────────────

_WAVE_COLORS = {
    1: "#27ae60",
    2: "#e67e22",
    3: "#c0392b",
    None: "#4a6080",
}


def build_graph(session_id: str) -> dict:
    """Build Sigma.js-compatible graph nodes/edges from waves and dependencies."""
    waves = get_waves(session_id)
    deps = get_dependencies(session_id)

    wave_lookup: dict[str, int] = {}
    for w in waves:
        for srv_id in w.get("server_ids", []):
            wave_lookup[srv_id] = w["wave_number"]

    # Collect all server IDs
    server_ids: set[str] = set()
    for w in waves:
        server_ids.update(w.get("server_ids", []))
    for d in deps:
        server_ids.add(d["source_server_id"])
        server_ids.add(d["target_server_id"])

    # Lay nodes out in rows by wave
    nodes = []
    wave_servers: dict[int | None, list[str]] = {}
    for srv in server_ids:
        wn = wave_lookup.get(srv)
        wave_servers.setdefault(wn, []).append(srv)

    x_spacing = 180
    y_spacing = 120
    for wn, srvs in sorted(wave_servers.items(), key=lambda kv: (kv[0] is None, kv[0])):
        base_x = ((wn or 0) - 1) * x_spacing
        for i, srv in enumerate(srvs):
            nodes.append({
                "id": srv,
                "label": srv[:16],
                "x": base_x,
                "y": i * y_spacing,
                "size": 12,
                "color": _WAVE_COLORS.get(wn, _WAVE_COLORS[None]),
            })

    edges = []
    for d in deps:
        edges.append({
            "id": d["id"],
            "source": d["source_server_id"],
            "target": d["target_server_id"],
            "label": d.get("dep_type", ""),
            "color": "#2980b9",
            "size": 2,
        })

    return {"nodes": nodes, "edges": edges}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="MCE Wave Planner — manage migration waves via CLI")
    ap.add_argument("--session-id", required=True, help="Migration session ID")
    action_grp = ap.add_mutually_exclusive_group(required=True)
    action_grp.add_argument("--list", action="store_true", help="List waves for the session")
    action_grp.add_argument("--graph", action="store_true", help="Output Sigma.js graph data")
    action_grp.add_argument("--auto-assign", action="store_true", help="Auto-assign servers to waves")
    action_grp.add_argument("--deps", action="store_true", help="List server dependencies")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    if args.list:
        result = get_waves(args.session_id)
    elif args.graph:
        result = build_graph(args.session_id)
    elif args.auto_assign:
        result = auto_assign_waves(args.session_id)
    else:
        result = get_dependencies(args.session_id)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        if isinstance(result, list):
            print(f"[wave_planner] {len(result)} item(s) for session {args.session_id}")
            for item in result[:10]:
                print(f"  {item}")
        else:
            print(f"[wave_planner] {result}")


if __name__ == "__main__":
    main()
