#!/usr/bin/env python3
# CUI // SP-CTI
"""Make a fetched web page a first-class, citeable source (oss-cite-01).

Before this module, provenance for a fetched URL was a URL string, a content
hash and a metadata JSON blob hanging off whatever row happened to store it —
and ``source_citation_registry`` had no ``citation_type`` that could hold it.
An inline ``[source: ...]`` tag pointing at a web page therefore could not be
validated against a persisted provenance record, which is precisely what the
TRUST invariant requires.

What a fetch provenance record pins down
----------------------------------------
=========================  ==============================================
``requested_url``          what the caller asked for
``final_url``              where the server actually served from
``redirect_chain``         every hop in between, in order
``http_status``            the status of the response that produced content
``fetched_at``             ISO-8601 UTC instant of the fetch
``content_hash``           sha256 of the exact bytes that were used
``etag`` / ``last_modified``  revalidators, when the server sent them
=========================  ==============================================

``requested_url`` and ``final_url`` are stored separately on purpose: a
citation that says "per https://example.gov/policy" is a different claim from
one that resolved through three hops to a mirror, and a record that keeps only
one of the two cannot tell those apart afterwards.

This generalizes the richest provenance ICDEV had for a fetched artifact —
``tools/genesis/reflexes/research.py::_export_signal``, which attaches
``evidence={"feed": ..., "fetched_at": ...}`` to an exported GKP.

Citation parsing and validation are NOT reimplemented here: everything
citation-shaped delegates to :mod:`tools.quality.citation_grounding`, so a web
citation is validated by the same parser as DIC, RFI, proposals and Tech
Writer. This module contributes exactly one thing to that pipeline — the set
of source ids that are *real*, drawn from persisted fetch rows.

Security posture: :func:`fetch_with_provenance` performs outbound HTTP through
the central client (``tools/http/client.py`` — mTLS, proxy, retry/backoff) and
never through a bare ``urllib``/``requests`` call. Optional SSRF gating is
described under "Egress" below. See ``docs/security/sandbox-coverage.md``
(Gap 39) for the recorded ingress decision.

Egress
------
``args/http_client.yaml`` carries an ``egress`` section, default
``enabled: false`` — the same default-off semantics as the guard's original
home in ``tools/doc_modernization/link_check.py``. When enabled, every fetch
is checked by that guard (HTTPS-only, suffix allow/denylist with deny-wins,
resolve-then-reject on any private/loopback/link-local answer) before a socket
is opened, and a denial raises :class:`EgressDenied`. ``oss-filter-03`` will
promote the guard to ``tools/http/egress_guard.py``; the import below prefers
that path as soon as it exists.

Usage::

    from tools.provenance.web_citation import fetch_and_record, validate_web_citations

    rec = fetch_and_record("https://example.gov/policy", project_id="proj-1")
    rec["fetch_id"]        # 'wfp-...' — the id an LLM cites
    rec["citation_id"]     # 'scr-...' — the source_citation_registry row
    rec["content"]         # response text

    draft = f"The policy requires annual review [source: {rec['fetch_id']}]."
    validate_web_citations(draft, project_id="proj-1")["valid"]   # True

CLI::

    python tools/provenance/web_citation.py --fetch https://example.gov/x --json
    python tools/provenance/web_citation.py --list --project proj-1 --json
    python tools/provenance/web_citation.py --show wfp-abc123 --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.provenance.registry import register_citation  # noqa: E402
from tools.quality import citation_grounding  # noqa: E402

TABLE = "web_fetch_provenance"
CITATION_TYPE = "web"
ARGS_FILE = BASE_DIR / "args" / "http_client.yaml"

# Sent so the operator of a fetched site can attribute (and rate-limit) us.
USER_AGENT = "ICDEV-Provenance/1.0 (+citation-fetch)"


class EgressDenied(RuntimeError):
    """Raised when the egress guard refuses a URL before any socket is opened."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"egress denied for {url}: {reason}")
        self.url = url
        self.reason = reason


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# ``fetched_at`` is TEXT, not TIMESTAMP: it is evidence of when a specific set
# of bytes was observed, so it is stored as the exact ISO-8601 UTC string that
# was recorded rather than something a backend may re-render in a local zone.
# ``tenant_id`` and ``classification`` exist because get_connection() injects
# RLS predicates naming both columns whenever a security context is present.

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id TEXT PRIMARY KEY,
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_type TEXT,
    content_length INTEGER,
    etag TEXT,
    last_modified TEXT,
    redirect_chain TEXT,
    title TEXT,
    fetch_tool TEXT,
    project_id TEXT,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

INDEXES = (
    ("idx_wfp_hash", "content_hash"),
    ("idx_wfp_project", "project_id"),
    ("idx_wfp_final_url", "final_url"),
    ("idx_wfp_fetched_at", "fetched_at"),
)


