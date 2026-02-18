#!/usr/bin/env python3
"""ICDEV SaaS Tenant Manager - Core tenant lifecycle management.

CUI // SP-CTI

Manages the full tenant lifecycle: create, provision, approve, list, get,
update, suspend, delete.  Handles user management and API key rotation
within tenants.  All operations are audit-logged to the platform database.

Usage:
    # Create a tenant
    python tools/saas/tenant_manager.py --create --name "ACME Defense" --il IL4 \
        --tier starter --admin-email admin@acme.gov --admin-name "Jane Doe"

    # Provision a pending tenant
    python tools/saas/tenant_manager.py --provision --tenant-id "tenant-xxx"

    # Approve and provision (IL5+/Enterprise)
    python tools/saas/tenant_manager.py --approve --tenant-id "tenant-xxx" --approver-id "user-yyy"

    # List tenants
    python tools/saas/tenant_manager.py --list --status active --json

    # Get tenant details
    python tools/saas/tenant_manager.py --get --tenant-id "tenant-xxx"

    # Suspend / delete
    python tools/saas/tenant_manager.py --suspend --tenant-id "tenant-xxx"
    python tools/saas/tenant_manager.py --delete --tenant-id "tenant-xxx"

    # User management
    python tools/saas/tenant_manager.py --add-user --tenant-id "tenant-xxx" \
        --email user@acme.gov --role viewer
    python tools/saas/tenant_manager.py --list-users --tenant-id "tenant-xxx"
"""

import argparse
import hashlib
import json
import re
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
TENANTS_DIR = DATA_DIR / "tenants"

# Ensure project root is importable
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Imports from sibling SaaS modules
# ---------------------------------------------------------------------------
from tools.saas.platform_db import get_platform_connection  # noqa: E402
from tools.saas.models import (  # noqa: E402
    TenantStatus,
    SubscriptionTier,
    ImpactLevel,
    TIER_LIMITS,
    UserRole,
    AuthMethod,
)

# Dynamic import of init_icdev_db for tenant database provisioning
from tools.db import init_icdev_db  # noqa: E402


# ============================================================================
# Helpers
# ============================================================================

def _slugify(name):
    """Convert a tenant name to a URL-safe slug.

    Examples:
        "ACME Defense"       -> "acme-defense"
        "My Corp (Gov)"     -> "my-corp-gov"
        "  Hello   World  " -> "hello-world"
        "Special!@#Chars"   -> "specialchars"
    """
    slug = name.lower().strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug


def _generate_api_key():
    """Generate an API key with icdev_ prefix.

    Returns:
        (full_key, prefix, key_hash) tuple where:
        - full_key: "icdev_" + 32 random hex chars (shown once to user)
        - prefix: first 8 chars after "icdev_" for identification
        - key_hash: SHA-256 hash of the full key (stored in DB)
    """
    random_hex = secrets.token_hex(16)
    full_key = "icdev_" + random_hex
    prefix = random_hex[:8]
    key_hash = hashlib.sha256(full_key.encode("utf-8")).hexdigest()
    return full_key, prefix, key_hash


