# CUI // SP-CTI
"""IDR Workflow — 8-stage session state machine.

Stages:
  0 setup        → user configures session, selects domain profile
  1 ingesting    → files uploaded and sent to DIC ingest_file()
  2 analyzing    → diagram_analysis + config/IaC review per upload
  3 conflicts    → HITL: human resolves topology conflicts
  4 synthesizing → context_builder assembles unified evidence context
  5 generating   → ACE-orchestrated AI doc generation via DIC doc_generator
  6 writeguard   → WriteGuard hard gate (blocking); auto-fix up to 3× then HITL
  7 reviewing    → HITL: human reviews/edits/approves full document
  8 publishing   → final coherence pass, export all formats, mark published

Transitions are stored in idr_sessions.stage + status. Kickback rewinds to
the appropriate stage with reviewer notes injected into the next generation.
"""
from __future__ import annotations

import os
import pathlib
import uuid
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.docgen import session_manager as sm
from tools.docgen.domain_profiles import get_profile, get_writeguard_mode, get_ato_doc_type

log = get_logger(__name__)

_STAGE_STATUS: list[tuple[int, str]] = [
    (0, "setup"),
    (1, "ingesting"),
    (2, "analyzing"),
    (3, "conflicts"),
    (4, "synthesizing"),
    (5, "generating"),
    (6, "writeguard"),
    (7, "reviewing"),
    (8, "publishing"),
]

# WriteGuard pass threshold
_WG_MIN_SCORE = 70
_WG_MAX_RETRIES = 3

# Maximum context characters passed to ACE generation (1M-ctx models are GA)
_ACE_CONTEXT_MAX_CHARS = 50_000


# ─── TRUST citation & placeholder gate (cnr-doc-01) ──────────────────────────
# Every LLM-generated artifact MUST carry inline [source: …] citations and be
# free of unresolved placeholders before it can be exported/published (CLAUDE.md
# TRUST invariant). Built on the SHARED tools/quality grounding modules — no
# citation parsing is re-implemented here.

