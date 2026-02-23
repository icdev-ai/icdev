#!/usr/bin/env python3
"""ICDEV SaaS Tenant Admin Portal -- Flask Blueprint.

CUI // SP-CTI

Web-based administration portal for ICDEV SaaS tenants. Provides dashboard,
project management, compliance overview, team management, API key management,
usage metrics, audit trail viewer, and tenant settings.

Auth is handled by the main middleware (g.tenant_id, g.user_id, g.user_role).
For portal-specific session management, this module uses Flask session cookies
with the API key stored on login.

Routes:
    GET  /portal/          -> Dashboard (requires auth)
    GET  /portal/login     -> Login page (public)
    POST /portal/login     -> Handle API key login
    GET  /portal/projects  -> Project list for tenant
    GET  /portal/compliance -> Compliance status overview
    GET  /portal/team      -> Team management
    GET  /portal/settings  -> Tenant settings
    GET  /portal/keys      -> API key management
    GET  /portal/usage     -> Usage metrics
    GET  /portal/audit     -> Audit trail viewer
    GET  /portal/logout    -> Clear session, redirect to login

Usage:
    from tools.saas.portal.app import portal_bp
    app.register_blueprint(portal_bp)
"""

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"
PLATFORM_DB = DATA_DIR / "platform.db"
TENANTS_DIR = DATA_DIR / "tenants"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.portal")

# ---------------------------------------------------------------------------
# CUI Banner Configuration (matches Dashboard config.py pattern)
# ---------------------------------------------------------------------------
_CUI_YAML = BASE_DIR / "args" / "cui_markings.yaml"