def init_tables(conn: Any = None) -> None:
    """Create the fetch-provenance table and its indexes (idempotent)."""
    owned = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(DDL)
        for idx, col in INDEXES:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON {TABLE}({col})")
        conn.commit()
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# Provenance record
# ---------------------------------------------------------------------------


def content_sha256(content: Any) -> str:
    """sha256 of fetched content. ``str`` is hashed as UTF-8; bytes as-is."""
    if content is None:
        content = b""
    if isinstance(content, str):
        content = content.encode("utf-8", errors="replace")
    return hashlib.sha256(content).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WebFetchProvenance:
    """One fetch of one URL at one instant — the citeable unit."""

    requested_url: str
    final_url: str = ""
    http_status: int = 0
    fetched_at: str = ""
    content_hash: str = ""
    content_type: Optional[str] = None
    content_length: Optional[int] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    redirect_chain: list[str] = field(default_factory=list)
    title: str = ""
    fetch_tool: str = "tools.provenance.web_citation"
    project_id: Optional[str] = None
    tenant_id: Optional[str] = None
    classification: str = "CUI"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"wfp-{uuid.uuid4().hex[:16]}"
        if not self.final_url:
            self.final_url = self.requested_url
        if not self.fetched_at:
            self.fetched_at = _utcnow_iso()

    @property
    def redirected(self) -> bool:
        return self.final_url != self.requested_url

    def citation_tag(self) -> str:
        """The inline tag an LLM should emit to cite this fetch."""
        return f"[source: {self.id}]"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "fetched_at": self.fetched_at,
            "content_hash": self.content_hash,
            "content_type": self.content_type,
            "content_length": self.content_length,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "redirect_chain": list(self.redirect_chain),
            "title": self.title,
            "fetch_tool": self.fetch_tool,
            "project_id": self.project_id,
            "tenant_id": self.tenant_id,
            "classification": self.classification,
        }

    def to_source_provenance(self) -> citation_grounding.Provenance:
        """Project onto the shared per-source lineage record.

        ``ingest_timestamp`` carries ``fetched_at`` and ``version_ref`` carries
        the ETag / Last-Modified revalidator, so a web source drops into
        ``build_artifact_provenance`` next to a RAG chunk with no remapping.
        """
        return citation_grounding.Provenance(
            source_id=self.id,
            sha256=self.content_hash,
            classification=self.classification,
            version_ref=self.etag or self.last_modified or "",
            ingest_timestamp=self.fetched_at,
        )

    @classmethod
    def from_row(cls, row: dict) -> "WebFetchProvenance":
        """Rebuild from a persisted ``web_fetch_provenance`` row."""
        chain = row.get("redirect_chain")
        if isinstance(chain, str):
            try:
                chain = json.loads(chain)
            except (TypeError, ValueError):
                chain = []
        return cls(
            id=row.get("id", ""),
            requested_url=row.get("requested_url", ""),
            final_url=row.get("final_url", ""),
            http_status=int(row.get("http_status") or 0),
            fetched_at=row.get("fetched_at", ""),
            content_hash=row.get("content_hash", ""),
            content_type=row.get("content_type"),
            content_length=row.get("content_length"),
            etag=row.get("etag"),
            last_modified=row.get("last_modified"),
            redirect_chain=list(chain or []),
            title=row.get("title") or "",
            fetch_tool=row.get("fetch_tool") or "",
            project_id=row.get("project_id"),
            tenant_id=row.get("tenant_id"),
            classification=row.get("classification") or "CUI",
        )


