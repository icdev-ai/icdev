#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Proposal Genesis — autonomous capture-to-delivery daemon.

Wraps tools/proposal_genesis/daemon.py for the /proposal-genesis dashboard page.
Provides status, reflex trigger, pipeline run, and quality score endpoints.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

proposal_genesis_api = Blueprint("proposal_genesis_api", __name__, url_prefix="/api/proposal-genesis")


class _PGCompatConn:
    """Silently pre-translate ? → %s for PG so translate_sql never warns."""
    def __init__(self, conn):
        self._conn = conn
        self._pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    def _fix(self, sql):
        return sql.replace("?", "%s") if self._pg and "?" in sql else sql
    def execute(self, sql, params=()):
        return self._conn.execute(self._fix(sql), params)
    def executemany(self, sql, seq):
        return self._conn.executemany(self._fix(sql), seq)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    def __getattr__(self, name): return getattr(self._conn, name)


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return _PGCompatConn(conn)


def _mac_filter_by_classification(rows):
    """Bell-LaPadula no-read-up filter (prop-sec-02).

    Win/loss records and lessons (pg_win_loss_records/pg_win_loss_lessons)
    are genuinely SECRET-capable competitive intelligence — why we won or
    lost against a named competitor, our own weaknesses — but classify
    per-row (classification column, default CUI), not per-endpoint. A
    blanket @require_clearance("SECRET") on the whole route would hide the
    (usually CUI) majority of records from ordinary capture/proposal roles;
    filtering per-row here matches the pattern already used in
    tools/dashboard/api/proposals.py's _mac_filter().

    No-op (returns rows unchanged) when g.security_context is unset
    (system/unauthenticated context) — same compatibility fallback as
    classification_enforcer.can_read(None).
    """
    try:
        from flask import g
        ctx = getattr(g, "security_context", None)
    except RuntimeError:
        ctx = None
    if ctx is None:
        return rows
    from tools.security.classification_enforcer import can_read
    return [r for r in rows if can_read(r.get("classification") or "CUI", ctx)]


def _run_daemon_cmd(args_list, timeout=30):
    """Run proposal_genesis daemon CLI and parse JSON output."""
    try:
        result = subprocess.run(
            [sys.executable, "tools/proposal_genesis/daemon.py"] + args_list,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
        )
        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start >= 0:
            return json.loads(stdout[json_start:]), None
        return None, result.stderr or "Could not parse output"
    except subprocess.TimeoutExpired:
        return None, "Command timed out"
    except Exception as exc:
        return None, str(exc)


# ── Status ────────────────────────────────────────────────────────────────────


@proposal_genesis_api.route("/status", methods=["GET"])
def api_pg_status():
    """GET /api/proposal-genesis/status — Daemon status with reflex details."""
    data, err = _run_daemon_cmd(["--status", "--json"])
    if err:
        return jsonify({"daemon_status": "error", "error": err}), 500
    # Ensure top-level daemon_status key exists for frontend consumers
    if isinstance(data, dict) and "daemon_status" not in data:
        daemon_info = data.get("daemon", {})
        if daemon_info.get("enabled"):
            data["daemon_status"] = "running"
        else:
            data["daemon_status"] = "disabled"
    return jsonify(data)


# ── Reflex Control ────────────────────────────────────────────────────────────

# All 25 reflexes across 4 phases (CAPTURE, PROPOSE, DELIVER, LEARN)
_ALLOWED_REFLEXES = [
    # Phase 1: CAPTURE
    "discover",
    "scout",
    "shape",
    "engage",
    "regulate",
    "vehicle",
    "talent",
    "team",
    # Phase 2: PROPOSE
    "extract",
    "map",
    "draft",
    "polish",
    "decide",
    "review",
    "price",
    "comply_cmmc",
    "trace",
    # Phase 3: DELIVER
    "monitor",
    "fulfill",
    "publish",
    "bridge",
    # Phase 4: LEARN
    "analyze",
    "train",
    "adapt",
]


@proposal_genesis_api.route("/reflex/<name>", methods=["POST"])
@require_role("admin", "pm")
def api_pg_run_reflex(name):
    """POST /api/proposal-genesis/reflex/<name> — Run a single reflex."""
    if name not in _ALLOWED_REFLEXES:
        return jsonify({"error": f"Unknown reflex: {name}"}), 400
    data, err = _run_daemon_cmd(["--reflex", name, "--json"], timeout=300)
    if err:
        return jsonify({"error": err}), 500
    return jsonify(data)


@proposal_genesis_api.route("/run-reflex", methods=["POST"])
@require_role("admin", "pm", "bd", "capture_mgr")
def api_pg_run_reflex_by_body():
    """POST /api/proposal-genesis/run-reflex — Run a reflex via JSON body {"reflex": "name"}."""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("reflex", "").strip()
    if not name:
        return jsonify({"error": "Missing required field: reflex"}), 400
    if name not in _ALLOWED_REFLEXES:
        return jsonify({"error": f"Unknown reflex: {name}"}), 400
    data, err = _run_daemon_cmd(["--reflex", name, "--json"], timeout=300)
    if err:
        return jsonify({"error": err}), 500
    return jsonify(data)


