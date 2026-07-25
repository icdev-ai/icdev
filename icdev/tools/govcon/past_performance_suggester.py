"""Past-performance suggestion helper for proposals / RFI workbench.

Composes EXISTING data — it creates no new data source. Given an opportunity's
requirements (capture record / RFI), it matches *completed* contracts already
recorded in the CPMP tables (``cpmp_contracts`` + ``cpmp_cpars_assessments``)
and the win/loss outcome data (``pg_win_loss_records``), ranks them by
capability / agency / NAICS similarity, and returns **suggested references**
with key metrics.

Design guarantees (enforced by tests):
  - **Suggestions only.** Nothing is ever written back into a proposal or RFI
    section. A human selects. ``suggest_references`` is read-only over the CPMP
    tables and returns a plain dict; the ``auto_inserted`` flag is always False.
  - **Grounded.** Every suggested reference carries ``[source: ...]`` citations
    to the underlying CPMP records, validated with the shared
    ``tools.quality.citation_grounding`` module (no re-implementation of
    citation parsing/validation).
  - **Provider abstraction for similarity.** Similarity reuses the shared
    embedding provider (``tools.llm.get_embedding_provider``). When no live
    provider is available (air-gap / offline test env), it degrades to a
    deterministic token-overlap fallback so the helper is fully offline-safe.
  - **Graceful when tables absent.** A fresh DB without the CPMP tables yields
    an empty suggestion list rather than an error.

Citation form: ``[source: cpmp_contracts:<id>]`` (and, when present, a matching
``cpmp_cpars_assessments:<id>`` tag). These are parseable by
``citation_grounding.parse_citations``.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.quality.citation_grounding import (  # noqa: E402
    has_citations,
    validate_citations,
)

# Contracts in these states are "completed enough" to cite as past performance.
_COMPLETED_STATUSES = ("active", "option_pending", "complete", "closed")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "will",
        "are", "our", "was", "has", "have", "not", "all", "any", "can", "its",
        "a", "an", "of", "to", "in", "on", "by", "or", "as", "at", "is", "be",
        "we", "us", "it", "support", "services", "service", "contract",
        "program", "task", "order", "solution", "solutions", "provide",
    }
)


# ── Text helpers ───────────────────────────────────────────────────────────────

def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _opportunity_text(opportunity: dict) -> str:
    """Flatten the citable requirement text out of a capture/RFI record."""
    opportunity = opportunity or {}
    parts: list[str] = []
    for key in ("title", "requirements", "description", "scope", "capabilities"):
        val = opportunity.get(key)
        if isinstance(val, (list, tuple)):
            parts.extend(str(v) for v in val)
        elif val:
            parts.append(str(val))
    return " ".join(parts)


def _contract_text(row: dict) -> str:
    parts = [
        row.get("title"),
        row.get("agency"),
        row.get("agency_hierarchy"),
        row.get("naics_code"),
        row.get("contract_type"),
        row.get("notes"),
    ]
    meta = row.get("metadata")
    if meta:
        try:
            md = json.loads(meta) if isinstance(meta, str) else meta
            if isinstance(md, dict):
                for k in ("capabilities", "keywords", "description", "scope"):
                    v = md.get(k)
                    if isinstance(v, (list, tuple)):
                        parts.extend(str(x) for x in v)
                    elif v:
                        parts.append(str(v))
        except (ValueError, TypeError):
            pass
    return " ".join(str(p) for p in parts if p)


# ── Similarity (provider abstraction + deterministic fallback) ─────────────────

def _embed_similarities(opp_text: str, contract_texts: list[str]) -> list[float] | None:
    """Cosine similarity of opportunity vs each contract via the shared
    embedding provider. Returns None if no live provider is available so the
    caller can fall back to the deterministic path (offline-safe)."""
    try:
        from tools.llm import get_embedding_provider

        provider = get_embedding_provider()
        if provider is None:
            return None
        opp_vec = provider.embed(opp_text)
        if not opp_vec:
            return None
        sims: list[float] = []
        for ct in contract_texts:
            vec = provider.embed(ct)
            sims.append(_cosine(opp_vec, vec) if vec else 0.0)
        return sims
    except Exception:
        # Any provider/import/network error -> deterministic fallback.
        return None


def _deterministic_similarities(opp_text: str, contract_texts: list[str]) -> list[float]:
    opp_tokens = _tokens(opp_text)
    return [_jaccard(opp_tokens, _tokens(ct)) for ct in contract_texts]


# ── DB read (read-only, table-absent tolerant) ─────────────────────────────────

def _fetch_completed_contracts(conn) -> list[dict]:
    cols = [
        "id", "contract_number", "title", "agency", "agency_hierarchy",
        "contract_type", "naics_code", "total_value", "cpars_rating_current",
        "pop_start", "pop_end", "status", "opportunity_id", "notes", "metadata",
    ]
    placeholders = ",".join("%s" for _ in _COMPLETED_STATUSES)
    sql = (
        f"SELECT {', '.join(cols)} FROM cpmp_contracts "
        f"WHERE status IN ({placeholders})"
    )
    try:
        rows = conn.execute(sql, _COMPLETED_STATUSES).fetchall()
    except Exception:
        return []
    out: list[dict] = []
    for r in rows:
        out.append({c: r[i] for i, c in enumerate(cols)})
    return out


def _fetch_latest_cpars(conn, contract_id: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT id, overall_rating, overall_score, period_end "
            "FROM cpmp_cpars_assessments WHERE contract_id = %s "
            "ORDER BY period_end DESC",
            (contract_id,),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {
        "id": row[0],
        "overall_rating": row[1],
        "overall_score": row[2],
        "period_end": row[3],
    }


def _fetch_outcome(conn, opportunity_id: str | None) -> str | None:
    if not opportunity_id:
        return None
    try:
        row = conn.execute(
            "SELECT outcome FROM pg_win_loss_records WHERE opportunity_id = %s "
            "ORDER BY created_at DESC",
            (opportunity_id,),
        ).fetchone()
    except Exception:
        return None
    return row[0] if row else None


# ── Public API ─────────────────────────────────────────────────────────────────

def suggest_references(
    opportunity: dict,
    *,
    top_k: int = 5,
    min_score: float = 0.0,
    conn=None,
) -> dict:
    """Return ranked past-performance suggestions for an opportunity.

    Args:
        opportunity: capture/RFI record. Recognised keys: ``requirements`` (or
            ``description`` / ``scope``), ``title``, ``capabilities`` (list),
            ``agency``, ``naics_code``.
        top_k: max suggestions to return.
        min_score: drop suggestions whose blended score is below this.
        conn: optional open DB connection (else a shared connection is opened).

    Returns::

        {
          "suggestions": [ { ...reference..., "citation": "[source: ...]" } ],
          "count": int,
          "method": "embedding" | "deterministic",
          "auto_inserted": False,   # invariant — nothing is ever auto-inserted
        }

    Every suggestion carries a ``[source: cpmp_contracts:<id>]`` citation whose
    ids are validated with the shared citation grounding module.
    """
    own_conn = False
    if conn is None:
        from tools.db.storage import get_connection

        conn = get_connection()
        own_conn = True
    try:
        contracts = _fetch_completed_contracts(conn)
        if not contracts:
            return {"suggestions": [], "count": 0, "method": "deterministic", "auto_inserted": False}

        opp_text = _opportunity_text(opportunity)
        opp_agency = (opportunity.get("agency") or "").strip().lower()
        opp_naics = (opportunity.get("naics_code") or "").strip()

        contract_texts = [_contract_text(c) for c in contracts]
        sims = _embed_similarities(opp_text, contract_texts)
        method = "embedding"
        if sims is None:
            sims = _deterministic_similarities(opp_text, contract_texts)
            method = "deterministic"

        scored: list[dict] = []
        allowed_sources: list[str] = []
        for contract, text_sim in zip(contracts, sims):
            cid = contract["id"]
            # Structured-match bonuses layered on top of text similarity.
            agency_match = bool(opp_agency) and opp_agency in (contract.get("agency") or "").lower()
            naics_match = bool(opp_naics) and opp_naics == (contract.get("naics_code") or "")
            blended = (
                0.70 * float(text_sim)
                + (0.20 if agency_match else 0.0)
                + (0.10 if naics_match else 0.0)
            )
            if blended < min_score:
                continue

            cpars = _fetch_latest_cpars(conn, cid)
            outcome = _fetch_outcome(conn, contract.get("opportunity_id"))

            source_tags = [f"cpmp_contracts:{cid}"]
            allowed_sources.append(f"cpmp_contracts:{cid}")
            if cpars:
                source_tags.append(f"cpmp_cpars_assessments:{cpars['id']}")
                allowed_sources.append(f"cpmp_cpars_assessments:{cpars['id']}")
            citation = "[source: " + ", ".join(source_tags) + "]"

            cpars_rating = (cpars or {}).get("overall_rating") or contract.get("cpars_rating_current")
            pop = " to ".join(
                x for x in (contract.get("pop_start"), contract.get("pop_end")) if x
            )
            summary = (
                f"{contract.get('title')} for {contract.get('agency')} "
                f"({contract.get('contract_type')}, NAICS {contract.get('naics_code')})"
                + (f", CPARS {cpars_rating}" if cpars_rating else "")
                + (f", outcome: {outcome}" if outcome else "")
                + f". {citation}"
            )

            scored.append(
                {
                    "contract_id": cid,
                    "contract_number": contract.get("contract_number"),
                    "title": contract.get("title"),
                    "agency": contract.get("agency"),
                    "naics_code": contract.get("naics_code"),
                    "contract_type": contract.get("contract_type"),
                    "total_value": contract.get("total_value"),
                    "period_of_performance": pop or None,
                    "cpars_rating": cpars_rating,
                    "cpars_score": (cpars or {}).get("overall_score"),
                    "outcome": outcome,
                    "similarity": round(float(text_sim), 4),
                    "score": round(blended, 4),
                    "agency_match": agency_match,
                    "naics_match": naics_match,
                    "citation": citation,
                    "sources": source_tags,
                    "summary": summary,
                }
            )

        scored.sort(key=lambda s: s["score"], reverse=True)
        top = scored[: max(top_k, 0)]
        for rank, s in enumerate(top, start=1):
            s["rank"] = rank

        # Grounding invariant: every surfaced suggestion must carry a valid,
        # non-hallucinated citation to a real CPMP record. Validated via the
        # shared module — not re-implemented here.
        allowed_set = set(allowed_sources)
        grounded: list[dict] = []
        for s in top:
            if not has_citations(s["summary"]):
                continue
            report = validate_citations(s["summary"], allowed_set)
            if report["hallucinated_citations"]:
                continue
            grounded.append(s)

        return {
            "suggestions": grounded,
            "count": len(grounded),
            "method": method,
            "auto_inserted": False,
        }
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Suggest past-performance references for an opportunity "
        "(suggestions only — never auto-inserted)."
    )
    parser.add_argument("--requirements", default="", help="Opportunity requirement text")
    parser.add_argument("--title", default="", help="Opportunity title")
    parser.add_argument("--agency", default="", help="Target agency")
    parser.add_argument("--naics", default="", help="Target NAICS code")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args(argv)

    opp = {
        "requirements": args.requirements,
        "title": args.title,
        "agency": args.agency,
        "naics_code": args.naics,
    }
    result = suggest_references(opp, top_k=args.top_k)
    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(f"Method: {result['method']}  Suggestions: {result['count']}")
        for s in result["suggestions"]:
            print(
                f"  #{s['rank']} [{s['score']}] {s['title']} — {s['agency']} "
                f"(CPARS {s.get('cpars_rating')})  {s['citation']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
