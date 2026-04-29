# CUI // SP-CTI
"""File-based secret resolver for DataBridge (air-gap fallback).

Resolves secret refs of the form  file:secret_id
by reading plaintext from {secret_files_root}/{secret_id}.

Root path is operator-configured via args/databridge_config.yaml
(key: secret_files_root) or DATABRIDGE_SECRET_FILES_ROOT env var.
Default: /etc/strategos/secrets
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("databridge.resolvers.file")

_DEFAULT_ROOT = "/etc/strategos/secrets"


class SecretResolverError(Exception):
    """Raised when a secret reference cannot be resolved."""


def _get_root() -> Path:
    """Return the configured secret files root directory."""
    env_root = os.environ.get("DATABRIDGE_SECRET_FILES_ROOT")
    if env_root:
        return Path(env_root)

    try:
        import yaml  # type: ignore[import-untyped]

        config_path = (
            Path(__file__).resolve().parents[4] / "args" / "databridge_config.yaml"
        )
        if config_path.exists():
            with open(config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            root = cfg.get("secret_files_root")
            if root:
                return Path(root)
    except Exception:
        pass

    return Path(_DEFAULT_ROOT)


def resolve(secret_ref: str) -> str:
    """Resolve a ``file:secret_id`` reference to plaintext.

    Reads ``{secret_files_root}/{secret_id}``.  Path traversal is blocked
    by resolving to an absolute path and asserting it stays within root.

    Args:
        secret_ref: Reference of the form ``file:db_password``.

    Returns:
        Stripped plaintext content of the secret file (never empty).

    Raises:
        SecretResolverError: if traversal detected, file missing, unreadable,
                             or empty.
    """
    if not secret_ref.startswith("file:"):
        raise SecretResolverError(f"Not a file ref: {secret_ref!r}")

    secret_id = secret_ref[5:]
    if not secret_id:
        raise SecretResolverError(f"Empty secret_id in file ref: {secret_ref!r}")

    root = _get_root()
    secret_path = (root / secret_id).resolve()
    root_resolved = root.resolve()

    # Block path traversal (e.g. file:../../../etc/passwd)
    if not str(secret_path).startswith(str(root_resolved)):
        raise SecretResolverError(
            f"Path traversal detected in file ref: {secret_ref!r}"
        )

    if not secret_path.exists():
        raise SecretResolverError(f"Secret file not found: {secret_path}")

    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SecretResolverError(
            f"Cannot read secret file {secret_path}: {exc}"
        ) from exc

    if not value:
        raise SecretResolverError(f"Secret file {secret_path} is empty")

    return value
