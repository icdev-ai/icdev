#!/usr/bin/env python3
# CUI // SP-CTI
"""Scope-lock for the app red team (oss-redteam-02).

An HTTP red team is dual-use. It is only defensible as a **self-test against
systems we own**, and the difference between "security tool" and "attack tool"
is entirely this file. So the scope-lock is not a wrapper around the scanner —
the scanner cannot run without clearing it.

Four controls, none of which has a bypass:

1. **Hard target allowlist**, default localhost only. A non-allowlisted host is
   refused outright. There is deliberately no "warn and continue" path: a
   warning that can be ignored is not a control.
2. **A persisted, written authorization record per target**, checked before a
   run. Owning the host is not enough — someone has to have recorded that this
   host may be tested, and why.
3. **No general-scanner mode.** The catalog is fixed and first-party. There is
   no "point it at an arbitrary URL and see what sticks" entry point.
4. **No payload hoarding.** Nothing here stores an exploit payload beyond what a
   reproduction (oss-poc-01) actually requires to replay.

Public-repo constraint (ICDEV is public): this module handles *targets and
authorization*, never findings. Finding specifics — exploit paths, payloads,
auth-gap locations — go to the private triage path, never into a PR body, a
docs page, or a card description. See :func:`public_summary`.
"""
from __future__ import annotations

import ipaddress
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.security.redteam_scope")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

#: Loopback only, out of the box. Broadening this is the deliberate act that
#: authorises testing a non-local host, and it must be paired with an
#: authorization record — the allowlist alone does not grant permission.
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1")

#: Where authorization records live. Under data/, never committed — an
#: authorization to test a host is operational state, not source.
_AUTH_DIR = BASE_DIR / "data" / "redteam" / "authorizations"


class ScopeViolation(Exception):
    """A target was refused by the scope-lock. Raised, never downgraded."""


@dataclass
class RedTeamScope:
    """Resolved scope policy for a red-team run."""

    allowed_hosts: tuple = DEFAULT_ALLOWED_HOSTS
    require_authorization: bool = True
    max_requests_per_run: int = 500

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_hosts": list(self.allowed_hosts),
            "require_authorization": self.require_authorization,
            "max_requests_per_run": self.max_requests_per_run,
        }


def _host_allowed(host: str, allowed: tuple) -> bool:
    host = (host or "").lower()
    for a in allowed:
        a = a.lower()
        if host == a or host.endswith("." + a):
            return True
    return False


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class Authorization:
    """A recorded, written authorization to test one target."""

    target: str                          # host or host:port
    authorized_by: str
    reason: str
    authorized_at: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"rta-{uuid.uuid4().hex[:12]}"
        if not self.authorized_at:
            self.authorized_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _auth_path(target: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in target)
    return _AUTH_DIR / f"{safe}.json"


def record_authorization(
    target: str, authorized_by: str, reason: str
) -> Authorization:
    """Persist a written authorization for *target*.

    A record is required rather than inferred because "we own localhost" is not
    the same as "someone decided this run should happen". The reason is stored so
    an audit can ask *why* a host was in scope, not just whether it was.
    """
    if not authorized_by.strip() or not reason.strip():
        raise ValueError("authorization requires both authorized_by and a reason")
    auth = Authorization(target=target, authorized_by=authorized_by, reason=reason)
    _AUTH_DIR.mkdir(parents=True, exist_ok=True)
    _auth_path(target).write_text(json.dumps(auth.to_dict(), indent=2), encoding="utf-8")
    logger.info("recorded red-team authorization for %s by %s", target, authorized_by)
    return auth


def load_authorization(target: str) -> Optional[Authorization]:
    path = _auth_path(target)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Authorization(**data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unreadable authorization for %s: %s", target, exc)
        return None


def assert_in_scope(url: str, scope: Optional[RedTeamScope] = None) -> str:
    """Refuse *url* unless it clears every control. Returns the target string.

    The single choke point. Every request the scanner makes passes through here,
    and it RAISES on refusal — there is no return value that means "denied but
    proceeding", because that path is exactly what makes a red team an attack
    tool.
    """
    scope = scope or load_scope()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        raise ScopeViolation(f"no host in target url: {url!r}")

    if not _host_allowed(host, scope.allowed_hosts):
        raise ScopeViolation(
            f"host {host!r} is not on the red-team allowlist "
            f"({', '.join(scope.allowed_hosts)}). Add it deliberately and record "
            "an authorization; there is no warn-and-continue path."
        )

    target = f"{host}:{parts.port}" if parts.port else host

    if scope.require_authorization and not _is_loopback(host):
        auth = load_authorization(target) or load_authorization(host)
        if auth is None:
            raise ScopeViolation(
                f"no written authorization on file for {target!r}. Record one with "
                "record_authorization(target, authorized_by, reason) before testing "
                "a non-loopback host."
            )

    return target


def load_scope(config_path: Optional[Path] = None) -> RedTeamScope:
    """Resolve scope from args/redteam_scope.yaml, defaulting to loopback-only."""
    path = config_path or (BASE_DIR / "args" / "redteam_scope.yaml")
    if not path.exists():
        return RedTeamScope()
    try:
        import yaml

        cfg = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("redteam_scope", {})
    except Exception as exc:  # noqa: BLE001 - a broken config must fail SAFE
        logger.warning("scope config unreadable (%s); defaulting to loopback-only", exc)
        return RedTeamScope()

    hosts = tuple(cfg.get("allowed_hosts") or DEFAULT_ALLOWED_HOSTS)
    return RedTeamScope(
        allowed_hosts=hosts,
        require_authorization=bool(cfg.get("require_authorization", True)),
        max_requests_per_run=int(cfg.get("max_requests_per_run", 500)),
    )


# ── Public-surface redaction (oss-redteam-02) ────────────────────────────────


_SENSITIVE_KEYS = {
    "payload", "exploit", "request", "response", "reproduction", "steps",
    "path", "route", "url", "location", "evidence", "detail", "curl",
}


def public_summary(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A redacted, count-only summary safe for a PR body or docs page.

    ICDEV is public. A finding's specifics — where the gap is, how to reach it,
    the payload that triggers it — are a roadmap for the next person, so they
    never leave the private triage path. What is safe to say publicly is *how
    many, of what severity*, and nothing that localises a defect.
    """
    by_sev: Dict[str, int] = {}
    for f in findings:
        sev = str(f.get("severity", "unknown")).lower()
        by_sev[sev] = by_sev.get(sev, 0) + 1
    return {
        "total_findings": len(findings),
        "by_severity": by_sev,
        "note": (
            "Specifics (paths, payloads, reproductions) are withheld from public "
            "artifacts and held in the private triage path per oss-redteam-02."
        ),
    }


def assert_no_sensitive_fields(text: str) -> None:
    """Guard a string bound for a public surface.

    Raises if it contains a serialised finding with sensitive keys. Cheap and
    conservative on purpose — a false positive costs a rephrase, a false
    negative leaks an auth-gap location into a public repo.
    """
    lowered = text.lower()
    hits = [k for k in _SENSITIVE_KEYS if f'"{k}"' in lowered]
    if hits:
        raise ValueError(
            f"refusing to emit to a public surface: contains {sorted(hits)} — "
            "route finding specifics to the private triage path"
        )


def private_triage_path() -> Path:
    """Where finding specifics go — under data/, gitignored, never public."""
    p = Path(os.environ.get("ICDEV_REDTEAM_TRIAGE_DIR", str(BASE_DIR / "data" / "redteam" / "triage")))
    p.mkdir(parents=True, exist_ok=True)
    return p
