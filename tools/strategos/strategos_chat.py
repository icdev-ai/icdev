# CUI // SP-CTI
"""Strategos Chat — domain layer for the floating intelligence chat panel.

Wraps ChatManager with a Strategos-specific system prompt, domain RAG over
sg_* tables, and entity context injection for right-click → ask flows.

Usage:
    from tools.strategos.strategos_chat import (
        create_strategos_context,
        inject_entity_context,
        StrategosRAG,
    )
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from tools.db.storage import get_connection, is_pg

logger = logging.getLogger("icdev.strategos_chat")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

STRATEGOS_SYSTEM_PROMPT = """You are STRATEGOS — an AI military intelligence analyst embedded in the ICDEV™ Strategos platform. You reason analytically across multi-domain intelligence data.

RESPONSE FORMAT:
  Lead every substantive answer with BLUF (Bottom Line Up Front) — one sentence stating the key finding or recommendation, then elaboration. Be direct. Avoid filler.

DATA AVAILABLE TO YOU (via RAG and context injection):
  • Conflict events — type, severity, location, actor, timestamp
  • ORBAT units — side, type, strength, equipment, disposition, nation
  • Intelligence briefs — SITREP, IIR, WARNORD, assessments (last 30 days)
  • Priority signals — composite-scored intercepts and OSINT
  • Maritime vessel tracks — AIS data, ghost vessels, behavioral anomalies
  • Knowledge graph entities — nodes, relationships, link analysis
  • Wargame scenarios — turn history, Lanchester attrition, OODA scores, COAs
  • Military doctrine — FM 3-0, JP 3-0, Clausewitz, MDMP

BEHAVIOR:
  • When asked about a specific entity (vessel, unit, KG node, signal), anchor your analysis to the provided context data first, then expand to broader pattern analysis.
  • Cite doctrine when it applies. Format: [FM 3-0, §X.X] or [JP 3-0].
  • For simulated scenarios, reason through consequences step by step using Lanchester attrition curves, OODA cycle time, and Center of Gravity analysis.
  • Distinguish between what the data shows (fact) and your assessment (analysis).
  • When data is insufficient, say so clearly and state what additional intelligence would be needed (PIR/CCIR).
  • All responses are classified CUI // SP-CTI unless instructed otherwise.

