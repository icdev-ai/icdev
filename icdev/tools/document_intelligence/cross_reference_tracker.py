# CUI // SP-CTI
"""Inter-document cross-reference tracking + cascade flagging (dmx-ref-01).

Documents cite each other in prose ("see Section 3 of the Backup SOP",
"per <Title> §N"). When one document is revised, the documents that cite the
revised section can silently go stale. This module makes those links explicit
and turns a revision into a finding on every citing document.

It COMPLEMENTS (does not replace) ``consistency_checker`` — that detects
KG concept overlap; this tracks EXPLICIT textual references and cascades on a
version approval whose changed sections intersect inbound references.

Pipeline (all deterministic — no LLM, air-gap safe):
  1. extract  — regex over document text -> dic_cross_references rows.
  2. resolve  — match target_doc_ref to a known DIC document by title/filename;
                fill target_doc_id. Unresolved refs are dangling_reference
                findings.
  3. cascade  — on a version approval, inbound refs whose target_section
                intersects the changed sections raise a cross_reference_cascade
                finding on each citing document.

Findings are written to ``docmod_findings`` (append-only, stable dedupe_key) so
they flow through the existing ``drift_bridge`` -> ACOIC compliance path and the
``get_findings`` latest-state dedup unchanged. HITL is preserved: a cascade
raises a finding for a human to triage; it never edits a document.

TRUST: verdicts here are deterministic. A cross_reference_cascade never asserts
the citing document IS wrong — only that a cited section changed and the citing
text should be re-reviewed.

Public API:
    extract_references(text, source_doc_id, source_section="") -> list[dict]
    store_references_from_text(conn, source_doc_id, text, ...) -> int
    store_references(conn, source_doc_id, ...) -> int
    resolve_references(conn=None, tenant_id=None) -> dict
    cascade_on_version_approval(version_id, conn=None) -> dict
    backfill(conn=None) -> dict
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from tools.doc_modernization.constants import REFERENCE_PATTERNS
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Compiled once at import. Each pattern exposes a named group ``doc`` and may
# expose ``section`` (see tools/doc_modernization/constants.REFERENCE_PATTERNS).
_COMPILED: list[re.Pattern] = [re.compile(p) for p in REFERENCE_PATTERNS]

_XREF_PACK_ID = "cross_reference"          # synthetic pack id for docmod findings
_XREF_ENTITY_TYPE = "system_reference"     # a doc referenced by another doc

_SECTION_NUM = re.compile(r"(\d+(?:\.\d+)*)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection

    return get_connection()


def _row_get(row, name, index):
    """Access a DB row by column name or positional index (sqlite3.Row / tuple / dict)."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(name)
    if isinstance(row, (list, tuple)):
        return row[index] if len(row) > index else None
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        try:
            return row[index]
        except Exception:
            return None


# ── Normalisation helpers ──────────────────────────────────────────────────────

def _normalize_title(text: str | None) -> str:
    """Lowercase, strip a leading article, collapse whitespace/punctuation."""
    s = (text or "").strip().lower()
    if s.startswith("the "):
        s = s[4:]
    s = re.sub(r"[^\w\s.\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _section_key(text: str | None) -> str:
    """Extract a comparable section number token from a heading or reference.

    "Section 3" -> "3", "§4.2" -> "4.2", "3. Backup Procedure" -> "3",
    "Overview" -> "" (no number). Deterministic; used to match a reference's
    target_section against a changed section's heading.
    """
    if not text:
        return ""
    m = _SECTION_NUM.search(str(text))
    return m.group(1) if m else ""


# ── Extraction ─────────────────────────────────────────────────────────────────

def extract_references(text: str, source_doc_id: str,
                       source_section: str = "") -> list[dict]:
    """Deterministically extract inter-document references from ``text``.

    Returns a de-duplicated list of dicts:
        {source_doc_id, source_section, target_doc_ref, target_section, ref_text}
    ``target_doc_ref`` is the raw cited title; ``target_section`` is the cited
    section number (may be empty for a whole-document reference).
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    haystack = text or ""
    for rx in _COMPILED:
        for m in rx.finditer(haystack):
            gd = m.groupdict()
            doc = (gd.get("doc") or "").strip().rstrip(".,;:")
            if not doc:
                continue
            section = (gd.get("section") or "").strip()
            ref_text = m.group(0).strip()
            key = (source_section, _normalize_title(doc), _section_key(section))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "source_doc_id": source_doc_id,
                "source_section": source_section,
                "target_doc_ref": doc,
                "target_section": section,
                "ref_text": ref_text,
            })
    return out


def _ref_id(source_doc_id: str, source_section: str, target_doc_ref: str,
            target_section: str) -> str:
    """Deterministic primary key so re-extraction of the same reference is
    idempotent (a repeated ingest/backfill collapses onto the same row rather
    than appending a duplicate)."""
    raw = "|".join([
        source_doc_id or "",
        source_section or "",
        _normalize_title(target_doc_ref),
        _section_key(target_section),
    ])
    return "xref-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _insert_reference(conn, ref: dict, tenant_id, classification) -> bool:
    """Insert one cross-reference if not already present. Returns True if a new
    row was written. Portable idempotency (SELECT-then-INSERT, deterministic id)
    — no dialect-specific ON CONFLICT."""
    rid = _ref_id(ref["source_doc_id"], ref.get("source_section", ""),
                  ref["target_doc_ref"], ref.get("target_section", ""))
    existing = conn.execute(
        "SELECT 1 FROM dic_cross_references WHERE id = %s", (rid,)
    ).fetchone()
    if existing:
        return False
    conn.execute(
        """INSERT INTO dic_cross_references
           (id, source_doc_id, source_section, target_doc_ref, target_doc_id,
            target_section, ref_text, tenant_id, classification, extracted_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            rid, ref["source_doc_id"], ref.get("source_section", ""),
            ref["target_doc_ref"], None, ref.get("target_section", ""),
            ref.get("ref_text", ""), tenant_id or "default",
            classification or "CUI", _now(),
        ),
    )
    return True


