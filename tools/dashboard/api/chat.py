# [TEMPLATE: CUI // SP-CTI]
"""Flask Blueprint for multi-stream parallel chat API (Phase 44 — D257-D260).

Provides endpoints for creating/managing chat contexts, sending messages,
mid-stream intervention, and dirty-tracking state queries.
"""

import sys
from pathlib import Path


from flask import Blueprint, jsonify, request

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Backend imports (graceful)
# ---------------------------------------------------------------------------

try:
    from tools.dashboard.chat_manager import chat_manager

    _HAS_CHAT = True
except ImportError:
    _HAS_CHAT = False
    chat_manager = None

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

chat_api = Blueprint("chat_api", __name__, url_prefix="/api/chat")


def _require_chat():
    """Check that chat manager is available."""
    if not _HAS_CHAT or chat_manager is None:
        return jsonify({"error": "Chat manager not available"}), 503
    return None


# ---------------------------------------------------------------------------
# Context endpoints
# ---------------------------------------------------------------------------


@chat_api.route("/contexts", methods=["POST"])
def create_context():
    """Create a new chat context.

    Body: {user_id, tenant_id?, title?, project_id?, agent_model?, system_prompt?}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    user_id = data.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    result = chat_manager.create_context(
        user_id=user_id,
        tenant_id=data.get("tenant_id", ""),
        title=data.get("title", ""),
        project_id=data.get("project_id", ""),
        agent_model=data.get("agent_model", "sonnet"),
        system_prompt=data.get("system_prompt", ""),
    )

    if "error" in result:
        return jsonify(result), 429  # Rate limit / max concurrent
    return jsonify(result), 201


@chat_api.route("/contexts", methods=["GET"])
def list_contexts():
    """List chat contexts.

    Query params: user_id?, tenant_id?, include_closed?
    """
    err = _require_chat()
    if err:
        return err

    user_id = request.args.get("user_id", "")
    tenant_id = request.args.get("tenant_id", "")
    include_closed = request.args.get("include_closed", "false").lower() == "true"

    contexts = chat_manager.list_contexts(
        user_id=user_id,
        tenant_id=tenant_id,
        include_closed=include_closed,
    )
    return jsonify({"contexts": contexts, "total": len(contexts)})


@chat_api.route("/contexts/<context_id>", methods=["GET"])
def get_context(context_id):
    """Get context details with recent messages."""
    err = _require_chat()
    if err:
        return err

    ctx = chat_manager.get_context(context_id)
    if not ctx:
        return jsonify({"error": "Context not found"}), 404

    # Include recent messages
    messages = chat_manager.get_messages(context_id, since_turn=0, limit=50)
    ctx["messages"] = messages
    return jsonify(ctx)


# ---------------------------------------------------------------------------
# Message endpoints
# ---------------------------------------------------------------------------


@chat_api.route("/<context_id>/send", methods=["POST"])
def send_message(context_id):
    """Send a message to a context.

    Body: {content, role?}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400

    role = data.get("role", "user")
    result = chat_manager.send_message(context_id, content, role=role)

    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@chat_api.route("/<context_id>/intervene", methods=["POST"])
