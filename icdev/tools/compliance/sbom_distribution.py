#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ SBOM Distribution and Delivery — version-specific retrieval (sbx-gov-02).

The 2026 Minimum Elements for a Software Bill of Materials names Distribution
and Delivery as a practice: "SBOMs should be available promptly to those who
need them." It lists the acceptable mechanisms — accompanying the installation,
a **version-specific URL**, an API to a database, or a public repository — and
folds the retired 2021 Access Control element into it with an explicit limit:
access controls **may** limit sharing with unauthorized parties, but must not
prevent sharing between authorized parties, nor stop an organization from
integrating SBOM data into trusted security tooling.

ICDEV wrote a file to disk and stored its path in ``sbom_records``. A path on
the generating host is not a delivery mechanism — nobody outside that host can
follow it. This module is the retrieval half: it resolves a version-specific
address to exactly one ``sbom_records`` row and returns that artifact's bytes
unmodified, under an access decision that is deliberately narrow.

**Where the line is drawn.** Three things can withhold an SBOM here, and only
three:

1. the caller is unauthenticated (401);
2. the caller's role has no software-supply-chain need (403). The allowed set
   is deliberately wide — every engineering, security, compliance and
   contracting role, plus service accounts, because "trusted security tools"
   authenticate as service accounts and blocking them is the failure the
   standard calls out by name;
3. the artifact's classification is not dominated by the caller's clearance,
   or it belongs to another tenant (403).

