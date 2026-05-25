# CUI // SP-CTI
"""Strategy Agent — generates Courses of Action (COAs) from scenario context.

Extends sg-agent-04 design. RAG activation gate (sg-rag-06) is now open:
before calling Ollama, the agent retrieves doctrine passages and historical
conflict events via corrective_rag.parallel_retrieve() (D-KARL-3) and injects
them into ScenarioContext.rag_context.

Usage:
    from tools.strategos.strategy_agent import StrategyAgent, ScenarioContext
    agent = StrategyAgent()
    result = agent.generate_coas(ScenarioContext(scenario="..."))

CLI:
    python tools/strategos/strategy_agent.py --scenario "..." --json
    python tools/strategos/strategy_agent.py --scenario "..." --theater ukraine
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.strategy_agent")

# ── Constants ─────────────────────────────────────────────────────────────────

_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.environ.get("OLLAMA_STRATEGY_MODEL", os.environ.get("OLLAMA_MODEL", "qwen3.5:latest"))
_TIMEOUT = int(os.environ.get("STRATEGY_AGENT_TIMEOUT", "120"))

_MAX_COAS = 3          # MDMP standard: generate 3 COAs minimum
_RAG_TOP_K = 8         # documents per strategy retrieved before merge
_RAG_MAX_CHARS = 3000  # rag_context injected into prompt is capped here

# GraphRAG scoring profile: provenance weights historical traceback
# (edge_weight=0.5, centrality=0.3, recency=0.2) — best match for doctrine
_RAG_PROFILE = "provenance"

_SYSTEM_PROMPT = """\
You are a military strategic planning advisor. Given a scenario context, generate
Courses of Action (COAs) following the MDMP (Military Decision Making Process).

For each COA provide:
- name: short identifier (COA Alpha / COA Bravo / COA Charlie)
- description: 2-3 sentence summary of the approach
- scheme_of_maneuver: how forces are employed to achieve the objective
- risk_level: LOW / MEDIUM / HIGH / CRITICAL
- feasibility: 0.0-1.0 (can it be accomplished with available resources)
- acceptability: 0.0-1.0 (is the cost justified by the expected gain)
- suitability: 0.0-1.0 (does it accomplish the mission)
- distinguishing_factor: what makes this COA unique vs the others
- doctrine_basis: cite one applicable doctrine reference (FM 3-0, JP 3-0, Clausewitz, etc.)
- historical_analogy: name one historical case with similar conditions and what lesson it provides

If DOCTRINE CONTEXT or HISTORICAL EVENTS are present in the intelligence context, ground
your doctrine_basis and historical_analogy citations in those retrieved passages.

