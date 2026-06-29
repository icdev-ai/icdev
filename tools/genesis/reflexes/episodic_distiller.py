#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Episodic Distiller Reflex — episodic → semantic memory distillation.

After N new episodic events accumulate, a cheaper model clusters them by
embedding similarity and distills each cluster into 1-3 timeless semantic
facts.  Marks source entries distilled=1 so they are not re-processed.

YELLOW tier (reads episodic events; writes semantic facts; LLM tokens used).
Cadence: every 6 hours (configurable in genesis_config.yaml).
"""

IMPLEMENTATION_STATUS = "full"

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fetch_undistilled_episodic(limit: int) -> list[dict]:
    """Return up to `limit` episodic events not yet marked distilled."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, content, type, importance, created_at "
            "FROM memory_entries "
            "WHERE tier = %s AND (distilled = 0 OR distilled IS NULL) "
            "ORDER BY created_at ASC "
            "LIMIT %s",
            ("episodic", limit),
        ).fetchall()
        result = []
        for r in rows:
            if isinstance(r, dict):
                result.append(r)
            else:
                result.append({
                    "id": r[0], "content": r[1], "type": r[2],
                    "importance": r[3], "created_at": r[4],
                })
        return result
    except Exception as exc:
        print(f"  EpisodicDistiller: fetch failed: {exc}")
        return []
    finally:
        conn.close()


