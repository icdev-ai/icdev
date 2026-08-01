
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Flask Blueprint for multi-stream parallel chat API (Phase 44 — D257-D260).

Provides endpoints for creating/managing chat contexts, sending messages,
mid-stream intervention, and dirty-tracking state queries.
"""

import sys
from pathlib import Path


from flask import Blueprint, jsonify, request

logger = get_logger("icdev.dashboard.api.chat")

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

try:
    from tools.requirements.intake_engine import create_session as _intake_create_session  # noqa: F401
    _HAS_INTAKE = True
except ImportError:
    _HAS_INTAKE = False

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

    Body: {user_id, tenant_id?, title?, project_id?, agent_model?, system_prompt?,
           reasoning_mode?: "off"|"auto"|"on"}
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
        reasoning_mode=data.get("reasoning_mode", "off"),
    )

    if "error" in result:
        return jsonify(result), 429  # Rate limit / max concurrent
    return jsonify(result), 201


@chat_api.route("/contexts/<context_id>/reasoning", methods=["PATCH", "POST"])
def set_reasoning_mode(context_id):
    """Update a session's reasoned-codegen mode mid-conversation.

    Body: {reasoning_mode: "off"|"auto"|"on"}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    mode = data.get("reasoning_mode", "off")
    result = chat_manager.set_reasoning_mode(context_id, mode)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result), 200


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
        user_config TEXT,
        is_user_created INTEGER DEFAULT 0,
        created_by TEXT DEFAULT NULL,
        created_at TEXT DEFAULT NULL,
        template_requirements TEXT DEFAULT NULL,
        category TEXT DEFAULT NULL,
        tenant_id TEXT DEFAULT '',
        canvas_seeds TEXT DEFAULT NULL,
        workflow_steps TEXT DEFAULT NULL
    )""")
    # Idempotent column migrations for existing tables (backend-aware probe)
    from tools.db.storage import column_exists
    for col_def in [
        ("classification",          "TEXT DEFAULT NULL"),
        ("fast_track",              "INTEGER DEFAULT 0"),
        ("skip_requirement_types",  "TEXT"),
        ("user_config",             "TEXT"),
        ("is_user_created",         "INTEGER DEFAULT 0"),
        ("created_by",              "TEXT DEFAULT NULL"),
        ("created_at",              "TEXT DEFAULT NULL"),
        ("template_requirements",   "TEXT DEFAULT NULL"),
        ("category",                "TEXT DEFAULT NULL"),
        ("tenant_id",               "TEXT DEFAULT ''"),
        ("canvas_seeds",            "TEXT DEFAULT NULL"),
        ("workflow_steps",          "TEXT DEFAULT NULL"),
    ]:
        if not column_exists(conn, "use_case_overrides", col_def[0]):
            try:
                conn.execute(f"ALTER TABLE use_case_overrides ADD COLUMN {col_def[0]} {col_def[1]}")
            except Exception:
                pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_uc_overrides_tenant ON use_case_overrides(tenant_id)"
    )
    conn.commit()


def _chain_init_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS use_case_chains (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        use_case_ids TEXT NOT NULL,
        merged_requirements TEXT,
        linked_session_id TEXT DEFAULT NULL,
        status TEXT DEFAULT 'draft',
        created_at TEXT NOT NULL,
        created_by TEXT DEFAULT 'dashboard-user',
        updated_at TEXT,
        classification TEXT DEFAULT NULL
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_uc_chains_tenant ON use_case_chains(tenant_id)"
    )
    # Migrate existing tables that lack the classification column
    try:
        conn.execute("ALTER TABLE use_case_chains ADD COLUMN classification TEXT DEFAULT NULL")
        conn.commit()
    except Exception:
        pass  # Column already exists
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
    # Null-guard: only apply DB value if non-null AND YAML didn't already have it
    for jcol in ("category", "template_requirements", "canvas_seeds", "workflow_steps"):
        db_val = row[jcol] if jcol in row.keys() else None
        if db_val is not None:
            try:
                parsed = _json.loads(db_val) if isinstance(db_val, str) else db_val
                # Don't overwrite YAML-provided value with DB null/empty
                if parsed is not None and parsed != [] and parsed != {}:
                    result[jcol] = parsed
            except Exception:
                pass
        # If YAML already has a value (e.g. category as plain string), keep it
        if jcol == "category" and db_val is not None and not isinstance(db_val, (list, dict)):
            result["category"] = db_val
    return result


def _get_tenant_id() -> str:
    """Extract tenant_id from request. Returns '' for single-instance mode."""
    body = request.get_json(silent=True) or {}
    return str(
        body.get("tenant_id")
        or request.args.get("tenant_id")
        or ""
    ).strip()


def _uc_row_to_usecase(row) -> dict:
    """Convert a DB-only user-created use_case_overrides row to a use case dict."""
    import json as _json
    d = dict(row) if not isinstance(row, dict) else row
    for jcol in ("canvas_wiring", "quick_actions", "skip_requirement_types",
                 "user_config", "template_requirements", "canvas_seeds", "workflow_steps"):
        if d.get(jcol) and isinstance(d[jcol], str):
            try:
                d[jcol] = _json.loads(d[jcol])
            except Exception:
                d[jcol] = []
    d["ricoas"] = bool(d.get("ricoas", 0))
    d["fast_track"] = bool(d.get("fast_track", 0))
    d["is_user_created"] = bool(d.get("is_user_created", 0))
    return d


def _uc_load_all(tenant_id: str = "") -> list:
    """Load all use cases: YAML base + DB overrides + user-created, RLS-filtered."""
    from tools.db.storage import get_connection as _gc
    yaml_cases = _uc_load_yaml()
    yaml_ids = {c["id"] for c in yaml_cases}
    overrides = {}
    user_created = []
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            # tenant_id='' sentinel matches both tenant-specific and global overrides
            rows = conn.execute(
                "SELECT * FROM use_case_overrides WHERE tenant_id = %s OR tenant_id = ''",
                (tenant_id,)
            ).fetchall()
            for row in rows:
                d = dict(row)
                if d.get("is_user_created"):
                    if d.get("tenant_id", "") in ("", tenant_id):
                        user_created.append(d)
                else:
                    overrides[d["id"]] = row
    except Exception:
        pass
    merged = [_uc_apply_override(c, overrides.get(c["id"])) for c in yaml_cases]
    for row in user_created:
        if row.get("id") not in yaml_ids:
            merged.append(_uc_row_to_usecase(row))
    return merged


_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _merge_chain_requirements(use_cases: list) -> list:
    """Union-merge template_requirements across use cases.

    Dedup key: first 50 chars of normalized text.
    Conflict: keep lower rank (higher priority).
    Output: stable-sorted by priority rank, each annotated with source_uc_id.
    """
    seen: dict = {}  # key -> merged req dict
    for uc in use_cases:
        uc_id = uc.get("id", "")
        for req in (uc.get("template_requirements") or []):
            text = (req.get("text") or "").strip().lower()
            dedup_key = text[:50]
            if not dedup_key:
                continue
            rank = _PRIORITY_RANK.get((req.get("priority") or "medium").lower(), 2)
            if dedup_key not in seen or rank < seen[dedup_key]["_rank"]:
                seen[dedup_key] = dict(req, source_uc_id=uc_id, _rank=rank)
    result = sorted(seen.values(), key=lambda r: r["_rank"])
    for r in result:
        r.pop("_rank", None)
    return result


_canvas_catalog_cache: dict = {}


def _load_canvas_catalog() -> dict:
    """Load canvas artifact catalog from args/cloud_vendor_policy.yaml (cached)."""
    global _canvas_catalog_cache
    if _canvas_catalog_cache:
        return _canvas_catalog_cache
    import yaml as _yaml
    policy_path = BASE_DIR / "args" / "cloud_vendor_policy.yaml"
    try:
        with open(policy_path, "r", encoding="utf-8") as fh:
            data = _yaml.safe_load(fh) or {}
        _canvas_catalog_cache = data.get("canvas_artifact_catalog", {})
    except Exception:
        _canvas_catalog_cache = {}
    return _canvas_catalog_cache


def _safe_table_name(name: str) -> str:
    """Return name unchanged if it is a safe SQL identifier, else raise ValueError."""
    import re as _re
    if not name or not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Unsafe table name rejected: {name!r}")
    return name


def _canvas_instances_init_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS canvas_instances (
        id              TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        tenant_id       TEXT NOT NULL,
        canvas          TEXT NOT NULL,
        artifact_type   TEXT NOT NULL,
        artifact_name   TEXT NOT NULL,
        use_case_id     TEXT,
        status          TEXT NOT NULL DEFAULT 'seeded',
        classification  TEXT NOT NULL DEFAULT 'CUI',
        created_at      TEXT NOT NULL,
        metadata_json   TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_instances_session ON canvas_instances(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_instances_tenant_canvas ON canvas_instances(tenant_id, canvas)"
    )
    conn.commit()


def _seed_canvas_artifacts(use_case: dict, session_id: str, tenant_id: str) -> list:
    """Pre-instantiate canvas templates/snippets for a use case.

    Best-effort: logs warnings on failure but never raises.
    Returns list of validated artifact dicts (canvas, type, name, instance_id).
    """
    import uuid
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    _log = get_logger("icdev.chat")
    seeded = []
    canvas_seeds = use_case.get("canvas_seeds") or []
    if not canvas_seeds:
        return seeded
    try:
        with _gc() as _init_conn:
            _canvas_instances_init_table(_init_conn)
    except Exception as _init_exc:
        _log.warning("canvas_instances table init failed: %s", _init_exc)
    catalog = _load_canvas_catalog()
    for seed in canvas_seeds:
        canvas_key = seed.get("canvas")
        catalog_entry = catalog.get(canvas_key)
        if not catalog_entry:
            _log.warning("canvas seed: unknown canvas key '%s'", canvas_key)
            continue
        canvas_db_rel = catalog_entry.get("db", "")
        canvas_db_path = BASE_DIR / canvas_db_rel
        templates_table = catalog_entry.get("templates_table", "")
        snippets_table = catalog_entry.get("snippets_table", "")
        for tmpl_name in (seed.get("templates") or []):
            if not templates_table:
                continue
            try:
                safe_tmpl_table = _safe_table_name(templates_table)
                with _gc(db_path=str(canvas_db_path)) as cconn:
                    cconn.set_security_context(None)  # rls-bypass: canvas seeding; tenant isolation at API boundary
                    row = cconn.execute(
                        f"SELECT name FROM {safe_tmpl_table} WHERE name = %s LIMIT 1",  # nosec B608 — table name validated by _safe_table_name
                        (tmpl_name,)
                    ).fetchone()
                    if row:
                        instance_id = str(uuid.uuid4())
                        try:
                            with _gc() as mconn:
                                mconn.execute(
                                    "INSERT INTO canvas_instances "
                                    "(id, session_id, tenant_id, canvas, artifact_type, artifact_name, created_at) "
                                    "VALUES (%s, %s, %s, %s, 'template', %s, %s)",
                                    (instance_id, session_id, tenant_id, canvas_key, tmpl_name,
                                     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                                )
                        except Exception as ins_exc:
                            _log.warning("canvas_instances insert failed: %s/template/%s — %s", canvas_key, tmpl_name, ins_exc)
                            instance_id = None
                        seeded.append({"canvas": canvas_key, "type": "template", "name": tmpl_name, "instance_id": instance_id})
            except Exception as exc:
                _log.warning("canvas seed failed: %s/template/%s — %s", canvas_key, tmpl_name, exc)
        for snip_name in (seed.get("snippets") or []):
            if not snippets_table:
                continue
            try:
                safe_snip_table = _safe_table_name(snippets_table)
                with _gc(db_path=str(canvas_db_path)) as cconn:
                    cconn.set_security_context(None)  # rls-bypass: canvas seeding; tenant isolation at API boundary
                    row = cconn.execute(
                        f"SELECT name FROM {safe_snip_table} WHERE name = %s LIMIT 1",  # nosec B608 — table name validated by _safe_table_name
                        (snip_name,)
                    ).fetchone()
                    if row:
                        instance_id = str(uuid.uuid4())
                        try:
                            with _gc() as mconn:
                                mconn.execute(
                                    "INSERT INTO canvas_instances "
                                    "(id, session_id, tenant_id, canvas, artifact_type, artifact_name, created_at) "
                                    "VALUES (%s, %s, %s, %s, 'snippet', %s, %s)",
                                    (instance_id, session_id, tenant_id, canvas_key, snip_name,
                                     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                                )
                        except Exception as ins_exc:
                            _log.warning("canvas_instances insert failed: %s/snippet/%s — %s", canvas_key, snip_name, ins_exc)
                            instance_id = None
                        seeded.append({"canvas": canvas_key, "type": "snippet", "name": snip_name, "instance_id": instance_id})
            except Exception as exc:
                _log.warning("canvas seed failed: %s/snippet/%s — %s", canvas_key, snip_name, exc)
    return seeded


@chat_api.route("/use-cases", methods=["GET"])
def list_use_cases():
    """Return use case catalog (YAML defaults merged with DB overrides)."""
    tenant_id = _get_tenant_id()
    category = request.args.get("category", "").strip().lower()
    query = request.args.get("q", "").strip().lower()

    merged = _uc_load_all(tenant_id)
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
            "is_user_created": c.get("is_user_created", False),
            "canvas_seeds": c.get("canvas_seeds", []),
            "workflow_steps": c.get("workflow_steps", []),
            "template_requirements": c.get("template_requirements", []),
        }
        for c in merged
    ]
    return jsonify({"use_cases": summary, "total": len(summary)})


# ---------------------------------------------------------------------------
# D1 — POST /api/chat/use-cases — create user-created use case
# ---------------------------------------------------------------------------

@chat_api.route("/use-cases", methods=["POST"])
def create_use_case():
    """Create a new user-created use case (DB only; YAML unchanged)."""
    import json as _json
    import re as _re
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"error": "label required"}), 400

    tenant_id = _get_tenant_id()
    now = datetime.now(timezone.utc).isoformat()
    base_id = _re.sub(r"[^a-z0-9_]", "_", label.lower())[:40].strip("_") or "custom"

    try:
        with _gc() as conn:
            _uc_init_table(conn)
            # Check collision against YAML ids and DB ids
            yaml_ids = {c["id"] for c in _uc_load_yaml()}
            db_ids = {row[0] for row in conn.execute("SELECT id FROM use_case_overrides").fetchall()}
            all_ids = yaml_ids | db_ids
            use_case_id = base_id
            suffix = 2
            while use_case_id in all_ids:
                use_case_id = f"{base_id}_{suffix}"
                suffix += 1
            cs = body.get("canvas_seeds")
            ws = body.get("workflow_steps")
            tr = body.get("template_requirements")
            qa = body.get("quick_actions")
            cw = body.get("canvas_wiring")
            srt = body.get("skip_requirement_types")
            uc = body.get("user_config")
            conn.execute("""
                INSERT INTO use_case_overrides
                    (id, label, description, icon, badge, agent_model, ricoas,
                     boost_threshold, system_prompt, seed_message,
                     canvas_wiring, quick_actions, updated_at, updated_by,
                     fast_track, skip_requirement_types, user_config,
                     is_user_created, created_by, created_at,
                     template_requirements, category, tenant_id,
                     canvas_seeds, workflow_steps)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s)
            """, (
                use_case_id, label,
                body.get("description"), body.get("icon", "⚙"),
                body.get("badge"), body.get("agent_model", "kimi-cloud"),
                1 if body.get("ricoas") else 0,
                body.get("boost_threshold", 70),
                body.get("system_prompt"), body.get("seed_message"),
                _json.dumps(cw) if cw is not None else None,
                _json.dumps(qa) if qa is not None else None,
                now, body.get("created_by", "dashboard-user"),
                1 if body.get("fast_track") else 0,
                _json.dumps(srt) if srt is not None else None,
                _json.dumps(uc) if uc is not None else None,
                body.get("created_by", "dashboard-user"), now,
                _json.dumps(tr) if tr is not None else None,
                body.get("category", "general"), tenant_id,
                _json.dumps(cs) if cs is not None else None,
                _json.dumps(ws) if ws is not None else None,
            ))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "created_at": now}), 201


# ---------------------------------------------------------------------------
# D4 — POST /api/chat/use-cases/import (literal route — MUST be before /<id>)
# ---------------------------------------------------------------------------

@chat_api.route("/use-cases/import", methods=["POST"])
def import_use_cases():
    """Import YAML use case bundle (multipart file or JSON yaml_content)."""
    import json as _json
    import yaml as _yaml
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    tenant_id = _get_tenant_id()
    now = datetime.now(timezone.utc).isoformat()
    overwrite = False
    raw = None
    # multipart/form-data
    if request.files.get("file"):
        f = request.files["file"]
        raw = f.read().decode("utf-8", errors="replace")
        overwrite = request.form.get("overwrite", "false").lower() == "true"
    else:
        body = request.get_json(silent=True) or {}
        raw = body.get("yaml_content", "")
        overwrite = bool(body.get("overwrite", False))

    try:
        bundle = _yaml.safe_load(raw or "") or {}
    except Exception as exc:
        return jsonify({"error": f"YAML parse error: {exc}"}), 400
    if "icdev_uc_bundle" not in bundle:
        return jsonify({"error": "Not a valid ICDEV use case bundle (missing icdev_uc_bundle key)"}), 400

    use_cases_raw = bundle.get("use_cases", [])
    imported, skipped, errors = [], [], []
    yaml_ids = {c["id"] for c in _uc_load_yaml()}

    try:
        with _gc() as conn:
            _uc_init_table(conn)
            for uc in use_cases_raw:
                uc_id = (uc.get("id") or "").strip()
                if not uc_id:
                    errors.append({"id": None, "reason": "missing id"})
                    continue
                if uc_id in yaml_ids and not overwrite:
                    skipped.append({"id": uc_id, "reason": "conflicts with YAML base use case"})
                    continue
                existing = conn.execute(
                    "SELECT is_user_created FROM use_case_overrides WHERE id=%s", (uc_id,)
                ).fetchone()
                if existing and not overwrite:
                    skipped.append({"id": uc_id, "reason": "already exists"})
                    continue
                tr = uc.get("template_requirements")
                cs = uc.get("canvas_seeds")
                ws = uc.get("workflow_steps")
                qa = uc.get("quick_actions")
                cw = uc.get("canvas_wiring")
                srt = uc.get("skip_requirement_types")
                uconf = uc.get("user_config")
                conn.execute("""
                    INSERT INTO use_case_overrides
                        (id, label, description, icon, badge, agent_model, ricoas,
                         boost_threshold, system_prompt, seed_message,
                         canvas_wiring, quick_actions, updated_at, updated_by,
                         fast_track, skip_requirement_types, user_config,
                         is_user_created, created_by, created_at,
                         template_requirements, category, tenant_id,
                         canvas_seeds, workflow_steps)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s)
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
                        template_requirements=excluded.template_requirements,
                        category=excluded.category, tenant_id=excluded.tenant_id,
                        canvas_seeds=excluded.canvas_seeds,
                        workflow_steps=excluded.workflow_steps
                """, (
                    uc_id, uc.get("label"), uc.get("description"),
                    uc.get("icon", "⚙"), uc.get("badge"), uc.get("agent_model", "kimi-cloud"),
                    1 if uc.get("ricoas") else 0, uc.get("boost_threshold", 70),
                    uc.get("system_prompt"), uc.get("seed_message"),
                    _json.dumps(cw) if cw is not None else None,
                    _json.dumps(qa) if qa is not None else None,
                    now, "import",
                    1 if uc.get("fast_track") else 0,
                    _json.dumps(srt) if srt is not None else None,
                    _json.dumps(uconf) if uconf is not None else None,
                    "import", now,
                    _json.dumps(tr) if tr is not None else None,
                    uc.get("category", "general"), tenant_id,
                    _json.dumps(cs) if cs is not None else None,
                    _json.dumps(ws) if ws is not None else None,
                ))
                imported.append(uc_id)
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"imported": imported, "skipped": skipped, "errors": errors})


# ---------------------------------------------------------------------------
# Chain endpoints (D5, D6, D7)
# ---------------------------------------------------------------------------

@chat_api.route("/chains", methods=["POST"])
def create_chain():
    """Create a use case chain with merged requirements pool."""
    import json as _json
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    uc_ids = body.get("use_case_ids") or []
    if not name or not uc_ids or len(uc_ids) < 1:
        return jsonify({"error": "name and at least one use_case_id required"}), 400

    tenant_id = _get_tenant_id()
    now = datetime.now(timezone.utc).isoformat()
    chain_id = f"chain_{now.replace(':', '-').replace('.', '-')}"

    # Load the selected use cases and merge requirements
    all_ucs = _uc_load_all(tenant_id)
    selected = [uc for uc in all_ucs if uc.get("id") in uc_ids]
    merged_reqs = _merge_chain_requirements(selected)

    try:
        with _gc() as conn:
            _chain_init_table(conn)
            conn.execute("""
                INSERT INTO use_case_chains
                    (id, tenant_id, name, use_case_ids, merged_requirements,
                     status, created_at, created_by)
                VALUES (%s,%s,%s,%s,%s,'draft',%s,%s)
            """, (
                chain_id, tenant_id, name,
                _json.dumps(uc_ids),
                _json.dumps(merged_reqs),
                now, body.get("created_by", "dashboard-user"),
            ))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "chain_id": chain_id,
        "merged_requirements": merged_reqs,
        "requirement_count": len(merged_reqs),
    }), 201


@chat_api.route("/chains", methods=["GET"])
def list_chains():
    """List use case chains for the current tenant."""
    import json as _json
    from tools.db.storage import get_connection as _gc
    tenant_id = _get_tenant_id()
    chains = []
    try:
        with _gc() as conn:
            conn.set_security_context(None)  # rls-bypass: use_case_chains has no classification column
            _chain_init_table(conn)
            rows = conn.execute(
                "SELECT * FROM use_case_chains WHERE tenant_id = %s OR tenant_id = '' ORDER BY created_at DESC",
                (tenant_id,)
            ).fetchall()
            for row in rows:
                d = dict(row)
                for jcol in ("use_case_ids", "merged_requirements"):
                    if d.get(jcol) and isinstance(d[jcol], str):
                        try:
                            d[jcol] = _json.loads(d[jcol])
                        except Exception:
                            pass
                chains.append(d)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"chains": chains, "total": len(chains)})


@chat_api.route("/chains/<chain_id>/activate", methods=["POST"])
def activate_chain(chain_id):
    """Activate a use case chain — creates a RICOAS intake session with merged requirements."""
    import json as _json
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    if not _HAS_INTAKE:
        return jsonify({"error": "Intake engine not available — ensure tools.requirements.intake_engine is installed"}), 503

    body = request.get_json(silent=True) or {}
    tenant_id = _get_tenant_id()
    user_id = body.get("user_id", "dashboard-user")
    now = datetime.now(timezone.utc).isoformat()

    chain = None
    try:
        with _gc() as conn:
            conn.set_security_context(None)  # rls-bypass: use_case_chains has no classification column
            _chain_init_table(conn)
            row = conn.execute(
                "SELECT * FROM use_case_chains WHERE id=%s", (chain_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "Chain not found"}), 404
            chain = dict(row)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # Parse merged requirements
    merged_reqs = []
    if chain.get("merged_requirements"):
        try:
            merged_reqs = _json.loads(chain["merged_requirements"]) if isinstance(chain["merged_requirements"], str) else chain["merged_requirements"]
        except Exception:
            pass

    # Parse use_case_ids early so they can be stored in session context
    uc_ids_early = []
    if chain.get("use_case_ids"):
        try:
            uc_ids_early = _json.loads(chain["use_case_ids"]) if isinstance(chain["use_case_ids"], str) else chain["use_case_ids"]
        except Exception:
            pass

    # Seed requirements into intake session
    try:
        from tools.requirements.intake_engine import create_session as _create_session
        chain_name = chain.get("name", "Chained Use Cases")
        session_result = _create_session(
            project_id=None,
            customer_name=chain_name,
            customer_org=tenant_id or None,
            created_by=user_id,
            extra_context={"use_case_ids": uc_ids_early, "chain_id": chain_id},
        )
        context_id = session_result.get("session_id") or session_result.get("context_id")

        # Seed merged requirements — best-effort via internal bypass
        if merged_reqs and context_id:
            try:
                with _gc() as conn:
                    conn.set_security_context(None)  # rls-bypass: internal chain activation; tenant isolation enforced at API boundary
                    for req in merged_reqs:
                        conn.execute("""
                            INSERT OR IGNORE INTO intake_requirements
                                (session_id, requirement_type, priority, raw_text,
                                 acceptance_criteria, source_document, status)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """, (
                            context_id,
                            req.get("type", "functional"),
                            req.get("priority", "medium"),
                            req.get("text", ""),
                            req.get("criteria", ""),
                            f"chain:{chain_id}",
                            "validated",
                        ))
                    conn.commit()
            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # requirement seeding is best-effort
                logger.warning(
                    "activate_chain: best-effort INSERT into intake_requirements failed (non-blocking): %s",
                    _exc,
                )

        # Seed canvas artifacts
        uc_ids = []
        if chain.get("use_case_ids"):
            try:
                uc_ids = _json.loads(chain["use_case_ids"]) if isinstance(chain["use_case_ids"], str) else chain["use_case_ids"]
            except Exception:
                pass
        seeded_artifacts = []
        all_ucs = _uc_load_all(tenant_id)
        for uc in all_ucs:
            if uc.get("id") in uc_ids:
                seeded_artifacts.extend(_seed_canvas_artifacts(uc, context_id or "", tenant_id))

        # Update chain status
        with _gc() as conn:
            conn.set_security_context(None)  # rls-bypass: use_case_chains has no classification column
            _chain_init_table(conn)
            conn.execute(
                "UPDATE use_case_chains SET status='active', linked_session_id=%s, updated_at=%s WHERE id=%s",
                (context_id, now, chain_id)
            )
            conn.commit()

        _instantiated = sum(1 for a in seeded_artifacts if a.get("instance_id") is not None)
        return jsonify({
            "ok": True,
            "chain_id": chain_id,
            "context_id": context_id,
            "requirement_count": len(merged_reqs),
            "canvas_artifacts_seeded": seeded_artifacts,
            "canvas_seeds": {
                "instantiated": _instantiated,
                "available": len(seeded_artifacts),
            },
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@chat_api.route("/use-cases/<use_case_id>", methods=["GET"])
def get_use_case(use_case_id):
    """Return full use case definition (YAML + DB override merged, or DB-only user-created)."""
    from tools.db.storage import get_connection as _gc
    base = next((c for c in _uc_load_yaml() if c.get("id") == use_case_id), None)
    row = None
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            row = conn.execute(
                "SELECT * FROM use_case_overrides WHERE id = %s", (use_case_id,)
            ).fetchone()
    except Exception:
        pass
    if not base:
        # Fall back to DB-only user-created use case
        if row and dict(row).get("is_user_created"):
            return jsonify(_uc_row_to_usecase(dict(row)))
        return jsonify({"error": "Use case not found"}), 404
    return jsonify(_uc_apply_override(dict(base), row))


@chat_api.route("/use-cases/<use_case_id>", methods=["DELETE"])
def delete_use_case(use_case_id):
    """Delete a user-created use case. Returns 400 for YAML-backed use cases."""
    from tools.db.storage import get_connection as _gc
    yaml_ids = {c["id"] for c in _uc_load_yaml()}
    if use_case_id in yaml_ids:
        return jsonify({"error": "Cannot delete a YAML-backed use case — use DELETE /use-cases/<id>/override to reset overrides"}), 400
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            row = conn.execute(
                "SELECT is_user_created FROM use_case_overrides WHERE id=%s", (use_case_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "Use case not found"}), 404
            if not row["is_user_created"]:
                return jsonify({"error": "Use case is not user-created — use DELETE /use-cases/<id>/override to reset overrides"}), 400
            conn.execute("DELETE FROM use_case_overrides WHERE id=%s", (use_case_id,))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "deleted": True})


@chat_api.route("/use-cases/<use_case_id>/export", methods=["GET"])
def export_use_case(use_case_id):
    """Export a use case as a YAML bundle (portable across ICDEV instances)."""
    import yaml as _yaml
    from datetime import datetime, timezone
    from flask import Response as _Resp
    all_cases = _uc_load_all(_get_tenant_id())
    uc = next((c for c in all_cases if c.get("id") == use_case_id), None)
    if not uc:
        return jsonify({"error": "Use case not found"}), 404
    # Strip internal metadata before export
    strip_keys = {"is_user_created", "created_by", "created_at", "updated_at", "updated_by", "tenant_id"}
    export_uc = {k: v for k, v in uc.items() if k not in strip_keys and v is not None}
    bundle = {
        "icdev_uc_bundle": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "use_cases": [export_uc],
    }
    filename = use_case_id.replace("_", "-") + "-bundle.yaml"
    return _Resp(
        _yaml.dump(bundle, allow_unicode=True, sort_keys=False, default_flow_style=False),
        mimetype="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@chat_api.route("/use-cases/<use_case_id>/workflow-step", methods=["POST"])
def workflow_step(use_case_id):
    """Update the current workflow step for a use case in a chat context."""
    import json as _json
    from tools.db.storage import get_connection as _gc
    body = request.get_json(silent=True) or {}
    context_id = (body.get("context_id") or "").strip()
    step = body.get("step")
    if not context_id or step is None:
        return jsonify({"error": "context_id and step required"}), 400
    try:
        step = int(step)
    except (TypeError, ValueError):
        return jsonify({"error": "step must be an integer"}), 400

    # Resolve use case to get step definition
    all_cases = _uc_load_all(_get_tenant_id())
    uc = next((c for c in all_cases if c.get("id") == use_case_id), None)
    if not uc:
        return jsonify({"error": "Use case not found"}), 404
    steps = uc.get("workflow_steps") or []
    step_def = next((s for s in steps if s.get("step") == step), None)

    # Merge-update extra_context in the chat context row
    try:
        with _gc() as conn:
            row = conn.execute(
                "SELECT extra_context FROM chat_contexts WHERE id=%s", (context_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "Chat context not found"}), 404
            extra = {}
            if row["extra_context"]:
                try:
                    extra = _json.loads(row["extra_context"])
                except Exception:
                    pass
            extra["uc_workflow_step"] = step
            extra["uc_id"] = use_case_id
            conn.execute(
                "UPDATE chat_contexts SET extra_context=%s WHERE id=%s",
                (_json.dumps(extra), context_id)
            )
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "ok": True,
        "use_case_id": use_case_id,
        "step": step,
        "step_definition": step_def,
        "canvas_deep_link": f"/{step_def.get('canvas', '')}" if step_def and step_def.get("canvas") else None,
    })


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
    tid = str(body.get("tenant_id") or "").strip()
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            conn.execute("""
                INSERT INTO use_case_overrides
                    (id,label,description,icon,badge,agent_model,ricoas,
                     boost_threshold,system_prompt,seed_message,
                     canvas_wiring,quick_actions,updated_at,updated_by,
                     fast_track,skip_requirement_types,user_config,tenant_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    user_config=excluded.user_config,
                    tenant_id=excluded.tenant_id
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
                tid,
            ))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "updated_at": now})