def intervene(context_id):
    """Mid-stream intervention (D265-D267).

    Body: {message}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message required"}), 400

    result = chat_manager.intervene(context_id, message)

    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@chat_api.route("/<context_id>/messages", methods=["GET"])
def get_messages(context_id):
    """Get messages for a context.

    Query params: since? (turn number), limit?
    """
    err = _require_chat()
    if err:
        return err

    since = request.args.get("since", 0, type=int)
    limit = request.args.get("limit", 100, type=int)

    messages = chat_manager.get_messages(context_id, since_turn=since, limit=limit)
    return jsonify({"context_id": context_id, "messages": messages, "total": len(messages)})


# ---------------------------------------------------------------------------
# State endpoints
# ---------------------------------------------------------------------------


@chat_api.route("/<context_id>/state", methods=["GET"])
def get_state(context_id):
    """Get context state with dirty-tracking (Feature 4).

    Query params: since_version? (dirty version)
    """
    err = _require_chat()
    if err:
        return err

    ctx = chat_manager.get_context(context_id)
    if not ctx:
        return jsonify({"error": "Context not found"}), 404

    since_version = request.args.get("since_version", 0, type=int)

    # Get incremental updates from state tracker if available
    try:
        from tools.dashboard.state_tracker import state_tracker

        client_id = request.args.get("client_id", request.remote_addr or "unknown")
        updates = state_tracker.get_updates(client_id, context_id, since_version)
        ctx["state_updates"] = updates
    except ImportError:
        ctx["state_updates"] = {"up_to_date": True, "changes": []}

    return jsonify(ctx)


@chat_api.route("/<context_id>/close", methods=["POST"])
def close_context(context_id):
    """Close/archive a chat context."""
    err = _require_chat()
    if err:
        return err

    result = chat_manager.close_context(context_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ---------------------------------------------------------------------------
# Use Cases catalog (FORGE-pattern: reads args/use_cases.yaml)
# ---------------------------------------------------------------------------

_USE_CASES_PATH = BASE_DIR / "args" / "use_cases.yaml"


def _uc_load_yaml():
    import yaml as _yaml
    try:
        with open(_USE_CASES_PATH, "r", encoding="utf-8") as fh:
            return (_yaml.safe_load(fh) or {}).get("use_cases", [])
    except Exception:
        return []


def _uc_init_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS use_case_overrides (
        id TEXT PRIMARY KEY,
        label TEXT, description TEXT, icon TEXT, badge TEXT,
        agent_model TEXT, ricoas INTEGER, boost_threshold INTEGER,
        system_prompt TEXT, seed_message TEXT,
        canvas_wiring TEXT, quick_actions TEXT,
        updated_at TEXT, updated_by TEXT,
        classification TEXT DEFAULT NULL,
        fast_track INTEGER DEFAULT 0,
        skip_requirement_types TEXT,
        user_config TEXT
    )""")
    # Idempotent column migrations for existing tables
    existing = {row[1] for row in conn.execute("PRAGMA table_info(use_case_overrides)").fetchall()}
    for col_def in [
        ("classification", "TEXT DEFAULT NULL"),
        ("fast_track", "INTEGER DEFAULT 0"),
        ("skip_requirement_types", "TEXT"),
        ("user_config", "TEXT"),
    ]:
        if col_def[0] not in existing:
            try:
                conn.execute(f"ALTER TABLE use_case_overrides ADD COLUMN {col_def[0]} {col_def[1]}")
            except Exception:
                pass
    conn.commit()


def _uc_apply_override(base, row):
    import json as _json
    if not row:
        return base
    result = dict(base)
    for col in ("label", "description", "icon", "badge", "agent_model",
                "system_prompt", "seed_message"):
        if row[col] is not None:
            result[col] = row[col]
    if row["ricoas"] is not None:
        result["ricoas"] = bool(row["ricoas"])
    if row["boost_threshold"] is not None:
        result["boost_threshold"] = row["boost_threshold"]
    if row["fast_track"] is not None:
        result["fast_track"] = bool(row["fast_track"])
    for jcol in ("canvas_wiring", "quick_actions", "skip_requirement_types", "user_config"):
        if row[jcol] is not None:
            try:
                parsed = _json.loads(row[jcol])
                # For user_config, merge DB additions on top of YAML defaults (union, not replace)
                if jcol == "user_config" and isinstance(parsed, dict) and isinstance(result.get("user_config"), dict):
                    merged_uc = dict(result["user_config"])
                    for k, v in parsed.items():
                        if k in merged_uc and isinstance(merged_uc[k], dict) and isinstance(v, dict):
                            base_defaults = merged_uc[k].get("defaults", [])
                            db_defaults = v.get("defaults", [])
                            merged_uc[k] = {"defaults": list(dict.fromkeys(base_defaults + db_defaults))}
                        else:
                            merged_uc[k] = v
                    result["user_config"] = merged_uc
                else:
                    result[jcol] = parsed
            except Exception:
                pass
    return result


