#!/usr/bin/env python3
# CUI // SP-PROPIN
"""GovProposal Blueprint — routes from the standalone GovProposal app,
mounted under /gov-proposal in ICDEV core.

Enabled when ICDEV_GOV_PROPOSAL_ENABLED=true in .env.
"""
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash)

logger = logging.getLogger("icdev.gov_proposal")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent  # icdev/tools/gov_proposal/ -> repo root
_GP_DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

gov_proposal_bp = Blueprint(
    "gov_proposal",
    __name__,
    url_prefix="/gov-proposal",
    template_folder=str(BASE_DIR / "tools" / "dashboard" / "templates"),
)

CUI_BANNER = os.environ.get("GOVPROPOSAL_CUI_BANNER", "CUI // SP-PROPIN")
_API_KEY = os.environ.get("GOVPROPOSAL_API_KEY", "").strip()
_RESEARCH_RATE_LIMIT = (10, 60)

_rl_lock = threading.Lock()
_rl_windows: dict = defaultdict(deque)


def _check_rate_limit(key: str, max_calls: int, window_secs: int) -> bool:
    now = time.monotonic()
    with _rl_lock:
        dq = _rl_windows[key]
        cutoff = now - window_secs
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_calls:
            return False
        dq.append(now)
        return True


def _get_db():
    import sqlite3
    conn = sqlite3.connect(str(_GP_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json(text):
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}

# CONTEXT PROCESSOR
# =========================================================================
@gov_proposal_bp.context_processor
def inject_globals():
    pending_reminders = 0
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM deliverable_reminders "
            "WHERE status = 'pending' AND reminder_date <= date('now')"
        ).fetchone()
        pending_reminders = row["cnt"] if row else 0
        conn.close()
    except Exception:
        pass
    return {
        "cui_banner": CUI_BANNER,
        "now": _now(),
        "app_name": "GovProposal Portal",
        "pending_reminders": pending_reminders,
    }


# =========================================================================
# ERROR HANDLERS
# =========================================================================


# =========================================================================
# AUTH + RATE LIMITING (before_request)
# =========================================================================
_API_KEY = os.environ.get("GOVPROPOSAL_API_KEY", "").strip()
# Research endpoint: max 10 calls per minute per IP
_RESEARCH_RATE_LIMIT = (10, 60)


@gov_proposal_bp.before_request
def _before_request():
    from flask import request as _req
    path = _req.path

    # ── L: Optional API key auth for /api/* routes ────────────────────────
    if _API_KEY and path.startswith("/api/"):
        provided = (
            _req.headers.get("X-Api-Key", "")
            or _req.args.get("api_key", "")
        )
        if provided != _API_KEY:
            return jsonify({"error": "Unauthorized. Provide X-Api-Key header."}), 401

    # ── I: Rate limit /api/rfx/research ──────────────────────────────────
    if path == "/api/rfx/research" and _req.method == "POST":
        ip = _req.remote_addr or "unknown"
        max_calls, window = _RESEARCH_RATE_LIMIT
        if not _check_rate_limit(f"research:{ip}", max_calls, window):
            return jsonify({
                "error": f"Rate limit exceeded. Max {max_calls} research requests per {window}s."
            }), 429


# =========================================================================
# ROUTES
# =========================================================================
@gov_proposal_bp.route("/")
def home():
    """Home dashboard with pipeline overview."""
    conn = _get_db()
    try:
        # Pipeline stats
        stages = {}
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM opportunities GROUP BY status"
            ).fetchall()
            for row in rows:
                stages[row["status"]] = row["cnt"]
        except Exception as e:
            logger.error("Error querying opportunity pipeline stage counts: %s", e)

        # Recent opportunities
        recent_opps = []
        try:
            recent_opps = conn.execute(
                """SELECT id, title, agency, response_deadline, fit_score, status
                   FROM opportunities ORDER BY discovered_at DESC LIMIT 10"""
            ).fetchall()
        except Exception as e:
            logger.error("Error querying recent opportunities: %s", e)

        # Active proposals
        active_proposals = []
        try:
            active_proposals = conn.execute(
                """SELECT p.id, p.title, p.status, p.cag_status, p.due_date,
                          o.agency
                   FROM proposals p
                   LEFT JOIN opportunities o ON p.opportunity_id = o.id
                   WHERE p.status NOT IN ('submitted', 'awarded', 'lost')
                   ORDER BY p.due_date ASC LIMIT 10"""
            ).fetchall()
        except Exception as e:
            logger.error("Error querying active proposals: %s", e)

        # CAG alerts
        open_alerts = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM cag_alerts WHERE status = 'open'"
            ).fetchone()
            open_alerts = row["cnt"] if row else 0
        except Exception as e:
            logger.error("Error querying open CAG alerts count: %s", e)

        return render_template("gov_proposal/home.html",
                               stages=stages,
                               recent_opps=recent_opps,
                               active_proposals=active_proposals,
                               open_alerts=open_alerts)
    finally:
        conn.close()


@gov_proposal_bp.route("/opportunities")
def opportunities():
    """Opportunity listing."""
    conn = _get_db()
    try:
        status_filter = request.args.get("status")
        agency_filter = request.args.get("agency")

        query = "SELECT * FROM opportunities"
        params = []
        conditions = []

        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter)
        if agency_filter:
            conditions.append("agency LIKE ?")
            params.append(f"%{agency_filter}%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY discovered_at DESC LIMIT 100"

        opps = conn.execute(query, params).fetchall()

        # Get distinct agencies for filter
        agencies = []
        try:
            agencies = [r["agency"] for r in conn.execute(
                "SELECT DISTINCT agency FROM opportunities ORDER BY agency"
            ).fetchall()]
        except Exception as e:
            logger.error("Error querying distinct agencies for filter: %s", e)

        return render_template("gov_proposal/opportunities.html",
                               opportunities=opps,
                               agencies=agencies,
                               status_filter=status_filter,
                               agency_filter=agency_filter)
    finally:
        conn.close()


@gov_proposal_bp.route("/opportunities/<opp_id>")
def opportunity_detail(opp_id):
    """Opportunity detail with scorecard."""
    conn = _get_db()
    try:
        opp = conn.execute(
            "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        if not opp:
            flash("Opportunity not found", "error")
            return redirect(url_for("gov_proposal.opportunities"))

        # Qualification scores
        scores = conn.execute(
            "SELECT * FROM opportunity_scores WHERE opportunity_id = ? ORDER BY dimension",
            (opp_id,)
        ).fetchall()

        # Pipeline history
        pipeline = conn.execute(
            "SELECT * FROM pipeline_stages WHERE opportunity_id = ? ORDER BY entered_at",
            (opp_id,)
        ).fetchall()

        # Related proposals
        proposals = conn.execute(
            "SELECT * FROM proposals WHERE opportunity_id = ?", (opp_id,)
        ).fetchall()

        return render_template("gov_proposal/opportunity_detail.html",
                               opp=opp, scores=scores,
                               pipeline=pipeline, proposals=proposals)
    finally:
        conn.close()


@gov_proposal_bp.route("/proposals")
def proposals():
    """Proposal listing."""
    conn = _get_db()
    try:
        props = conn.execute(
            """SELECT p.*, o.agency, o.title as opp_title
               FROM proposals p
               LEFT JOIN opportunities o ON p.opportunity_id = o.id
               ORDER BY p.updated_at DESC LIMIT 50"""
        ).fetchall()
        return render_template("gov_proposal/proposals.html", proposals=props)
    finally:
        conn.close()


@gov_proposal_bp.route("/proposals/kanban")
def proposals_kanban():
    """Proposal Kanban board — proposals grouped by workflow status."""
    conn = _get_db()
    try:
        props = conn.execute("""
            SELECT p.id, p.title, p.status, p.due_date, p.cag_status,
                   p.assigned_pm, p.result, p.updated_at,
                   o.agency, o.title as opp_title,
                   COUNT(DISTINCT s.id)                                    AS section_count,
                   SUM(CASE WHEN s.hitl_status='accepted' THEN 1 ELSE 0 END) AS accepted_count,
                   SUM(CASE WHEN s.hitl_status='pending'  THEN 1 ELSE 0 END) AS pending_count
            FROM proposals p
            LEFT JOIN opportunities o ON p.opportunity_id = o.id
            LEFT JOIN rfx_ai_sections s ON s.proposal_id = p.id
            GROUP BY p.id
            ORDER BY p.due_date ASC, p.updated_at DESC
        """).fetchall()
    finally:
        conn.close()

    # Group into ordered columns
    COLUMNS = [
        ("draft",        "Draft",        "#95a5a6"),
        ("pink_review",  "Pink Review",  "#e91e8c"),
        ("red_review",   "Red Review",   "#e74c3c"),
        ("gold_review",  "Gold Review",  "#f39c12"),
        ("white_review", "White Review", "#bdc3c7"),
        ("final",        "Final",        "#2980b9"),
        ("submitted",    "Submitted",    "#8e44ad"),
        ("awarded",      "Awarded",      "#27ae60"),
        ("lost",         "Lost",         "#7f8c8d"),
    ]
    columns = {key: [] for key, _, _ in COLUMNS}
    for p in props:
        status = (p["status"] or "draft").lower()
        if status not in columns:
            status = "draft"
        columns[status].append(dict(p))

    return render_template(
        "proposals_kanban.html",
        columns=columns,
        column_meta=COLUMNS,
        total=len(props),
    )


@gov_proposal_bp.route("/proposals/<prop_id>")
def proposal_detail(prop_id):
    """Proposal detail with sections, reviews, CAG status."""
    conn = _get_db()
    try:
        prop = conn.execute(
            """SELECT p.*, o.title as opp_title, o.agency, o.response_deadline
               FROM proposals p
               LEFT JOIN opportunities o ON p.opportunity_id = o.id
               WHERE p.id = ?""", (prop_id,)
        ).fetchone()
        if not prop:
            flash("Proposal not found", "error")
            return redirect(url_for("gov_proposal.proposals"))

        # Sections by volume
        sections = conn.execute(
            """SELECT * FROM proposal_sections
               WHERE proposal_id = ? ORDER BY volume, section_number""",
            (prop_id,)
        ).fetchall()

        # Reviews
        reviews = conn.execute(
            """SELECT * FROM proposal_reviews
               WHERE proposal_id = ? ORDER BY review_type, created_at DESC""",
            (prop_id,)
        ).fetchall()

        # CAG alerts
        alerts = conn.execute(
            "SELECT * FROM cag_alerts WHERE proposal_id = ? ORDER BY created_at DESC",
            (prop_id,)
        ).fetchall()

        # Compliance matrix
        compliance = conn.execute(
            """SELECT compliance_status, COUNT(*) as cnt
               FROM compliance_matrices WHERE proposal_id = ?
               GROUP BY compliance_status""",
            (prop_id,)
        ).fetchall()

        # Win themes
        themes = conn.execute(
            "SELECT * FROM win_themes WHERE proposal_id = ?", (prop_id,)
        ).fetchall()

        return render_template("gov_proposal/proposal_detail.html",
                               prop=prop, sections=sections,
                               reviews=reviews, alerts=alerts,
                               compliance=compliance, themes=themes)
    finally:
        conn.close()


@gov_proposal_bp.route("/knowledge")
def knowledge():
    """Knowledge base browser."""
    conn = _get_db()
    try:
        entry_type = request.args.get("type")
        search_query = request.args.get("q")

        query = "SELECT * FROM kb_entries WHERE is_active = 1"
        params = []

        if entry_type:
            query += " AND entry_type = ?"
            params.append(entry_type)
        if search_query:
            query += " AND (title LIKE ? OR content LIKE ?)"
            params.extend([f"%{search_query}%", f"%{search_query}%"])

        query += " ORDER BY updated_at DESC LIMIT 50"
        entries = conn.execute(query, params).fetchall()

        # Type counts
        type_counts = {}
        try:
            rows = conn.execute(
                "SELECT entry_type, COUNT(*) as cnt FROM kb_entries "
                "WHERE is_active = 1 GROUP BY entry_type"
            ).fetchall()
            type_counts = {r["entry_type"]: r["cnt"] for r in rows}
        except Exception as e:
            logger.error("Error querying knowledge base entry type counts: %s", e)

        return render_template("gov_proposal/knowledge.html",
                               entries=entries,
                               type_counts=type_counts,
                               entry_type=entry_type,
                               search_query=search_query)
    finally:
        conn.close()


@gov_proposal_bp.route("/cag")
def cag_monitor():
    """Classification Aggregation Guard monitor."""
    conn = _get_db()
    try:
        # Open alerts by severity
        alert_summary = {}
        try:
            rows = conn.execute(
                """SELECT severity, COUNT(*) as cnt FROM cag_alerts
                   WHERE status = 'open' GROUP BY severity"""
            ).fetchall()
            alert_summary = {r["severity"]: r["cnt"] for r in rows}
        except Exception as e:
            logger.error("Error querying CAG alert summary by severity: %s", e)

        # Recent alerts
        recent_alerts = []
        try:
            recent_alerts = conn.execute(
                """SELECT ca.*, p.title as proposal_title
                   FROM cag_alerts ca
                   LEFT JOIN proposals p ON ca.proposal_id = p.id
                   ORDER BY ca.created_at DESC LIMIT 20"""
            ).fetchall()
        except Exception as e:
            logger.error("Error querying recent CAG alerts: %s", e)

        # Active rules
        rule_count = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM cag_rules WHERE is_active = 1"
            ).fetchone()
            rule_count = row["cnt"] if row else 0
        except Exception as e:
            logger.error("Error querying active CAG rules count: %s", e)

        # Exposure register summary
        exposures = []
        try:
            exposures = conn.execute(
                """SELECT capability_group,
                          COUNT(*) as exposure_count,
                          GROUP_CONCAT(DISTINCT categories_exposed) as all_categories
                   FROM cag_exposure_register
                   GROUP BY capability_group
                   ORDER BY exposure_count DESC LIMIT 10"""
            ).fetchall()
        except Exception as e:
            logger.error("Error querying CAG exposure register: %s", e)

        return render_template("gov_proposal/cag.html",
                               alert_summary=alert_summary,
                               recent_alerts=recent_alerts,
                               rule_count=rule_count,
                               exposures=exposures)
    finally:
        conn.close()


@gov_proposal_bp.route("/competitors")
def competitors():
    """Competitive intelligence dashboard."""
    conn = _get_db()
    try:
        comps = []
        try:
            raw = conn.execute(
                """SELECT c.*, COUNT(cw.id) as win_count
                   FROM competitors c
                   LEFT JOIN competitor_wins cw ON c.id = cw.competitor_id
                   WHERE c.is_active = 1
                   GROUP BY c.id
                   ORDER BY win_count DESC"""
            ).fetchall()
            # Template expects 'name' and 'primary_naics'; DB has 'company_name' and 'naics_codes'
            import json as _json
            for r in raw:
                d = dict(r)
                d["name"] = d.get("company_name", "")
                nc = d.get("naics_codes", "")
                try:
                    codes = _json.loads(nc) if nc else []
                    d["primary_naics"] = codes[0] if codes else ""
                except Exception:
                    d["primary_naics"] = nc or ""
                comps.append(d)
        except Exception as e:
            logger.error("Error querying competitors with win counts: %s", e)

        # Recent wins
        recent_wins = []
        try:
            recent_wins = conn.execute(
                """SELECT * FROM competitor_wins
                   ORDER BY award_date DESC LIMIT 20"""
            ).fetchall()
        except Exception as e:
            logger.error("Error querying recent competitor wins: %s", e)

        return render_template("gov_proposal/competitors.html",
                               competitors=comps,
                               recent_wins=recent_wins)
    finally:
        conn.close()


@gov_proposal_bp.route("/analytics")
def analytics():
    """Win/loss analytics."""
    conn = _get_db()
    try:
        # Win/loss stats
        stats = {"wins": 0, "losses": 0, "win_rate": 0.0}
        try:
            rows = conn.execute(
                "SELECT result, COUNT(*) as cnt FROM debriefs GROUP BY result"
            ).fetchall()
            for row in rows:
                if row["result"] == "win":
                    stats["wins"] = row["cnt"]
                elif row["result"] == "loss":
                    stats["losses"] = row["cnt"]
            total = stats["wins"] + stats["losses"]
            if total > 0:
                stats["win_rate"] = round(stats["wins"] / total * 100, 1)
        except Exception as e:
            logger.error("Error querying win/loss stats from debriefs: %s", e)

        # Patterns
        patterns = []
        try:
            patterns = conn.execute(
                "SELECT * FROM win_loss_patterns ORDER BY confidence DESC LIMIT 10"
            ).fetchall()
        except Exception as e:
            logger.error("Error querying win/loss patterns: %s", e)

        # Pipeline value
        pipeline_value = 0
        try:
            row = conn.execute(
                """SELECT SUM(estimated_value_high) as total
                   FROM opportunities
                   WHERE status NOT IN ('no_bid', 'archived', 'lost')"""
            ).fetchone()
            pipeline_value = row["total"] or 0
        except Exception as e:
            logger.error("Error querying pipeline total value: %s", e)

        return render_template("gov_proposal/analytics.html",
                               stats=stats, patterns=patterns,
                               pipeline_value=pipeline_value)
    finally:
        conn.close()


@gov_proposal_bp.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "govproposal-dashboard",
        "db_path": str(DB_PATH),
        "timestamp": _now(),
    })


