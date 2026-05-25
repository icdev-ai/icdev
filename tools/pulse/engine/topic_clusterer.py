"""Topic clustering engine for ICDEV™ Pulse.

Groups related pain points from research_cache into coherent article themes
using TF-IDF-like keyword extraction (stdlib only) and keyword overlap scoring.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import math
import re
import string
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from tools.pulse.config import SEARCH_SOURCES
from tools.pulse.db import get_row, insert_row, query_rows, update_row

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stopwords — common English words that carry no topical signal
# ---------------------------------------------------------------------------
STOPWORDS: set[str] = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "aren't",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "can't",
    "cannot",
    "could",
    "couldn't",
    "did",
    "didn't",
    "do",
    "does",
    "doesn't",
    "doing",
    "don't",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "get",
    "got",
    "had",
    "hadn't",
    "has",
    "hasn't",
    "have",
    "haven't",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "isn't",
    "it",
    "it's",
    "its",
    "itself",
    "just",
    "let",
    "like",
    "ll",
    "may",
    "me",
    "might",
    "more",
    "most",
    "mustn't",
    "my",
    "myself",
    "need",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "re",
    "s",
    "same",
    "shan't",
    "she",
    "should",
    "shouldn't",
    "so",
    "some",
    "such",
    "t",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "us",
    "ve",
    "very",
    "was",
    "wasn't",
    "we",
    "were",
    "weren't",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "won't",
    "would",
    "wouldn't",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "also",
    "already",
    "always",
    "another",
    "back",
    "come",
    "even",
    "every",
    "first",
    "go",
    "going",
    "good",
    "great",
    "however",
    "keep",
    "know",
    "last",
    "long",
    "look",
    "made",
    "make",
    "many",
    "much",
    "never",
    "new",
    "next",
    "one",
    "people",
    "really",
    "right",
    "say",
    "see",
    "since",
    "still",
    "take",
    "tell",
    "thing",
    "think",
    "time",
    "two",
    "use",
    "used",
    "using",
    "want",
    "way",
    "well",
    "work",
    "year",
}


# ---------------------------------------------------------------------------
# Keyword extraction (TF-IDF-like, stdlib only)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into tokens."""
    text = text.lower()
    text = re.sub(f"[{re.escape(string.punctuation)}]", " ", text)
    return [w for w in text.split() if len(w) > 2 and w not in STOPWORDS]


def _term_frequency(tokens: list[str]) -> dict[str, float]:
    """Compute normalized term frequency for a token list."""
    counts = Counter(tokens)
    if not counts:
        return {}
    max_count = max(counts.values())
    return {term: count / max_count for term, count in counts.items()}