def capture(
    response: Any,
    requested_url: str,
    content: Any = None,
    *,
    title: str = "",
    project_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    classification: str = "CUI",
    fetch_tool: str = "tools.provenance.web_citation",
) -> WebFetchProvenance:
    """Build a provenance record from a ``requests.Response``.

    Kept separate from :func:`fetch_with_provenance` so a call site that
    already owns its fetch (url_analyzer, extractors, source_scanner) can
    record provenance without re-fetching. ``content`` defaults to
    ``response.text`` — pass it explicitly when the caller hashes something
    else (e.g. the raw bytes).

    ``redirect_chain`` is taken from ``response.history`` and lists the hop
    URLs in request order, ending with the final URL.
    """
    headers = getattr(response, "headers", None) or {}
    if content is None:
        content = getattr(response, "text", "") or ""

    history = getattr(response, "history", None) or []
    chain: list[str] = []
    for hop in history:
        hop_url = getattr(hop, "url", None)
        if hop_url:
            chain.append(str(hop_url))
    final_url = str(getattr(response, "url", "") or requested_url)
    if chain:
        chain.append(final_url)

    length = headers.get("Content-Length")
    try:
        length = int(length) if length is not None else len(
            content.encode("utf-8", errors="replace") if isinstance(content, str) else content
        )
    except (TypeError, ValueError):
        length = None

    return WebFetchProvenance(
        requested_url=requested_url,
        final_url=final_url,
        http_status=int(getattr(response, "status_code", 0) or 0),
        content_hash=content_sha256(content),
        content_type=headers.get("Content-Type"),
        content_length=length,
        etag=headers.get("ETag"),
        last_modified=headers.get("Last-Modified"),
        redirect_chain=chain,
        title=title,
        fetch_tool=fetch_tool,
        project_id=project_id,
        tenant_id=tenant_id,
        classification=classification,
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _egress_config() -> dict:
    """Read the ``egress`` section of args/http_client.yaml. Default: off."""
    try:
        import yaml

        data = yaml.safe_load(ARGS_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    cfg = data.get("egress")
    return cfg if isinstance(cfg, dict) else {}


def _egress_guard():
    """Resolve the SSRF guard. Prefers the oss-filter-03 home once it lands."""
    try:
        from tools.http.egress_guard import egress_guard  # type: ignore

        return egress_guard
    except Exception:
        pass
    try:
        from tools.doc_modernization.link_check import egress_guard

        return egress_guard
    except Exception:
        return None


def check_egress(url: str, cfg: Optional[dict] = None) -> tuple[bool, str]:
    """Apply the egress guard to ``url``. Returns ``(allowed, reason)``.

    Disabled by default (``egress.enabled: false``), in which case this returns
    ``(True, "disabled")`` without resolving anything. A configured-but-
    unavailable guard fails CLOSED — an SSRF gate that silently no-ops when its
    module is missing is worse than no gate, because the config claims cover.
    """
    cfg = _egress_config() if cfg is None else cfg
    if not cfg.get("enabled"):
        return (True, "disabled")
    guard = _egress_guard()
    if guard is None:
        return (False, "guard_unavailable")
    allowed, reason, _ips = guard(url, cfg)
    return (bool(allowed), reason)


def fetch_with_provenance(
    url: str,
    *,
    project_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    classification: str = "CUI",
    headers: Optional[dict] = None,
    egress_cfg: Optional[dict] = None,
    **kwargs,
) -> tuple[str, WebFetchProvenance]:
    """GET ``url`` through the central HTTP client and capture its provenance.

    Returns ``(content, provenance)``. Nothing is persisted — call
    :func:`record` (or use :func:`fetch_and_record`) for that, so a caller can
    decide after the fact whether a 404 is worth a citation row.

    Raises :class:`EgressDenied` when the guard is enabled and refuses the URL.
    HTTP errors are NOT raised: a non-2xx response is real provenance (the
    status is part of the record) and the caller decides what it means.
    """
    allowed, reason = check_egress(url, egress_cfg)
    if not allowed:
        raise EgressDenied(url, reason)

    from tools.http.client import request

    merged = {"User-Agent": USER_AGENT}
    merged.update(headers or {})
    response = request("GET", url, headers=merged, **kwargs)
    content = response.text or ""
    prov = capture(
        response,
        url,
        content,
        project_id=project_id,
        tenant_id=tenant_id,
        classification=classification,
    )
    return content, prov


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def record(
    prov: WebFetchProvenance,
    *,
    register: bool = True,
    conn: Any = None,
) -> dict:
    """Persist ``prov`` and register it as a ``web`` citation.

    Returns ``{"fetch_id", "citation_id", "provenance"}``. ``citation_id`` is
    ``""`` when ``register=False`` or when the registry insert failed.

    Every call inserts a new row: a fetch is an *event*, and two fetches of the
    same URL a month apart are two different pieces of evidence even when the
    bytes are identical. Use :func:`get_by_hash` to find prior observations of
    the same content.
    """
    owned = conn is None
    conn = conn or get_connection()
    try:
        init_tables(conn)
        conn.execute(
            f"""INSERT INTO {TABLE}
                (id, requested_url, final_url, http_status, fetched_at,
                 content_hash, content_type, content_length, etag, last_modified,
                 redirect_chain, title, fetch_tool, project_id, tenant_id,
                 classification, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                prov.id,
                prov.requested_url,
                prov.final_url,
                prov.http_status,
                prov.fetched_at,
                prov.content_hash,
                prov.content_type,
                prov.content_length,
                prov.etag,
                prov.last_modified,
                json.dumps(prov.redirect_chain),
                prov.title,
                prov.fetch_tool,
                prov.project_id,
                prov.tenant_id,
                prov.classification,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    finally:
        if owned:
            conn.close()

    citation_id = ""
    if register:
        citation_id = register_citation(
            citation_type=CITATION_TYPE,
            source_table=TABLE,
            source_record_id=prov.id,
            source_hash=prov.content_hash,
            source_doc=prov.final_url,
            classification=prov.classification,
            project_id=prov.project_id,
        )

    return {
        "fetch_id": prov.id,
        "citation_id": citation_id,
        "provenance": prov.to_dict(),
    }


def fetch_and_record(url: str, **kwargs) -> dict:
    """Fetch, capture, persist and register in one call.

    Returns :func:`record`'s dict plus ``content``. Keyword arguments are
    split between the two: ``register`` goes to :func:`record`, everything
    else to :func:`fetch_with_provenance`.
    """
    register = kwargs.pop("register", True)
    content, prov = fetch_with_provenance(url, **kwargs)
    result = record(prov, register=register)
    result["content"] = content
    return result


def get(fetch_id: str) -> Optional[dict]:
    """Return one fetch-provenance row by id."""
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE id = %s", (fetch_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_by_hash(content_hash: str) -> list[dict]:
    """Every prior observation of the same content, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE content_hash = %s ORDER BY fetched_at DESC",
            (content_hash,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def list_fetches(project_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    """List fetch-provenance rows, newest first, optionally scoped to a project."""
    conn = get_connection()
    try:
        if project_id:
            rows = conn.execute(
                f"SELECT * FROM {TABLE} WHERE project_id = %s "
                f"ORDER BY fetched_at DESC LIMIT %s",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {TABLE} ORDER BY fetched_at DESC LIMIT %s", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Citation validation — delegates to tools/quality/citation_grounding.py
# ---------------------------------------------------------------------------


def allowed_sources(project_id: Optional[str] = None, limit: int = 1000) -> list[str]:
    """The fetch ids that actually exist — the allow-set for citation checks."""
    return [r["id"] for r in list_fetches(project_id, limit=limit) if r.get("id")]


def validate_web_citations(
    text: str,
    *,
    project_id: Optional[str] = None,
    extra_sources: Optional[Iterable[str]] = None,
) -> dict:
    """Validate ``[source: wfp-...]`` tags against persisted fetch rows.

    A thin adapter: parsing and the report shape come from
    ``citation_grounding.validate_citations``. What this adds is the allow-set
    — ids read out of ``web_fetch_provenance``, which is exactly the "validate
    against a persisted provenance record" half of the TRUST invariant.

    ``extra_sources`` lets a surface mix web ids with its own (RAG chunk ids,
    DIC sources) in a single pass rather than validating twice.
    """
    allowed = set(allowed_sources(project_id))
    allowed.update(str(s) for s in (extra_sources or []))
    return citation_grounding.validate_citations(text, allowed)


def web_citation_gate(
    sections: list[dict],
    *,
    project_id: Optional[str] = None,
    require_citations: bool = True,
    extra_sources: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Promote/export gate for drafts citing fetched pages.

    Injects the persisted allow-set into each section and hands off to
    ``citation_grounding.citation_gate``, so findings come back in the same
    shape as every other drafting surface's citation defects. A section that
    already declares its own ``allowed_sources`` keeps them.
    """
    allowed = set(allowed_sources(project_id))
    allowed.update(str(s) for s in (extra_sources or []))
    scoped = []
    for sec in sections:
        merged = dict(sec)
        own = merged.get("allowed_sources")
        merged["allowed_sources"] = (
            allowed | {str(s) for s in own} if own is not None else allowed
        )
        scoped.append(merged)
    return citation_grounding.citation_gate(
        scoped, require_citations=require_citations
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Web citation & fetch provenance")
    parser.add_argument("--fetch", metavar="URL", help="Fetch a URL and record its provenance")
    parser.add_argument("--no-register", action="store_true",
                        help="Record provenance without registering a citation")
    parser.add_argument("--show", metavar="FETCH_ID", help="Show one fetch-provenance record")
    parser.add_argument("--list", action="store_true", help="List fetch-provenance records")
    parser.add_argument("--project", help="Scope --list / --validate to a project")
    parser.add_argument("--validate", metavar="FILE",
                        help="Validate [source: ...] tags in a file against persisted fetches")
    parser.add_argument("--init-db", action="store_true", help="Create the table and indexes")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    def emit(payload) -> None:
        print(json.dumps(payload, indent=2, default=str) if args.json else payload)

    if args.init_db:
        init_tables()
        emit({"status": "ok", "table": TABLE})
        return 0

    if args.fetch:
        try:
            result = fetch_and_record(args.fetch, register=not args.no_register)
        except EgressDenied as exc:
            emit({"status": "denied", "url": exc.url, "reason": exc.reason})
            return 1
        result.pop("content", None)
        emit(result)
        return 0

    if args.show:
        row = get(args.show)
        if not row:
            emit({"status": "not_found", "id": args.show})
            return 1
        emit(row)
        return 0

    if args.list:
        emit(list_fetches(args.project))
        return 0

    if args.validate:
        text = Path(args.validate).read_text(encoding="utf-8")
        emit(validate_web_citations(text, project_id=args.project))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
