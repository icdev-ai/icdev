# [TEMPLATE: CUI // SP-CTI]
from __future__ import annotations
"""
Dashboard authentication middleware (Phase 30 — D169-D172).

Provides:
- API key hashing (SHA-256) and validation
- Flask session management (signed cookies)
- before_request hook for auth enforcement
- require_role() decorator for RBAC
- Auth event logging (append-only, D6 compliant)
- CLI bootstrap for creating first admin user
"""

import functools
import hashlib
import hmac
import json
import os
import secrets
import uuid
from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger
from datetime import datetime, timezone

from flask import (
    Flask,
    abort,
    g,
    redirect,
    request,
    session,
    url_for,
)

from tools.dashboard.config import DASHBOARD_SECRET, DB_PATH

# ---------------------------------------------------------------------------
# Key generation & hashing
# ---------------------------------------------------------------------------

API_KEY_PREFIX = "icdev_dash_"
API_KEY_LENGTH = 32  # 32 random bytes = 64 hex chars


def generate_api_key() -> str:
    """Generate a new dashboard API key with prefix."""
    raw = secrets.token_hex(API_KEY_LENGTH)
    return f"{API_KEY_PREFIX}{raw}"


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hash of an API key for storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str) -> str:
    """Extract the first 8 visible chars after the prefix for display."""
    after_prefix = raw_key[len(API_KEY_PREFIX) :]
    return after_prefix[:8] if len(after_prefix) >= 8 else after_prefix


# ---------------------------------------------------------------------------
# Database helpers (OS-agnostic — uses config DB_PATH)
# ---------------------------------------------------------------------------


logger = get_logger("icdev.dashboard.auth")


