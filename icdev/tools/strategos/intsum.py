# CUI // SP-CTI
"""INTSUM Auto-Generator — Daily Intelligence Summary builder.

Aggregates recent activity from SIGINT, vessel/air tracks, ORBAT changes,
and threat assessments into a 6-paragraph INTSUM, then uses the LLM to
synthesize each paragraph.

Para 1 — Situation Overview
Para 2 — Enemy Forces / Adversary Activity
Para 3 — Friendly Forces
Para 4 — Weather and Terrain Effects
Para 5 — Assessment / Threat Estimate
Para 6 — Distribution / Classification Notice

TRUST invariant (CLAUDE.md): every LLM-drafted paragraph that makes factual
claims about observed signals must carry inline ``[source: <id>]`` citations
resolving to the concrete evidence set fed into its prompt. After generation
each grounded paragraph is validated with ``tools/quality/citation_grounding.py``
(the shared, surface-agnostic citation parser/validator — NOT re-implemented
here). Ungrounded LLM prose is flagged, a grounding verdict is persisted
alongside the INTSUM, and promotion of ungrounded content requires an explicit,
audited HITL override (``force_ungrounded=True``). The deterministic no-LLM
fallback emits template-generated prose that makes **no** citation claims.

Usage:
    from tools.strategos.intsum import generate_intsum
    result = generate_intsum(theater="western_pacific", lookback_hours=24)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = get_logger("icdev.strategos.intsum")

_PARA_HEADINGS = [
    "Situation Overview",
    "Adversary Activity",
    "Friendly Forces",
    "Weather and Terrain",
    "Assessment",
    "Distribution",
]

# Paragraph numbers (1-indexed) whose factual claims derive from the citable
# signal-evidence set (SIGINT / SOCMINT snippets). Only these require inline
# citations. Friendly Forces (3) rides on ORBAT counts, Weather (4) has no
# signal source, Distribution (6) is a boilerplate classification notice — none
# of those make citable signal claims, so requiring citations there would only
# invite fabricated ones.
_GROUNDED_PARAS = {1, 2, 5}

_SYSTEM_PROMPT = (
    "You are a military intelligence analyst writing classified INTSUM paragraphs. "
    "Write in formal intelligence report prose. Be concise and specific."
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aggregate_context(conn, lookback_hours: int, theater: str) -> dict[str, Any]:
    """Pull recent data from signal, track, and ORBAT tables.

    Builds ``ctx["evidence"]``: a list of ``{id, label, excerpt, source_ref}``
    where ``id`` is a short, prose-friendly citation handle (``S1``..``SN``)
    mapped to a concrete source row. These handles — not raw aggregate counts —
    are what the model is instructed to cite.
    """
    from tools.db.storage import is_pg
    ph = "%s" if is_pg() else "?"
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    ctx: dict[str, Any] = {
        "sigint_count": 0, "eo_count": 0, "socmint_count": 0,
        "vessel_count": 0, "air_count": 0, "orbat_count": 0,
        "pir_active": 0, "ccir_triggered": 0,
        "evidence": [], "theater": theater,
        "lookback_hours": lookback_hours,
    }

    def safe_count(table: str, where: str = "", params: tuple = ()) -> int:
        try:
            q = f"SELECT COUNT(*) FROM {table}"  # nosec B608
            if where:
                q += f" WHERE {where}"
            row = conn.execute(q, params).fetchone()
            return row[0] if row else 0
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return 0

    def safe_rows(table: str, id_col: str, text_col: str, ts_col: str,
                  limit: int = 5) -> list[tuple]:
        """Return (row_id, text) tuples for recent rows, newest first."""
        try:
            rows = conn.execute(
                f"SELECT {id_col}, {text_col} FROM {table} "  # nosec B608
                f"WHERE {ts_col} >= {ph} "
                f"ORDER BY {ts_col} DESC LIMIT {ph}",
                (cutoff, limit),
            ).fetchall()
            return [(r[0], r[1] or "") for r in rows if r[1]]
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return []

    ctx["sigint_count"] = safe_count("sg_sigint_events", f"collected_at >= {ph}", (cutoff,))
    ctx["eo_count"] = safe_count("sg_eo_signals", f"collected_at >= {ph}", (cutoff,))
    ctx["socmint_count"] = safe_count("sg_socmint_signals", f"posted_at >= {ph}", (cutoff,))
    ctx["vessel_count"] = safe_count("sg_vessel_tracks", f"timestamp >= {ph}", (cutoff,))
    ctx["air_count"] = safe_count("sg_multidomain_tracks",
                                   f"source_type IN ('adsb','uas','satellite') AND timestamp >= {ph}",
                                   (cutoff,))
    ctx["orbat_count"] = safe_count("sg_orbat_units")
    ctx["pir_active"] = safe_count("sg_pir_requirements", "status = 'active'")
    ctx["ccir_triggered"] = safe_count(
        "sg_ccir_trigger_events",
        f"resolved = 0 AND created_at >= {ph}", (cutoff,),
    )

    raw_evidence = (
        [("SIGINT", "sg_sigint_events", rid, txt)
         for rid, txt in safe_rows("sg_sigint_events", "id", "description", "collected_at", 3)]
        + [("SOCMINT", "sg_socmint_signals", rid, txt)
           for rid, txt in safe_rows("sg_socmint_signals", "id", "text", "posted_at", 2)]
    )
    evidence: list[dict[str, Any]] = []
    for label, table, rid, txt in raw_evidence:
        if not txt:
            continue
        evidence.append({
            "id": f"S{len(evidence) + 1}",
            "label": label,
            "excerpt": txt[:300],
            "source_ref": f"{table}:{rid}",
        })
    ctx["evidence"] = evidence
    return ctx


def _base_context(ctx: dict[str, Any]) -> str:
    theater = ctx.get("theater", "global")
    period = f"last {ctx.get('lookback_hours', 24)} hours"
    return (
        f"Theater: {theater}. Period: {period}.\n"
        f"Signal counts — SIGINT: {ctx['sigint_count']}, "
        f"EO: {ctx['eo_count']}, SOCMINT: {ctx['socmint_count']}.\n"
        f"Tracks — Vessels: {ctx['vessel_count']}, Air/UAS/Satellite: {ctx['air_count']}.\n"
        f"ORBAT units on file: {ctx['orbat_count']}. "
        f"Active PIR/CCIR: {ctx['pir_active']}. "
        f"Triggered CCIRs (unresolved): {ctx['ccir_triggered']}."
    )


def _evidence_block(ctx: dict[str, Any]) -> str:
    """Render the citable evidence list for grounded-paragraph prompts."""
    ev = ctx.get("evidence", [])
    if not ev:
        return ""
    lines = "\n".join(f"[{e['id']}] ({e['label']}) {e['excerpt']}" for e in ev)
    return (
        "\n\nEvidence — you MUST support every factual claim about observed "
        "signal activity with an inline citation of the form [source: S1], "
        "using ONLY the evidence ids listed below. Do not invent ids. If the "
        "evidence does not support a claim, omit the claim.\n" + lines
    )


def _build_prompts(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one LLM prompt spec per INTSUM paragraph.

    Returns a list of ``{prompt, require_citations}`` dicts. A grounded
    paragraph only *requires* citations when there is at least one evidence
    item to cite — with an empty evidence set we cannot demand citations, so
    the paragraph degrades to a lower-confidence (still uncited) draft.
    """
    base = _base_context(ctx)
    ev_block = _evidence_block(ctx)
    have_evidence = bool(ctx.get("evidence"))

    def grounded(instr: str) -> dict[str, Any]:
        return {
            "prompt": f"{instr}\n\nData:\n{base}{ev_block}",
            "require_citations": have_evidence,
        }

    def plain(instr: str) -> dict[str, Any]:
        return {"prompt": f"{instr}\n\nData:\n{base}", "require_citations": False}

    return [
        grounded(
            "Write a concise 3-4 sentence Situation Overview paragraph for a military "
            "INTSUM. Summarize overall activity level and key developments. "
            "Do NOT use headers or bullet points."
        ),
        grounded(
            "Write a concise 3-4 sentence Adversary Activity paragraph for a military "
            "INTSUM. Focus on observed or inferred adversary movements, intent, and "
            "significant signal activity. Do NOT use headers or bullet points."
        ),
        plain(
            "Write a concise 2-3 sentence Friendly Forces paragraph for a military INTSUM. "
            "Note disposition, readiness posture, and relevant changes. "
            "Do NOT use headers or bullet points."
        ),
        plain(
            "Write a concise 2 sentence Weather and Terrain paragraph for a military INTSUM. "
            "State general conditions and any militarily significant effects. "
            "Do NOT use headers or bullet points."
        ),
        grounded(
            "Write a concise 3-4 sentence Assessment paragraph for a military INTSUM. "
            "State your intelligence assessment of adversary intent, MLCOA, and key "
            "indicators to watch. Do NOT use headers or bullet points."
        ),
        {
            "prompt": (
                "Write a 1-2 sentence Distribution paragraph for a military INTSUM. "
                "Include classification (CUI // SP-CTI) and distribution statement. "
                "Do NOT use headers or bullet points."
            ),
            "require_citations": False,
        },
    ]