@chat_api.route("/use-cases/<use_case_id>/override", methods=["DELETE"])
def reset_use_case(use_case_id):
    """Delete DB override — restores YAML factory defaults. Refuses to delete user-created rows."""
    from tools.db.storage import get_connection as _gc
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            row = conn.execute(
                "SELECT is_user_created FROM use_case_overrides WHERE id=%s", (use_case_id,)
            ).fetchone()
            if row and row["is_user_created"]:
                return jsonify({
                    "error": "Cannot reset a user-created use case via /override — use DELETE /use-cases/<id> instead"
                }), 400
            conn.execute("DELETE FROM use_case_overrides WHERE id=%s", (use_case_id,))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "reset": True})


# ---------------------------------------------------------------------------
# Standalone app — shared column manager assets
# ---------------------------------------------------------------------------

_COL_MANAGER_TOOLBAR_BTN = '<button class="btn btn-secondary btn-sm" onclick="toggleColManager()">&#x2699; Columns</button>'

_COL_MANAGER_HTML = """    <div id="col-manager" style="display:none;padding:12px 16px;border-top:1px solid #30363d;background:#1c2128">
  <div style="font-size:0.75rem;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px">Manage Columns</div>
  <div id="col-list" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px"></div>
  <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
    <input id="new-col-label" type="text" placeholder="Column name..." style="background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:4px 8px;color:#e6edf3;font-size:0.8rem;width:160px">
    <select id="new-col-type" style="background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:4px 8px;color:#e6edf3;font-size:0.8rem">
      <option value="text">Text</option>
      <option value="number">Number</option>
      <option value="date">Date</option>
    </select>
    <button class="btn btn-primary btn-sm" onclick="addColumn()">+ Add Column</button>
    <button class="btn btn-secondary btn-sm" onclick="resetColumns()">Reset to Default</button>
  </div>
</div>"""