def _load_yaml(filepath: Path) -> dict:
    """Load a YAML file. Uses PyYAML if available, otherwise minimal parser."""
    try:
        import yaml
        with open(filepath, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        return _simple_yaml_parse(filepath)


def _simple_yaml_parse(filepath: Path) -> dict:
    """Minimal YAML-subset parser for flat and one-level nested mappings."""
    data: dict = {}
    if not filepath.exists():
        return data
    current_section = None
    with open(filepath, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if indent == 0:
                if value:
                    data[key] = value
                else:
                    current_section = key
                    data[current_section] = {}
            elif current_section is not None:
                data[current_section][key] = value
    return data


_CUI_CONFIG = _load_yaml(_CUI_YAML) if _CUI_YAML.exists() else {}

CUI_BANNER_TOP = os.environ.get(
    "ICDEV_CUI_BANNER_TOP",
    _CUI_CONFIG.get("banner_top", "CUI // SP-CTI"),
)
CUI_BANNER_BOTTOM = os.environ.get(
    "ICDEV_CUI_BANNER_BOTTOM",
    _CUI_CONFIG.get("banner_bottom", "CUI // SP-CTI"),
)
CUI_BANNER_ENABLED = os.environ.get(
    "ICDEV_CUI_BANNER_ENABLED", "true"
).lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
portal_bp = Blueprint(
    "portal",
    __name__,
    url_prefix="/portal",
    template_folder="templates",
    static_folder="static",
)


# ---------------------------------------------------------------------------
# Portal Session Store (Enhancement #1A — opaque tokens, no raw API keys)
# ---------------------------------------------------------------------------
_PORTAL_SESSIONS = {}  # token -> {tenant_id, user_id, role, created_at}
_PORTAL_SESSION_TTL = 24 * 3600  # 24 hours


def _register_portal_session(token, tenant_id, user_id, role):
    """Register a new portal session token."""
    _PORTAL_SESSIONS[token] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role,
        "created_at": time.time(),
    }


def validate_portal_session_token(token):
    """Validate a portal session token and return session data or None."""
    sess = _PORTAL_SESSIONS.get(token)
    if not sess:
        return None
    if time.time() - sess["created_at"] > _PORTAL_SESSION_TTL:
        _PORTAL_SESSIONS.pop(token, None)
        return None
    return sess


def invalidate_portal_session(token):
    """Remove a portal session token."""
    _PORTAL_SESSIONS.pop(token, None)


# ---------------------------------------------------------------------------
# CSRF Protection (Enhancement #1B)
# ---------------------------------------------------------------------------
def _generate_csrf_token():
    """Generate or retrieve a CSRF token for the current session."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def _validate_csrf_token():
    """Validate the CSRF token from the submitted form."""
    token = request.form.get("_csrf_token", "")
    expected = session.get("_csrf_token", "")
    if not expected or not token or token != expected:
        return False
    return True


@portal_bp.context_processor
def _inject_cui_banner():
    """Inject CUI banner settings and CSRF token into all portal templates."""
    return {
        "cui_banner_top": CUI_BANNER_TOP,
        "cui_banner_bottom": CUI_BANNER_BOTTOM,
        "cui_banner_enabled": CUI_BANNER_ENABLED,
        "csrf_token": _generate_csrf_token(),
    }


# ---------------------------------------------------------------------------
# Database Helpers
# ---------------------------------------------------------------------------
def _get_platform_conn():
    """Get a connection to the platform database."""
    db_path = Path(os.environ.get("PLATFORM_DB_PATH", str(PLATFORM_DB)))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_tenant_conn(tenant_id):
    """Get a connection to a tenant's isolated database."""
    conn = _get_platform_conn()
    try:
        row = conn.execute(
            "SELECT slug, db_host, db_name FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        if not row:
            return None
        slug = row["slug"]
        db_name = row["db_name"] or (slug + ".db")
        db_host = row["db_host"]
        if db_host:
            db_path = Path(db_host) / db_name
        else:
            db_path = TENANTS_DIR / db_name
    finally:
        conn.close()

    if not db_path.exists():
        return None

    tconn = sqlite3.connect(str(db_path))
    tconn.row_factory = sqlite3.Row
    return tconn


def _utcnow():
    """Current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Auth Helpers (portal session)
# ---------------------------------------------------------------------------
def _portal_auth_required(f):
    """Decorator: redirect to login if no portal session."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        # Check if middleware already set tenant context
        tenant_id = getattr(g, "tenant_id", None)
        if tenant_id:
            return f(*args, **kwargs)
        # Fall back to session-based auth
        if "portal_tenant_id" not in session:
            return redirect(url_for("portal.login"))
        g.tenant_id = session["portal_tenant_id"]
        g.user_id = session.get("portal_user_id")
        g.user_role = session.get("portal_user_role")
        return f(*args, **kwargs)

    return decorated


def _get_tenant_info(tenant_id):
    """Fetch tenant record from platform DB."""
    conn = _get_platform_conn()
    try:
        row = conn.execute(
            """SELECT id, name, slug, status, tier, impact_level,
                      settings, artifact_config, bedrock_config, idp_config,
                      created_at, updated_at
               FROM tenants WHERE id = ?""",
            (tenant_id,),
        ).fetchone()
        if row:
            tenant = dict(row)
            for field in ("settings", "artifact_config", "bedrock_config", "idp_config"):
                val = tenant.get(field)
                if val and isinstance(val, str):
                    try:
                        tenant[field] = json.loads(val)
                    except json.JSONDecodeError:
                        tenant[field] = {}
                elif val is None:
                    tenant[field] = {}
            return tenant
        return None
    finally:
        conn.close()


def _get_subscription(tenant_id):
    """Fetch active subscription for a tenant."""
    conn = _get_platform_conn()
    try:
        row = conn.execute(
            """SELECT id, tier, status, max_projects, max_users,
                      starts_at, ends_at, created_at
               FROM subscriptions WHERE tenant_id = ? AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes: Login / Logout
# ---------------------------------------------------------------------------
@portal_bp.route("/login", methods=["GET"])
def login():
    """Render the login page (public endpoint)."""
    error = request.args.get("error")
    return render_template("login.html", error=error)


@portal_bp.route("/login", methods=["POST"])
def login_post():
    """Handle API key login via form submission."""
    # Validate CSRF token (Enhancement #1B)
    if not _validate_csrf_token():
        return redirect(url_for("portal.login", error="Invalid form submission. Please try again."))

    api_key = request.form.get("api_key", "").strip()
    if not api_key:
        return redirect(url_for("portal.login", error="API key is required"))

    # Validate the key
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    conn = _get_platform_conn()
    try:
        row = conn.execute(
            """SELECT k.id as key_id, k.tenant_id, k.user_id, k.status as key_status,
                      u.role, u.email, u.display_name,
                      t.status as tenant_status, t.name as tenant_name
               FROM api_keys k
               JOIN users u ON k.user_id = u.id AND k.tenant_id = u.tenant_id
               JOIN tenants t ON k.tenant_id = t.id
               WHERE k.key_hash = ?""",
            (key_hash,),
        ).fetchone()

        if not row:
            return redirect(url_for("portal.login", error="Invalid API key"))

        row = dict(row)

        if row["key_status"] not in ("active", 1):
            return redirect(url_for("portal.login", error="API key is revoked or expired"))

        if row["tenant_status"] not in ("active",):
            return redirect(url_for("portal.login", error="Tenant is not active"))

        # Generate opaque portal session token (Enhancement #1A)
        portal_token = "psess_" + secrets.token_hex(24)
        _register_portal_session(
            portal_token, row["tenant_id"], row["user_id"], row["role"],
        )

        # Set session — NO raw API key stored (Enhancement #1A)
        session["portal_tenant_id"] = row["tenant_id"]
        session["portal_user_id"] = row["user_id"]
        session["portal_user_role"] = row["role"]
        session["portal_user_email"] = row["email"]
        session["portal_user_name"] = row.get("display_name") or row["email"]
        session["portal_tenant_name"] = row["tenant_name"]
        session["portal_session_token"] = portal_token

        # Update last_used
        try:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (_utcnow(), row["key_id"]),
            )
            conn.commit()
        except Exception:
            pass

        return redirect(url_for("portal.dashboard"))
    finally:
        conn.close()


@portal_bp.route("/logout")
def logout():
    """Clear session and redirect to login."""
    # Invalidate portal session token (Enhancement #1A)
    token = session.get("portal_session_token")
    if token:
        invalidate_portal_session(token)
    session.clear()
    return redirect(url_for("portal.login"))


# ---------------------------------------------------------------------------
# Routes: Dashboard
# ---------------------------------------------------------------------------
@portal_bp.route("/")
@_portal_auth_required
def dashboard():
    """Main dashboard page."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    if not tenant:
        session.clear()
        return redirect(url_for("portal.login", error="Tenant not found"))

    tenant_name = tenant.get("name", "Unknown Tenant")

    # Project counts from tenant DB
    project_count = 0
    active_projects = 0
    compliance_score = 0
    tconn = _get_tenant_conn(tenant_id)
    if tconn:
        try:
            try:
                row = tconn.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()
                project_count = row["cnt"] if row else 0
            except Exception:
                pass
            try:
                row = tconn.execute(
                    "SELECT COUNT(*) as cnt FROM projects WHERE status = 'active'"
                ).fetchone()
                active_projects = row["cnt"] if row else 0
            except Exception:
                pass
            # Compliance score: average across projects
            try:
                row = tconn.execute(
                    "SELECT AVG(compliance_score) as avg_score FROM projects"
                ).fetchone()
                compliance_score = round(row["avg_score"] or 0)
            except Exception:
                compliance_score = 0
        finally:
            tconn.close()

    # Recent audit activity
    recent_activity = []
    conn = _get_platform_conn()
    try:
        rows = conn.execute(
            """SELECT event_type, action, details, recorded_at
               FROM audit_platform WHERE tenant_id = ?
               ORDER BY recorded_at DESC LIMIT 10""",
            (tenant_id,),
        ).fetchall()
        recent_activity = [dict(r) for r in rows]
    except Exception:
        pass
    finally:
        conn.close()

    # Active alerts (placeholder: count high/critical findings)
    alerts = 0

    return render_template(
        "dashboard.html",
        tenant_name=tenant_name,
        project_count=project_count,
        active_projects=active_projects,
        compliance_score=compliance_score,
        recent_activity=recent_activity,
        alerts=alerts,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Projects
# ---------------------------------------------------------------------------
@portal_bp.route("/projects")
@_portal_auth_required
def projects():
    """Project list for the tenant."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    project_list = []
    tconn = _get_tenant_conn(tenant_id)
    if tconn:
        try:
            rows = tconn.execute(
                """SELECT id, name, status, compliance_score,
                          created_at, updated_at
                   FROM projects ORDER BY updated_at DESC"""
            ).fetchall()
            project_list = [dict(r) for r in rows]
        except Exception:
            pass
        finally:
            tconn.close()

    return render_template(
        "projects.html",
        tenant_name=tenant_name,
        projects=project_list,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Compliance
# ---------------------------------------------------------------------------
@portal_bp.route("/compliance")
@_portal_auth_required
def compliance():
    """Compliance status overview."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    compliance_data = []
    tconn = _get_tenant_conn(tenant_id)
    if tconn:
        try:
            rows = tconn.execute(
                """SELECT p.name as project_name, p.compliance_score,
                          p.status, p.id as project_id
                   FROM projects p ORDER BY p.name"""
            ).fetchall()
            compliance_data = [dict(r) for r in rows]
        except Exception:
            pass
        finally:
            tconn.close()

    return render_template(
        "compliance.html",
        tenant_name=tenant_name,
        compliance_data=compliance_data,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Team Management
# ---------------------------------------------------------------------------
@portal_bp.route("/team")
@_portal_auth_required
def team():
    """Team management page."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    users = []
    conn = _get_platform_conn()
    try:
        rows = conn.execute(
            """SELECT id, email, display_name, role, status,
                      last_login, auth_method, created_at
               FROM users WHERE tenant_id = ?
               ORDER BY created_at""",
            (tenant_id,),
        ).fetchall()
        users = [dict(r) for r in rows]
    except Exception:
        pass
    finally:
        conn.close()

    return render_template(
        "team.html",
        tenant_name=tenant_name,
        users=users,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Settings
# ---------------------------------------------------------------------------
@portal_bp.route("/profile")
@_portal_auth_required
def profile():
    """User profile page with optional BYOK LLM key management."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    user_email = session.get("portal_user_email", "")
    user_name = session.get("portal_user_name", "User")
    user_role = session.get("portal_user_role", "viewer")
    user_id = session.get("portal_user_id", "")

    # Fetch user details from platform DB
    user_info = {}
    conn = _get_platform_conn()
    try:
        row = conn.execute(
            """SELECT id, email, display_name, role, status,
                      auth_method, last_login, created_at
               FROM users WHERE id = ? AND tenant_id = ?""",
            (user_id, tenant_id),
        ).fetchone()
        if row:
            user_info = dict(row)
    except Exception:
        pass
    finally:
        conn.close()

    # BYOK LLM keys (only if enabled via env var)
    byok_enabled = os.environ.get(
        "ICDEV_BYOK_ENABLED", "false"
    ).lower() in ("1", "true", "yes")
    llm_keys = []
    if byok_enabled:
        tconn = _get_tenant_conn(tenant_id)
        if tconn:
            try:
                rows = tconn.execute(
                    """SELECT id, provider, key_label, status,
                              department, is_department_key,
                              created_at, updated_at
                       FROM dashboard_user_llm_keys
                       WHERE user_id = ?
                       ORDER BY created_at DESC""",
                    (user_id,),
                ).fetchall()
                llm_keys = [dict(r) for r in rows]
            except Exception:
                pass  # Table may not exist on older tenant DBs
            finally:
                tconn.close()

    return render_template(
        "profile.html",
        tenant_name=tenant_name,
        user_info=user_info,
        byok_enabled=byok_enabled,
        llm_keys=llm_keys,
        user_name=user_name,
        user_role=user_role,
    )


@portal_bp.route("/settings")
@_portal_auth_required
def settings():
    """Tenant settings page."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    if not tenant:
        return redirect(url_for("portal.dashboard"))

    subscription = _get_subscription(tenant_id)

    # Load LLM provider keys (Phase 32)
    llm_keys = []
    try:
        conn = _get_platform_conn()
        rows = conn.execute(
            "SELECT id, provider, key_label, key_prefix, status, "
            "created_at, updated_at "
            "FROM tenant_llm_keys WHERE tenant_id = ? "
            "ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
        llm_keys = [dict(r) for r in rows]
        conn.close()
    except Exception:
        pass  # Table may not exist yet on older DBs

    # Determine if tenant tier allows BYOK
    tier = (subscription or {}).get("tier", tenant.get("tier", "starter"))
    byok_allowed = tier in ("professional", "enterprise")

    return render_template(
        "settings.html",
        tenant_name=tenant.get("name", "Unknown"),
        tenant=tenant,
        subscription=subscription or {},
        llm_keys=llm_keys,
        byok_allowed=byok_allowed,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: API Key Management
# ---------------------------------------------------------------------------
@portal_bp.route("/keys")
@_portal_auth_required
def api_keys():
    """API key management page."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    keys = []
    conn = _get_platform_conn()
    try:
        rows = conn.execute(
            """SELECT k.id, k.name, k.key_prefix, k.status, k.created_at,
                      k.last_used_at, k.expires_at, u.email as owner_email
               FROM api_keys k
               JOIN users u ON k.user_id = u.id
               WHERE k.tenant_id = ?
               ORDER BY k.created_at DESC""",
            (tenant_id,),
        ).fetchall()
        keys = [dict(r) for r in rows]
    except Exception:
        pass
    finally:
        conn.close()

    # Check if a new key was just created (passed via query param)
    new_key = request.args.get("new_key")

    return render_template(
        "api_keys.html",
        tenant_name=tenant_name,
        keys=keys,
        new_key=new_key,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Usage Metrics
# ---------------------------------------------------------------------------
@portal_bp.route("/usage")
@_portal_auth_required
def usage():
    """Usage metrics page."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    usage_data = {
        "total_api_calls": 0,
        "total_tokens": 0,
        "top_endpoints": [],
        "daily_calls": [],
    }

    conn = _get_platform_conn()
    try:
        # Total API calls
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM usage_records WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        usage_data["total_api_calls"] = row["cnt"] if row else 0

        # Total tokens
        row = conn.execute(
            "SELECT COALESCE(SUM(tokens_used), 0) as total FROM usage_records WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        usage_data["total_tokens"] = row["total"] if row else 0

        # Top endpoints
        rows = conn.execute(
            """SELECT endpoint, COUNT(*) as cnt
               FROM usage_records WHERE tenant_id = ?
               GROUP BY endpoint ORDER BY cnt DESC LIMIT 10""",
            (tenant_id,),
        ).fetchall()
        usage_data["top_endpoints"] = [dict(r) for r in rows]
    except Exception:
        pass
    finally:
        conn.close()

    return render_template(
        "usage.html",
        tenant_name=tenant_name,
        usage_data=usage_data,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Audit Trail
# ---------------------------------------------------------------------------
@portal_bp.route("/audit")
@_portal_auth_required
def audit():
    """Audit trail viewer."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    page_num = int(request.args.get("page", 1))
    per_page = 25
    offset = (page_num - 1) * per_page

    audit_entries = []
    total_entries = 0
    conn = _get_platform_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_platform WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        total_entries = row["cnt"] if row else 0

        rows = conn.execute(
            """SELECT id, event_type, action, details, ip_address,
                      user_agent, recorded_at
               FROM audit_platform WHERE tenant_id = ?
               ORDER BY recorded_at DESC LIMIT ? OFFSET ?""",
            (tenant_id, per_page, offset),
        ).fetchall()
        audit_entries = [dict(r) for r in rows]
    except Exception:
        pass
    finally:
        conn.close()

    total_pages = max(1, (total_entries + per_page - 1) // per_page)

    return render_template(
        "audit.html",
        tenant_name=tenant_name,
        audit_entries=audit_entries,
        page_num=page_num,
        total_pages=total_pages,
        total_entries=total_entries,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Phase Roadmap
# ---------------------------------------------------------------------------
@portal_bp.route("/phases")
@_portal_auth_required
def phases():
    """Phase roadmap — ICDEV phases filtered by tenant tier and impact level."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    # Determine tenant's tier and impact level for filtering
    subscription = _get_subscription(tenant_id)
    tier = (subscription or {}).get("tier", (tenant or {}).get("tier", "starter"))
    impact_level = (tenant or {}).get("impact_level", "IL4")

    # Load phases from registry
    try:
        from tools.dashboard.phase_loader import (
            load_phases, load_categories, load_statuses,
            get_phase_summary, filter_phases,
        )
        all_phases = load_phases()
        categories = load_categories()
        statuses = load_statuses()

        # Filter to phases available for this tenant's tier
        phases_list = filter_phases(all_phases, tier=tier)
        summary = get_phase_summary(phases_list)

        # Also compute all-phases summary for comparison
        all_summary = get_phase_summary(all_phases)
    except (ImportError, Exception):
        phases_list = []
        categories = {}
        statuses = {}
        summary = {"total": 0, "completed": 0, "active": 0, "planned": 0,
                   "progress_pct": 0, "by_category": {}}
        all_summary = summary

    # Optional category filter
    cat_filter = request.args.get("category", "")
    if cat_filter:
        phases_list = [p for p in phases_list if p.get("category") == cat_filter]

    return render_template(
        "phases.html",
        tenant_name=tenant_name,
        phases=phases_list,
        categories=categories,
        statuses=statuses,
        summary=summary,
        all_summary=all_summary,
        category_filter=cat_filter,
        tenant_tier=tier,
        tenant_il=impact_level,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
        active_page="phases",
    )


# ---------------------------------------------------------------------------
# Routes: CMMC Self-Assessment (Phase 4 — Enhancement #8)
# ---------------------------------------------------------------------------
@portal_bp.route("/cmmc")
@_portal_auth_required
def cmmc():
    """CMMC self-assessment wizard."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    project_list = []
    tconn = _get_tenant_conn(tenant_id)
    if tconn:
        try:
            rows = tconn.execute(
                """SELECT id, name, status, compliance_score,
                          created_at, updated_at
                   FROM projects ORDER BY updated_at DESC"""
            ).fetchall()
            project_list = [dict(r) for r in rows]
        except Exception:
            pass
        finally:
            tconn.close()

    return render_template(
        "cmmc.html",
        tenant_name=tenant_name,
        projects=project_list,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )


# ---------------------------------------------------------------------------
# Routes: Cross-Language Translation (Phase 43)
# ---------------------------------------------------------------------------
@portal_bp.route("/translations")
@_portal_auth_required
def translations():
    """Translation jobs — list, status, validation scores (tenant-scoped)."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    jobs = []
    total = completed = in_progress = failed = 0
    avg_api_score = None

    tconn = _get_tenant_conn(tenant_id)
    if tconn:
        try:
            try:
                rows = tconn.execute(
                    """SELECT id, project_id, source_language, target_language,
                              status, total_units, translated_units, mocked_units,
                              failed_units, gate_result, llm_model,
                              elapsed_seconds, created_at
                       FROM translation_jobs ORDER BY created_at DESC LIMIT 100"""
                ).fetchall()
                jobs = [dict(r) for r in rows]
            except Exception:
                pass

            total = len(jobs)
            completed = sum(1 for j in jobs if j.get("status") == "completed")
            in_progress = sum(1 for j in jobs if j.get("status") in
                              ("pending", "extracting", "translating", "assembling", "validating"))
            failed = sum(1 for j in jobs if j.get("status") in ("failed", "partial"))

            try:
                row = tconn.execute(
                    """SELECT AVG(score) as avg_score FROM translation_validations
                       WHERE check_type = 'api_surface' AND passed = 1"""
                ).fetchone()
                if row and row["avg_score"]:
                    avg_api_score = round(row["avg_score"] * 100, 1)
            except Exception:
                pass
        finally:
            tconn.close()

    return render_template(
        "translations.html",
        tenant_name=tenant_name,
        jobs=jobs,
        total=total,
        completed=completed,
        in_progress=in_progress,
        failed=failed,
        avg_api_score=avg_api_score,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
        active_page="translations",
    )


@portal_bp.route("/translations/<job_id>")
@_portal_auth_required
def translation_detail(job_id):
    """Translation job detail (tenant-scoped)."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    job = None
    units = []
    validations = []
    deps = []

    tconn = _get_tenant_conn(tenant_id)
    if tconn:
        try:
            try:
                row = tconn.execute(
                    "SELECT * FROM translation_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                job = dict(row) if row else None
            except Exception:
                pass

            if job:
                try:
                    rows = tconn.execute(
                        """SELECT unit_name, unit_kind, source_file, status,
                                  source_complexity, target_complexity,
                                  repair_count, candidate_selected
                           FROM translation_units WHERE job_id = ?
                           ORDER BY created_at""", (job_id,)
                    ).fetchall()
                    units = [dict(u) for u in rows]
                except Exception:
                    pass

                try:
                    rows = tconn.execute(
                        """SELECT check_type, passed, score, findings
                           FROM translation_validations WHERE job_id = ?""",
                        (job_id,)
                    ).fetchall()
                    validations = [dict(v) for v in rows]
                except Exception:
                    pass

                try:
                    rows = tconn.execute(
                        """SELECT source_import, target_import, mapping_source,
                                  confidence, domain
                           FROM translation_dependency_mappings WHERE job_id = ?""",
                        (job_id,)
                    ).fetchall()
                    deps = [dict(d) for d in rows]
                except Exception:
                    pass
        finally:
            tconn.close()

    if not job:
        return render_template("404.html", message="Translation job not found"), 404

    return render_template(
        "translation_detail.html",
        tenant_name=tenant_name,
        job=job,
        units=units,
        validations=validations,
        deps=deps,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
        active_page="translations",
    )


@portal_bp.route("/chat-streams")
@_portal_auth_required
def chat_streams():
    """Multi-stream parallel chat — Phase 44 (D257-D260). Tenant-scoped."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    tenant_name = tenant.get("name", "Unknown") if tenant else "Unknown"

    return render_template(
        "chat_streams.html",
        tenant_name=tenant_name,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
        active_page="chat-streams",
    )
