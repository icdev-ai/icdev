# CUI // SP-CTI
"""Authorized-target gate for external servers that analyse ARTIFACTS (rea-wire-01).

A server whose registry entry carries ``authorization: {required: true}`` — today
only ``rea``, the reverse-engineering server — refuses every call unless the
operator has authorized the exact bytes under analysis. Three rules:

1. **The basis is the operator's, never the agent's.** It is granted in code by
   :func:`TargetGrants.grant` (an operator action, or the adapter acting for
   one), keyed by the target's sha256. It is never read from tool arguments, so
   a model cannot authorize a target by asserting ``"basis": "owned"`` in a
   call it composes itself.
2. **The grant names BYTES, not a path.** The registry hashes whatever file the
   call names at call time and looks that digest up. A file swapped after the
   grant has a different digest and is refused.
3. **Every call leaves one provenance row**, ``re_analysis.authorized`` with the
   target sha256(s), the basis and the operator, or ``re_analysis.refused`` with
   the reason. A call whose authorized row cannot be written is REFUSED: an RE
   run with no record of why it was allowed is the thing this rail forbids.

A call that names no target (``procedure_pseudo_code`` on the open document)
is allowed only once an authorized target has been opened on that server
process, and its row cites those targets. Calling one before anything was
opened is refused, not waved through as "nothing to check".

Stdlib only; imports nothing from the server it guards.
"""
from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: The bases an operator may declare. ``args/external_mcp_servers.yaml`` may
#: narrow this per server; it can never widen it.
DEFAULT_BASES = ("owned", "licensed", "customer-authorized")

#: Directory targets (a JS ``dist/``) are digested as a tree. Bounded so a
#: mistaken ``/`` is refused rather than hashed for an hour.
MAX_TREE_FILES = 20000

EVENT_AUTHORIZED = "re_analysis.authorized"
EVENT_REFUSED = "re_analysis.refused"


class TargetUnreadable(ValueError):
    """The named target could not be digested."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_digest(path: str | os.PathLike) -> str:
    """sha256 of a file, or of a directory tree (sorted ``relpath\\0sha256`` lines)."""
    target = Path(path).expanduser()
    try:
        if target.is_file():
            return _sha256_file(target)
        if target.is_dir():
            files = sorted(p for p in target.rglob("*") if p.is_file())
            if len(files) > MAX_TREE_FILES:
                raise TargetUnreadable(
                    f"{target} holds more than {MAX_TREE_FILES} files; name a narrower target"
                )
            tree = hashlib.sha256()
            for item in files:
                rel = item.relative_to(target).as_posix()
                tree.update(f"{rel}\0{_sha256_file(item)}\n".encode("utf-8"))
            return tree.hexdigest()
    except OSError as exc:
        raise TargetUnreadable(f"{target}: {exc}") from exc
    raise TargetUnreadable(f"{target} is not a readable file or directory")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operator() -> str:
    for key in ("ICDEV_OPERATOR", "USER", "USERNAME"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return "unknown"


class TargetGrants:
    """Operator grants, keyed ``(server, sha256)``. Process-local by design."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def grant(
        self,
        server: str,
        target: str | os.PathLike,
        *,
        basis: str,
        operator: Optional[str] = None,
        note: str = "",
        bases: tuple[str, ...] = DEFAULT_BASES,
    ) -> dict:
        """Authorize the bytes at *target* for *server*. Raises on a bad basis."""
        basis_text = str(basis or "").strip().lower()
        if basis_text not in bases:
            raise ValueError(f"authorization basis {basis!r} is not one of {list(bases)}")
        record = {
            "server": server,
            "target_sha256": target_digest(target),
            "target_name": Path(target).name,
            "basis": basis_text,
            "operator": operator or _operator(),
            "note": str(note or "")[:500],
            "granted_at": _now(),
        }
        with self._lock:
            self._grants[(server, record["target_sha256"])] = record
        return dict(record)

    def lookup(self, server: str, sha256: str) -> Optional[dict]:
        with self._lock:
            found = self._grants.get((server, sha256))
        return dict(found) if found else None

    def revoke(self, server: str, sha256: str) -> bool:
        with self._lock:
            return self._grants.pop((server, sha256), None) is not None


def _target_values(spec: dict, tool: str, arguments: dict) -> list[str]:
    """Argument values that name an artifact under analysis for *tool*."""
    auth = spec.get("authorization") or {}
    if tool in set(auth.get("non_target_tools") or []):
        return []
    keys = list(auth.get("target_arguments") or [])
    out: list[str] = []
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def decide(
    spec: dict,
    tool: str,
    arguments: dict,
    grants: TargetGrants,
    session_targets: list[dict],
) -> dict:
    """Allow or refuse one call. Pure apart from hashing the named targets."""
    server = str(spec.get("name") or "")
    base = {"server": server, "tool": tool}
    targets = _target_values(spec, tool, arguments)

    if not targets:
        if not session_targets:
            return {**base, "allowed": False, "reason": "no_authorized_target_open",
                    "message": (f"{tool!r} acts on the open session, and no authorized "
                                f"target has been opened on server {server!r}")}
        return {**base, "allowed": True, "targets": [dict(t) for t in session_targets],
                "opens_target": False}

    records: list[dict] = []
    for value in targets:
        try:
            sha = target_digest(value)
        except TargetUnreadable as exc:
            return {**base, "allowed": False, "reason": "target_unreadable", "message": str(exc)}
        granted = grants.lookup(server, sha)
        if granted is None:
            return {**base, "allowed": False, "reason": "no_authorization_basis",
                    "target_sha256": sha,
                    "message": (f"target {Path(value).name!r} (sha256 {sha[:12]}…) has no "
                                f"operator authorization basis for server {server!r}; "
                                "grant one (owned | licensed | customer-authorized) first")}
        records.append(granted)
    return {**base, "allowed": True, "targets": records, "opens_target": True}


def audit_recorder(event_type: str, action: str, details: dict, classification: str) -> int:
    """Default provenance writer: one hash-chained ``audit_trail`` row."""
    from icdev.tools.audit.audit_logger import log_event

    return log_event(
        event_type=event_type,
        actor="mcp_client",
        action=action,
        details=details,
        classification=classification,
        raise_on_error=True,
    )


Recorder = Callable[[str, str, dict, str], Any]


def record(decision: dict, recorder: Recorder, classification: str) -> bool:
    """Write the provenance row for *decision*. False when it could not be written."""
    allowed = bool(decision.get("allowed"))
    event = EVENT_AUTHORIZED if allowed else EVENT_REFUSED
    details = {
        "server": decision.get("server"),
        "tool": decision.get("tool"),
        "reason": decision.get("reason"),
        "targets": [
            {k: t.get(k) for k in ("target_sha256", "target_name", "basis", "operator", "granted_at")}
            for t in (decision.get("targets") or [])
        ] or None,
        "refused_target_sha256": decision.get("target_sha256"),
        "recorded_at": _now(),
    }
    action = (f"{'authorized' if allowed else 'refused'} {decision.get('server')}:"
              f"{decision.get('tool')}")
    try:
        result = recorder(event, action, details, classification)
    except Exception as exc:  # noqa: BLE001 — reported, and refuses the call below
        logger.warning("mcp_client: provenance row not written (%s)", exc)
        return False
    return not (isinstance(result, int) and result < 0)