# =========================================================================
# SAM.GOV SCAN ENDPOINT
# =========================================================================
@gov_proposal_bp.route("/api/sam/scan", methods=["POST"])
def api_sam_scan():
    """Trigger a SAM.gov opportunity scan.

    POST body (JSON, all optional):
        naics   — comma-separated NAICS codes, e.g. "541512,541519"
        agency  — agency name filter
        days_back — integer, days to look back (default 7)
    """
    try:
        from tools.monitor.sam_scanner import scan_opportunities
    except ImportError as exc:
        return jsonify({"error": f"Scanner module not available: {exc}"}), 500

    body = request.get_json(silent=True) or {}
    naics = body.get("naics") or None
    agency = body.get("agency") or None
    days_back = int(body.get("days_back", 7))

    api_key = os.environ.get("SAM_GOV_API_KEY", "")
    if not api_key:
        return jsonify({
            "error": "SAM_GOV_API_KEY is not configured. "
                     "Set it in your .env file and restart the server."
        }), 400

    result = scan_opportunities(naics=naics, agency=agency, days_back=days_back)
    status_code = 200 if result.get("status") == "success" else (
        429 if result.get("errors") and "429" in str(result.get("errors")) else 200
    )
    return jsonify(result), status_code


# =========================================================================
# API ENDPOINTS (JSON)
# =========================================================================
@gov_proposal_bp.route("/api/opportunities", methods=["GET"])
def api_opportunities():
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, agency, status, fit_score, response_deadline "
            "FROM opportunities ORDER BY discovered_at DESC LIMIT 50"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@gov_proposal_bp.route("/api/proposals", methods=["GET"])
def api_proposals():
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, status, cag_status, due_date "
            "FROM proposals ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@gov_proposal_bp.route("/api/proposals/<prop_id>/status", methods=["PATCH"])
def api_proposal_move_status(prop_id):
    """Move a proposal to a new status (used by Kanban drag-drop)."""
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip().lower()
    valid = {"draft", "pink_review", "red_review", "gold_review",
             "white_review", "final", "submitted", "awarded", "lost"}
    if new_status not in valid:
        return jsonify({"error": f"Invalid status '{new_status}'"}), 400
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id FROM proposals WHERE id = ?", (prop_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Proposal not found"}), 404
        conn.execute(
            "UPDATE proposals SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, prop_id),
        )
        conn.commit()
        return jsonify({"status": "ok", "new_status": new_status})
    finally:
        conn.close()


@gov_proposal_bp.route("/api/cag/alerts", methods=["GET"])
def api_cag_alerts():
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM cag_alerts WHERE status = 'open' "
            "ORDER BY severity DESC, created_at DESC LIMIT 20"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@gov_proposal_bp.route("/api/pipeline/stats", methods=["GET"])
def api_pipeline_stats():
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM opportunities GROUP BY status"
        ).fetchall()
        return jsonify({r["status"]: r["count"] for r in rows})
    finally:
        conn.close()


# =========================================================================
# ERP ROUTES — Employee, Skills, LCATs, Capabilities
# =========================================================================

@gov_proposal_bp.route("/team")
def team():
    """ERP: Employee directory."""
    conn = _get_db()
    try:
        company_type = request.args.get("type")
        clearance = request.args.get("clearance")
        availability = request.args.get("availability")
        search = request.args.get("search", "").strip()

        query = """
            SELECT e.id, e.full_name, e.job_title, e.company_type,
                   e.clearance_level, e.clearance_status, e.clearance_expiry,
                   e.availability, e.status, e.location, e.years_experience,
                   l.lcat_code, l.lcat_name,
                   COUNT(DISTINCT es.skill_id) AS skill_count,
                   COUNT(DISTINCT c.id) AS cert_count
            FROM employees e
            LEFT JOIN employee_lcats el ON el.employee_id = e.id
                AND el.end_date IS NULL AND el.is_primary = 1
            LEFT JOIN lcats l ON el.lcat_id = l.id
            LEFT JOIN employee_skills es ON es.employee_id = e.id
            LEFT JOIN certifications c ON c.employee_id = e.id
                AND c.status = 'active'
            WHERE e.status = 'active'
        """
        params = []
        if company_type:
            query += " AND e.company_type = ?"
            params.append(company_type)
        if clearance:
            query += " AND e.clearance_level = ?"
            params.append(clearance)
        if availability:
            query += " AND e.availability = ?"
            params.append(availability)
        if search:
            query += " AND (e.full_name LIKE ? OR e.job_title LIKE ? OR l.lcat_name LIKE ?)"
            params.extend([f"%{search}%"] * 3)
        query += " GROUP BY e.id ORDER BY e.full_name LIMIT 200"

        employees = conn.execute(query, params).fetchall()

        # Stats
        stats = {}
        try:
            stats["total"] = conn.execute(
                "SELECT COUNT(*) FROM employees WHERE status='active'"
            ).fetchone()[0]
            stats["own"] = conn.execute(
                "SELECT COUNT(*) FROM employees WHERE status='active' AND company_type='own'"
            ).fetchone()[0]
            stats["cleared"] = conn.execute(
                "SELECT COUNT(*) FROM employees WHERE status='active' "
                "AND clearance_level IS NOT NULL AND clearance_level NOT IN ('none', '')"
            ).fetchone()[0]
            stats["available"] = conn.execute(
                "SELECT COUNT(*) FROM employees WHERE status='active' AND availability='available'"
            ).fetchone()[0]
        except Exception as e:
            logger.error("Error querying employee stats: %s", e)

        return render_template("gov_proposal/team.html",
                               employees=employees,
                               stats=stats,
                               company_type=company_type,
                               clearance=clearance,
                               availability=availability,
                               search=search)
    finally:
        conn.close()


