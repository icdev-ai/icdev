# CUI // SP-CTI
"""OPORD Generator — Five-Paragraph Operations Order (FM 5-0 / JP 5-0).

Paragraphs:
  1 — Situation (enemy, friendly, attachments)
  2 — Mission (who, what, when, where, why)
  3 — Execution (commander's intent, concept, tasks, coordinating instructions)
  4 — Sustainment (logistics, medical, personnel)
  5 — Command & Signal (command succession, comms, reports)

TRUST invariant (nav-strat-02)
------------------------------
Each synthesized paragraph is LLM prose and therefore MUST be grounded: the
prompt injects concrete doctrine/historical source snippets (with ids), the
model is required to cite them inline as ``[source: <id>]``, and every
paragraph is validated with the shared ``tools/quality/citation_grounding``
utilities. A per-paragraph grounding verdict is persisted with the OPORD, an
overall grounding status is exposed on the OPORD detail/approval payload, and
``approve_opord`` blocks approval of an ungrounded OPORD unless an explicit,
audited ``force`` override is supplied (mirroring the repo's
citation_guard/placeholder_guard gate pattern). The no-LLM fallback assembles
a clearly-labeled manual-completion template and never fabricates citations.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.opord")

# ── Source-grounding instruction appended to every paragraph prompt ──────────
_CITATION_INSTRUCTION = (
    "\n\nSOURCE MATERIAL — cite these and only these, inline, immediately after "
    "each supported claim, using the exact form [source: <id>]. Do not cite any "
    "id that is not listed below and do not invent sources. Every substantive "
    "assertion MUST carry a [source: <id>] tag drawn from this list:\n{sources}\n"
)

PARA_PROMPTS = [
    (
        "You are a staff officer drafting OPORD Paragraph 1 (Situation) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write a formal Situation paragraph covering: (a) Enemy Forces — assessed strength, "
        "disposition, capabilities, and most likely COA; (b) Friendly Forces — higher HQ mission, "
        "adjacent units, supporting assets; (c) Attachments and Detachments. "
        "Use military OPORD format. Be concise and decisive."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 2 (Mission) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write a single clear mission statement answering Who, What (task), When, Where, and Why (purpose). "
        "Follow Army/Joint OPORD format. One paragraph, 2-4 sentences."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 3 (Execution) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write the Execution paragraph covering: (a) Commander's Intent — end state, key tasks, "
        "acceptable risk; (b) Concept of Operations — phasing, main effort, supporting efforts; "
        "(c) Tasks to Subordinate Units — key assignments; (d) Coordinating Instructions — "
        "ROE summary, timeline, phase lines. Use formal military prose."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 4 (Sustainment) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write the Sustainment paragraph covering: (a) Logistics — supply classes, transportation, "
        "maintenance priorities; (b) Personnel Services — casualty reporting, replacements; "
        "(c) Army Health System — medical support, MEDEVAC, treatment facilities. "
        "Use formal military logistics prose."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 5 (Command and Signal) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write the Command and Signal paragraph covering: (a) Command — succession of command, "
        "location of command posts, liaison requirements; (b) Control — decision points, "
        "battle rhythm, CCIR reporting; (c) Signal — primary/alternate/contingency comms, "
        "COMSEC instructions. Use formal military prose."
    ),
]

PARA_HEADINGS = [
    "1. SITUATION",
    "2. MISSION",
    "3. EXECUTION",
    "4. SUSTAINMENT",
    "5. COMMAND AND SIGNAL",
]

PARA_FIELDS = ["situation", "mission", "execution", "sustainment", "command_signal"]

# Grounding verdict statuses (per-paragraph and rolled-up OPORD-level).
GROUNDING_GROUNDED = "grounded"
GROUNDING_UNGROUNDED = "ungrounded"
GROUNDING_FALLBACK = "fallback"
GROUNDING_PENDING = "pending"

# Labeled manual-completion template used when no LLM is available. Contains NO
# citation tags — the fallback path must never fabricate provenance.
_FALLBACK_TEMPLATE = (
    "[TEMPLATE — automated synthesis unavailable; complete {heading} manually. "
    "No sources were retrieved or cited.]"
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Source retrieval ─────────────────────────────────────────────────────────

def _gather_sources(scenario: str, theater: str) -> tuple[list[dict], list[str], str]:
    """Retrieve concrete doctrine/historical source snippets for grounding.

    Returns (sources, allowed_ids, source_block):
      sources      — [{id, title, excerpt}] fed into the prompt
      allowed_ids  — the ids the model is permitted to cite
      source_block — formatted text injected into the prompt's SOURCE MATERIAL
    Degrades to ([], [], "") if the doctrine corpus is unavailable.
    """
    try:
        from tools.strategos.doctrine_corpus import (
            get_doctrine_citations,
            get_historical_precedents,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("doctrine_corpus unavailable for grounding: %s", exc)
        return [], [], ""

    entries = []
    try:
        entries.extend(get_doctrine_citations(scenario=scenario, theater=theater, top_k=3))
        entries.extend(get_historical_precedents(scenario=scenario, theater=theater, top_k=2))
    except Exception as exc:  # pragma: no cover - retrieval guard
        logger.warning("doctrine retrieval failed: %s", exc)
        return [], [], ""

    sources: list[dict] = []
    allowed_ids: list[str] = []
    seen: set[str] = set()
    for e in entries:
        if e.id in seen:
            continue
        seen.add(e.id)
        excerpt = (e.content or "").strip().replace("\n", " ")
        if len(excerpt) > 260:
            excerpt = excerpt[:257].rstrip() + "..."
        sources.append({"id": e.id, "title": e.title, "excerpt": excerpt})
        allowed_ids.append(e.id)

    lines = [f"  [source: {s['id']}] {s['title']}: {s['excerpt']}" for s in sources]
    source_block = "\n".join(lines)
    return sources, allowed_ids, source_block


def _evaluate_grounding(content: str, allowed_ids: list[str], used_llm: bool) -> dict:
    """Produce a per-paragraph grounding verdict using citation_grounding.

    Verdict status:
      - "fallback"   : no LLM was used (labeled template, no citations expected)
      - "ungrounded" : LLM prose with missing OR hallucinated citations (flagged)
      - "grounded"   : LLM prose citing only available sources
    """
    from tools.quality.citation_grounding import parse_citations, validate_citations

    if not used_llm:
        cited = parse_citations(content)
        return {
            "status": GROUNDING_FALLBACK,
            "method": "fallback",
            "has_citations": bool(cited),
            "cited_count": 0,
            "available_count": len(allowed_ids),
            "hallucinated": cited,  # a fallback should carry no citations at all
            "allowed_sources": list(allowed_ids),
        }

    report = validate_citations(content, allowed_ids)
    cited = parse_citations(content)
    if report["hallucinated_citations"]:
        status = GROUNDING_UNGROUNDED
    elif not cited:
        status = GROUNDING_UNGROUNDED
    else:
        status = GROUNDING_GROUNDED
    return {
        "status": status,
        "method": "llm",
        "has_citations": bool(cited),
        "cited_count": report["cited_count"],
        "available_count": report["available_count"],
        "hallucinated": report["hallucinated_citations"],
        "allowed_sources": list(allowed_ids),
    }


def overall_grounding_status(grounding: dict | None) -> str:
    """Roll per-paragraph verdicts up into one OPORD-level status.

    Priority: any fallback -> "fallback"; any ungrounded -> "ungrounded";
    all present verdicts grounded -> "grounded"; none synthesized -> "pending".
    """
    if not grounding:
        return GROUNDING_PENDING
    statuses = [v.get("status") for v in grounding.values() if isinstance(v, dict)]
    if not statuses:
        return GROUNDING_PENDING
    if any(s == GROUNDING_FALLBACK for s in statuses):
        return GROUNDING_FALLBACK
    if any(s == GROUNDING_UNGROUNDED for s in statuses):
        return GROUNDING_UNGROUNDED
    if all(s == GROUNDING_GROUNDED for s in statuses):
        return GROUNDING_GROUNDED
    return GROUNDING_UNGROUNDED


def is_grounded_for_approval(grounding: dict | None) -> bool:
    """True only when every synthesized paragraph is grounded (approval-ready)."""
    return overall_grounding_status(grounding) == GROUNDING_GROUNDED


# ── Persistence helpers ──────────────────────────────────────────────────────

def _load_grounding(conn, ph: str, opord_id: str) -> dict:
    """Read + parse the stored grounding JSON (tolerant of a missing column)."""
    try:
        row = conn.execute(
            f"SELECT grounding FROM sg_opords WHERE id = {ph}", (opord_id,)  # nosec B608
        ).fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    raw = row[0] if not isinstance(row, dict) else row.get("grounding")
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}


def create_opord(
    title: str,
    theater: str = "unspecified",
    scenario: str = "",
    task_org: str = "",
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        opord_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_opords "
            "(id, title, theater, task_org, status, created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'draft', %s, %s, %s)",
            (opord_id, title, theater, task_org or None, created_by, now, now),
        )
        conn.commit()
        return {"opord_id": opord_id, "title": title, "theater": theater, "status": "draft"}
    finally:
        conn.close()


def synthesize_paragraph(opord_id: str, para_num: int, scenario: str = "") -> dict[str, Any]:
    """LLM-synthesize one OPORD paragraph (1-5), grounded + validated, and persist it."""
    if para_num < 1 or para_num > 5:
        return {"error": "para_num must be 1-5"}
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT title, theater FROM sg_opords WHERE id = {ph}", (opord_id,)  # nosec B608
        ).fetchone()
        if not row:
            return {"error": "OPORD not found"}
        title = row[0] if not isinstance(row, dict) else row["title"]
        theater = row[1] if not isinstance(row, dict) else row["theater"]

        # 1. Retrieve concrete source material for inline grounding.
        sources, allowed_ids, source_block = _gather_sources(
            scenario or "General operations", theater or "unspecified"
        )

        # 2. Build prompt; require inline [source: <id>] citations when sources exist.
        base_prompt = PARA_PROMPTS[para_num - 1].format(
            title=title, theater=theater,
            scenario=scenario or "General operations",
        )
        prompt = base_prompt
        if source_block:
            prompt += _CITATION_INSTRUCTION.format(sources=source_block)

        # 3. Synthesize (LLM) or fall back to a labeled manual template.
        content, used_llm = _llm_call(prompt, has_sources=bool(source_block))
        if not used_llm:
            content = _FALLBACK_TEMPLATE.format(heading=PARA_HEADINGS[para_num - 1])

        # 4. Validate + build the per-paragraph grounding verdict.
        verdict = _evaluate_grounding(content, allowed_ids, used_llm)

        # 5. Persist content + merged grounding verdict + rolled-up status.
        field = PARA_FIELDS[para_num - 1]
        now = _now_utc()
        grounding = _load_grounding(conn, ph, opord_id)
        grounding[field] = verdict
        status = overall_grounding_status(grounding)
        grounding_json = json.dumps(grounding)
        try:
            conn.execute(
                f"UPDATE sg_opords SET {field} = {ph}, grounding = {ph}, "  # nosec B608
                f"grounding_status = {ph}, updated_at = {ph} WHERE id = {ph}",
                (content, grounding_json, status, now, opord_id),
            )
        except Exception:
            # Grounding columns absent (migration not yet applied) — still persist content.
            logger.warning("grounding columns unavailable; persisting content only")
            conn.execute(
                f"UPDATE sg_opords SET {field} = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
                (content, now, opord_id),
            )
        conn.commit()
        return {
            "para_num": para_num,
            "heading": PARA_HEADINGS[para_num - 1],
            "content": content,
            "grounding": verdict,
            "grounding_status": status,
            "sources": sources,
        }
    finally:
        conn.close()


def synthesize_all(opord_id: str, scenario: str = "") -> dict[str, Any]:
    """Synthesize all 5 paragraphs sequentially."""
    results = []
    for i in range(1, 6):
        r = synthesize_paragraph(opord_id, i, scenario=scenario)
        if "error" in r:
            return r
        results.append(r)
    status = results[-1].get("grounding_status") if results else GROUNDING_PENDING
    return {"opord_id": opord_id, "paragraphs": results, "grounding_status": status}


def _llm_call(prompt: str, has_sources: bool = False) -> tuple[str, bool]:
    """Return (content, used_llm). used_llm is False when synthesis is unavailable."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        system = (
            "You are a senior military staff officer. "
            "Produce formal, unclassified-style OPORD paragraphs "
            "in standard US Army/Joint format."
        )
        if has_sources:
            system += (
                " Ground every substantive claim in the provided SOURCE MATERIAL "
                "and cite it inline as [source: <id>]. Never cite a source id that "
                "was not provided, and never invent one."
            )
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system,
            max_tokens=800,
            classification="CUI // SP-CTI",
        )
        resp = router.invoke("chat_response", req)
        content = (resp.content or "").strip()
        if not content:
            return "", False
        return content, True
    except Exception as exc:
        logger.warning("OPORD LLM synthesis failed: %s", exc)
        return "", False


