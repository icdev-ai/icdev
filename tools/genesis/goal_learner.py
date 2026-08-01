#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Genesis Goal Learner — Self-Improving Goals from Experience (D-GEN-GL-1).

When an agent solves a novel problem not covered by any existing goal, this
reflex auto-generates a new FORGE goal file, assigns a quality score, and
tracks version history.  The generated goal is staged for human review via
the GKP Promoter — it is never written to goals/ without approval.

Novelty detection works by scanning genesis_audit and kanban task completion
records for problem domains not matched by existing goal keywords.  A simple
TF-IDF-style coverage check against goals/manifest.md determines novelty.

Usage:
    python tools/genesis/goal_learner.py --scan --json
    python tools/genesis/goal_learner.py --scan --write --json     # also write goal files
    python tools/genesis/goal_learner.py --list --json             # list generated goals
    python tools/genesis/goal_learner.py --stats --json
    python tools/genesis/goal_learner.py --review <goal_id> --json
    python tools/genesis/goal_learner.py --approve <goal_id> --json
    python tools/genesis/goal_learner.py --reject <goal_id> --reason "..." --json
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.genesis.goal_learner")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LEARNER_VERSION = "1.0"
GOALS_DIR = BASE_DIR / "goals"
SUGGESTED_GOALS_DIR = BASE_DIR / "data" / "genesis" / "suggested_goals"

# Minimum novelty score (0–1) for goal generation
DEFAULT_NOVELTY_THRESHOLD = 0.6
MAX_GOALS_PER_RUN = 3

# Quality scoring weights — overridable via args/genesis_config.yaml
# reflexes.goal_learner.quality_weights
QUALITY_WEIGHTS = {
    "novelty_score": 0.40,
    "evidence_count": 0.25,   # Number of audit events supporting this goal
    "domain_clarity": 0.20,   # How well-defined the domain is
    "tool_coverage": 0.15,    # How many tools the goal references
}

# Fallback defaults — used when genesis_config.yaml is absent or a key is missing.
# All of these are overridable from args/genesis_config.yaml reflexes.goal_learner.*
_REFLEX_DEFAULTS: Dict[str, Any] = {
    "novelty_threshold": DEFAULT_NOVELTY_THRESHOLD,
    "max_goals_per_run": MAX_GOALS_PER_RUN,
    "min_evidence_count": 2,            # Min audit/kanban events a domain must have to qualify
    "lookback_days": 7,
    "write_files": True,
    "top_terms_count": 60,
    "max_keywords_per_domain": 30,
    "max_audit_events": 500,
    "max_kanban_completions": 200,
    "evidence_count_normalizer": 4.0,   # N events → 1.0; (count-1)/N
    "domain_clarity_divisor": 10.0,     # avg keyword length / N → [0,1]
    "tool_coverage_placeholder": 0.5,   # static until tool-analysis is implemented
    "quality_weights": QUALITY_WEIGHTS,
    "anomaly_detection": {
        "min_samples": 5,
        "z_score": 2.0,
    },
}

# Goal status values
STATUS_SUGGESTED = "suggested"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"