def _make_col_manager_js(default_cols_json: str, extra_js: str = "") -> str:
    return f"""
        var DEFAULT_COLS = {default_cols_json};
        var COL_CFG_KEY = STORAGE_KEY + '_cols';
        function getCols() {{
            try {{ var c = JSON.parse(localStorage.getItem(COL_CFG_KEY)); if (c && c.length) return c; }} catch(e) {{}}
            return DEFAULT_COLS;
        }}
        function saveCols(cols) {{ localStorage.setItem(COL_CFG_KEY, JSON.stringify(cols)); }}
        function slugify(s) {{ return s.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'').substring(0,30) || ('col_'+Date.now()); }}
        function renderColManager() {{
            var cols = getCols();
            var list = document.getElementById('col-list');
            if (!list) return;
            list.innerHTML = '';
            cols.forEach(function(col, i) {{
                var chip = document.createElement('div');
                chip.style.cssText = 'display:inline-flex;align-items:center;gap:4px;background:#21262d;border:1px solid #30363d;border-radius:4px;padding:3px 8px;font-size:0.75rem;color:#c9d1d9';
                chip.innerHTML = '<span contenteditable="true" onblur="renameCol('+i+',this.textContent.trim())" style="outline:none;min-width:20px">'+escHtml(col.label)+'</span>'
                    + '<span style="color:#6e7681;font-size:0.65rem;margin-left:2px">['+col.type+']</span>'
                    + (cols.length > 1 ? '<button onclick="removeCol('+i+')" style="background:none;border:none;color:#6e7681;cursor:pointer;font-size:0.7rem;padding:0 2px" title="Remove">&times;</button>' : '');
                list.appendChild(chip);
            }});
        }}
        function toggleColManager() {{
            var p = document.getElementById('col-manager');
            if (p) {{ p.style.display = p.style.display === 'none' ? 'block' : 'none'; if (p.style.display !== 'none') renderColManager(); }}
        }}
        function addColumn() {{
            var label = (document.getElementById('new-col-label').value || '').trim();
            if (!label) {{ document.getElementById('new-col-label').focus(); return; }}
            var type = document.getElementById('new-col-type').value;
            var cols = getCols();
            var key = slugify(label);
            if (cols.some(function(c) {{ return c.key === key; }})) key = key + '_' + Date.now();
            cols.push({{key: key, label: label, type: type}});
            saveCols(cols); document.getElementById('new-col-label').value = '';
            renderTableHeader(); renderColManager(); renderTable();
        }}
        function removeCol(idx) {{
            var cols = getCols(); if (cols.length <= 1) return;
            cols.splice(idx, 1); saveCols(cols); renderTableHeader(); renderColManager(); renderTable();
        }}
        function renameCol(idx, newLabel) {{
            if (!newLabel) return;
            var cols = getCols(); if (!cols[idx]) return;
            cols[idx].label = newLabel; saveCols(cols); renderTableHeader();
        }}
        function resetColumns() {{
            if (!confirm('Reset columns to default? Existing data in removed columns will be lost.')) return;
            localStorage.removeItem(COL_CFG_KEY); renderTableHeader(); renderColManager(); renderTable();
        }}
        function renderTableHeader() {{
            var cols = getCols();
            var thead = document.querySelector('#tracker-table thead tr');
            if (!thead) return;
            thead.innerHTML = cols.map(function(c) {{ return '<th>'+escHtml(c.label)+'</th>'; }}).join('') + '<th></th>';
        }}
        function escHtml(s) {{ return (s||'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
        {extra_js}"""