def _utcnow():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_platform(conn, tenant_id, actor, action, resource_type,
                    resource_id=None, details=None):
    """Append an entry to the platform audit log (immutable/append-only)."""
    detail_obj = {"resource_type": resource_type, "actor": actor}
    if resource_id:
        detail_obj["resource_id"] = resource_id
    if details:
        detail_obj.update(details)
    conn.execute(
        """INSERT INTO audit_platform
           (tenant_id, event_type, action,
            details, ip_address, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            tenant_id,
            resource_type,
            action,
            json.dumps(detail_obj),
            "127.0.0.1",
            _utcnow(),
        ),
    )


def _tier_limits(tier_key):
    """Look up TIER_LIMITS by string key, handling enum-keyed dict."""
    for enum_key, limits in TIER_LIMITS.items():
        if enum_key.value == tier_key or enum_key == tier_key:
            return limits
    return {}


# ============================================================================
# Tenant CRUD
# ============================================================================

def create_tenant(name, impact_level, tier, admin_email, admin_name=None):
    """Create a new tenant with admin user and initial API key.

    For IL2-IL4 with Starter/Professional tier: auto-approves (status=provisioning).
    For IL5+/Enterprise: status stays pending (requires admin approval).

    Returns dict with tenant, user, and api_key (full key shown once).
    """
    il = impact_level.upper()
    tier_lower = tier.lower()

    if il not in [e.value for e in ImpactLevel]:
        raise ValueError(
            "Invalid impact level: {}. Valid: {}".format(
                il, [e.value for e in ImpactLevel]))
    if tier_lower not in [e.value for e in SubscriptionTier]:
        raise ValueError(
            "Invalid tier: {}. Valid: {}".format(
                tier_lower, [e.value for e in SubscriptionTier]))

    limits = _tier_limits(tier_lower)
    allowed_ils = limits.get("allowed_il_levels", [])
    if il not in allowed_ils:
        raise ValueError(
            "Tier '{}' does not support impact level {}. Allowed: {}".format(
                tier_lower, il, allowed_ils))

    slug = _slugify(name)
    if not slug:
        raise ValueError(
            "Cannot generate a valid slug from name: '{}'".format(name))

    tenant_id = "tenant-" + uuid.uuid4().hex[:12]
    user_id = "user-" + uuid.uuid4().hex[:12]
    key_id = "key-" + uuid.uuid4().hex[:12]
    subscription_id = "sub-" + uuid.uuid4().hex[:12]
    now = _utcnow()

    auto_approve = (
        il in ("IL2", "IL4") and tier_lower in ("starter", "professional"))
    initial_status = (
        TenantStatus.PROVISIONING.value if auto_approve
        else TenantStatus.PENDING.value)

    full_key, key_prefix, key_hash = _generate_api_key()

    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT id FROM tenants WHERE slug = ? AND status != 'deleted'",
            (slug,)).fetchone()
        if row:
            raise ValueError(
                "A tenant with slug '{}' already exists (id={}).".format(
                    slug, row[0]))

        conn.execute(
            """INSERT INTO tenants
               (id, name, slug, status, tier, impact_level,
                db_host, db_name, k8s_namespace, settings,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tenant_id, name, slug, initial_status, tier_lower, il,
             None, None, None, json.dumps({}), now, now))

        display_name = admin_name or admin_email.split("@")[0]
        conn.execute(
            """INSERT INTO users
               (id, tenant_id, email, display_name, role, auth_method,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, tenant_id, admin_email, display_name,
             UserRole.TENANT_ADMIN.value, AuthMethod.API_KEY.value,
             "active", now))

        conn.execute(
            """INSERT INTO api_keys
               (id, tenant_id, user_id, key_hash, key_prefix, name,
                status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key_id, tenant_id, user_id, key_hash, key_prefix,
             "initial-admin-key", "active", now, None))

        conn.execute(
            """INSERT INTO subscriptions
               (id, tenant_id, tier, status, max_projects, max_users,
                allowed_il_levels, allowed_frameworks,
                starts_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (subscription_id, tenant_id, tier_lower, "active",
             limits.get("max_projects", 5),
             limits.get("max_users", 3),
             json.dumps(limits.get("allowed_il_levels", ["IL2", "IL4"])),
             json.dumps(limits.get("allowed_frameworks", ["nist_800_53"])),
             now, now))

        _audit_platform(
            conn, tenant_id, user_id, "tenant.created", "tenant", tenant_id,
            details={
                "name": name, "slug": slug, "tier": tier_lower,
                "impact_level": il, "auto_approved": auto_approve,
                "admin_email": admin_email})

        conn.commit()

        result = {
            "tenant": {
                "id": tenant_id, "name": name, "slug": slug,
                "status": initial_status, "tier": tier_lower,
                "impact_level": il, "created_at": now},
            "user": {
                "id": user_id, "email": admin_email,
                "display_name": display_name,
                "role": UserRole.TENANT_ADMIN.value},
            "api_key": {
                "id": key_id, "key": full_key, "prefix": key_prefix,
                "note": "Save this key now. It cannot be retrieved later."}}

        if auto_approve:
            provision_result = provision_tenant(tenant_id)
            result["tenant"]["status"] = provision_result.get("status", "active")
            result["tenant"]["db_name"] = provision_result.get("db_name")
            result["tenant"]["k8s_namespace"] = provision_result.get("k8s_namespace")

        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def provision_tenant(tenant_id):
    """Provision infrastructure for a tenant.

    Creates the tenant's isolated SQLite database, initialises its schema
    using init_icdev_db.init_db(), and sets k8s_namespace.

    Requires tenant status to be 'provisioning'.
    """
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT id, slug, status, impact_level FROM tenants WHERE id = ?",
            (tenant_id,)).fetchone()
        if not row:
            raise ValueError("Tenant not found: {}".format(tenant_id))

        _, slug, status, il = row
        if status != TenantStatus.PROVISIONING.value:
            raise ValueError(
                "Tenant {} status is '{}', expected 'provisioning'. "
                "Use approve_tenant() first for IL5+/Enterprise tenants.".format(
                    tenant_id, status))

        TENANTS_DIR.mkdir(parents=True, exist_ok=True)
        db_name = slug + ".db"
        db_path = TENANTS_DIR / db_name
        init_icdev_db.init_db(db_path)

        k8s_namespace = "icdev-tenant-" + slug
        now = _utcnow()

        conn.execute(
            """UPDATE tenants
               SET db_host = ?, db_name = ?, k8s_namespace = ?,
                   status = ?, updated_at = ?
               WHERE id = ?""",
            ("localhost-sqlite", db_name, k8s_namespace,
             TenantStatus.ACTIVE.value, now, tenant_id))

        _audit_platform(
            conn, tenant_id, "system", "tenant.provisioned", "tenant",
            tenant_id, details={
                "db_path": str(db_path),
                "k8s_namespace": k8s_namespace,
                "impact_level": il})

        conn.commit()
        return {
            "id": tenant_id, "slug": slug,
            "status": TenantStatus.ACTIVE.value,
            "db_name": db_name, "db_path": str(db_path),
            "k8s_namespace": k8s_namespace}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def approve_tenant(tenant_id, approver_id):
    """Approve a pending tenant and trigger provisioning.

    Required for IL5+/Enterprise tenants that do not auto-approve.
    Sets approved_by, approved_at, transitions to provisioning, then provisions.
    """
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT id, status FROM tenants WHERE id = ?",
            (tenant_id,)).fetchone()
        if not row:
            raise ValueError("Tenant not found: {}".format(tenant_id))
        if row[1] != TenantStatus.PENDING.value:
            raise ValueError(
                "Tenant {} status is '{}', expected 'pending'.".format(
                    tenant_id, row[1]))

        now = _utcnow()
        conn.execute(
            """UPDATE tenants
               SET approved_by = ?, approved_at = ?,
                   status = ?, updated_at = ?
               WHERE id = ?""",
            (approver_id, now, TenantStatus.PROVISIONING.value, now, tenant_id))

        _audit_platform(
            conn, tenant_id, approver_id, "tenant.approved", "tenant",
            tenant_id, details={"approver_id": approver_id})

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return provision_tenant(tenant_id)


def list_tenants(status=None):
    """List all tenants, optionally filtered by status.

    Returns list of tenant dicts (excludes soft-deleted unless status='deleted').
    """
    conn = get_platform_connection()
    try:
        if status:
            rows = conn.execute(
                """SELECT id, name, slug, status, tier,
                          impact_level, db_host, db_name, k8s_namespace,
                          created_at, updated_at
                   FROM tenants WHERE status = ?
                   ORDER BY created_at DESC""",
                (status,)).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, name, slug, status, tier,
                          impact_level, db_host, db_name, k8s_namespace,
                          created_at, updated_at
                   FROM tenants WHERE status != 'deleted'
                   ORDER BY created_at DESC""").fetchall()

        columns = [
            "id", "name", "slug", "status", "tier",
            "impact_level", "db_host", "db_name", "k8s_namespace",
            "created_at", "updated_at"]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