def assembled_doc_findings(
    doc_text: str,
    *,
    allowed_sources: Any = None,
    require_citations: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Return (citation_findings, placeholder_findings) for an assembled document.

    The whole document is treated as a single ``citation_gate`` section. Pure — no
    DB, no Flask. Empty lists mean the document is clean. When ``allowed_sources``
    is provided, citations to any id outside that set are flagged as hallucinated;
    when it is None only citation *presence* is enforced (``require_citations``).
    """
    section: dict[str, Any] = {"item_number": "document", "content": doc_text or ""}
    if allowed_sources is not None:
        section["allowed_sources"] = allowed_sources
    citation: list[dict] = []
    placeholder: list[dict] = []
    try:
        from tools.quality.citation_grounding import citation_gate
        citation = citation_gate([section], require_citations=require_citations)
    except Exception:
        citation = []
    try:
        from tools.quality.content_grounding import placeholder_findings
        placeholder = placeholder_findings([section])
    except Exception:
        placeholder = []
    return citation, placeholder


def citation_publish_gate(
    doc_text: str,
    *,
    force_citations: bool = False,
    force_placeholders: bool = False,
    allowed_sources: Any = None,
    require_citations: bool = True,
) -> dict[str, Any]:
    """TRUST export/publish gate — mirrors govcon ``response_drafter.approve_draft``.

    Placeholder guard runs first, then citation guard. A defect blocks unless the
    matching ``force_*`` override is set, in which case the finding is recorded in
    ``overrides`` for the caller to persist to the append-only audit trail.

    Returns ``{blocked, gate, citation_findings, placeholder_findings, overrides,
    grounding_available}``.  ``grounding_available`` is False when the shared
    grounding module could not be imported — callers MUST fail closed on that
    (never publish text no gate could inspect).
    """
    grounding_available = True
    try:
        import tools.quality.citation_grounding  # noqa: F401
        import tools.quality.content_grounding  # noqa: F401
    except Exception:
        grounding_available = False

    citation, placeholder = assembled_doc_findings(
        doc_text, allowed_sources=allowed_sources, require_citations=require_citations
    )
    result: dict[str, Any] = {
        "blocked": False,
        "gate": None,
        "citation_findings": citation,
        "placeholder_findings": placeholder,
        "overrides": {},
        "grounding_available": grounding_available,
    }
    if placeholder and not force_placeholders:
        result.update(blocked=True, gate="placeholder_guard")
        return result
    if citation and not force_citations:
        result.update(blocked=True, gate="citation_guard")
        return result
    overrides: dict[str, Any] = {}
    if placeholder and force_placeholders:
        overrides["placeholder_guard_override"] = placeholder
    if citation and force_citations:
        overrides["citation_guard_override"] = citation
    result["overrides"] = overrides
    return result


def _diff_scope_check(
    original: str,
    proposed: str,
    failed_sections: list[str] | None = None,
) -> bool:
    """Return True if the proposed rewrite stays within scope.

    Aborts (returns False) when >30% of sections not flagged as failing were
    substantially rewritten — guards against overeager autonomous agents that
    rewrite good sections while fixing bad ones.

    Args:
        original:        Document text before rewrite.
        proposed:        Document text after rewrite.
        failed_sections: Names/snippets of sections the quality check flagged.
    """
    import re as _re

    def _split(text: str) -> list[str]:
        parts = _re.split(r"\n#{1,3}\s+|\n{2,}", text)
        return [p.strip() for p in parts if p.strip()]

    orig_secs = _split(original)
    prop_secs = _split(proposed)
    if not orig_secs:
        return True
    # Single-section documents have no "good" sections to protect; skip check.
    if len(orig_secs) <= 1:
        return True

    failed = {(f or "").lower() for f in (failed_sections or [])}
    overeager = 0
    non_failed = 0

    for i, orig_sec in enumerate(orig_secs):
        head = orig_sec.lower()[:120]
        if any(f and f in head for f in failed):
            continue  # this section was supposed to change
        non_failed += 1
        if i < len(prop_secs):
            prop_sec = prop_secs[i]
            orig_tok = set(orig_sec.lower().split())
            prop_tok = set(prop_sec.lower().split())
            if orig_tok:
                overlap = len(orig_tok & prop_tok) / len(orig_tok)
                if overlap < 0.70:  # >30% token churn = substantially rewritten
                    overeager += 1

    if non_failed == 0:
        return True
    return (overeager / non_failed) <= 0.30


def _append_compliance_stamp(doc_text: str, classification: str) -> str:
    """Append a compliance framework applicability section to the document.

    Maps classification → impact level → applicable frameworks, then appends
    a structured "Compliance Framework Applicability" appendix.  The crosswalk
    engine is called to verify the mapping data is available; if unavailable
    the stamp falls back to a static table.
    """
    _CLS_MAP: dict[str, tuple[str, list[str]]] = {
        "public":     ("LOW",      ["NIST SP 800-53 Rev 5 Low Baseline"]),
        "cui":        ("MODERATE", ["NIST SP 800-53 Rev 5 Moderate", "FedRAMP Moderate", "CMMC Level 2"]),
        "secret":     ("HIGH",     ["NIST SP 800-53 Rev 5 High", "FedRAMP High", "CMMC Level 3"]),
        "top secret": ("HIGH",     ["NIST SP 800-53 Rev 5 High", "FedRAMP High", "CMMC Level 3", "ICD 503"]),
        # "TS//SCI".lower().split("//")[0] == "ts" — matches this key
        "ts":         ("HIGH",     ["NIST SP 800-53 Rev 5 High", "FedRAMP High", "CMMC Level 3", "ICD 503", "IC Tech Spec-for ICD/ICS 503"]),
    }
    key = (classification or "cui").lower().split("//")[0].strip()
    level, frameworks = _CLS_MAP.get(key, _CLS_MAP["cui"])

    try:
        from tools.compliance.crosswalk_engine import get_crosswalk_summary
        summary = get_crosswalk_summary()
        xwalk_note = f"Crosswalk coverage: {summary.get('total_mappings', 'N/A')} control mappings loaded."
    except Exception:
        xwalk_note = "See /compliance/crosswalk for full control mapping details."

    lines = [
        "",
        "---",
        "",
        "## Appendix: Compliance Framework Applicability",
        "",
        f"**Impact Level:** {level}  |  **Classification:** {classification.upper()}",
        "",
        "| Framework | Applicability |",
        "|---|---|",
    ]
    for fw in frameworks:
        lines.append(f"| {fw} | Required |")
    lines += [
        "",
        f"_{xwalk_note}_",
        "",
        "_This document was generated by ICDEV™ Intelligent Documentation Regeneration (IDR)._",
        "_Control implementation narratives: /compliance/crosswalk_",
    ]
    return doc_text + "\n" + "\n".join(lines)


# ─── Item 8: Document freshness gate ────────────────────────────────────────

# cnr-doc-04(e): cache of digest keyed by a (path, mtime_ns, size) signature so a
# rendered session list doesn't re-read every upload file on every request. The
# signature only changes when a file actually changes, so the digest VALUE is
# preserved exactly (no re-baselining of existing stored hashes).
_HASH_CACHE: dict[tuple, str] = {}
_HASH_CACHE_MAX = 512


def compute_source_hash(upload_paths: list[str]) -> str:
    """Return a stable SHA-256 hash of the content of all upload files.

    Files are sorted by path before hashing to ensure determinism.
    Returns a hex string, or empty string if no paths provided or all files missing.
    Caches by (path, mtime, size) so unchanged files are not re-read on every call.
    """
    import hashlib

    if not upload_paths:
        return ""

    ordered = sorted(upload_paths)
    # Cheap stat-only signature (no byte reads) to short-circuit unchanged sources.
    sig_parts: list[tuple] = []
    stat_ok = True
    for path in ordered:
        try:
            st = os.stat(path)
            sig_parts.append((path, st.st_mtime_ns, st.st_size))
        except OSError:
            stat_ok = False
            break
    sig = tuple(sig_parts) if stat_ok and sig_parts else None
    if sig is not None and sig in _HASH_CACHE:
        return _HASH_CACHE[sig]

    h = hashlib.sha256()
    any_read = False
    for path in ordered:
        p = pathlib.Path(path)
        try:
            h.update(p.read_bytes())
            any_read = True
        except (OSError, IOError):
            pass
    digest = h.hexdigest() if any_read else ""
    if sig is not None and digest:
        if len(_HASH_CACHE) >= _HASH_CACHE_MAX:
            _HASH_CACHE.clear()
        _HASH_CACHE[sig] = digest
    return digest


def record_source_hash(session_id: str, upload_paths: list[str]) -> str:
    """Compute and persist the source hash for a session's uploads.

    Call this after all uploads are ingested (end of Stage 1).
    Returns the hash string.
    """
    from datetime import datetime, timezone

    h = compute_source_hash(upload_paths)
    if h:
        sm.set_field(
            session_id,
            last_source_hash=h,
            source_hash_checked_at=datetime.now(timezone.utc).isoformat(),
        )
        log.info("IDR freshness: session=%s hash=%s", session_id, h[:12])
    return h


def check_freshness(
    session_id: str,
    upload_paths: list[str],
    stored_hash: str | None = None,
) -> dict:
    """Compare current upload hash against the stored last_source_hash.

    Returns {"stale": bool, "current_hash": str, "stored_hash": str}.
    stale=True means uploads have changed since the document was last generated.
    stale=False if no stored hash (new session) or hashes match.

    cnr-doc-04(e): pass ``stored_hash`` (from a session row the caller already has)
    to skip the per-session get_session() round-trip when rendering a session list.
    """
    if stored_hash is None:
        session = sm.get_session(session_id) or {}
        stored = session.get("last_source_hash") or ""
    else:
        stored = stored_hash or ""
    current = compute_source_hash(upload_paths)
    stale = bool(stored and current and stored != current)
    return {"stale": stale, "current_hash": current, "stored_hash": stored}


# ─── Item 3: OKB policy gate ─────────────────────────────────────────────────

_POLICY_DIR = pathlib.Path(__file__).resolve().parents[2] / "context" / "docgen" / "policies"


def policy_check(doc_text: str, doc_type: str | None, classification: str | None) -> dict:
    """Machine-checkable OKB policy gate for WriteGuard.

    Loads YAML constraint file for *doc_type* (falls back to default.yaml),
    evaluates each constraint against *doc_text*, and returns a result dict:

        {
            "passed": bool,
            "violations": [{"id": str, "description": str, "required": bool}],
            "warnings": [{"id": str, "description": str}],
        }

    A constraint with ``required: true`` that fails makes ``passed`` False.
    A constraint with ``negate: true`` matches when the pattern is ABSENT.
    Falls back to pass=True/no violations on any load/parse error.
    """
    import re as _re
    import yaml as _yaml

    def _load_constraints(dt: str | None) -> list[dict]:
        candidates = [dt or "default", "default"] if dt and dt != "default" else ["default"]
        for name in candidates:
            p = _POLICY_DIR / f"{name}.yaml"
            if p.exists():
                try:
                    raw = _yaml.safe_load(p.read_text(encoding="utf-8"))
                    return list(raw.get("constraints", []))
                except Exception:
                    pass
        return []

    try:
        constraints = _load_constraints(doc_type)
    except Exception:
        return {"passed": True, "violations": [], "warnings": []}

    violations: list[dict] = []
    warnings: list[dict] = []

    for c in constraints:
        cid = c.get("id", "?")
        desc = c.get("description", "")
        pattern = c.get("pattern", "")
        required = bool(c.get("required", False))
        negate = bool(c.get("negate", False))

        try:
            matched = bool(_re.search(pattern, doc_text, _re.MULTILINE))
        except Exception:
            continue

        triggered = (not matched) if (not negate) else matched
        if triggered:
            entry = {"id": cid, "description": desc, "required": required}
            if required:
                violations.append(entry)
            else:
                warnings.append(entry)

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "warnings": warnings,
    }


# ─── Stage transitions ────────────────────────────────────────────────────────

def advance(session_id: str, to_stage: int) -> dict[str, Any]:
    """Advance a session to *to_stage* and return the updated session."""
    if to_stage < 0 or to_stage > 8:
        raise ValueError(f"Invalid stage: {to_stage}")
    _, status = _STAGE_STATUS[to_stage]
    sm.advance_stage(session_id, to_stage, status)
    session = sm.get_session(session_id)
    log.info("IDR advance: session=%s stage=%d status=%s", session_id, to_stage, status)
    return session


def kickback(session_id: str, to_stage: int, reason: str = "") -> dict[str, Any]:
    """Rewind to *to_stage* (e.g. 5 = re-generate) with an optional reason logged."""
    log.warning(
        "IDR kickback: session=%s → stage=%d reason=%r", session_id, to_stage, reason
    )
    return advance(session_id, to_stage)


# ─── Stage 0: LLM-first document ingestion ───────────────────────────────────

_STAGE0_EXTRACT_PROMPT = (
    "You are an expert document analyst. Extract structured information from the following document.\n"
    "Return JSON with these keys:\n"
    "  - entities: list of {name, type, description} (nodes/components/actors)\n"
    "  - topology: list of {source, target, relationship} (connections between entities)\n"
    "  - key_findings: list of strings (important facts, risks, requirements)\n"
    "  - document_type: string (best guess: runbook/ssp/poam/stig_checklist/architecture_doc/other)\n"
    "  - classification_hint: string (CUI/SECRET/UNCLASSIFIED/unknown)\n\n"
    "Document text (first 4000 chars):\n{text}"
)


def stage0_ingest_document(session_id: str, doc_text: str) -> dict:
    """Stage 0: LLM-first extraction from an uploaded document.

    Extracts entities, topology, key findings, and classification hint from
    raw document text. Results are persisted to the session context so that
    Stage 4 (context_builder) can incorporate prior_docs without re-reading
    the raw file.

    Returns {"entities": [...], "topology": [...], "key_findings": [...],
             "document_type": str, "classification_hint": str, "session_id": str}.
    Falls back to empty extraction on any LLM/import error.
    """
    import json as _json

    _FALLBACK = {
        "entities": [],
        "topology": [],
        "key_findings": [],
        "document_type": "unknown",
        "classification_hint": "unknown",
        "session_id": session_id,
        "extracted": False,
    }

    if not doc_text or not doc_text.strip():
        return _FALLBACK.copy()

    try:
        # Route through the GOVERNED cortex.complete facade (provenance + audit +
        # output redaction + budget attribution) instead of a raw router.invoke.
        # docgen ingests trusted first-party document text, so trusted_content=True
        # preserves the original skip_injection_scan=True behaviour — the input
        # injection screen / input redaction are skipped, egress is still governed.
        from tools.cortex import api as cortex_api
        from tools.cortex.schemas import CortexContext

        prompt = _STAGE0_EXTRACT_PROMPT.format(text=doc_text[:4000])
        cx = cortex_api.complete(
            prompt,
            function="stage0_doc_extract",
            ctx=CortexContext(domain="document", agent_id="docgen",
                              trusted_content=True),
            max_tokens=1024,
            temperature=0.0,
        )
        raw = (cx.text or "").strip()

        import re as _re
        raw = _re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        data = _json.loads(raw)

        result = {
            "entities": list(data.get("entities", [])),
            "topology": list(data.get("topology", [])),
            "key_findings": list(data.get("key_findings", [])),
            "document_type": str(data.get("document_type", "unknown")),
            "classification_hint": str(data.get("classification_hint", "unknown")),
            "session_id": session_id,
            "extracted": True,
        }

        # Persist prior_docs context to session (used by context_builder in Stage 4)
        existing = sm.get_session(session_id) or {}
        prior = _json.loads(existing.get("prior_docs_context") or "[]")
        prior.append(result)
        sm.set_field(session_id, prior_docs_context=_json.dumps(prior))

        log.info(
            "IDR stage0: session=%s extracted entities=%d topology=%d findings=%d",
            session_id, len(result["entities"]), len(result["topology"]), len(result["key_findings"]),
        )
        return result

    except (ImportError, Exception) as exc:
        log.debug("IDR stage0_ingest_document fallback: %s", exc)
        return _FALLBACK.copy()


# ─── Stage 1: Upload & Ingest ─────────────────────────────────────────────────

def stage1_ingest_upload(
    session_id: str,
    upload_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Ingest a single upload into DIC. Returns the updated upload row."""
    try:
        from tools.document_intelligence.ingest_orchestrator import ingest_file

        result = ingest_file(
            path=file_path,
            collection_id=_get_collection_id(session_id),
        )
        # IngestOutcome is a dataclass; fall back to dict .get() for compatibility
        dic_doc_id = (
            getattr(result, "doc_id", None)
            if result and not isinstance(result, dict)
            else (result.get("doc_id") if result else None)
        )
        sm.set_upload_status(upload_id, "ingested", dic_doc_id=dic_doc_id)
        log.info("IDR ingest: upload=%s → dic_doc=%s", upload_id, dic_doc_id)
    except ImportError:
        log.warning("DIC ingest_orchestrator not available — marking upload as ingested without DIC")
        sm.set_upload_status(upload_id, "ingested")
    except Exception as exc:
        log.exception("IDR ingest failed: upload=%s", upload_id)
        sm.set_upload_status(upload_id, "error", error_msg=str(exc))

    return sm.get_upload(upload_id) or {}


# ─── Stage 2: Domain Analysis ─────────────────────────────────────────────────

def _run_analyzer(module_path: str, fn_name: str, file_path: str, domain: str) -> dict[str, Any]:
    """Import module and call analyzer function with (file_path[, domain]) signature."""
    import importlib

    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    import inspect
    sig = inspect.signature(fn)
    # analyze_from_file accepts an optional domain kwarg; others take only file_path
    if len(sig.parameters) >= 2:
        return fn(file_path, domain)
    return fn(file_path)


def stage2_analyze_upload(
    session_id: str,
    upload_id: str,
    upload: dict[str, Any],
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run applicable analyzers for one upload. Returns list of idr_analyses rows created.

    Routing by upload_type:
      diagram   → profile.diagram_analyzer / diagram_analyzer_fn (analyze_from_file)
      config    → profile.config_reviewer / config_reviewer_fn (review_config / review_firewall_config)
      iac       → profile.iac_reviewer / iac_reviewer_fn (review_iac)
      doc       → LLM entity/procedure extraction + mark analyzed
      supplement → mark analyzed
    """
    domain = session.get("domain", "network")
    profile = get_profile(domain)
    file_path = upload.get("file_path", "")
    upload_type = upload.get("upload_type", "")
    analyses_created: list[dict[str, Any]] = []

    if not file_path:
        log.warning("IDR stage2: upload %s has no file_path, skipping", upload_id)
        sm.set_upload_status(upload_id, "analyzed")
        return analyses_created

    # ── Diagram analysis ──────────────────────────────────────────────────────
    if upload_type == "diagram":
        diag_mod_path = profile.get("diagram_analyzer")
        diag_fn_name = profile.get("diagram_analyzer_fn", "analyze_from_file")
        if diag_mod_path and diag_fn_name:
            try:
                result = _run_analyzer(diag_mod_path, diag_fn_name, file_path, domain)
                result_ref_id = result.get("analysis_id") or str(uuid.uuid4())
                import json as _json
                row = sm.add_analysis(
                    session_id, upload_id, "diagram_analysis", result_ref_id,
                    result_json=_json.dumps(result),
                )
                analyses_created.append(row)
                sm.set_upload_status(upload_id, "analyzed")
                log.info(
                    "IDR diagram analysis complete: upload=%s ref=%s tabs=%s",
                    upload_id, result_ref_id,
                    list((result.get("tabs") or {}).keys()),
                )
            except Exception:
                log.exception("IDR diagram analysis failed: upload=%s", upload_id)
                sm.set_upload_status(upload_id, "error", error_msg="diagram_analysis failed")
        else:
            log.debug("No diagram analyzer configured for domain=%s", domain)
            sm.set_upload_status(upload_id, "analyzed")

    # ── Config review ─────────────────────────────────────────────────────────
    elif upload_type == "config":
        cfg_mod_path = profile.get("config_reviewer")
        cfg_fn_name = profile.get("config_reviewer_fn")
        if cfg_mod_path and cfg_fn_name:
            try:
                result = _run_analyzer(cfg_mod_path, cfg_fn_name, file_path, domain)
                result_ref_id = result.get("review_id") or str(uuid.uuid4())
                import json as _json
                row = sm.add_analysis(
                    session_id, upload_id, "config_review", result_ref_id,
                    result_json=_json.dumps(result),
                )
                analyses_created.append(row)
                sm.set_upload_status(upload_id, "analyzed")
                log.info(
                    "IDR config review complete: upload=%s vendor=%s findings=%d",
                    upload_id,
                    result.get("vendor", "?"),
                    len(result.get("security_compliance", [])),
                )
            except Exception:
                log.exception("IDR config review failed: upload=%s", upload_id)
                sm.set_upload_status(upload_id, "error", error_msg="config_review failed")
        else:
            log.debug("No config reviewer configured for domain=%s", domain)
            sm.set_upload_status(upload_id, "analyzed")

    # ── IaC review ────────────────────────────────────────────────────────────
    elif upload_type == "iac":
        iac_mod_path = profile.get("iac_reviewer")
        iac_fn_name = profile.get("iac_reviewer_fn")
        if iac_mod_path and iac_fn_name:
            try:
                result = _run_analyzer(iac_mod_path, iac_fn_name, file_path, domain)
                result_ref_id = result.get("review_id") or str(uuid.uuid4())
                import json as _json
                row = sm.add_analysis(
                    session_id, upload_id, "iac_review", result_ref_id,
                    result_json=_json.dumps(result),
                )
                analyses_created.append(row)
                sm.set_upload_status(upload_id, "analyzed")
                log.info(
                    "IDR IaC review complete: upload=%s iac_type=%s risk=%s",
                    upload_id,
                    result.get("iac_type", "?"),
                    result.get("risk_score", "?"),
                )
            except Exception:
                log.exception("IDR IaC review failed: upload=%s", upload_id)
                sm.set_upload_status(upload_id, "error", error_msg="iac_review failed")
        else:
            log.debug("No IaC reviewer configured for domain=%s", domain)
            sm.set_upload_status(upload_id, "analyzed")

    # ── Email — extract body text and run entity extraction ───────────────────
    elif upload_type == "email":
        try:
            from tools.docgen.context_builder import _extract_email
            email_text = _extract_email(file_path)
            if email_text.strip():
                extraction = stage0_ingest_document(session_id, email_text[:8000])
                log.info(
                    "IDR email extraction: upload=%s entities=%d findings=%d",
                    upload_id,
                    len(extraction.get("entities", [])),
                    len(extraction.get("key_findings", [])),
                )
        except Exception as exc:
            log.warning("IDR email extraction skipped for %s: %s", upload_id, exc)
        sm.set_upload_status(upload_id, "analyzed")

    # ── Doc / supplement — LLM entity extraction ──────────────────────────────
    elif upload_type in ("doc", "supplement"):
        try:
            from tools.docgen.context_builder import _extract_text_from_file
            doc_text = _extract_text_from_file(file_path)
            if doc_text.strip():
                extraction = stage0_ingest_document(session_id, doc_text[:8000])
                entity_count = len(extraction.get("entities", []))
                finding_count = len(extraction.get("key_findings", []))
                log.info(
                    "IDR doc extraction: upload=%s entities=%d findings=%d",
                    upload_id, entity_count, finding_count,
                )
        except Exception as exc:
            log.warning("IDR doc extraction skipped for %s: %s", upload_id, exc)
        sm.set_upload_status(upload_id, "analyzed")

    else:
        sm.set_upload_status(upload_id, "analyzed")

    return analyses_created


# ─── Item 10: Semantic conflict detection ────────────────────────────────────

# Keyword contradiction pairs for fallback when LLM is unavailable.
_CONTRADICTION_PAIRS: list[tuple[str, str]] = [
    ("encryption enabled", "encryption disabled"),
    ("encryption required", "encryption not required"),
    ("sha-256", "md5"),
    ("fips 140", "non-fips"),
    ("allow", "deny"),
    ("enabled", "disabled"),
    ("required", "not required"),
    ("encrypted", "plaintext"),
    ("tls 1.3", "tls 1.0"),
    ("tls 1.2", "ssl 3"),
    ("authenticated", "unauthenticated"),
    ("authorized", "unauthorized"),
]


def detect_semantic_conflicts(doc_text: str) -> list[dict]:
    """Detect cross-section contradictions using LLM or keyword fallback.

    Splits the document into sections (up to 6) and checks each pair for
    contradictions.  Returns a list of conflict dicts:
        {"section_a": str, "section_b": str, "description": str,
         "severity": "warning" | "error"}

    Caps at 15 section pairs (C(6,2)=15).  Returns [] on any exception.
    """
    import re as _re

    if not doc_text or not doc_text.strip():
        return []

    # Split on markdown headers.
    raw_parts = _re.split(r"\n#{1,3}\s+", doc_text)
    sections = [p.strip() for p in raw_parts if p.strip()]
    if len(sections) <= 1:
        return []

    # Cap to first 6 sections to keep pairs ≤ 15.
    sections = sections[:6]
    conflicts: list[dict] = []

    _llm_available = True
    try:
        # Governed facade (see stage0_ingest_document): trusted first-party
        # section text, so trusted_content=True keeps the old skip_injection_scan
        # semantics while adding provenance/audit/egress governance.
        from tools.cortex import api as _cortex_api
        from tools.cortex.schemas import CortexContext as _CortexContext
    except ImportError:
        _llm_available = False

    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            sec_a = sections[i]
            sec_b = sections[j]

            if _llm_available:
                try:
                    prompt = (
                        "Do the following two document sections contradict each other? "
                        "Return JSON only: {\"contradicts\": true/false, \"description\": \"...\"}.\n\n"
                        f"Section A (first 600 chars):\n{sec_a[:600]}\n\n"
                        f"Section B (first 600 chars):\n{sec_b[:600]}"
                    )
                    cx = _cortex_api.complete(
                        prompt,
                        function="detect_semantic_conflicts",
                        ctx=_CortexContext(domain="document", agent_id="docgen",
                                           trusted_content=True),
                        max_tokens=128,
                        temperature=0.0,
                    )
                    raw = (cx.text or "").strip()
                    raw = _re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
                    import json as _json
                    data = _json.loads(raw)
                    if data.get("contradicts"):
                        conflicts.append({
                            "section_a": sec_a[:120],
                            "section_b": sec_b[:120],
                            "description": str(data.get("description", "Contradiction detected")),
                            "severity": "error",
                        })
                    continue
                except Exception:
                    _llm_available = False  # fall through to keyword fallback

            # Keyword fallback
            combined_a = sec_a.lower()
            combined_b = sec_b.lower()
            for kw_a, kw_b in _CONTRADICTION_PAIRS:
                if kw_a in combined_a and kw_b in combined_b:
                    conflicts.append({
                        "section_a": sec_a[:120],
                        "section_b": sec_b[:120],
                        "description": f"Potential contradiction: '{kw_a}' vs '{kw_b}'",
                        "severity": "warning",
                    })
                    break
                if kw_b in combined_a and kw_a in combined_b:
                    conflicts.append({
                        "section_a": sec_a[:120],
                        "section_b": sec_b[:120],
                        "description": f"Potential contradiction: '{kw_b}' vs '{kw_a}'",
                        "severity": "warning",
                    })
                    break

    return conflicts


# ─── Stage 3: Conflict check ──────────────────────────────────────────────────

def stage3_check_gate(session_id: str) -> bool:
    """Return True if all conflicts are resolved (gate passes)."""
    pending = sm.pending_conflict_count(session_id)
    if pending > 0:
        log.info("IDR stage-3 gate BLOCKED: session=%s pending_conflicts=%d", session_id, pending)
    return pending == 0


# ─── Item 11: ATO boundary context enrichment ────────────────────────────────

def stage1_enrich_boundary_context(session_id: str, context: dict) -> dict:
    """Enrich context for boundary_narrative doc type with trust zone data.

    If context["doc_type"] == "boundary_narrative", queries the network canvas
    for VLAN/segment/firewall/DMZ nodes and injects them as trust_zone_summary.
    Silently skips on any DB/import error.  Returns context (modified or original).
    """
    if context.get("doc_type") != "boundary_narrative":
        return context

    if context.get("trust_zone_summary"):
        return context  # already enriched

    try:
        from tools.network.db.init_db import get_connection as _nc_conn
        conn = _nc_conn()
        rows = conn.execute(
            "SELECT label, node_type, cidr FROM nc_nodes "
            "WHERE node_type IN ('vlan', 'segment', 'firewall', 'dmz') LIMIT 50"
        ).fetchall()
        trust_zones = [
            {"label": r["label"], "node_type": r["node_type"], "cidr": r.get("cidr", "")}
            for r in rows
        ]
        context["trust_zone_summary"] = trust_zones
        log.info(
            "IDR boundary enrich: session=%s trust_zones=%d", session_id, len(trust_zones)
        )
    except Exception as exc:
        log.debug("IDR boundary enrich skipped: %s", exc)

    return context


# ─── Stage 4-5: ACE multi-coworker doc generation ─────────────────────────────

def stage5_ace_generate(
    session_id: str,
    context: dict[str, Any],
    role_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Launch ACE multi-coworker doc generation (non-blocking).

    Spawns an ACEController instance whose coworkers generate the document
    sections in parallel (technical_writer, network_engineer, etc.).
    Returns immediately with the instance_id so the caller can poll.

    Args:
        session_id: IDR session being generated.
        context:    Unified context dict from context_builder.build_context().
        role_ids:   Optional list of ACE role IDs to override defaults.

    Returns:
        {"instance_id": str, "status": "launched" | "unavailable"}
    """
    doc_type = context.get("doc_type", "runbook")

    # Enrich boundary_narrative context with trust zone data from network canvas.
    if doc_type == "boundary_narrative" and not context.get("trust_zone_summary"):
        context = stage1_enrich_boundary_context(session_id, context)

    ato_config = get_ato_doc_type(doc_type)

    # ATO doc types override roles and inject section structure (unless caller passed role_ids).
    if role_ids is None:
        if ato_config:
            role_ids = list(ato_config["roles"])
        else:
            role_ids = ["technical_writer", "network_engineer"]

    sections_hint = ""
    if ato_config:
        sections_hint = f"Required sections: {', '.join(ato_config['sections'])}\n\n"

    poam_hint = ""
    if doc_type == "stig_checklist" and context.get("nqe_poam_items"):
        import json as _json
        items = context["nqe_poam_items"]
        if isinstance(items, (list, dict)):
            poam_hint = f"NQE POAM items:\n{_json.dumps(items, indent=2)[:4000]}\n\n"
        else:
            poam_hint = f"NQE POAM items:\n{str(items)[:4000]}\n\n"

    problem_text = (
        f"{sections_hint}"
        f"Generate a complete {doc_type} document titled "
        f"'{context.get('title', 'Network Documentation')}' for domain "
        f"'{context.get('domain', 'network')}'.\n\n"
        f"Classification: {context.get('classification', 'CUI')}\n\n"
        f"{poam_hint}"
        f"Context summary:\n{context.get('query_string', '')[:_ACE_CONTEXT_MAX_CHARS]}"
    )

    try:
        from icdev.tools.ace.controller import ACEController

        ctrl = ACEController.get_instance()
        instance_id = ctrl.launch(
            problem_text=problem_text,
            trigger_source="idr",
            trigger_ref=session_id,
            user_id="idr_workflow",
            project_id=session_id,
            role_ids=role_ids,
        )
        sm.set_field(session_id, ace_instance_id=instance_id)
        log.info(
            "IDR ACE generate launched: session=%s instance=%s roles=%s",
            session_id, instance_id, role_ids,
        )
        return {"instance_id": instance_id, "status": "launched"}
    except ImportError:
        log.warning("ACEController not available — skipping ACE generation for session=%s", session_id)
        return {"instance_id": None, "status": "unavailable"}
    except Exception:
        log.exception("IDR ACE generate failed: session=%s", session_id)
        return {"instance_id": None, "status": "error"}


# ─── AI classification suggestion (Items 4 & 9) ──────────────────────────────

_CLS_FALLBACK = {"classification": "CUI", "confidence": 0.5, "rationale": "default"}

_CLS_SUGGEST_PROMPT = (
    "You are a document classification assistant for a US federal government system.\n"
    "Analyse the following document excerpt and determine the appropriate classification level.\n\n"
    "Classification levels (ascending): PUBLIC, CUI, SECRET, TOP SECRET, TS//SCI\n\n"
    "High-sensitivity indicators: SIPR, TS//SCI, SCI, SAP, SAR, NOFORN, IC system names, "
    "NSA/NRO/NGA/CIA references, nuclear/weapons data.\n"
    "SECRET indicators: SECRET markings, FOUO alongside sensitive ops, classified system configs.\n"
    "CUI indicators: PII, ITAR, FOUO, pre-decisional budget, law enforcement, contractor proprietary.\n"
    "PUBLIC: no controlled markings, fully releasable.\n\n"
    "Respond in JSON only: {\"classification\": \"<level>\", \"confidence\": <0.0-1.0>, \"rationale\": \"<one sentence>\"}\n\n"
    "Document excerpt:\n{text}"
)


def suggest_classification(text_sample: str) -> dict:
    """Use LLM to suggest a classification level from document content.

    Returns {"classification": str, "confidence": float, "rationale": str}.
    Confidence < 0.85 means the UI should require human confirmation.
    Falls back to CUI/0.5 on any error.
    """
    if not text_sample or not text_sample.strip():
        return _CLS_FALLBACK.copy()

    try:
        # Governed facade (see stage0_ingest_document): trusted first-party
        # document excerpt, so trusted_content=True keeps skip_injection_scan
        # semantics while adding provenance/audit/egress governance.
        from tools.cortex import api as cortex_api
        from tools.cortex.schemas import CortexContext
        import json as _json

        prompt = _CLS_SUGGEST_PROMPT.format(text=text_sample[:3000])
        cx = cortex_api.complete(
            prompt,
            function="classification_suggest",
            ctx=CortexContext(domain="document", agent_id="docgen",
                              trusted_content=True),
            max_tokens=128,
            temperature=0.0,
        )
        raw = (cx.text or "").strip()

        # Strip code fences if present
        import re as _re
        raw = _re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

        data = _json.loads(raw)
        cls_level = str(data.get("classification", "CUI")).upper()
        confidence = float(data.get("confidence", 0.5))
        rationale = str(data.get("rationale", "LLM suggestion"))

        # Clamp confidence
        confidence = max(0.0, min(1.0, confidence))

        return {"classification": cls_level, "confidence": confidence, "rationale": rationale}

    except (ImportError, Exception) as exc:
        log.debug("suggest_classification fallback: %s", exc)
        return _CLS_FALLBACK.copy()


def stage2_suggest_classification(session_id: str, text_sample: str) -> dict:
    """Run LLM classification suggestion and persist result to session.

    High-confidence (>=0.85) suggestions are written to session.suggested_classification
    so Stage 1 UI can pre-populate the classification selector.
    Always writes suggested_classification_confidence.

    Returns the suggestion dict {"classification", "confidence", "rationale"}.
    """
    result = suggest_classification(text_sample)
    confidence = result.get("confidence", 0.0)

    if confidence >= 0.85:
        sm.set_field(session_id, suggested_classification=result["classification"])
        log.info(
            "IDR classification pre-fill: session=%s cls=%s confidence=%.2f",
            session_id, result["classification"], confidence,
        )
    sm.set_field(session_id, suggested_classification_confidence=confidence)
    return result


# ─── Stage 6: WriteGuard gate ─────────────────────────────────────────────────

def stage6_writeguard(
    session_id: str,
    doc_text: str,
    domain: str,
) -> dict[str, Any]:
    """Run WriteGuard with an auto-fix loop (up to _WG_MAX_RETRIES rewrites).

    Returns {passed, score, result, fixed_text, attempts, blocked, ace_regen_needed}.
    On pass:  passed=True, ace_regen_needed=False.
    On block: passed=False, blocked=True, ace_regen_needed=True (all retries used).
    """
    get_writeguard_mode(domain)  # validates domain is known before attempting
    # Resolve doc_type and classification for the OKB policy gate.
    _session = sm.get_session(session_id) or {}
    _doc_type = _session.get("doc_type")
    _classification = _session.get("classification", "CUI")

    try:
        from tools.pulse.writeguard import run_full_quality_check, rewrite_content

        current_text = doc_text
        last_result: dict[str, Any] = {}
        last_score = 0.0

        # Run initial check then up to _WG_MAX_RETRIES rewrite-and-recheck cycles.
        for attempt in range(_WG_MAX_RETRIES + 1):
            last_result = run_full_quality_check(current_text)
            last_score = float(last_result.get("overall_score", 0))

            if last_score >= _WG_MIN_SCORE:
                # OKB policy gate — only for ATO doc types with YAML constraints.
                # Regular doc types (runbook, etc.) skip the policy gate entirely.
                from tools.docgen.domain_profiles import get_ato_doc_type as _gat
                _ato_cfg = _gat(_doc_type)
                if _ato_cfg is not None:
                    policy_result = policy_check(current_text, _doc_type, _classification)
                else:
                    policy_result = {"passed": True, "violations": [], "warnings": []}
                # TRUST gate (cnr-doc-01): surface citation/placeholder defects on
                # the passing text so reviewers see them before the publish gate.
                _cite_findings, _ph_findings = assembled_doc_findings(current_text)
                log.info(
                    "IDR WriteGuard: session=%s score=%.1f PASS (attempt=%d) policy=%s "
                    "citation_defects=%d placeholder_defects=%d",
                    session_id, last_score, attempt,
                    "PASS" if policy_result["passed"] else "FAIL",
                    len(_cite_findings), len(_ph_findings),
                )
                return {
                    "passed": policy_result["passed"],
                    "score": last_score,
                    "result": last_result,
                    "fixed_text": current_text,
                    "attempts": attempt,
                    "blocked": not policy_result["passed"],
                    "ace_regen_needed": False,
                    "policy_violations": policy_result["violations"],
                    "policy_warnings": policy_result["warnings"],
                    "citation_findings": _cite_findings,
                    "placeholder_findings": _ph_findings,
                }

            if attempt >= _WG_MAX_RETRIES:
                break  # no more rewrites allowed

            # Auto-fix: apply deterministic rewrites and re-check next iteration.
            rewrite_result = rewrite_content(current_text, last_result)
            fixed_text = rewrite_result.get("rewritten_text", current_text)

            # Scope bounding: reject rewrite if overeager agent touched >30% of
            # non-failing sections (arxiv "Overeager Coding Agents" signal).
            failed_checks = [
                c.get("name", "") for c in last_result.get("checks", [])
                if c.get("status") == "fail"
            ]
            if not _diff_scope_check(current_text, fixed_text, failed_sections=failed_checks):
                log.warning(
                    "IDR WriteGuard: session=%s attempt=%d scope-check ABORTED rewrite "
                    "(overeager agent rewrote >30%% of non-failing sections)",
                    session_id, attempt + 1,
                )
                fixed_text = current_text  # revert; next loop re-checks original

            log.info(
                "IDR WriteGuard: session=%s score=%.1f FAIL → auto-fix %d/%d",
                session_id, last_score, attempt + 1, _WG_MAX_RETRIES,
            )
            current_text = fixed_text

        # All rewrite attempts exhausted — ACE regen required.
        log.warning(
            "IDR WriteGuard: session=%s score=%.1f BLOCKED after %d attempts → ACE regen needed",
            session_id, last_score, _WG_MAX_RETRIES,
        )
        return {
            "passed": False,
            "score": last_score,
            "result": last_result,
            "fixed_text": current_text,
            "attempts": _WG_MAX_RETRIES,
            "blocked": True,
            "ace_regen_needed": True,
            "policy_violations": [],
            "policy_warnings": [],
            "citation_findings": [],
            "placeholder_findings": [],
        }

    except ImportError:
        # cnr-doc-02: a missing WriteGuard engine must FAIL CLOSED — "quality
        # check unavailable" can never be reported as "quality passed". Block the
        # gate and surface the reason so publish cannot proceed.
        log.error("WriteGuard not available (ImportError) — FAILING CLOSED (gate blocked)")
        return {
            "passed": False, "score": 0,
            "result": {"error": "WriteGuard unavailable (ImportError) — gate failed closed"},
            "fixed_text": doc_text,
            "attempts": 0, "blocked": True, "ace_regen_needed": False,
            "policy_violations": [], "policy_warnings": [],
            "citation_findings": [], "placeholder_findings": [],
            "writeguard_unavailable": True,
        }
    except Exception as exc:
        log.exception("IDR WriteGuard exception: session=%s", session_id)
        return {
            "passed": False, "score": 0, "result": {"error": str(exc)}, "fixed_text": doc_text,
            "attempts": 0, "blocked": False, "ace_regen_needed": False,
            "policy_violations": [], "policy_warnings": [],
            "citation_findings": [], "placeholder_findings": [],
        }


def stage6_trigger_ace_regen(session_id: str) -> dict[str, Any]:
    """Kick the session back to stage 5 (generating) for ACE regeneration.

    Called when WriteGuard is blocked after exhausting all auto-fix retries.
    Clears wg_result_id so the gate re-arms for the regenerated document.
    """
    sm.set_field(session_id, wg_result_id=None)
    log.warning(
        "IDR ACE regen triggered: session=%s → rewinding to stage 5", session_id
    )
    return kickback(
        session_id, 5, reason="WriteGuard blocked after max auto-fix retries — ACE regen"
    )


def stage6_check_gate(session_id: str) -> bool:
    """Return True if WriteGuard has passed for this session (wg_result_id is set)."""
    session = sm.get_session(session_id)
    if not session:
        return False
    return bool(session.get("wg_result_id"))


# ─── Stage 8: Publish all formats ─────────────────────────────────────────────

def stage8_publish(
    session_id: str,
    doc_text: str,
    title: str,
    output_dir: str | None = None,
    classification: str = "CUI",
) -> list[dict[str, Any]]:
    """Export the approved document to HTML, DOCX, PDF.

    Returns list of idr_artifacts rows (one per format).
    """
    if not output_dir:
        output_dir = str(
            pathlib.Path("data") / "docgen" / "artifacts" / session_id
        )
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Append compliance framework stamp to all export formats (item 5).
    doc_text = _append_compliance_stamp(doc_text, classification)

    artifacts = []
    _try_export_html(session_id, doc_text, title, output_dir, classification, artifacts)
    _try_export_docx(session_id, doc_text, title, output_dir, classification, artifacts)
    _try_export_pdf(session_id, doc_text, title, output_dir, classification, artifacts)

    sm.advance_stage(session_id, 8, "published")

    # If this session was orchestrated by an ACE instance, generate an evidence
    # report so the publish event is captured in the ace_audit_log trail.
    session = sm.get_session(session_id)
    ace_instance_id = session and session.get("ace_instance_id")
    if ace_instance_id:
        try:
            from icdev.tools.ace.evidence_report import generate as _ev_generate
            _ev_generate(
                ace_instance_id,
                fmt="json",
                publish_meta={
                    "idr_session_id": session_id,
                    "artifact_count": len(artifacts),
                    "formats": [a.get("format") for a in artifacts if a.get("format")],
                },
            )
        except Exception:
            log.debug(
                "IDR evidence_report.generate skipped for ace_instance=%s",
                ace_instance_id,
                exc_info=True,
            )

    log.info("IDR published: session=%s artifacts=%d", session_id, len(artifacts))
    return artifacts


_CLS_BANNER_STYLES: dict[str, str] = {
    "PUBLIC":          "background:#0d3b1e;color:#4caf50;border:2px solid #2e7d32",
    "UNCLASSIFIED":    "background:#0d3b1e;color:#4caf50;border:2px solid #2e7d32",
    "CUI":             "background:#0d2b4e;color:#4fc3f7;border:2px solid #1565c0",
    "SECRET":          "background:#3e1a00;color:#ff9800;border:2px solid #e65100",
    "TOP SECRET":      "background:#3b0a0a;color:#ef5350;border:2px solid #b71c1c",
    "TOP SECRET//SCI": "background:#2a0a3b;color:#ce93d8;border:2px solid #6a1b9a",
}


def _sanitize_html(html_str: str) -> str:
    """Strip stored-XSS vectors from rendered markdown (cnr-doc-03).

    Removes <script>/<style>/<iframe>/<object>/<embed>/<svg>/<form> and similar
    active elements, inline on* event-handler attributes, and javascript:/vbscript:/
    data: URIs in href/src. Pure-regex — no external deps (air-gap safe). Markdown
    passes raw HTML through by default, so this runs on the RENDERED output.
    """
    import re as _re

    if not html_str:
        return ""
    s = html_str
    _dangerous = r"script|style|iframe|object|embed|svg|math|template|link|meta|base|form|input|button|textarea|noscript|frame|frameset|applet"
    # Remove dangerous element blocks (opening tag through matching close).
    s = _re.sub(rf"(?is)<\s*({_dangerous})\b.*?<\s*/\s*\1\s*>", "", s)
    # Remove any remaining dangerous opening/void/closing tags.
    s = _re.sub(rf"(?is)<\s*/?\s*({_dangerous})\b[^>]*>", "", s)
    # Strip inline event-handler attributes (onload=, onerror=, onclick=, …).
    s = _re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", s)
    # Neutralize javascript:/vbscript:/data: URIs in href/src.
    s = _re.sub(r"(?i)(href|src)\s*=\s*([\"']?)\s*(?:javascript|vbscript|data)\s*:[^\"'>\s]*", r"\1=\2#", s)
    return s


def _try_export_html(session_id, text, title, out_dir, classification, artifacts):
    try:
        import html as _html

        html_path = str(pathlib.Path(out_dir) / "document.html")
        cls_upper = (classification or "CUI").upper()
        banner_style = _CLS_BANNER_STYLES.get(cls_upper, _CLS_BANNER_STYLES["CUI"])
        banner_css = f"{banner_style};padding:6px 12px;font-size:13px;font-weight:bold;text-align:center;letter-spacing:1px;"
        # cnr-doc-03: escape untrusted title/classification and sanitize the rendered
        # markdown body so a hostile document title or embedded HTML can't inject XSS
        # into the exported artifact.
        title = _html.escape(str(title or ""))
        classification = _html.escape(str(classification or "CUI"))
        try:
            import markdown as _md
            body_html = _sanitize_html(_md.markdown(text, extensions=["tables", "fenced_code"]))
        except Exception:
            body_html = f"<pre>{_html.escape(text)}</pre>"
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body{{font-family:Arial,sans-serif;margin:0;padding:0;background:#fff;color:#111;}}
    .cls-banner{{{banner_css}}}
    .doc-body{{max-width:900px;margin:24px auto;padding:0 24px;}}
    h1,h2,h3{{border-bottom:1px solid #e0e0e0;padding-bottom:4px;}}
    h1{{font-size:22px;border-bottom:2px solid #ccc;padding-bottom:8px;}}
    .content{{line-height:1.7;}}
    .content table{{border-collapse:collapse;width:100%;}}
    .content th,.content td{{border:1px solid #ccc;padding:6px 10px;}}
    .content th{{background:#f0f4f8;}}
    .content ul,.content ol{{padding-left:20px;}}
    .content code{{background:#f5f5f5;padding:2px 4px;border-radius:3px;}}
    .content pre{{background:#f5f5f5;padding:12px;border-radius:4px;overflow-x:auto;}}
  </style>
</head>
<body>
  <div class="cls-banner">{classification}</div>
  <div class="doc-body">
    <h1>{title}</h1>
    <div class="content">{body_html}</div>
  </div>
  <div class="cls-banner">{classification}</div>
</body>
</html>"""
        pathlib.Path(html_path).write_text(html, encoding="utf-8")
        row = sm.add_artifact(session_id, "html", file_path=html_path)
        artifacts.append(row)
    except Exception:
        log.exception("IDR HTML export failed: session=%s", session_id)


def _try_export_docx(session_id, text, title, out_dir, classification, artifacts):
    try:
        from tools.presentations.generate_exec_doc import generate_docx

        docx_path = str(pathlib.Path(out_dir) / "document.docx")
        # Wrap content with classification markings (header + footer lines)
        marked_content = f"{classification}\n\n{text}\n\n{classification}"
        generate_docx(title=title, content=marked_content, output_path=docx_path)
        row = sm.add_artifact(session_id, "docx", file_path=docx_path)
        artifacts.append(row)
    except ImportError:
        log.debug("generate_exec_doc not available — skipping DOCX export")
    except Exception:
        log.exception("IDR DOCX export failed: session=%s", session_id)


def _try_export_pdf(session_id, text, title, out_dir, classification, artifacts):
    try:
        import importlib.util

        # cnr-doc-04(d): export_to_pdf silently writes an HTML file (document.pdf →
        # document.html) when fpdf2 is missing — which would BOTH overwrite the real
        # HTML artifact AND be mislabelled 'pdf'. Only attempt a genuine PDF when
        # fpdf2 is present; the HTML artifact is already produced by _try_export_html.
        if importlib.util.find_spec("fpdf") is None:
            log.info("IDR PDF export skipped: fpdf2 not installed (HTML already exported)")
            return

        from tools.network.pdf_export import export_to_pdf

        pdf_path = str(pathlib.Path(out_dir) / "document.pdf")
        export_to_pdf(content=text, output_path=pdf_path, title=title, classification=classification)
        if pathlib.Path(pdf_path).is_file():
            row = sm.add_artifact(session_id, "pdf", file_path=pdf_path)
            artifacts.append(row)
        else:
            log.warning(
                "IDR PDF export produced no .pdf (fpdf2 fallback?) — not recording a 'pdf' artifact: session=%s",
                session_id,
            )
    except ImportError:
        log.debug("pdf_export not available — skipping PDF export")
    except Exception:
        log.exception("IDR PDF export failed: session=%s", session_id)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_collection_id(session_id: str) -> str:
    session = sm.get_session(session_id)
    if session and session.get("dic_collection_id"):
        return session["dic_collection_id"]
    col_id = f"idr-{session_id}"
    sm.set_field(session_id, dic_collection_id=col_id)
    return col_id