_UC_DEFAULT_COLS: dict = {
    "ato_package_builder": [
        {"key": "control_id",     "label": "Control ID",       "type": "text"},
        {"key": "family",         "label": "Control Family",    "type": "text"},
        {"key": "status",         "label": "Status",            "type": "text"},
        {"key": "evidence",       "label": "Evidence Location", "type": "text"},
        {"key": "inherited_from", "label": "Inherited From",    "type": "text"},
        {"key": "due_date",       "label": "Due Date",          "type": "date"},
        {"key": "owner",          "label": "Owner",             "type": "text"},
        {"key": "notes",          "label": "Notes",             "type": "text"},
    ],
    "incident_response_plan": [
        {"key": "playbook_item", "label": "Playbook Item", "type": "text"},
        {"key": "ir_category",  "label": "IR Category",   "type": "text"},
        {"key": "severity",     "label": "Severity",      "type": "text"},
        {"key": "owner_team",   "label": "Owner / Team",  "type": "text"},
        {"key": "poc_24_7",     "label": "POC (24/7)",    "type": "text"},
        {"key": "last_tested",  "label": "Last Tested",   "type": "date"},
        {"key": "status",       "label": "Status",        "type": "text"},
        {"key": "notes",        "label": "Notes",         "type": "text"},
    ],
    "sbom_attestation": [
        {"key": "component",   "label": "Component",    "type": "text"},
        {"key": "version",     "label": "Version",      "type": "text"},
        {"key": "supplier",    "label": "Supplier",     "type": "text"},
        {"key": "license",     "label": "License",      "type": "text"},
        {"key": "cve_status",  "label": "CVE Status",   "type": "text"},
        {"key": "ndaa_889",    "label": "§889 Status", "type": "text"},
        {"key": "attestation", "label": "Attestation",  "type": "text"},
        {"key": "notes",       "label": "Notes",        "type": "text"},
    ],
    "fedramp_auth_prep": [
        {"key": "control_id",     "label": "Control ID",     "type": "text"},
        {"key": "family",         "label": "Family",         "type": "text"},
        {"key": "csp_inherited",  "label": "CSP Inherited",  "type": "text"},
        {"key": "customer_resp",  "label": "Customer Resp.", "type": "text"},
        {"key": "implementation", "label": "Implementation", "type": "text"},
        {"key": "evidence",       "label": "Evidence",       "type": "text"},
        {"key": "status",         "label": "Status",         "type": "text"},
        {"key": "notes",          "label": "Notes",          "type": "text"},
    ],
    "privacy_impact_assessment": [
        {"key": "pii_element",     "label": "PII Element",     "type": "text"},
        {"key": "legal_authority", "label": "Legal Authority", "type": "text"},
        {"key": "purpose",         "label": "Purpose",         "type": "text"},
        {"key": "retention",       "label": "Retention",       "type": "text"},
        {"key": "access_controls", "label": "Access Controls", "type": "text"},
        {"key": "risk_level",      "label": "Risk Level",      "type": "text"},
        {"key": "status",          "label": "Status",          "type": "text"},
        {"key": "notes",           "label": "Notes",           "type": "text"},
    ],
    "cdrl_generator": [
        {"key": "cdrl_num",        "label": "CDRL #",          "type": "text"},
        {"key": "data_item_title", "label": "Data Item Title", "type": "text"},
        {"key": "di_number",       "label": "DI Number",       "type": "text"},
        {"key": "clin",            "label": "CLIN",            "type": "text"},
        {"key": "frequency",       "label": "Frequency",       "type": "text"},
        {"key": "due_date",        "label": "Due Date",        "type": "date"},
        {"key": "status",          "label": "Status",          "type": "text"},
        {"key": "notes",           "label": "Notes",           "type": "text"},
    ],
    "program_status_review": [
        {"key": "work_package", "label": "Work Package", "type": "text"},
        {"key": "planned",      "label": "Planned ($)",  "type": "number"},
        {"key": "earned",       "label": "Earned ($)",   "type": "number"},
        {"key": "actual",       "label": "Actual ($)",   "type": "number"},
        {"key": "spi",          "label": "SPI",          "type": "number"},
        {"key": "cpi",          "label": "CPI",          "type": "number"},
        {"key": "status",       "label": "Status",       "type": "text"},
        {"key": "notes",        "label": "Notes",        "type": "text"},
    ],
    "section_508_audit": [
        {"key": "requirement",    "label": "Requirement",      "type": "text"},
        {"key": "wcag_criterion", "label": "WCAG Criterion",   "type": "text"},
        {"key": "component_page", "label": "Component / Page", "type": "text"},
        {"key": "conformance",    "label": "Conformance",      "type": "text"},
        {"key": "finding",        "label": "Finding",          "type": "text"},
        {"key": "remediation",    "label": "Remediation",      "type": "text"},
        {"key": "owner",          "label": "Owner",            "type": "text"},
        {"key": "notes",          "label": "Notes",            "type": "text"},
    ],
    "grant_tech_proposal": [
        {"key": "requirement",    "label": "Requirement",    "type": "text"},
        {"key": "grant_section",  "label": "Grant Section",  "type": "text"},
        {"key": "match_pct",      "label": "Match %",        "type": "number"},
        {"key": "funding_source", "label": "Funding Source", "type": "text"},
        {"key": "metric",         "label": "Metric",         "type": "text"},
        {"key": "status",         "label": "Status",         "type": "text"},
        {"key": "due_date",       "label": "Due Date",       "type": "date"},
        {"key": "notes",          "label": "Notes",          "type": "text"},
    ],
    "cjis_compliance_prep": [
        {"key": "policy_area",       "label": "Policy Area",       "type": "text"},
        {"key": "requirement",       "label": "Requirement",       "type": "text"},
        {"key": "compliance_status", "label": "Compliance Status", "type": "text"},
        {"key": "evidence",          "label": "Evidence",          "type": "text"},
        {"key": "gap",               "label": "Gap",               "type": "text"},
        {"key": "remediation",       "label": "Remediation",       "type": "text"},
        {"key": "owner",             "label": "Owner",             "type": "text"},
        {"key": "notes",             "label": "Notes",             "type": "text"},
    ],
}

