# CUI // SP-CTI
"""OPT-57 — Sandbox liveness API.

Endpoints:
  GET /api/sandbox/liveness — latest liveness probe summary from audit_trail
  GET /api/sandbox/log       — recent sandbox_execution_log rows
  GET /sandbox              — sandbox-status HTML page
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, render_template_string, request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

sandbox_api = Blueprint("sandbox_api", __name__)


def _parse_details(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        v = json.loads(raw)
    except Exception:
        return {}
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return {}
    return v if isinstance(v, dict) else {}


def _iso(dt):
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


@sandbox_api.route("/api/sandbox/liveness", methods=["GET"])
def sandbox_liveness():
    """Return the latest sandbox liveness probe summary.

    Reads from audit_trail where action='sandbox.liveness_probe'.
    Shape matches tools/maintenance/sandbox_smoke.py run_probe() output.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, details, created_at
            FROM audit_trail
            WHERE action = 'sandbox.liveness_probe'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if row is None:
            return jsonify({
                "status": "no_data",
                "healthy": False,
                "smoke_passed": False,
                "probe_at": None,
                "message": "No liveness probe has run yet. Invoke "
                           "python tools/maintenance/sandbox_smoke.py --json",
            })
        details = _parse_details(row.get("details"))
        out = {
            "id": row.get("id"),
            "probe_at": details.get("probe_at") or _iso(row.get("created_at")),
            "status": "healthy" if details.get("exit_code") == 0 else (
                "smoke_failed" if details.get("exit_code") == 2 else "degraded"),
            "healthy": bool(details.get("health", {}).get("available")
                            and details.get("health", {}).get("docker_running")),
            "smoke_passed": bool(details.get("smoke", {}).get("exit_code") == 0
                                 and "ok" in (details.get("smoke", {}).get("stdout") or "").lower()),
            "health": details.get("health", {}),
            "smoke": details.get("smoke", {}),
            "duration_ms": details.get("duration_ms"),
            "exit_code": details.get("exit_code"),
        }
        # Annotate staleness vs 24h window
        try:
            created = row.get("created_at")
            if created is not None:
                if isinstance(created, str):
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age = datetime.now(timezone.utc) - (created if created.tzinfo
                                                     else created.replace(tzinfo=timezone.utc))
                out["age_hours"] = round(age.total_seconds() / 3600, 2)
                out["fresh"] = age < timedelta(hours=24)
        except Exception:
            out["age_hours"] = None
            out["fresh"] = False
        return jsonify(out)
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)[:300]}), 500


@sandbox_api.route("/api/sandbox/log", methods=["GET"])
def sandbox_log():
    """Return recent rows from sandbox_execution_log (if the table exists)."""
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
    except (TypeError, ValueError):
        limit = 20
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_name = 'sandbox_execution_log'""")
        if cur.fetchone() is None:
            return jsonify({"rows": [], "total": 0,
                            "message": "sandbox_execution_log table does not exist yet"})
        cur.execute("""
            SELECT * FROM sandbox_execution_log
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        # Stringify timestamps for JSON
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
        return jsonify({"rows": rows, "total": len(rows)})
    except Exception as exc:
        return jsonify({"rows": [], "total": 0, "error": str(exc)[:300]}), 500


_SANDBOX_PAGE = """
{% extends "base.html" %}
{% block title %}Sandbox Status - ICDEV™ Dashboard{% endblock %}
{% block content %}
<div class="page-header">
    <h1>Sandbox Status</h1>
    <p class="subtitle">LLM-Sandbox liveness probe and recent execution log.</p>
</div>

<div class="card" style="padding:20px; margin-bottom:24px;">
    <div class="card-label">Latest liveness probe</div>
    <pre id="sandbox-liveness" style="margin-top:12px; padding:12px; background:var(--bg-code); border-radius:6px; overflow:auto; max-height:300px;">Loading…</pre>
</div>

<div class="card" style="padding:20px;">
    <div class="card-label">Recent sandbox_execution_log (up to 20)</div>
    <table style="width:100%; margin-top:12px; font-size:13px;">
        <thead>
            <tr style="text-align:left; color:var(--text-dim);">
                <th>When</th>
                <th>Executor</th>
                <th>Language</th>
                <th>Status</th>
                <th>Exit</th>
                <th>Runtime</th>
            </tr>
        </thead>
        <tbody id="sandbox-log-tbody">
            <tr><td colspan="6" style="color:var(--text-dim); padding:16px;">Loading…</td></tr>
        </tbody>
    </table>
</div>

<script>
(function () {
    fetch('/api/sandbox/liveness').then(function (r) { return r.json(); }).then(function (d) {
        var el = document.getElementById('sandbox-liveness');
        if (el) el.textContent = JSON.stringify(d, null, 2);
    }).catch(function () {
        var el = document.getElementById('sandbox-liveness');
        if (el) el.textContent = 'liveness endpoint error';
    });

    fetch('/api/sandbox/log?limit=20').then(function (r) { return r.json(); }).then(function (d) {
        var tbody = document.getElementById('sandbox-log-tbody');
        if (!tbody) return;
        var rows = d.rows || [];
        if (rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-dim); padding:16px;">'
                + (d.message || 'No sandbox executions recorded yet.') + '</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map(function (r) {
            return '<tr style="border-top:1px solid var(--border);">'
                + '<td style="padding:6px 4px;">' + (r.created_at || '') + '</td>'
                + '<td style="padding:6px 4px;">' + (r.executor_type || '') + '</td>'
                + '<td style="padding:6px 4px;">' + (r.language || '') + '</td>'
                + '<td style="padding:6px 4px;">' + (r.status || '') + '</td>'
                + '<td style="padding:6px 4px;">' + (r.exit_code != null ? r.exit_code : '') + '</td>'
                + '<td style="padding:6px 4px;">' + (r.runtime_ms != null ? r.runtime_ms + ' ms' : '') + '</td>'
                + '</tr>';
        }).join('');
    }).catch(function () {
        var tbody = document.getElementById('sandbox-log-tbody');
        if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-dim); padding:16px;">log endpoint error</td></tr>';
    });
})();
</script>
{% endblock %}
"""


@sandbox_api.route("/sandbox", methods=["GET"])
def sandbox_page():
    """Render the sandbox status page."""
    return render_template_string(_SANDBOX_PAGE)