@chat_api.route("/use-cases", methods=["GET"])
def list_use_cases():
    """Return use case catalog (YAML defaults merged with DB overrides)."""
    from tools.db.storage import get_connection as _gc
    cases = _uc_load_yaml()
    category = request.args.get("category", "").strip().lower()
    query = request.args.get("q", "").strip().lower()

    overrides = {}
    try:
        with _gc() as conn:
            for row in conn.execute("SELECT * FROM use_case_overrides").fetchall():
                overrides[row["id"]] = row
    except Exception:
        pass

    merged = [_uc_apply_override(c, overrides.get(c.get("id", ""))) for c in cases]
    if category:
        merged = [c for c in merged if c.get("category", "").lower() == category]
    if query:
        merged = [c for c in merged if query in c.get("label", "").lower()
                  or query in (c.get("description") or "").lower()]

    summary = [
        {
            "id": c.get("id", ""),
            "label": c.get("label", ""),
            "category": c.get("category", ""),
            "icon": c.get("icon", ""),
            "description": (c.get("description") or "").strip(),
            "badge": c.get("badge", ""),
            "agent_model": c.get("agent_model", "kimi-cloud"),
            "ricoas": c.get("ricoas", False),
            "fast_track": c.get("fast_track", False),
            "boost_threshold": c.get("boost_threshold", 70),
            "skip_requirement_types": c.get("skip_requirement_types", []),
            "user_config": c.get("user_config", {}),
            "canvas_wiring": c.get("canvas_wiring", []),
            "quick_actions": c.get("quick_actions", []),
        }
        for c in merged
    ]
    return jsonify({"use_cases": summary, "total": len(summary)})


@chat_api.route("/use-cases/<use_case_id>", methods=["GET"])
def get_use_case(use_case_id):
    """Return full use case definition (YAML + DB override merged)."""
    from tools.db.storage import get_connection as _gc
    base = next((c for c in _uc_load_yaml() if c.get("id") == use_case_id), None)
    if not base:
        return jsonify({"error": "Use case not found"}), 404
    row = None
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            row = conn.execute(
                "SELECT * FROM use_case_overrides WHERE id = ?", (use_case_id,)
            ).fetchone()
    except Exception:
        pass
    return jsonify(_uc_apply_override(dict(base), row))


@chat_api.route("/use-cases/<use_case_id>", methods=["PUT"])
def update_use_case(use_case_id):
    """Persist user overrides for a use case (YAML unchanged, overrides in DB)."""
    import json as _json
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    body = request.get_json(silent=True) or {}
    now = datetime.now(timezone.utc).isoformat()
    cw = body.get("canvas_wiring")
    qa = body.get("quick_actions")
    srt = body.get("skip_requirement_types")
    uc = body.get("user_config")
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            conn.execute("""
                INSERT INTO use_case_overrides
                    (id,label,description,icon,badge,agent_model,ricoas,
                     boost_threshold,system_prompt,seed_message,
                     canvas_wiring,quick_actions,updated_at,updated_by,
                     fast_track,skip_requirement_types,user_config)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label, description=excluded.description,
                    icon=excluded.icon, badge=excluded.badge,
                    agent_model=excluded.agent_model, ricoas=excluded.ricoas,
                    boost_threshold=excluded.boost_threshold,
                    system_prompt=excluded.system_prompt,
                    seed_message=excluded.seed_message,
                    canvas_wiring=excluded.canvas_wiring,
                    quick_actions=excluded.quick_actions,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by,
                    fast_track=excluded.fast_track,
                    skip_requirement_types=excluded.skip_requirement_types,
                    user_config=excluded.user_config
            """, (
                use_case_id,
                body.get("label"), body.get("description"), body.get("icon"),
                body.get("badge"), body.get("agent_model"),
                1 if body.get("ricoas") else 0,
                body.get("boost_threshold"),
                body.get("system_prompt"), body.get("seed_message"),
                _json.dumps(cw) if cw is not None else None,
                _json.dumps(qa) if qa is not None else None,
                now, body.get("updated_by", "dashboard-user"),
                1 if body.get("fast_track") else 0,
                _json.dumps(srt) if srt is not None else None,
                _json.dumps(uc) if uc is not None else None,
            ))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "updated_at": now})