@gov_proposal_bp.route("/team/<emp_id>")
def team_detail(emp_id):
    """ERP: Employee detail."""
    conn = _get_db()
    try:
        emp = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
        if not emp:
            flash("Employee not found", "error")
            return redirect(url_for("gov_proposal.team"))

        skills = conn.execute("""
            SELECT s.skill_name, s.category, es.proficiency, es.years_used
            FROM employee_skills es
            JOIN skills s ON es.skill_id = s.id
            WHERE es.employee_id = ?
            ORDER BY es.proficiency DESC, s.category
        """, (emp_id,)).fetchall()

        certs = conn.execute("""
            SELECT cert_name, issuing_body, cert_number, expiry_date, status
            FROM certifications WHERE employee_id = ?
            ORDER BY expiry_date ASC
        """, (emp_id,)).fetchall()

        lcat_history = conn.execute("""
            SELECT l.lcat_code, l.lcat_name, el.effective_date, el.end_date,
                   el.is_primary, lr.direct_labor_rate, lr.wrap_rate
            FROM employee_lcats el
            JOIN lcats l ON el.lcat_id = l.id
            LEFT JOIN lcat_rates lr ON lr.lcat_id = l.id
                AND lr.effective_date = (
                    SELECT MAX(effective_date) FROM lcat_rates
                    WHERE lcat_id = l.id AND effective_date <= date('now')
                )
            WHERE el.employee_id = ?
            ORDER BY el.is_primary DESC, el.effective_date DESC
        """, (emp_id,)).fetchall()

        # FPDS benchmark suggestions
        title_word = (emp["job_title"] or "").split()[0] if emp["job_title"] else ""
        benchmarks = []
        if title_word:
            try:
                benchmarks = conn.execute("""
                    SELECT labor_category, average_rate, median_rate, percentile_75, agency
                    FROM pricing_benchmarks WHERE labor_category LIKE ?
                    ORDER BY data_period DESC LIMIT 5
                """, (f"%{title_word}%",)).fetchall()
            except Exception as e:
                logger.error("Error querying FPDS pricing benchmarks for employee: %s", e)

        return render_template("gov_proposal/team_detail.html",
                               employee=emp,
                               skills=skills,
                               certifications=certs,
                               lcat_history=lcat_history,
                               benchmarks=benchmarks)
    finally:
        conn.close()


@gov_proposal_bp.route("/lcat-rates")
def lcat_rates():
    """ERP: LCAT rate cards."""
    conn = _get_db()
    try:
        lcats = conn.execute("""
            SELECT l.id, l.lcat_code, l.lcat_name, l.naics_code,
                   l.min_experience_years,
                   lr.direct_labor_rate, lr.fringe_rate, lr.overhead_rate,
                   lr.ga_rate, lr.fee_rate, lr.wrap_rate, lr.effective_date,
                   COUNT(DISTINCT el.employee_id) AS employee_count
            FROM lcats l
            LEFT JOIN lcat_rates lr ON lr.lcat_id = l.id
                AND lr.effective_date = (
                    SELECT MAX(effective_date) FROM lcat_rates
                    WHERE lcat_id = l.id AND effective_date <= date('now')
                )
            LEFT JOIN employee_lcats el ON el.lcat_id = l.id AND el.end_date IS NULL
            GROUP BY l.id
            ORDER BY l.lcat_code
        """).fetchall()

        # FPDS benchmarks for comparison
        benchmarks = []
        try:
            benchmarks = conn.execute("""
                SELECT labor_category, average_rate, median_rate,
                       percentile_25, percentile_75, agency, naics_code
                FROM pricing_benchmarks
                ORDER BY data_period DESC, labor_category
                LIMIT 50
            """).fetchall()
        except Exception as e:
            logger.error("Error querying FPDS pricing benchmarks for LCAT rates page: %s", e)

        return render_template("gov_proposal/lcat_rates.html", lcats=lcats, benchmarks=benchmarks)
    finally:
        conn.close()


@gov_proposal_bp.route("/capabilities")
def capabilities():
    """ERP: Company core capabilities."""
    conn = _get_db()
    try:
        caps = conn.execute("""
            SELECT capability_name, category, description,
                   employee_count, proficiency_avg, proposal_count, last_used
            FROM capabilities WHERE is_active = 1
            ORDER BY employee_count DESC, category
        """).fetchall()

        by_category = {}
        for cap in caps:
            cat = cap["category"] or "other"
            by_category.setdefault(cat, []).append(dict(cap))

        # Clearance stats
        clearance_summary = {}
        try:
            rows = conn.execute("""
                SELECT clearance_level, COUNT(*) as cnt
                FROM employees WHERE status='active'
                  AND clearance_level IS NOT NULL AND clearance_level NOT IN ('none', '')
                GROUP BY clearance_level
                ORDER BY CASE clearance_level
                    WHEN 'ts_sci_poly' THEN 0 WHEN 'ts_sci' THEN 1
                    WHEN 'top_secret' THEN 2 WHEN 'secret' THEN 3 ELSE 4 END
            """).fetchall()
            clearance_summary = {r["clearance_level"]: r["cnt"] for r in rows}
        except Exception as e:
            logger.error("Error querying employee clearance summary: %s", e)

        return render_template("gov_proposal/capabilities.html",
                               capabilities=caps,
                               by_category=by_category,
                               clearance_summary=clearance_summary)
    finally:
        conn.close()


# =========================================================================
# CRM ROUTES — Contacts, Interactions, Pipeline
# =========================================================================

@gov_proposal_bp.route("/crm")
def crm():
    """CRM: Contact list."""
    conn = _get_db()
    try:
        rel_filter = request.args.get("rel")
        sector_filter = request.args.get("sector")
        search = request.args.get("search", "").strip()

        query = """
            SELECT c.id, c.full_name, c.title, c.company, c.email,
                   c.sector, c.agency, c.status, c.last_contact_date,
                   rt.type_name AS relationship_type, rt.color_code,
                   COUNT(DISTINCT i.id) AS interaction_count,
                   COUNT(DISTINCT pc.opportunity_id) AS opp_count
            FROM contacts c
            LEFT JOIN relationship_types rt ON c.relationship_type_id = rt.id
            LEFT JOIN interactions i ON i.contact_id = c.id
            LEFT JOIN pipeline_contacts pc ON pc.contact_id = c.id
            WHERE c.status = 'active'
        """
        params = []
        if rel_filter:
            query += " AND rt.type_name = ?"
            params.append(rel_filter)
        if sector_filter:
            query += " AND c.sector = ?"
            params.append(sector_filter)
        if search:
            query += " AND (c.full_name LIKE ? OR c.company LIKE ? OR c.email LIKE ?)"
            params.extend([f"%{search}%"] * 3)
        query += " GROUP BY c.id ORDER BY c.full_name LIMIT 200"

        contacts = conn.execute(query, params).fetchall()

        # Relationship types for filter dropdown
        rel_types = conn.execute(
            "SELECT type_name, color_code FROM relationship_types ORDER BY type_name"
        ).fetchall()

        # Stats
        stats = {}
        try:
            for rt in rel_types:
                count = conn.execute(
                    "SELECT COUNT(*) FROM contacts c "
                    "JOIN relationship_types rt ON c.relationship_type_id = rt.id "
                    "WHERE c.status='active' AND rt.type_name=?",
                    (rt["type_name"],)
                ).fetchone()[0]
                stats[rt["type_name"]] = count
        except Exception as e:
            logger.error("Error querying CRM contact stats by relationship type: %s", e)

        # Pending actions (interactions with upcoming next_action_date)
        pending = []
        try:
            pending = conn.execute("""
                SELECT i.contact_id, c.full_name, i.next_action, i.next_action_date
                FROM interactions i
                JOIN contacts c ON i.contact_id = c.id
                WHERE i.next_action IS NOT NULL
                  AND i.next_action_date IS NOT NULL
                  AND i.next_action_date <= date('now', '+14 days')
                  AND i.next_action_date >= date('now', '-1 day')
                ORDER BY i.next_action_date
                LIMIT 10
            """).fetchall()
        except Exception as e:
            logger.error("Error querying pending CRM interaction actions: %s", e)

        sectors = []
        try:
            sectors = [r[0] for r in conn.execute(
                "SELECT DISTINCT sector FROM contacts WHERE sector IS NOT NULL ORDER BY sector"
            ).fetchall()]
        except Exception as e:
            logger.error("Error querying distinct contact sectors for filter: %s", e)

        return render_template("gov_proposal/crm.html",
                               contacts=contacts,
                               rel_types=rel_types,
                               stats=stats,
                               pending_actions=pending,
                               sectors=sectors,
                               rel_filter=rel_filter,
                               sector_filter=sector_filter,
                               search=search)
    finally:
        conn.close()


