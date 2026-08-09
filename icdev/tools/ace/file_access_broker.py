# CUI // SP-CTI
"""ACE FileAccessBroker — scoped filesystem access for co-worker agents.

Validates that requested paths fall within the role's declared ``folder_access``
scopes before reading or writing.  Every operation is append-only audited to
``ace_audit_log``.

Usage (from StepExecutor)::

    broker = FileAccessBroker(spec.folder_access)
    content = broker.read("reports/summary.txt",
                          coworker_id=spec.coworker_id,
                          instance_id=instance_id)
    broker.write(".tmp/ace/out.txt", content,
                 coworker_id=spec.coworker_id,
                 instance_id=instance_id)

Role YAML declares scope via::

    folder_access:
      - path: "reports/"
        mode: "r"
      - path: ".tmp/ace/"
        mode: "rw"
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.file_access_broker")

def _find_repo_root() -> Path:
    """Resolve the ICDEV repo root by walking up from this file to the nearest
    directory containing the ``CLAUDE.md`` project marker.

    Robust to the canonical/legacy nesting split — ``icdev/tools/ace/...`` and
    ``tools/ace/...`` live at different depths, so a hardcoded ``parents[N]``
    index is wrong for one of the two copies (it resolved to ``C:\\AI`` instead
    of ``C:\\AI\\ICDev``, which made every agent-mode read/write miss the repo
    and report FileNotFoundError).
    """
    here = Path(__file__).resolve()
    for cand in (here.parent, *here.parents):
        if (cand / "CLAUDE.md").is_file() and (cand / "tools").is_dir():
            return cand
    # Fallback: best-effort two levels above the canonical location.
    return here.parents[3] if len(here.parents) > 3 else here.parent


_REPO_ROOT = _find_repo_root()
_DB_ENV = "ICDEV_ACE_DB_URL"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScopeViolationError(PermissionError):
    """Raised when a path falls outside the role's declared folder_access scopes."""


class ReadOnlyScopeError(PermissionError):
    """Raised when a write is attempted against a read-only scope."""


# ---------------------------------------------------------------------------
# FileAccessBroker
# ---------------------------------------------------------------------------


class FileAccessBroker:
    """Validates and executes scoped filesystem reads and writes.

    Args:
        folder_access: List of scope dicts from the role YAML, e.g.
            ``[{"path": "reports/", "mode": "r"}, {"path": ".tmp/ace/", "mode": "rw"}]``.
            All paths are resolved relative to the repo root.
        repo_root: Override for the repository root (test injection).
    """

    def __init__(
        self,
        folder_access: list[dict[str, Any]],
        repo_root: Path | None = None,
    ) -> None:
        self._scopes = list(folder_access or [])
        self._root = repo_root or _REPO_ROOT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, path: str, *, coworker_id: str, instance_id: str) -> str:
        """Read a file within the role's allowed scopes.

        Args:
            path:        Path relative to repo root (or absolute).
            coworker_id: Audit identity for the requesting co-worker.
            instance_id: ACE instance ID for audit trail.

        Returns:
            File contents as a UTF-8 string.

        Raises:
            ScopeViolationError: Path not within any declared read scope.
            FileNotFoundError:   File does not exist.
        """
        target = self._resolve(path, need_write=False)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {target}")
        content = target.read_text(encoding="utf-8")
        self._audit("file_read", path, coworker_id, instance_id, len(content))
        return content

    def write(
        self,
        path: str,
        content: str,
        *,
        coworker_id: str,
        instance_id: str,
    ) -> int:
        """Write content to a file within the role's ``rw`` scopes.

        Creates parent directories as needed.  Never deletes files — use
        ``write`` to overwrite, but only within explicitly declared ``rw`` scope.

        Args:
            path:        Path relative to repo root (or absolute).
            content:     UTF-8 text to write.
            coworker_id: Audit identity.
            instance_id: ACE instance ID for audit trail.

        Returns:
            Number of bytes written.

        Raises:
            ScopeViolationError:  Path not within any declared scope.
            ReadOnlyScopeError:   Matching scope exists but mode is ``"r"``.
        """
        target = self._resolve(path, need_write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        bytes_written = target.write_text(content, encoding="utf-8", newline="")
        self._audit("file_write", path, coworker_id, instance_id, bytes_written)
        return bytes_written

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def _resolve(self, path: str, need_write: bool) -> Path:
        """Resolve *path* and verify it falls within an appropriate scope."""
        target = Path(path)
        if not target.is_absolute():
            target = self._root / path
        target = target.resolve()

        # Prevent all traversal outside the repo root
        try:
            target.relative_to(self._root)
        except ValueError:
            raise ScopeViolationError(
                f"Path '{path}' resolves outside repository root '{self._root}'"
            )

        # Check against declared scopes
        for scope in self._scopes:
            scope_path = (self._root / scope.get("path", "")).resolve()
            mode = scope.get("mode", "r")
            try:
                target.relative_to(scope_path)
            except ValueError:
                continue  # not under this scope

            # Path matches this scope
            if need_write and mode != "rw":
                raise ReadOnlyScopeError(
                    f"Scope '{scope.get('path')}' is read-only (mode='r'); "
                    f"cannot write to '{path}'"
                )
            return target

        raise ScopeViolationError(
            f"'{path}' is not within any declared folder_access scope "
            f"({'rw' if need_write else 'r or rw'} required)"
        )

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _audit(
        self,
        action: str,
        path: str,
        coworker_id: str,
        instance_id: str,
        size: int,
    ) -> None:
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "INSERT INTO ace_audit_log "
                    "(instance_id, coworker_id, action, detail, actor, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        instance_id,
                        coworker_id,
                        action,
                        f"path={path} size={size}",
                        "file_access_broker",
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # audit must never crash the caller
            logger.warning("_audit: best-effort INSERT into ace_audit_log failed (non-blocking): %s", exc)
