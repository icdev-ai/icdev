#!/usr/bin/env python3
# CUI // SP-CTI
"""WAR Retriever — domain-specific scoring profiles for Strategos.

Three profiles adjust base RAG retrieval scores for war-domain queries:

    ghost_track:      weights recency + geographic proximity
    iw_engine:        weights historical similarity + PMESII-PT overlap
    strategy_advisor: weights doctrine relevance

Usage:
    python tools/strategos/war_retriever.py \\
        --query "PLA ADIZ incursion" --profile ghost_track --json
    python tools/strategos/war_retriever.py \\
        --query "information operations Ukraine" --profile iw_engine --json
    python tools/strategos/war_retriever.py \\
        --query "joint maritime interdiction doctrine" --profile strategy_advisor --json
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.rag.retriever import RAGRetriever
from tools.rag.vector_store_provider import SearchResult

# ---------------------------------------------------------------------------
# Profile names
# ---------------------------------------------------------------------------

GHOST_TRACK = "ghost_track"
IW_ENGINE = "iw_engine"
STRATEGY_ADVISOR = "strategy_advisor"

_PROFILE_NAMES = (GHOST_TRACK, IW_ENGINE, STRATEGY_ADVISOR)

# PMESII-PT canonical dimension set
_PMESII_DIMS = frozenset((
    "political", "military", "economic", "social",
    "information", "infrastructure", "physical_environment", "time",
))

# Doctrine-domain keywords for STRATEGY_ADVISOR scoring
_DOCTRINE_KEYWORDS = frozenset((
    "doctrine", "field manual", "fm ", "jp ", "joint pub",
    "strategy", "campaign", "operational", "warfighting", "concept",
    "capstone", "keystone", "jcs", "cjcs", "mcdp", "adp ", "adrp",
    "historical case", "precedent", "playbook", "annex",
))


# ---------------------------------------------------------------------------
# RetrievalProfile
# ---------------------------------------------------------------------------

@dataclass
class RetrievalProfile:
    """Scoring weights and affinity config for a single WAR retrieval profile."""

    name: str
    # Component weights (need not sum to 1; normalised internally)
    semantic_weight: float = 1.0
    recency_weight: float = 0.0
    geo_weight: float = 0.0
    pmesii_weight: float = 0.0
    doctrine_weight: float = 0.0
    # Source type affinity
    preferred_source_types: List[str] = field(default_factory=list)
    source_boost: float = 0.20
    # Default result pool
    top_k: int = 10


# Built-in profiles
PROFILES: Dict[str, RetrievalProfile] = {
    GHOST_TRACK: RetrievalProfile(
        name=GHOST_TRACK,
        semantic_weight=0.20,
        recency_weight=0.50,
        geo_weight=0.30,
        preferred_source_types=["sg_raw_signals", "gdelt", "osint", "conflict_events"],
        source_boost=0.15,
        top_k=10,
    ),
    IW_ENGINE: RetrievalProfile(
        name=IW_ENGINE,
        semantic_weight=0.50,
        pmesii_weight=0.50,
        preferred_source_types=["sg_iw_effects", "cyber_ops", "stix", "conflict_events"],
        source_boost=0.10,
        top_k=10,
    ),
    STRATEGY_ADVISOR: RetrievalProfile(
        name=STRATEGY_ADVISOR,
        semantic_weight=0.20,
        doctrine_weight=0.80,
        preferred_source_types=["doctrine", "field_manual", "strategy_doc", "wargame", "historical_case"],
        source_boost=0.30,
        top_k=10,
    ),
}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _recency_score(metadata: Dict[str, Any], now: datetime) -> float:
    """Score 0-1 with exponential decay; half-life = 7 days.

    Falls back to 0.5 (neutral) when no timestamp is present.
    """
    raw = (
        metadata.get("created_at")
        or metadata.get("discovered_at")
        or metadata.get("updated_at")
    )
    if not raw:
        return 0.5

    try:
        if isinstance(raw, str):
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        elif isinstance(raw, (int, float)):
            ts = datetime.fromtimestamp(raw, tz=timezone.utc)
        else:
            return 0.5
        age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
        return math.exp(-age_days * math.log(2) / 7.0)
    except Exception:
        return 0.5


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (WGS-84 approximation)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _geo_score(
    metadata: Dict[str, Any],
    ref_lat: Optional[float],
    ref_lon: Optional[float],
    ref_aoi: str,
) -> float:
    """Score 0-1 for geographic proximity to a reference point or AOI.

    Priority order:
      1. Haversine distance if ref lat/lon + result lat/lon are available.
         Decay half-life = 500 km (0 km → 1.0, 500 km → 0.5).
      2. AOI string match against metadata aoi/region/country fields.
         Exact match → 1.0; partial overlap → 0.6.
      3. No usable geo anchor → 0.5 neutral.
    """
    if ref_lat is not None and ref_lon is not None:
        r_lat = metadata.get("lat") or metadata.get("latitude")
        r_lon = metadata.get("lon") or metadata.get("longitude")
        if r_lat is not None and r_lon is not None:
            try:
                dist_km = _haversine_km(
                    float(ref_lat), float(ref_lon), float(r_lat), float(r_lon)
                )
                return math.exp(-dist_km * math.log(2) / 500.0)
            except Exception:
                pass

    if ref_aoi:
        doc_aoi = str(
            metadata.get("aoi", "") or metadata.get("region", "") or metadata.get("country", "")
        ).lower()
        ref_lower = ref_aoi.lower()
        if doc_aoi:
            if doc_aoi == ref_lower:
                return 1.0
            if ref_lower in doc_aoi or doc_aoi in ref_lower:
                return 0.6

    return 0.5


def _pmesii_overlap_score(
    metadata: Dict[str, Any],
    dominant_indicators: List[str],
) -> float:
    """Score 0-1 based on Jaccard overlap of PMESII-PT dimension tags.

    Compares the document's tagged dimensions against the current WRI
    dominant indicators.  Falls back to content-keyword detection when
    explicit metadata tags are absent.

    Returns 0.5 when dominant_indicators is empty (no WRI context).
    """
    if not dominant_indicators:
        return 0.5

    # Extract document's declared PMESII tags
    raw = (
        metadata.get("pmesii_dims")
        or metadata.get("dimensions")
        or metadata.get("pmesii")
        or []
    )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [d.strip() for d in raw.split(",") if d.strip()]

    doc_dims = frozenset(str(d).lower() for d in raw) & _PMESII_DIMS

    # Fallback: keyword scan of content field stored in metadata
    if not doc_dims:
        content_lower = str(metadata.get("content", "")).lower()
        doc_dims = frozenset(d for d in _PMESII_DIMS if d in content_lower)

    dominant = frozenset(str(d).lower() for d in dominant_indicators) & _PMESII_DIMS

    if not doc_dims or not dominant:
        return 0.3  # low score when no dimension tags can be matched

    union = doc_dims | dominant
    return len(doc_dims & dominant) / len(union)


def _doctrine_relevance_score(
    content: str,
    metadata: Dict[str, Any],
    source_type: str,
) -> float:
    """Score 0-1 based on doctrine keyword density.

    Source type exact match against known doctrine types → strong prior (0.9).
    Title hits count double; saturates at 5 total keyword hits → 1.0.
    """
    if source_type in ("doctrine", "field_manual", "strategy_doc", "wargame", "historical_case"):
        return 0.9

    content_lower = content.lower()
    hits = sum(1 for kw in _DOCTRINE_KEYWORDS if kw in content_lower)

    title_lower = str(
        metadata.get("title", "") or metadata.get("name", "")
    ).lower()
    hits += sum(2 for kw in _DOCTRINE_KEYWORDS if kw in title_lower)

    return min(1.0, hits / 5.0)


def _load_dominant_indicators() -> List[str]:
    """Return dominant WRI indicators from the latest sg_wri_assessments row."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT dominant_indicators FROM sg_wri_assessments"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            raw = row[0]
            return json.loads(raw) if isinstance(raw, str) else list(raw)
    except Exception:
        pass
    return []