# Stopwords for novelty comparison
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "not", "no", "nor", "so", "yet", "both", "either", "neither",
    "it", "its", "this", "that", "these", "those", "we", "us", "our",
    "you", "your", "they", "them", "their", "he", "she", "him", "her",
    "all", "each", "every", "any", "few", "more", "most", "other",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "up", "down", "off", "over", "under", "again",
}

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "gl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tokenize(text: str) -> List[str]:
    """Lower-case alpha tokens, removing stopwords."""
    tokens = re.findall(r"[a-z]{3,}", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


class _NLPExtractor:
    """LLM-backed semantic keyword extractor with regex fallback.

    Uses Claude Haiku for richer concept/entity extraction when an LLM is
    available; silently falls back to _tokenize() in air-gap or no-LLM
    environments so the module remains pure-stdlib safe.
    """

    _SYSTEM = (
        "Extract semantically meaningful technical keywords from the supplied text. "
        "Return ONLY a JSON array of lowercase strings — no explanation, no markdown. "
        "Include technical terms, domain nouns, action verbs, and software concepts. "
        "Exclude stopwords, articles, prepositions, and common filler words. At most 20 keywords."
    )
    _FUNCTION = "goal_learner_nlp_extract"
    _cache: Dict[str, set] = {}

    @classmethod
    def extract(cls, text: str) -> List[str]:
        """Return keyword list. Uses LLM when available, else regex fallback."""
        if not text:
            return []
        key = _sha256(text[:500])[:16]
        if key in cls._cache:
            return list(cls._cache[key])
        keywords = cls._llm_extract(text)
        if keywords is None:
            keywords = set(_tokenize(text))
        cls._cache[key] = keywords
        return list(keywords)

    @classmethod
    def _llm_extract(cls, text: str) -> Optional[set]:
        """Attempt LLM extraction. Returns keyword set or None on any failure."""
        try:
            from icdev.tools.llm.router import LLMRequest, LLMRouter  # noqa: PLC0415

            router = LLMRouter()
            if router.is_no_llm_mode() or not router.has_any_llm():
                return None
            resp = router.invoke(
                cls._FUNCTION,
                LLMRequest(
                    messages=[{"role": "user", "content": text[:2000]}],
                    system_prompt=cls._SYSTEM,
                ),
            )
            keywords = json.loads(resp.content.strip())
            if isinstance(keywords, list):
                return {str(k).lower() for k in keywords if k} - _STOPWORDS
        except Exception:
            return None
        return None


def _log_audit(event_type: str, goal_id: str = None, details: Dict = None) -> None:
    """Append-only audit log (NIST AU-2)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO genesis_audit
                (id, event_type, reflex_name, details, gkp_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                f"aud-{uuid.uuid4().hex[:10]}",
                event_type,
                "goal_learner",
                json.dumps(details) if details else None,
                goal_id,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Best-effort
        logger.warning("_log_audit: best-effort INSERT into genesis_audit failed (non-blocking): %s", exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Goal coverage index — build from goals/manifest.md
# ---------------------------------------------------------------------------


def _load_goal_coverage() -> Tuple[List[str], Dict[str, List[str]]]:
    """Parse goals/manifest.md and return (all_keywords, goal_keyword_map).

    goal_keyword_map: { goal_file -> [keyword, ...] }
    """
    manifest_path = GOALS_DIR / "manifest.md"
    if not manifest_path.exists():
        return [], {}

    text = manifest_path.read_text(encoding="utf-8")
    goal_keyword_map: Dict[str, List[str]] = {}
    all_keywords: List[str] = []

    for line in text.splitlines():
        # Table rows: | Goal Name | goals/file.md | Description |
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) >= 3 and ".md" in parts[1]:
            goal_file = parts[1].strip("`")
            description = " ".join(parts[2:])
            keywords = _tokenize(f"{parts[0]} {description}")
            goal_keyword_map[goal_file] = keywords
            all_keywords.extend(keywords)

    return all_keywords, goal_keyword_map


def _novelty_score(domain_keywords: List[str], all_goal_keywords: List[str]) -> float:
    """Compute novelty: fraction of domain keywords not covered by any goal (0–1)."""
    if not domain_keywords:
        return 0.0
    goal_vocab = set(all_goal_keywords)
    novel = sum(1 for kw in domain_keywords if kw not in goal_vocab)
    return novel / len(domain_keywords)


# ---------------------------------------------------------------------------
# Configuration loading (FORGE args layer)
# ---------------------------------------------------------------------------


def _load_reflex_config() -> Dict[str, Any]:
    """Load goal_learner knobs from args/genesis_config.yaml.

    Merges reflexes.goal_learner with convergence.anomaly_detection params,
    falling back to _REFLEX_DEFAULTS on any parse or import error.
    """
    try:
        import yaml  # noqa: PLC0415

        config_path = BASE_DIR / "args" / "genesis_config.yaml"
        if not config_path.exists():
            return dict(_REFLEX_DEFAULTS)
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        merged: Dict[str, Any] = dict(_REFLEX_DEFAULTS)
        reflex_cfg = raw.get("reflexes", {}).get("goal_learner", {})
        merged.update({k: v for k, v in reflex_cfg.items() if v is not None})

        # quality_weights: merge sub-keys so partial overrides work
        if isinstance(reflex_cfg.get("quality_weights"), dict):
            merged["quality_weights"] = {**QUALITY_WEIGHTS, **reflex_cfg["quality_weights"]}

        # Inherit convergence.anomaly_detection params when not overridden locally
        ad_global = raw.get("convergence", {}).get("anomaly_detection", {})
        ad_local = reflex_cfg.get("anomaly_detection", {})
        if ad_global or ad_local:
            merged["anomaly_detection"] = {
                **merged["anomaly_detection"],
                **ad_global,
                **ad_local,
            }
        return merged
    except Exception:
        return dict(_REFLEX_DEFAULTS)


class _AdaptiveThreshold:
    """Adaptive novelty threshold via z-score anomaly detection.

    Uses the same pattern as convergence.anomaly_detection in genesis_config.yaml:
      threshold = mean(historical_novelty_scores) + z_score × std

    Falls back to the configured static threshold when fewer than min_samples
    records exist so early-phase behavior stays stable while data builds up.
    """

    @staticmethod
    def compute(
        static_threshold: float = DEFAULT_NOVELTY_THRESHOLD,
        min_samples: int = 5,
        z_score: float = 2.0,
    ) -> float:
        """Return adaptive novelty threshold clamped to [0.0, 1.0]."""
        try:
            import statistics  # noqa: PLC0415

            conn = get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT novelty_score FROM genesis_generated_goals
                    WHERE status != 'rejected'
                    ORDER BY created_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            finally:
                conn.close()

            scores = [
                float(r["novelty_score"] if isinstance(r, dict) else r[0])
                for r in rows
                if r is not None
            ]
            if len(scores) < min_samples:
                return static_threshold

            mean = statistics.mean(scores)
            std = statistics.stdev(scores) if len(scores) > 1 else 0.0
            return min(1.0, max(0.0, mean + z_score * std))
        except Exception:
            return static_threshold


# ---------------------------------------------------------------------------
# Evidence collection — scan audit and kanban tables
# ---------------------------------------------------------------------------


def _collect_novel_events(lookback_days: int = 7, max_events: int = 500) -> List[Dict[str, Any]]:
    """Scan genesis_audit for problem-solving events in the look-back window."""
    conn = get_connection()
    try:
        # Look back N days using ISO date prefix comparison (deterministic)
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        rows = conn.execute(
            """
            SELECT id, event_type, reflex_name, details, created_at
            FROM genesis_audit
            WHERE created_at >= %s
              AND success IS NOT 0
              AND event_type NOT LIKE 'genesis.promoter.%'
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (cutoff, max_events),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _collect_kanban_completions(lookback_days: int = 7, max_completions: int = 200) -> List[Dict[str, Any]]:
    """Collect recently completed kanban tasks as evidence of novel problem-solving."""
    conn = get_connection()
    try:
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        rows = conn.execute(
            """
            SELECT id, title, description, task_type, completed_at
            FROM kanban_tasks
            WHERE status = 'done'
              AND completed_at IS NOT NULL
              AND completed_at >= %s
            ORDER BY completed_at DESC
            LIMIT %s
            """,
            (cutoff, max_completions),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Domain clustering — group evidence into candidate problem domains
# ---------------------------------------------------------------------------


def _cluster_into_domains(
    audit_events: List[Dict],
    kanban_completions: List[Dict],
    all_goal_keywords: List[str],
    novelty_threshold: float = DEFAULT_NOVELTY_THRESHOLD,
    *,
    top_terms_count: int = 60,
    max_keywords_per_domain: int = 30,
    min_evidence_count: int = 2,
) -> List[Dict[str, Any]]:
    """Group audit events + kanban completions into candidate domains.

    Returns list of domain dicts sorted by novelty score descending.
    Each domain: {domain_label, keywords, novelty_score, evidence_count, events, tasks}
    """
    # Build a bag of (text, source) pairs
    texts: List[Tuple[str, str]] = []
    for ev in audit_events:
        details = ev.get("details") or ""
        if isinstance(details, str):
            try:
                d = json.loads(details)
                text = " ".join(str(v) for v in d.values())
            except Exception:
                text = details
        else:
            text = str(details)
        combined = f"{ev.get('event_type', '')} {ev.get('reflex_name', '')} {text}"
        texts.append((combined, "audit", ev))

    for task in kanban_completions:
        combined = f"{task.get('title', '')} {task.get('description', '')} {task.get('task_type', '')}"
        texts.append((combined, "kanban", task))

    # Count keyword frequencies across all evidence
    # Use NLP extractor for richer semantic coverage; falls back to regex in air-gap mode.
    all_tokens: List[str] = []
    evidence_tokens: List[List[str]] = []
    for (text, _src, _item) in texts:
        tokens = _NLPExtractor.extract(text)
        evidence_tokens.append(tokens)
        all_tokens.extend(tokens)

    if not all_tokens:
        return []

    token_freq = Counter(all_tokens)
    # Top N most frequent terms form candidate domain labels
    top_terms = [tok for tok, _cnt in token_freq.most_common(top_terms_count)]

    # Greedy grouping: cluster evidence by shared top-term
    seen_domains: Dict[str, Dict] = {}
    for i, (text, src, item) in enumerate(texts):
        tokens = set(evidence_tokens[i])
        best_term = None
        best_overlap = 0
        for term in top_terms:
            if term in tokens:
                overlap = token_freq[term]
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_term = term

        if best_term is None:
            continue

        if best_term not in seen_domains:
            seen_domains[best_term] = {
                "domain_label": best_term,
                "keywords": [],
                "evidence_count": 0,
                "events": [],
                "tasks": [],
            }

        dom = seen_domains[best_term]
        dom["evidence_count"] += 1
        dom["keywords"].extend(list(tokens - _STOPWORDS))
        if src == "audit":
            dom["events"].append(item.get("id", ""))
        else:
            dom["tasks"].append(item.get("id", ""))

    # Score each domain
    domains: List[Dict] = []
    for term, dom in seen_domains.items():
        kws = list(set(dom["keywords"]))
        n_score = _novelty_score(kws, all_goal_keywords)
        dom["novelty_score"] = round(n_score, 3)
        dom["keywords"] = kws[:max_keywords_per_domain]
        if n_score >= novelty_threshold and dom["evidence_count"] >= min_evidence_count:
            domains.append(dom)

    domains.sort(key=lambda d: d["novelty_score"], reverse=True)
    return domains


# ---------------------------------------------------------------------------
# Goal markdown generation
# ---------------------------------------------------------------------------

_GOAL_TEMPLATE = """\
# CUI // SP-CTI
# Goal: {title}

**Version:** {version}
**Classification:** CUI // SP-CTI
**Source:** Auto-generated by Goal Learner Reflex (D-GEN-GL-1)
**Status:** SUGGESTED — requires human review and approval before activation
**Generated:** {generated_at}
**Goal ID:** {goal_id}
**Quality Score:** {quality_score:.2f} / 1.00
**Novelty Score:** {novelty_score:.2f}

---

## Purpose

{description}

This goal was automatically derived from {evidence_count} novel problem-solving
event(s) observed in the past {lookback_days} days that had no coverage in any
existing ICDEV™ goal.

---

## Trigger

- Novel agent behavior is detected in `genesis_audit` or `kanban_tasks`
  with domain keywords not matched by `goals/manifest.md`
- Goal Learner reflex fires (daily 20:00 or manual `--scan`)

---

## Inputs

- `genesis_audit` records (lookback window: {lookback_days} days)
- `kanban_tasks` completed records (same window)
- `goals/manifest.md` (existing goal coverage index)
- `args/genesis_config.yaml` → `reflexes.goal_learner` block

---

## Evidence

Observed domain keywords: {keyword_list}

Supporting audit events: {event_count}
Supporting kanban tasks: {task_count}

---

## Process

### Step 1 — Detect Novel Domain
**Tool:** `tools/genesis/goal_learner.py --scan --json`

Run novelty check against all existing goals.  Any domain with novelty score
≥ {novelty_threshold} AND evidence count ≥ 2 qualifies for goal generation.

### Step 2 — Generate Goal Specification
**Tool:** `tools/genesis/goal_learner.py --scan --write --json`

Produce this goal markdown and store metadata in `genesis_generated_goals`.

### Step 3 — Human Review
**Tool:** `tools/genesis/goal_learner.py --list --json` (find pending goals)
`tools/genesis/goal_learner.py --approve <goal_id>` (approve)
`tools/genesis/goal_learner.py --reject <goal_id> --reason "..."` (reject)

On approval:
- Goal file is copied to `goals/` and indexed in `goals/manifest.md`
- A GKP of type `proven_pattern` is exported via the Promoter

### Step 4 — Version History
All edits to this goal increment the version number.  Previous versions
are preserved in `data/genesis/suggested_goals/` with the version suffix.

---

## Outputs

- `goals/{{slug}}.md` (on approval)
- `genesis_generated_goals` DB record
- GKP entry (`proven_pattern`) in `genesis_gkp` (on approval)
- Audit entry in `genesis_audit`

---

## Quality Scoring

| Dimension | Weight | Score |
|-----------|--------|-------|
| Novelty score | 40% | {novelty_score:.2f} |
| Evidence count | 25% | {evidence_normalized:.2f} |
| Domain clarity | 20% | {domain_clarity:.2f} |
| Tool coverage | 15% | {tool_coverage:.2f} |
| **Overall** | **100%** | **{quality_score:.2f}** |

---

## Guardrails

- This file is SUGGESTED and must not be used until a human approves it
- Goal Learner never writes directly to `goals/` — all writes go through
  the human approval gate
- Audit trail is append-only (NIST AU-2)
- Maximum {max_goals_per_run} goals generated per reflex run

---

## Related Goals

- `goals/genesis_goal_learner.md` — Goal Learner reflex workflow
- `goals/genesis_promoter.md` — GKP Promoter (knowledge gateway)
- `goals/genesis_daemon.md` — Daemon lifecycle

---

*Auto-generated by ICDEV™ Goal Learner v{learner_version} on {generated_at}*
"""


def _domain_to_title(domain_label: str, keywords: List[str]) -> str:
    """Produce a human-readable goal title from a domain."""
    # Capitalize domain label and add context from top keywords
    top = [kw for kw in keywords[:3] if kw != domain_label]
    if top:
        return f"{domain_label.title()} {top[0].title()} Workflow"
    return f"{domain_label.title()} Workflow"


def _domain_to_slug(title: str) -> str:
    """Convert title to a valid filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug[:60]


def _compute_quality_score(
    novelty_score: float,
    evidence_count: int,
    keywords: List[str],
    *,
    quality_weights: Optional[Dict[str, float]] = None,
    evidence_count_normalizer: float = 4.0,
    domain_clarity_divisor: float = 10.0,
    tool_coverage_placeholder: float = 0.5,
) -> Tuple[float, float, float, float]:
    """Return (overall, evidence_norm, domain_clarity, tool_coverage) scores.

    All numeric knobs are configurable via args/genesis_config.yaml
    reflexes.goal_learner.* — falling back to the documented defaults.
    """
    weights = quality_weights if quality_weights is not None else QUALITY_WEIGHTS

    # Normalize evidence count: 1 event → ~0, (normalizer+1) events → 1.0
    evidence_norm = min(1.0, max(0.0, (evidence_count - 1) / evidence_count_normalizer))

    # Domain clarity: specificity of keywords (longer, more distinct = higher)
    if not keywords:
        domain_clarity = 0.0
    else:
        avg_len = sum(len(k) for k in keywords) / len(keywords)
        domain_clarity = min(1.0, avg_len / domain_clarity_divisor)

    tool_coverage = tool_coverage_placeholder

    overall = (
        weights.get("novelty_score", 0.40) * novelty_score
        + weights.get("evidence_count", 0.25) * evidence_norm
        + weights.get("domain_clarity", 0.20) * domain_clarity
        + weights.get("tool_coverage", 0.15) * tool_coverage
    )
    return round(overall, 3), round(evidence_norm, 3), round(domain_clarity, 3), round(tool_coverage, 3)


def _render_goal_markdown(
    domain: Dict[str, Any],
    goal_id: str,
    lookback_days: int,
    novelty_threshold: float,
    *,
    quality_weights: Optional[Dict[str, float]] = None,
    evidence_count_normalizer: float = 4.0,
    domain_clarity_divisor: float = 10.0,
    tool_coverage_placeholder: float = 0.5,
) -> Tuple[str, str, float]:
    """Render goal markdown for a domain.  Returns (markdown, title, quality_score)."""
    keywords = domain.get("keywords", [])
    domain_label = domain.get("domain_label", "unknown")
    evidence_count = domain.get("evidence_count", 0)
    novelty_score = domain.get("novelty_score", 0.0)
    event_count = len(domain.get("events", []))
    task_count = len(domain.get("tasks", []))

    title = _domain_to_title(domain_label, keywords)
    overall, evidence_norm, domain_clarity, tool_coverage = _compute_quality_score(
        novelty_score,
        evidence_count,
        keywords,
        quality_weights=quality_weights,
        evidence_count_normalizer=evidence_count_normalizer,
        domain_clarity_divisor=domain_clarity_divisor,
        tool_coverage_placeholder=tool_coverage_placeholder,
    )

    description = (
        f"Workflow for handling '{domain_label}' problem domains. "
        f"This goal captures a recurring pattern that current goals do not address, "
        f"covering areas such as: {', '.join(keywords[:5])}."
    )

    keyword_list = ", ".join(f"`{k}`" for k in keywords[:15])

    md = _GOAL_TEMPLATE.format(
        title=title,
        version="1.0",
        generated_at=_utcnow_iso(),
        goal_id=goal_id,
        quality_score=overall,
        novelty_score=novelty_score,
        description=description,
        evidence_count=evidence_count,
        lookback_days=lookback_days,
        keyword_list=keyword_list,
        event_count=event_count,
        task_count=task_count,
        novelty_threshold=novelty_threshold,
        evidence_normalized=evidence_norm,
        domain_clarity=domain_clarity,
        tool_coverage=tool_coverage,
        max_goals_per_run=MAX_GOALS_PER_RUN,
        learner_version=LEARNER_VERSION,
    )
    return md, title, overall


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def _ensure_table() -> None:
    """Create genesis_generated_goals table if it doesn't exist."""
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS genesis_generated_goals (
                id              TEXT PRIMARY KEY,
                version         INTEGER NOT NULL DEFAULT 1,
                domain_label    TEXT NOT NULL,
                title           TEXT NOT NULL,
                slug            TEXT NOT NULL,
                novelty_score   REAL NOT NULL DEFAULT 0.0,
                quality_score   REAL NOT NULL DEFAULT 0.0,
                evidence_count  INTEGER NOT NULL DEFAULT 0,
                keywords        TEXT,
                goal_markdown   TEXT NOT NULL,
                sha256          TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'suggested'
                    CHECK(status IN ('suggested','approved','rejected','superseded')),
                gkp_id          TEXT,
                goal_file_path  TEXT,
                rejection_reason TEXT,
                approved_at     TEXT,
                rejected_at     TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gg_status ON genesis_generated_goals(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gg_domain ON genesis_generated_goals(domain_label)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gg_created ON genesis_generated_goals(created_at)"
        )
        conn.commit()
    finally:
        conn.close()


def _store_generated_goal(
    goal_id: str,
    domain: Dict[str, Any],
    title: str,
    slug: str,
    goal_markdown: str,
    quality_score: float,
) -> None:
    """Persist generated goal to genesis_generated_goals."""
    conn = get_connection()
    try:
        now = _utcnow_iso()
        conn.execute(
            """
            INSERT INTO genesis_generated_goals
                (id, version, domain_label, title, slug, novelty_score,
                 quality_score, evidence_count, keywords, goal_markdown,
                 sha256, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                goal_id,
                1,
                domain.get("domain_label", ""),
                title,
                slug,
                domain.get("novelty_score", 0.0),
                quality_score,
                domain.get("evidence_count", 0),
                json.dumps(domain.get("keywords", [])),
                goal_markdown,
                _sha256(goal_markdown),
                STATUS_SUGGESTED,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _write_goal_file(goal_id: str, slug: str, markdown: str) -> Path:
    """Write goal markdown to data/genesis/suggested_goals/."""
    SUGGESTED_GOALS_DIR.mkdir(parents=True, exist_ok=True)
    path = SUGGESTED_GOALS_DIR / f"{slug}_{goal_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def _check_duplicate_domain(domain_label: str) -> Optional[str]:
    """Return existing goal_id if this domain was already generated and is still active."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id FROM genesis_generated_goals
            WHERE domain_label = %s AND status IN ('suggested', 'approved')
            LIMIT 1
            """,
            (domain_label,),
        ).fetchone()
        if row:
            return row["id"] if isinstance(row, dict) else row[0]
    finally:
        conn.close()
    return None


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------


def scan_and_generate(
    lookback_days: int = 7,
    novelty_threshold: float = DEFAULT_NOVELTY_THRESHOLD,
    max_goals: int = MAX_GOALS_PER_RUN,
    write_files: bool = False,
    *,
    reflex_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Main entry: detect novel domains, generate goals.

    Returns result dict with 'goals_generated', 'goals', and 'skipped'.

    Knobs are loaded from args/genesis_config.yaml (FORGE args layer) when
    reflex_config is not supplied.  The novelty_threshold is further refined
    by _AdaptiveThreshold when enough historical data exists.
    """
    _ensure_table()

    cfg = reflex_config if reflex_config is not None else _load_reflex_config()

    # Adaptive novelty threshold — uses z-score over historical novelty scores
    # when enough samples exist; falls back to the static configured value.
    ad_cfg = cfg.get("anomaly_detection", {})
    effective_threshold = _AdaptiveThreshold.compute(
        static_threshold=novelty_threshold,
        min_samples=int(ad_cfg.get("min_samples", 5)),
        z_score=float(ad_cfg.get("z_score", 2.0)),
    )

    top_terms_count = int(cfg.get("top_terms_count", 60))
    max_keywords_per_domain = int(cfg.get("max_keywords_per_domain", 30))
    min_evidence_count = int(cfg.get("min_evidence_count", 2))
    max_audit_events = int(cfg.get("max_audit_events", 500))
    max_kanban_completions = int(cfg.get("max_kanban_completions", 200))
    quality_weights = cfg.get("quality_weights", QUALITY_WEIGHTS)
    evidence_count_normalizer = float(cfg.get("evidence_count_normalizer", 4.0))
    domain_clarity_divisor = float(cfg.get("domain_clarity_divisor", 10.0))
    tool_coverage_placeholder = float(cfg.get("tool_coverage_placeholder", 0.5))

    # 1. Load existing goal coverage
    all_goal_keywords, goal_keyword_map = _load_goal_coverage()

    # 2. Collect evidence
    audit_events = _collect_novel_events(lookback_days, max_events=max_audit_events)
    kanban_tasks = _collect_kanban_completions(lookback_days, max_completions=max_kanban_completions)

    # 3. Cluster into candidate domains
    domains = _cluster_into_domains(
        audit_events,
        kanban_tasks,
        all_goal_keywords,
        effective_threshold,
        top_terms_count=top_terms_count,
        max_keywords_per_domain=max_keywords_per_domain,
        min_evidence_count=min_evidence_count,
    )

    generated: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for domain in domains[:max_goals]:
        domain_label = domain.get("domain_label", "")

        # Dedup check
        existing = _check_duplicate_domain(domain_label)
        if existing:
            skipped.append(
                {"domain_label": domain_label, "reason": "already_generated", "existing_id": existing}
            )
            continue

        # Generate goal
        goal_id = _generate_id("gl")
        markdown, title, quality_score = _render_goal_markdown(
            domain,
            goal_id,
            lookback_days,
            effective_threshold,
            quality_weights=quality_weights,
            evidence_count_normalizer=evidence_count_normalizer,
            domain_clarity_divisor=domain_clarity_divisor,
            tool_coverage_placeholder=tool_coverage_placeholder,
        )
        slug = _domain_to_slug(title)

        # Persist to DB
        _store_generated_goal(goal_id, domain, title, slug, markdown, quality_score)

        # Optionally write file
        goal_file_path: Optional[str] = None
        if write_files:
            path = _write_goal_file(goal_id, slug, markdown)
            goal_file_path = str(path.relative_to(BASE_DIR))
            # Update DB with file path
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE genesis_generated_goals SET goal_file_path = %s, updated_at = %s WHERE id = %s",
                    (goal_file_path, _utcnow_iso(), goal_id),
                )
                conn.commit()
            finally:
                conn.close()

        _log_audit(
            "genesis.goal_learner.generated",
            goal_id,
            {
                "domain_label": domain_label,
                "title": title,
                "quality_score": quality_score,
                "novelty_score": domain.get("novelty_score", 0.0),
                "evidence_count": domain.get("evidence_count", 0),
                "goal_file": goal_file_path,
            },
        )

        generated.append(
            {
                "goal_id": goal_id,
                "title": title,
                "slug": slug,
                "domain_label": domain_label,
                "novelty_score": domain.get("novelty_score", 0.0),
                "quality_score": quality_score,
                "evidence_count": domain.get("evidence_count", 0),
                "status": STATUS_SUGGESTED,
                "goal_file": goal_file_path,
            }
        )

    result = {
        "scanned_at": _utcnow_iso(),
        "lookback_days": lookback_days,
        "novelty_threshold": novelty_threshold,
        "effective_novelty_threshold": round(effective_threshold, 4),
        "adaptive_threshold_active": effective_threshold != novelty_threshold,
        "min_evidence_count": min_evidence_count,
        "audit_events_scanned": len(audit_events),
        "kanban_tasks_scanned": len(kanban_tasks),
        "candidate_domains": len(domains),
        "goals_generated": len(generated),
        "goals_skipped": len(skipped),
        "goals": generated,
        "skipped": skipped,
    }

    _log_audit(
        "genesis.goal_learner.scan_complete",
        None,
        {
            "goals_generated": len(generated),
            "goals_skipped": len(skipped),
            "candidate_domains": len(domains),
        },
    )

    return result


# ---------------------------------------------------------------------------
# Query & approval operations
# ---------------------------------------------------------------------------


def list_generated_goals(status: str = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List generated goals, optionally filtered by status."""
    _ensure_table()
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT id, version, domain_label, title, slug, novelty_score,
                       quality_score, evidence_count, status, goal_file_path,
                       gkp_id, created_at, updated_at
                FROM genesis_generated_goals
                WHERE status = %s
                ORDER BY quality_score DESC, created_at DESC
                LIMIT %s
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, version, domain_label, title, slug, novelty_score,
                       quality_score, evidence_count, status, goal_file_path,
                       gkp_id, created_at, updated_at
                FROM genesis_generated_goals
                ORDER BY quality_score DESC, created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_goal_detail(goal_id: str) -> Optional[Dict[str, Any]]:
    """Get full goal record including markdown."""
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM genesis_generated_goals WHERE id = %s", (goal_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def approve_goal(goal_id: str) -> Dict[str, Any]:
    """Approve a suggested goal — write to goals/ and register in manifest.

    Also exports a GKP of type 'proven_pattern' via the Promoter.
    Always requires human action.
    """
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM genesis_generated_goals WHERE id = %s", (goal_id,)
        ).fetchone()
        if not row:
            return {"error": f"Goal not found: {goal_id}"}

        goal = dict(row)
        current_status = goal.get("status")
        if current_status != STATUS_SUGGESTED:
            return {"error": f"Cannot approve goal with status '{current_status}'", "goal_id": goal_id}

        slug = goal["slug"]
        markdown = goal["goal_markdown"]
        title = goal["title"]

        # Write to goals/
        goal_file = GOALS_DIR / f"{slug}.md"
        goal_file.write_text(markdown, encoding="utf-8")

        # Append to goals/manifest.md
        manifest_path = GOALS_DIR / "manifest.md"
        manifest_text = manifest_path.read_text(encoding="utf-8")
        new_row = f"| {title} | goals/{slug}.md | Auto-learned goal from experience (D-GEN-GL-1) |\n"
        if f"goals/{slug}.md" not in manifest_text:
            manifest_text = manifest_text.rstrip() + "\n" + new_row
            manifest_path.write_text(manifest_text, encoding="utf-8")

        # Export GKP
        gkp_id = None
        try:
            from tools.genesis.promoter import export_gkp

            gkp_result = export_gkp(
                reflex="goal_learner",
                artifact_type="proven_pattern",
                payload={
                    "goal_id": goal_id,
                    "title": title,
                    "slug": slug,
                    "goal_file": f"goals/{slug}.md",
                    "novelty_score": goal.get("novelty_score", 0.0),
                    "quality_score": goal.get("quality_score", 0.0),
                    "evidence_count": goal.get("evidence_count", 0),
                    "pattern_type": "learned_goal",
                    "signature": f"goal_learner:{slug}",
                    "root_cause": "Novel problem domain not covered by existing goals",
                    "remediation": {"action": "activate_generated_goal", "goal_file": f"goals/{slug}.md"},
                    "auto_healable": False,
                    "occurrence_count": goal.get("evidence_count", 1),
                },
                confidence=goal.get("quality_score", 0.5),
                evidence={"goal_id": goal_id, "domain_label": goal.get("domain_label")},
            )
            gkp_id = gkp_result.get("gkp_id")
        except Exception as e:
            gkp_id = None
            _log_audit("genesis.goal_learner.gkp_export_failed", goal_id, {"error": str(e)})

        # Update DB
        now = _utcnow_iso()
        conn.execute(
            """
            UPDATE genesis_generated_goals
            SET status = %s, approved_at = %s, goal_file_path = %s,
                gkp_id = %s, updated_at = %s
            WHERE id = %s
            """,
            (STATUS_APPROVED, now, f"goals/{slug}.md", gkp_id, now, goal_id),
        )
        conn.commit()
    finally:
        conn.close()

    _log_audit(
        "genesis.goal_learner.approved",
        goal_id,
        {"title": title, "goal_file": f"goals/{slug}.md", "gkp_id": gkp_id},
    )

    return {
        "status": "approved",
        "goal_id": goal_id,
        "title": title,
        "goal_file": f"goals/{slug}.md",
        "gkp_id": gkp_id,
    }


def reject_goal(goal_id: str, reason: str = "") -> Dict[str, Any]:
    """Reject a suggested goal."""
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM genesis_generated_goals WHERE id = %s", (goal_id,)
        ).fetchone()
        if not row:
            return {"error": f"Goal not found: {goal_id}"}

        current_status = row["status"] if isinstance(row, dict) else row[0]
        if current_status == STATUS_APPROVED:
            return {"error": "Cannot reject an already-approved goal", "goal_id": goal_id}

        now = _utcnow_iso()
        conn.execute(
            """
            UPDATE genesis_generated_goals
            SET status = %s, rejection_reason = %s, rejected_at = %s, updated_at = %s
            WHERE id = %s
            """,
            (STATUS_REJECTED, reason, now, now, goal_id),
        )
        conn.commit()
    finally:
        conn.close()

    _log_audit(
        "genesis.goal_learner.rejected",
        goal_id,
        {"reason": reason},
    )
    return {"status": "rejected", "goal_id": goal_id, "reason": reason}


def get_stats() -> Dict[str, Any]:
    """Return summary statistics."""
    _ensure_table()
    conn = get_connection()
    try:
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM genesis_generated_goals"
        ).fetchone()
        by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM genesis_generated_goals GROUP BY status"
        ).fetchall()
        avg_quality = conn.execute(
            "SELECT AVG(quality_score) as avg FROM genesis_generated_goals WHERE status != 'rejected'"
        ).fetchone()

        return {
            "total_goals": (total_row["cnt"] if isinstance(total_row, dict) else total_row[0]) if total_row else 0,
            "by_status": {
                (r["status"] if isinstance(r, dict) else r[0]): (r["cnt"] if isinstance(r, dict) else r[1])
                for r in by_status
            },
            "avg_quality_score": round(
                (avg_quality["avg"] if isinstance(avg_quality, dict) else avg_quality[0]) or 0.0,
                3,
            ),
            "timestamp": _utcnow_iso(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reflex entry point (called by daemon.py)
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], state: Any) -> Dict[str, Any]:
    """Genesis daemon reflex entry point.

    Args:
        config: Reflex config block from genesis_config.yaml
        state:  ReflexState instance (unused — stateless scan)

    Returns:
        Dict with metric_name and metric_value for daemon tracking.
    """
    # Merge daemon-supplied config block with YAML defaults so all knobs
    # (quality_weights, top_terms_count, anomaly_detection params, etc.)
    # flow through without requiring the daemon to enumerate them.
    base_cfg = _load_reflex_config()
    base_cfg.update({k: v for k, v in config.items() if v is not None})

    novelty_threshold = float(base_cfg.get("novelty_threshold", DEFAULT_NOVELTY_THRESHOLD))
    max_goals = int(base_cfg.get("max_goals_per_run", MAX_GOALS_PER_RUN))
    lookback_days = int(base_cfg.get("lookback_days", 7))
    write_files = bool(base_cfg.get("write_files", True))

    result = scan_and_generate(
        lookback_days=lookback_days,
        novelty_threshold=novelty_threshold,
        max_goals=max_goals,
        write_files=write_files,
        reflex_config=base_cfg,
    )

    return {
        "metric_name": "goals_generated",
        "metric_value": result.get("goals_generated", 0),
        "details": result,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Genesis Goal Learner — Self-Improving Goals from Experience"
    )
    parser.add_argument("--scan", action="store_true", help="Scan for novel domains and generate goals")
    parser.add_argument("--write", action="store_true", help="Write goal files to suggested_goals/ (with --scan)")
    parser.add_argument(
        "--lookback-days", type=int, default=7, help="Look-back window in days (default: 7)"
    )
    parser.add_argument(
        "--novelty-threshold",
        type=float,
        default=DEFAULT_NOVELTY_THRESHOLD,
        help=f"Minimum novelty score 0–1 (default: {DEFAULT_NOVELTY_THRESHOLD})",
    )
    parser.add_argument(
        "--max-goals", type=int, default=MAX_GOALS_PER_RUN, help=f"Max goals per run (default: {MAX_GOALS_PER_RUN})"
    )
    parser.add_argument("--list", action="store_true", help="List generated goals")
    parser.add_argument(
        "--status-filter",
        type=str,
        default=None,
        choices=["suggested", "approved", "rejected", "superseded"],
        help="Filter by status",
    )
    parser.add_argument("--review", type=str, metavar="GOAL_ID", help="Show full detail for a goal")
    parser.add_argument("--approve", type=str, metavar="GOAL_ID", help="Approve a suggested goal")
    parser.add_argument("--reject", type=str, metavar="GOAL_ID", help="Reject a suggested goal")
    parser.add_argument("--reason", type=str, default="", help="Rejection reason (with --reject)")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    def _out(data: Any) -> None:
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print(data)

    if args.scan:
        result = scan_and_generate(
            lookback_days=args.lookback_days,
            novelty_threshold=args.novelty_threshold,
            max_goals=args.max_goals,
            write_files=args.write,
        )
        if args.json:
            _out(result)
        else:
            print(f"Scanned {result['audit_events_scanned']} audit events, "
                  f"{result['kanban_tasks_scanned']} kanban tasks")
            print(f"Candidate domains: {result['candidate_domains']}")
            print(f"Goals generated:   {result['goals_generated']}")
            print(f"Goals skipped:     {result['goals_skipped']}")
            for g in result.get("goals", []):
                print(
                    f"  [{g['goal_id']}] {g['title']!r} "
                    f"novelty={g['novelty_score']:.2f} quality={g['quality_score']:.2f}"
                )
        return

    if args.list:
        goals = list_generated_goals(status=args.status_filter)
        if args.json:
            _out(goals)
        else:
            print(f"{'ID':<14} {'Status':<12} {'Quality':<8} {'Novelty':<8} {'Title'}")
            print("-" * 90)
            for g in goals:
                print(
                    f"{g['id']:<14} {g['status']:<12} "
                    f"{g['quality_score']:<8.2f} {g['novelty_score']:<8.2f} {g['title']}"
                )
        return

    if args.review:
        goal = get_goal_detail(args.review)
        if not goal:
            _out({"error": f"Goal not found: {args.review}"})
        else:
            if args.json:
                _out(goal)
            else:
                print(goal.get("goal_markdown", ""))
        return

    if args.approve:
        result = approve_goal(args.approve)
        _out(result)
        return

    if args.reject:
        result = reject_goal(args.reject, reason=args.reason)
        _out(result)
        return

    if args.stats:
        _out(get_stats())
        return

    parser.print_help()


if __name__ == "__main__":
    main()