def get_tenant(tenant_id):
    """Get full details for a single tenant, or None if not found."""
    conn = get_platform_connection()
    try:
        row = conn.execute(
            """SELECT id, name, slug, status, tier,
                      impact_level, db_host, db_name, k8s_namespace,
                      settings, artifact_config, bedrock_config, idp_config,
                      approved_by, approved_at, created_at, updated_at
               FROM tenants WHERE id = ?""",
            (tenant_id,)).fetchone()
        if not row:
            return None

        columns = [
            "id", "name", "slug", "status", "tier",
            "impact_level", "db_host", "db_name", "k8s_namespace",
            "settings", "artifact_config", "bedrock_config", "idp_config",
            "approved_by", "approved_at", "created_at", "updated_at"]
        tenant = dict(zip(columns, row))

        for json_field in ("settings", "artifact_config",
                           "bedrock_config", "idp_config"):
            val = tenant.get(json_field)
            if val and isinstance(val, str):
                try:
                    tenant[json_field] = json.loads(val)
                except json.JSONDecodeError:
                    pass
            elif val is None:
                tenant[json_field] = {}

        return tenant
    finally:
        conn.close()


def update_tenant(tenant_id, **kwargs):
    """Update tenant configuration fields.

    Allowed kwargs: settings, artifact_config, bedrock_config, idp_config, name.
    JSON fields are merged (not replaced) with existing values.
    """
    allowed_fields = {
        "settings", "artifact_config", "bedrock_config", "idp_config", "name"}
    json_fields = {
        "settings", "artifact_config", "bedrock_config", "idp_config"}
    invalid = set(kwargs.keys()) - allowed_fields
    if invalid:
        raise ValueError(
            "Cannot update fields: {}. Allowed: {}".format(
                invalid, allowed_fields))
    if not kwargs:
        raise ValueError("No fields provided to update.")

    conn = get_platform_connection()
    try:
        existing = conn.execute(
            """SELECT id, settings, artifact_config, bedrock_config,
                      idp_config
               FROM tenants WHERE id = ?""",
            (tenant_id,)).fetchone()
        if not existing:
            raise ValueError("Tenant not found: {}".format(tenant_id))

        col_map = {
            "settings": 1, "artifact_config": 2,
            "bedrock_config": 3, "idp_config": 4}

        updates = {}
        for field, value in kwargs.items():
            if field in json_fields:
                existing_val = existing[col_map[field]]
                if existing_val and isinstance(existing_val, str):
                    try:
                        existing_dict = json.loads(existing_val)
                    except json.JSONDecodeError:
                        existing_dict = {}
                else:
                    existing_dict = {}
                if isinstance(value, dict):
                    existing_dict.update(value)
                    updates[field] = json.dumps(existing_dict)
                else:
                    updates[field] = (
                        json.dumps(value) if not isinstance(value, str)
                        else value)
            else:
                updates[field] = value

        now = _utcnow()
        set_clause = ", ".join("{} = ?".format(k) for k in updates)
        set_clause += ", updated_at = ?"
        values = list(updates.values()) + [now, tenant_id]

        conn.execute(
            "UPDATE tenants SET {} WHERE id = ?".format(set_clause),
            values)

        _audit_platform(
            conn, tenant_id, "system", "tenant.updated", "tenant",
            tenant_id, details={"updated_fields": list(kwargs.keys())})

        conn.commit()
        return get_tenant(tenant_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def suspend_tenant(tenant_id):
    """Suspend a tenant.  Sets status to 'suspended'.  Reversible."""
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT id, status FROM tenants WHERE id = ?",
            (tenant_id,)).fetchone()
        if not row:
            raise ValueError("Tenant not found: {}".format(tenant_id))
        if row[1] == TenantStatus.DELETED.value:
            raise ValueError(
                "Cannot suspend a deleted tenant: {}".format(tenant_id))

        now = _utcnow()
        conn.execute(
            "UPDATE tenants SET status = ?, updated_at = ? WHERE id = ?",
            (TenantStatus.SUSPENDED.value, now, tenant_id))

        _audit_platform(
            conn, tenant_id, "system", "tenant.suspended", "tenant",
            tenant_id, details={"previous_status": row[1]})

        conn.commit()
        return {
            "id": tenant_id,
            "status": TenantStatus.SUSPENDED.value,
            "updated_at": now}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_tenant(tenant_id):
    """Soft-delete a tenant.  Sets status to 'deleted'.  Data is retained."""
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT id, status FROM tenants WHERE id = ?",
            (tenant_id,)).fetchone()
        if not row:
            raise ValueError("Tenant not found: {}".format(tenant_id))
        if row[1] == TenantStatus.DELETED.value:
            raise ValueError(
                "Tenant already deleted: {}".format(tenant_id))

        now = _utcnow()
        conn.execute(
            "UPDATE tenants SET status = ?, updated_at = ? WHERE id = ?",
            (TenantStatus.DELETED.value, now, tenant_id))

        conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE tenant_id = ?",
            (tenant_id,))

        conn.execute(
            """UPDATE users SET status = 'deactivated'
               WHERE tenant_id = ?""",
            (tenant_id,))

        _audit_platform(
            conn, tenant_id, "system", "tenant.deleted", "tenant",
            tenant_id,
            details={"previous_status": row[1], "soft_delete": True})

        conn.commit()
        return {
            "id": tenant_id,
            "status": TenantStatus.DELETED.value,
            "updated_at": now}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# Tenant Database Path