@gov_proposal_bp.route("/crm/new", methods=["GET", "POST"])
def crm_new():
    """CRM: Add new contact."""
    conn = _get_db()
    try:
        rel_types = conn.execute(
            "SELECT id, type_name, color_code FROM relationship_types ORDER BY type_name"
        ).fetchall()

        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            if not full_name:
                flash("Name is required", "error")
                return render_template("gov_proposal/crm_form.html", rel_types=rel_types,
                                       contact=request.form)

            cid = str(uuid.uuid4())
            now = _now()
            # Resolve relationship type name → id
            rt_name = request.form.get("relationship_type") or None
            rt_id = None
            if rt_name:
                rt_row = conn.execute(
                    "SELECT id FROM relationship_types WHERE type_name = ?", (rt_name,)
                ).fetchone()
                rt_id = rt_row["id"] if rt_row else None
            conn.execute("""
                INSERT INTO contacts (
                    id, full_name, title, email, phone, company,
                    relationship_type_id, sector, agency, sub_agency,
                    notes, linkedin_url, sam_entity_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (cid,
                  full_name,
                  request.form.get("title") or None,
                  request.form.get("email") or None,
                  request.form.get("phone") or None,
                  request.form.get("company") or None,
                  rt_id,
                  request.form.get("sector") or None,
                  request.form.get("agency") or None,
                  request.form.get("sub_agency") or None,
                  request.form.get("notes") or None,
                  request.form.get("linkedin_url") or None,
                  request.form.get("sam_entity_id") or None,
                  now, now))
            conn.commit()
            flash(f"Contact '{full_name}' added", "success")
            return redirect(url_for("gov_proposal.crm_detail", contact_id=cid))

        return render_template("gov_proposal/crm_form.html", rel_types=rel_types, contact={})
    finally:
        conn.close()


@gov_proposal_bp.route("/crm/<contact_id>")
def crm_detail(contact_id):
    """CRM: Contact detail."""
    conn = _get_db()
    try:
        contact = conn.execute("""
            SELECT c.*, rt.type_name AS relationship_type, rt.color_code
            FROM contacts c
            LEFT JOIN relationship_types rt ON c.relationship_type_id = rt.id
            WHERE c.id = ?
        """, (contact_id,)).fetchone()
        if not contact:
            flash("Contact not found", "error")
            return redirect(url_for("gov_proposal.crm"))

        interactions = conn.execute("""
            SELECT * FROM interactions
            WHERE contact_id = ?
            ORDER BY interaction_date DESC, created_at DESC
            LIMIT 50
        """, (contact_id,)).fetchall()

        linked_opps = conn.execute("""
            SELECT o.id, o.title, o.agency, o.status, o.response_deadline,
                   pc.role, pc.influence_level
            FROM pipeline_contacts pc
            JOIN opportunities o ON pc.opportunity_id = o.id
            WHERE pc.contact_id = ?
            ORDER BY o.response_deadline DESC
        """, (contact_id,)).fetchall()

        rel_types = conn.execute(
            "SELECT id, type_name FROM relationship_types ORDER BY type_name"
        ).fetchall()

        return render_template("gov_proposal/crm_detail.html",
                               contact=contact,
                               interactions=interactions,
                               linked_opps=linked_opps,
                               rel_types=rel_types)
    finally:
        conn.close()


@gov_proposal_bp.route("/crm/<contact_id>/log", methods=["POST"])
def crm_log_interaction(contact_id):
    """CRM: Log interaction for a contact."""
    conn = _get_db()
    try:
        notes = request.form.get("notes", "").strip()
        if not notes:
            flash("Notes are required", "error")
            return redirect(url_for("gov_proposal.crm_detail", contact_id=contact_id))

        iid = str(uuid.uuid4())
        now = _now()
        conn.execute("""
            INSERT INTO interactions (
                id, contact_id, opportunity_id, interaction_date,
                interaction_type, subject, notes, outcome,
                next_action, next_action_date, logged_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (iid, contact_id,
              request.form.get("opportunity_id") or None,
              now[:10],
              request.form.get("interaction_type", "note"),
              request.form.get("subject") or None,
              notes,
              request.form.get("outcome") or None,
              request.form.get("next_action") or None,
              request.form.get("next_action_date") or None,
              request.form.get("logged_by") or None,
              now))
        conn.execute(
            "UPDATE contacts SET last_contact_date=?, updated_at=? WHERE id=?",
            (now[:10], now, contact_id)
        )
        conn.commit()
        flash("Interaction logged", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    finally:
        conn.close()
    return redirect(url_for("gov_proposal.crm_detail", contact_id=contact_id))


# =========================================================================
# ERP/CRM API ENDPOINTS
# =========================================================================

@gov_proposal_bp.route("/api/erp/stats")
def api_erp_stats():
    conn = _get_db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM employees WHERE status='active'"
        ).fetchone()[0]
        cleared = conn.execute(
            "SELECT COUNT(*) FROM employees WHERE status='active' "
            "AND clearance_level IS NOT NULL AND clearance_level NOT IN ('none', '')"
        ).fetchone()[0]
        available = conn.execute(
            "SELECT COUNT(*) FROM employees WHERE status='active' "
            "AND availability='available'"
        ).fetchone()[0]
        return jsonify({"total": total, "cleared": cleared, "available": available})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@gov_proposal_bp.route("/api/crm/stats")
def api_crm_stats():
    conn = _get_db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE status='active'"
        ).fetchone()[0]
        by_type = {r[0] or "unknown": r[1] for r in conn.execute("""
            SELECT rt.type_name, COUNT(c.id)
            FROM contacts c
            LEFT JOIN relationship_types rt ON c.relationship_type_id = rt.id
            WHERE c.status='active' GROUP BY rt.type_name
        """).fetchall()}
        return jsonify({"total": total, "by_type": by_type})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# =========================================================================
# PRICING ROUTES — Package Catalog, Calculator, Scenarios
# =========================================================================

_SL_LABELS = {
    "cloud_infra":   "Cloud Infrastructure",
    "cyber_pentest": "Cyber / Pentest",
    "ai_ml_ops":     "AI/ML Operations",
    "help_desk":     "Help Desk",
}
_TIER_ORDER = {"bronze": 0, "silver": 1, "gold": 2}


def _pricing_calc(dlr_hours, indirect, fee_rate, odc_base):
    """DCAA-compliant wrap-rate pricing calculation.

    Returns dict with every line item.
    dlr_hours: list of (rate, hours) tuples.
    """
    dlc = sum(r * h for r, h in dlr_hours)
    total_hours = sum(h for _, h in dlr_hours)
    fringe_cost   = dlc * indirect["fringe_rate"]
    lf            = dlc * (1 + indirect["fringe_rate"])          # labor + fringe
    overhead_cost = lf * indirect["overhead_rate"]
    cost_pool     = lf * (1 + indirect["overhead_rate"])         # after OH
    ga_cost       = cost_pool * indirect["ga_rate"]
    cost_before_fee = cost_pool * (1 + indirect["ga_rate"])
    fee_amount      = cost_before_fee * fee_rate
    odc_total       = odc_base * (1 + indirect["odc_markup"])
    total_price     = cost_before_fee + fee_amount + odc_total
    breakeven       = cost_before_fee + odc_total
    margin_pct      = (fee_amount / total_price * 100) if total_price else 0
    wrap_rate       = (total_price - odc_total) / dlc if dlc else 0
    return {
        "total_hours":        total_hours,
        "direct_labor_cost":  dlc,
        "fringe_cost":        fringe_cost,
        "overhead_cost":      overhead_cost,
        "ga_cost":            ga_cost,
        "cost_before_fee":    cost_before_fee,
        "fee_amount":         fee_amount,
        "odc_cost":           odc_total,
        "total_price":        total_price,
        "breakeven_price":    breakeven,
        "margin_amount":      fee_amount,
        "margin_pct":         margin_pct,
        "wrap_rate":          wrap_rate,
    }


@gov_proposal_bp.route("/api/opportunities/<opp_id>/score", methods=["POST"])
def api_score_opportunity(opp_id):
    """Trigger qualification scoring for an opportunity (replaces CLI command)."""
    import logging as _log
    try:
        from tools.monitor.opportunity_scorer import score_fit, score_qualification
        score_fit(opp_id)
        result = score_qualification(opp_id)
        return jsonify({"ok": True, "score": result.get("overall_score"), "decision": result.get("decision")})
    except Exception as e:
        _log.getLogger(__name__).error("Opportunity scoring failed for %s: %s", opp_id, e)
        return jsonify({"error": "Scoring temporarily unavailable. Please try again."}), 503


@gov_proposal_bp.route("/api/capabilities/refresh", methods=["POST"])
def api_refresh_capabilities():
    """Aggregate capabilities from employee skills (replaces CLI command)."""
    import logging as _log
    try:
        from tools.erp.skills_tracker import aggregate_capabilities
        count = aggregate_capabilities()
        return jsonify({"ok": True, "capabilities_updated": count})
    except Exception as e:
        _log.getLogger(__name__).error("Capability refresh failed: %s", e)
        return jsonify({"error": "Capability refresh temporarily unavailable. Please try again."}), 503