def _extract_keywords(text: str, top_n: int = 15) -> list[tuple[str, float]]:
    """Extract top-N keywords from *text* ranked by term frequency.

    Returns list of (keyword, score) tuples sorted descending by score.
    """
    tokens = _tokenize(text)
    tf = _term_frequency(tokens)
    ranked = sorted(tf.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[:top_n]


def _build_idf(documents: list[str]) -> dict[str, float]:
    """Build inverse-document-frequency map across all *documents*."""
    n_docs = len(documents)
    if n_docs == 0:
        return {}
    doc_freq: Counter[str] = Counter()
    for doc in documents:
        unique_tokens = set(_tokenize(doc))
        for token in unique_tokens:
            doc_freq[token] += 1
    return {term: math.log((1 + n_docs) / (1 + df)) + 1 for term, df in doc_freq.items()}


def _tfidf_keywords(text: str, idf: dict[str, float], top_n: int = 15) -> list[tuple[str, float]]:
    """Extract keywords using TF * IDF scoring."""
    tokens = _tokenize(text)
    tf = _term_frequency(tokens)
    scores = {term: tf_val * idf.get(term, 1.0) for term, tf_val in tf.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[:top_n]


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def _keyword_overlap_score(kw_a: list[tuple[str, float]], kw_b: list[tuple[str, float]]) -> float:
    """Compute similarity between two keyword lists via weighted overlap.

    Returns a score in [0, 1].
    """
    set_a = {k for k, _ in kw_a}
    set_b = {k for k, _ in kw_b}
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    if not intersection:
        return 0.0
    # Weight by average score of shared keywords
    scores_a = dict(kw_a)
    scores_b = dict(kw_b)
    weighted = sum(scores_a.get(k, 0) + scores_b.get(k, 0) for k in intersection)
    max_possible = sum(v for _, v in kw_a) + sum(v for _, v in kw_b)
    if max_possible == 0:
        return 0.0
    return min(weighted / max_possible, 1.0)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _result_text(result: dict) -> str:
    """Combine searchable text fields from a research_cache row."""
    parts = [
        result.get("query", ""),
        result.get("title", ""),
        result.get("snippet", ""),
        result.get("pain_point_category", ""),
    ]
    return " ".join(p for p in parts if p)


def _result_full_text(result: dict) -> str:
    """Full text including body for deeper keyword extraction."""
    parts = [
        _result_text(result),
        result.get("full_text", "") or "",
    ]
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _try_absorb_candidates(
    seed_idx: int,
    n: int,
    assigned: list[bool],
    kw_per_result: list[list[tuple[str, float]]],
    similarity_threshold: float,
) -> list[int]:
    """Grow a cluster from seed_idx by absorbing similar unassigned candidates."""
    cluster_indices = [seed_idx]
    assigned[seed_idx] = True

    for candidate_idx in range(seed_idx + 1, n):
        if assigned[candidate_idx]:
            continue
        max_sim = max(_keyword_overlap_score(kw_per_result[m], kw_per_result[candidate_idx]) for m in cluster_indices)
        if max_sim >= similarity_threshold:
            cluster_indices.append(candidate_idx)
            assigned[candidate_idx] = True

    return cluster_indices


def _collect_remaining(
    n: int,
    assigned: list[bool],
    research_results: list[dict],
    kw_per_result: list[list[tuple[str, float]]],
    idf: dict[str, float],
    min_cluster_size: int,
    clusters: list[dict],
) -> None:
    """Group remaining unassigned results into a catch-all cluster if big enough."""
    remaining = [i for i in range(n) if not assigned[i]]
    if len(remaining) >= min_cluster_size:
        members = [research_results[i] for i in remaining]
        cluster = _build_cluster(members, kw_per_result, remaining, idf)
        cluster["name"] = "Miscellaneous Topics"
        clusters.append(cluster)
    elif remaining:
        logger.info("Discarding %d research results that did not meet min_cluster_size.", len(remaining))


def cluster_research(
    research_results: list[dict],
    min_cluster_size: int = 3,
) -> list[dict]:
    """Group *research_results* into topic clusters.

    Uses TF-IDF keyword extraction across the corpus, then greedy
    agglomerative clustering based on keyword overlap scoring.

    Returns a list of cluster dicts (unsorted).
    """
    if not research_results:
        logger.info("No research results to cluster.")
        return []

    logger.info("Clustering %d research results (min_cluster_size=%d).", len(research_results), min_cluster_size)

    corpus = [_result_full_text(r) for r in research_results]
    idf = _build_idf(corpus)

    kw_per_result: list[list[tuple[str, float]]] = [
        _tfidf_keywords(_result_full_text(r), idf, top_n=15) for r in research_results
    ]

    n = len(research_results)
    assigned = [False] * n
    clusters: list[dict] = []
    similarity_threshold = 0.08

    for seed_idx in range(n):
        if assigned[seed_idx]:
            continue

        cluster_indices = _try_absorb_candidates(seed_idx, n, assigned, kw_per_result, similarity_threshold)

        if len(cluster_indices) < min_cluster_size:
            for idx in cluster_indices:
                assigned[idx] = False
            continue

        members = [research_results[i] for i in cluster_indices]
        cluster = _build_cluster(members, kw_per_result, cluster_indices, idf)
        clusters.append(cluster)

    _collect_remaining(n, assigned, research_results, kw_per_result, idf, min_cluster_size, clusters)

    logger.info("Created %d topic clusters.", len(clusters))
    return clusters


def _build_cluster(
    members: list[dict],
    kw_per_result: list[list[tuple[str, float]]],
    indices: list[int],
    idf: dict[str, float],
) -> dict:
    """Build a cluster dict from its member research results."""
    # Aggregate keywords to name the cluster
    combined_text = " ".join(_result_full_text(m) for m in members)
    top_kw = _tfidf_keywords(combined_text, idf, top_n=5)
    cluster_name = " + ".join(kw for kw, _ in top_kw[:3]).title() if top_kw else "Unnamed Cluster"

    # Extract pain points
    pain_points = []
    for m in members:
        category = m.get("pain_point_category", "")
        snippet = m.get("snippet", "")
        if category and category not in pain_points:
            pain_points.append(category)
        elif snippet:
            short = snippet[:120].strip()
            if short and short not in pain_points:
                pain_points.append(short)

    research_ids = [m.get("id", "") for m in members if m.get("id")]

    # Compute priority score
    priority = _compute_priority(members)

    # Description from top keywords
    kw_labels = [kw for kw, _ in top_kw[:5]]
    description = f"Articles covering: {', '.join(kw_labels)}." if kw_labels else ""

    cluster_id = f"tc-{uuid4().hex[:12]}"

    return {
        "id": cluster_id,
        "name": cluster_name,
        "description": description,
        "pain_points": pain_points,
        "research_ids": research_ids,
        "priority_score": round(priority, 4),
        "used_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------


def _compute_priority(members: list[dict]) -> float:
    """Score cluster priority in [0, 1].

    Factors (weights):
        - average relevance (0.3)
        - number of sources  (0.3)
        - recency            (0.2)
        - platform diversity (0.2)
    """
    if not members:
        return 0.0

    # --- Average relevance (0-1) ---
    relevance_scores = [float(m.get("relevance_score") or 0.0) for m in members]
    avg_relevance = sum(relevance_scores) / len(relevance_scores)
    # Clamp
    avg_relevance = max(0.0, min(avg_relevance, 1.0))

    # --- Number of sources (log-scaled, 1 source=0, 20+=1) ---
    n_sources = len(members)
    source_score = min(math.log(1 + n_sources) / math.log(21), 1.0)

    # --- Recency (how recent are the results, 0=old, 1=today) ---
    now = datetime.now(timezone.utc)
    max_age_days = 90  # results older than 90 days score 0
    ages: list[float] = []
    for m in members:
        fetched = m.get("fetched_at", "")
        if fetched:
            try:
                dt = datetime.fromisoformat(fetched)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_days = (now - dt).total_seconds() / 86400
                ages.append(max(0.0, 1.0 - age_days / max_age_days))
            except (ValueError, TypeError):
                ages.append(0.5)
        else:
            ages.append(0.5)
    recency_score = sum(ages) / len(ages) if ages else 0.5

    # --- Platform diversity (unique sources / total known platforms) ---
    platforms = {m.get("source", "") for m in members if m.get("source")}
    total_platforms = max(len(SEARCH_SOURCES), 1)
    diversity_score = min(len(platforms) / total_platforms, 1.0)

    # Weighted sum
    priority = 0.3 * avg_relevance + 0.3 * source_score + 0.2 * recency_score + 0.2 * diversity_score

    return max(0.0, min(priority, 1.0))


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def rank_clusters(clusters: list[dict]) -> list[dict]:
    """Rank clusters by priority_score descending.

    Clusters with higher used_count are deprioritized so fresh topics
    surface first.
    """

    def sort_key(c: dict) -> float:
        base = float(c.get("priority_score", 0.0))
        used = int(c.get("used_count", 0))
        # Decay priority by 20 % per usage
        decay = 0.8**used
        return base * decay

    ranked = sorted(clusters, key=sort_key, reverse=True)
    logger.info("Ranked %d clusters.", len(ranked))
    return ranked


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def save_clusters(clusters: list[dict]) -> int:
    """Persist a list of cluster dicts to the topic_clusters table.

    Returns the number of rows inserted.
    """
    count = 0
    for cluster in clusters:
        data = {
            "id": cluster["id"],
            "name": cluster["name"],
            "description": cluster.get("description", ""),
            "pain_points": cluster.get("pain_points", []),
            "research_ids": cluster.get("research_ids", []),
            "priority_score": cluster.get("priority_score", 0.5),
            "used_count": cluster.get("used_count", 0),
            "created_at": cluster.get("created_at", datetime.now(timezone.utc).isoformat()),
        }
        insert_row("topic_clusters", data)
        count += 1
        logger.debug("Saved cluster %s: %s", cluster["id"], cluster["name"])
    logger.info("Saved %d clusters to topic_clusters.", count)
    return count


def load_clusters() -> list[dict]:
    """Load all clusters from the database, deserializing JSON fields."""
    rows = query_rows("topic_clusters", limit=500)
    clusters = []
    for row in rows:
        cluster = dict(row)
        for field in ("pain_points", "research_ids"):
            val = cluster.get(field)
            if isinstance(val, str):
                try:
                    cluster[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    cluster[field] = []
        clusters.append(cluster)
    return clusters


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_next_topic() -> dict | None:
    """Return the highest-priority unused cluster for the next blog post.

    An *unused* cluster has ``used_count == 0``.  If all clusters have been
    used at least once, the least-used cluster is returned instead.
    Also checks against existing post titles to avoid thematic overlap.

    Returns ``None`` if no clusters exist.
    """
    clusters = load_clusters()
    if not clusters:
        logger.info("No topic clusters available.")
        return None

    ranked = rank_clusters(clusters)

    # Load existing titles for overlap detection
    existing_title_words: set[str] = set()
    try:
        from tools.pulse.db import get_existing_titles

        for title in get_existing_titles():
            existing_title_words.update(w.lower() for w in title.split() if len(w) > 4)
    except Exception:
        pass

    def _has_title_overlap(cluster_name: str) -> bool:
        """Check if cluster name significantly overlaps with existing titles."""
        words = {w.lower() for w in cluster_name.split() if len(w) > 4}
        if not words or not existing_title_words:
            return False
        overlap = words & existing_title_words
        return len(overlap) / len(words) > 0.6  # >60% word overlap

    # Prefer unused AND non-overlapping
    for c in ranked:
        if int(c.get("used_count", 0)) == 0 and not _has_title_overlap(c["name"]):
            logger.info("Next topic: %s (unused, no overlap, priority=%.4f)", c["name"], c["priority_score"])
            return c

    # Fallback: unused but possibly overlapping
    for c in ranked:
        if int(c.get("used_count", 0)) == 0:
            logger.info("Next topic: %s (unused, priority=%.4f)", c["name"], c["priority_score"])
            return c

    # Fallback: least used (rank_clusters already applies usage decay)
    best = ranked[0]
    logger.info(
        "All clusters used; recycling: %s (used=%d, priority=%.4f)",
        best["name"],
        best.get("used_count", 0),
        best["priority_score"],
    )
    return best


def mark_cluster_used(cluster_id: str) -> None:
    """Increment the used_count for a cluster."""
    row = get_row("topic_clusters", cluster_id)
    if row is None:
        logger.warning("Cluster %s not found; cannot mark as used.", cluster_id)
        return
    new_count = int(row.get("used_count", 0)) + 1
    update_row("topic_clusters", cluster_id, {"used_count": new_count})
    logger.info("Cluster %s used_count incremented to %d.", cluster_id, new_count)
