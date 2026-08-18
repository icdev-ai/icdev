#!/usr/bin/env python3
"""DocDrift — is this document still true? (DIC flagship).

[TEMPLATE: CUI // SP-CTI]

**Naming.** This was ACOIC ("Autonomous Compliance-Of-Impact Coupler") while it
only understood network-infrastructure drift. It is now fed by every docmod pack
— network, crypto, software, policy, approved-change records and cited evidence —
so the user-facing name is **DocDrift** and the page lives at
``/document-intelligence/docdrift`` (``/acoic`` 301-redirects).

The module file and the tables (``dic_drift_events``, ``dic_acoic_regen_queue``,
``dic_ssp_fragments``) deliberately keep the old name. Renaming them buys nothing
a user can see and costs a real migration plus an import churn across
drift_detector, ndc_topology_drift, drift_bridge and the DIC blueprint — and
``_ensure_schema`` uses CREATE TABLE IF NOT EXISTS, so a missed call site would
silently recreate the old table alongside the new one and split the data rather
than fail loudly. If you are here because "acoic" looked stale: it is, and that
is on purpose.

ACOIC is the bridge that turns a *canvas drift event* into *compliance work*:

    drift detected  ->  document impact scored  ->  regeneration queued (HITL)
                    ->  affected NIST 800-53 controls re-mapped (RICOAS crosswalk)
                    ->  cited SSP fragments drafted (CoD-verified, AI-labeled, HITL-gated)

This module owns the **dic-acoic-02** scope: the RICOAS / NIST 800-53 bridge and
SSP-fragment generation. It also carries the minimal **dic-acoic-01** base
(drift-event recording + impact scoring + regen queue) so the bridge is
runnable end-to-end.

**How drift actually arrives here.** The producer is the ``ndc_topology_drift``
Genesis reflex (``tools/genesis/reflexes/ndc_topology_drift.py``), which diffs a
topology's generated device configs against its saved ``nc_versions`` baseline
and calls :func:`tools.network.drift_detector.emit_drift_events` ->
:func:`record_drift_event` / :func:`enqueue_regen`. Do **not** wire this via
``event_bus.subscribe('dic', ...)``: ``_LISTENERS`` is a process-local registry,
and the reflex runs in the genesis daemon while DIC runs in Flask, so an
in-process subscriber would never fire. :func:`handle_drift` remains the sink for
direct/programmatic callers (e.g. the IDC feed and the ACOIC CLI).

Design principles (mirrors :mod:`tools.document_intelligence.verifier`):

* **Reuse, don't reinvent.** Control mapping goes through the existing RICOAS /
  NIST 800-53 crosswalk engine (:mod:`tools.compliance.crosswalk_engine`) and
  the compliance knowledge graph (:mod:`tools.knowledge_graph.compliance_graph`).
  Anti-hallucination goes through :func:`tools.document_intelligence.verifier.verify`.
* **Deterministic-first.** Every step has an air-gap / headless fallback. The
  LLM is optional; when no provider is reachable the SSP draft is built from a
  cited deterministic template and still passes through the CoD verifier.
* **Nothing ships un-reviewed.** Generated fragments are persisted as
  ``origin='ai_generated'``, ``ai_labeled=1``, ``status='pending_review'``.
  A human must :func:`approve_fragment` before the fragment is authoritative.

Tables (all RLS-compatible — they carry ``tenant_id`` / ``classification`` like
the rest of the DIC schema, so they use ``get_connection`` not a canvas conn):

* ``dic_drift_events``       — recorded canvas drift events (acoic-01 base)
* ``dic_acoic_regen_queue``  — impacted documents awaiting HITL regeneration
* ``dic_ssp_fragments``      — drafted, CoD-verified, AI-labeled SSP fragments
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repo root on path when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Canonical storage (RLS-aware). The DIC tables carry tenant_id/classification
# so the global row predicate applies cleanly, exactly like ingest_orchestrator.
try:  # pragma: no cover - import shape varies by install layout
    from icdev.tools.db.storage import get_connection
except Exception:  # backward-compat shim
    from tools.db.storage import get_connection

# Anti-hallucination gate (CoD claim replay + citation validation).
try:  # pragma: no cover
    from icdev.tools.document_intelligence.verifier import verify as _verify
except Exception:
    from tools.document_intelligence.verifier import verify as _verify


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Drift severity -> (impact_level, numeric impact_score). The score drives the
# regen-queue ordering; the level is what the ACOIC page renders.
_SEVERITY_IMPACT = {
    "critical": ("high", 0.95),
    "high": ("high", 0.80),
    "major": ("high", 0.75),
    "medium": ("moderate", 0.50),
    "moderate": ("moderate", 0.50),
    "minor": ("low", 0.25),
    "low": ("low", 0.20),
    "info": ("low", 0.10),
}
_DEFAULT_IMPACT = ("moderate", 0.50)

# Regen-queue lifecycle.
_QUEUE_STATES = ("queued", "regenerating", "drafted", "approved", "rejected")

# SSP-fragment HITL lifecycle.
_FRAGMENT_STATES = ("pending_review", "approved", "rejected")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS dic_drift_events (
        event_id        TEXT PRIMARY KEY,
        source          TEXT NOT NULL,
        entity          TEXT,
        severity        TEXT,
        detected_at     TEXT NOT NULL,
        payload_json    TEXT,
        processed       INTEGER NOT NULL DEFAULT 0,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_acoic_regen_queue (
        item_id         TEXT PRIMARY KEY,
        document_id     TEXT NOT NULL,
        event_id        TEXT,
        drift_source    TEXT,
        drift_entity    TEXT,
        impact_level    TEXT,
        impact_score    REAL,
        state           TEXT NOT NULL DEFAULT 'queued',
        queued_at       TEXT NOT NULL,
        updated_at      TEXT,
        ssp_fragment_id TEXT,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_ssp_fragments (
        fragment_id     TEXT PRIMARY KEY,
        document_id     TEXT,
        control_id      TEXT NOT NULL,
        frameworks_json TEXT,
        fragment_text   TEXT,
        status          TEXT NOT NULL DEFAULT 'pending_review',
        assigned_to     TEXT,
        origin          TEXT NOT NULL DEFAULT 'ai_generated',
        ai_labeled      INTEGER NOT NULL DEFAULT 1,
        verified        INTEGER NOT NULL DEFAULT 0,
        abstained       INTEGER NOT NULL DEFAULT 0,
        verify_reason   TEXT,
        citations_json  TEXT,
        cod_verdict_json TEXT,
        regen_item_id   TEXT,
        created_at      TEXT NOT NULL,
        reviewed_by     TEXT,
        reviewed_at     TEXT,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
]


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    for ddl in _SCHEMA:
        cur.execute(ddl)
    conn.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hid(prefix: str, *parts: str) -> str:
    h = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{h}"


def _score_impact(severity: str | None) -> tuple[str, float]:
    """Map a drift severity to (impact_level, impact_score)."""
    return _SEVERITY_IMPACT.get((severity or "").lower(), _DEFAULT_IMPACT)


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        for key in ("content", "text", "chunk_text", "body", "passage"):
            v = chunk.get(key)
            if isinstance(v, str) and v.strip():
                return v
        return ""
    for key in ("content", "text", "chunk_text", "body", "passage"):
        v = getattr(chunk, key, None)
        if isinstance(v, str) and v.strip():
            return v
    return ""


# --------------------------------------------------------------------------- #
# acoic-01 base: drift recording + impact scoring + regen queue
# --------------------------------------------------------------------------- #

def record_drift_event(
    source: str,
    entity: str | None = None,
    severity: str = "medium",
    *,
    payload: dict | None = None,
    dedup_key: str | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
) -> str:
    """Persist a canvas drift event. Returns the event_id.

    This is the sink for the ``ndc.topology.drift_detected`` feed (wired in
    :func:`handle_drift` and called directly by the ``ndc_topology_drift``
    reflex via :func:`tools.network.drift_detector.emit_drift_events`).

    Args:
        dedup_key: Stable content key for cross-run idempotency. Without it the
            event_id hashes ``detected_at``, so a scheduled producer re-reporting
            the SAME unchanged drift inserts a new row every run. Callers on a
            cadence MUST pass a content-derived key (e.g. topology|device|
            category|baseline_hash|current_hash). Omitted => legacy behavior.
    """
    detected_at = _now()
    event_id = (
        _hid("dic_drift", dedup_key)
        if dedup_key
        else _hid("dic_drift", source, entity or "", detected_at)
    )
    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.cursor().execute(
            """
            INSERT OR REPLACE INTO dic_drift_events
                (event_id, source, entity, severity, detected_at, payload_json,
                 processed, tenant_id, classification)
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s)
            """,
            (
                event_id, source, entity, severity, detected_at,
                json.dumps(payload or {}), tenant_id, classification,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return event_id


def enqueue_regen(
    document_id: str,
    *,
    event_id: str | None = None,
    drift_source: str | None = None,
    drift_entity: str | None = None,
    severity: str = "medium",
    dedup_key: str | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
) -> dict[str, Any]:
    """Enqueue an impacted document for HITL regeneration.

    Args:
        dedup_key: Stable content key for cross-run idempotency (see
            :func:`record_drift_event`). When set, an existing row is left
            untouched rather than replaced — re-running the producer must never
            reset a queue item a human already moved to drafted/approved.
            Omitted => legacy replace-on-conflict behavior.
    """
    impact_level, impact_score = _score_impact(severity)
    queued_at = _now()
    item_id = (
        _hid("dic_regen", dedup_key)
        if dedup_key
        else _hid("dic_regen", document_id, event_id or "", queued_at)
    )
    # OR IGNORE preserves human-advanced state; OR REPLACE would stomp it back
    # to 'queued'. storage.translate_sql maps both to the PG ON CONFLICT form.
    verb = "INSERT OR IGNORE" if dedup_key else "INSERT OR REPLACE"
    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.cursor().execute(
            f"""
            {verb} INTO dic_acoic_regen_queue
                (item_id, document_id, event_id, drift_source, drift_entity,
                 impact_level, impact_score, state, queued_at, updated_at,
                 tenant_id, classification)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s)
            """,
            (
                item_id, document_id, event_id, drift_source, drift_entity,
                impact_level, impact_score, queued_at, queued_at,
                tenant_id, classification,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "item_id": item_id,
        "document_id": document_id,
        "impact_level": impact_level,
        "impact_score": impact_score,
        "state": "queued",
    }


def _set_queue_state(item_id: str, state: str, *, fragment_id: str | None = None) -> None:
    if state not in _QUEUE_STATES:
        raise ValueError(f"invalid queue state: {state}")
    conn = get_connection()
    try:
        _ensure_schema(conn)
        if fragment_id is not None:
            conn.cursor().execute(
                "UPDATE dic_acoic_regen_queue SET state = %s, updated_at = %s, "
                "ssp_fragment_id = %s WHERE item_id = %s",
                (state, _now(), fragment_id, item_id),
            )
        else:
            conn.cursor().execute(
                "UPDATE dic_acoic_regen_queue SET state = %s, updated_at = %s "
                "WHERE item_id = %s",
                (state, _now(), item_id),
            )
        conn.commit()
    finally:
        conn.close()


def handle_drift(event: dict, ctx: Any = None) -> dict[str, Any]:
    """Reflex / subscription handler for a canvas drift event.

    Called directly by producers (the docmod drift bridge, the IDC feed, the CLI).
    ``event`` is expected to carry at least a ``source`` and ``severity``;
    ``document_id`` and ``control_ids`` are optional and drive enqueue + control
    re-map respectively.

    ``dedup_key`` is optional but REQUIRED for any producer on a schedule: without
    it the event id hashes ``detected_at``, so every sweep re-inserts the same
    unchanged drift. Pass a stable content key (the docmod bridge uses the
    finding_id, which is stable per finding and changes when the finding is
    superseded).

    End-to-end: record event -> enqueue impacted doc -> re-map affected NIST
    controls. SSP-fragment drafting is a separate, explicitly-invoked step
    (:func:`process_regen_item`) so it stays HITL-gated and never auto-publishes.
    """
    event = event or {}
    source = event.get("source") or event.get("canvas") or "unknown"
    entity = event.get("entity") or event.get("node") or event.get("resource")
    severity = event.get("severity", "medium")
    tenant_id = event.get("tenant_id")
    classification = event.get("classification")
    dedup_key = event.get("dedup_key")

    event_id = record_drift_event(
        source, entity, severity, payload=event, dedup_key=dedup_key,
        tenant_id=tenant_id, classification=classification,
    )

    out: dict[str, Any] = {"event_id": event_id, "enqueued": [], "controls": {}}

    document_id = event.get("document_id")
    if document_id:
        out["enqueued"].append(
            enqueue_regen(
                document_id, event_id=event_id, drift_source=source,
                drift_entity=entity, severity=severity,
                dedup_key=f"{dedup_key}|{document_id}" if dedup_key else None,
                tenant_id=tenant_id, classification=classification,
            )
        )

    control_ids = event.get("control_ids") or event.get("controls") or []
    if control_ids:
        out["controls"] = map_changed_controls(control_ids)

    # Mark processed.
    conn = get_connection()
    try:
        conn.cursor().execute(
            "UPDATE dic_drift_events SET processed = 1 WHERE event_id = %s",
            (event_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return out


# --------------------------------------------------------------------------- #
# acoic-02: RICOAS / NIST 800-53 bridge
# --------------------------------------------------------------------------- #

def map_changed_controls(control_ids: list[str]) -> dict[str, Any]:
    """Re-map drift-affected NIST 800-53 controls across compliance frameworks.

    For each control this consults the RICOAS / NIST 800-53 crosswalk engine
    (cross-framework satisfaction: FedRAMP, NIST 800-171, CMMC, ISO 27001, …)
    and, when available, the compliance knowledge graph for a KG-verified path
    to CMMC. Pure-function crosswalk lookups are air-gap safe; the KG path is
    best-effort and skipped if the graph DB is unavailable.

    Returns ``{control_id: {"frameworks": {...}, "kg_path": {...}|None}}``.
    """
    # Crosswalk engine (deterministic JSON lookup, no DB required). Root
    # `tools.*` is preferred here: it ships the populated 800-53 crosswalk data,
    # whereas the icdev mirror's data file is not reliably resolvable in every
    # runtime (and a fresh `icdev.tools.*` import can hit a circular-import path).
    try:  # pragma: no cover
        from tools.compliance.crosswalk_engine import get_frameworks_for_control
    except Exception:
        from icdev.tools.compliance.crosswalk_engine import get_frameworks_for_control

    # Compliance KG (best-effort; needs the graph DB).
    try:  # pragma: no cover
        from tools.knowledge_graph.compliance_graph import get_crosswalk_path
    except Exception:
        try:
            from icdev.tools.knowledge_graph.compliance_graph import get_crosswalk_path
        except Exception:
            get_crosswalk_path = None  # type: ignore

    mapping: dict[str, Any] = {}
    for raw in control_ids:
        cid = str(raw).strip().upper()
        if not cid:
            continue
        try:
            frameworks = get_frameworks_for_control(cid)
        except Exception:
            frameworks = {}
        kg_path = None
        if get_crosswalk_path is not None:
            try:
                kg_path = get_crosswalk_path(cid, "cmmc")
            except Exception:
                kg_path = None
        mapping[cid] = {"frameworks": frameworks, "kg_path": kg_path}
    return mapping


# --------------------------------------------------------------------------- #
# acoic-02: SSP fragment generation (cited, CoD-verified, AI-labeled, HITL)
# --------------------------------------------------------------------------- #

def _retrieve_evidence(query: str, tenant_id: str | None, k: int = 5) -> list[str]:
    """Best-effort grounded retrieval over the DIC RAG index.

    Returns a list of chunk texts ([SOURCE-1] == first). Empty on any failure —
    the verifier then abstains, which is the correct grounded behavior.
    """
    try:  # pragma: no cover
        from icdev.tools.rag.retriever import RAGRetriever
    except Exception:
        try:
            from tools.rag.retriever import RAGRetriever
        except Exception:
            return []
    try:
        retriever = RAGRetriever(tenant_id or "")
        results = retriever.search(query, top_k=k)
    except TypeError:
        try:
            results = retriever.search(query)
        except Exception:
            return []
    except Exception:
        return []
    texts: list[str] = []
    for r in (results or [])[:k]:
        t = _chunk_text(r)
        if t.strip():
            texts.append(t)
    return texts


def _ssp_evidence_module():
    """The governed evidence seam, or ``None`` when it cannot be imported.

    A DIC install without the seam module behaves exactly like one with the
    toggle off. DocDrift must not stop drafting because an evidence module
    failed to import — that is the same fail-open the rest of this file already
    applies to the crosswalk KG and the LLM router.
    """
    try:  # pragma: no cover - import shape varies by install layout
        from icdev.tools.document_intelligence import ssp_evidence
    except Exception:
        try:
            from tools.document_intelligence import ssp_evidence
        except Exception:
            return None
    return ssp_evidence


def _reset_evidence_run() -> None:
    """Start a fresh evidence run — drop the memo cache, re-arm the budget."""
    module = _ssp_evidence_module()
    if module is not None:
        module.reset_run_state()


def _evidence_run_stats() -> dict[str, Any]:
    """Resolutions spent and asks refused by the cap, for the caller to report."""
    module = _ssp_evidence_module()
    return module.run_stats() if module is not None else {}


def _gather_evidence(
    control_id: str,
    frameworks: dict,
    tenant_id: str | None,
    classification: str | None,
    k: int = 5,
) -> tuple[list[str], list[dict], str, dict]:
    """Evidence for one control: governed seam first, legacy retrieval otherwise.

    Returns ``(texts, citations, path, detail)``.

    * ``texts``     — what the drafter and :func:`verifier.verify` consume.
      ``[SOURCE-1]`` is ``texts[0]``, unchanged from the legacy contract.
    * ``citations`` — INDEX-ALIGNED provenance for those texts, or ``[]`` on the
      legacy path, which has none to give. This is what makes a persisted
      ``[SOURCE-N]`` resolvable to a source id after the drafting call returns.
    * ``path``      — which chain produced the texts (``ssp_evidence.PATH_*``).
      Recorded on the fragment, because a drafting run whose evidence chain is
      unknowable afterwards is exactly what this migration is fixing.
    * ``detail``    — the backends consulted, the ones that DIED, and a
      governance refusal. Carried even when the legacy path was taken, so a
      thin governed answer is never mistaken for a thin corpus.

    Toggle off (the shipped default) short-circuits to the legacy call before
    anything is imported, so ``cortex.enabled: false`` is the pre-migration
    behaviour exactly rather than an approximation of it.
    """
    legacy_query = f"{control_id} {' '.join(str(x) for x in frameworks)} implementation"
    detail: dict[str, Any] = {}

    ssp_evidence = _ssp_evidence_module()
    if ssp_evidence is None:
        # No seam module at all — indistinguishable from the toggle being off,
        # and treated identically.
        return _retrieve_evidence(legacy_query, tenant_id, k), [], "legacy", detail

    bundle = ssp_evidence.resolve_evidence(
        control_id,
        frameworks=sorted(str(x) for x in frameworks),
        tenant_id=tenant_id,
        classification=classification,
        top_k=k,
    )
    if bundle is None:
        # Toggle off / re-entrant / budget spent / Cortex absent. Each of those
        # is logged with its own reason inside the seam; here they are one
        # answer, because they all mean "do what you did before".
        return _retrieve_evidence(legacy_query, tenant_id, k), [], ssp_evidence.PATH_LEGACY, detail

    detail = {
        "backends": list(bundle.backends),
        "backend_errors": list(bundle.errors),
        "blocked": bundle.blocked,
        "resolve_verdict": bundle.verdict,
    }
    if not bundle.is_empty:
        return list(bundle.texts), list(bundle.citations), ssp_evidence.PATH_CORTEX, detail

    if ssp_evidence.fallback_on_empty():
        # The governed fan-out answered with nothing a narrative can be written
        # from — a governance refusal, or a fan-out where every rung failed.
        # Falling back keeps the migration behaviour-preserving; `detail` keeps
        # the reason visible rather than laundering an outage into "no evidence".
        # A THIN answer does not reach here (it is not empty). That case is
        # covered by `detail["backend_errors"]` being persisted on the fragment —
        # see the measured cold/warm split in ssp_evidence's module docstring.
        return (
            _retrieve_evidence(legacy_query, tenant_id, k),
            [],
            ssp_evidence.PATH_CORTEX_EMPTY_FALLBACK,
            detail,
        )
    return [], [], ssp_evidence.PATH_CORTEX, detail


def _draft_fragment_text(control_id: str, frameworks: dict, evidence: list[str]) -> str:
    """Build a cited SSP-fragment draft.

    LLM-first (a control-narrative prompt grounded ONLY in the cited evidence),
    deterministic-cited fallback otherwise. Either way every factual sentence
    carries a ``[SOURCE-N]`` tag so the verifier can replay it.
    """
    if not evidence:
        return ""

    fw_line = ", ".join(sorted(frameworks)) if frameworks else "NIST 800-53"

    # LLM attempt — grounded, citation-required.
    llm_text = _llm_draft(control_id, fw_line, evidence)
    if llm_text:
        return llm_text

    # Deterministic fallback: stitch the strongest evidence into a control
    # narrative, citing each chunk it draws from. Conservative by construction.
    lines = [
        f"Control {control_id} is satisfied by the following documented "
        f"implementation evidence (cross-mapped to: {fw_line})."
    ]
    for i, chunk in enumerate(evidence, start=1):
        snippet = " ".join(chunk.split())[:280]
        lines.append(f"{snippet} [SOURCE-{i}]")
    return "\n".join(lines)


def _llm_draft(control_id: str, fw_line: str, evidence: list[str]) -> str | None:
    """Draft the fragment via the LLM router; None when no provider is reachable."""
    try:  # pragma: no cover
        from icdev.tools.llm.router import LLMRouter
    except Exception:
        try:
            from tools.llm.router import LLMRouter
        except Exception:
            return None
    evidence_block = "\n\n".join(
        f"[SOURCE-{i}]\n{chunk[:1500]}" for i, chunk in enumerate(evidence, start=1)
    )
    prompt = (
        "You are drafting an SSP (System Security Plan) implementation narrative "
        f"for NIST 800-53 control {control_id} (cross-mapped to: {fw_line}). "
        "Write 2-4 sentences describing how the control is implemented. Use ONLY "
        "the EVIDENCE below — do not invent facts. Every sentence MUST end with a "
        "[SOURCE-N] tag identifying the evidence it draws from. If the evidence "
        "does not support the control, write exactly: INSUFFICIENT_EVIDENCE.\n\n"
        f"EVIDENCE:\n{evidence_block}\n\nSSP NARRATIVE:"
    )
    try:
        router = LLMRouter()
        text = None
        for meth in ("generate", "complete", "chat", "route", "call"):
            fn = getattr(router, meth, None)
            if not callable(fn):
                continue
            try:
                resp = fn(prompt)
            except TypeError:
                try:
                    resp = fn(prompt=prompt)
                except Exception:
                    continue
            except Exception:
                continue
            text = resp if isinstance(resp, str) else getattr(resp, "text", None)
            if text is None and isinstance(resp, dict):
                text = resp.get("text") or resp.get("content") or resp.get("response")
            if text:
                break
        if not text or "INSUFFICIENT_EVIDENCE" in text:
            return None
        return text.strip()
    except Exception:
        return None


def generate_ssp_fragment(
    control_id: str,
    *,
    document_id: str | None = None,
    evidence_chunks: list[Any] | None = None,
    regen_item_id: str | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
) -> dict[str, Any]:
    """Draft a cited, CoD-verified, AI-labeled SSP fragment for one control.

    Pipeline: re-map control -> retrieve grounded evidence -> draft cited
    narrative -> :func:`verifier.verify` (CoD claim replay) -> persist as
    ``pending_review``. The fragment is NEVER auto-approved — a human must call
    :func:`approve_fragment`.

    Returns the persisted fragment row (dict).
    """
    control_id = str(control_id).strip().upper()
    frameworks = map_changed_controls([control_id]).get(control_id, {}).get("frameworks", {})

    # Evidence acquisition (cef-di-03). Caller-supplied chunks still win — a
    # caller that brought its own evidence asked for that evidence, not for a
    # resolution of its own — and are recorded as such.
    sources: list[dict] = []
    evidence_path = "caller"
    evidence_detail: dict[str, Any] = {}
    chunks = evidence_chunks
    if chunks is None:
        chunks, sources, evidence_path, evidence_detail = _gather_evidence(
            control_id, frameworks, tenant_id, classification
        )
    evidence_texts = [_chunk_text(c) for c in (chunks or [])]

    draft = _draft_fragment_text(control_id, frameworks, evidence_texts)

    # CoD anti-hallucination gate.
    vr = _verify(draft, evidence_texts) if draft else {
        "verified_text": "", "claims": [], "abstained": True,
        "reason": "no_evidence", "citation_report": {},
    }

    created_at = _now()
    fragment_id = _hid("dic_ssp", control_id, document_id or "", created_at)
    verified = 0 if vr.get("abstained") else 1
    fragment_text = vr.get("verified_text") or ""

    # Provenance for the [SOURCE-N] tags the draft carries. Additive to the
    # verifier's own structural report, which keeps its existing shape: this is
    # WHAT each index pointed at, that is WHETHER the tags were well-formed, and
    # they are different facts. `sources` is empty on the legacy path — which
    # produced bare chunk texts with no source identity at all, so recording an
    # empty list is the honest answer rather than a gap.
    citation_report = dict(vr.get("citation_report") or {})
    citation_report["evidence_path"] = evidence_path
    citation_report["sources"] = sources
    if evidence_detail:
        citation_report["evidence_detail"] = evidence_detail

    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.cursor().execute(
            """
            INSERT OR REPLACE INTO dic_ssp_fragments
                (fragment_id, document_id, control_id, frameworks_json,
                 fragment_text, status, origin, ai_labeled, verified, abstained,
                 verify_reason, citations_json, cod_verdict_json, regen_item_id,
                 created_at, tenant_id, classification)
            VALUES (%s, %s, %s, %s, %s, 'pending_review', 'ai_generated', 1, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                fragment_id, document_id, control_id, json.dumps(frameworks),
                fragment_text, verified, 1 if vr.get("abstained") else 0,
                vr.get("reason", ""),
                json.dumps(citation_report),
                json.dumps(vr.get("claims", [])),
                regen_item_id, created_at, tenant_id, classification,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    if regen_item_id:
        _set_queue_state(regen_item_id, "drafted", fragment_id=fragment_id)

    return {
        "fragment_id": fragment_id,
        "control_id": control_id,
        "document_id": document_id,
        "status": "pending_review",
        "origin": "ai_generated",
        "ai_labeled": True,
        "verified": bool(verified),
        "abstained": bool(vr.get("abstained")),
        "verify_reason": vr.get("reason", ""),
        "fragment_text": fragment_text,
        "frameworks": frameworks,
    }


def process_regen_item(item_id: str, control_ids: list[str] | None = None) -> dict[str, Any]:
    """Drive a queued regen item to drafted SSP fragment(s).

    Pulls the impacted document + any control IDs recorded on the originating
    drift event, drafts one CoD-verified fragment per control, and advances the
    queue item to ``drafted`` (still HITL-gated for approval).

    This call is the evidence-seam RUN boundary (cef-di-03): the per-control
    memo cache is dropped and the outbound resolution budget is re-armed here,
    so a long-lived Flask worker cannot serve a freshly ingested document's
    evidence from a cache minted at startup. The budget actually spent is
    returned under ``evidence_stats`` — a bounded run that reads as a complete
    one is the defect "no silent caps" names.
    """
    _reset_evidence_run()
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT document_id, event_id, tenant_id, classification "
            "FROM dic_acoic_regen_queue WHERE item_id = %s",
            (item_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "regen item not found", "item_id": item_id}
        document_id, event_id, tenant_id, classification = (
            row["document_id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0],
            row["event_id"] if hasattr(row, "keys") else row[1],
            row["tenant_id"] if hasattr(row, "keys") else row[2],
            row["classification"] if hasattr(row, "keys") else row[3],
        )
        if control_ids is None and event_id:
            cur.execute(
                "SELECT payload_json FROM dic_drift_events WHERE event_id = %s",
                (event_id,),
            )
            ev = cur.fetchone()
            if ev:
                payload_json = ev["payload_json"] if hasattr(ev, "keys") else ev[0]
                try:
                    payload = json.loads(payload_json or "{}")
                    control_ids = payload.get("control_ids") or payload.get("controls") or []
                except Exception:
                    control_ids = []
    finally:
        conn.close()

    _set_queue_state(item_id, "regenerating")
    control_ids = control_ids or []
    fragments = [
        generate_ssp_fragment(
            cid, document_id=document_id, regen_item_id=item_id,
            tenant_id=tenant_id, classification=classification,
        )
        for cid in control_ids
    ]
    if not fragments:
        _set_queue_state(item_id, "drafted")
    return {
        "item_id": item_id,
        "document_id": document_id,
        "fragments": fragments,
        "evidence_stats": _evidence_run_stats(),
    }


# --------------------------------------------------------------------------- #
# HITL review
# --------------------------------------------------------------------------- #

def _review_fragment(fragment_id: str, status: str, reviewed_by: str | None) -> dict[str, Any]:
    if status not in ("approved", "rejected", "needs_revision"):
        raise ValueError("status must be 'approved', 'rejected', or 'needs_revision'")
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "UPDATE dic_ssp_fragments SET status = %s, reviewed_by = %s, reviewed_at = %s "
            "WHERE fragment_id = %s",
            (status, reviewed_by, _now(), fragment_id),
        )
        conn.commit()
        changed = cur.rowcount
        # Advance the linked queue item.
        cur.execute(
            "SELECT control_id, document_id, regen_item_id FROM dic_ssp_fragments "
            "WHERE fragment_id = %s",
            (fragment_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    # Audit the human decision. dic_ssp_fragments is a mutable workflow table —
    # it holds only the CURRENT status, so an approval leaves no evidence of who
    # decided what, when. These fragments become SSP content, so that record is
    # the cATO audit trail. audit_trail is append-only (enforced in
    # .claude/hooks/pre_tool_use.py) and hash-chained by audit_logger.
    #
    # Fail-closed (raise_on_error=True): an approval that cannot be audited must
    # not silently stand. NIST AU-5 — an unrecorded authorisation decision is an
    # audit finding, so failing the approval is the safer outcome. Machine-driven
    # transitions stay best-effort; only human decisions gate on the audit write.
    rd = dict(row) if row is not None and hasattr(row, "keys") else {}
    if changed:
        from tools.audit.audit_logger import log_event

        log_event(
            event_type="dic.ssp_fragment.review",
            actor=reviewed_by or "unknown",
            action=f"ssp_fragment.{status}",
            details={
                "fragment_id": fragment_id,
                "status": status,
                "control_id": rd.get("control_id"),
                "document_id": rd.get("document_id"),
                "regen_item_id": rd.get("regen_item_id"),
            },
            classification="CUI",
            raise_on_error=True,
        )

    if row:
        # NB: the SELECT above fetches three columns for the audit record, so the
        # positional fallback must index regen_item_id explicitly (not row[0]).
        item_id = rd.get("regen_item_id") if rd else row[2]
        if item_id:
            queue_state = "drafted" if status == "needs_revision" else status
            _set_queue_state(item_id, queue_state)
    return {"fragment_id": fragment_id, "status": status, "updated": changed}


def approve_fragment(fragment_id: str, reviewed_by: str | None = None) -> dict[str, Any]:
    """HITL approval — the fragment becomes authoritative."""
    return _review_fragment(fragment_id, "approved", reviewed_by)


def reject_fragment(fragment_id: str, reviewed_by: str | None = None) -> dict[str, Any]:
    """HITL rejection — the fragment is discarded from the SSP."""
    return _review_fragment(fragment_id, "rejected", reviewed_by)


def request_revision(fragment_id: str, reviewed_by: str | None = None) -> dict[str, Any]:
    """HITL request for revision — the fragment returns to editor queue."""
    return _review_fragment(fragment_id, "needs_revision", reviewed_by)


def assign_fragment(fragment_id: str, assigned_to: str) -> dict[str, Any]:
    """Assign an SSP fragment to a user for review or editing."""
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "UPDATE dic_ssp_fragments SET assigned_to = %s WHERE fragment_id = %s",
            (assigned_to, fragment_id),
        )
        conn.commit()
        changed = cur.rowcount
    finally:
        conn.close()
    return {"fragment_id": fragment_id, "assigned_to": assigned_to, "updated": changed}


# --------------------------------------------------------------------------- #
# Page data (consumed by the /document-intelligence/acoic route)
# --------------------------------------------------------------------------- #

def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        out = []
        for r in cur.fetchall():
            if hasattr(r, "keys"):
                out.append({k: r[k] for k in r.keys()})
            else:
                out.append(dict(zip(cols, r)))
        return out
    finally:
        conn.close()


# These three carried bare `?` placeholders while the rest of this module uses
# %s. They did not crash on PostgreSQL — translate_sql rewrote them — but that
# made a runtime read path depend on the translator, which is an init-time
# SQLite fallback and explicitly not load-bearing, and it logged a
# "bare ? placeholder detected" warning on every call. Authored for PG directly.
def list_drift_events(limit: int = 50) -> list[dict[str, Any]]:
    return _rows(
        "SELECT source, entity, severity, detected_at FROM dic_drift_events "
        "ORDER BY detected_at DESC LIMIT %s",
        (limit,),
    )


def list_regen_queue(limit: int = 50) -> list[dict[str, Any]]:
    return _rows(
        "SELECT item_id, document_id, impact_level, state, queued_at "
        "FROM dic_acoic_regen_queue ORDER BY impact_score DESC, queued_at DESC LIMIT %s",
        (limit,),
    )


def list_ssp_fragments(limit: int = 50) -> list[dict[str, Any]]:
    return _rows(
        "SELECT fragment_id, control_id, document_id, status, verified, ai_labeled "
        "FROM dic_ssp_fragments ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )


def get_acoic_page_context() -> dict[str, Any]:
    """Bundle the three tables the ``acoic.html`` template renders.

    The DIC blueprint route should do::

        from tools.document_intelligence import acoic
        return render_template("document_intelligence/acoic.html",
                               **acoic.get_acoic_page_context())
    """
    return {
        "drift_events": list_drift_events(),
        "regen_queue": list_regen_queue(),
        "ssp_fragments": list_ssp_fragments(),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="ACOIC — drift→impact→regen→NIST SSP bridge")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("drift", help="record a drift event (+ optional enqueue/remap)")
    sp.add_argument("--source", required=True)
    sp.add_argument("--entity")
    sp.add_argument("--severity", default="medium")
    sp.add_argument("--document-id")
    sp.add_argument("--controls", help="comma-separated NIST control IDs")

    sp = sub.add_parser("map", help="re-map NIST controls across frameworks")
    sp.add_argument("--controls", required=True, help="comma-separated NIST control IDs")

    sp = sub.add_parser("fragment", help="generate a CoD-verified SSP fragment")
    sp.add_argument("--control", required=True)
    sp.add_argument("--document-id")
    sp.add_argument("--evidence", action="append", default=[],
                    help="evidence chunk text (repeatable; @path to read a file)")

    sp = sub.add_parser("approve", help="HITL approve a fragment")
    sp.add_argument("--fragment-id", required=True)
    sp.add_argument("--by")

    sp = sub.add_parser("reject", help="HITL reject a fragment")
    sp.add_argument("--fragment-id", required=True)
    sp.add_argument("--by")

    sub.add_parser("queue", help="show the regen queue")
    sub.add_parser("fragments", help="show drafted SSP fragments")
    sub.add_parser("page", help="dump the ACOIC page context as JSON")

    p.add_argument("--json", action="store_true", help="emit JSON")
    args = p.parse_args(argv)

    def _emit(obj):
        print(json.dumps(obj, indent=2, default=str))

    if args.cmd == "drift":
        ev = {"source": args.source, "entity": args.entity, "severity": args.severity}
        if args.document_id:
            ev["document_id"] = args.document_id
        if args.controls:
            ev["control_ids"] = [c.strip() for c in args.controls.split(",") if c.strip()]
        _emit(handle_drift(ev))
    elif args.cmd == "map":
        ctrls = [c.strip() for c in args.controls.split(",") if c.strip()]
        _emit(map_changed_controls(ctrls))
    elif args.cmd == "fragment":
        evidence = []
        for e in args.evidence:
            if e.startswith("@"):
                evidence.append(Path(e[1:]).read_text(encoding="utf-8"))
            else:
                evidence.append(e)
        _emit(generate_ssp_fragment(
            args.control, document_id=args.document_id,
            evidence_chunks=evidence or None,
        ))
    elif args.cmd == "approve":
        _emit(approve_fragment(args.fragment_id, args.by))
    elif args.cmd == "reject":
        _emit(reject_fragment(args.fragment_id, args.by))
    elif args.cmd == "queue":
        _emit(list_regen_queue())
    elif args.cmd == "fragments":
        _emit(list_ssp_fragments())
    elif args.cmd == "page":
        _emit(get_acoic_page_context())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