def get_opord(opord_id: str) -> dict | None:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        base_cols = (
            "id", "title", "theater", "task_org", "situation", "mission",
            "execution", "sustainment", "command_signal", "status",
            "created_by", "approved_by", "created_at", "updated_at",
        )
        select_base = (
            "SELECT id, title, theater, task_org, situation, mission, execution, "
            "sustainment, command_signal, status, created_by, approved_by, "
            "created_at, updated_at"
        )
        row = None
        grounding_raw = None
        grounding_status = None
        try:
            row = conn.execute(
                f"{select_base}, grounding, grounding_status "  # nosec B608
                f"FROM sg_opords WHERE id = {ph}",
                (opord_id,),
            ).fetchone()
            if row is not None:
                grounding_raw = row[len(base_cols)]
                grounding_status = row[len(base_cols) + 1]
                row = row[: len(base_cols)]
        except Exception:
            # Grounding columns absent — fall back to the base projection.
            row = conn.execute(
                f"{select_base} FROM sg_opords WHERE id = {ph}",  # nosec B608
                (opord_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(zip(base_cols, row))
        try:
            result["grounding"] = json.loads(grounding_raw) if grounding_raw else {}
        except Exception:
            result["grounding"] = {}
        result["grounding_status"] = grounding_status or overall_grounding_status(
            result["grounding"]
        )
        result["grounded"] = result["grounding_status"] == GROUNDING_GROUNDED
        return result
    finally:
        conn.close()


def list_opords(theater: str = "", limit: int = 30) -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, title, theater, status, created_by, created_at "  # nosec B608
                f"FROM sg_opords WHERE theater = {ph} ORDER BY created_at DESC LIMIT {ph}",
                (theater, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, theater, status, created_by, created_at "  # nosec B608
                "FROM sg_opords ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        cols = ("id", "title", "theater", "status", "created_by", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def _record_grounding_audit(
    conn, ph: str, opord_id: str, action: str, grounding_status: str,
    actor: str, reason: str,
) -> None:
    """Append an immutable audit row for a grounding override (NIST AU). Best-effort."""
    try:
        conn.execute(
            "INSERT INTO sg_opord_grounding_audit "
            f"(id, opord_id, action, grounding_status, actor, reason, created_at) "  # nosec B608
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
            (str(uuid.uuid4()), opord_id, action, grounding_status, actor, reason, _now_utc()),
        )
    except Exception as exc:  # pragma: no cover - audit table may be absent pre-migration
        logger.warning("grounding audit write skipped: %s", exc)


def approve_opord(
    opord_id: str,
    approved_by: str = "commander",
    force: bool = False,
    force_reason: str = "",
) -> dict[str, Any]:
    """Approve an OPORD, gated on grounding status.

    A grounded OPORD approves normally. An ungrounded/fallback/pending OPORD is
    BLOCKED unless ``force=True`` with a non-empty ``force_reason`` — in which
    case the override is recorded in the append-only ``sg_opord_grounding_audit``
    table (NIST AU) and approval proceeds, tagged ``approved (forced)``.

    Returns a dict: {status, grounding_status, forced, reason?}.
    """
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        exists = conn.execute(
            f"SELECT 1 FROM sg_opords WHERE id = {ph}", (opord_id,)  # nosec B608
        ).fetchone()
        if not exists:
            return {"status": "error", "error": "OPORD not found"}

        grounding = _load_grounding(conn, ph, opord_id)
        gstatus = overall_grounding_status(grounding)

        if gstatus != GROUNDING_GROUNDED:
            if not force:
                return {
                    "status": "blocked",
                    "grounding_status": gstatus,
                    "forced": False,
                    "reason": (
                        "OPORD is not grounded — synthesized paragraphs are missing "
                        "validated [source: ...] citations. Supply an explicit, "
                        "documented force override to approve anyway."
                    ),
                }
            if not (force_reason or "").strip():
                return {
                    "status": "blocked",
                    "grounding_status": gstatus,
                    "forced": False,
                    "reason": "force override requires a non-empty force_reason.",
                }
            # Audited override.
            _record_grounding_audit(
                conn, ph, opord_id, "force_approve", gstatus, approved_by, force_reason.strip()
            )

        try:
            conn.execute(
                f"UPDATE sg_opords SET status = 'approved', approved_by = {ph}, "  # nosec B608
                f"updated_at = {ph} WHERE id = {ph}",
                (approved_by, _now_utc(), opord_id),
            )
            conn.commit()
        except Exception as exc:
            logger.error("approve_opord failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        forced = gstatus != GROUNDING_GROUNDED
        return {
            "status": "approved (forced)" if forced else "approved",
            "grounding_status": gstatus,
            "forced": forced,
        }
    finally:
        conn.close()


def delete_opord(opord_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_opords WHERE id = {ph}", (opord_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_opord failed: %s", exc)
        return False
    finally:
        conn.close()
