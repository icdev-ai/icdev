# CUI // SP-CTI
"""SecretRef credential indirection layer (D-RAG-17).

Config references secrets by name, resolved at runtime. Prevents API keys
from being stored directly in YAML config files.

Formats:
    env:ANTHROPIC_API_KEY       → os.environ["ANTHROPIC_API_KEY"]
    file:/path/to/key           → read file content (stripped)
    aws:secret-name             → AWS Secrets Manager (via CSP abstraction)
    plain:value                 → literal value (dev/test only, logged as warning)

Integrates with tools/cloud/provider_factory.py for cloud secret backends.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import os
from pathlib import Path

logger = get_logger("icdev.rag.secret_ref")


def resolve_secret(ref: str) -> str:
    """Resolve a SecretRef to its value.

    Args:
        ref: Secret reference in format "prefix:value".
            - env:VAR_NAME → environment variable
            - file:/path/to/key → file content
            - aws:secret-name → AWS Secrets Manager
            - azure:vault/secret → Azure Key Vault
            - gcp:project/secret → GCP Secret Manager
            - plain:value → literal (dev/test only)

    Returns:
        Resolved secret value, or empty string if resolution fails.

    Raises:
        ValueError: If ref format is invalid.
    """
    if not ref or ":" not in ref:
        # Not a SecretRef — could be a literal value or empty
        return ref or ""

    prefix, _, value = ref.partition(":")

    if prefix == "env":
        return _resolve_env(value)
    elif prefix == "file":
        return _resolve_file(value)
    elif prefix == "aws":
        return _resolve_cloud(value, csp="aws")
    elif prefix == "azure":
        return _resolve_cloud(value, csp="azure")
    elif prefix == "gcp":
        return _resolve_cloud(value, csp="gcp")
    elif prefix == "oci":
        return _resolve_cloud(value, csp="oci")
    elif prefix == "plain":
        # SEC: plain: prefix restricted — only in dev/test environments
        env = os.environ.get("ICDEV_ENVIRONMENT", "development")
        if env in ("production", "staging"):
            logger.error(
                "SecretRef 'plain:' prefix BLOCKED in %s environment. Use env: or cloud secret manager instead.",
                env,
            )
            return ""
        logger.warning(
            "SecretRef using 'plain:' prefix — not recommended for production. "
            "Use env: or cloud secret manager instead."
        )
        return value
    else:
        # SEC: Unknown prefix — reject rather than pass through
        logger.error("SecretRef unknown prefix %r — rejected. Valid: env, file, aws, azure, gcp, oci, plain", prefix)
        return ""


def _resolve_env(var_name: str) -> str:
    """Resolve from environment variable."""
    val = os.environ.get(var_name, "")
    if not val:
        logger.debug("SecretRef env:%s not set", var_name)
    return val


def _resolve_file(file_path: str) -> str:
    """Resolve from file content.

    SEC: Path traversal protection — resolved path must be within allowed
    directories (project root, /etc/secrets, /run/secrets, or explicit
    ICDEV_SECRET_FILE_DIRS env var).
    """
    path = Path(file_path).resolve()

    # SEC: Block path traversal — enforce allowed directories
    base_dir = Path(__file__).resolve().parent.parent.parent
    allowed_dirs = [
        base_dir,
        Path("/etc/secrets"),
        Path("/run/secrets"),
    ]
    # Allow custom directories via env var (comma-separated)
    extra = os.environ.get("ICDEV_SECRET_FILE_DIRS", "")
    if extra:
        for d in extra.split(","):
            d = d.strip()
            if d:
                allowed_dirs.append(Path(d).resolve())

    if not any(_is_subpath(path, allowed) for allowed in allowed_dirs):
        logger.error(
            "SecretRef file:%s rejected — resolved path %s is outside allowed directories",
            file_path,
            path,
        )
        return ""

    if not path.exists():
        logger.debug("SecretRef file:%s not found", file_path)
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.debug("SecretRef file:%s read failed: %s", file_path, exc)
        return ""


def _is_subpath(path: Path, parent: Path) -> bool:
    """Check if path is under parent directory (safe comparison)."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_cloud(secret_name: str, csp: str = "aws") -> str:
    """Resolve from cloud secret manager via CSP abstraction (D225)."""
    try:
        from tools.cloud.provider_factory import CSPProviderFactory

        factory = CSPProviderFactory()
        secrets_provider = factory.get_secrets_provider()
        return secrets_provider.get_secret(secret_name) or ""
    except Exception as exc:
        logger.debug("SecretRef %s:%s failed: %s", csp, secret_name, exc)
        return ""


def resolve_config_secrets(config: dict) -> dict:
    """Recursively resolve all SecretRef values in a config dict.

    Walks the config dict and resolves any string value that matches
    a SecretRef pattern (contains ':' with known prefix).

    Args:
        config: Configuration dict potentially containing SecretRef values.

    Returns:
        New dict with all SecretRef values resolved.
    """
    known_prefixes = {"env", "file", "aws", "azure", "gcp", "oci", "plain"}

    def _resolve(val):
        if isinstance(val, str) and ":" in val:
            prefix = val.split(":", 1)[0]
            if prefix in known_prefixes:
                return resolve_secret(val)
        return val

    def _walk(obj):
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_walk(item) for item in obj]
        else:
            return _resolve(obj)

    return _walk(config)
