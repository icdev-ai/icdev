# CUI // SP-CTI
"""File-based secret resolver — air-gap fallback.

Resolves ``file:secret_id`` refs by reading plaintext from
``{secret_files_root}/{secret_id}``.  The root is set by the operator
via ``args/databridge_config.yaml:secret_files_root`` or the
``DATABRIDGE_SECRET_FILES_ROOT`` environment variable.  Path traversal
is blocked: the resolved path must start with the resolved root.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import os
from pathlib import Path
from typing import Optional

logger = get_logger("databridge.resolvers.file")

_DEFAULT_ROOT = "/etc/strategos/secrets"


class SecretResolverError(Exception):
    """Raised when the file resolver cannot return a value."""


def resolve(secret_ref: str) -> str:
    """Resolve a ``file:secret_id`` reference.

    Args:
        secret_ref: Full reference including ``file:`` prefix,
            e.g. ``file:db_password`` or ``file:prod/api_key``.

    Returns:
        Stripped plaintext content of the secret file.

    Raises:
        SecretResolverError: if the root is not configured, the path
            fails the traversal check, or the file is missing/empty.
    """
    secret_id = secret_ref[len("file:"):]
    if not secret_id:
        raise SecretResolverError("Empty secret_id in file: reference")

    root = _get_secret_files_root()
    root_path = Path(root).resolve()

    # Block path traversal: resolved target must be under root
    target_path = (root_path / secret_id).resolve()
    try:
        target_path.relative_to(root_path)
    except ValueError:
        raise SecretResolverError(
            f"Path traversal detected: {secret_id!r} escapes root {str(root_path)!r}"
        )

    if not target_path.exists():
        raise SecretResolverError(
            f"Secret file not found: {str(target_path)!r} (secret_id={secret_id!r})"
        )

    if not target_path.is_file():
        raise SecretResolverError(
            f"Secret path is not a regular file: {str(target_path)!r}"
        )

    try:
        value = target_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SecretResolverError(
            f"Cannot read secret file {str(target_path)!r}: {exc}"
        ) from exc

    if not value:
        raise SecretResolverError(
            f"Secret file is empty: {str(target_path)!r} (secret_id={secret_id!r})"
        )

    return value


def _get_secret_files_root() -> str:
    """Return the secret files root from env or config; raise if not configured."""
    # Environment variable takes precedence
    env_root: Optional[str] = os.environ.get("DATABRIDGE_SECRET_FILES_ROOT")
    if env_root:
        return env_root

    # Fall back to args/databridge_config.yaml
    try:
        import yaml  # type: ignore[import-untyped]

        config_path = Path(__file__).resolve().parents[3] / "args" / "databridge_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
                root = cfg.get("secret_files_root", "").strip()
                if root:
                    return root
    except Exception:
        pass

    # No config found — use the default (standard air-gap path)
    logger.debug(
        "secret_files_root not configured; using default %r", _DEFAULT_ROOT
    )
    return _DEFAULT_ROOT