def _mark_distilled(ids: list[int]) -> None:
    """Mark memory_entries rows as distilled=1."""
    if not ids:
        return
    conn = get_connection()
    try:
        placeholders = ", ".join(["%s"] * len(ids))
        conn.execute(
            f"UPDATE memory_entries SET distilled = 1 WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
    except Exception as exc:
        print(f"  EpisodicDistiller: mark_distilled failed: {exc}")
    finally:
        conn.close()


def _save_semantic_fact(content: str, importance: int = 6, source: str = "distiller") -> dict:
    """Write a distilled fact to memory_entries as a semantic entry."""
    from tools.memory.memory_write import write_to_db
    return write_to_db(
        content=content,
        entry_type="fact",
        importance=importance,
        source=source,
        tier="semantic",
    )


# ---------------------------------------------------------------------------
# Clustering — cosine similarity on TF-IDF bag of words (stdlib only)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    import re
    return re.findall(r"[a-zA-Z]{4,}", text.lower())


def _tfidf_vector(tokens: list[str], vocab: list[str]) -> list[float]:
    from math import log
    freq: dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    n = len(tokens) or 1
    return [freq.get(w, 0) / n * (1 + log(1 + freq.get(w, 0))) for w in vocab]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _cluster_entries(entries: list[dict], threshold: float = 0.35) -> list[list[dict]]:
    """Greedy single-linkage clustering by cosine similarity of bag-of-words."""
    if not entries:
        return []
    tokenized = [_tokenize(e["content"]) for e in entries]
    vocab = sorted(set(t for tokens in tokenized for t in tokens))
    if not vocab:
        return [[e] for e in entries]
    vectors = [_tfidf_vector(tokens, vocab) for tokens in tokenized]

    clusters: list[list[int]] = []
    assigned = [False] * len(entries)
    for i in range(len(entries)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(entries)):
            if assigned[j]:
                continue
            sim = _cosine(vectors[i], vectors[j])
            if sim >= threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    return [[entries[i] for i in cl] for cl in clusters]


# ---------------------------------------------------------------------------
# LLM distillation
# ---------------------------------------------------------------------------


def _distill_cluster_with_llm(
    cluster: list[dict], model_function: str, max_facts: int = 3
) -> list[str]:
    """Call LLM router to distill a cluster into up to max_facts semantic facts.

    Returns list of fact strings, or [] on any error (safe to skip).
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.router import LLMRequest

        snippets = "\n".join(
            f"- {e['content'][:300]}" for e in cluster[:15]
        )
        prompt = (
            f"You are a knowledge distillation assistant.\n"
            f"The following are {len(cluster)} episodic memory events from an AI system:\n\n"
            f"{snippets}\n\n"
            f"Distill these into {max_facts} concise, timeless, actionable facts (semantic memory). "
            f"Each fact should be a single sentence that remains true regardless of when it is read. "
            f"Output ONLY a JSON array of strings, no explanation. "
            f"Example: [\"ICDEV uses PostgreSQL as its primary backend.\", \"ACE coworkers save sessions to agent_loop_sessions.\"]"
        )
        router = LLMRouter()
        request = LLMRequest(
            prompt=prompt,
            system_prompt="You are a concise knowledge distillation assistant. Return only valid JSON.",
            max_tokens=512,
        )
        response = router.invoke(model_function, request)
        raw = (response.content or "").strip()
        # extract JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            facts = json.loads(raw[start : end + 1])
            return [f.strip() for f in facts if isinstance(f, str) and f.strip()]
    except Exception as exc:
        print(f"  EpisodicDistiller: LLM distillation failed: {exc}")
    return []


def _distill_cluster_heuristic(cluster: list[dict], max_facts: int = 2) -> list[str]:
    """Fallback heuristic: pick the highest-importance entries as pseudo-facts."""
    sorted_entries = sorted(cluster, key=lambda e: e.get("importance", 5), reverse=True)
    facts = []
    for entry in sorted_entries[:max_facts]:
        content = entry["content"].strip()
        if content and len(content) > 20:
            facts.append(content[:400])
    return facts


# ---------------------------------------------------------------------------
# Main reflex entrypoint
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], trust: Any) -> dict[str, Any]:
    """Execute the Episodic Distiller Reflex.

    Config keys (genesis_config.yaml → episodic_distiller):
        trigger_count (int, default 20): min undistilled entries before running
        batch_size (int, default 50): max entries to process per run
        cluster_threshold (float, default 0.35): cosine similarity for clustering
        max_facts_per_cluster (int, default 3): LLM cap per cluster
        model_function (str, default 'summarization'): LLM router function name
        use_llm (bool, default true): if false, use heuristic fallback (no tokens)
    """
    trigger_count = int(config.get("trigger_count", 20))
    batch_size = int(config.get("batch_size", 50))
    cluster_threshold = float(config.get("cluster_threshold", 0.35))
    max_facts = int(config.get("max_facts_per_cluster", 3))
    model_function = config.get("model_function", "summarization")
    use_llm = bool(config.get("use_llm", True))

    # Check whether there are enough undistilled entries to proceed
    entries = _fetch_undistilled_episodic(batch_size)
    if len(entries) < trigger_count:
        print(
            f"  EpisodicDistiller: {len(entries)} undistilled entries "
            f"(threshold: {trigger_count}) — skipping"
        )
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {
                "skipped": True,
                "reason": f"below_threshold ({len(entries)}/{trigger_count})",
                "undistilled_count": len(entries),
            },
        }

    print(f"  EpisodicDistiller: processing {len(entries)} episodic entries")
    clusters = _cluster_entries(entries, threshold=cluster_threshold)
    print(f"  EpisodicDistiller: formed {len(clusters)} clusters")

    facts_written = 0
    entries_distilled = 0
    source_ids: list[int] = []

    for i, cluster in enumerate(clusters):
        cluster_ids = [e["id"] for e in cluster]
        if use_llm:
            facts = _distill_cluster_with_llm(cluster, model_function, max_facts)
        else:
            facts = _distill_cluster_heuristic(cluster, max_facts)

        for fact in facts:
            try:
                importance = min(
                    10, 4 + round(sum(e.get("importance", 5) for e in cluster) / len(cluster))
                )
                result = _save_semantic_fact(fact, importance=importance)
                if result.get("status") in ("inserted", "duplicate_merged"):
                    facts_written += 1
            except Exception as exc:
                print(f"  EpisodicDistiller: save fact failed: {exc}")

        source_ids.extend(cluster_ids)
        entries_distilled += len(cluster)

    _mark_distilled(source_ids)
    print(
        f"  EpisodicDistiller: wrote {facts_written} semantic facts, "
        f"marked {entries_distilled} episodic entries distilled"
    )

    return {
        "success": True,
        "metric_value": float(facts_written),
        "details": {
            "entries_processed": entries_distilled,
            "clusters": len(clusters),
            "semantic_facts_written": facts_written,
            "use_llm": use_llm,
        },
    }