_CATEGORY_DEFAULT_COLS: dict = {
    "budget": [
        {"key": "vendor",      "label": "Vendor",        "type": "vendor"},
        {"key": "item",        "label": "Item",          "type": "text"},
        {"key": "qty",         "label": "Qty",           "type": "qty"},
        {"key": "estimate",    "label": "Estimate ($)",  "type": "number"},
        {"key": "quotation",   "label": "Quotation ($)", "type": "number"},
        {"key": "expiration",  "label": "Expiration",    "type": "date"},
        {"key": "poc",         "label": "POC",           "type": "text"},
        {"key": "description", "label": "Description",   "type": "text"},
        {"key": "notes",       "label": "Notes",         "type": "text"},
    ],
    "modernization": [
        {"key": "asset",             "label": "Asset Name",         "type": "text"},
        {"key": "type",              "label": "Type",               "type": "text"},
        {"key": "version",           "label": "Version / Age",      "type": "text"},
        {"key": "classification_7r", "label": "7Rs Classification", "type": "text"},
        {"key": "phase",             "label": "Phase",              "type": "text"},
        {"key": "risk",              "label": "Risk",               "type": "text"},
        {"key": "notes",             "label": "Notes",              "type": "text"},
    ],
    "compliance_ato": [
        {"key": "control",  "label": "Control",  "type": "text"},
        {"key": "family",   "label": "Family",   "type": "text"},
        {"key": "status",   "label": "Status",   "type": "text"},
        {"key": "evidence", "label": "Evidence", "type": "text"},
        {"key": "due",      "label": "Due",      "type": "date"},
    ],
    "acquisition": [
        {"key": "deliverable", "label": "Deliverable", "type": "text"},
        {"key": "clin",        "label": "CLIN",        "type": "text"},
        {"key": "due_date",    "label": "Due Date",    "type": "date"},
        {"key": "status",      "label": "Status",      "type": "text"},
        {"key": "notes",       "label": "Notes",       "type": "text"},
    ],
    "zero_trust": [
        {"key": "pillar",      "label": "Pillar",         "type": "text"},
        {"key": "requirement", "label": "Requirement",    "type": "text"},
        {"key": "maturity",    "label": "Maturity Level", "type": "text"},
        {"key": "gap",         "label": "Gap",            "type": "text"},
        {"key": "action",      "label": "Action",         "type": "text"},
    ],
    "it_operations": [
        {"key": "requirement", "label": "Requirement", "type": "text"},
        {"key": "standard",    "label": "Standard",    "type": "text"},
        {"key": "status",      "label": "Status",      "type": "text"},
        {"key": "finding",     "label": "Finding",     "type": "text"},
        {"key": "owner",       "label": "Owner",       "type": "text"},
    ],
    "state_local": [
        {"key": "requirement",   "label": "Requirement",  "type": "text"},
        {"key": "grant_section", "label": "Grant Section","type": "text"},
        {"key": "status",        "label": "Status",       "type": "text"},
        {"key": "notes",         "label": "Notes",        "type": "text"},
        {"key": "owner",         "label": "Owner",        "type": "text"},
    ],
    "knowledge": [
        {"key": "document",    "label": "Document / Section", "type": "text"},
        {"key": "owner",       "label": "Owner",              "type": "text"},
        {"key": "last_review", "label": "Last Review",        "type": "date"},
        {"key": "staleness",   "label": "Staleness",          "type": "text"},
        {"key": "status",      "label": "Status",             "type": "text"},
        {"key": "contributor", "label": "Contributor",        "type": "text"},
        {"key": "notes",       "label": "Notes",              "type": "text"},
    ],
    "general": [
        {"key": "document",    "label": "Document / Section", "type": "text"},
        {"key": "owner",       "label": "Owner",              "type": "text"},
        {"key": "last_review", "label": "Last Review",        "type": "date"},
        {"key": "staleness",   "label": "Staleness",          "type": "text"},
        {"key": "status",      "label": "Status",             "type": "text"},
        {"key": "contributor", "label": "Contributor",        "type": "text"},
        {"key": "notes",       "label": "Notes",              "type": "text"},
    ],
}