(3) is the legitimate withholding case the task description protects: ICDEV
SBOMs carry ``icdev:classification`` (CUI // SP-CTI) and ``icdev:distribution``
(Distribution D) properties, and a CUI artifact does not become releasable
because the requester happens to hold a login. Nothing else withholds. In
particular there is no allowlist of individual projects, no per-record share
toggle, and no "internal only" flag — each of those would be a mechanism for
preventing sharing between authorized parties, which is precisely what the
2026 wording forbids.

Every decision, grant and denial alike, is written to the append-only audit
trail. A denial that leaves no trace is indistinguishable from an outage, and
the whole point of folding Access Control into Distribution and Delivery is
that withholding has to be accountable.

Library use::

    from tools.compliance.sbom_distribution import (
        resolve_record, evaluate_access, read_artifact_bytes, retrieval_url,
    )

CLI::

    python tools/compliance/sbom_distribution.py --list [--project-id P] [--json]
    python tools/compliance/sbom_distribution.py --project-id P --version 2.0 --json
    python tools/compliance/sbom_distribution.py --record-id 7 --out ./sbom.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.compliance.sbom_distribution")

# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------

#: Mounted by ``tools/supply_chain/blueprint.py``. Kept here rather than in the
#: blueprint so the generator can embed the URL it will be served from without
#: importing Flask.
RETRIEVAL_PREFIX = "/api/supply_chain/sbom"

#: MIME types by ``sbom_records.format``. Both named formats are JSON on the
#: wire; the distinct types let a consumer dispatch without sniffing.
_MEDIA_TYPES = {
    "cyclonedx": "application/vnd.cyclonedx+json",
    "spdx": "application/spdx+json",
}
DEFAULT_MEDIA_TYPE = "application/json"


def base_url() -> str:
    """The externally reachable origin of this ICDEV instance."""
    return os.environ.get("ICDEV_BASE_URL", "http://localhost:5050").rstrip("/")


def retrieval_path(project_id: str, version: str) -> str:
    """The version-specific path for one project/version pair.

    Both segments are percent-encoded with ``safe=''`` so a project id or
    version containing ``/`` addresses one segment rather than silently
    reshaping the route.
    """
    return (
        f"{RETRIEVAL_PREFIX}/"
        f"{quote(str(project_id), safe='')}/"
        f"{quote(str(version), safe='')}"
    )


def retrieval_url(project_id: str, version: str, *, absolute: bool = True) -> str:
    """The version-specific URL the 2026 element asks for.

    Absolute by default: the URL is meant to travel — into the SBOM document
    itself, into a compliance package, to a customer — and a relative path is
    not a delivery mechanism for any of those.
    """
    path = retrieval_path(project_id, version)
    return f"{base_url()}{path}" if absolute else path


def record_url(record_id: int, *, absolute: bool = True) -> str:
    """The row-keyed permalink, for when the version string is not to hand."""
    path = f"{RETRIEVAL_PREFIX}/record/{int(record_id)}"
    return f"{base_url()}{path}" if absolute else path


# ---------------------------------------------------------------------------
# Access decision
# ---------------------------------------------------------------------------

#: Roles that may retrieve an SBOM artifact.
#:
#: Read this as the standard does — the question is not "who owns SBOMs" but
#: "who is an authorized party", and the answer covers everyone with a software
#: supply-chain need plus the machine callers that integrate SBOM data into
#: security tooling. ``service`` is here for exactly that reason.
#:
#: Excluded: ``bd``, ``capture_mgr``, ``contract_mgr`` and ``reviewer`` — the
#: business-development and proposal-review roles, which have no supply-chain
#: function. Membership is a need determination, not a seniority ladder.
#: Keep in sync with ``tools/dashboard/auth.py::VALID_DASHBOARD_ROLES``.
SBOM_RETRIEVAL_ROLES = frozenset(
    {
        "admin",
        "isso",
        "ciso",
        "auditor",
        "pm",
        "developer",
        "co",
        "cor",
        "component_admin",
        "migration_engineer",
        "service",
    }
)

# Denial reasons. Stable identifiers — the dashboard and the audit trail both
# key on them, and a test asserts the deny leg by reason rather than by prose.
REASON_OK = "authorized"
REASON_UNAUTHENTICATED = "unauthenticated"
REASON_ROLE_NOT_AUTHORIZED = "role_not_authorized"
REASON_CLASSIFICATION_WITHHELD = "classification_withheld"
REASON_TENANT_WITHHELD = "tenant_withheld"


@dataclass(frozen=True)
class AccessDecision:
    """The outcome of one retrieval attempt."""

    allowed: bool
    status: int
    reason: str
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _user_field(user: Any, *names: str, default: str = "") -> str:
    """Read the first present field from a user mapping or row object."""
    for name in names:
        try:
            value = user[name] if hasattr(user, "__getitem__") else getattr(user, name)
        except (KeyError, IndexError, TypeError, AttributeError):
            value = getattr(user, name, None)
        if value:
            return str(value)
    return default


def _dominated(clearance: str) -> set:
    """Classification labels a caller at ``clearance`` may read (Bell-LaPadula).

    Read-down, never read-up — the same helper the storage layer's RLS
    predicate uses, so a caller sees the same set here as they would through a
    security-context-bearing connection.
    """
    from tools.security.security_context import classifications_dominated_by

    return {c.upper() for c in classifications_dominated_by(clearance or "CUI")}


def evaluate_access(record: dict, user: Any) -> AccessDecision:
    """Decide whether ``user`` may retrieve ``record``'s artifact.

    ``record`` is an ``sbom_records`` row (a mapping). ``user`` is the
    dashboard's ``g.current_user`` mapping, or ``None`` when unauthenticated.
    """
    if not user:
        return AccessDecision(
            False, 401, REASON_UNAUTHENTICATED,
            "authentication required to retrieve an SBOM artifact",
        )

    role = _user_field(user, "role").strip().lower()
    if role not in SBOM_RETRIEVAL_ROLES:
        return AccessDecision(
            False, 403, REASON_ROLE_NOT_AUTHORIZED,
            f"role '{role or 'unknown'}' has no software supply-chain need for SBOM data",
        )

    # Classification: the withholding case the 2026 element preserves.
    marking = (record.get("classification") or "CUI").strip().upper()
    clearance = _user_field(user, "clearance_level", "classification", default="CUI")
    if marking not in _dominated(clearance):
        return AccessDecision(
            False, 403, REASON_CLASSIFICATION_WITHHELD,
            f"artifact is marked {marking}; caller clearance {clearance.upper()} does not dominate it",
        )

    # Tenant: a NULL tenant on the record is a single-tenant/legacy artifact and
    # stays visible, matching the storage layer's nullable tenant_id default.
    record_tenant = (record.get("tenant_id") or "").strip()
    caller_tenant = _user_field(user, "tenant_id").strip()
    if record_tenant and caller_tenant and record_tenant != caller_tenant:
        return AccessDecision(
            False, 403, REASON_TENANT_WITHHELD,
            "artifact belongs to another tenant",
        )

    return AccessDecision(True, 200, REASON_OK, "")


# ---------------------------------------------------------------------------
# Record lookup
# ---------------------------------------------------------------------------

_RECORD_COLUMNS = (
    "s.id, s.project_id, s.version, s.format, s.file_path, "
    "s.component_count, s.vulnerability_count, s.generated_at, "
    "s.sbom_version, s.serial_number, s.author_signature, "
    "s.signature_algorithm, s.classification, s.tenant_id"
)


def _rows(cur) -> list:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def resolve_record(conn, project_id: str = None, version: str = None,
                   record_id: int = None) -> Optional[dict]:
    """Resolve a version-specific address to exactly one ``sbom_records`` row.

    ``record_id`` addresses the row directly. Otherwise ``project_id`` plus
    ``version`` is matched against the 2026 ``sbom_version`` element **first**
    and the legacy ``version`` column second — one query each rather than an OR,
    so which column answered is never ambiguous. Today the generator writes
    ``version`` and leaves ``sbom_version`` NULL; when sbx-fld-01 starts writing
    it, the same URL keeps resolving and starts preferring the standard's field.

    Returns None when nothing matches. Ties (two rows, same version) resolve to
    the most recently generated one.
    """
    if record_id is not None:
        rows = _rows(conn.execute(
            f"SELECT {_RECORD_COLUMNS} FROM sbom_records s WHERE s.id = %s",
            (int(record_id),),
        ))
        return rows[0] if rows else None

    if not project_id or version is None:
        return None

    for column in ("sbom_version", "version"):
        rows = _rows(conn.execute(
            f"SELECT {_RECORD_COLUMNS} FROM sbom_records s "
            f"WHERE s.project_id = %s AND s.{column} = %s "
            "ORDER BY s.generated_at DESC, s.id DESC LIMIT 1",
            (str(project_id), str(version)),
        ))
        if rows:
            return rows[0]
    return None


def list_records(conn, project_id: str = None, limit: int = 100) -> list:
    """SBOM records newest first, each carrying its version-specific URL."""
    sql = (
        f"SELECT {_RECORD_COLUMNS}, p.name AS project_name "
        "FROM sbom_records s LEFT JOIN projects p ON p.id = s.project_id "
    )
    params: tuple = ()
    if project_id:
        sql += "WHERE s.project_id = %s "
        params = (str(project_id),)
    sql += "ORDER BY s.generated_at DESC, s.id DESC LIMIT %s"
    params = params + (int(limit),)
    return [describe_record(r) for r in _rows(conn.execute(sql, params))]


def describe_record(record: dict) -> dict:
    """Annotate a row with its retrieval addresses and delivery-readiness.

    ``retrievable`` answers "would a fully authorized caller get bytes from
    this URL right now" — it is False when the artifact is missing from disk,
    which is the one Distribution and Delivery failure a listing can detect
    without attempting the fetch.
    """
    enriched = dict(record)
    version = record.get("sbom_version") or record.get("version")
    enriched["sbom_version_effective"] = version
    enriched["retrieval_url"] = (
        retrieval_url(record.get("project_id"), version) if version else None
    )
    enriched["record_url"] = record_url(record["id"]) if record.get("id") else None
    enriched["media_type"] = media_type(record)
    enriched["signed"] = bool(record.get("author_signature"))
    path = record.get("file_path")
    enriched["retrievable"] = bool(path) and Path(path).is_file()
    return enriched


def media_type(record: dict) -> str:
    return _MEDIA_TYPES.get((record.get("format") or "").strip().lower(), DEFAULT_MEDIA_TYPE)


def filename_for(record: dict) -> str:
    """A stable, version-specific download name.

    Derived from the record, not from ``file_path``: the on-disk name carries a
    generation timestamp, which makes two downloads of the same version look
    like different artifacts.
    """
    version = record.get("sbom_version") or record.get("version") or "unknown"
    fmt = (record.get("format") or "cyclonedx").strip().lower()
    suffix = "spdx.json" if fmt == "spdx" else "cdx.json"
    safe = "".join(c if (c.isalnum() or c in "._-") else "_"
                   for c in f"{record.get('project_id', 'sbom')}_{version}")
    return f"sbom_{safe}.{suffix}"


# ---------------------------------------------------------------------------
# Artifact bytes
# ---------------------------------------------------------------------------

class ArtifactUnavailable(FileNotFoundError):
    """The row exists but its artifact does not — a 404, not a 500."""


def read_artifact_bytes(record: dict) -> bytes:
    """Return the artifact's bytes exactly as they sit on disk.

    Binary read, no parse, no re-serialization: the SBOM Author Signature that
    sbx-sig-01 persists is computed over these bytes, so a round-trip through
    ``json.load``/``json.dump`` would hand the recipient a document whose
    signature no longer verifies. "The exact bytes" is a correctness
    requirement here, not a preference.
    """
    path = record.get("file_path")
    if not path:
        raise ArtifactUnavailable("record has no file_path")
    resolved = Path(path)
    if not resolved.is_file():
        raise ArtifactUnavailable(f"artifact missing from disk: {path}")
    return resolved.read_bytes()


def artifact_digest(payload: bytes) -> str:
    """SHA-256 of what was served, so a recipient can verify the transfer."""
    return hashlib.sha256(payload).hexdigest()


#: CycloneDX metadata property names the generator writes. The handling
#: instructions travel *inside* the document; these are read back only so the
#: response can repeat them in headers, for a consumer that pipes the artifact
#: somewhere without parsing it.
CLASSIFICATION_PROPERTY = "icdev:classification"
DISTRIBUTION_PROPERTY = "icdev:distribution"


def document_markings(payload: bytes) -> dict:
    """The classification and distribution statements carried in the document.

    Best-effort and non-destructive: it parses a copy to read the markings and
    never touches the bytes that get served. An unparseable or foreign-format
    document yields empty strings rather than an error — a third-party SBOM
    with no ICDEV properties is a normal thing to hold, not a fault.
    """
    markings = {"classification": "", "distribution": ""}
    try:
        doc = json.loads(payload.decode("utf-8"))
        props = (doc.get("metadata") or {}).get("properties") or []
        for prop in props:
            name = (prop or {}).get("name")
            if name == CLASSIFICATION_PROPERTY:
                markings["classification"] = str(prop.get("value") or "")
            elif name == DISTRIBUTION_PROPERTY:
                markings["distribution"] = str(prop.get("value") or "")
    except Exception:
        pass
    return markings


# ---------------------------------------------------------------------------
# Conformance score (sbx-sig-02)
# ---------------------------------------------------------------------------

def conformance(record: dict) -> dict:
    """The SBOM's 2026 minimum-elements score, when it can be measured.

    The validator is sbx-sig-02's deliverable and has not landed. Rather than
    invent a number, this returns ``{"available": False}`` and the dashboard
    renders "not assessed" — a score column that shows 0, or blank, for an
    unmeasured artifact is a claim about the artifact, and it would be false.
    When ``sbom_minimum_elements_validator`` appears this starts returning real
    scores with no change at the call sites.
    """
    result = {"available": False, "score": None, "total": None,
              "reason": "sbom_minimum_elements_validator (sbx-sig-02) not installed"}
    try:
        from tools.compliance import sbom_minimum_elements_validator as validator
    except ImportError:
        return result

    try:
        payload = read_artifact_bytes(record)
    except ArtifactUnavailable as exc:
        result["reason"] = str(exc)
        return result

    try:
        scored = validator.score_document(json.loads(payload.decode("utf-8")))
    except Exception as exc:  # a validator error must not break the listing
        logger.warning("conformance scoring failed for record %s: %s", record.get("id"), exc)
        result["reason"] = f"scoring failed: {exc}"
        return result

    return {
        "available": True,
        "score": scored.get("score"),
        "total": scored.get("total"),
        "elements": scored.get("elements"),
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def log_distribution(record: dict, user: Any, decision: AccessDecision,
                     *, digest: str = None, ip_address: str = None) -> None:
    """Record one retrieval attempt in the append-only audit trail.

    Best-effort by design — a failed audit write must not deny an authorized
    party their SBOM — but it names only real ``audit_trail`` columns and an
    event type the constraint admits (migration 20260808071512), so the
    swallowed-INSERT failure mode this repo has been bitten by does not apply.
    """
    try:
        from tools.audit.audit_logger import log_event

        details = {
            "sbom_record_id": record.get("id"),
            "version": record.get("sbom_version") or record.get("version"),
            "format": record.get("format"),
            "reason": decision.reason,
            "actor_role": _user_field(user, "role") or "anonymous",
        }
        if digest:
            details["sha256"] = digest
        if decision.detail:
            details["detail"] = decision.detail

        log_event(
            event_type="sbom.distributed" if decision.allowed else "sbom.distribution_denied",
            actor=_user_field(user, "id", "user_id", default="anonymous"),
            action=(
                f"SBOM v{details['version']} released for project {record.get('project_id')}"
                if decision.allowed
                else f"SBOM retrieval denied ({decision.reason})"
            ),
            project_id=record.get("project_id"),
            details=details,
            affected_files=[record.get("file_path")] if record.get("file_path") else None,
            classification=(record.get("classification") or "CUI"),
            ip_address=ip_address,
        )
    except Exception as exc:
        logger.warning("SBOM distribution audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_connection():
    from tools.db.storage import get_connection

    return get_connection()


def main(argv: Iterable[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retrieve SBOM artifacts by version-specific address (sbx-gov-02)"
    )
    parser.add_argument("--list", action="store_true", help="List SBOM records and their URLs")
    parser.add_argument("--project-id", "--project", dest="project_id", help="Project ID")
    parser.add_argument("--version", help="SBOM version")
    parser.add_argument("--record-id", type=int, help="sbom_records row id")
    parser.add_argument("--out", help="Write the artifact bytes to this path")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(list(argv) if argv is not None else None)

    conn = _cli_connection()
    try:
        if args.list or not (args.record_id or (args.project_id and args.version)):
            records = list_records(conn, project_id=args.project_id, limit=args.limit)
            if args.json:
                print(json.dumps({"records": records, "count": len(records)},
                                 indent=2, default=str))
            else:
                if not records:
                    print("No SBOM records.")
                for r in records:
                    mark = "" if r["retrievable"] else "  [artifact missing]"
                    print(f"[{r['id']}] {r['project_id']} v{r['sbom_version_effective']} "
                          f"-> {r['retrieval_url']}{mark}")
            return 0

        record = resolve_record(conn, project_id=args.project_id,
                                version=args.version, record_id=args.record_id)
        if not record:
            msg = "no SBOM record matches that address"
            print(json.dumps({"error": msg}) if args.json else f"Error: {msg}", file=sys.stderr)
            return 1

        payload = read_artifact_bytes(record)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_bytes(payload)

        described = describe_record(record)
        described["sha256"] = artifact_digest(payload)
        described["bytes"] = len(payload)
        described["conformance"] = conformance(record)
        if args.json:
            print(json.dumps(described, indent=2, default=str))
        elif args.out:
            print(f"Wrote {len(payload)} bytes to {args.out} (sha256 {described['sha256']})")
        else:
            sys.stdout.buffer.write(payload)
        return 0
    except ArtifactUnavailable as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
