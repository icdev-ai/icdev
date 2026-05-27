# CUI // SP-CTI
"""DataBridge Connection Manager — CRUD for stored connection configs.

Provides helpers to look up, create, and update connection records in the
``db_connections`` table of icdev.db.  Also resolves secret references for
auth credentials via a pluggable resolver chain.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = get_logger("databridge.connection_manager")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SecretNotFoundError(Exception):
    """Raised when no resolver in the chain can locate a secret."""


# ---------------------------------------------------------------------------
# Secret resolver registry
# ---------------------------------------------------------------------------
# Each entry: prefix → callable(secret_ref: str) -> str
# A SecretResolverError from any resolver triggers the env fallback.

class SecretResolverError(Exception):
    """Raised when a backend-specific resolver fails."""


# ---------------------------------------------------------------------------
# Secret resolver classes
# ---------------------------------------------------------------------------


class VaultResolver:
    """HashiCorp Vault KV resolver.

    Resolves ``vault:path/to/secret#field`` refs via the hvac client.
    Reads VAULT_ADDR and VAULT_TOKEN from the environment.  Resolved secrets
    are cached in-process for 5 minutes (cache lives in the resolver module).
    """

    def resolve(self, secret_id: str) -> str:
        """Resolve a ``vault:`` prefixed secret reference.

        Args:
            secret_id: Full reference, e.g. ``vault:secret/db/prod#password``.

        Returns:
            Plaintext secret value (never empty).

        Raises:
            SecretResolverError: on misconfiguration, network error, or
                missing field.
        """
        try:
            from tools.databridge.resolvers.vault_resolver import (
                resolve as _vault_resolve,
            )
        except ImportError as exc:
            raise SecretResolverError(
                "hvac package is not installed — cannot use vault resolver. "
                "Install it: pip install hvac"
            ) from exc
        return _vault_resolve(secret_id)


class AWSSecretsResolver:
    """AWS Secrets Manager resolver.

    Resolves ``aws:secret-name[#json-key]`` refs via boto3.  Uses the
    GovCloud endpoint (us-gov-west-1) by default.  Credentials are sourced
    from environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
    or the EC2/ECS instance profile — never hardcoded.
    """

    def resolve(self, secret_id: str) -> str:
        """Resolve an ``aws:`` prefixed secret reference.

        Args:
            secret_id: Full reference, e.g. ``aws:prod/db/password`` or
                ``aws:prod/db/creds#password``.

        Returns:
            Plaintext secret value.

        Raises:
            SecretResolverError: on misconfiguration, network error, or
                missing key.
        """
        try:
            from tools.databridge.resolvers.aws_resolver import (
                resolve as _aws_resolve,
            )
        except ImportError as exc:
            raise SecretResolverError(
                "boto3 is not installed — cannot use aws resolver. "
                "Install it: pip install boto3"
            ) from exc
        return _aws_resolve(secret_id)


# ---------------------------------------------------------------------------
# Secret resolver registry
# ---------------------------------------------------------------------------
# Each entry: prefix → callable(secret_ref: str) -> str

SECRET_RESOLVERS: Dict[str, Callable[[str], str]] = {
    "vault": VaultResolver().resolve,
    "aws": AWSSecretsResolver().resolve,
}

# --- File resolver (air-gap fallback) ---
try:
    from tools.databridge.resolvers.file_resolver import resolve as _file_resolve

    SECRET_RESOLVERS["file"] = _file_resolve
except ImportError:
    pass


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_databridge_cfg() -> Dict[str, Any]:
    """Load args/databridge_config.yaml (gracefully returns {} on any error)."""
    try:
        import yaml  # type: ignore[import-untyped]

        config_path = Path(__file__).resolve().parents[2] / "args" / "databridge_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    except Exception:
        pass
    return {}


def _get_secret_backend() -> str:
    """Return the configured secret backend (env / vault / aws / file)."""
    env_override = os.environ.get("DATABRIDGE_SECRET_BACKEND")
    if env_override:
        return env_override
    return _load_databridge_cfg().get("secret_backend", "env")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_connection(
    connection_id: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a connection record by ID.

    Returns a dict with at least ``id``, ``connector_name``,
    ``config_yaml``, ``auth_secret_ref``, ``status`` keys, or None
    if not found.
    """
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        row = conn.execute("SELECT * FROM db_connections WHERE id = ?", (connection_id,)).fetchone()
        conn.close()
        if row:
            return dict(row)
        return None
    except Exception as exc:
        logger.error("get_connection(%s) failed: %s", connection_id, exc)
        return None


def update_connection(
    connection_id: str,
    updates: Dict[str, Any],
    db_path: Optional[str] = None,
) -> bool:
    """Update fields on an existing connection record.

    ``updates`` is a dict of column_name -> value.  Only known safe
    columns are written.
    """
    db = db_path or str(DB_PATH)
    allowed = {"status", "last_sync", "config_yaml", "auth_secret_ref", "name"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    values = list(filtered.values()) + [connection_id]
    try:
        conn = _get_conn(db)
        conn.execute(
            f"UPDATE db_connections SET {set_clause} WHERE id = ?",  # nosec B608 -- column names validated against allowlist above
            values,
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error("update_connection(%s) failed: %s", connection_id, exc)
        return False


def resolve_secret(auth_secret_ref: str) -> str:
    """Resolve a secret reference to its plaintext value.

    Prefix-based dispatch:
      - ``env:VAR_NAME``      → reads from environment variable
      - ``vault:path#field``  → HashiCorp Vault KV (hvac)
      - ``aws:name[#key]``    → AWS Secrets Manager (boto3, GovCloud)
      - ``file:secret_id``    → controlled path (air-gap fallback)

    For refs without a recognised prefix the active ``secret_backend``
    from ``args/databridge_config.yaml`` is used.

    Chain: primary backend → env fallback → SecretNotFoundError.

    Raises:
        SecretNotFoundError: when no resolver can supply a non-empty value.
    """
    if not auth_secret_ref:
        raise SecretNotFoundError("Empty secret reference")

    # Determine which backend prefix this ref carries
    matched_prefix: Optional[str] = None
    for prefix in ("env", *SECRET_RESOLVERS):
        if auth_secret_ref.startswith(f"{prefix}:"):
            matched_prefix = prefix
            break

    # ---- env: prefix — direct lookup, no further fallback -------------------
    if matched_prefix == "env":
        var_name = auth_secret_ref[4:]
        value = os.environ.get(var_name)
        if not value:
            raise SecretNotFoundError(f"Environment variable not set: {var_name!r}")
        return value

    # ---- known backend prefix (vault / aws / file) --------------------------
    if matched_prefix is not None and matched_prefix in SECRET_RESOLVERS:
        return _resolve_with_fallback(matched_prefix, auth_secret_ref)

    # ---- no prefix — consult config secret_backend -------------------------
    backend = _get_secret_backend()
    if backend == "env":
        # Treat the whole ref as an env var name
        value = os.environ.get(auth_secret_ref)
        if not value:
            raise SecretNotFoundError(
                f"Secret not found: env var {auth_secret_ref!r} is not set"
            )
        return value

    if backend in SECRET_RESOLVERS:
        # Prepend the backend prefix so the resolver can dispatch normally
        synthetic_ref = f"{backend}:{auth_secret_ref}"
        return _resolve_with_fallback(backend, synthetic_ref)

    # Unrecognised backend — log and raise immediately
    raise SecretNotFoundError(
        f"Unknown secret_backend {backend!r} for ref {auth_secret_ref!r}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_with_fallback(prefix: str, secret_ref: str) -> str:
    """Try the named backend resolver; fall through to env on failure.

    If the env fallback also misses, raises SecretNotFoundError.
    """
    try:
        value = SECRET_RESOLVERS[prefix](secret_ref)
        if not value:
            raise SecretResolverError(f"Backend '{prefix}' returned empty for {secret_ref!r}")
        return value
    except Exception as exc:
        logger.warning(
            "Backend '%s' failed for %r — trying env fallback: %s",
            prefix,
            secret_ref,
            exc,
        )

    # Build env var key from ref body: strip prefix, take last path component
    # before any '#', uppercase, replace non-alphanum with '_'
    ref_body = secret_ref[len(prefix) + 1:]  # strip "prefix:"
    key_part = ref_body.split("#")[0].split("/")[-1]
    env_key = "".join(c if c.isalnum() else "_" for c in key_part).upper()

    value = os.environ.get(env_key)
    if value:
        logger.debug("Env fallback succeeded for %r via %r", secret_ref, env_key)
        return value

    raise SecretNotFoundError(
        f"Secret not found: backend '{prefix}' failed and env var {env_key!r} is not set"
        f" (ref={secret_ref!r})"
    )


def _get_conn(db_path: str):  # noqa: ANN201
    """Return a DB connection (PostgreSQL or SQLite) with row_factory.

    Prefers get_connection() from storage so PostgreSQL is used in prod.
    Falls back to direct SQLite when storage layer is unavailable.
    """
    import sqlite3 as _sqlite3

    if db_path == str(DB_PATH):
        try:
            from tools.db.storage import get_connection as _gc
            conn = _gc()
            conn.row_factory = _sqlite3.Row
            return conn
        except Exception:
            pass  # storage unavailable — fall through to direct SQLite

    conn = _sqlite3.connect(str(db_path))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