Return ONLY a valid JSON object with key "courses_of_action" containing a list of COA objects.
Do not include markdown fences or commentary outside the JSON.
"""


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class ScenarioContext:
    """Input context for the strategy agent.

    rag_context: populated by _enrich_with_rag() before Ollama call;
                 callers may also pre-set this to skip RAG enrichment.
    """
    scenario: str
    theater: str = "unspecified"
    commander_intent: str = ""
    constraints: list[str] = field(default_factory=list)
    pmesii_vector: list[float] = field(default_factory=list)  # [P,M,E,S,I,I2,PT] 0–1
    rag_context: str | None = None  # None → enriched by sg-rag-06; set → skip enrichment

    def to_prompt(self) -> str:
        """Serialize context to LLM-ready text block."""
        parts = [f"THEATER: {self.theater}"]
        if self.commander_intent:
            parts.append(f"COMMANDER'S INTENT: {self.commander_intent}")
        if self.constraints:
            parts.append("CONSTRAINTS:\n" + "\n".join(f"  - {c}" for c in self.constraints))
        if self.pmesii_vector and len(self.pmesii_vector) == 7:
            labels = ["Political", "Military", "Economic", "Social", "Infrastructure", "Info", "PhysEnv"]
            pmesii_str = ", ".join(f"{l}={v:.2f}" for l, v in zip(labels, self.pmesii_vector))
            parts.append(f"PMESII-PT: {pmesii_str}")
        if self.rag_context:
            parts.append(f"INTELLIGENCE CONTEXT:\n{self.rag_context}")
        parts.append(f"\nSCENARIO:\n{self.scenario}")
        return "\n\n".join(parts)


@dataclass
class CourseOfAction:
    """A single Course of Action."""
    name: str
    description: str
    scheme_of_maneuver: str = ""
    risk_level: str = "MEDIUM"           # LOW / MEDIUM / HIGH / CRITICAL
    feasibility: float = 0.5             # 0.0–1.0
    acceptability: float = 0.5          # 0.0–1.0
    suitability: float = 0.5             # 0.0–1.0
    distinguishing_factor: str = ""
    doctrine_basis: str = ""             # FM/JP/Clausewitz citation grounding this COA
    historical_analogy: str = ""         # historical case with parallel conditions

    @property
    def composite_score(self) -> float:
        """Weighted composite of feasibility, acceptability, suitability."""
        return round(
            self.feasibility * 0.4
            + self.acceptability * 0.3
            + self.suitability * 0.3,
            3,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["composite_score"] = self.composite_score
        return d


@dataclass
class StrategyResult:
    """Output from the strategy agent — consumed by hitl_advisor.py for HITL review.

    recommended_coa_index: index into courses_of_action of the highest composite_score COA.
    rag_active: False when RAG returned nothing; True after sg-rag-06 enrichment succeeds.
    rag_doc_count: number of RAG documents merged into rag_context.
    """
    courses_of_action: list[CourseOfAction] = field(default_factory=list)
    recommended_coa_index: int = 0
    model_used: str = ""
    latency_ms: int = 0
    rag_active: bool = False
    rag_doc_count: int = 0
    theater: str = ""
    timestamp: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "courses_of_action": [c.to_dict() for c in self.courses_of_action],
            "recommended_coa_index": self.recommended_coa_index,
            "recommended_coa": self.courses_of_action[self.recommended_coa_index].name
                if self.courses_of_action else "",
            "model_used": self.model_used,
            "latency_ms": self.latency_ms,
            "rag_active": self.rag_active,
            "rag_doc_count": self.rag_doc_count,
            "theater": self.theater,
            "timestamp": self.timestamp,
            "error": self.error,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ── RAG enrichment ────────────────────────────────────────────────────────────

def _query_sg_knowledge(
    scenario: str,
    theater: str,
    top_k: int = 6,
) -> tuple[list[dict], list[dict]]:
    """Directly query sg_corpus_documents and sg_conflict_events with OR-based matching.

    The corrective_rag source_scan uses AND-matching across all query terms, which
    is too strict for long scenario queries. This function uses OR matching on a
    short set of key terms so individual term hits surface doctrine + events.

    Returns (doctrine_docs, event_docs) — each a list of {content, source, score} dicts.
    """
    try:
        from tools.db.storage import get_connection

        conn = get_connection("icdev")
        if conn is None:
            return [], []

        # Extract key terms: theater + first 3 meaningful words from scenario
        stop = {"a", "an", "the", "of", "and", "or", "in", "at", "to", "for",
                "is", "are", "was", "were", "be", "by", "on", "with", "from"}
        scenario_words = [w.lower() for w in scenario.split() if w.lower() not in stop]
        key_terms: list[str] = []
        if theater and theater != "unspecified":
            key_terms.append(theater.lower())
        key_terms.extend(scenario_words[:3])
        key_terms = list(dict.fromkeys(key_terms))[:4]  # dedup, max 4

        if not key_terms:
            return [], []

        # OR-based LIKE clauses — any term hit is a match
        def _or_like(col_expr: str, terms: list[str]) -> tuple[str, list]:
            clauses = [f"{col_expr} ILIKE ?" for t in terms]
            params = [f"%{t}%" for t in terms]
            return f"({' OR '.join(clauses)})", params

        # Query sg_corpus_documents
        col_expr = "COALESCE(title, '') || ' ' || COALESCE(content, '')"
        where_sql, params = _or_like(col_expr, key_terms)
        sql = (
            f"SELECT id, title, content FROM sg_corpus_documents "  # nosec B608
            f"WHERE {where_sql} LIMIT ?"
        )
        doctrine_docs: list[dict] = []
        try:
            cur = conn.execute(sql, params + [top_k])
            for row in cur.fetchall():
                row_id, title, content = row[0], row[1] or "", row[2] or ""
                snippet = (title + " — " + content).strip()[:400]
                if snippet:
                    doctrine_docs.append({
                        "content": snippet,
                        "source": "source_scan:sg_corpus_documents",
                        "id": str(row_id),
                        "score": 0.6,
                        "metadata": {"source_type": "sg_corpus_documents"},
                    })
        except Exception as exc:
            logger.debug("sg_corpus_documents query failed: %s", exc)

        # Query sg_conflict_events — description is most populated column
        col_expr2 = "COALESCE(description, '') || ' ' || COALESCE(event_type, '')"
        where_sql2, params2 = _or_like(col_expr2, key_terms)
        sql2 = (
            f"SELECT id, description, event_type, severity, event_ts FROM sg_conflict_events "  # nosec B608
            f"WHERE {where_sql2} ORDER BY event_ts DESC NULLS LAST LIMIT ?"
        )
        event_docs: list[dict] = []
        try:
            cur2 = conn.execute(sql2, params2 + [top_k])
            for row in cur2.fetchall():
                row_id, desc, evt_type, severity, ts = (
                    row[0], row[1] or "", row[2] or "", row[3] or "", row[4]
                )
                ts_str = str(ts)[:10] if ts else ""
                snippet = f"[{evt_type}] {desc}".strip()[:300]
                if snippet:
                    event_docs.append({
                        "content": snippet,
                        "source": "source_scan:sg_conflict_events",
                        "id": str(row_id),
                        "score": 0.55,
                        "metadata": {
                            "source_type": "sg_conflict_events",
                            "severity": severity,
                            "event_ts": ts_str,
                        },
                    })
        except Exception as exc:
            logger.debug("sg_conflict_events query failed: %s", exc)

        return doctrine_docs, event_docs

    except Exception as exc:
        logger.debug("SG knowledge query failed: %s", exc)
        return [], []


def _enrich_with_rag(context: ScenarioContext) -> tuple[str | None, int]:
    """Query doctrine corpus + historical events via RAG-KG bridge before COA generation.

    Two-track retrieval:
      1. corrective_rag.parallel_retrieve() — vector + graphrag + source_scan (D-KARL-3)
      2. _query_sg_knowledge() — direct OR-based scan of sg_corpus_documents and
         sg_conflict_events (bypasses AND-strict source_scan for long queries)

    Merges both tracks, deduplicates by id, formats into DOCTRINE CONTEXT /
    HISTORICAL EVENTS sections. Returns (rag_context_text, doc_count).
    Returns (None, 0) on total failure so caller proceeds without context.
    """
    # Track 1: corrective_rag parallel retrieval
    crag_docs: list[dict] = []
    try:
        from tools.rag.corrective_rag import parallel_retrieve

        query_parts = []
        if context.theater and context.theater != "unspecified":
            query_parts.append(context.theater)
        query_parts.append(context.scenario)
        query = " ".join(query_parts)[:500]

        result = parallel_retrieve(
            query=query,
            top_k=_RAG_TOP_K,
            profile=_RAG_PROFILE,
            aggregate=False,
        )
        crag_docs = result.get("merged_documents", [])
    except Exception as exc:
        logger.debug("corrective_rag track failed: %s", exc)

    # Track 2: direct SG knowledge scan (OR-based, theater-aware)
    sg_doctrine, sg_events = _query_sg_knowledge(
        scenario=context.scenario,
        theater=context.theater,
        top_k=_RAG_TOP_K,
    )

    # Merge and deduplicate by id
    seen_ids: set[str] = set()
    doctrine_docs: list[dict] = []
    event_docs: list[dict] = []
    other_docs: list[dict] = []

    # Prioritize SG-specific results first
    for doc in sg_doctrine:
        doc_id = doc.get("id", "")
        if doc_id not in seen_ids:
            seen_ids.add(doc_id)
            doctrine_docs.append(doc)

    for doc in sg_events:
        doc_id = doc.get("id", "")
        if doc_id not in seen_ids:
            seen_ids.add(doc_id)
            event_docs.append(doc)

    # Fold in corrective_rag results, categorizing by source
    for doc in crag_docs:
        doc_id = doc.get("id", "")
        src = doc.get("source", "")
        if doc_id in seen_ids:
            continue
        seen_ids.add(doc_id)
        if "sg_corpus_documents" in src or "corpus" in src.lower():
            doctrine_docs.append(doc)
        elif "sg_conflict_events" in src or "conflict" in src.lower():
            event_docs.append(doc)
        else:
            other_docs.append(doc)

    total_docs = len(doctrine_docs) + len(event_docs) + len(other_docs)
    if total_docs == 0:
        return None, 0

    sections: list[str] = []

    if doctrine_docs:
        lines = ["=== DOCTRINE CONTEXT ==="]
        for d in doctrine_docs[:4]:
            snippet = d.get("content", "").strip()[:400]
            if snippet:
                lines.append(f"• {snippet}")
        sections.append("\n".join(lines))

    if event_docs:
        lines = ["=== HISTORICAL EVENTS ==="]
        for d in event_docs[:4]:
            snippet = d.get("content", "").strip()[:300]
            if snippet:
                lines.append(f"• {snippet}")
        sections.append("\n".join(lines))

    if other_docs and not sections:
        # Fallback: use whatever corrective_rag retrieved
        lines = ["=== RETRIEVED CONTEXT ==="]
        for d in other_docs[:6]:
            snippet = d.get("content", "").strip()[:300]
            if snippet:
                lines.append(f"• {snippet}")
        sections.append("\n".join(lines))

    if not sections:
        return None, 0

    rag_text = "\n\n".join(sections)
    if len(rag_text) > _RAG_MAX_CHARS:
        rag_text = rag_text[:_RAG_MAX_CHARS] + "\n[...truncated]"

    return rag_text, total_docs


# ── Strategy Agent ────────────────────────────────────────────────────────────

class StrategyAgent:
    """Generates COAs from scenario context.

    Flow:
      1. If context.rag_context is None, call _enrich_with_rag() to retrieve
         doctrine passages + historical conflict events via corrective_rag
         (D-KARL-3, provenance profile).
      2. Call Ollama /api/chat with enriched context; fall back to deterministic
         stub COAs if Ollama is unavailable.
    """

    def __init__(
        self,
        base_url: str = _OLLAMA_BASE_URL,
        model: str = _OLLAMA_MODEL,
        timeout: int = _TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    # ── Public API ─────────────────────────────────────────────────────────

    def generate_coas(self, context: ScenarioContext) -> StrategyResult:
        """Generate COAs for a scenario context.

        Enriches context with doctrine + historical event RAG context before
        calling Ollama. Falls back to deterministic stub COAs if Ollama is
        unavailable. Returns StrategyResult with rag_active=True when RAG
        retrieved at least one document.
        """
        t0 = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        rag_doc_count = 0

        # RAG enrichment gate (sg-rag-06): populate rag_context if not pre-set
        if context.rag_context is None:
            rag_text, rag_doc_count = _enrich_with_rag(context)
            if rag_text:
                context.rag_context = rag_text

        rag_active = context.rag_context is not None

        try:
            raw = self._call_ollama(context)
            coas = self._parse_coas(raw)
            error = ""
        except Exception as exc:
            logger.warning("Ollama call failed (%s) — using deterministic stub COAs", exc)
            coas = _stub_coas(context)
            error = str(exc)

        if not coas:
            coas = _stub_coas(context)

        coas = coas[:_MAX_COAS]
        best_idx = max(range(len(coas)), key=lambda i: coas[i].composite_score)
        latency_ms = int((time.monotonic() - t0) * 1000)

        return StrategyResult(
            courses_of_action=coas,
            recommended_coa_index=best_idx,
            model_used=self._model,
            latency_ms=latency_ms,
            rag_active=rag_active,
            rag_doc_count=rag_doc_count,
            theater=context.theater,
            timestamp=timestamp,
            error=error,
        )

    # ── Ollama call ────────────────────────────────────────────────────────

    def _call_ollama(self, context: ScenarioContext) -> str:
        """Call Ollama chat() and return the assistant message content."""
        try:
            import ollama as _ollama
        except ImportError as exc:
            raise ImportError("ollama library required: pip install ollama") from exc

        client = _ollama.Client(host=self._base_url, timeout=self._timeout)

        # Disable thinking tokens for reasoning models (qwen3+, gemma4) — without
        # this, thinking tokens consume the entire budget and content comes back empty.
        _model_lower = self._model.lower()
        think: bool | None = (
            False if any(x in _model_lower for x in ("qwen3", "gemma4")) else None
        )

        resp = client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": context.to_prompt()},
            ],
            format="json",
            options={"temperature": 0.3, "num_predict": 1024},
            think=think,
            stream=False,
        )
        return resp.message.content

    # ── Parse COAs from LLM output ─────────────────────────────────────────

    def _parse_coas(self, raw: str) -> list[CourseOfAction]:
        """Parse JSON response from LLM into a list of CourseOfAction objects."""
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError(f"No JSON found in LLM output: {raw[:200]}")
            obj = json.loads(raw[start:end])

        coa_list = obj.get("courses_of_action", [])
        if not isinstance(coa_list, list):
            raise ValueError("Expected 'courses_of_action' list in response")

        coas: list[CourseOfAction] = []
        for item in coa_list:
            if not isinstance(item, dict):
                continue
            coas.append(CourseOfAction(
                name=str(item.get("name", f"COA {len(coas) + 1}")),
                description=str(item.get("description", "")),
                scheme_of_maneuver=str(item.get("scheme_of_maneuver", "")),
                risk_level=str(item.get("risk_level", "MEDIUM")).upper(),
                feasibility=float(item.get("feasibility", 0.5)),
                acceptability=float(item.get("acceptability", 0.5)),
                suitability=float(item.get("suitability", 0.5)),
                distinguishing_factor=str(item.get("distinguishing_factor", "")),
                doctrine_basis=str(item.get("doctrine_basis", "")),
                historical_analogy=str(item.get("historical_analogy", "")),
            ))
        return coas

    # ── Parallel RAG dispatcher ────────────────────────────────────────────

    def _retrieve_rag_context(
        self,
        query: str,
        theater: str = "unspecified",
        top_k: int = _RAG_TOP_K,
    ) -> dict[str, list[dict]]:
        """Run doctrine corpus + historical event queries in parallel.

        Fires two retrieval workers concurrently via ThreadPoolExecutor:
          1. Doctrine worker — rag_vector + graphrag strategies via corrective_rag
          2. Events worker  — source_scan strategy via corrective_rag, filtered
             to sg_conflict_events rows

        Returns {'doctrine_results': [...], 'event_results': [...]}.
        Each list contains {content, source, id, score, metadata} dicts from
        corrective_rag.parallel_retrieve merged_documents.
        """
        from concurrent.futures import ThreadPoolExecutor

        full_query = (
            f"{theater} {query}".strip()
            if theater and theater != "unspecified"
            else query
        )[:500]

        def _fetch_doctrine() -> list[dict]:
            try:
                from tools.rag.corrective_rag import parallel_retrieve

                result = parallel_retrieve(
                    query=full_query,
                    top_k=top_k,
                    profile=_RAG_PROFILE,
                    aggregate=False,
                    sources=["rag_vector", "graphrag"],
                )
                return result.get("merged_documents", [])
            except Exception as exc:
                logger.debug("Doctrine worker failed: %s", exc)
                return []

        def _fetch_events() -> list[dict]:
            try:
                from tools.rag.corrective_rag import parallel_retrieve

                result = parallel_retrieve(
                    query=full_query,
                    top_k=top_k,
                    profile=_RAG_PROFILE,
                    aggregate=False,
                    sources=["source_scan"],
                )
                return [
                    d for d in result.get("merged_documents", [])
                    if "sg_conflict_events" in d.get("source", "")
                    or "conflict" in d.get("source", "").lower()
                ]
            except Exception as exc:
                logger.debug("Events worker failed: %s", exc)
                return []

        from concurrent.futures import wait as _futures_wait

        doctrine_results: list[dict] = []
        event_results: list[dict] = []

        # shutdown(wait=False) so the executor exit doesn't block until threads finish;
        # futures_wait caps the combined wall-clock window to 3 seconds.
        executor = ThreadPoolExecutor(max_workers=2)
        try:
            f_doctrine = executor.submit(_fetch_doctrine)
            f_events = executor.submit(_fetch_events)
            done, _ = _futures_wait([f_doctrine, f_events], timeout=2.9)
            if f_doctrine in done:
                try:
                    doctrine_results = f_doctrine.result()
                except Exception as exc:
                    logger.debug("Doctrine result error: %s", exc)
            if f_events in done:
                try:
                    event_results = f_events.result()
                except Exception as exc:
                    logger.debug("Events result error: %s", exc)
        finally:
            executor.shutdown(wait=False)

        return {"doctrine_results": doctrine_results, "event_results": event_results}


# ── Deterministic stub COAs (Ollama unavailable) ──────────────────────────────

def _stub_coas(context: ScenarioContext) -> list[CourseOfAction]:
    """Return template COAs when Ollama is unavailable."""
    theater = context.theater
    return [
        CourseOfAction(
            name="COA Alpha",
            description=f"[STUB] Direct action approach for {theater} scenario. "
                        "Concentrates available forces on the primary objective.",
            scheme_of_maneuver="Main effort attacks along axis of advance; "
                               "supporting effort fixes flanks.",
            risk_level="HIGH",
            feasibility=0.70,
            acceptability=0.60,
            suitability=0.80,
            distinguishing_factor="Speed and decisive engagement; highest tempo.",
        ),
        CourseOfAction(
            name="COA Bravo",
            description=f"[STUB] Economy of force approach for {theater} scenario. "
                        "Preserves resources while degrading adversary capability.",
            scheme_of_maneuver="Shaping operations isolate objective; "
                               "main effort exploits degraded adversary position.",
            risk_level="MEDIUM",
            feasibility=0.80,
            acceptability=0.75,
            suitability=0.75,
            distinguishing_factor="Balanced risk; maximizes sustainability.",
        ),
        CourseOfAction(
            name="COA Charlie",
            description=f"[STUB] Consolidation approach for {theater} scenario. "
                        "Holds current positions and builds conditions for future operations.",
            scheme_of_maneuver="Defend in depth; disrupt adversary initiative; "
                               "prepare conditions for transition to offense.",
            risk_level="LOW",
            feasibility=0.90,
            acceptability=0.55,
            suitability=0.65,
            distinguishing_factor="Lowest risk; preserves force; slowest to decisive outcome.",
        ),
    ]


# ── Convenience function for hitl_advisor.py ──────────────────────────────────

def get_coas(
    scenario: str,
    theater: str = "unspecified",
    commander_intent: str = "",
    constraints: list[str] | None = None,
    pmesii_vector: list[float] | None = None,
    rag_context: str | None = None,
) -> StrategyResult:
    """Convenience wrapper — instantiates StrategyAgent and returns result.

    Primary entry point for hitl_advisor.py:

        from tools.strategos.strategy_agent import get_coas
        result = get_coas(scenario=text, theater="ukraine")
        # result.courses_of_action → list of COAs for HITL review
        # result.rag_active → True when doctrine/events were retrieved
    """
    ctx = ScenarioContext(
        scenario=scenario,
        theater=theater,
        commander_intent=commander_intent,
        constraints=constraints or [],
        pmesii_vector=pmesii_vector or [],
        rag_context=rag_context,
    )
    return StrategyAgent().generate_coas(ctx)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Strategy Agent — COA generator with RAG")
    parser.add_argument("--scenario", required=True, help="Scenario description text")
    parser.add_argument("--theater", default="unspecified", help="Theater / AOR label")
    parser.add_argument("--intent", default="", help="Commander's intent")
    parser.add_argument("--constraint", action="append", dest="constraints", default=[],
                        help="Constraint (repeatable)")
    parser.add_argument("--no-rag", action="store_true",
                        help="Skip RAG enrichment (use Ollama only)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    # --no-rag passes a sentinel string to skip enrichment
    pre_rag = "__no_rag__" if args.no_rag else None

    result = get_coas(
        scenario=args.scenario,
        theater=args.theater,
        commander_intent=args.intent,
        constraints=args.constraints,
        rag_context=pre_rag,
    )

    # Strip no-rag sentinel from output
    if result.error == "" and pre_rag:
        pass

    if args.json:
        print(result.to_json())
    else:
        print("\n── Strategy Agent — COA Report ─────────────────────────────────")
        print(f"Theater  : {result.theater}")
        print(f"Model    : {result.model_used}  RAG={'ON (' + str(result.rag_doc_count) + ' docs)' if result.rag_active else 'STUB'}")
        print(f"Latency  : {result.latency_ms}ms")
        if result.error:
            print(f"Warning  : {result.error}")
        print()
        for i, coa in enumerate(result.courses_of_action):
            marker = " ← RECOMMENDED" if i == result.recommended_coa_index else ""
            print(f"  {coa.name}{marker}")
            print(f"    Risk     : {coa.risk_level}  Score={coa.composite_score:.3f}")
            print(f"    Summary  : {coa.description}")
            if coa.distinguishing_factor:
                print(f"    Distinct : {coa.distinguishing_factor}")
            print()


if __name__ == "__main__":
    _main()