ENTITY CONTEXT:
  When entity context is provided at the top of the conversation (e.g., "VESSEL CONTEXT", "ORBAT UNIT", "KG NODE"), treat it as primary source data and anchor your analysis to it before drawing on broader patterns."""

# ---------------------------------------------------------------------------
# StrategosRAG
# ---------------------------------------------------------------------------

_PH = lambda: "%s" if is_pg() else "?"  # noqa: E731


class StrategosRAG:
    """Thin RAG wrapper that queries sg_* tables for domain context."""

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """Return up to top_k relevant chunks from Strategos data sources."""
        terms = [t.strip().lower() for t in query.split() if len(t.strip()) > 3][:6]
        if not terms:
            return []

        results: list[dict] = []
        ph = _PH()

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(self._query_conflict_events, terms, ph, top_k): "conflict_event",
                pool.submit(self._query_orbat_units, terms, ph, top_k): "orbat_unit",
                pool.submit(self._query_briefs, terms, ph, top_k): "intel_brief",
                pool.submit(self._query_corpus, terms, ph, top_k): "doctrine",
            }
            for fut in as_completed(futures):
                try:
                    results.extend(fut.result())
                except Exception as exc:
                    logger.debug("StrategosRAG source error: %s", exc)

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return results[:top_k]

    # -- private per-table queries --

    def _query_conflict_events(self, terms: list[str], ph: str, top_k: int) -> list[dict]:
        try:
            conn = get_connection()
            cur = conn.cursor()
            like_clauses = " OR ".join([f"LOWER(description) LIKE {ph}" for _ in terms])
            params = [f"%{t}%" for t in terms]
            cur.execute(
                f"SELECT event_type, severity, description, event_ts, source "  # nosec B608
                f"FROM sg_conflict_events WHERE {like_clauses} "
                f"ORDER BY event_ts DESC LIMIT %s",
                params + [top_k],
            )
            rows = cur.fetchall()
            conn.close()
            out = []
            for r in rows:
                content = (
                    f"[Conflict Event] Type: {r[0]}, Severity: {r[1]}, "
                    f"Source: {r[4] or 'unknown'}, Date: {str(r[3])[:10]}\n{r[2] or ''}"
                )
                out.append({"content": content[:600], "source_type": "conflict_event", "score": 0.7})
            return out
        except Exception as exc:
            logger.debug("conflict_events RAG error: %s", exc)
            return []

    def _query_orbat_units(self, terms: list[str], ph: str, top_k: int) -> list[dict]:
        try:
            conn = get_connection()
            cur = conn.cursor()
            like_clauses = " OR ".join(
                [f"(LOWER(unit_name) LIKE {ph} OR LOWER(unit_type) LIKE {ph})" for _ in terms]
            )
            params = [v for t in terms for v in (f"%{t}%", f"%{t}%")]
            cur.execute(
                f"SELECT unit_name, unit_type, nation, strength, status, location "  # nosec B608
                f"FROM sg_orbat_units WHERE {like_clauses} LIMIT %s",
                params + [top_k],
            )
            rows = cur.fetchall()
            conn.close()
            out = []
            for r in rows:
                content = (
                    f"[ORBAT Unit] {r[0]} ({r[1]}), Nation: {r[2] or '?'}, "
                    f"Strength: {r[3] or '?'}, Status: {r[4] or '?'}, Location: {r[5] or '?'}"
                )
                out.append({"content": content[:400], "source_type": "orbat_unit", "score": 0.65})
            return out
        except Exception as exc:
            logger.debug("orbat_units RAG error: %s", exc)
            return []

    def _query_briefs(self, terms: list[str], ph: str, top_k: int) -> list[dict]:
        try:
            conn = get_connection()
            cur = conn.cursor()
            like_clauses = " OR ".join(
                [f"(LOWER(title) LIKE {ph} OR LOWER(content_md) LIKE {ph})" for _ in terms]
            )
            params = [v for t in terms for v in (f"%{t}%", f"%{t}%")]
            now = datetime.now(timezone.utc).isoformat()
            cur.execute(
                f"SELECT brief_type, title, content_md, created_at "  # nosec B608
                f"FROM sg_intelligence_briefs WHERE ({like_clauses}) "
                f"ORDER BY created_at DESC LIMIT %s",
                params + [top_k],
            )
            rows = cur.fetchall()
            conn.close()
            out = []
            for r in rows:
                snippet = (r[2] or "")[:400]
                content = f"[Intel Brief / {r[0]}] {r[1]} ({str(r[3])[:10]})\n{snippet}"
                out.append({"content": content[:600], "source_type": "intel_brief", "score": 0.75})
            return out
        except Exception as exc:
            logger.debug("intel_briefs RAG error: %s", exc)
            return []

    def _query_corpus(self, terms: list[str], ph: str, top_k: int) -> list[dict]:
        try:
            conn = get_connection()
            cur = conn.cursor()
            like_clauses = " OR ".join(
                [f"(LOWER(title) LIKE {ph} OR LOWER(content) LIKE {ph})" for _ in terms]
            )
            params = [v for t in terms for v in (f"%{t}%", f"%{t}%")]
            cur.execute(
                f"SELECT source_type, title, content "  # nosec B608
                f"FROM sg_corpus_documents WHERE {like_clauses} LIMIT %s",
                params + [top_k],
            )
            rows = cur.fetchall()
            conn.close()
            out = []
            for r in rows:
                snippet = (r[2] or "")[:500]
                content = f"[Doctrine / {r[0]}] {r[1]}\n{snippet}"
                out.append({"content": content[:600], "source_type": "doctrine", "score": 0.8})
            return out
        except Exception as exc:
            logger.debug("corpus RAG error: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Context factory
# ---------------------------------------------------------------------------

def create_strategos_context(user_id: str, page: str = "") -> dict:
    """Create a ChatManager context pre-loaded with the Strategos system prompt."""
    try:
        from tools.dashboard.chat_manager import chat_manager

        title = f"STRATEGOS — {page}" if page else "STRATEGOS Intelligence Chat"
        ctx = chat_manager.create_context(
            user_id=user_id,
            title=title,
            system_prompt=STRATEGOS_SYSTEM_PROMPT,
            agent_model="sonnet",
        )
        logger.info("Created Strategos context %s for user %s", ctx.get("context_id"), user_id)
        return ctx
    except Exception as exc:
        logger.error("Failed to create Strategos context: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Entity context injection
# ---------------------------------------------------------------------------

_ENTITY_TEMPLATES: dict[str, str] = {
    "vessel": (
        "VESSEL CONTEXT\n"
        "Name: {label}\n"
        "MMSI: {mmsi}\n"
        "Flag: {flag}\n"
        "Position: {lat:.4f}°N, {lon:.4f}°E\n"
        "Speed: {speed_kts} kts\n"
        "Cargo type: {cargo_type}\n"
        "Track points: {track_points}\n"
        "Anomaly flag: {anomaly_flag}\n\n"
        "Provide a maritime intelligence assessment of this vessel. "
        "Include behavioral analysis, flag-state risk, cargo risk, and any indicators of concern."
    ),
    "ghost_vessel": (
        "GHOST VESSEL CONTEXT\n"
        "Name: {label}\n"
        "MMSI: {mmsi}\n"
        "Flag: {flag}\n"
        "Last position: {lat:.4f}°N, {lon:.4f}°E\n"
        "Anomaly: {anomaly_flag}\n\n"
        "This vessel has been flagged as a dark/ghost vessel (AIS gaps or spoofing suspected). "
        "Provide a threat assessment including likely intent, risk level, and recommended PIR."
    ),
    "kg_node": (
        "KNOWLEDGE GRAPH NODE\n"
        "Label: {label}\n"
        "Type: {node_type}\n"
        "Node ID: {id}\n"
        "Connected nodes: {neighbors}\n\n"
        "Analyze this entity within the intelligence knowledge graph. "
        "Describe its significance, likely relationships, and any intelligence gaps."
    ),
    "orbat_unit": (
        "ORBAT UNIT\n"
        "Name: {label}\n"
        "Side: {side}\n"
        "Strength: {strength}\n"
        "Unit ID: {unit_id}\n\n"
        "Provide a force assessment for this ORBAT unit. "
        "Include combat effectiveness estimate, vulnerabilities, and recommended COA targeting."
    ),
    "supply_node": (
        "DIB SUPPLY NODE\n"
        "Name: {label}\n"
        "Side: {side}\n"
        "Tier: {tier}\n"
        "Criticality: {criticality}\n\n"
        "Assess this defense industrial base node. "
        "Include interdiction priority, downstream dependencies, and strategic significance."
    ),
    "signal": (
        "INTELLIGENCE SIGNAL\n"
        "Title: {label}\n"
        "Signal ID: {signal_id}\n"
        "Priority score: {priority}\n"
        "Source: {source}\n\n"
        "Analyze this intelligence signal. "
        "Assess credibility, significance, what collection requirements it addresses, and follow-on actions."
    ),
    "intel_brief": (
        "INTELLIGENCE BRIEF\n"
        "Title: {label}\n"
        "Brief ID: {brief_id}\n"
        "Type: {brief_type}\n\n"
        "Summarize the key intelligence findings from this brief and provide your assessment."
    ),
}

_DEFAULTS: dict[str, str] = {
    "label": "Unknown", "mmsi": "N/A", "flag": "N/A",
    "lat": 0.0, "lon": 0.0, "speed_kts": "N/A", "cargo_type": "N/A",
    "track_points": "N/A", "anomaly_flag": "N/A",
    "id": "N/A", "node_type": "N/A", "neighbors": 0,
    "side": "N/A", "strength": "N/A", "unit_id": "N/A",
    "tier": "N/A", "criticality": "N/A",
    "signal_id": "N/A", "priority": "N/A", "source": "N/A",
    "brief_id": "N/A", "brief_type": "N/A",
}


def inject_entity_context(context_id: str, entity: dict) -> dict:
    """Format entity as structured opening message and queue it into the context."""
    try:
        from tools.dashboard.chat_manager import chat_manager

        entity_type = entity.get("type", "unknown")
        template = _ENTITY_TEMPLATES.get(entity_type)
        if not template:
            text = f"ENTITY CONTEXT\nType: {entity_type}\n" + "\n".join(
                f"{k}: {v}" for k, v in entity.items() if k != "type"
            )
        else:
            ctx_data = {**_DEFAULTS, **entity}
            try:
                text = template.format(**ctx_data)
            except (KeyError, ValueError):
                text = f"ENTITY CONTEXT ({entity_type})\n" + "\n".join(
                    f"{k}: {v}" for k, v in entity.items()
                )

        result = chat_manager.send_message(context_id, text, role="user")
        logger.info("Injected %s entity context into %s", entity_type, context_id)
        return result
    except Exception as exc:
        logger.error("Failed to inject entity context: %s", exc)
        return {"error": str(exc)}