@proposal_genesis_api.route("/pipeline", methods=["POST"])
@require_role("admin", "pm")
def api_pg_run_pipeline():
    """POST /api/proposal-genesis/pipeline — Run discover->extract->map->draft->polish."""
    data, err = _run_daemon_cmd(["--pipeline", "--json"], timeout=600)
    if err:
        return jsonify({"error": err}), 500
    return jsonify(data)


# ── Quality Scores ────────────────────────────────────────────────────────────


@proposal_genesis_api.route("/quality-scores", methods=["GET"])
def api_pg_quality_scores():
    """GET /api/proposal-genesis/quality-scores — Recent quality check results."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT pqs.*, po.title AS opportunity_title "
            "FROM pg_proposal_quality_scores pqs "
            "LEFT JOIN proposal_opportunities po ON po.id = pqs.opportunity_id "
            "ORDER BY pqs.created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        scores = [dict(r) for r in rows]
        return jsonify({"scores": scores, "count": len(scores)})
    except Exception as exc:
        return jsonify({"scores": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Audit Trail ───────────────────────────────────────────────────────────────


@proposal_genesis_api.route("/audit", methods=["GET"])
def api_pg_audit():
    """GET /api/proposal-genesis/audit — Recent audit events."""
    limit = int(request.args.get("limit", "100"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, duration_ms, metric_name, metric_value, "
            "created_at as timestamp "
            "FROM pg_proposal_genesis_audit "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        events = []
        for r in rows:
            e = dict(r)
            e["action"] = e.get("event_type", "")
            events.append(e)
        return jsonify({"events": events, "count": len(events)})
    except Exception as exc:
        return jsonify({"events": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Pulse Links ───────────────────────────────────────────────────────────────


@proposal_genesis_api.route("/pulse-links", methods=["GET"])
def api_pg_pulse_links():
    """GET /api/proposal-genesis/pulse-links — Pulse-proposal content links (D-PG-5)."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM pg_pulse_proposal_links ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        links = [dict(r) for r in rows]
        return jsonify({"links": links, "count": len(links)})
    except Exception as exc:
        return jsonify({"links": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Summary Stats ─────────────────────────────────────────────────────────────


@proposal_genesis_api.route("/summary", methods=["GET"])
def api_pg_summary():
    """GET /api/proposal-genesis/summary — High-level metrics for dashboard."""
    conn = _get_db()
    try:
        stats = {}
        try:
            stats["opportunities"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM proposal_opportunities WHERE status IN ('tracking', 'drafting')"
            ).fetchone()["cnt"]
        except Exception:
            stats["opportunities"] = 0

        try:
            stats["shall_statements"] = conn.execute("SELECT COUNT(*) as cnt FROM rfp_shall_statements").fetchone()[
                "cnt"
            ]
        except Exception:
            stats["shall_statements"] = 0

        try:
            stats["drafts"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM proposal_section_drafts WHERE status = 'draft'"
            ).fetchone()["cnt"]
        except Exception:
            stats["drafts"] = 0

        try:
            row = conn.execute("SELECT AVG(composite_score) as avg_score FROM pg_proposal_quality_scores").fetchone()
            stats["avg_quality"] = round(row["avg_score"] or 0, 3)
        except Exception:
            stats["avg_quality"] = 0

        try:
            stats["audit_events_24h"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_proposal_genesis_audit WHERE created_at > datetime('now', '-1 day')"
            ).fetchone()["cnt"]
        except Exception:
            stats["audit_events_24h"] = 0

        try:
            stats["pulse_links"] = conn.execute("SELECT COUNT(*) as cnt FROM pg_pulse_proposal_links").fetchone()["cnt"]
        except Exception:
            stats["pulse_links"] = 0

        # Phase B stats
        try:
            stats["capture_plans"] = conn.execute("SELECT COUNT(*) as cnt FROM pg_capture_plans").fetchone()["cnt"]
        except Exception:
            stats["capture_plans"] = 0

        try:
            stats["teaming_assessments"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_teaming_assessments"
            ).fetchone()["cnt"]
        except Exception:
            stats["teaming_assessments"] = 0

        try:
            stats["intel_briefs"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_proposal_genesis_audit WHERE event_type = 'brief_generated'"
            ).fetchone()["cnt"]
        except Exception:
            stats["intel_briefs"] = 0

        # Phase C stats
        try:
            stats["crm_accounts"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_crm_accounts WHERE status = 'active'"
            ).fetchone()["cnt"]
        except Exception:
            stats["crm_accounts"] = 0

        try:
            stats["crm_contacts"] = conn.execute("SELECT COUNT(*) as cnt FROM pg_crm_contacts").fetchone()["cnt"]
        except Exception:
            stats["crm_contacts"] = 0

        try:
            stats["crm_interactions"] = conn.execute("SELECT COUNT(*) as cnt FROM pg_crm_interactions").fetchone()[
                "cnt"
            ]
        except Exception:
            stats["crm_interactions"] = 0

        try:
            row = conn.execute("SELECT AVG(score) as avg_score FROM pg_crm_engagement_scores").fetchone()
            stats["avg_engagement"] = round(row["avg_score"] or 0, 3)
        except Exception:
            stats["avg_engagement"] = 0

        # Phase D stats
        try:
            stats["published_articles"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pulse_posts WHERE author_id = 'pg_publish'"
            ).fetchone()["cnt"]
        except Exception:
            stats["published_articles"] = 0

        try:
            stats["cdrl_case_studies"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_pulse_proposal_links WHERE link_type = 'cdrl_to_case_study'"
            ).fetchone()["cnt"]
        except Exception:
            stats["cdrl_case_studies"] = 0

        # Phase F stats
        try:
            stats["bid_decisions"] = conn.execute("SELECT COUNT(*) as cnt FROM pg_bid_decisions").fetchone()["cnt"]
        except Exception:
            stats["bid_decisions"] = 0

        try:
            stats["bid_recommendations"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_bid_decisions WHERE decision = 'bid'"
            ).fetchone()["cnt"]
        except Exception:
            stats["bid_recommendations"] = 0

        try:
            stats["win_loss_records"] = conn.execute("SELECT COUNT(*) as cnt FROM pg_win_loss_records").fetchone()[
                "cnt"
            ]
        except Exception:
            stats["win_loss_records"] = 0

        try:
            stats["win_loss_lessons"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_win_loss_lessons WHERE actionable = 1"
            ).fetchone()["cnt"]
        except Exception:
            stats["win_loss_lessons"] = 0

        try:
            stats["training_pairs"] = conn.execute("SELECT COUNT(*) as cnt FROM pg_training_pair_sources").fetchone()[
                "cnt"
            ]
        except Exception:
            stats["training_pairs"] = 0

        # Phase E stats
        try:
            stats["active_contracts"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM cpmp_contracts WHERE status IN ('active', 'option_pending')"
            ).fetchone()["cnt"]
        except Exception:
            stats["active_contracts"] = 0

        try:
            stats["at_risk_contracts"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM cpmp_contracts "
                "WHERE status IN ('active', 'option_pending') "
                "AND health IN ('yellow', 'red')"
            ).fetchone()["cnt"]
        except Exception:
            stats["at_risk_contracts"] = 0

        try:
            stats["overdue_deliverables"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM cpmp_deliverables d "
                "JOIN cpmp_contracts c ON c.id = d.contract_id "
                "WHERE c.status IN ('active', 'option_pending') "
                "AND d.status = 'overdue'"
            ).fetchone()["cnt"]
        except Exception:
            stats["overdue_deliverables"] = 0

        try:
            stats["cdrls_generated"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM cpmp_cdrl_generations WHERE generated_by = 'pg_fulfill'"
            ).fetchone()["cnt"]
        except Exception:
            stats["cdrls_generated"] = 0

        return jsonify(stats)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── Phase B: Capture Plans ───────────────────────────────────────────────────


@proposal_genesis_api.route("/capture-plans", methods=["GET"])
def api_pg_capture_plans():
    """GET /api/proposal-genesis/capture-plans — Capture plans with opportunity context."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT cp.*, po.title AS opportunity_title, po.agency, po.naics_code "
            "FROM pg_capture_plans cp "
            "LEFT JOIN proposal_opportunities po ON po.id = cp.opportunity_id "
            "ORDER BY cp.updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        plans = [dict(r) for r in rows]
        return jsonify({"plans": plans, "count": len(plans)})
    except Exception as exc:
        return jsonify({"plans": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase B: Capture Plan Phase Gates ────────────────────────────────────────

CAPTURE_PHASES = ['qualify', 'pursue', 'capture', 'bid', 'proposal']
CAPTURE_PHASE_LABELS = {
    'qualify':  'Qualify',
    'pursue':   'Pursue',
    'capture':  'Capture',
    'bid':      'Bid/No-Bid',
    'proposal': 'Proposal',
}


@proposal_genesis_api.route("/capture-plans/<plan_id>/gates", methods=["GET"])
def api_pg_capture_plan_gates(plan_id):
    """GET /api/proposal-genesis/capture-plans/<plan_id>/gates — Gate decision history."""
    conn = _get_db()
    try:
        plan = conn.execute(
            "SELECT id, opportunity_id, current_phase FROM pg_capture_plans WHERE id = %s",
            (plan_id,),
        ).fetchone()
        if not plan:
            return jsonify({"error": "not_found"}), 404
        rows = conn.execute(
            "SELECT * FROM pg_capture_gate_decisions WHERE capture_plan_id = %s ORDER BY created_at DESC",
            (plan_id,),
        ).fetchall()
        gates = [dict(r) for r in rows]
        return jsonify({
            "plan_id": plan_id,
            "current_phase": plan["current_phase"] or "qualify",
            "phase_label": CAPTURE_PHASE_LABELS.get(plan["current_phase"] or "qualify", "Qualify"),
            "phases": CAPTURE_PHASES,
            "gates": gates,
            "count": len(gates),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@proposal_genesis_api.route("/capture-plans/<plan_id>/advance", methods=["POST"])
@require_role("capture_mgr", "admin")
def api_pg_capture_plan_advance(plan_id):
    """POST /api/proposal-genesis/capture-plans/<plan_id>/advance — Advance phase gate (capture_mgr, admin)."""
    import uuid as _uuid
    body = request.get_json(silent=True) or {}
    decision = body.get("decision", "advance")
    rationale = body.get("rationale", "")
    decided_by = body.get("decided_by", "")
    criteria_met = body.get("gate_criteria_met", "")

    if decision not in ("advance", "hold", "no_bid", "return"):
        return jsonify({"error": "invalid decision"}), 400

    conn = _get_db()
    try:
        plan = conn.execute(
            "SELECT id, opportunity_id, current_phase FROM pg_capture_plans WHERE id = %s",
            (plan_id,),
        ).fetchone()
        if not plan:
            return jsonify({"error": "not_found"}), 404

        current = plan["current_phase"] or "qualify"
        opp_id = plan["opportunity_id"]

        if decision == "no_bid":
            to_phase = "no_bid"
            new_phase = current
        elif decision == "advance":
            idx = CAPTURE_PHASES.index(current) if current in CAPTURE_PHASES else -1
            if idx == -1 or idx >= len(CAPTURE_PHASES) - 1:
                return jsonify({"error": "already_at_final_phase"}), 400
            to_phase = CAPTURE_PHASES[idx + 1]
            new_phase = to_phase
        else:
            to_phase = current
            new_phase = current

        gate_id = str(_uuid.uuid4())
        from datetime import datetime, timezone as _tz
        now = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        conn.execute(
            "INSERT INTO pg_capture_gate_decisions "
            "(id, capture_plan_id, opportunity_id, from_phase, to_phase, decision, rationale, decided_by, gate_criteria_met, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (gate_id, plan_id, opp_id, current, to_phase, decision, rationale, decided_by, criteria_met, now),
        )

        if decision == "advance":
            conn.execute(
                "UPDATE pg_capture_plans SET current_phase = %s, updated_at = %s WHERE id = %s",
                (new_phase, now, plan_id),
            )

        conn.commit()
        return jsonify({
            "gate_id": gate_id,
            "plan_id": plan_id,
            "from_phase": current,
            "to_phase": to_phase,
            "decision": decision,
            "new_phase": new_phase,
        }), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@proposal_genesis_api.route("/capture-plans/pipeline-summary", methods=["GET"])
@require_role("capture_mgr", "admin")
def api_pg_capture_pipeline_summary():
    """GET /api/proposal-genesis/capture-plans/pipeline-summary — Aggregated plan counts per phase."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT COALESCE(current_phase,'qualify') AS phase, COUNT(*) AS cnt "
            "FROM pg_capture_plans GROUP BY COALESCE(current_phase,'qualify')"
        ).fetchall()
        summary = {r["phase"]: r["cnt"] for r in rows}
        return jsonify({"summary": summary, "phases": CAPTURE_PHASES})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── Phase B: Teaming Assessments ─────────────────────────────────────────────


@proposal_genesis_api.route("/teaming-assessments", methods=["GET"])
def api_pg_teaming_assessments():
    """GET /api/proposal-genesis/teaming-assessments — Partner fit assessments."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ta.*, tp.name AS partner_name, po.title AS opportunity_title "
            "FROM pg_teaming_assessments ta "
            "LEFT JOIN pg_teaming_partners tp ON tp.id = ta.partner_id "
            "LEFT JOIN proposal_opportunities po ON po.id = ta.opportunity_id "
            "ORDER BY ta.fit_score DESC LIMIT %s",
            (limit,),
        ).fetchall()
        assessments = [dict(r) for r in rows]
        return jsonify({"assessments": assessments, "count": len(assessments)})
    except Exception as exc:
        return jsonify({"assessments": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase C: CRM Accounts ───────────────────────────────────────────────────


@proposal_genesis_api.route("/crm-accounts", methods=["GET"])
def api_pg_crm_accounts():
    """GET /api/proposal-genesis/crm-accounts — CRM accounts with engagement scores."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ca.*, "
            "(SELECT es.score FROM pg_crm_engagement_scores es "
            " WHERE es.account_id = ca.id ORDER BY es.created_at DESC LIMIT 1"
            ") AS latest_engagement_score "
            "FROM pg_crm_accounts ca "
            "WHERE ca.status IN ('active', 'prospect') "
            "ORDER BY ca.updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        accounts = [dict(r) for r in rows]
        return jsonify({"accounts": accounts, "count": len(accounts)})
    except Exception as exc:
        return jsonify({"accounts": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase C: CRM Interactions ────────────────────────────────────────────────


@proposal_genesis_api.route("/crm-interactions", methods=["GET"])
def api_pg_crm_interactions():
    """GET /api/proposal-genesis/crm-interactions — Recent CRM interactions."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ci.*, ca.name AS account_name, po.title AS opportunity_title "
            "FROM pg_crm_interactions ci "
            "LEFT JOIN pg_crm_accounts ca ON ca.id = ci.account_id "
            "LEFT JOIN proposal_opportunities po ON po.id = ci.opportunity_id "
            "ORDER BY ci.interaction_date DESC LIMIT %s",
            (limit,),
        ).fetchall()
        interactions = [dict(r) for r in rows]
        return jsonify({"interactions": interactions, "count": len(interactions)})
    except Exception as exc:
        return jsonify({"interactions": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase C: Engagement Scores ───────────────────────────────────────────────


@proposal_genesis_api.route("/engagement-scores", methods=["GET"])
def api_pg_engagement_scores():
    """GET /api/proposal-genesis/engagement-scores — Engagement scores per account."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT es.*, ca.name AS account_name "
            "FROM pg_crm_engagement_scores es "
            "LEFT JOIN pg_crm_accounts ca ON ca.id = es.account_id "
            "ORDER BY es.score DESC LIMIT %s",
            (limit,),
        ).fetchall()
        scores = [dict(r) for r in rows]
        return jsonify({"scores": scores, "count": len(scores)})
    except Exception as exc:
        return jsonify({"scores": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase C: CRM Account CRUD ────────────────────────────────────────────────


@proposal_genesis_api.route("/crm-accounts", methods=["POST"])
@require_role("admin", "pm", "bd", "capture_mgr")
def api_pg_create_account():
    """POST /api/proposal-genesis/crm-accounts — Create a CRM account."""
    from tools.proposal_genesis.reflexes.engage import create_account

    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400
    result = create_account(
        name=name,
        agency=data.get("agency", ""),
        sub_agency=data.get("sub_agency", ""),
        account_type=data.get("account_type", "government"),
        website=data.get("website", ""),
        naics_codes=data.get("naics_codes", ""),
        set_asides=data.get("set_asides", ""),
        notes=data.get("notes", ""),
        status=data.get("status", "active"),
    )
    return jsonify(result), 201 if result.get("success") else 400


@proposal_genesis_api.route("/crm-accounts/<account_id>", methods=["PUT"])
@require_role("admin", "pm", "bd", "capture_mgr")
def api_pg_update_account(account_id):
    """PUT /api/proposal-genesis/crm-accounts/<id> — Update a CRM account."""
    from tools.proposal_genesis.reflexes.engage import update_account

    data = request.get_json(force=True, silent=True) or {}
    result = update_account(account_id, **data)
    return jsonify(result), 200 if result.get("success") else 400


# ── Phase C: CRM Contact CRUD ───────────────────────────────────────────────


@proposal_genesis_api.route("/crm-contacts", methods=["GET"])
def api_pg_crm_contacts():
    """GET /api/proposal-genesis/crm-contacts — List CRM contacts."""
    from tools.proposal_genesis.reflexes.engage import list_contacts

    account_id = request.args.get("account_id")
    limit = int(request.args.get("limit", "50"))
    contacts = list_contacts(account_id=account_id, limit=limit)
    return jsonify({"contacts": contacts, "count": len(contacts)})


@proposal_genesis_api.route("/crm-contacts/<contact_id>", methods=["GET"])
def api_pg_get_contact(contact_id):
    """GET /api/proposal-genesis/crm-contacts/<id> — Get a single contact."""
    from tools.proposal_genesis.reflexes.engage import get_contact

    contact = get_contact(contact_id)
    if not contact:
        return jsonify({"error": "contact not found"}), 404
    return jsonify({"contact": contact})


@proposal_genesis_api.route("/crm-contacts", methods=["POST"])
@require_role("admin", "pm", "bd", "capture_mgr")
def api_pg_create_contact():
    """POST /api/proposal-genesis/crm-contacts — Create a CRM contact."""
    from tools.proposal_genesis.reflexes.engage import create_contact

    data = request.get_json(force=True, silent=True) or {}
    account_id = data.get("account_id", "").strip()
    name = data.get("name", "").strip()
    if not account_id:
        return jsonify({"success": False, "error": "account_id is required"}), 400
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400
    result = create_contact(
        account_id=account_id,
        name=name,
        title=data.get("title", ""),
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        role_in_procurement=data.get("role_in_procurement", ""),
        influence_level=data.get("influence_level", "unknown"),
        notes=data.get("notes", ""),
    )
    return jsonify(result), 201 if result.get("success") else 400


@proposal_genesis_api.route("/crm-contacts/<contact_id>", methods=["PUT"])
@require_role("admin", "pm", "bd", "capture_mgr")
def api_pg_update_contact(contact_id):
    """PUT /api/proposal-genesis/crm-contacts/<id> — Update a CRM contact."""
    from tools.proposal_genesis.reflexes.engage import update_contact

    data = request.get_json(force=True, silent=True) or {}
    result = update_contact(contact_id, **data)
    return jsonify(result), 200 if result.get("success") else 400


@proposal_genesis_api.route("/crm-contacts/<contact_id>", methods=["DELETE"])
@require_role("admin", "pm", "bd", "capture_mgr")
def api_pg_delete_contact(contact_id):
    """DELETE /api/proposal-genesis/crm-contacts/<id> — Delete a CRM contact."""
    from tools.proposal_genesis.reflexes.engage import delete_contact

    result = delete_contact(contact_id)
    return jsonify(result), 200 if result.get("success") else 404


# ── Phase C: Manual Interaction Logging ──────────────────────────────────────


@proposal_genesis_api.route("/crm-interactions", methods=["POST"])
@require_role("admin", "pm", "bd", "capture_mgr")
def api_pg_log_interaction():
    """POST /api/proposal-genesis/crm-interactions — Log a manual interaction."""
    from tools.proposal_genesis.reflexes.engage import log_manual_interaction

    data = request.get_json(force=True, silent=True) or {}
    account_id = data.get("account_id", "").strip()
    interaction_type = data.get("interaction_type", "").strip()
    subject = data.get("subject", "").strip()
    if not account_id:
        return jsonify({"success": False, "error": "account_id is required"}), 400
    if not interaction_type:
        return jsonify({"success": False, "error": "interaction_type is required"}), 400
    if not subject:
        return jsonify({"success": False, "error": "subject is required"}), 400
    result = log_manual_interaction(
        account_id=account_id,
        interaction_type=interaction_type,
        subject=subject,
        contact_id=data.get("contact_id", ""),
        notes=data.get("notes", ""),
        opportunity_id=data.get("opportunity_id", ""),
        interaction_date=data.get("interaction_date"),
    )
    return jsonify(result), 201 if result.get("success") else 400


# ── Phase D: Published Articles ──────────────────────────────────────────────


@proposal_genesis_api.route("/published-articles", methods=["GET"])
def api_pg_published_articles():
    """GET /api/proposal-genesis/published-articles — Case study articles staged by Publish reflex."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT pp.id, pp.title, pp.slug, pp.status, pp.topic, "
            "pp.readability_score, pp.author_id, pp.created_at, pp.updated_at "
            "FROM pulse_posts pp "
            "WHERE pp.author_id = 'pg_publish' "
            "ORDER BY pp.created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        articles = [dict(r) for r in rows]
        return jsonify({"articles": articles, "count": len(articles)})
    except Exception as exc:
        return jsonify({"articles": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


@proposal_genesis_api.route("/case-study-links", methods=["GET"])
def api_pg_case_study_links():
    """GET /api/proposal-genesis/case-study-links — CDRL-to-case-study Pulse links."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ppl.*, po.title AS opportunity_title "
            "FROM pg_pulse_proposal_links ppl "
            "LEFT JOIN proposal_opportunities po ON po.id = ppl.opportunity_id "
            "WHERE ppl.link_type = 'cdrl_to_case_study' "
            "ORDER BY ppl.created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        links = [dict(r) for r in rows]
        return jsonify({"links": links, "count": len(links)})
    except Exception as exc:
        return jsonify({"links": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase E: Contract Health ──────────────────────────────────────────────


@proposal_genesis_api.route("/contract-health", methods=["GET"])
def api_pg_contract_health():
    """GET /api/proposal-genesis/contract-health — Active contract health summary."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, contract_number, title, agency, status, "
            "health, health_score, total_value, funded_value, "
            "billed_value, pop_start, pop_end, cpars_rating_current "
            "FROM cpmp_contracts "
            "WHERE status IN ('active', 'option_pending') "
            "ORDER BY health_score ASC LIMIT %s",
            (limit,),
        ).fetchall()
        contracts = [dict(r) for r in rows]
        return jsonify({"contracts": contracts, "count": len(contracts)})
    except Exception as exc:
        return jsonify({"contracts": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase E: CPARS Predictions ────────────────────────────────────────────


@proposal_genesis_api.route("/cpars-predictions", methods=["GET"])
def api_pg_cpars_predictions():
    """GET /api/proposal-genesis/cpars-predictions — Recent CPARS monitoring results."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, event_type, opportunity_id, details, created_at "
            "FROM pg_proposal_genesis_audit "
            "WHERE reflex_name = 'monitor' "
            "AND event_type = 'contract_monitored' "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        predictions = []
        for r in rows:
            e = dict(r)
            try:
                e["parsed"] = json.loads(e.get("details", "{}"))
            except Exception:
                e["parsed"] = {}
            predictions.append(e)
        return jsonify({"predictions": predictions, "count": len(predictions)})
    except Exception as exc:
        return jsonify({"predictions": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase E: Overdue Deliverables ─────────────────────────────────────────


@proposal_genesis_api.route("/overdue-deliverables", methods=["GET"])
def api_pg_overdue_deliverables():
    """GET /api/proposal-genesis/overdue-deliverables — Overdue CPMP deliverables."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT d.id, d.cdrl_number, d.title, d.deliverable_type, "
            "d.due_date, d.status, d.days_overdue, "
            "c.contract_number, c.title AS contract_title "
            "FROM cpmp_deliverables d "
            "JOIN cpmp_contracts c ON c.id = d.contract_id "
            "WHERE c.status IN ('active', 'option_pending') "
            "AND d.status = 'overdue' "
            "ORDER BY d.days_overdue DESC LIMIT %s",
            (limit,),
        ).fetchall()
        deliverables = [dict(r) for r in rows]
        return jsonify({"deliverables": deliverables, "count": len(deliverables)})
    except Exception as exc:
        return jsonify({"deliverables": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase E: CDRL Generations ─────────────────────────────────────────────


@proposal_genesis_api.route("/cdrl-generations", methods=["GET"])
def api_pg_cdrl_generations():
    """GET /api/proposal-genesis/cdrl-generations — CDRL auto-generation audit trail."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT cg.id, cg.deliverable_id, cg.contract_id, "
            "cg.cdrl_type, cg.generation_tool, cg.status, "
            "cg.error_message, cg.generated_by, cg.created_at, "
            "d.title AS deliverable_title, d.cdrl_number, "
            "c.contract_number "
            "FROM cpmp_cdrl_generations cg "
            "LEFT JOIN cpmp_deliverables d ON d.id = cg.deliverable_id "
            "LEFT JOIN cpmp_contracts c ON c.id = cg.contract_id "
            "ORDER BY cg.created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        generations = [dict(r) for r in rows]
        return jsonify({"generations": generations, "count": len(generations)})
    except Exception as exc:
        return jsonify({"generations": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase F: Bid Decisions ──────────────────────────────────────────────────


@proposal_genesis_api.route("/bid-decisions", methods=["GET"])
def api_pg_bid_decisions():
    """GET /api/proposal-genesis/bid-decisions — Bid/no-bid scoring decisions."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT bd.id, bd.opportunity_id, bd.decision, bd.win_probability, "
            "bd.score_breakdown, bd.rationale, bd.decided_by, bd.created_at, "
            "o.title AS opportunity_title, o.agency, o.naics_code "
            "FROM pg_bid_decisions bd "
            "LEFT JOIN sam_gov_opportunities o ON o.id = bd.opportunity_id "
            "ORDER BY bd.created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        decisions = []
        for r in rows:
            d = dict(r)
            try:
                d["scores"] = json.loads(d.get("score_breakdown") or "{}")
            except Exception:
                d["scores"] = {}
            decisions.append(d)
        return jsonify({"decisions": decisions, "count": len(decisions)})
    except Exception as exc:
        return jsonify({"decisions": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase F: Win/Loss Records ───────────────────────────────────────────────


@proposal_genesis_api.route("/win-loss-records", methods=["GET"])
def api_pg_win_loss_records():
    """GET /api/proposal-genesis/win-loss-records — Win/loss analysis records."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT wl.id, wl.opportunity_id, wl.outcome, wl.competitor_name, "
            "wl.competitor_strengths, wl.our_strengths, wl.our_weaknesses, "
            "wl.lessons_learned, wl.created_at, wl.classification, "
            "o.title AS opportunity_title, o.agency "
            "FROM pg_win_loss_records wl "
            "LEFT JOIN sam_gov_opportunities o ON o.id = wl.opportunity_id "
            "ORDER BY wl.created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        records = []
        for r in rows:
            rec = dict(r)
            try:
                rec["lessons_parsed"] = json.loads(rec.get("lessons_learned") or "[]")
            except Exception:
                rec["lessons_parsed"] = []
            records.append(rec)
        records = _mac_filter_by_classification(records)
        return jsonify({"records": records, "count": len(records)})
    except Exception as exc:
        return jsonify({"records": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase F: Win/Loss Lessons ───────────────────────────────────────────────


@proposal_genesis_api.route("/win-loss-lessons", methods=["GET"])
def api_pg_win_loss_lessons():
    """GET /api/proposal-genesis/win-loss-lessons — Categorized lessons learned."""
    limit = int(request.args.get("limit", "100"))
    category = request.args.get("category")
    conn = _get_db()
    try:
        query = (
            "SELECT l.id, l.win_loss_id, l.category, l.lesson, "
            "l.actionable, l.applied, l.created_at, l.classification, "
            "wl.outcome, wl.opportunity_id, "
            "o.title AS opportunity_title, o.agency "
            "FROM pg_win_loss_lessons l "
            "LEFT JOIN pg_win_loss_records wl ON wl.id = l.win_loss_id "
            "LEFT JOIN sam_gov_opportunities o ON o.id = wl.opportunity_id "
        )
        params = []
        if category:
            query += "WHERE l.category = ? "
            params.append(category)
        query += "ORDER BY l.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        lessons = _mac_filter_by_classification([dict(r) for r in rows])
        return jsonify({"lessons": lessons, "count": len(lessons)})
    except Exception as exc:
        return jsonify({"lessons": [], "count": 0, "note": str(exc)})
    finally:
        conn.close()


# ── Phase F: Training Pairs ─────────────────────────────────────────────────


@proposal_genesis_api.route("/training-pairs", methods=["GET"])
def api_pg_training_pairs():
    """GET /api/proposal-genesis/training-pairs — Fine-tuning training pair tracking."""
    limit = int(request.args.get("limit", "50"))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, source_type, source_id, pair_count, "
            "content_hash, created_at "
            "FROM pg_training_pair_sources "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        pairs = [dict(r) for r in rows]

        # Aggregate stats
        agg = conn.execute(
            "SELECT source_type, COUNT(*) as cnt, SUM(pair_count) as total_pairs "
            "FROM pg_training_pair_sources GROUP BY source_type"
        ).fetchall()
        by_source = {r["source_type"]: {"count": r["cnt"], "pairs": r["total_pairs"]} for r in agg}

        return jsonify(
            {
                "pairs": pairs,
                "count": len(pairs),
                "by_source": by_source,
            }
        )
    except Exception as exc:
        return jsonify({"pairs": [], "count": 0, "by_source": {}, "note": str(exc)})
    finally:
        conn.close()


# ── Trend Charts ──────────────────────────────────────────────────────────────


@proposal_genesis_api.route("/trends/win-rate", methods=["GET"])
def api_pg_trend_win_rate():
    """GET /api/proposal-genesis/trends/win-rate — Win rate over time (monthly)."""
    conn = _get_db()
    try:
        _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
        _mfmt = "to_char(bdo.created_at::timestamp, 'YYYY-MM')" if _pg else "strftime('%Y-%m', bdo.created_at)"
        rows = conn.execute(f"""
            SELECT {_mfmt} AS month,
                   COUNT(*) AS total,
                   SUM(CASE WHEN bdo.outcome = 'won' THEN 1 ELSE 0 END) AS wins
            FROM pg_bid_decision_outcomes bdo
            WHERE bdo.outcome IN ('won', 'lost')
            GROUP BY month
            ORDER BY month ASC
            LIMIT 24
        """).fetchall()
        data = []
        for r in rows:
            total = r["total"] or 1
            data.append(
                {
                    "month": r["month"],
                    "total": total,
                    "wins": r["wins"] or 0,
                    "win_rate": round((r["wins"] or 0) / total, 3),
                }
            )
        return jsonify({"data": data})
    except Exception:
        return jsonify({"data": []})
    finally:
        conn.close()


@proposal_genesis_api.route("/trends/quality-scores", methods=["GET"])
def api_pg_trend_quality():
    """GET /api/proposal-genesis/trends/quality-scores — Avg quality score over time (weekly)."""
    conn = _get_db()
    try:
        _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
        _wfmt = "to_char(created_at::timestamp, 'YYYY-\"W\"IW')" if _pg else "strftime('%Y-W%W', created_at)"
        rows = conn.execute(f"""
            SELECT {_wfmt} AS week,
                   ROUND(AVG(composite_score), 2) AS avg_score,
                   COUNT(*) AS count
            FROM pg_proposal_quality_scores
            GROUP BY week
            ORDER BY week ASC
            LIMIT 52
        """).fetchall()
        data = [{"week": r["week"], "avg_score": r["avg_score"], "count": r["count"]} for r in rows]
        return jsonify({"data": data})
    except Exception:
        return jsonify({"data": []})
    finally:
        conn.close()


@proposal_genesis_api.route("/trends/training-pairs", methods=["GET"])
def api_pg_trend_training_pairs():
    """GET /api/proposal-genesis/trends/training-pairs — Cumulative training pair growth (weekly)."""
    conn = _get_db()
    try:
        _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
        _wfmt = "to_char(created_at::timestamp, 'YYYY-\"W\"IW')" if _pg else "strftime('%Y-W%W', created_at)"
        rows = conn.execute(f"""
            SELECT {_wfmt} AS week,
                   SUM(pair_count) AS pairs_added
            FROM pg_training_pair_sources
            GROUP BY week
            ORDER BY week ASC
            LIMIT 52
        """).fetchall()
        data = []
        cumulative = 0
        for r in rows:
            cumulative += r["pairs_added"] or 0
            data.append(
                {
                    "week": r["week"],
                    "pairs_added": r["pairs_added"] or 0,
                    "cumulative": cumulative,
                }
            )
        return jsonify({"data": data})
    except Exception:
        return jsonify({"data": []})
    finally:
        conn.close()


@proposal_genesis_api.route("/trends/engagement", methods=["GET"])
def api_pg_trend_engagement():
    """GET /api/proposal-genesis/trends/engagement — Engagement score distribution."""
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT es.account_id, ca.name AS account_name,
                   es.score, es.score_breakdown
            FROM pg_crm_engagement_scores es
            JOIN pg_crm_accounts ca ON ca.id = es.account_id
            WHERE es.id IN (
                SELECT id FROM pg_crm_engagement_scores es2
                WHERE es2.account_id = es.account_id
                ORDER BY es2.created_at DESC LIMIT 1
            )
            ORDER BY es.score DESC
            LIMIT 20
        """).fetchall()
        data = []
        for r in rows:
            entry = {"account": r["account_name"], "score": r["score"]}
            try:
                entry["breakdown"] = json.loads(r["score_breakdown"] or "{}")
            except Exception:
                entry["breakdown"] = {}
            data.append(entry)
        return jsonify({"data": data})
    except Exception:
        return jsonify({"data": []})
    finally:
        conn.close()