@gov_proposal_bp.route("/pricing")
def pricing():
    """Pricing calculator — package catalog + interactive rate builder."""
    conn = _get_db()
    try:
        # Load packages grouped by service line then tier
        pkg_rows = conn.execute("""
            SELECT id, service_line, tier, name, description, period,
                   labor_mix, odc_base, market_low, market_high, notes
            FROM service_packages
            ORDER BY service_line,
                     CASE tier WHEN 'bronze' THEN 0 WHEN 'silver' THEN 1 ELSE 2 END
        """).fetchall()

        # Labor rates dict for JS calculator
        lab_rows = conn.execute(
            "SELECT lcat_code, lcat_name, base_rate, discipline FROM pricing_labor"
        ).fetchall()
        labor_rates = [dict(r) for r in lab_rows]

        # Active indirect rate
        ir = conn.execute(
            "SELECT * FROM indirect_rates WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        # Build package catalog with pre-calculated prices
        packages = {}
        for sl in _SL_LABELS:
            packages[sl] = []

        lrate_map = {r["lcat_code"]: r["base_rate"] for r in lab_rows}

        for pkg in pkg_rows:
            labor_mix = _safe_json(pkg["labor_mix"]) or []
            dlr_pairs = [(lrate_map.get(lm.get("lcat_code", ""), 0),
                          lm.get("hours", 0)) for lm in labor_mix]

            if ir:
                ir_dict = dict(ir)
                tm_calc  = _pricing_calc(dlr_pairs, ir_dict, ir_dict["fee_tm"],  pkg["odc_base"])
                ffp_calc = _pricing_calc(dlr_pairs, ir_dict, ir_dict["fee_ffp"], pkg["odc_base"])
            else:
                tm_calc = ffp_calc = {k: 0 for k in
                    ["total_hours", "direct_labor_cost", "fringe_cost", "overhead_cost",
                     "ga_cost", "cost_before_fee", "fee_amount", "odc_cost",
                     "total_price", "breakeven_price", "margin_amount", "margin_pct", "wrap_rate"]}

            packages[pkg["service_line"]].append({
                "id": pkg["id"],
                "tier": pkg["tier"],
                "name": pkg["name"],
                "description": pkg["description"],
                "period": pkg["period"],
                "labor_mix": labor_mix,
                "odc_base": pkg["odc_base"],
                "market_low": pkg["market_low"],
                "market_high": pkg["market_high"],
                "notes": pkg["notes"],
                "tm":  tm_calc,
                "ffp": ffp_calc,
            })

        # Recent scenarios count
        scenario_count = 0
        try:
            scenario_count = conn.execute(
                "SELECT COUNT(*) FROM pricing_scenarios"
            ).fetchone()[0]
        except Exception as e:
            logger.error("Error querying pricing scenario count: %s", e)

        return render_template(
            "pricing.html",
            packages=packages,
            sl_labels=_SL_LABELS,
            indirect=dict(ir) if ir else {},
            labor_rates=labor_rates,
            scenario_count=scenario_count,
        )
    finally:
        conn.close()


@gov_proposal_bp.route("/pricing/scenarios")
def pricing_scenarios():
    """List of saved pricing scenarios."""
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT ps.*, ir.name AS rate_name,
                   ir.fringe_rate, ir.overhead_rate, ir.ga_rate,
                   ir.fee_tm, ir.fee_ffp, ir.odc_markup
            FROM pricing_scenarios ps
            JOIN indirect_rates ir ON ps.indirect_rate_id = ir.id
            ORDER BY ps.created_at DESC
        """).fetchall()
        scenarios = [dict(r) for r in rows]
        for s in scenarios:
            s["sl_label"] = _SL_LABELS.get(s["service_line"], s["service_line"])
        return render_template(
            "pricing_scenarios.html",
            scenarios=scenarios,
            sl_labels=_SL_LABELS,
        )
    finally:
        conn.close()


@gov_proposal_bp.route("/api/pricing/calculate", methods=["POST"])
def api_pricing_calculate():
    """Compute pricing given labor mix + indirect rates. Returns JSON."""
    data = request.get_json(silent=True) or {}
    conn = _get_db()
    try:
        ir = conn.execute(
            "SELECT * FROM indirect_rates WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not ir:
            return jsonify({"error": "No active indirect rate configured."}), 400

        ir_dict = dict(ir)
        contract_type = data.get("contract_type", "ffp")
        fee_rate = ir_dict["fee_ffp"] if contract_type == "ffp" else ir_dict["fee_tm"]

        labor_lines = data.get("labor_lines", [])  # [{lcat_code, hours}]
        odc_base = float(data.get("odc_base", 0))

        lab_rows = conn.execute("SELECT lcat_code, base_rate FROM pricing_labor").fetchall()
        lrate_map = {r["lcat_code"]: r["base_rate"] for r in lab_rows}

        dlr_pairs = [
            (lrate_map.get(ll.get("lcat_code", ""), float(ll.get("custom_rate", 0))),
             float(ll.get("hours", 0)))
            for ll in labor_lines
        ]

        result = _pricing_calc(dlr_pairs, ir_dict, fee_rate, odc_base)
        result["contract_type"] = contract_type
        result["indirect_rate"] = {
            "fringe":  ir_dict["fringe_rate"],
            "oh":      ir_dict["overhead_rate"],
            "ga":      ir_dict["ga_rate"],
            "fee":     fee_rate,
            "odc_mk":  ir_dict["odc_markup"],
        }
        # Format all monetary values for display
        for k in list(result):
            if isinstance(result[k], float) and k not in ("margin_pct", "wrap_rate"):
                result[f"{k}_fmt"] = f"${result[k]:,.2f}"
        result["margin_pct_fmt"] = f"{result['margin_pct']:.1f}%"
        result["wrap_rate_fmt"]  = f"{result['wrap_rate']:.2f}x"
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@gov_proposal_bp.route("/api/pricing/save", methods=["POST"])
def api_pricing_save():
    """Save a pricing scenario from the calculator."""
    data = request.get_json(silent=True) or {}
    conn = _get_db()
    try:
        ir = conn.execute(
            "SELECT * FROM indirect_rates WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not ir:
            return jsonify({"error": "No active indirect rate."}), 400

        import uuid
        now = _now()
        sid = str(uuid.uuid4())
        ir_dict = dict(ir)
        contract_type = data.get("contract_type", "ffp")
        fee_rate = ir_dict["fee_ffp"] if contract_type == "ffp" else ir_dict["fee_tm"]

        labor_lines = data.get("labor_lines", [])
        odc_base = float(data.get("odc_base", 0))
        lab_rows = conn.execute("SELECT lcat_code, base_rate FROM pricing_labor").fetchall()
        lrate_map = {r["lcat_code"]: r["base_rate"] for r in lab_rows}
        dlr_pairs = [
            (lrate_map.get(ll.get("lcat_code", ""), float(ll.get("custom_rate", 0))),
             float(ll.get("hours", 0)))
            for ll in labor_lines
        ]
        calc = _pricing_calc(dlr_pairs, ir_dict, fee_rate, odc_base)

        conn.execute("""
            INSERT INTO pricing_scenarios
                (id, name, package_id, indirect_rate_id,
                 service_line, tier, period, contract_type,
                 labor_hours, direct_labor_cost, fringe_cost, overhead_cost,
                 ga_cost, total_cost_before_fee, fee_amount, odc_cost,
                 total_price, breakeven_price, margin_amount, margin_pct,
                 notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sid,
            data.get("name") or "Untitled Scenario",
            data.get("package_id"),
            ir["id"],
            data.get("service_line", ""),
            data.get("tier", ""),
            data.get("period", "monthly"),
            contract_type,
            calc["total_hours"],
            calc["direct_labor_cost"],
            calc["fringe_cost"],
            calc["overhead_cost"],
            calc["ga_cost"],
            calc["cost_before_fee"],
            calc["fee_amount"],
            calc["odc_cost"],
            calc["total_price"],
            calc["breakeven_price"],
            calc["margin_amount"],
            calc["margin_pct"],
            data.get("notes", ""),
            now, now,
        ))
        conn.commit()
        return jsonify({"ok": True, "id": sid, "total_price": calc["total_price"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@gov_proposal_bp.route("/api/pricing/stats")
def api_pricing_stats():
    conn = _get_db()
    try:
        sc = conn.execute("SELECT COUNT(*) FROM pricing_scenarios").fetchone()[0]
        avg_margin = conn.execute(
            "SELECT AVG(margin_pct) FROM pricing_scenarios"
        ).fetchone()[0] or 0
        total_pipeline = conn.execute(
            "SELECT SUM(total_price) FROM pricing_scenarios"
        ).fetchone()[0] or 0
        return jsonify({
            "scenarios": sc,
            "avg_margin_pct": round(avg_margin, 1),
            "total_pipeline": total_pipeline,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# =========================================================================
# RFX AI PROPOSAL ENGINE
# =========================================================================
# Lazy imports — services load on first request so the dashboard starts
# even if sentence-transformers or unsloth are not yet installed.

def _rfx_doc_proc():
    from tools.rfx.document_processor import (
        store_upload, list_documents, get_document, delete_document
    )
    return store_upload, list_documents, get_document, delete_document

def _rfx_rag():
    from tools.rfx.rag_service import vectorize_document, search_all
    return vectorize_document, search_all

def _rfx_req():
    from tools.rfx.requirement_extractor import (
        extract_and_store, get_requirements, get_compliance_status,
        update_requirement_status
    )
    return extract_and_store, get_requirements, get_compliance_status, update_requirement_status

def _rfx_excl():
    from tools.rfx.exclusion_service import add_term, list_terms, remove_term
    return add_term, list_terms, remove_term

def _rfx_research():
    from tools.rfx.research_service import deep_research, get_cached_research
    return deep_research, get_cached_research

def _rfx_llm():
    from tools.rfx.llm_bridge import generate_section, score_section
    return generate_section, score_section


# ── pages ─────────────────────────────────────────────────────────────────────

@gov_proposal_bp.route("/ai-proposals")
def ai_proposals():
    """AI Proposal Dashboard — all proposals with their AI section status."""
    conn = _get_db()
    try:
        proposals = conn.execute("""
            SELECT p.id, p.title, p.status, p.due_date,
                   p.assigned_pm, p.created_at,
                   o.title as opp_title, o.agency,
                   COUNT(s.id) as section_count,
                   SUM(CASE WHEN s.hitl_status='accepted' THEN 1 ELSE 0 END) as accepted_count,
                   SUM(CASE WHEN s.hitl_status='pending' THEN 1 ELSE 0 END) as pending_count
            FROM proposals p
            LEFT JOIN opportunities o ON p.opportunity_id = o.id
            LEFT JOIN rfx_ai_sections s ON s.proposal_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """).fetchall()
        return render_template("gov_proposal/rfx/ai_proposals.html",
                               proposals=[dict(r) for r in proposals])
    finally:
        conn.close()


@gov_proposal_bp.route("/ai-proposals/<proposal_id>")
def ai_proposal_detail(proposal_id):
    """AI Proposal detail: sections list + HITL review panel."""
    conn = _get_db()
    try:
        proposal = conn.execute(
            "SELECT p.*, o.title as opp_title, o.agency, o.response_deadline "
            "FROM proposals p "
            "LEFT JOIN opportunities o ON p.opportunity_id = o.id "
            "WHERE p.id = ?", (proposal_id,)
        ).fetchone()
        if not proposal:
            flash("Proposal not found.", "error")
            return redirect(url_for("gov_proposal.ai_proposals"))

        sections = conn.execute(
            "SELECT * FROM rfx_ai_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number", (proposal_id,)
        ).fetchall()

        docs = conn.execute(
            "SELECT id, filename, doc_type, vectorized, chunk_count, created_at "
            "FROM rfx_documents WHERE proposal_id = ? ORDER BY created_at",
            (proposal_id,)
        ).fetchall()

        req_status = conn.execute("""
            SELECT status, COUNT(*) as cnt
            FROM rfx_requirement_status WHERE proposal_id = ?
            GROUP BY status
        """, (proposal_id,)).fetchall()

        pricing = conn.execute(
            "SELECT id, name, total_price, margin_pct, contract_type, period "
            "FROM pricing_scenarios ORDER BY created_at DESC LIMIT 10"
        ).fetchall()

        win_themes = conn.execute(
            "SELECT theme_text FROM win_themes WHERE proposal_id = ?",
            (proposal_id,)
        ).fetchall()

        return render_template(
            "rfx/ai_proposal_detail.html",
            proposal=dict(proposal),
            sections=[dict(s) for s in sections],
            docs=[dict(d) for d in docs],
            req_status={r["status"]: r["cnt"] for r in req_status},
            pricing_scenarios=[dict(p) for p in pricing],
            win_themes=[w["theme_text"] for w in win_themes],
        )
    finally:
        conn.close()


@gov_proposal_bp.route("/rfx/documents")
def rfx_documents():
    """Document upload and management page."""
    _, list_documents, _, _ = _rfx_doc_proc()
    docs = list_documents()
    return render_template("gov_proposal/rfx/documents.html", documents=docs)


@gov_proposal_bp.route("/rfx/requirements")
def rfx_requirements():
    """Requirements view with compliance status per proposal."""
    _, get_requirements, get_compliance_status, _ = _rfx_req()
    proposal_id = request.args.get("proposal_id")
    requirements = get_requirements(proposal_id=proposal_id)
    compliance = get_compliance_status(proposal_id) if proposal_id else {}

    conn = _get_db()
    try:
        proposals = conn.execute(
            "SELECT id, title FROM proposals ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()

    return render_template("gov_proposal/rfx/requirements.html",
                           requirements=requirements,
                           compliance=compliance,
                           proposals=[dict(p) for p in proposals],
                           selected_proposal=proposal_id)


@gov_proposal_bp.route("/rfx/exclusions")
def rfx_exclusions():
    """Sensitive term exclusion list management."""
    _, list_terms, _ = _rfx_excl()
    terms = list_terms()
    by_type: dict = {}
    for t in terms:
        by_type.setdefault(t["term_type"], []).append(t)
    return render_template("gov_proposal/rfx/exclusions.html", terms=terms, by_type=by_type)


@gov_proposal_bp.route("/rfx/research")
def rfx_research():
    """Web and government source research panel."""
    proposal_id = request.args.get("proposal_id")
    _, get_cached_research = _rfx_research()
    cached = get_cached_research(proposal_id) if proposal_id else []

    conn = _get_db()
    try:
        proposals = conn.execute(
            "SELECT id, title FROM proposals ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()

    return render_template("gov_proposal/rfx/research.html",
                           cached_research=cached,
                           proposals=[dict(p) for p in proposals],
                           selected_proposal=proposal_id)


@gov_proposal_bp.route("/rfx/fine-tuning")
def rfx_fine_tuning():
    """Fine-tuning job dashboard."""
    conn = _get_db()
    try:
        jobs = conn.execute(
            "SELECT j.*, m.model_name as config_name "
            "FROM rfx_finetune_jobs j "
            "LEFT JOIN rfx_model_config m ON j.model_config_id = m.id "
            "ORDER BY j.created_at DESC"
        ).fetchall()
        models = conn.execute(
            "SELECT * FROM rfx_model_config ORDER BY priority, created_at"
        ).fetchall()
        docs = conn.execute(
            "SELECT id, filename, doc_type FROM rfx_documents "
            "WHERE exclude_from_training = 0 ORDER BY filename"
        ).fetchall()
    finally:
        conn.close()

    return render_template("gov_proposal/rfx/fine_tuning.html",
                           jobs=[dict(j) for j in jobs],
                           models=[dict(m) for m in models],
                           corpus_docs=[dict(d) for d in docs])


# ── API: documents ─────────────────────────────────────────────────────────────

@gov_proposal_bp.route("/api/rfx/documents/upload", methods=["POST"])
def api_rfx_upload():
    """Upload a document (RFI/RFP or corpus). Multipart form-data."""
    import tempfile
    from pathlib import Path as P

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    doc_type = request.form.get("doc_type", "other")
    proposal_id = request.form.get("proposal_id") or None
    opportunity_id = request.form.get("opportunity_id") or None
    notes = request.form.get("notes") or None

    store_upload, _, _, _ = _rfx_doc_proc()

    # Save to temp file then hand to processor
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=P(f.filename).suffix
    ) as tmp:
        f.save(tmp.name)
        tmp_path = P(tmp.name)

    try:
        result = store_upload(
            src_path=tmp_path,
            doc_type=doc_type,
            proposal_id=proposal_id,
            opportunity_id=opportunity_id,
            notes=notes,
        )
        # Rename to use original filename in result
        result["filename"] = f.filename
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return jsonify(result), 200 if result.get("status") != "duplicate" else 409


@gov_proposal_bp.route("/api/rfx/documents/<doc_id>/vectorize", methods=["POST"])
def api_rfx_vectorize(doc_id):
    """Trigger embedding generation for a document's chunks."""
    vectorize_document, _ = _rfx_rag()
    result = vectorize_document(doc_id)
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)


@gov_proposal_bp.route("/api/rfx/documents/<doc_id>", methods=["DELETE"])
def api_rfx_delete_doc(doc_id):
    """Delete a document and its chunks."""
    _, _, _, delete_document = _rfx_doc_proc()
    ok = delete_document(doc_id)
    return jsonify({"deleted": ok}), 200 if ok else 404


# ── API: requirements ──────────────────────────────────────────────────────────

@gov_proposal_bp.route("/api/rfx/requirements/extract", methods=["POST"])
def api_rfx_extract_requirements():
    """Extract requirements from an uploaded document."""
    data = request.get_json(force=True) or {}
    doc_id = data.get("doc_id")
    proposal_id = data.get("proposal_id")

    if not doc_id:
        return jsonify({"error": "doc_id required"}), 400

    extract_and_store, _, _, _ = _rfx_req()
    result = extract_and_store(doc_id=doc_id, proposal_id=proposal_id)
    return jsonify(result)


@gov_proposal_bp.route("/api/rfx/requirements/<proposal_id>")
def api_rfx_get_requirements(proposal_id):
    """Return all requirements for a proposal with their address status."""
    _, get_requirements, get_compliance_status, _ = _rfx_req()
    reqs = get_requirements(proposal_id=proposal_id)
    status = get_compliance_status(proposal_id)
    return jsonify({"requirements": reqs, "compliance": status})


# ── API: exclusions ────────────────────────────────────────────────────────────

@gov_proposal_bp.route("/api/rfx/exclusions/add", methods=["POST"])
def api_rfx_add_exclusion():
    """Add a sensitive term to the exclusion list."""
    data = request.get_json(force=True) or {}
    term = data.get("sensitive_term", "").strip()
    if not term:
        return jsonify({"error": "sensitive_term required"}), 400

    add_term, _, _ = _rfx_excl()
    result = add_term(
        sensitive_term=term,
        term_type=data.get("term_type", "custom"),
        context_notes=data.get("context_notes"),
        case_sensitive=bool(data.get("case_sensitive", False)),
        whole_word=bool(data.get("whole_word", True)),
    )
    return jsonify(result)


@gov_proposal_bp.route("/api/rfx/exclusions/<entry_id>", methods=["DELETE"])
def api_rfx_remove_exclusion(entry_id):
    """Soft-delete an exclusion list entry."""
    _, _, remove_term = _rfx_excl()
    ok = remove_term(entry_id)
    return jsonify({"removed": ok}), 200 if ok else 404


# ── API: exclusions preview ────────────────────────────────────────────────────

@gov_proposal_bp.route("/api/rfx/exclusions/preview", methods=["POST"])
def api_rfx_preview_mask():
    """Preview masking without modifying the DB."""
    data = request.get_json(force=True) or {}
    text = data.get("text", "")
    _, list_terms, _ = _rfx_excl()
    from tools.rfx.exclusion_service import preview_mask
    result = preview_mask(text)
    return jsonify(result)


# ── API: research ──────────────────────────────────────────────────────────────

@gov_proposal_bp.route("/api/rfx/research", methods=["POST"])
def api_rfx_research():
    """Run deep research (web + SAM.gov + USASpending) for a query."""
    data = request.get_json(force=True, silent=True)
    if data is None:
        return jsonify({"error": "Request body must be valid JSON."}), 400
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required."}), 400
    if len(query) > 500:
        return jsonify({"error": "query must be 500 characters or fewer."}), 400

    deep_research, _ = _rfx_research()
    result = deep_research(query=query,
                           proposal_id=data.get("proposal_id"))

    # Optionally summarize with LLM
    if data.get("summarize", False):
        try:
            from tools.rfx.llm_bridge import summarize_research
            all_results = (result.get("sam_results") or []) + \
                          (result.get("spend_results") or []) + \
                          (result.get("web_results") or [])
            result["summary"] = summarize_research(
                all_results, query,
                proposal_id=data.get("proposal_id")
            )
        except Exception:
            result["summary"] = ""

    return jsonify(result)


# ── API: AI section generation ─────────────────────────────────────────────────

@gov_proposal_bp.route("/api/rfx/ai/generate", methods=["POST"])
def api_rfx_generate_section():
    """Generate an AI proposal section with RAG + LLM."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    data = request.get_json(force=True) or {}
    proposal_id = data.get("proposal_id")
    section_title = data.get("section_title", "").strip()
    volume = data.get("volume", "technical")
    pricing_scenario_id = data.get("pricing_scenario_id")

    if not proposal_id or not section_title:
        return jsonify({"error": "proposal_id and section_title required"}), 400

    # Pull RFP context from rfx_requirements
    _, get_requirements, _, _ = _rfx_req()
    reqs = get_requirements(proposal_id=proposal_id)
    rfp_context = "\n".join(r["req_text"] for r in reqs[:20])

    # RAG search
    _, search_all = _rfx_rag()
    search_results = search_all(
        query=f"{section_title} {volume}",
        top_k=6,
        proposal_id=proposal_id,
    )
    rag_chunks = search_results.get("combined", [])

    # Win themes
    conn = _get_db()
    try:
        wt_rows = conn.execute(
            "SELECT theme_text FROM win_themes WHERE proposal_id = ?",
            (proposal_id,)
        ).fetchall()
        win_themes = [r["theme_text"] for r in wt_rows]

        # Pricing context for cost volumes
        pricing_ctx = None
        if volume == "cost" and pricing_scenario_id:
            ps = conn.execute(
                "SELECT * FROM pricing_scenarios WHERE id = ?",
                (pricing_scenario_id,)
            ).fetchone()
            if ps:
                ps = dict(ps)
                pricing_ctx = (
                    f"Service: {ps.get('sl_label','')}, "
                    f"Tier: {ps.get('tier','')}, "
                    f"Type: {ps.get('contract_type','').upper()}, "
                    f"Hours: {ps.get('labor_hours',0):.0f}, "
                    f"Total Price: ${ps.get('total_price',0):,.2f}, "
                    f"Margin: {ps.get('margin_pct',0):.1f}%"
                )
    finally:
        conn.close()

    generate_section, _ = _rfx_llm()
    try:
        gen = generate_section(
            section_title=section_title,
            volume=volume,
            rfp_context=rfp_context,
            rag_chunks=rag_chunks,
            kb_entries=[c for c in rag_chunks if c.get("source") == "kb"],
            win_themes=win_themes,
            pricing_context=pricing_ctx,
            proposal_id=proposal_id,
        )
    except Exception as llm_err:
        import logging as _log
        _log.getLogger(__name__).error("AI generation failed: %s", llm_err, exc_info=True)
        return jsonify({
            "error": "AI generation is temporarily unavailable. Please try again or contact your system administrator.",
            "llm_unavailable": True,
        }), 503

    # Defensive: never persist error-like content to the database
    draft_content = gen.get("content_draft", "")
    if not draft_content or draft_content.startswith("[LLM error:") or "Traceback (" in draft_content:
        import logging as _log
        _log.getLogger(__name__).error("AI generation returned error-like content for proposal=%s", proposal_id)
        return jsonify({
            "error": "AI generation produced an invalid response. Please try again or contact your system administrator.",
            "llm_unavailable": True,
        }), 503

    # Persist to rfx_ai_sections
    now = _dt.now(_tz.utc).isoformat()
    section_id = str(_uuid.uuid4())
    conn = _get_db()
    try:
        conn.execute("""
            INSERT INTO rfx_ai_sections
                (id, proposal_id, volume, section_title, content_draft,
                 source_type, rag_sources, pricing_scenario_id,
                 prompt_hash, hitl_status, classification,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,'pending','CUI // SP-PROPIN',?,?)
        """, (
            section_id, proposal_id, volume, section_title,
            draft_content,
            gen.get("source_type", "ai"),
            json.dumps(gen.get("rag_sources", [])),
            pricing_scenario_id,
            gen.get("prompt_hash", ""),
            now, now,
        ))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"section_id": section_id, "status": "pending",
                    "section_title": section_title, **gen})


@gov_proposal_bp.route("/api/rfx/ai/sections/<section_id>/review", methods=["POST"])
def api_rfx_review_section(section_id):
    """Submit a HITL review action (accept / revise / reject)."""
    import uuid as _uuid
    import sqlite3
    from datetime import datetime as _dt, timezone as _tz

    data = request.get_json(force=True) or {}
    action = data.get("action", "").strip()
    if action not in ("accept", "revise", "reject", "flag"):
        return jsonify({"error": "action must be accept|revise|reject|flag"}), 400

    reviewer = data.get("reviewer", "reviewer")
    feedback = data.get("feedback", "")
    revised_content = data.get("revised_content", "")
    now = _dt.now(_tz.utc).isoformat()

    conn = _get_db()
    try:
        section = conn.execute(
            "SELECT * FROM rfx_ai_sections WHERE id = ?", (section_id,)
        ).fetchone()
        if not section:
            return jsonify({"error": "Section not found"}), 404

        section = dict(section)
        proposal_id = section["proposal_id"]

        # Validate content_draft exists for accept actions
        if action == "accept" and not section.get("content_draft"):
            return jsonify({"error": "Section has no draft content to accept"}), 400

        # Append-only HITL review record
        conn.execute("""
            INSERT INTO rfx_hitl_reviews
                (id, ai_section_id, proposal_id, reviewer, action,
                 feedback, revised_content, classification, reviewed_at)
            VALUES (?,?,?,?,?,?,?,'CUI // SP-PROPIN',?)
        """, (str(_uuid.uuid4()), section_id, proposal_id,
              reviewer, action, feedback, revised_content, now))

        # Update section status
        accepted_content = revised_content if action == "revise" else (
            section.get("content_draft") if action == "accept" else None
        )
        new_status = action + "ed" if action in ("accept", "reject") else "revised"
        conn.execute("""
            UPDATE rfx_ai_sections
            SET hitl_status = ?,
                hitl_feedback = ?,
                hitl_reviewed_by = ?,
                hitl_reviewed_at = ?,
                content_accepted = ?,
                revision_count = revision_count + ?,
                updated_at = ?
            WHERE id = ?
        """, (
            new_status, feedback, reviewer, now, accepted_content,
            1 if action == "revise" else 0,
            now, section_id,
        ))

        # If accepted, push content to proposal_sections
        if action in ("accept", "revise") and accepted_content:
            existing = conn.execute(
                "SELECT id FROM proposal_sections "
                "WHERE proposal_id = ? AND section_title = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (proposal_id, section["section_title"])
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE proposal_sections SET content = ?, status = 'drafted', "
                    "updated_at = ? WHERE id = ?",
                    (accepted_content, now, existing["id"])
                )
            else:
                conn.execute("""
                    INSERT INTO proposal_sections
                        (id, proposal_id, volume, section_number, section_title,
                         content, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,'drafted',?,?)
                """, (
                    str(_uuid.uuid4()), proposal_id,
                    section["volume"],
                    section.get("section_number") or "AI",
                    section["section_title"],
                    accepted_content, now, now,
                ))
            conn.execute(
                "UPDATE rfx_ai_sections SET proposal_section_id = "
                "(SELECT id FROM proposal_sections WHERE proposal_id=? AND section_title=? ORDER BY created_at DESC LIMIT 1) "
                "WHERE id=?",
                (proposal_id, section["section_title"], section_id)
            )

        conn.commit()
    except sqlite3.Error as db_err:
        conn.rollback()
        import logging as _log
        _log.getLogger(__name__).error("HITL review DB error: %s", db_err, exc_info=True)
        return jsonify({
            "error": "Unable to save your review. Please try again or contact your system administrator.",
            "detail": str(db_err),
        }), 500
    finally:
        conn.close()

    return jsonify({"section_id": section_id, "action": action, "status": "ok"})


# ── API: proposals (create from opportunity) ───────────────────────────────────

@gov_proposal_bp.route("/api/rfx/proposals/create", methods=["POST"])
def api_rfx_create_proposal():
    """Create a GovProposal proposal linked to an opportunity, ready for AI drafting."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    data = request.get_json(force=True) or {}
    opportunity_id = data.get("opportunity_id", "").strip()
    title = data.get("title", "").strip()
    if not opportunity_id or not title:
        return jsonify({"error": "opportunity_id and title required"}), 400

    now = _dt.now(_tz.utc).isoformat()
    prop_id = str(_uuid.uuid4())

    conn = _get_db()
    try:
        # Verify opportunity exists
        opp = conn.execute(
            "SELECT id, agency FROM opportunities WHERE id = ?",
            (opportunity_id,)
        ).fetchone()
        if not opp:
            return jsonify({"error": "Opportunity not found"}), 404

        conn.execute("""
            INSERT INTO proposals
                (id, opportunity_id, title, version, status,
                 classification, created_at, updated_at)
            VALUES (?,?,?,1,'draft','CUI // SP-PROPIN',?,?)
        """, (prop_id, opportunity_id, title, now, now))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"proposal_id": prop_id, "status": "created"})


# ── API: fine-tuning ───────────────────────────────────────────────────────────

@gov_proposal_bp.route("/api/rfx/finetune/start", methods=["POST"])
def api_rfx_finetune_start():
    """Start a fine-tuning job (deferred to finetune_runner subprocess)."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    data = request.get_json(force=True) or {}
    job_name = (data.get("job_name") or data.get("model_name", "")).strip()
    base_model = data.get("base_model", "llama3:8b")
    doc_ids = data.get("doc_ids", [])

    if not job_name:
        return jsonify({"error": "job_name required"}), 400

    now = _dt.now(_tz.utc).isoformat()
    job_id = str(_uuid.uuid4())

    conn = _get_db()
    try:
        conn.execute("""
            INSERT INTO rfx_finetune_jobs
                (id, job_name, model_name, base_model, backend, status,
                 model_config_id, training_doc_ids, training_doc_count,
                 lora_rank, lora_alpha, epochs, batch_size,
                 learning_rate, export_gguf, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            job_id, job_name, job_name, base_model,
            data.get("backend", "unsloth"), "queued",
            data.get("model_config_id") or None,
            json.dumps(doc_ids), len(doc_ids),
            data.get("lora_rank", 16), data.get("lora_alpha", 32),
            data.get("epochs", 3), data.get("batch_size", 4),
            str(data.get("learning_rate", "2e-4")),
            int(data.get("export_gguf", True)),
            now,
        ))
        conn.commit()
    finally:
        conn.close()

    # Launch finetune_runner in background thread (non-blocking)
    def _run_finetune():
        try:
            from tools.rfx.finetune_runner import launch_job
            launch_job(job_id)
        except ImportError:
            logger.info("finetune_runner not installed — job %s queued for manual pickup", job_id)
        except Exception as e:
            logger.error("Fine-tuning job %s failed: %s", job_id, e)

    threading.Thread(target=_run_finetune, daemon=True).start()

    return jsonify({"job_id": job_id, "status": "pending",
                    "message": "Fine-tuning job queued."})


@gov_proposal_bp.route("/api/rfx/finetune/status")
def api_rfx_finetune_status():
    """List all fine-tuning jobs with current status."""
    conn = _get_db()
    try:
        jobs = conn.execute(
            "SELECT id, model_name, base_model, backend, status, "
            "training_doc_count, progress_pct, error_message, "
            "started_at, completed_at, created_at "
            "FROM rfx_finetune_jobs ORDER BY created_at DESC"
        ).fetchall()
        return jsonify({"jobs": [dict(j) for j in jobs]})
    finally:
        conn.close()


# =========================================================================
# CONTRACT PERFORMANCE MANAGEMENT
# =========================================================================
@gov_proposal_bp.route("/contracts")
def contracts():
    """Contract list with CDRL/obligation summary."""
    conn = _get_db()
    try:
        status_filter = request.args.get("status", "")
        query = (
            "SELECT c.*, o.title as opp_title, o.agency, "
            "(SELECT COUNT(*) FROM contract_cdrls cc WHERE cc.contract_id = c.id) as cdrl_count, "
            "(SELECT COUNT(*) FROM contract_cdrls cc WHERE cc.contract_id = c.id "
            " AND cc.status IN ('delivered','accepted')) as cdrl_done, "
            "(SELECT COUNT(*) FROM contract_obligations co WHERE co.contract_id = c.id) as obl_count, "
            "(SELECT COUNT(*) FROM contract_obligations co WHERE co.contract_id = c.id "
            " AND co.status = 'compliant') as obl_done "
            "FROM contracts c "
            "LEFT JOIN opportunities o ON c.opportunity_id = o.id "
        )
        params = []
        if status_filter:
            query += "WHERE c.status = ? "
            params.append(status_filter)
        query += "ORDER BY c.created_at DESC"

        rows = conn.execute(query, params).fetchall()
        contract_list = [dict(r) for r in rows]

        # Stats
        active_count = sum(1 for c in contract_list if c["status"] == "active")
        total_cdrls = sum(c.get("cdrl_count", 0) for c in contract_list)
        overdue_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM contract_cdrls WHERE status = 'overdue'"
        ).fetchone()
        overdue_count = overdue_row["cnt"] if overdue_row else 0
        avg_row = conn.execute(
            "SELECT AVG(cpars_risk_score) as avg_risk FROM contracts WHERE status = 'active'"
        ).fetchone()
        avg_risk = round(avg_row["avg_risk"] or 0.0, 2)

        return render_template("gov_proposal/contracts.html",
                               contracts=contract_list,
                               active_count=active_count,
                               total_cdrls=total_cdrls,
                               overdue_count=overdue_count,
                               avg_risk=avg_risk,
                               status_filter=status_filter)
    except Exception as e:
        logger.error("Error loading contracts: %s", e)
        return render_template("gov_proposal/contracts.html",
                               contracts=[],
                               active_count=0, total_cdrls=0,
                               overdue_count=0, avg_risk=0.0,
                               status_filter="")
    finally:
        conn.close()


@gov_proposal_bp.route("/contracts/<contract_id>")
def contract_detail(contract_id):
    """Contract detail with CDRLs, obligations, reminders, timeline."""
    conn = _get_db()
    try:
        contract = conn.execute(
            "SELECT c.*, o.title as opp_title, o.agency "
            "FROM contracts c "
            "LEFT JOIN opportunities o ON c.opportunity_id = o.id "
            "WHERE c.id = ?",
            (contract_id,)
        ).fetchone()
        if not contract:
            flash("Contract not found.", "error")
            return redirect(url_for("gov_proposal.contracts"))

        cdrls = conn.execute(
            "SELECT * FROM contract_cdrls WHERE contract_id = ? ORDER BY cdrl_number",
            (contract_id,)
        ).fetchall()

        sow_obligations = conn.execute(
            "SELECT * FROM contract_obligations WHERE contract_id = ? "
            "AND obligation_type = 'sow' ORDER BY created_at",
            (contract_id,)
        ).fetchall()

        deliverables = conn.execute(
            "SELECT * FROM contract_obligations WHERE contract_id = ? "
            "AND obligation_type IN ('deliverable', 'milestone') ORDER BY due_date",
            (contract_id,)
        ).fetchall()

        reminders = conn.execute(
            "SELECT * FROM deliverable_reminders WHERE contract_id = ? "
            "AND status = 'pending' ORDER BY severity DESC, reminder_date",
            (contract_id,)
        ).fetchall()

        # Timeline: next 90 days of due dates
        timeline = []
        for c in cdrls:
            if c["next_due_date"] and c["status"] not in ("accepted", "delivered"):
                timeline.append({
                    "date": c["next_due_date"],
                    "type": "CDRL",
                    "title": f"CDRL {c['cdrl_number']}: {c['title'][:60]}",
                    "status": c["status"],
                })
        for o in list(sow_obligations) + list(deliverables):
            if o["due_date"] and o["status"] not in ("compliant", "waived"):
                timeline.append({
                    "date": o["due_date"],
                    "type": o["obligation_type"].upper(),
                    "title": o["obligation_text"][:80],
                    "status": o["status"],
                })
        timeline.sort(key=lambda x: x["date"])
        timeline = timeline[:30]  # Limit to next 30 items

        # Stats
        cdrl_total = len(cdrls)
        cdrl_delivered = sum(1 for c in cdrls if c["status"] in ("delivered", "accepted"))
        obl_total = len(sow_obligations) + len(deliverables)
        obl_compliant = sum(1 for o in list(sow_obligations) + list(deliverables)
                           if o["status"] == "compliant")

        # CPARS risk
        risk_score = contract["cpars_risk_score"] or 0.0
        if risk_score >= 0.8:
            risk_level = "critical"
        elif risk_score >= 0.5:
            risk_level = "high"
        elif risk_score >= 0.2:
            risk_level = "moderate"
        else:
            risk_level = "low"

        return render_template("gov_proposal/contract_detail.html",
                               contract=contract,
                               cdrls=cdrls,
                               sow_obligations=sow_obligations,
                               deliverables=deliverables,
                               reminders=reminders,
                               timeline=timeline,
                               cdrl_total=cdrl_total,
                               cdrl_delivered=cdrl_delivered,
                               obl_total=obl_total,
                               obl_compliant=obl_compliant,
                               risk_score=risk_score,
                               risk_level=risk_level)
    except Exception as e:
        logger.error("Error loading contract detail: %s", e)
        flash("Error loading contract detail.", "error")
        return redirect(url_for("gov_proposal.contracts"))
    finally:
        conn.close()


@gov_proposal_bp.route("/api/contracts/<contract_id>/cdrl/<cdrl_id>", methods=["PATCH"])
def api_update_cdrl(contract_id, cdrl_id):
    """Update CDRL status via API."""
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "status required"}), 400

    valid = ("not_due", "on_schedule", "at_risk", "delivered",
             "accepted", "rejected", "overdue")
    if new_status not in valid:
        return jsonify({"error": f"Invalid status. Must be one of: {valid}"}), 400

    conn = _get_db()
    try:
        cdrl = conn.execute(
            "SELECT * FROM contract_cdrls WHERE id = ? AND contract_id = ?",
            (cdrl_id, contract_id)
        ).fetchone()
        if not cdrl:
            return jsonify({"error": "CDRL not found"}), 404

        updates = ["status = ?", "updated_at = ?"]
        params = [new_status, _now()]
        if data.get("actual_delivery_date"):
            updates.append("actual_delivery_date = ?")
            params.append(data["actual_delivery_date"])
        if new_status == "accepted":
            updates.append("acceptance_date = ?")
            params.append(_now()[:10])
        if data.get("rejection_reason"):
            updates.append("rejection_reason = ?")
            params.append(data["rejection_reason"])

        params.append(cdrl_id)
        conn.execute(
            f"UPDATE contract_cdrls SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()

        return jsonify({"updated": True, "cdrl_id": cdrl_id, "status": new_status})
    finally:
        conn.close()


@gov_proposal_bp.route("/api/contracts/<contract_id>/obligation/<obl_id>", methods=["PATCH"])
def api_update_obligation(contract_id, obl_id):
    """Update obligation status via API."""
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "status required"}), 400

    valid = ("not_started", "in_progress", "compliant",
             "non_compliant", "waived", "deferred")
    if new_status not in valid:
        return jsonify({"error": f"Invalid status. Must be one of: {valid}"}), 400

    conn = _get_db()
    try:
        obl = conn.execute(
            "SELECT * FROM contract_obligations WHERE id = ? AND contract_id = ?",
            (obl_id, contract_id)
        ).fetchone()
        if not obl:
            return jsonify({"error": "Obligation not found"}), 404

        updates = ["status = ?", "updated_at = ?"]
        params = [new_status, _now()]
        if data.get("evidence"):
            updates.append("evidence = ?")
            params.append(data["evidence"])

        params.append(obl_id)
        conn.execute(
            f"UPDATE contract_obligations SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()

        return jsonify({"updated": True, "obligation_id": obl_id, "status": new_status})
    finally:
        conn.close()


@gov_proposal_bp.route("/api/contracts/<contract_id>/reminder/<reminder_id>/acknowledge", methods=["POST"])
def api_acknowledge_reminder(contract_id, reminder_id):
    """Acknowledge a pending reminder."""
    conn = _get_db()
    try:
        rem = conn.execute(
            "SELECT * FROM deliverable_reminders WHERE id = ? AND contract_id = ?",
            (reminder_id, contract_id)
        ).fetchone()
        if not rem:
            return jsonify({"error": "Reminder not found"}), 404

        conn.execute(
            "UPDATE deliverable_reminders SET status = 'acknowledged', "
            "acknowledged_at = ? WHERE id = ?",
            (_now(), reminder_id)
        )
        conn.commit()
        return jsonify({"acknowledged": True, "reminder_id": reminder_id})
    finally:
        conn.close()


# =========================================================================
# SBIR/STTR
# =========================================================================
@gov_proposal_bp.route("/sbir")
def sbir_list():
    """SBIR/STTR proposal listing with filters."""
    conn = _get_db()
    try:
        program_filter = request.args.get("program_type", "")
        phase_filter = request.args.get("phase", "")

        query = "SELECT * FROM sbir_proposals"
        conditions, params = [], []
        if program_filter:
            conditions.append("program_type = ?")
            params.append(program_filter)
        if phase_filter:
            conditions.append("phase = ?")
            params.append(phase_filter)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()
        proposals = [dict(r) for r in rows]

        phase1_count = sum(1 for p in proposals if p.get("phase") == "phase_1"
                          and p.get("status") not in ("closed", "withdrawn"))
        phase2_count = sum(1 for p in proposals if p.get("phase") == "phase_2"
                          and p.get("status") not in ("closed", "withdrawn"))
        trl_vals = [p["trl_current"] for p in proposals if p.get("trl_current")]
        avg_trl = round(sum(trl_vals) / len(trl_vals), 1) if trl_vals else 0

        return render_template("gov_proposal/sbir_list.html",
                               proposals=proposals,
                               total_count=len(proposals),
                               phase1_count=phase1_count,
                               phase2_count=phase2_count,
                               avg_trl=avg_trl,
                               program_filter=program_filter,
                               phase_filter=phase_filter)
    except Exception as e:
        logger.error("Error loading SBIR list: %s", e)
        return render_template("gov_proposal/sbir_list.html",
                               proposals=[], total_count=0,
                               phase1_count=0, phase2_count=0,
                               avg_trl=0, program_filter="",
                               phase_filter="")
    finally:
        conn.close()


@gov_proposal_bp.route("/sbir/<proposal_id>")
def sbir_detail(proposal_id):
    """SBIR/STTR proposal detail."""
    conn = _get_db()
    try:
        proposal = conn.execute(
            "SELECT * FROM sbir_proposals WHERE id = ?",
            (proposal_id,)
        ).fetchone()
        if not proposal:
            flash("SBIR proposal not found.", "error")
            return redirect(url_for("gov_proposal.sbir_list"))

        proposal = dict(proposal)

        # Build checklist from sbir_manager pattern
        checklist = []
        phase = proposal.get("phase", "phase_1")
        base_items = [
            ("Technical proposal narrative", True),
            ("Budget/cost proposal", True),
            ("Commercialization plan", phase != "phase_1"),
            ("Company data (DUNS, CAGE, SAM)", True),
            ("PI/Key personnel resumes", True),
            ("Subcontract plan", False),
            ("Letters of support", False),
            ("Data rights assertions", False),
        ]
        if proposal.get("program_type") == "sttr":
            base_items.append(("Research institution agreement", True))
            base_items.append(("Allocation plan (min 40% small biz, 30% RI)", True))

        submitted = proposal.get("status") in ("submitted", "awarded", "phase_complete")
        for item_text, required in base_items:
            checklist.append({
                "item": item_text,
                "required": required,
                "complete": submitted,
            })

        return render_template("gov_proposal/sbir_detail.html",
                               proposal=proposal,
                               checklist=checklist)
    except Exception as e:
        logger.error("Error loading SBIR detail: %s", e)
        flash("Error loading SBIR proposal.", "error")
        return redirect(url_for("gov_proposal.sbir_list"))
    finally:
        conn.close()


# =========================================================================
# IDIQ / BPA / GWAC
# =========================================================================
@gov_proposal_bp.route("/idiq")
def idiq_list():
    """IDIQ vehicle listing."""
    conn = _get_db()
    try:
        status_filter = request.args.get("status", "")
        query = (
            "SELECT v.*, "
            "(SELECT COUNT(*) FROM task_orders t WHERE t.vehicle_id = v.id) as task_order_count "
            "FROM idiq_vehicles v"
        )
        params = []
        if status_filter:
            query += " WHERE v.status = ?"
            params.append(status_filter)
        query += " ORDER BY v.created_at DESC"

        rows = conn.execute(query, params).fetchall()
        vehicles = [dict(r) for r in rows]

        active_count = sum(1 for v in vehicles if v.get("status") == "active")
        total_to = sum(v.get("task_order_count", 0) for v in vehicles)
        total_ceiling = sum(v.get("ceiling_value") or 0 for v in vehicles)

        return render_template("gov_proposal/idiq_list.html",
                               vehicles=vehicles,
                               total_count=len(vehicles),
                               active_count=active_count,
                               total_task_orders=total_to,
                               total_ceiling=total_ceiling,
                               status_filter=status_filter)
    except Exception as e:
        logger.error("Error loading IDIQ list: %s", e)
        return render_template("gov_proposal/idiq_list.html",
                               vehicles=[], total_count=0,
                               active_count=0, total_task_orders=0,
                               total_ceiling=0, status_filter="")
    finally:
        conn.close()


@gov_proposal_bp.route("/idiq/<vehicle_id>")
def idiq_detail(vehicle_id):
    """IDIQ vehicle detail with task orders."""
    conn = _get_db()
    try:
        vehicle = conn.execute(
            "SELECT * FROM idiq_vehicles WHERE id = ?",
            (vehicle_id,)
        ).fetchone()
        if not vehicle:
            flash("IDIQ vehicle not found.", "error")
            return redirect(url_for("gov_proposal.idiq_list"))

        vehicle = dict(vehicle)

        task_orders = [dict(r) for r in conn.execute(
            "SELECT * FROM task_orders WHERE vehicle_id = ? ORDER BY created_at DESC",
            (vehicle_id,)
        ).fetchall()]

        ceiling = vehicle.get("ceiling_value") or 0
        obligated = vehicle.get("total_obligated") or 0
        utilization_pct = round((obligated / ceiling * 100), 1) if ceiling > 0 else 0

        return render_template("gov_proposal/idiq_detail.html",
                               vehicle=vehicle,
                               task_orders=task_orders,
                               task_order_count=len(task_orders),
                               utilization_pct=utilization_pct)
    except Exception as e:
        logger.error("Error loading IDIQ detail: %s", e)
        flash("Error loading IDIQ vehicle.", "error")
        return redirect(url_for("gov_proposal.idiq_list"))
    finally:
        conn.close()


# =========================================================================
# RECOMPETES
# =========================================================================
@gov_proposal_bp.route("/recompetes")
def recompetes():
    """Recompete intelligence listing."""
    conn = _get_db()
    try:
        status_filter = request.args.get("status", "")
        query = "SELECT * FROM recompete_tracking"
        params = []
        if status_filter:
            query += " WHERE status = ?"
            params.append(status_filter)
        query += " ORDER BY anticipated_recompete_date ASC"

        rows = conn.execute(query, params).fetchall()
        recompete_list = [dict(r) for r in rows]

        # Stats
        upcoming_count = 0
        today = _now()[:10]
        for r in recompete_list:
            rd = r.get("anticipated_recompete_date", "")
            if rd and rd <= today[:4] + "-" + str(int(today[5:7]) + 3).zfill(2) + "-" + today[8:10]:
                upcoming_count += 1

        disp_vals = [r["displacement_score"] for r in recompete_list
                     if r.get("displacement_score") is not None]
        avg_displacement = round(sum(disp_vals) / len(disp_vals), 2) if disp_vals else 0

        pending_count = sum(1 for r in recompete_list
                           if r.get("our_decision") == "undecided")

        return render_template("gov_proposal/recompetes.html",
                               recompetes=recompete_list,
                               total_count=len(recompete_list),
                               upcoming_count=upcoming_count,
                               avg_displacement=avg_displacement,
                               pending_count=pending_count,
                               status_filter=status_filter)
    except Exception as e:
        logger.error("Error loading recompetes: %s", e)
        return render_template("gov_proposal/recompetes.html",
                               recompetes=[], total_count=0,
                               upcoming_count=0, avg_displacement=0,
                               pending_count=0, status_filter="")
    finally:
        conn.close()


@gov_proposal_bp.route("/recompetes/<recompete_id>")
def recompete_detail(recompete_id):
    """Recompete detail with displacement assessment."""
    conn = _get_db()
    try:
        recompete = conn.execute(
            "SELECT * FROM recompete_tracking WHERE id = ?",
            (recompete_id,)
        ).fetchone()
        if not recompete:
            flash("Recompete not found.", "error")
            return redirect(url_for("gov_proposal.recompetes"))

        recompete = dict(recompete)

        # Parse win strategy from JSON
        strategy = []
        raw_strategy = recompete.get("win_strategy")
        if raw_strategy:
            parsed = _safe_json(raw_strategy)
            if isinstance(parsed, list):
                strategy = parsed
            elif isinstance(parsed, dict) and "items" in parsed:
                strategy = parsed["items"]

        return render_template("gov_proposal/recompete_detail.html",
                               recompete=recompete,
                               strategy=strategy)
    except Exception as e:
        logger.error("Error loading recompete detail: %s", e)
        flash("Error loading recompete.", "error")
        return redirect(url_for("gov_proposal.recompetes"))
    finally:
        conn.close()


# =========================================================================
# MAIN
# =========================================================================
# End of GovProposal blueprint