# ---------------------------------------------------------------------------
# Standalone HTML app generator
# ---------------------------------------------------------------------------


@chat_api.route("/use-cases/<use_case_id>/standalone", methods=["GET"])
def standalone_app(use_case_id):
    """Generate and return a self-contained HTML app for the use case."""
    from flask import Response as _Resp
    base = next((c for c in _uc_load_yaml() if c.get("id") == use_case_id), None)
    row = None
    try:
        from tools.db.storage import get_connection as _gc
        with _gc() as conn:
            _uc_init_table(conn)
            row = conn.execute(
                "SELECT * FROM use_case_overrides WHERE id = %s", (use_case_id,)
            ).fetchone()
    except Exception:
        pass
    if not base:
        # Fall back to DB-only user-created use case
        if row and dict(row).get("is_user_created"):
            uc = _uc_row_to_usecase(dict(row))
        else:
            return jsonify({"error": "Use case not found"}), 404
    else:
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
    import json as _json
    label = uc.get("label", "Use Case")
    description = (uc.get("description") or "").strip()
    category = uc.get("category", "general")
    icon = uc.get("icon", "⚙")
    template_reqs = uc.get("template_requirements", [])
    user_config = uc.get("user_config") or {}

    uc_id = uc.get("id", "")
    default_cols = (
        _UC_DEFAULT_COLS.get(uc_id)
        or _CATEGORY_DEFAULT_COLS.get(category)
        or [{"key": "item", "label": "Item", "type": "text"}, {"key": "notes", "label": "Notes", "type": "text"}]
    )
    col_keys = [c["key"] for c in default_cols]

    # Category-specific options and controls only — columns come from default_cols above
    extra_summary_js = ""
    if category == "budget":
        tier_options = ""
        status_options = ""
        extra_controls = """
        <div class="budget-summary" id="budget-summary">
            <div class="summary-item"><span>Estimate Total:</span><strong id="est-total">$0.00</strong></div>
            <div class="summary-item"><span>Quotation Total:</span><strong id="quot-total">$0.00</strong></div>
            <div class="summary-item variance"><span>Variance:</span><strong id="variance-total">$0.00</strong></div>
        </div>"""
        extra_summary_js = """
        function updateSummary() {
            var items = getData();
            var est = 0, quot = 0;
            items.forEach(function(it) {
                est += parseFloat(it.estimate || 0) || 0;
                quot += parseFloat(it.quotation || 0) || 0;
            });
            var fmt = function(n) { return '$' + n.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ','); };
            var varAmt = quot - est;
            document.getElementById('est-total').textContent = fmt(est);
            document.getElementById('quot-total').textContent = fmt(quot);
            var varEl = document.getElementById('variance-total');
            var absAmt = Math.abs(varAmt);
            var fmtAbs = '$' + absAmt.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
            varEl.textContent = varAmt > 0 ? '+' + fmtAbs : varAmt < 0 ? '-' + fmtAbs : fmtAbs;
            varEl.style.color = varAmt > 0 ? '#f85149' : varAmt < 0 ? '#3fb950' : '';
        }"""
        after_render_call = "updateSummary();"
        input_change_call = "updateSummary();"
    elif category == "modernization":
        tier_options = '<option value="Retire">Retire</option><option value="Retain">Retain</option><option value="Rehost">Rehost</option><option value="Replatform">Replatform</option><option value="Repurchase">Repurchase</option><option value="Refactor">Refactor</option><option value="Re-architect">Re-architect</option>'
        status_options = '<option value="Phase 1">Phase 1</option><option value="Phase 2">Phase 2</option><option value="Phase 3">Phase 3</option><option value="Post-Go-Live">Post-Go-Live</option>'
        extra_controls = ""
        after_render_call = ""
        input_change_call = ""
    elif category == "acquisition":
        tier_options = ""
        status_options = '<option value="Not Started">Not Started</option><option value="In Progress">In Progress</option><option value="Submitted">Submitted</option><option value="Accepted">Accepted</option><option value="Rejected">Rejected</option>'
        extra_controls = ""
        after_render_call = ""
        input_change_call = ""
    elif category == "compliance_ato":
        tier_options = ""
        status_options = '<option value="Not Started">Not Started</option><option value="In Progress">In Progress</option><option value="Implemented">Implemented</option><option value="Inherited">Inherited</option><option value="N/A">N/A</option>'
        extra_controls = ""
        after_render_call = ""
        input_change_call = ""
    elif category == "zero_trust":
        tier_options = '<option value="Traditional">Traditional</option><option value="Initial">Initial</option><option value="Advanced">Advanced</option><option value="Optimal">Optimal</option>'
        status_options = '<option value="Open">Open</option><option value="In Progress">In Progress</option><option value="Closed">Closed</option>'
        extra_controls = ""
        after_render_call = ""
        input_change_call = ""
    elif category == "it_operations":
        tier_options = ""
        status_options = '<option value="Compliant">Compliant</option><option value="Non-Compliant">Non-Compliant</option><option value="Partial">Partial</option><option value="N/A">N/A</option>'
        extra_controls = ""
        after_render_call = ""
        input_change_call = ""
    elif category == "state_local":
        tier_options = ""
        status_options = '<option value="Not Started">Not Started</option><option value="In Progress">In Progress</option><option value="Complete">Complete</option><option value="N/A">N/A</option>'
        extra_controls = ""
        after_render_call = ""
        input_change_call = ""
    else:  # knowledge / document_refresh / general
        tier_options = '<option value="Current">Current</option><option value="Stale">Stale</option><option value="Critical">Critical (Action Required)</option>'
        status_options = '<option value="Draft">Draft Submitted</option><option value="In Review">In Review</option><option value="Approved">Approved</option><option value="Rebuilt">AI Rebuilt</option>'
        extra_controls = ""
        after_render_call = ""
        input_change_call = ""

    col_manager_toolbar_btn = _COL_MANAGER_TOOLBAR_BTN
    col_manager_html = _COL_MANAGER_HTML
    update_summary_js = _make_col_manager_js(_json.dumps(default_cols), extra_js=extra_summary_js)

    # Build requirements checklist HTML
    req_html = ""
    for i, req in enumerate(template_reqs[:20]):  # cap at 20 for compactness
        req_text = _html.escape(req.get("text", "")[:120])
        req_type = _html.escape(req.get("type", ""))
        req_priority = _html.escape(req.get("priority", ""))
        req_html += f'<li class="req-item"><span class="req-badge req-badge--{req_type}">{req_type}</span> <span class="req-priority req-priority--{req_priority}">{req_priority}</span> {req_text}</li>\n'

    vendors = user_config.get("vendors", {}).get("defaults", [])
    vendor_opts = "".join(f"<option>{_html.escape(v)}</option>" for v in vendors) or "<option>Custom</option>"

    th_html = "".join(f"<th>{_html.escape(c['label'])}</th>" for c in default_cols) + "<th></th>"

    # Build input row cells
    def make_input(key, idx, col_name):
        if key in ("tier", "classification_7r", "staleness", "maturity"):
            return f'<td><select class="cell-input" data-key="{key}">{tier_options}</select></td>'
        if key in ("status", "phase"):
            return f'<td><select class="cell-input" data-key="{key}">{status_options}</select></td>'
        if key == "vendor":
            return f'<td><select class="cell-input" data-key="{key}"><option value="">—</option>{vendor_opts}<option value="__custom__">Other...</option></select></td>'
        if key == "qty":
            return f'<td><input type="number" class="cell-input" data-key="{key}" min="1" value="1" style="width:60px"></td>'
        if key in ("unit_price",):
            return f'<td><input type="number" class="cell-input" data-key="{key}" min="0" step="0.01" placeholder="0.00" style="width:90px"></td>'
        if key == "total":
            return f'<td><input type="number" class="cell-input" data-key="{key}" min="0" step="0.01" placeholder="auto" style="width:90px" readonly></td>'
        if key in ("last_review", "due_date", "due"):
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
.summary-item{{display:flex;gap:8px;align-items:center}}.summary-item.total strong{{color:#58a6ff;font-size:1rem}}.summary-item.variance strong{{font-size:1rem}}
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
        {col_manager_toolbar_btn}
        <button class="btn btn-danger btn-sm" onclick="clearAll()">Clear All</button>
      </div>
    </div>
    {col_manager_html}
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
function activeCols() {{ return (typeof getCols === 'function') ? getCols() : COL_KEYS.map(function(k) {{ return {{key:k,label:k,type:'text'}}; }}); }}
function renderTable() {{
  var items = getData();
  var cols = activeCols();
  var tbody = document.getElementById('tracker-body');
  var empty = document.getElementById('empty-msg');
  if (!items.length) {{ tbody.innerHTML = ''; empty.style.display = 'block'; {after_render_call} return; }}
  empty.style.display = 'none';
  tbody.innerHTML = '';
  items.forEach(function(item, idx) {{
    var tr = document.createElement('tr');
    tr.dataset.idx = idx;
    cols.forEach(function(col) {{
      var val = item[col.key] || '';
      var cell = document.createElement('td');
      cell.innerHTML = makeCell(col.key, val, col.type);
      var cellInp = cell.querySelector('.cell-input');
      if (cellInp) cellInp.value = val;
      tr.appendChild(cell);
    }});
    var del = document.createElement('td');
    del.innerHTML = '<button class="delete-btn" title="Delete row" onclick="deleteRow(' + idx + ')">&#x2715;</button>';
    tr.appendChild(del);
    tbody.appendChild(tr);
    tr.querySelectorAll('.cell-input').forEach(function(inp) {{
      inp.addEventListener('change', function() {{
        if (inp.dataset.key === 'vendor' && inp.value === '__custom__') {{
          var custom = (prompt('Enter vendor name:') || '').trim();
          if (custom) {{
            var opt = document.createElement('option');
            opt.value = custom; opt.textContent = custom;
            inp.insertBefore(opt, inp.lastElementChild);
            inp.value = custom;
          }} else {{
            inp.value = '';
            return;
          }}
        }}
        updateRow(idx, inp.dataset.key, inp.value); {input_change_call}
      }});
    }});
  }});
  {after_render_call}
}}
function makeCell(key, val, colType) {{
  var v = (val || '').toString().replace(/"/g,'&quot;');
  if (key === 'tier' || key === 'classification_7r' || key === 'staleness' || key === 'maturity') {{
    var html = '{tier_options}'.replace(/data-key="[^"]*"/, 'data-key="'+key+'"');
    return '<select class="cell-input" data-key="'+key+'">'+html+'</select>';
  }}
  if (key === 'status' || key === 'phase') {{
    return '<select class="cell-input" data-key="'+key+'">{status_options}</select>';
  }}
  if (key === 'vendor' || colType === 'vendor') {{
    return '<select class="cell-input" data-key="'+key+'"><option value="">—</option>{vendor_opts}<option value="__custom__">Other...</option></select>';
  }}
  if (colType === 'number' || key === 'estimate' || key === 'quotation' || key === 'unit_price') {{
    return '<input type="number" class="cell-input" data-key="'+key+'" value="'+v+'" min="0" step="0.01" placeholder="0.00">';
  }}
  if (colType === 'qty' || key === 'qty') return '<input type="number" class="cell-input" data-key="'+key+'" value="'+(v||1)+'" min="1" style="width:60px">';
  if (colType === 'date' || key === 'last_review' || key === 'due_date' || key === 'due' || key === 'expiration') return '<input type="date" class="cell-input" data-key="'+key+'" value="'+v+'">';
  return '<input type="text" class="cell-input" data-key="'+key+'" value="'+v+'">';
}}
function addRow() {{
  var cols = activeCols();
  var items = getData();
  var row = {{}};
  cols.forEach(function(c) {{ row[c.key] = c.type === 'qty' || c.key === 'qty' ? '1' : ''; }});
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
  var cols = activeCols();
  var headers = cols.map(function(c) {{ return c.label; }});
  var rows = [headers.join(',')];
  items.forEach(function(it) {{
    var vals = cols.map(function(c) {{
      var v = (it[c.key] || '').toString().replace(/"/g, '""');
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
if (typeof renderTableHeader === 'function') renderTableHeader();
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