# ============================================================================

def get_tenant_db_path(tenant_id):
    """Return the filesystem path to a tenant's isolated database.

    For local development this is data/tenants/{slug}.db.
    """
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT slug, db_host, db_name FROM tenants WHERE id = ?",
            (tenant_id,)).fetchone()
        if not row:
            raise ValueError("Tenant not found: {}".format(tenant_id))

        slug, db_host, db_name = row
        if db_name:
            if db_host:
                return Path(db_host) / db_name
            return TENANTS_DIR / db_name
        return TENANTS_DIR / (slug + ".db")
    finally:
        conn.close()


# ============================================================================
# API Key Management
# ============================================================================

def rotate_api_key(key_id, user_id):
    """Revoke an existing API key and generate a new one.

    Returns the new key details (full key shown once).
    """
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT id, tenant_id, user_id, status FROM api_keys WHERE id = ?",
            (key_id,)).fetchone()
        if not row:
            raise ValueError("API key not found: {}".format(key_id))
        if row[2] != user_id:
            raise PermissionError(
                "User {} does not own key {}.".format(user_id, key_id))

        tenant_id = row[1]
        now = _utcnow()

        conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE id = ?",
            (key_id,))

        new_key_id = "key-" + uuid.uuid4().hex[:12]
        full_key, key_prefix, key_hash = _generate_api_key()

        conn.execute(
            """INSERT INTO api_keys
               (id, tenant_id, user_id, key_hash, key_prefix, name,
                status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_key_id, tenant_id, user_id, key_hash, key_prefix,
             "rotated-key", "active", now, None))

        _audit_platform(
            conn, tenant_id, user_id, "api_key.rotated", "api_key",
            new_key_id,
            details={"old_key_id": key_id, "new_key_prefix": key_prefix})

        conn.commit()
        return {
            "id": new_key_id, "key": full_key, "prefix": key_prefix,
            "revoked_key_id": key_id,
            "note": "Save this key now. It cannot be retrieved later."}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# User Management
# ============================================================================

def list_users(tenant_id):
    """List all users in a tenant."""
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT id FROM tenants WHERE id = ?",
            (tenant_id,)).fetchone()
        if not row:
            raise ValueError("Tenant not found: {}".format(tenant_id))

        rows = conn.execute(
            """SELECT id, tenant_id, email, display_name, role,
                      auth_method, status, last_login,
                      created_at
               FROM users WHERE tenant_id = ?
               ORDER BY created_at""",
            (tenant_id,)).fetchall()

        columns = [
            "id", "tenant_id", "email", "display_name", "role",
            "auth_method", "status", "last_login",
            "created_at"]
        return [dict(zip(columns, r)) for r in rows]
    finally:
        conn.close()


def add_user(tenant_id, email, display_name, role, auth_method="api_key"):
    """Add a user to a tenant.

    Validates role and auth_method against allowed enum values.
    Checks subscription user limits before adding.
    """
    role_lower = role.lower()
    valid_roles = [r.value for r in UserRole]
    if role_lower not in valid_roles:
        raise ValueError(
            "Invalid role: {}. Valid: {}".format(role_lower, valid_roles))

    auth_lower = auth_method.lower()
    valid_auth = [a.value for a in AuthMethod]
    if auth_lower not in valid_auth:
        raise ValueError(
            "Invalid auth method: {}. Valid: {}".format(
                auth_lower, valid_auth))

    conn = get_platform_connection()
    try:
        tenant_row = conn.execute(
            "SELECT id, status FROM tenants WHERE id = ?",
            (tenant_id,)).fetchone()
        if not tenant_row:
            raise ValueError("Tenant not found: {}".format(tenant_id))
        if tenant_row[1] not in (
                TenantStatus.ACTIVE.value,
                TenantStatus.PROVISIONING.value):
            raise ValueError(
                "Tenant {} status is '{}'. Users can only be added to "
                "active or provisioning tenants.".format(
                    tenant_id, tenant_row[1]))

        sub_row = conn.execute(
            """SELECT max_users FROM subscriptions
               WHERE tenant_id = ? AND status = 'active'""",
            (tenant_id,)).fetchone()
        if sub_row:
            max_users = sub_row[0]
            if max_users > 0:
                current_count = conn.execute(
                    """SELECT COUNT(*) FROM users
                       WHERE tenant_id = ? AND status = 'active'""",
                    (tenant_id,)).fetchone()[0]
                if current_count >= max_users:
                    raise ValueError(
                        "User limit reached ({}/{}). Upgrade subscription "
                        "tier to add more users.".format(
                            current_count, max_users))

        dup = conn.execute(
            """SELECT id FROM users
               WHERE tenant_id = ? AND email = ? AND status = 'active'""",
            (tenant_id, email)).fetchone()
        if dup:
            raise ValueError(
                "User with email '{}' already exists in tenant {}.".format(
                    email, tenant_id))

        user_id = "user-" + uuid.uuid4().hex[:12]
        now = _utcnow()

        conn.execute(
            """INSERT INTO users
               (id, tenant_id, email, display_name, role, auth_method,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, tenant_id, email, display_name, role_lower,
             auth_lower, "active", now))

        _audit_platform(
            conn, tenant_id, "system", "user.added", "user", user_id,
            details={"email": email, "role": role_lower})

        conn.commit()
        return {
            "id": user_id, "tenant_id": tenant_id,
            "email": email, "display_name": display_name,
            "role": role_lower, "auth_method": auth_lower,
            "status": "active", "created_at": now}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_user(tenant_id, user_id):
    """Deactivate a user in a tenant (soft removal).

    Does not delete the record - sets status='deactivated' and revokes API keys.
    """
    conn = get_platform_connection()
    try:
        row = conn.execute(
            """SELECT id, email, role, status FROM users
               WHERE id = ? AND tenant_id = ?""",
            (user_id, tenant_id)).fetchone()
        if not row:
            raise ValueError(
                "User {} not found in tenant {}.".format(
                    user_id, tenant_id))
        if row[3] == "deactivated":
            raise ValueError(
                "User {} is already deactivated.".format(user_id))

        now = _utcnow()

        conn.execute(
            """UPDATE users SET status = 'deactivated'
               WHERE id = ?""",
            (user_id,))

        conn.execute(
            """UPDATE api_keys SET status = 'revoked'
               WHERE user_id = ? AND tenant_id = ?""",
            (user_id, tenant_id))

        _audit_platform(
            conn, tenant_id, "system", "user.removed", "user", user_id,
            details={"email": row[1], "role": row[2]})

        conn.commit()
        return {
            "id": user_id, "email": row[1],
            "status": "deactivated", "deactivated_at": now}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# CLI
