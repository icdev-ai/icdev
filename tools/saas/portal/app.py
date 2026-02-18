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
import sqlite3
import sys
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
                      started_at, ends_at, created_at
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

        # Set session
        session["portal_tenant_id"] = row["tenant_id"]
        session["portal_user_id"] = row["user_id"]
        session["portal_user_role"] = row["role"]
        session["portal_user_email"] = row["email"]
        session["portal_user_name"] = row.get("display_name") or row["email"]
        session["portal_tenant_name"] = row["tenant_name"]
        session["portal_api_key"] = api_key  # For JS API calls

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
        "dashboard.html",
        tenant_name=tenant_name,
        project_count=len(project_list),
        active_projects=sum(1 for p in project_list if p.get("status") == "active"),
        compliance_score=0,
        recent_activity=[],
        alerts=0,
        projects=project_list,
        page="projects",
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
        "dashboard.html",
        tenant_name=tenant_name,
        project_count=0,
        active_projects=0,
        compliance_score=0,
        recent_activity=[],
        alerts=0,
        compliance_data=compliance_data,
        page="compliance",
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
@portal_bp.route("/settings")
@_portal_auth_required
def settings():
    """Tenant settings page."""
    tenant_id = g.tenant_id
    tenant = _get_tenant_info(tenant_id)
    if not tenant:
        return redirect(url_for("portal.dashboard"))

    subscription = _get_subscription(tenant_id)

    return render_template(
        "settings.html",
        tenant_name=tenant.get("name", "Unknown"),
        tenant=tenant,
        subscription=subscription or {},
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
        "dashboard.html",
        tenant_name=tenant_name,
        project_count=0,
        active_projects=0,
        compliance_score=0,
        recent_activity=[],
        alerts=0,
        usage_data=usage_data,
        page="usage",
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
        "dashboard.html",
        tenant_name=tenant_name,
        project_count=0,
        active_projects=0,
        compliance_score=0,
        recent_activity=[],
        alerts=0,
        audit_entries=audit_entries,
        page="audit",
        page_num=page_num,
        total_pages=total_pages,
        total_entries=total_entries,
        user_name=session.get("portal_user_name", "User"),
        user_role=session.get("portal_user_role", "viewer"),
    )
