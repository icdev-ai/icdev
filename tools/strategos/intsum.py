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

Usage:
    from tools.strategos.intsum import generate_intsum
    result = generate_intsum(theater="western_pacific", lookback_hours=24)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

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


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aggregate_context(conn, lookback_hours: int, theater: str) -> dict[str, Any]:
    """Pull recent data from signal, track, and ORBAT tables."""
    from tools.db.storage import is_pg
    ph = "%s" if is_pg() else "?"
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    ctx: dict[str, Any] = {
        "sigint_count": 0, "eo_count": 0, "socmint_count": 0,
        "vessel_count": 0, "air_count": 0, "orbat_count": 0,
        "pir_active": 0, "ccir_triggered": 0,
        "signal_snippets": [], "theater": theater,
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

    def safe_texts(table: str, text_col: str, ts_col: str, limit: int = 5) -> list[str]:
        try:
            rows = conn.execute(
                f"SELECT {text_col} FROM {table} WHERE {ts_col} >= {ph} "  # nosec B608
                f"ORDER BY {ts_col} DESC LIMIT {ph}",
                (cutoff, limit),
            ).fetchall()
            return [r[0] or "" for r in rows if r[0]]
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

    snippets = (
        safe_texts("sg_sigint_events", "description", "collected_at", 3)
        + safe_texts("sg_socmint_signals", "text", "posted_at", 2)
    )
    ctx["signal_snippets"] = [s[:300] for s in snippets if s]

    return ctx


def _build_prompts(ctx: dict[str, Any]) -> list[str]:
    """Build one LLM prompt per INTSUM paragraph."""
    theater = ctx.get("theater", "global")
    period = f"last {ctx.get('lookback_hours', 24)} hours"
    snippets_text = "\n".join(f"- {s}" for s in ctx.get("signal_snippets", [])[:5])
    if not snippets_text:
        snippets_text = "No raw signal snippets available."

    base_context = (
        f"Theater: {theater}. Period: {period}.\n"
        f"Signal counts — SIGINT: {ctx['sigint_count']}, "
        f"EO: {ctx['eo_count']}, SOCMINT: {ctx['socmint_count']}.\n"
        f"Tracks — Vessels: {ctx['vessel_count']}, Air/UAS/Satellite: {ctx['air_count']}.\n"
        f"ORBAT units on file: {ctx['orbat_count']}. "
        f"Active PIR/CCIR: {ctx['pir_active']}. "
        f"Triggered CCIRs (unresolved): {ctx['ccir_triggered']}.\n"
        f"Recent signal snippets:\n{snippets_text}"
    )

    prompts = [
        (
            f"Write a concise 3-4 sentence Situation Overview paragraph for a military "
            f"INTSUM. Summarize overall activity level and key developments. "
            f"Do NOT use headers or bullet points.\n\nData:\n{base_context}"
        ),
        (
            f"Write a concise 3-4 sentence Adversary Activity paragraph for a military "
            f"INTSUM. Focus on observed or inferred adversary movements, intent, and "
            f"significant signal activity. Do NOT use headers or bullet points.\n\nData:\n{base_context}"
        ),
        (
            f"Write a concise 2-3 sentence Friendly Forces paragraph for a military INTSUM. "
            f"Note disposition, readiness posture, and relevant changes. "
            f"Do NOT use headers or bullet points.\n\nData:\n{base_context}"
        ),
        (
            f"Write a concise 2 sentence Weather and Terrain paragraph for a military INTSUM. "
            f"State general conditions and any militarily significant effects. "
            f"Do NOT use headers or bullet points.\n\nData:\n{base_context}"
        ),
        (
            f"Write a concise 3-4 sentence Assessment paragraph for a military INTSUM. "
            f"State your intelligence assessment of adversary intent, MLCOA, and key "
            f"indicators to watch. Do NOT use headers or bullet points.\n\nData:\n{base_context}"
        ),
        (
            "Write a 1-2 sentence Distribution paragraph for a military INTSUM. "
            "Include classification (CUI // SP-CTI) and distribution statement. "
            "Do NOT use headers or bullet points."
        ),
    ]
    return prompts


def _synthesize_paragraph(prompt: str) -> str:
    """Call LLM for one paragraph."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a military intelligence analyst writing classified INTSUM paragraphs. "
                "Write in formal intelligence report prose. Be concise and specific."
            ),
            max_tokens=512,
            classification="CUI // SP-CTI",
        )
        resp = router.invoke("chat_response", req)
        return (resp.content or "").strip()
    except Exception as exc:
        logger.warning("LLM synthesis failed: %s", exc)
        return "[Synthesis unavailable — LLM not accessible. Review source data manually.]"


def generate_intsum(theater: str = "global", lookback_hours: int = 24) -> dict[str, Any]:
    """Generate a full INTSUM and persist to DB.

    Returns: {intsum_id, paragraphs, period_start, period_end, model_used, latency_ms, error?}
    """
    import time
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        start = time.time()
        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(hours=lookback_hours)

        ctx = _aggregate_context(conn, lookback_hours, theater)
        prompts = _build_prompts(ctx)

        paragraphs = []
        for i, (heading, prompt) in enumerate(zip(_PARA_HEADINGS, prompts), start=1):
            text = _synthesize_paragraph(prompt)
            paragraphs.append({"para_num": i, "heading": heading, "content": text})

        latency_ms = int((time.time() - start) * 1000)
        intsum_id = str(uuid.uuid4())
        now = _now_utc()

        conn.execute(
            "INSERT INTO sg_intsums "
            "(id, period_start, period_end, theater, classification, "
            " prepared_by, status, latency_ms, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                intsum_id,
                period_start.isoformat(),
                period_end.isoformat(),
                theater,
                "CUI // SP-CTI",
                "ICDEV Strategos / INTSUM Engine",
                "draft",
                latency_ms,
                now,
            ),
        )
        for para in paragraphs:
            conn.execute(
                "INSERT INTO sg_intsum_paragraphs "
                "(id, intsum_id, para_num, heading, content, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), intsum_id, para["para_num"],
                 para["heading"], para["content"], now),
            )
        conn.commit()

        return {
            "intsum_id": intsum_id,
            "paragraphs": paragraphs,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "theater": theater,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        logger.error("generate_intsum failed: %s", exc)
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
    """Return full INTSUM with paragraphs."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT id, period_start, period_end, theater, status, "  # nosec B608
            f"classification, prepared_by, created_at "
            f"FROM sg_intsums WHERE id = {ph}",
            (intsum_id,),
        ).fetchone()
        if not row:
            return None
        cols = ("id", "period_start", "period_end", "theater", "status",
                "classification", "prepared_by", "created_at")
        intsum = dict(zip(cols, row))
        paras = conn.execute(
            f"SELECT para_num, heading, content FROM sg_intsum_paragraphs "  # nosec B608
            f"WHERE intsum_id = {ph} ORDER BY para_num ASC",
            (intsum_id,),
        ).fetchall()
        intsum["paragraphs"] = [
            {"para_num": r[0], "heading": r[1], "content": r[2]} for r in paras
        ]
        return intsum
    finally:
        conn.close()