def _synthesize_paragraph(prompt: str) -> tuple[str, bool, str]:
    """Call LLM for one paragraph.

    Returns ``(text, llm_used, model_id)``. ``llm_used`` is False when the LLM
    is unavailable so the caller can fall back to deterministic template prose
    (which makes no citation claims).
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=512,
            classification="CUI // SP-CTI",
        )
        resp = router.invoke("chat_response", req)
        text = (resp.content or "").strip()
        if not text:
            return "", False, ""
        return text, True, (resp.model_id or "")
    except Exception as exc:
        logger.warning("LLM synthesis failed: %s", exc)
        return "", False, ""


def _template_paragraph(para_num: int, heading: str, ctx: dict[str, Any]) -> str:
    """Deterministic, citation-free paragraph assembled from aggregate counts.

    Used when the LLM is unavailable. Explicitly labelled template-generated so
    it is never mistaken for grounded, source-cited analysis.
    """
    theater = ctx.get("theater", "global")
    period = f"the last {ctx.get('lookback_hours', 24)} hours"
    if para_num == 1:
        body = (
            f"Over {period} in the {theater} theater, collection logged "
            f"{ctx['sigint_count']} SIGINT, {ctx['eo_count']} EO, and "
            f"{ctx['socmint_count']} SOCMINT signals, with {ctx['vessel_count']} "
            f"vessel and {ctx['air_count']} air/UAS/satellite tracks."
        )
    elif para_num == 2:
        body = (
            f"Adversary-associated activity is reflected in {ctx['sigint_count']} "
            f"SIGINT and {ctx['socmint_count']} SOCMINT signals; "
            f"{ctx['ccir_triggered']} CCIR trigger(s) remain unresolved."
        )
    elif para_num == 3:
        body = (
            f"{ctx['orbat_count']} ORBAT unit(s) are on file; friendly disposition "
            f"and readiness posture are unchanged pending analyst review."
        )
    elif para_num == 4:
        body = (
            "Weather and terrain effects are not available from current signal "
            "collection; no militarily significant effects are recorded."
        )
    elif para_num == 5:
        body = (
            f"Assessment is deferred to analyst review: {ctx['pir_active']} PIR/CCIR "
            f"active and {ctx['ccir_triggered']} unresolved trigger(s) are the key "
            f"indicators to watch."
        )
    else:
        body = (
            "CUI // SP-CTI. Distribution authorized to cleared personnel with a "
            "valid need-to-know."
        )
    return f"[TEMPLATE-GENERATED — no LLM available; not source-cited] {body}"


def _ground_paragraph(text: str, evidence_ids: list[str], require_citations: bool,
                      llm_used: bool) -> dict[str, Any]:
    """Validate one paragraph against the supplied evidence set.

    Delegates all citation parsing/validation to the shared
    ``citation_grounding`` module. Returns a verdict dict persisted alongside
    the paragraph.
    """
    from tools.quality.citation_grounding import parse_citations, validate_citations

    cited = parse_citations(text)
    report = validate_citations(text, evidence_ids)
    hallucinated = report["hallucinated_citations"]

    if not llm_used:
        # Template prose makes no factual signal claims and carries no
        # citations by design — grounded-as-template, never "ungrounded".
        method = "template"
        grounded = True
    elif require_citations:
        method = "llm"
        grounded = bool(cited) and not hallucinated
    else:
        # LLM prose that is not required to cite (boilerplate / non-signal).
        method = "llm"
        grounded = not hallucinated

    return {
        "method": method,
        "require_citations": require_citations,
        "grounded": grounded,
        "citations": cited,
        "hallucinated": hallucinated,
    }


def _has_grounding_columns(conn) -> bool:
    """True when migration 280 grounding columns are present on sg_intsums."""
    try:
        conn.execute("SELECT grounding_status FROM sg_intsums LIMIT 0").fetchall()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _audit_force_override(conn, intsum_id: str, findings: list[dict]) -> None:
    """Append an immutable audit row for a HITL force-ungrounded override."""
    try:
        conn.execute(
            "INSERT INTO sg_intsum_grounding_audit "
            "(id, intsum_id, findings, created_at) VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), intsum_id, json.dumps(findings), _now_utc()),
        )
    except Exception as exc:
        # Table may not exist yet (migration 280 not applied). The grounding
        # verdict itself is still persisted on sg_intsums.grounding_json.
        logger.warning("grounding force-override audit skipped: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def generate_intsum(theater: str = "global", lookback_hours: int = 24,
                    force_ungrounded: bool = False) -> dict[str, Any]:
    """Generate a full INTSUM and persist to DB with a grounding verdict.

    Each grounded paragraph is drafted from concrete source evidence, instructed
    to cite inline as ``[source: <id>]``, then validated against that evidence.
    Ungrounded LLM paragraphs are flagged; overall status is one of:

      - ``grounded``          — every grounded paragraph is source-cited & valid
      - ``ungrounded``        — at least one grounded paragraph failed validation
      - ``ungrounded_forced`` — same, but a HITL override was supplied (audited)
      - ``template``          — LLM unavailable; deterministic template prose

    Args:
        theater:          Theater / AOR label.
        lookback_hours:   Collection window.
        force_ungrounded: Explicit, audited HITL override that promotes an
                          INTSUM even when grounded paragraphs fail validation.

    Returns: {intsum_id, paragraphs, period_start, period_end, model_used,
              latency_ms, grounding, error?}
    """
    import time
    from tools.db.storage import get_connection
    from tools.quality.citation_grounding import citation_gate
    conn = get_connection()
    try:
        start = time.time()
        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(hours=lookback_hours)

        ctx = _aggregate_context(conn, lookback_hours, theater)
        evidence = ctx.get("evidence", [])
        evidence_ids = [e["id"] for e in evidence]
        prompts = _build_prompts(ctx)

        paragraphs = []
        any_llm = False
        model_used = ""
        for i, (heading, spec) in enumerate(zip(_PARA_HEADINGS, prompts), start=1):
            require = bool(spec["require_citations"]) and i in _GROUNDED_PARAS
            text, llm_used, model_id = _synthesize_paragraph(spec["prompt"])
            if not llm_used:
                text = _template_paragraph(i, heading, ctx)
            else:
                any_llm = True
                model_used = model_used or model_id
            verdict = _ground_paragraph(text, evidence_ids, require, llm_used)
            paragraphs.append({
                "para_num": i, "heading": heading, "content": text,
                **verdict,
            })

        # ── Gate: scan grounded LLM paragraphs for citation defects ──────────
        gate_sections = [
            {"item_number": p["para_num"], "content": p["content"],
             "allowed_sources": evidence_ids}
            for p in paragraphs
            if p["require_citations"] and p["method"] == "llm"
        ]
        gate_findings = citation_gate(gate_sections, require_citations=True)

        if not any_llm:
            status = "template"
        elif gate_findings:
            status = "ungrounded_forced" if force_ungrounded else "ungrounded"
        else:
            status = "grounded"

        grounding = {
            "status": status,
            "evidence_count": len(evidence),
            "evidence": [
                {"id": e["id"], "source_ref": e["source_ref"], "label": e["label"]}
                for e in evidence
            ],
            "gate_findings": gate_findings,
            "forced": bool(force_ungrounded),
            "paragraphs": [
                {"para_num": p["para_num"], "grounded": p["grounded"],
                 "method": p["method"], "require_citations": p["require_citations"],
                 "citations": p["citations"], "hallucinated": p["hallucinated"]}
                for p in paragraphs
            ],
        }

        latency_ms = int((time.time() - start) * 1000)
        intsum_id = str(uuid.uuid4())
        now = _now_utc()
        model_used = model_used or ("template" if not any_llm else "")

        has_cols = _has_grounding_columns(conn)
        if has_cols:
            conn.execute(
                "INSERT INTO sg_intsums "
                "(id, period_start, period_end, theater, classification, "
                " prepared_by, status, model_used, latency_ms, "
                " grounding_status, grounding_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    intsum_id, period_start.isoformat(), period_end.isoformat(),
                    theater, "CUI // SP-CTI", "ICDEV Strategos / INTSUM Engine",
                    "draft", model_used, latency_ms,
                    status, json.dumps(grounding), now,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO sg_intsums "
                "(id, period_start, period_end, theater, classification, "
                " prepared_by, status, model_used, latency_ms, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    intsum_id, period_start.isoformat(), period_end.isoformat(),
                    theater, "CUI // SP-CTI", "ICDEV Strategos / INTSUM Engine",
                    "draft", model_used, latency_ms, now,
                ),
            )

        for para in paragraphs:
            if has_cols:
                conn.execute(
                    "INSERT INTO sg_intsum_paragraphs "
                    "(id, intsum_id, para_num, heading, content, "
                    " grounded, require_citations, citations, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), intsum_id, para["para_num"],
                     para["heading"], para["content"],
                     1 if para["grounded"] else 0,
                     1 if para["require_citations"] else 0,
                     json.dumps(para["citations"]), now),
                )
            else:
                conn.execute(
                    "INSERT INTO sg_intsum_paragraphs "
                    "(id, intsum_id, para_num, heading, content, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), intsum_id, para["para_num"],
                     para["heading"], para["content"], now),
                )

        if force_ungrounded and gate_findings:
            _audit_force_override(conn, intsum_id, gate_findings)

        conn.commit()

        return {
            "intsum_id": intsum_id,
            "paragraphs": paragraphs,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "theater": theater,
            "model_used": model_used,
            "latency_ms": latency_ms,
            "grounding": grounding,
        }
    except Exception as exc:
        logger.error("generate_intsum failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"error": str(exc), "intsum_id": None}
    finally:
        conn.close()


def list_intsums(limit: int = 10) -> list[dict]:
    """Return recent INTSUMs (header only, no paragraphs)."""
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, period_start, period_end, theater, status, "  # nosec B608
            "classification, created_at "
            "FROM sg_intsums ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        cols = ("id", "period_start", "period_end", "theater",
                "status", "classification", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def get_intsum_detail(intsum_id: str) -> dict | None:
    """Return full INTSUM with paragraphs and grounding verdict."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        has_cols = _has_grounding_columns(conn)
        base_cols = ("id", "period_start", "period_end", "theater", "status",
                     "classification", "prepared_by", "created_at")
        select_cols = (
            "id, period_start, period_end, theater, status, "
            "classification, prepared_by, created_at"
        )
        if has_cols:
            select_cols += ", grounding_status, grounding_json"
        row = conn.execute(
            f"SELECT {select_cols} "  # nosec B608
            f"FROM sg_intsums WHERE id = {ph}",
            (intsum_id,),
        ).fetchone()
        if not row:
            return None
        cols = base_cols + (("grounding_status", "grounding_json") if has_cols else ())
        intsum = dict(zip(cols, row))
        if has_cols and intsum.get("grounding_json"):
            try:
                intsum["grounding"] = json.loads(intsum["grounding_json"])
            except Exception:
                intsum["grounding"] = None
            intsum.pop("grounding_json", None)

        para_cols = "para_num, heading, content"
        if has_cols:
            para_cols += ", grounded, require_citations, citations"
        paras = conn.execute(
            f"SELECT {para_cols} FROM sg_intsum_paragraphs "  # nosec B608
            f"WHERE intsum_id = {ph} ORDER BY para_num ASC",
            (intsum_id,),
        ).fetchall()
        para_list = []
        for r in paras:
            entry = {"para_num": r[0], "heading": r[1], "content": r[2]}
            if has_cols:
                entry["grounded"] = bool(r[3]) if r[3] is not None else None
                entry["require_citations"] = bool(r[4]) if r[4] is not None else False
                try:
                    entry["citations"] = json.loads(r[5]) if r[5] else []
                except Exception:
                    entry["citations"] = []
            para_list.append(entry)
        intsum["paragraphs"] = para_list
        return intsum
    finally:
        conn.close()