@chat_api.route("/use-cases/<use_case_id>/override", methods=["DELETE"])
def reset_use_case(use_case_id):
    """Delete DB override — restores YAML factory defaults."""
    from tools.db.storage import get_connection as _gc
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            conn.execute("DELETE FROM use_case_overrides WHERE id=?", (use_case_id,))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "reset": True})


# ---------------------------------------------------------------------------
# Standalone HTML app generator
# ---------------------------------------------------------------------------


@chat_api.route("/use-cases/<use_case_id>/standalone", methods=["GET"])
def standalone_app(use_case_id):
    """Generate and return a self-contained HTML app for the use case."""
    from flask import Response as _Resp
    base = next((c for c in _uc_load_yaml() if c.get("id") == use_case_id), None)
    if not base:
        return jsonify({"error": "Use case not found"}), 404
    row = None
    try:
        from tools.db.storage import get_connection as _gc
        with _gc() as conn:
            _uc_init_table(conn)
            row = conn.execute(
                "SELECT * FROM use_case_overrides WHERE id = ?", (use_case_id,)
            ).fetchone()
    except Exception:
        pass
    uc = _uc_apply_override(dict(base), row)
    html = _build_standalone_html(uc)
    filename = use_case_id.replace("_", "-") + "-standalone.html"
    return _Resp(
        html,
        mimetype="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_standalone_html(uc: dict) -> str:
    """Build a fully self-contained, dependency-free HTML app for the given use case."""
    import html as _html
    label = uc.get("label", "Use Case")
    description = (uc.get("description") or "").strip()
    category = uc.get("category", "general")
    icon = uc.get("icon", "⚙")
    template_reqs = uc.get("template_requirements", [])
    user_config = uc.get("user_config") or {}

    # Determine column config based on category
    if category == "budget":
        columns = ["Item / Description", "Vendor", "Qty", "Unit Price ($)", "Total ($)", "Tier", "Status"]
        col_keys = ["item", "vendor", "qty", "unit_price", "total", "tier", "status"]
        tier_options = '<option value="Tier 1">Tier 1 (Execute)</option><option value="Tier 2">Tier 2 (Backup)</option>'
        status_options = '<option value="Draft">Draft</option><option value="Quoted">Quoted</option><option value="Approved">Approved</option><option value="Obligated">Obligated</option>'
        extra_controls = """
        <div class="budget-summary" id="budget-summary">
            <div class="summary-item"><span>Tier 1 Total:</span><strong id="tier1-total">$0.00</strong></div>
            <div class="summary-item"><span>Tier 2 Total:</span><strong id="tier2-total">$0.00</strong></div>
            <div class="summary-item total"><span>Grand Total:</span><strong id="grand-total">$0.00</strong></div>
        </div>"""
        update_summary_js = """
        function updateSummary() {
            var items = getData();
            var t1 = 0, t2 = 0;
            items.forEach(function(it) {
                var total = parseFloat((it.total || '').toString().replace(/[^0-9.]/g,'')) || 0;
                if (it.tier === 'Tier 1') t1 += total; else t2 += total;
            });
            var fmt = function(n) { return '$' + n.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ','); };
            document.getElementById('tier1-total').textContent = fmt(t1);
            document.getElementById('tier2-total').textContent = fmt(t2);
            document.getElementById('grand-total').textContent = fmt(t1 + t2);
        }"""
        after_render_call = "updateSummary();"
        input_change_call = "updateSummary();"
    elif category == "modernization":
        columns = ["Asset Name", "Type", "Version / Age", "7Rs Classification", "Phase", "Risk", "Notes"]
        col_keys = ["asset", "type", "version", "classification_7r", "phase", "risk", "notes"]
        tier_options = '<option value="Retire">Retire</option><option value="Retain">Retain</option><option value="Rehost">Rehost</option><option value="Replatform">Replatform</option><option value="Repurchase">Repurchase</option><option value="Refactor">Refactor</option><option value="Re-architect">Re-architect</option>'
        status_options = '<option value="Phase 1">Phase 1</option><option value="Phase 2">Phase 2</option><option value="Phase 3">Phase 3</option><option value="Post-Go-Live">Post-Go-Live</option>'
        extra_controls = ""
        update_summary_js = ""
        after_render_call = ""
        input_change_call = ""
    else:  # knowledge / document_refresh / general
        columns = ["Document / Section", "Owner", "Last Review", "Staleness", "Status", "Contributor", "Notes"]
        col_keys = ["document", "owner", "last_review", "staleness", "status", "contributor", "notes"]
        tier_options = '<option value="Current">Current</option><option value="Stale">Stale</option><option value="Critical">Critical (Action Required)</option>'
        status_options = '<option value="Draft">Draft Submitted</option><option value="In Review">In Review</option><option value="Approved">Approved</option><option value="Rebuilt">AI Rebuilt</option>'
        extra_controls = ""
        update_summary_js = ""
        after_render_call = ""
        input_change_call = ""

    # Build requirements checklist HTML
    req_html = ""
    for i, req in enumerate(template_reqs[:20]):  # cap at 20 for compactness
        req_text = _html.escape(req.get("text", "")[:120])
        req_type = _html.escape(req.get("type", ""))
        req_priority = _html.escape(req.get("priority", ""))
        req_html += f'<li class="req-item"><span class="req-badge req-badge--{req_type}">{req_type}</span> <span class="req-priority req-priority--{req_priority}">{req_priority}</span> {req_text}</li>\n'

    vendors = user_config.get("vendors", {}).get("defaults", [])
    vendor_opts = "".join(f"<option>{_html.escape(v)}</option>" for v in vendors) or "<option>Custom</option>"

    # Build header column labels
    th_html = "".join(f"<th>{_html.escape(c)}</th>" for c in columns) + "<th></th>"

    # Build input row cells
    def make_input(key, idx, col_name):
        if key in ("tier", "classification_7r", "staleness"):
            return f'<td><select class="cell-input" data-key="{key}">{tier_options}</select></td>'
        if key == "status":
            return f'<td><select class="cell-input" data-key="{key}">{status_options}</select></td>'
        if key == "phase":
            return f'<td><select class="cell-input" data-key="{key}">{status_options}</select></td>'
        if key == "vendor":
            return f'<td><select class="cell-input" data-key="{key}"><option value="">—</option>{vendor_opts}<option value="__custom__">Other...</option></select></td>'
        if key == "qty":
            return f'<td><input type="number" class="cell-input" data-key="{key}" min="1" value="1" style="width:60px"></td>'
        if key in ("unit_price",):
            return f'<td><input type="number" class="cell-input" data-key="{key}" min="0" step="0.01" placeholder="0.00" style="width:90px"></td>'
        if key == "total":
            return f'<td><input type="number" class="cell-input" data-key="{key}" min="0" step="0.01" placeholder="auto" style="width:90px" readonly></td>'
        if key == "last_review":
            return f'<td><input type="date" class="cell-input" data-key="{key}"></td>'
        return f'<td><input type="text" class="cell-input" data-key="{key}" placeholder="{_html.escape(col_name)}..."></td>'

    label_esc = _html.escape(label)
    desc_esc = _html.escape(description)
    icon_esc = _html.escape(icon)
    storage_key = f"icdev_standalone_{uc.get('id', 'app')}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{label_esc} — Standalone</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f1117;color:#c9d1d9;font-size:14px}}
.app-header{{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;gap:12px}}
.app-header h1{{font-size:1.1rem;font-weight:600;color:#e6edf3}}
.app-header .desc{{font-size:0.8rem;color:#8b949e;margin-top:2px}}
.badge{{font-size:0.65rem;font-weight:700;padding:2px 7px;border-radius:10px;background:#1f6feb;color:#fff;vertical-align:middle}}
.main{{padding:20px 24px;max-width:1400px;margin:0 auto}}
.section{{background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:16px;overflow:hidden}}
.section-header{{padding:10px 16px;background:#1c2128;border-bottom:1px solid #30363d;display:flex;align-items:center;justify-content:space-between}}
.section-header h2{{font-size:0.85rem;font-weight:600;color:#e6edf3;letter-spacing:0.04em;text-transform:uppercase}}
.section-body{{padding:16px}}
.toolbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.btn{{padding:6px 14px;border:1px solid #30363d;border-radius:6px;cursor:pointer;font-size:0.8rem;font-weight:600;transition:all 0.15s}}
.btn-primary{{background:#1f6feb;color:#fff;border-color:#1f6feb}}.btn-primary:hover{{background:#388bfd}}
.btn-secondary{{background:#21262d;color:#c9d1d9}}.btn-secondary:hover{{background:#30363d}}
.btn-danger{{background:transparent;color:#f85149;border-color:#f85149}}.btn-danger:hover{{background:#f85149;color:#fff}}
.btn-sm{{padding:3px 9px;font-size:0.72rem}}
table{{width:100%;border-collapse:collapse;font-size:0.8rem}}
th{{padding:7px 8px;text-align:left;color:#8b949e;font-weight:600;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid #30363d;background:#1c2128}}
td{{padding:5px 6px;border-bottom:1px solid #21262d;vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#1c2128}}
.cell-input{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:4px 6px;color:#e6edf3;font-size:0.8rem}}
.cell-input:focus{{outline:none;border-color:#388bfd}}
.cell-input[readonly]{{color:#8b949e;background:#161b22}}
.delete-btn{{background:none;border:none;color:#6e7681;cursor:pointer;font-size:0.9rem;padding:2px 6px}}
.delete-btn:hover{{color:#f85149}}
.budget-summary{{display:flex;gap:20px;padding:12px 16px;background:#1c2128;border-top:1px solid #30363d;font-size:0.85rem;flex-wrap:wrap}}
.summary-item{{display:flex;gap:8px;align-items:center}}.summary-item.total strong{{color:#58a6ff;font-size:1rem}}
.req-list{{list-style:none;display:flex;flex-direction:column;gap:6px;max-height:280px;overflow-y:auto;padding:4px 0}}
.req-item{{display:flex;align-items:flex-start;gap:6px;font-size:0.78rem;line-height:1.45;color:#c9d1d9}}
.req-badge{{font-size:0.62rem;font-weight:700;padding:1px 5px;border-radius:9px;white-space:nowrap;flex-shrink:0;margin-top:1px}}
.req-badge--functional{{background:#1f6feb20;color:#58a6ff;border:1px solid #1f6feb}}
.req-badge--data{{background:#2ea04320;color:#3fb950;border:1px solid #2ea043}}
.req-badge--performance{{background:#d29922 20;color:#d29922;border:1px solid #d29922}}
.req-badge--compliance{{background:#f8514920;color:#f85149;border:1px solid #f85149}}
.req-badge--interface{{background:#bc8cff20;color:#bc8cff;border:1px solid #bc8cff}}
.req-priority{{font-size:0.6rem;padding:1px 4px;border-radius:4px;white-space:nowrap;flex-shrink:0;margin-top:2px}}
.req-priority--critical{{background:#f85149;color:#fff}}.req-priority--high{{background:#d29922;color:#fff}}
.req-priority--medium{{background:#8b949e;color:#fff}}.req-priority--low{{background:#21262d;color:#8b949e}}
.empty{{color:#6e7681;font-style:italic;padding:20px;text-align:center}}
.meta-row{{display:flex;gap:16px;flex-wrap:wrap;font-size:0.78rem;color:#8b949e;padding:8px 0}}
.meta-row span strong{{color:#c9d1d9}}
.print-note{{font-size:0.72rem;color:#6e7681;margin-top:8px}}
@media print{{.toolbar{{display:none}}.app-header{{background:#fff;color:#000;border-bottom:1px solid #ccc}}.section{{border:1px solid #ccc}}.cell-input{{border:none;background:transparent;color:#000}}body{{background:#fff;color:#000}}}}
</style>
</head>
<body>
<div class="app-header">
  <span style="font-size:1.6rem">{icon_esc}</span>
  <div>
    <h1>{label_esc} <span class="badge">Standalone</span></h1>
    <div class="desc">{desc_esc}</div>
  </div>
  <div style="margin-left:auto;font-size:0.72rem;color:#6e7681">Generated by ICDEV™ &bull; Data saved locally in your browser</div>
</div>
<div class="main">
  <div class="section">
    <div class="section-header">
      <h2>Tracker</h2>
      <div class="toolbar">
        <button class="btn btn-primary btn-sm" onclick="addRow()">+ Add Row</button>
        <button class="btn btn-secondary btn-sm" onclick="exportCSV()">Export CSV</button>
        <button class="btn btn-secondary btn-sm" onclick="printApp()">Print</button>
        <button class="btn btn-danger btn-sm" onclick="clearAll()">Clear All</button>
      </div>
    </div>
    <div style="overflow-x:auto">
      <table id="tracker-table">
        <thead><tr>{th_html}</tr></thead>
        <tbody id="tracker-body"></tbody>
      </table>
      <div id="empty-msg" class="empty" style="display:none">No rows yet. Click &quot;+ Add Row&quot; to start.</div>
    </div>
    {extra_controls}
    <div class="section-body" style="padding-top:8px">
      <p class="print-note">Data is auto-saved in your browser&apos;s localStorage. Export to CSV to back it up.</p>
    </div>
  </div>

  <div class="section">
    <div class="section-header"><h2>Pre-Built Requirements Framework ({len(template_reqs)} requirements)</h2></div>
    <div class="section-body">
      <ul class="req-list">
{req_html}      </ul>
    </div>
  </div>

  <div class="section">
    <div class="section-header"><h2>About This App</h2></div>
    <div class="section-body">
      <div class="meta-row">
        <span><strong>Use Case:</strong> {label_esc}</span>
        <span><strong>Category:</strong> {_html.escape(category)}</span>
        <span><strong>Storage:</strong> Browser localStorage (no server needed)</span>
        <span><strong>ICDEV™:</strong> Integrated via /chat when you&apos;re ready for AI Boost + Kanban</span>
      </div>
    </div>
  </div>
</div>
<script>
var STORAGE_KEY = '{storage_key}';
var COL_KEYS = {str(col_keys).replace("'", '"')};

function getData() {{
  try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); }} catch(e) {{ return []; }}
}}
function saveData(items) {{
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
}}
function renderTable() {{
  var items = getData();
  var tbody = document.getElementById('tracker-body');
  var empty = document.getElementById('empty-msg');
  if (!items.length) {{ tbody.innerHTML = ''; empty.style.display = 'block'; {after_render_call} return; }}
  empty.style.display = 'none';
  tbody.innerHTML = '';
  items.forEach(function(item, idx) {{
    var tr = document.createElement('tr');
    tr.dataset.idx = idx;
    var cells = COL_KEYS.map(function(key) {{
      var val = item[key] || '';
      var cell = document.createElement('td');
      var input = tr.querySelector ? null : null;
      cell.innerHTML = makeCell(key, val);
      return cell;
    }});
    cells.forEach(function(c) {{ tr.appendChild(c); }});
    var del = document.createElement('td');
    del.innerHTML = '<button class="delete-btn" title="Delete row" onclick="deleteRow(' + idx + ')">&#x2715;</button>';
    tr.appendChild(del);
    tbody.appendChild(tr);
    tr.querySelectorAll('.cell-input').forEach(function(inp) {{
      inp.addEventListener('change', function() {{ updateRow(idx, inp.dataset.key, inp.value); {input_change_call} }});
      inp.addEventListener('input', function() {{
        if (inp.dataset.key === 'qty' || inp.dataset.key === 'unit_price') autoTotal(idx);
      }});
    }});
  }});
  {after_render_call}
}}
function makeCell(key, val) {{
  var v = val ? val.replace(/"/g,'&quot;') : '';
  if (key === 'tier' || key === 'classification_7r' || key === 'staleness') {{
    var opts = document.createElement('select'); opts.className='cell-input'; opts.dataset={{ key: key }};
    var html = '{tier_options}'.replace(/data-key="[^"]*"/, 'data-key="'+key+'"');
    return '<select class="cell-input" data-key="'+key+'">'+html+'</select>';
  }}
  if (key === 'status' || key === 'phase') {{
    return '<select class="cell-input" data-key="'+key+'">{status_options}</select>';
  }}
  if (key === 'vendor') {{
    return '<select class="cell-input" data-key="'+key+'"><option value="">—</option>{vendor_opts}<option value="__custom__">Other...</option></select>';
  }}
  if (key === 'total' || key === 'unit_price') return '<input type="number" class="cell-input" data-key="'+key+'" value="'+v+'" min="0" step="0.01"' + (key==='total'?' readonly':'') + '>';
  if (key === 'qty') return '<input type="number" class="cell-input" data-key="'+key+'" value="'+(v||1)+'" min="1" style="width:60px">';
  if (key === 'last_review') return '<input type="date" class="cell-input" data-key="'+key+'" value="'+v+'">';
  return '<input type="text" class="cell-input" data-key="'+key+'" value="'+v+'">';
}}
function addRow() {{
  var items = getData();
  var row = {{}};
  COL_KEYS.forEach(function(k) {{ row[k] = ''; }});
  if (row.qty !== undefined) row.qty = '1';
  items.push(row);
  saveData(items);
  renderTable();
  var tbody = document.getElementById('tracker-body');
  var lastRow = tbody.lastElementChild;
  if (lastRow) {{ var first = lastRow.querySelector('.cell-input'); if (first) first.focus(); }}
}}
function updateRow(idx, key, value) {{
  var items = getData();
  if (items[idx]) {{ items[idx][key] = value; saveData(items); }}
}}
function autoTotal(idx) {{
  var items = getData();
  if (!items[idx]) return;
  var row = items[idx];
  var qty = parseFloat(row.qty) || 1;
  var price = parseFloat(row.unit_price) || 0;
  items[idx].total = (qty * price).toFixed(2);
  saveData(items);
  var tbody = document.getElementById('tracker-body');
  var tr = tbody.querySelector('tr[data-idx="'+idx+'"]');
  if (tr) {{ var totalInp = tr.querySelector('[data-key="total"]'); if (totalInp) totalInp.value = items[idx].total; }}
  {after_render_call}
}}
function deleteRow(idx) {{
  var items = getData(); items.splice(idx, 1); saveData(items); renderTable();
}}
function clearAll() {{
  if (!confirm('Clear all rows? This cannot be undone.')) return;
  localStorage.removeItem(STORAGE_KEY); renderTable();
}}
function exportCSV() {{
  var items = getData();
  if (!items.length) {{ alert('No data to export.'); return; }}
  var headers = {str(columns).replace("'", '"')};
  var rows = [headers.join(',')];
  items.forEach(function(it) {{
    var vals = COL_KEYS.map(function(k) {{
      var v = (it[k] || '').toString().replace(/"/g, '""');
      return '"' + v + '"';
    }});
    rows.push(vals.join(','));
  }});
  var blob = new Blob([rows.join('\\n')], {{type:'text/csv'}});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url; a.download = '{label_esc.replace(" ", "_")}_data.csv';
  a.click(); setTimeout(function() {{ URL.revokeObjectURL(url); }}, 1000);
}}
function printApp() {{ window.print(); }}
{update_summary_js}
renderTable();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@chat_api.route("/diagnostics", methods=["GET"])
def diagnostics():
    """Chat system diagnostics."""
    err = _require_chat()
    if err:
        return err

    return jsonify(chat_manager.get_diagnostics())
