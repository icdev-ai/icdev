# CUI // SP-CTI
"""AADC↔AIMC Canvas Bridge.

Provides cross-canvas functions linking AADC (Agentic AI Design Canvas) model
nodes to the AIMC (AI/ML Model Canvas) FOUNDATION_MODELS catalog.

Functions:
    get_aimc_catalog()       → Full FOUNDATION_MODELS list from AIMC
    link_model_node(...)     → Upsert an AADC node → AIMC model reference
    get_model_refs(...)      → Refs for an AADC design with joined model metadata
    get_aadc_refs_for_model(...)  → AADC designs using a given AIMC model ID
    check_il_compatibility(...)  → Return IL violations for an AADC design
    unlink_model_node(...)   → Remove a reference by ref ID
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.aiml_canvas.constants import FOUNDATION_MODELS, IL_REQUIREMENTS
from tools.agentic_ai_canvas.db.init_db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_by_id(model_id: str) -> dict | None:
    return next((m for m in FOUNDATION_MODELS if m["id"] == model_id), None)


# ── Public API ────────────────────────────────────────────────────────────────

def get_aimc_catalog() -> list[dict]:
    """Return the full AIMC FOUNDATION_MODELS list."""
    return list(FOUNDATION_MODELS)


def link_model_node(
    aadc_design_id: str,
    aadc_node_id: str,
    aimc_model_id: str,
    aimc_design_id: str | None = None,
    notes: str = "",
) -> dict:
    """Create or update an AADC node → AIMC model reference.

    Upserts on (aadc_design_id, aadc_node_id) — one AIMC model per node.
    Returns the stored ref record.
    """
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM aadc_aimc_model_refs WHERE aadc_design_id=%s AND aadc_node_id=%s",
            (aadc_design_id, aadc_node_id),
        ).fetchone()
        if existing:
            ref_id = existing["id"]
            conn.execute(
                """UPDATE aadc_aimc_model_refs
                   SET aimc_model_id=%s, aimc_design_id=%s, notes=%s, created_at=%s
                   WHERE id=%s""",
                (aimc_model_id, aimc_design_id, notes, _now(), ref_id),
            )
        else:
            ref_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO aadc_aimc_model_refs
                   (id, aadc_design_id, aadc_node_id, aimc_model_id, aimc_design_id, notes, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (ref_id, aadc_design_id, aadc_node_id, aimc_model_id, aimc_design_id, notes, _now()),
            )
        conn.commit()
        return {
            "id": ref_id,
            "aadc_design_id": aadc_design_id,
            "aadc_node_id": aadc_node_id,
            "aimc_model_id": aimc_model_id,
            "aimc_design_id": aimc_design_id,
            "notes": notes,
            "model": _model_by_id(aimc_model_id),
        }
    finally:
        conn.close()


def get_model_refs(aadc_design_id: str) -> list[dict]:
    """Return all AIMC model refs for an AADC design, with full model metadata."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM aadc_aimc_model_refs WHERE aadc_design_id=%s ORDER BY created_at",
            (aadc_design_id,),
        ).fetchall()
        refs = []
        for row in rows:
            r = dict(row)
            r["model"] = _model_by_id(r["aimc_model_id"])
            refs.append(r)
        return refs
    finally:
        conn.close()