# ============================================================================

def _print_result(data, as_json=False):
    """Print result to stdout."""
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        if isinstance(data, list):
            for item in data:
                print("-" * 60)
                for k, v in item.items():
                    print("  {}: {}".format(k, v))
            print("\nTotal: {}".format(len(data)))
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    print("  {}:".format(k))
                    for kk, vv in v.items():
                        print("    {}: {}".format(kk, vv))
                else:
                    print("  {}: {}".format(k, v))
        else:
            print(data)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ICDEV SaaS Tenant Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--create", action="store_true", help="Create a new tenant")
    action.add_argument(
        "--provision", action="store_true",
        help="Provision a pending tenant")
    action.add_argument(
        "--approve", action="store_true",
        help="Approve and provision a tenant")
    action.add_argument(
        "--list", action="store_true", help="List tenants")
    action.add_argument(
        "--get", action="store_true", help="Get tenant details")
    action.add_argument(
        "--suspend", action="store_true", help="Suspend a tenant")
    action.add_argument(
        "--delete", action="store_true", help="Soft-delete a tenant")
    action.add_argument(
        "--add-user", action="store_true",
        help="Add a user to a tenant")
    action.add_argument(
        "--list-users", action="store_true",
        help="List users in a tenant")

    parser.add_argument("--tenant-id", type=str, help="Tenant ID")
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output as JSON")

    parser.add_argument("--name", type=str, help="Tenant name")
    parser.add_argument(
        "--il", type=str,
        help="Impact level (IL2, IL4, IL5, IL6)")
    parser.add_argument(
        "--tier", type=str,
        help="Subscription tier (starter, professional, enterprise)")
    parser.add_argument(
        "--admin-email", type=str, help="Admin email address")
    parser.add_argument(
        "--admin-name", type=str, default=None,
        help="Admin display name")

    parser.add_argument(
        "--approver-id", type=str, help="Approver user ID")

    parser.add_argument(
        "--status", type=str, default=None,
        help="Filter by status")

    parser.add_argument("--email", type=str, help="User email address")
    parser.add_argument(
        "--display-name", type=str, help="User display name")
    parser.add_argument(
        "--role", type=str,
        help="User role (tenant_admin, developer, "
             "compliance_officer, auditor, viewer)")
    parser.add_argument(
        "--auth-method", type=str, default="api_key",
        help="Auth method (api_key, oauth, cac_piv)")

    args = parser.parse_args()

    try:
        if args.create:
            if not all([args.name, args.il, args.tier, args.admin_email]):
                parser.error(
                    "--create requires --name, --il, --tier, "
                    "and --admin-email")
            result = create_tenant(
                name=args.name, impact_level=args.il,
                tier=args.tier, admin_email=args.admin_email,
                admin_name=args.admin_name)
            _print_result(result, args.as_json)

        elif args.provision:
            if not args.tenant_id:
                parser.error("--provision requires --tenant-id")
            result = provision_tenant(args.tenant_id)
            _print_result(result, args.as_json)

        elif args.approve:
            if not args.tenant_id or not args.approver_id:
                parser.error(
                    "--approve requires --tenant-id and --approver-id")
            result = approve_tenant(args.tenant_id, args.approver_id)
            _print_result(result, args.as_json)

        elif args.list:
            result = list_tenants(status=args.status)
            _print_result(result, args.as_json)

        elif args.get:
            if not args.tenant_id:
                parser.error("--get requires --tenant-id")
            result = get_tenant(args.tenant_id)
            if result is None:
                print("Tenant not found: {}".format(args.tenant_id))
                sys.exit(1)
            _print_result(result, args.as_json)

        elif args.suspend:
            if not args.tenant_id:
                parser.error("--suspend requires --tenant-id")
            result = suspend_tenant(args.tenant_id)
            _print_result(result, args.as_json)

        elif args.delete:
            if not args.tenant_id:
                parser.error("--delete requires --tenant-id")
            result = delete_tenant(args.tenant_id)
            _print_result(result, args.as_json)

        elif args.add_user:
            if not all([args.tenant_id, args.email, args.role]):
                parser.error(
                    "--add-user requires --tenant-id, --email, "
                    "and --role")
            display = args.display_name or args.email.split("@")[0]
            result = add_user(
                tenant_id=args.tenant_id, email=args.email,
                display_name=display, role=args.role,
                auth_method=args.auth_method)
            _print_result(result, args.as_json)

        elif args.list_users:
            if not args.tenant_id:
                parser.error("--list-users requires --tenant-id")
            result = list_users(args.tenant_id)
            _print_result(result, args.as_json)

    except (ValueError, PermissionError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("FATAL: {}".format(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