def store_references_from_text(conn, source_doc_id: str, text: str,
                               source_section: str = "",
                               tenant_id=None, classification=None) -> int:
    """Extract references from a block of text and upsert them. Returns the
    number of NEW rows written. Best-effort: never raises to its caller (an
    ingest hook must not fail the ingest)."""
    written = 0
    try:
        for ref in extract_references(text, source_doc_id, source_section):
            if _insert_reference(conn, ref, tenant_id, classification):
                written += 1
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("cross_reference_tracker.store_from_text(%s): %s",
                       source_doc_id, exc)
    return written


def _doc_context(conn, doc_id: str) -> tuple[str | None, str | None, str | None]:
    """Return (latest_approved_version_id, tenant_id, classification) for a doc."""
    ver = conn.execute(
        "SELECT version_id FROM dic_versions WHERE doc_id=%s AND status='approved' "
        "ORDER BY version_no DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    version_id = _row_get(ver, "version_id", 0) if ver else None
    meta = conn.execute(
        "SELECT tenant_id, classification FROM dic_documents WHERE doc_id=%s",
        (doc_id,),
    ).fetchone()
    tenant_id = _row_get(meta, "tenant_id", 0) if meta else None
    classification = _row_get(meta, "classification", 1) if meta else None
    return version_id, tenant_id, classification


def _version_sections(conn, version_id: str) -> list[tuple[str, str]]:
    """(heading, content) for every section of a version."""
    rows = conn.execute(
        "SELECT heading, content FROM dic_sections WHERE version_id=%s "
        "ORDER BY section_id",
        (version_id,),
    ).fetchall()
    return [((_row_get(r, "heading", 0) or ""), (_row_get(r, "content", 1) or ""))
            for r in rows]


def store_references(conn, source_doc_id: str, tenant_id=None,
                     classification=None) -> int:
    """Extract + upsert references for a document's latest approved version,
    per section (heading -> source_section). Returns count of new rows."""
    version_id, tid, cls = _doc_context(conn, source_doc_id)
    tenant_id = tenant_id or tid
    classification = classification or cls
    if not version_id:
        return 0
    written = 0
    for heading, content in _version_sections(conn, version_id):
        written += store_references_from_text(
            conn, source_doc_id, content, source_section=heading,
            tenant_id=tenant_id, classification=classification,
        )
    return written


# ── docmod finding writer ──────────────────────────────────────────────────────

def _open_finding_keys(conn, doc_id: str) -> set[str]:
    """dedupe_keys of the doc's currently-open findings (latest state per chain).

    Append-only: we must not insert a second open row for a key that is already
    open, or drift_bridge would emit a duplicate every sweep."""
    rows = [dict(r) if not isinstance(r, dict) else r for r in conn.execute(
        "SELECT dedupe_key, state, created_at FROM docmod_findings WHERE doc_id=%s "
        "ORDER BY created_at",
        (doc_id,),
    ).fetchall()]
    latest: dict[str, str] = {}
    for r in rows:
        key = _row_get(r, "dedupe_key", 0)
        if key:
            latest[key] = _row_get(r, "state", 1)
    return {k for k, st in latest.items() if st == "open"}


def _ensure_run(conn, scope_type: str, scope_id: str | None,
                triggered_by: str, tenant_id, classification) -> str:
    """Create a docmod_scan_runs row (findings FK-reference it) and return run_id."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO docmod_scan_runs
           (run_id, scope_type, scope_id, pack_ids, evidence_hash, triggered_by,
            started_at, tenant_id, classification)
           VALUES (%s,%s,%s,'["cross_reference"]','',%s,%s,%s,%s)""",
        (run_id, scope_type, scope_id, triggered_by, _now(), tenant_id,
         classification or "CUI"),
    )
    return run_id