def get_aadc_refs_for_model(aimc_model_id: str) -> list[dict]:
    """Return all AADC designs (with ref metadata) that reference a given AIMC model."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT r.*, d.name as design_name, d.classification as il_level
               FROM aadc_aimc_model_refs r
               LEFT JOIN aadc_designs d ON d.id = r.aadc_design_id
               WHERE r.aimc_model_id=%s
               ORDER BY r.created_at DESC""",
            (aimc_model_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# Explicit classification-marking → DoD Impact Level map. Keys are matched
# case-insensitively against the leading marking (anything before a '//' banner
# separator, e.g. "CUI // SP-CTI" → "CUI"). Unknown markings default to IL4.
_CLASSIFICATION_IL_MAP = {
    "UNCLASSIFIED": "IL2",
    "U": "IL2",
    "PUBLIC": "IL2",
    "CUI": "IL4",
    "CONTROLLED UNCLASSIFIED INFORMATION": "IL4",
    "CUI-HIGH": "IL5",
    "SECRET": "IL6",
    "S": "IL6",
    "TOP SECRET": "IL6",
    "TS": "IL6",
}


def _classification_to_il(classification: str | None) -> str:
    """Map an aadc_designs classification marking to an IL string (default IL4)."""
    if not classification:
        return "IL4"
    marking = str(classification).split("//")[0].strip().upper()
    if marking.startswith("IL"):
        return marking
    return _CLASSIFICATION_IL_MAP.get(marking, "IL4")


def check_il_compatibility(aadc_design_id: str, target_il: str | None = None) -> list[dict]:
    """Check IL compatibility of all linked models for an AADC design.

    Returns list of violation dicts. Empty list means all models are compatible.
    """
    conn = get_connection()
    try:
        design_row = conn.execute(
            "SELECT * FROM aadc_designs WHERE id=%s", (aadc_design_id,)
        ).fetchone()
        if not design_row:
            return [{"error": f"AADC design '{aadc_design_id}' not found"}]
        design_dict = dict(design_row)
        # aadc_designs stores a 'classification' marking, not an 'il_level'.
        # An explicit target_il always wins; otherwise derive the IL from the
        # design's classification value (defaulting to IL4).
        il_level = target_il or _classification_to_il(design_dict.get("classification"))

        refs = conn.execute(
            "SELECT * FROM aadc_aimc_model_refs WHERE aadc_design_id=%s",
            (aadc_design_id,),
        ).fetchall()
    finally:
        conn.close()

    il_int = int(il_level.replace("IL", "")) if il_level.startswith("IL") else 4
    il_req = IL_REQUIREMENTS.get(il_level, {})

    violations: list[dict] = []
    for row in refs:
        ref = dict(row)
        model = _model_by_id(ref["aimc_model_id"])
        if not model:
            violations.append({
                "ref_id": ref["id"],
                "aadc_node_id": ref["aadc_node_id"],
                "aimc_model_id": ref["aimc_model_id"],
                "severity": "CAT1",
                "issue": f"Model '{ref['aimc_model_id']}' not found in AIMC catalog",
            })
            continue

        issues: list[str] = []
        if il_int not in model.get("il_suitability", []):
            issues.append(
                f"Model '{model['name']}' (provider: {model['provider']}) "
                f"does not support {il_level} — supports IL"
                + "/IL".join(str(x) for x in model.get("il_suitability", []))
            )
        if il_req.get("must_be_air_gap") and not model.get("air_gap_ready"):
            issues.append(
                f"{il_level} requires air-gap but '{model['name']}' is cloud-connected"
            )
        allowed = il_req.get("allowed_providers", [])
        if allowed and not any(p in model.get("provider", "") for p in allowed):
            issues.append(
                f"Provider '{model['provider']}' not approved for {il_level}. "
                f"Approved: {', '.join(allowed)}"
            )

        if issues:
            violations.append({
                "ref_id": ref["id"],
                "aadc_node_id": ref["aadc_node_id"],
                "aimc_model_id": ref["aimc_model_id"],
                "model_name": model.get("name"),
                "provider": model.get("provider"),
                "severity": "CAT1",
                "issues": issues,
            })

    return violations


def unlink_model_node(ref_id: str) -> bool:
    """Remove an AADC↔AIMC model reference by ref ID. Returns True if deleted."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM aadc_aimc_model_refs WHERE id=%s", (ref_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def bridge_security_check(aadc_design_id: str, nodes: list[dict], edges: list[dict]) -> list[dict]:
    """OWASP LLM01 bridge check — for every AADC node linked to an LLM-type AIMC model,
    verify an input-sanitizer exists upstream. Returns CAT2 findings for violations.
    """
    refs = get_model_refs(aadc_design_id)
    if not refs:
        return []

    llm_model_types = {"llm", "vlm", "code", "judge"}
    llm_linked_node_ids = {
        r["aadc_node_id"]
        for r in refs
        if r.get("model") and r["model"].get("type") in llm_model_types
    }
    if not llm_linked_node_ids:
        return []

    # Build reverse adjacency (predecessor map) for upstream checks
    predecessors: dict[str, set[str]] = {}
    for e in edges:
        src, tgt = e.get("source", ""), e.get("target", "")
        predecessors.setdefault(tgt, set()).add(src)

    def _upstream_types(node_id: str, visited: set | None = None) -> set[str]:
        visited = visited or set()
        result: set[str] = set()
        for pred_id in predecessors.get(node_id, set()):
            if pred_id in visited:
                continue
            visited.add(pred_id)
            pred_node = next((n for n in nodes if n["id"] == pred_id), None)
            if pred_node:
                result.add(pred_node.get("type", ""))
                result |= _upstream_types(pred_id, visited)
        return result

    SANITIZER_TYPES = {"input-sanitizer", "safety-guardrail", "safety-content-filter", "pii-scrubber"}
    findings: list[dict] = []
    for node_id in llm_linked_node_ids:
        node = next((n for n in nodes if n["id"] == node_id), None)
        if not node:
            continue
        upstream = _upstream_types(node_id)
        if not (upstream & SANITIZER_TYPES):
            ref = next((r for r in refs if r["aadc_node_id"] == node_id), {})
            model_name = (ref.get("model") or {}).get("name", ref.get("aimc_model_id", "?"))
            findings.append({
                "id": f"bridge-llm01-{node_id}",
                "framework": "OWASP LLM (Bridge)",
                "category": "LLM01",
                "severity": "CAT2",
                "title": f"LLM node '{node.get('label', node_id)}' missing input sanitizer",
                "detail": f"Node is linked to LLM model '{model_name}' via AIMC bridge but has no "
                          "input-sanitizer, safety-guardrail, or content-filter upstream. "
                          "Risk: prompt injection attacks.",
                "recommendation": "Add an input-sanitizer or safety-guardrail node upstream of this node.",
            })
    return findings