def _normalised_weights(profile: RetrievalProfile) -> Dict[str, float]:
    """Return per-component weights normalised to sum to 1."""
    raw = {
        "semantic": profile.semantic_weight,
        "recency": profile.recency_weight,
        "geo": profile.geo_weight,
        "pmesii": profile.pmesii_weight,
        "doctrine": profile.doctrine_weight,
    }
    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


# ---------------------------------------------------------------------------
# WARRetriever
# ---------------------------------------------------------------------------

class WARRetriever:
    """Domain-specific RAG retriever for Strategos war-analysis queries.

    Wraps RAGRetriever, retrieves a larger candidate pool (3× top_k),
    then applies profile-specific scoring to produce the final ranking.
    """

    def __init__(
        self,
        profile: str = GHOST_TRACK,
        tenant_id: str = "",
        config: Optional[dict] = None,
    ):
        if profile not in PROFILES:
            raise ValueError(f"Unknown profile '{profile}'. Valid: {list(PROFILES)}")
        self._profile = PROFILES[profile]
        self._base = RAGRetriever(tenant_id=tenant_id, config=config)

    @property
    def profile(self) -> RetrievalProfile:
        return self._profile

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        source_types: Optional[List[str]] = None,
        project_id: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Retrieve and re-score results using the active profile.

        Args:
            query: Natural language query.
            top_k: Override profile default result count.
            source_types: Optional source type filter passed to base retriever.
            project_id: Optional project scope.
            context: Profile-specific context dict.
                ghost_track:
                    ref_lat (float): Reference latitude for geo proximity.
                    ref_lon (float): Reference longitude for geo proximity.
                    aoi (str): Area-of-interest label (fallback when no lat/lon).
                iw_engine:
                    pmesii_dominant (list[str]): Override DB lookup of dominant
                        WRI indicators (e.g. ["military","information"]).
                strategy_advisor:
                    (no extra keys required; doctrine scoring is self-contained)

        Returns:
            List[SearchResult] sorted by final_score descending.
            Profile component scores stored in result.metadata["_scores"].
        """
        profile = self._profile
        final_k = top_k or profile.top_k
        ctx = context or {}

        base_results = self._base.search(
            query=query,
            top_k=max(final_k * 3, 30),
            source_types=source_types,
            project_id=project_id,
            rerank=False,  # profile scoring replaces re-ranking
        )

        if not base_results:
            return []

        weights = _normalised_weights(profile)

        if profile.name == GHOST_TRACK:
            scored = self._score_ghost_track(base_results, ctx, profile, weights)
        elif profile.name == IW_ENGINE:
            scored = self._score_iw_engine(base_results, ctx, profile, weights)
        else:  # STRATEGY_ADVISOR
            scored = self._score_strategy_advisor(base_results, ctx, profile, weights)

        scored.sort(key=lambda r: r.final_score, reverse=True)
        return scored[:final_k]

    # ------------------------------------------------------------------
    # Profile scorers
    # ------------------------------------------------------------------

    def _score_ghost_track(
        self,
        results: List[SearchResult],
        ctx: Dict[str, Any],
        profile: RetrievalProfile,
        weights: Dict[str, float],
    ) -> List[SearchResult]:
        """ghost_track: recency × geographic proximity."""
        ref_lat = ctx.get("ref_lat")
        ref_lon = ctx.get("ref_lon")
        ref_aoi = str(ctx.get("aoi", ""))
        now = datetime.now(timezone.utc)
        sw, rw, gw = weights["semantic"], weights["recency"], weights["geo"]

        for r in results:
            sem_s = r.final_score
            rec_s = _recency_score(r.metadata, now)
            geo_s = _geo_score(r.metadata, ref_lat, ref_lon, ref_aoi)

            ps = sw * sem_s + rw * rec_s + gw * geo_s
            if r.source_type in profile.preferred_source_types:
                ps = min(1.0, ps + profile.source_boost)

            r.metadata["_profile"] = GHOST_TRACK
            r.metadata["_scores"] = {
                "semantic": round(sem_s, 4),
                "recency": round(rec_s, 4),
                "geo": round(geo_s, 4),
            }
            r.final_score = round(ps, 4)

        return results

    def _score_iw_engine(
        self,
        results: List[SearchResult],
        ctx: Dict[str, Any],
        profile: RetrievalProfile,
        weights: Dict[str, float],
    ) -> List[SearchResult]:
        """iw_engine: historical similarity × PMESII-PT overlap."""
        dominant = ctx.get("pmesii_dominant") or _load_dominant_indicators()
        sw, pw = weights["semantic"], weights["pmesii"]

        for r in results:
            sem_s = r.final_score
            pmesii_s = _pmesii_overlap_score(r.metadata, dominant)

            ps = sw * sem_s + pw * pmesii_s
            if r.source_type in profile.preferred_source_types:
                ps = min(1.0, ps + profile.source_boost)

            r.metadata["_profile"] = IW_ENGINE
            r.metadata["_scores"] = {
                "semantic": round(sem_s, 4),
                "pmesii_overlap": round(pmesii_s, 4),
                "dominant_indicators": list(dominant),
            }
            r.final_score = round(ps, 4)

        return results

    def _score_strategy_advisor(
        self,
        results: List[SearchResult],
        ctx: Dict[str, Any],
        profile: RetrievalProfile,
        weights: Dict[str, float],
    ) -> List[SearchResult]:
        """strategy_advisor: doctrine relevance."""
        sw, dw = weights["semantic"], weights["doctrine"]

        for r in results:
            sem_s = r.final_score
            doc_s = _doctrine_relevance_score(r.content, r.metadata, r.source_type)

            ps = sw * sem_s + dw * doc_s
            if r.source_type in profile.preferred_source_types:
                ps = min(1.0, ps + profile.source_boost)

            r.metadata["_profile"] = STRATEGY_ADVISOR
            r.metadata["_scores"] = {
                "semantic": round(sem_s, 4),
                "doctrine": round(doc_s, 4),
            }
            r.final_score = round(ps, 4)

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="WAR Retriever — domain-specific scoring profiles")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument(
        "--profile",
        choices=list(_PROFILE_NAMES),
        default=GHOST_TRACK,
        help="Scoring profile",
    )
    parser.add_argument("--top-k", type=int, default=None, help="Number of results")
    parser.add_argument("--source-types", help="Comma-separated source type filter")
    parser.add_argument("--project-id", default="", help="Project ID filter")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    # ghost_track context
    parser.add_argument("--ref-lat", type=float, help="Reference latitude (ghost_track)")
    parser.add_argument("--ref-lon", type=float, help="Reference longitude (ghost_track)")
    parser.add_argument("--aoi", default="", help="Area-of-interest label (ghost_track)")
    # iw_engine context
    parser.add_argument(
        "--pmesii-dominant",
        help="Comma-separated dominant PMESII-PT dims (iw_engine)",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    source_types = args.source_types.split(",") if args.source_types else None

    context: Dict[str, Any] = {}
    if args.profile == GHOST_TRACK:
        if args.ref_lat is not None:
            context["ref_lat"] = args.ref_lat
        if args.ref_lon is not None:
            context["ref_lon"] = args.ref_lon
        if args.aoi:
            context["aoi"] = args.aoi
    elif args.profile == IW_ENGINE and args.pmesii_dominant:
        context["pmesii_dominant"] = [d.strip() for d in args.pmesii_dominant.split(",")]

    retriever = WARRetriever(
        profile=args.profile,
        tenant_id=args.tenant_id,
    )
    results = retriever.search(
        query=args.query,
        top_k=args.top_k,
        source_types=source_types,
        project_id=args.project_id,
        context=context,
    )

    if args.json_output:
        print(json.dumps({
            "classification": "CUI // SP-CTI",
            "query": args.query,
            "profile": args.profile,
            "results_count": len(results),
            "results": [r.to_dict() for r in results],
        }, indent=2))
    else:
        if not results:
            print("No results found.")
            return
        for r in results:
            scores = r.metadata.get("_scores", {})
            print(
                f"[{r.source_type}:{r.source_id}]"
                f" (final:{r.final_score:.3f} | {scores})"
                f" {r.content[:100]}..."
            )


if __name__ == "__main__":
    main()