def _get_db():
    """Get a connection to the ICDEV™ database."""
    conn = get_connection(db_path=str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _dashboard_users_table_exists(conn) -> bool:
    """Portable existence probe.

    A `sqlite_master` lookup would be wrong on PostgreSQL, which is the primary
    backend. Probing the table itself is true on both. PostgreSQL aborts the
    transaction on a failed statement, so roll back before the caller reuses
    the connection.
    """
    try:
        conn.execute("SELECT 1 FROM dashboard_users LIMIT 1").fetchone()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def log_auth_event(user_id, event_type, ip_address=None, user_agent=None, details=None):
    """Append-only auth event logging (D6 compliant)."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO dashboard_auth_log
               (user_id, event_type, ip_address, user_agent, details)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, event_type, ip_address, user_agent, details),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Auth logging should never break the request
        logger.warning("log_auth_event: best-effort INSERT into dashboard_auth_log failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# User + key CRUD
# ---------------------------------------------------------------------------


def create_user(
    email, display_name, role="developer", created_by=None, tenant_id=None,
    clearance_level="CUI", compartments=None,
):
    """Create a new dashboard user. Returns user dict.

    clearance_level / compartments are the Bell-LaPadula MAC subject
    attributes (prop-sec-02) — dashboard_users.clearance_level (TEXT,
    e.g. 'CUI'/'SECRET'/'TOP SECRET') and .compartments (JSON array of
    COI_*/LAC_*/SCI strings). Both default to the least-privileged value
    so existing callers are unaffected.
    """
    user_id = str(uuid.uuid4())
    compartments_json = json.dumps(list(compartments) if compartments else [])
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO dashboard_users
               (id, email, display_name, role, created_by, tenant_id,
                clearance_level, compartments)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, email, display_name, role, created_by, tenant_id,
             clearance_level, compartments_json),
        )
        conn.commit()
    finally:
        conn.close()

    log_auth_event(user_id, "user_created", details=f"email={email}, role={role}")
    return {
        "id": user_id,
        "email": email,
        "display_name": display_name,
        "role": role,
        "status": "active",
        "tenant_id": tenant_id,
        "clearance_level": clearance_level,
        "compartments": compartments_json,
    }


def create_api_key_for_user(user_id, label=None, created_by=None, expires_at=None):
    """Generate and store an API key for a user. Returns the RAW key (only time it's visible)."""
    raw_key = generate_api_key()
    key_id = str(uuid.uuid4())
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO dashboard_api_keys
               (id, user_id, key_hash, key_prefix, label, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (key_id, user_id, hashed, prefix, label, expires_at),
        )
        conn.commit()
    finally:
        conn.close()

    log_auth_event(
        user_id,
        "key_created",
        details=f"key_id={key_id}, prefix={prefix}, label={label}",
    )
    return {"key_id": key_id, "raw_key": raw_key, "prefix": prefix}


def validate_api_key(raw_key):
    """Validate an API key. Returns user Row or None."""
    if not raw_key or not raw_key.startswith(API_KEY_PREFIX):
        return None

    hashed = hash_api_key(raw_key)
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT u.id, u.email, u.display_name, u.role, u.status,
                      u.clearance_level, u.compartments,
                      k.id as key_id, k.expires_at
               FROM dashboard_api_keys k
               JOIN dashboard_users u ON k.user_id = u.id
               WHERE k.key_hash = %s AND k.status = 'active'""",
            (hashed,),
        ).fetchone()

        if not row:
            return None

        # Check user status
        if row["status"] != "active":
            return None

        # Check expiry
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    return None
            except (ValueError, TypeError):
                pass

        # Update last_used_at
        conn.execute(
            "UPDATE dashboard_api_keys SET last_used_at = %s WHERE id = %s",
            (datetime.now(timezone.utc).isoformat(), row["key_id"]),
        )
        conn.commit()
        return row
    finally:
        conn.close()


def get_user_by_id(user_id, tenant_id=None):
    """Fetch a dashboard user by ID, scoped to tenant when provided."""
    conn = _get_db()
    try:
        if tenant_id is not None:
            return conn.execute(
                "SELECT * FROM dashboard_users WHERE id = %s AND tenant_id = %s",
                (user_id, tenant_id),
            ).fetchone()
        return conn.execute("SELECT * FROM dashboard_users WHERE id = %s", (user_id,)).fetchone()
    finally:
        conn.close()


def list_users(status=None, tenant_id=None):
    """List all dashboard users, optionally filtered by status and tenant."""
    conn = _get_db()
    try:
        clauses = []
        params = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        else:
            clauses.append("(tenant_id IS NULL OR tenant_id = '')")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM dashboard_users {where} ORDER BY created_at DESC",  # nosec B608
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_api_key(key_id, revoked_by=None):
    """Revoke a dashboard API key."""
    conn = _get_db()
    try:
        conn.execute(
            """UPDATE dashboard_api_keys
               SET status = 'revoked', revoked_at = %s, revoked_by = %s
               WHERE id = %s""",
            (datetime.now(timezone.utc).isoformat(), revoked_by, key_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Find user_id for logging
    conn = _get_db()
    try:
        row = conn.execute("SELECT user_id FROM dashboard_api_keys WHERE id = %s", (key_id,)).fetchone()
        if row:
            log_auth_event(
                row["user_id"],
                "key_revoked",
                details=f"key_id={key_id}, revoked_by={revoked_by}",
            )
    finally:
        conn.close()


def list_api_keys_for_user(user_id):
    """List all API keys for a user (hashes redacted)."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT id, key_prefix, label, status, last_used_at,
                      expires_at, created_at, revoked_at
               FROM dashboard_api_keys WHERE user_id = %s
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def suspend_user(user_id, suspended_by=None):
    """Suspend a dashboard user."""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE dashboard_users SET status = 'suspended', updated_at = %s WHERE id = %s",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    log_auth_event(user_id, "user_suspended", details=f"by={suspended_by}")


def reactivate_user(user_id, reactivated_by=None):
    """Reactivate a suspended dashboard user."""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE dashboard_users SET status = 'active', updated_at = %s WHERE id = %s",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    log_auth_event(user_id, "user_reactivated", details=f"by={reactivated_by}")


# ---------------------------------------------------------------------------
# RBAC — role-based access control (D172)
# ---------------------------------------------------------------------------

# Single source of truth for dashboard_users.role. Keep in sync with the
# CHECK constraint in tools/db/init_icdev_db.py's dashboard_users CREATE
# TABLE (SQL CHECK constraints can't reference a Python constant directly,
# so this list and that constraint must be updated together). Every role
# referenced anywhere in RBAC_MATRIX below, or in any @require_role(...)
# call, must appear here -- otherwise create_user() can never actually
# persist a user with that role (dashboard-users-role-check-constraint;
# bd/capture_mgr/contract_mgr/reviewer were added to RBAC_MATRIX by
# prop-fix-08 but never reached the CHECK constraint until this fix).
VALID_DASHBOARD_ROLES = frozenset({
    "admin", "pm", "developer", "isso", "co", "cor",
    # GovLift (migration 139) — see tools/govlift/rbac.py::GOVLIFT_ROLES
    "migration_engineer", "component_admin", "auditor", "ciso",
    # GovCon / Proposals / CPMP (prop-fix-08) — see RBAC_MATRIX below
    "bd", "capture_mgr", "contract_mgr", "reviewer",
})

# Maps page/action to allowed roles
RBAC_MATRIX = {
    # Pages accessible to all authenticated users
    "home": {"admin", "pm", "developer", "isso", "co", "cor"},
    "projects": {"admin", "pm", "developer", "isso", "co"},
    "agents": {"admin", "pm", "developer", "isso", "co"},
    "monitoring": {"admin", "pm", "developer", "isso", "co"},
    "activity": {"admin", "pm", "developer", "isso", "co"},
    "profile": {"admin", "pm", "developer", "isso", "co", "cor"},
    # Pages with restricted access
    "batch": {"admin", "isso", "pm", "developer"},
    "chat": {"admin", "isso", "pm", "developer"},
    "diagrams": {"admin", "isso", "pm", "developer"},
    "cicd": {"admin", "isso", "pm", "developer"},
    "query": {"admin", "isso", "pm", "developer"},
    "gateway": {"admin", "isso"},
    # Admin-only
    "admin": {"admin"},
    # Usage: admin sees all, others see own
    "usage": {"admin", "pm", "developer", "isso", "co"},
    # CPMP (Phase 60)
    "cpmp": {"admin", "pm", "developer", "isso", "co", "contract_mgr"},
    "cpmp_cor": {"admin", "pm", "isso", "co", "cor", "contract_mgr"},
    # GovCon (prop-fix-08) — capture/BD surfaces
    "govcon": {"admin", "bd", "capture_mgr", "pm", "isso"},
    "govcon_requirements": {"admin", "bd", "capture_mgr", "pm", "isso"},
    "govcon_capabilities": {"admin", "bd", "capture_mgr", "pm", "isso"},
    # Proposals (prop-fix-08)
    "proposals_list": {"admin", "bd", "capture_mgr", "pm", "reviewer"},
    "proposals_detail": {"admin", "bd", "capture_mgr", "pm", "reviewer"},
    "proposals_section_detail": {"admin", "bd", "capture_mgr", "pm", "reviewer"},
    "proposals_compliance_gaps": {"admin", "bd", "capture_mgr", "pm", "reviewer"},
    "proposals_reviews_dashboard": {"admin", "pm", "reviewer"},
    "proposals_language": {"admin", "bd", "capture_mgr", "pm", "reviewer"},
    "proposals_ptw": {"admin", "capture_mgr", "pm"},
    # CPMP detail/deliverable surfaces (prop-fix-08)
    "cpmp_detail": {"admin", "pm", "contract_mgr", "co", "cor", "isso"},
    "cpmp_deliverable_detail": {"admin", "pm", "contract_mgr", "co", "cor", "isso"},
    # Proposal Genesis (prop-fix-08)
    "proposal_genesis": {"admin", "pm", "bd", "capture_mgr"},
    # Strategos — approval / resolution / workflow-advance / authoritative-deletion
    # class of state-changing routes (nav-sec-05). Enforcement is via the
    # @require_role(*_STRATEGOS_APPROVAL_ROLES) decorators in
    # apps/strategos/blueprint.py; this entry is the documentary role map (the
    # matrix itself is not consumed programmatically for enforcement).
    "strategos_approval": {"admin", "pm", "isso", "co", "cor", "reviewer"},
    # ZIG mutating endpoints — capability-status PATCH + global assess POST
    # (nav-sec-05). Enforced via @require_role(*_ZIG_MUTATION_ROLES) in
    # tools/security_canvas/blueprint.py.
    "zig_mutation": {"admin", "isso", "ciso"},
}


def require_role(*roles):
    """Decorator to restrict access to specific roles."""

    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                abort(401)
            user_role = user["role"] if isinstance(user, dict) else user.get("role", "")
            if user_role not in roles:
                log_auth_event(
                    user.get("id", "unknown") if isinstance(user, dict) else user["id"],
                    "permission_denied",
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent", "")[:256],
                    details=f"required={roles}, had={user_role}",
                )
                abort(403)
            return f(*args, **kwargs)

        return decorated_function

    return decorator


# ---------------------------------------------------------------------------
# Flask integration — before_request + registration
# ---------------------------------------------------------------------------

# Public endpoints that don't require authentication
PUBLIC_ENDPOINTS = frozenset(
    {
        "login",
        "login_page",
        "static",
        "api_events.ingest_event",
        "api_events.healthcheck",
        "api_contact_submit",
        # Cortex service liveness probe (ctx-expose-02) — status only, no data.
        "cortex.api_v1_health",
        # Dashboard liveness probe (/api/health). app.py documents it as the
        # "P2 — monitoring / load-balancer" endpoint, but it was not public, so
        # it answered 401 to every caller that could not hold a session — i.e.
        # to every load balancer, container HEALTHCHECK and uptime monitor,
        # which is the entire set of intended callers. It returns
        # {"status", "db"} and nothing else, matching the status-only-no-data
        # rule the Cortex probe above is held to.
        "api_health",
    }
)

# Cortex service keys (external server-to-server callers) are honored only on
# these prefixes — see the icdev_ctx_ branch in _auth_before_request.
_CORTEX_SERVICE_KEY_PREFIX = "icdev_ctx_"
_CORTEX_SERVICE_PATHS = ("/cortex/api/v1/", "/api/databridge/v1/")


def _extract_api_key_from_request():
    """Extract API key from Authorization header or query param."""
    # Header: Authorization: Bearer icdev_dash_...
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    # Query param fallback (for SSE/WebSocket)
    return request.args.get("api_key", "")


def _attach_security_context():
    """Derive g.security_context from g.current_user (prop-sec-02).

    Must be called after g.current_user is set to an authenticated user
    dict. Without this, tools.security.classification_enforcer's
    @require_clearance/@require_compartment decorators and the Bell-LaPadula
    MAC helpers used across tools/dashboard/api/proposals.py and cpmp.py
    (_mac_ctx() et al.) always see g.security_context as unset and silently
    no-op (their "ctx is None -> allow" compatibility fallback), regardless
    of the authenticated user's actual clearance_level/compartments.
    """
    try:
        from tools.security.security_context import _extract_from_flask_g
        _extract_from_flask_g()
    except Exception:
        # Never let MAC-context derivation break the primary auth flow.
        pass


def _auth_before_request():
    """Flask before_request hook for authentication."""
    g.current_user = None

    # Defer /api/v1/* to the new JWT middleware (tools.dashboard.api.auth,
    # Phase C / P1.3). This hook stays authoritative for legacy /api/* and
    # Jinja page routes; it only steps aside for the versioned surface.
    if request.path.startswith("/api/v1/"):
        return None

    # Cortex service keys (icdev_ctx_ — tools/cortex/service_keys.py): scoped,
    # tenant-bound credentials for external server-to-server consumers
    # (compass, idea_lab) of the Cortex REST surface and DataBridge feeds
    # (ctx-expose-02/05). Resolved here so rest_v1's _server_context() reads
    # the same g.security_context seam it reads for session users; the key
    # row — never the request — supplies tenant/classification. Keys are only
    # honored on the two service prefixes: they are not dashboard users.
    service_key = _extract_api_key_from_request()
    if service_key.startswith(_CORTEX_SERVICE_KEY_PREFIX):
        if not request.path.startswith(_CORTEX_SERVICE_PATHS):
            abort(401)
        try:
            from tools.cortex.service_keys import resolve_context
            binding = resolve_context(service_key, None)
        except Exception:
            binding = None
        if binding is None:
            log_auth_event(
                None,
                "login_failed",
                ip_address=request.remote_addr,
                user_agent=request.headers.get("User-Agent", "")[:256],
                details="invalid_cortex_service_key",
            )
            abort(401)
        ctx = binding["ctx"]
        g.cortex_binding = binding
        g.current_user = {
            "id": f"cortex-svc:{binding['label']}",
            "display_name": f"Cortex service ({binding['label']})",
            "role": "service",
            "tenant_id": ctx.tenant_id,
            "clearance_level": ctx.classification,
        }
        g.security_context = {
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id or f"cortex-svc:{binding['label']}",
            "classification": ctx.classification,
        }
        return None

    # Skip auth for public endpoints
    if request.endpoint and request.endpoint in PUBLIC_ENDPOINTS:
        return None

    # Skip auth for static files
    if request.path.startswith("/static"):
        return None

    # Check session first (cookie-based, set after login)
    user_id = session.get("user_id")
    if user_id:
        user = get_user_by_id(user_id)
        if user and user["status"] == "active":
            g.current_user = dict(user)
            _attach_security_context()
            return None
        else:
            # Session invalid — clear it
            session.clear()
            log_auth_event(user_id, "session_expired")

    # Check API key in header/query
    raw_key = _extract_api_key_from_request()
    if raw_key:
        user = validate_api_key(raw_key)
        if user:
            g.current_user = dict(user)
            _attach_security_context()
            # Set session so subsequent requests use cookie
            session["user_id"] = user["id"]
            log_auth_event(
                user["id"],
                "login_success",
                ip_address=request.remote_addr,
                user_agent=request.headers.get("User-Agent", "")[:256],
                details="via_api_key",
            )
            return None
        else:
            # nav-sec-01: The .env bootstrap key (ICDEV_DASHBOARD_API_KEY,
            # written by _auto_provision_env_key on first install) may not yet
            # have a DB row, so validate_api_key() above returns None the first
            # time it is presented. Honor it here ONLY when the request actually
            # PRESENTS a key equal to it, compared in constant time, then
            # bootstrap its DB entry. Merely having the key set in the
            # environment must never authenticate a request on its own.
            env_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
            if env_key and hmac.compare_digest(raw_key, env_key):
                user = bootstrap_env_user(env_key)
                if user:
                    g.current_user = dict(user)
                    _attach_security_context()
                    session["user_id"] = user["id"]
                    log_auth_event(
                        user["id"],
                        "login_success",
                        ip_address=request.remote_addr,
                        user_agent=request.headers.get("User-Agent", "")[:256],
                        details="via_env_key",
                    )
                    return None

            # API requests get 401, browser requests redirect
            log_auth_event(
                None,
                "login_failed",
                ip_address=request.remote_addr,
                user_agent=request.headers.get("User-Agent", "")[:256],
                details="invalid_api_key",
            )
            if request.is_json or request.path.startswith("/api/"):
                abort(401)

    # nav-sec-01: Developer convenience ONLY — auto-login the admin env user
    # for requests that present NO credential. Off by default; must be
    # explicitly opted into via ICDEV_DASHBOARD_DEV_AUTOLOGIN. This restores the
    # legacy "env key set => everyone is admin" behavior, so it MUST NEVER be
    # enabled in a deployed or network-exposed environment — it is a full auth
    # bypass by design, intended for local single-user development only.
    dev_autologin = os.environ.get(
        "ICDEV_DASHBOARD_DEV_AUTOLOGIN", ""
    ).lower() in ("true", "1", "yes")
    if dev_autologin:
        env_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
        if env_key:
            user = bootstrap_env_user(env_key)
            if user:
                g.current_user = dict(user)
                _attach_security_context()
                session["user_id"] = user["id"]
                log_auth_event(
                    user["id"],
                    "login_success",
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent", "")[:256],
                    details="via_dev_autologin",
                )
                return None

    # Not authenticated — redirect to login for browser requests
    if request.is_json or request.path.startswith("/api/"):
        abort(401)
    return redirect(url_for("login_page"))


def _security_after_request(response):
    """Add security headers to all responses."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def _auto_provision_env_key():
    """Auto-generate ICDEV_DASHBOARD_API_KEY in .env if not set.

    On first install: creates admin user, generates key, appends to .env,
    and sets the env var for the current process. Prints key to console.

    Provisioning is a convenience, not an enforcement step, and it runs from
    register_dashboard_auth() -> create_app() -> module scope. So a database
    without the auth schema (a fresh checkout, or any test that merely imports
    tools.dashboard.app) made `import tools.dashboard.app` raise
    OperationalError: no such table: dashboard_users, taking whole test modules
    down at collection time.

    Degrading here is fail-closed: with no ICDEV_DASHBOARD_API_KEY the
    before_request hook treats the caller as unauthenticated and redirects or
    401s. Never swallow the registration of the auth middleware itself.
    """
    if os.environ.get("ICDEV_DASHBOARD_API_KEY"):
        return  # Already configured

    # Generate key and provision admin user
    email = "admin@icdev.local"
    try:
        conn = _get_db()
    except Exception as exc:  # DB unreachable — cannot provision, stay fail-closed
        logger.warning("Dashboard API key auto-provision skipped (no database): %s", exc)
        return

    if not _dashboard_users_table_exists(conn):
        conn.close()
        logger.warning(
            "Dashboard API key auto-provision skipped: dashboard_users table not found. "
            "Run the auth migrations, then restart to receive a generated key."
        )
        return

    try:
        row = conn.execute("SELECT id FROM dashboard_users WHERE email = %s", (email,)).fetchone()
        if row:
            user_id = row["id"]
        else:
            user_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO dashboard_users (id, email, display_name, role, created_by) VALUES (%s, %s, %s, %s, %s)",
                (user_id, email, "Admin", "admin", "auto_provision"),
            )
            conn.commit()
    finally:
        conn.close()

    key_info = create_api_key_for_user(user_id, label="auto_provision")
    raw_key = key_info["raw_key"]

    # Write to .env
    from pathlib import Path

    env_path = Path(DB_PATH).parent.parent / ".env"
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            if "ICDEV_DASHBOARD_API_KEY" not in content:
                with open(env_path, "a", encoding="utf-8") as f:
                    f.write("\n# ICDEV™ Dashboard API Key (auto-generated, change to rotate)\n")
                    f.write(f"ICDEV_DASHBOARD_API_KEY={raw_key}\n")
        else:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("# ICDEV™ Dashboard API Key (auto-generated, change to rotate)\n")
                f.write(f"ICDEV_DASHBOARD_API_KEY={raw_key}\n")
    except OSError:
        pass  # Can't write .env — print to console instead

    os.environ["ICDEV_DASHBOARD_API_KEY"] = raw_key
    print(f"[ICDEV™ Dashboard] Auto-generated API key: {raw_key}")
    print(f"[ICDEV™ Dashboard] Saved to {env_path}")
    print("[ICDEV™ Dashboard] To rotate: change ICDEV_DASHBOARD_API_KEY in .env and restart")


def register_dashboard_auth(app: Flask):
    """Register auth middleware on a Flask app.

    Sets ``app.secret_key`` from config (or generates one) and installs
    the ``before_request`` / ``after_request`` hooks.
    Auto-provisions an API key in .env on first run.
    """
    # Secret key for signed sessions (D171)
    if DASHBOARD_SECRET:
        app.secret_key = DASHBOARD_SECRET
    else:
        # Auto-generate — sessions won't survive restarts but that's OK for dev
        app.secret_key = secrets.token_hex(32)

    # Auto-provision API key on first install
    _auto_provision_env_key()

    app.before_request(_auth_before_request)
    app.after_request(_security_after_request)


# ---------------------------------------------------------------------------
# CLI bootstrap — create first admin user
# ---------------------------------------------------------------------------


def bootstrap_admin(email, display_name="Admin"):
    """Create the first admin user + API key via CLI.

    Returns (user_dict, raw_api_key).
    """
    user = create_user(email, display_name, role="admin", created_by="cli_bootstrap")
    key_info = create_api_key_for_user(user["id"], label="Bootstrap key")
    return user, key_info["raw_key"]


def bootstrap_env_user(raw_key):
    """Ensure .env API key has a matching DB entry. Returns user Row or None."""
    # Check if key already exists
    user = validate_api_key(raw_key)
    if user:
        return user
    # Create admin user + store the specific key
    email = "admin@icdev.local"
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM dashboard_users WHERE email = %s", (email,)).fetchone()
        if row:
            user_id = row["id"]
        else:
            user_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO dashboard_users (id, email, display_name, role, created_by) VALUES (%s, %s, %s, %s, %s)",
                (user_id, email, "Admin", "admin", "env_bootstrap"),
            )
        # Store the provided key
        key_id = str(uuid.uuid4())
        hashed = hash_api_key(raw_key)
        prefix = key_prefix(raw_key)
        conn.execute(
            "INSERT INTO dashboard_api_keys (id, user_id, key_hash, key_prefix, label) VALUES (%s, %s, %s, %s, %s)",
            (key_id, user_id, hashed, prefix, "env_key"),
        )
        conn.commit()
    finally:
        conn.close()
    return validate_api_key(raw_key)


def _cli_main():
    """CLI entry point for admin bootstrap."""
    import argparse

    parser = argparse.ArgumentParser(description="Dashboard auth management")
    sub = parser.add_subparsers(dest="command")

    # Create admin
    create_cmd = sub.add_parser("create-admin", help="Create admin user + API key")
    create_cmd.add_argument("--email", required=True, help="Admin email")
    create_cmd.add_argument("--name", default="Admin", help="Display name")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")

    # List users
    sub.add_parser("list-users", help="List all dashboard users")

    args = parser.parse_args()

    if args.command == "create-admin":
        user, raw_key = bootstrap_admin(args.email, args.name)
        print(f"Admin user created: {user['email']} (id: {user['id']})")
        print("API Key (save this — it won't be shown again):")
        print(f"  {raw_key}")
    elif args.command == "list-users":
        users = list_users()
        if not users:
            print("No dashboard users found.")
        for u in users:
            print(f"  {u['email']}  role={u['role']}  status={u['status']}  id={u['id']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