def _emit_finding(conn, run_id: str, doc_id: str, version_id: str | None,
                  finding_type: str, entity_label: str, section_heading: str,
                  currency_verdict: str, severity: str, rationale: str,
                  dedupe_seed: str, tenant_id, classification,
                  open_keys: set[str]) -> str | None:
    """Insert an append-only docmod finding with a stable dedupe_key. Skips when
    an open finding already exists for the key (idempotent across re-runs).
    Returns the finding_id, or None when skipped."""
    dedupe_key = hashlib.sha256(dedupe_seed.encode("utf-8")).hexdigest()
    if dedupe_key in open_keys:
        return None
    fid = f"fnd-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, section_heading, pack_id,
            entity_label, entity_type, finding_type, currency_verdict, severity,
            rationale, evidence_json, confidence, state, dedupe_key, created_at,
            tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'[]',%s,'open',%s,%s,%s,%s)""",
        (
            fid, run_id, doc_id, version_id, section_heading, _XREF_PACK_ID,
            entity_label, _XREF_ENTITY_TYPE, finding_type, currency_verdict,
            severity, rationale, 1.0, dedupe_key, _now(), tenant_id,
            classification or "CUI",
        ),
    )
    open_keys.add(dedupe_key)
    return fid


# ── Resolution ─────────────────────────────────────────────────────────────────

def _document_index(conn) -> list[tuple[str, set[str], str, str]]:
    """(doc_id, {normalized aliases}, tenant_id, classification) for every doc.

    Aliases = the document title and its filename stem — the two human-facing
    labels a citation is likely to use. Deterministic; no fuzzy/LLM matching."""
    rows = conn.execute(
        "SELECT doc_id, title, filename, tenant_id, classification FROM dic_documents"
    ).fetchall()
    index: list[tuple[str, set[str], str, str]] = []
    for r in rows:
        doc_id = _row_get(r, "doc_id", 0)
        aliases = set()
        for raw in (_row_get(r, "title", 1), _row_get(r, "filename", 2)):
            norm = _normalize_title(raw)
            # Drop a file extension from a filename alias.
            norm = re.sub(r"\.(pdf|docx?|txt|md|html?)$", "", norm).strip()
            if norm:
                aliases.add(norm)
        if doc_id and aliases:
            index.append((doc_id, aliases,
                          _row_get(r, "tenant_id", 3) or "default",
                          _row_get(r, "classification", 4) or "CUI"))
    return index


def _match_document(ref_norm: str, index) -> str | None:
    """Resolve a normalized reference to a doc_id: exact alias match wins; else a
    containment match (ref within an alias or vice-versa). Returns the best
    (longest-overlap) doc_id, or None. Deterministic and conservative."""
    if not ref_norm:
        return None
    # Exact alias match first.
    for doc_id, aliases, _t, _c in index:
        if ref_norm in aliases:
            return doc_id
    # Containment fallback — require the shorter string to be a whole-word
    # substring of the longer so "Backup SOP" matches "Backup SOP v2" but a
    # 2-char accident cannot resolve.
    best: tuple[int, str] | None = None
    for doc_id, aliases, _t, _c in index:
        for alias in aliases:
            if len(ref_norm) < 4 or len(alias) < 4:
                continue
            if ref_norm in alias or alias in ref_norm:
                overlap = min(len(ref_norm), len(alias))
                if best is None or overlap > best[0]:
                    best = (overlap, doc_id)
    return best[1] if best else None


def resolve_references(conn=None, tenant_id: str | None = None) -> dict:
    """Match every unresolved cross-reference to a known document and fill
    target_doc_id. References that resolve to no document are raised as
    ``dangling_reference`` findings on the CITING document.

    Returns {resolved, dangling, still_unresolved, errors}.
    """
    own = conn is None
    if own:
        conn = _connect()
    result = {"resolved": 0, "dangling": 0, "still_unresolved": 0, "errors": []}
    try:
        index = _document_index(conn)
        sql = ("SELECT id, source_doc_id, source_section, target_doc_ref, "
               "target_section, ref_text, tenant_id, classification "
               "FROM dic_cross_references WHERE target_doc_id IS NULL")
        params: list = []
        if tenant_id:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        rows = conn.execute(sql, tuple(params)).fetchall()

        # Group dangling refs by citing doc so all get one run row.
        run_cache: dict[str, tuple[str, set[str]]] = {}

        for r in rows:
            rid = _row_get(r, "id", 0)
            source_doc_id = _row_get(r, "source_doc_id", 1)
            target_ref = _row_get(r, "target_doc_ref", 3)
            target_section = _row_get(r, "target_section", 4)
            ref_text = _row_get(r, "ref_text", 5)
            r_tenant = _row_get(r, "tenant_id", 6)
            r_cls = _row_get(r, "classification", 7)
            match = _match_document(_normalize_title(target_ref), index)
            if match and match != source_doc_id:
                conn.execute(
                    "UPDATE dic_cross_references SET target_doc_id=%s, extracted_at=%s "
                    "WHERE id=%s",
                    (match, _now(), rid),
                )
                result["resolved"] += 1
                continue
            if match == source_doc_id:
                # A self-reference is neither resolvable-elsewhere nor dangling.
                result["still_unresolved"] += 1
                continue
            # Dangling — raise a finding on the citing document.
            try:
                if source_doc_id not in run_cache:
                    run_id = _ensure_run(conn, "doc", source_doc_id, "manual",
                                         r_tenant, r_cls)
                    run_cache[source_doc_id] = (run_id, _open_finding_keys(conn, source_doc_id))
                run_id, open_keys = run_cache[source_doc_id]
                version_id, _t, _c = _doc_context(conn, source_doc_id)
                fid = _emit_finding(
                    conn, run_id, source_doc_id, version_id,
                    "dangling_reference", target_ref or "?", target_section or "",
                    "unknown", "low",
                    f"Cross-reference '{ref_text or target_ref}' resolves to no "
                    f"known document in the corpus.",
                    f"xref-dangling|{source_doc_id}|{_normalize_title(target_ref)}",
                    r_tenant, r_cls, open_keys,
                )
                if fid:
                    result["dangling"] += 1
                else:
                    result["still_unresolved"] += 1
            except Exception as exc:
                result["errors"].append(f"{rid}: {exc}")
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        result["errors"].append(str(exc))
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if own:
            conn.close()
    return result


# ── Cascade on version approval ────────────────────────────────────────────────

def _changed_section_keys(conn, doc_id: str, version_id: str) -> tuple[set[str], bool]:
    """Section keys (normalized numbers) whose content changed in ``version_id``
    relative to the previous approved version. Returns (changed_keys, all_changed).

    ``all_changed`` is True when there is no prior approved version (first
    approval — the whole document is new), so whole-document references cascade.
    """
    ver = conn.execute(
        "SELECT version_no FROM dic_versions WHERE version_id=%s", (version_id,)
    ).fetchone()
    version_no = _row_get(ver, "version_no", 0) if ver else None

    prev_version_id = None
    if version_no is not None:
        prev = conn.execute(
            "SELECT version_id FROM dic_versions WHERE doc_id=%s AND status='approved' "
            "AND version_no < %s ORDER BY version_no DESC LIMIT 1",
            (doc_id, version_no),
        ).fetchone()
        prev_version_id = _row_get(prev, "version_id", 0) if prev else None

    def _section_map(vid: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for heading, content in _version_sections(conn, vid):
            key = _section_key(heading)
            digest = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
            # Key by section number when present, else by normalized heading.
            out[key or _normalize_title(heading)] = digest
        return out

    current = _section_map(version_id)
    if not prev_version_id:
        return {k for k in current if k}, True

    previous = _section_map(prev_version_id)
    changed: set[str] = set()
    for key, digest in current.items():
        if key and previous.get(key) != digest:
            changed.add(key)
    return changed, False


def cascade_on_version_approval(version_id: str, conn=None) -> dict:
    """A version was just approved. Raise a ``cross_reference_cascade`` finding on
    every document whose inbound reference points at a section that changed in
    this version (or at the whole document, when this is a first approval or any
    section changed).

    Best-effort and non-blocking: an approval must never fail because a cascade
    could not be computed. HITL-preserving: it raises findings for triage; it
    never edits a document.

    Returns {target_doc_id, changed_sections, inbound, cascaded, errors}.
    """
    own = conn is None
    if own:
        conn = _connect()
    result = {"target_doc_id": None, "changed_sections": [], "inbound": 0,
              "cascaded": 0, "errors": []}
    try:
        vrow = conn.execute(
            "SELECT doc_id FROM dic_versions WHERE version_id=%s", (version_id,)
        ).fetchone()
        target_doc_id = _row_get(vrow, "doc_id", 0) if vrow else None
        if not target_doc_id:
            return result
        result["target_doc_id"] = target_doc_id

        changed_keys, all_changed = _changed_section_keys(conn, target_doc_id, version_id)
        result["changed_sections"] = sorted(changed_keys)

        inbound = conn.execute(
            "SELECT id, source_doc_id, source_section, target_doc_ref, "
            "target_section, ref_text, tenant_id, classification "
            "FROM dic_cross_references WHERE target_doc_id=%s",
            (target_doc_id,),
        ).fetchall()
        result["inbound"] = len(inbound)
        if not inbound:
            return result

        run_cache: dict[str, tuple[str, set[str]]] = {}
        for r in inbound:
            citing_doc = _row_get(r, "source_doc_id", 1)
            target_section = _row_get(r, "target_section", 4)
            ref_text = _row_get(r, "ref_text", 5)
            r_tenant = _row_get(r, "tenant_id", 6)
            r_cls = _row_get(r, "classification", 7)
            sec_key = _section_key(target_section)
            # Whole-document reference (no section) cascades on any change;
            # a sectioned reference cascades only when its section changed.
            hit = (not sec_key and (all_changed or changed_keys)) or (sec_key in changed_keys)
            if not hit:
                continue
            try:
                if citing_doc not in run_cache:
                    run_id = _ensure_run(conn, "doc", citing_doc, "api",
                                         r_tenant, r_cls)
                    run_cache[citing_doc] = (run_id, _open_finding_keys(conn, citing_doc))
                run_id, open_keys = run_cache[citing_doc]
                c_version_id, _t, _c = _doc_context(conn, citing_doc)
                sec_desc = f"§{sec_key}" if sec_key else "document"
                fid = _emit_finding(
                    conn, run_id, citing_doc, c_version_id,
                    "cross_reference_cascade", ref_text or target_doc_id,
                    _row_get(r, "source_section", 2) or "",
                    "divergent", "medium",
                    f"Cited {sec_desc} of {target_doc_id} changed on approval of "
                    f"version {version_id}; re-review this reference.",
                    f"xref-cascade|{citing_doc}|{_row_get(r, 'id', 0)}|{version_id}",
                    r_tenant, r_cls, open_keys,
                )
                if fid:
                    result["cascaded"] += 1
            except Exception as exc:
                result["errors"].append(f"{_row_get(r, 'id', 0)}: {exc}")
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        result["errors"].append(str(exc))
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if own:
            conn.close()
    return result


# ── Backfill ───────────────────────────────────────────────────────────────────

def backfill(conn=None) -> dict:
    """Extract references for every existing document, then run one resolution
    pass. Idempotent — safe to re-run. Returns {docs, references_new, resolve}."""
    own = conn is None
    if own:
        conn = _connect()
    result = {"docs": 0, "references_new": 0, "resolve": {}, "errors": []}
    try:
        rows = conn.execute("SELECT doc_id FROM dic_documents").fetchall()
        doc_ids = [_row_get(r, "doc_id", 0) for r in rows]
        for doc_id in doc_ids:
            try:
                result["references_new"] += store_references(conn, doc_id)
                result["docs"] += 1
            except Exception as exc:
                result["errors"].append(f"{doc_id}: {exc}")
        conn.commit()
        result["resolve"] = resolve_references(conn=conn)
    except Exception as exc:  # pragma: no cover - defensive
        result["errors"].append(str(exc))
    finally:
        if own:
            conn.close()
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        description="Inter-document cross-reference tracking (dmx-ref-01)"
    )
    ap.add_argument("--backfill", action="store_true",
                    help="extract references for all documents, then resolve")
    ap.add_argument("--resolve", action="store_true",
                    help="run only the resolution pass (fill target_doc_id, flag dangling)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    if args.backfill:
        out: dict = backfill()
    elif args.resolve:
        out = resolve_references()
    else:
        ap.error("one of --backfill or --resolve is required")
        return 2

    if args.json:
        print(_json.dumps(out, indent=2, default=str))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
