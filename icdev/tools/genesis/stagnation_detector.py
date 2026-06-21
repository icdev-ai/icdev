#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Stagnation Detector — detect stuck reflexes, break plateaus.

4 detection patterns:
  1. Oscillation:         Period-2 cycles (A→B→A→B) in last 4 metric values
  2. Stagnation:          Identical metric ≥3 consecutive runs (>0.99 similarity)
  3. Diminishing returns:  Metric slope < threshold over window
  4. Repetitive output:   ≥70% keyword overlap in last 3 GKP outputs

5 lateral thinking personas (qwen3.5 scanner tier, zero Claude cost):
  hacker, researcher, simplifier, architect, contrarian

ADRs: D-GEN-2 (scanner-tier only), D21 (deterministic detection), D6 (append-only)
"""

import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.common.helpers import now_isoformat  # noqa: E402


_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "from",
        "by",
        "as",
        "and",
        "or",
        "not",
        "this",
        "that",
        "it",
    ]
)

# ---------------------------------------------------------------------------
# Module-level fallback constants — overridable from genesis_config.yaml
# under stagnation_detector.  Change config, not code.
# ---------------------------------------------------------------------------
_OSCILLATION_TOLERANCE       = 0.01    # period-2 cycle match tolerance
_LLM_KEYWORD_CONTEXT_CHARS   = 2000    # max chars sent for keyword extraction
_LLM_ALT_CONTEXT_CHARS       = 4000    # max chars sent for alternative generation
_LLM_ALT_MAX_TOKENS          = 2048    # max tokens for alternative generation
_LLM_ALT_TEMPERATURE         = 0.7    # LLM temperature for lateral thinking
_SCORE_DENOMINATOR           = 3       # denominator for feasibility/novelty score (min 3 hits = 1.0)
_RECENT_METRICS_LIMIT        = 10      # default max metric history rows to fetch


_FEASIBILITY_KEYWORDS = frozenset(
    [
        "existing",
        "reuse",
        "simple",
        "stdlib",
        "deterministic",
        "incremental",
        "lightweight",
        "minimal",
        "backward",
        "compatible",
        "safe",
        "proven",
    ]
)
_NOVELTY_KEYWORDS = frozenset(
    [
        "new",
        "novel",
        "different",
        "alternative",
        "creative",
        "unconventional",
        "innovative",
        "fresh",
        "unique",
        "original",
        "rethink",
        "restructure",
    ]
)


def _tokenize(text: str) -> Set[str]:
    words = set(re.findall(r"[a-z_]+", text.lower()))
    return words - _STOPWORDS


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


class _NLPExtractor:
    """LLM-backed semantic keyword extractor with regex fallback.

    Uses scanner-tier LLM (Haiku) for richer concept extraction when available;
    silently falls back to _tokenize() in air-gap / no-LLM environments.
    ADR D-GEN-2: scanner-tier only, zero Claude cost.
    """

    _SYSTEM = (
        "Extract semantically meaningful keywords from the supplied text. "
        "Return ONLY a JSON array of lowercase strings — no explanation, no markdown. "
        "Include technical terms, domain nouns, and action verbs. "
        "Exclude stopwords, articles, and prepositions. At most 30 keywords."
    )
    _FUNCTION = "stagnation_nlp_extract"
    _cache: Dict[str, Set[str]] = {}

    @classmethod
    def extract(cls, text: str) -> Set[str]:
        """Return keyword set. Uses LLM when available, else regex fallback."""
        if not text:
            return set()
        key = hashlib.sha256(text.encode()).hexdigest()[:16]
        if key in cls._cache:
            return cls._cache[key]
        result = cls._llm_extract(text) or _tokenize(text)
        cls._cache[key] = result
        return result

    @classmethod
    def _llm_extract(cls, text: str) -> Optional[Set[str]]:
        """Attempt LLM extraction. Returns keyword set or None on any failure."""
        try:
            from icdev.tools.llm.router import LLMRequest, LLMRouter

            router = LLMRouter()
            if router.is_no_llm_mode() or not router.has_any_llm():
                return None
            resp = router.invoke(
                cls._FUNCTION,
                LLMRequest(
                    messages=[{"role": "user", "content": text[:_LLM_KEYWORD_CONTEXT_CHARS]}],
                    system_prompt=cls._SYSTEM,
                ),
            )
            keywords = json.loads(resp.content.strip())
            if isinstance(keywords, list):
                return {str(k).lower() for k in keywords if k} - _STOPWORDS
        except Exception:
            return None
        return None


def _parse_json_array(text: str) -> Optional[List[Dict]]:
    """Extract first JSON array from text using bracket counting (no regex)."""
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class StagnationDetector:
    """Detect stagnation patterns in Genesis reflex execution history."""

    def __init__(self, config: Dict[str, Any], llm_config: Dict[str, Any] = None):
        self.detection = config.get("detection", {})
        self.personas = config.get("personas", {})
        self.scoring = config.get("scoring", {})
        self.max_alts = config.get("max_alternatives_per_persona", 3)
        self.llm_config = llm_config or {}

    # ── Public API ────────────────────────────────────────────────────

    def detect(self, reflex_name: str) -> Dict[str, Any]:
        """Run all 4 pattern detectors. Returns first match or None."""
        metrics = self._get_recent_metrics(reflex_name)

        pattern = None
        details: Dict[str, Any] = {}

        # 1. Oscillation
        osc = self._detect_oscillation(metrics)
        if osc:
            pattern = "oscillation"
            details = osc

        # 2. Stagnation
        if not pattern:
            stag = self._detect_stagnation(metrics)
            if stag:
                pattern = "stagnation"
                details = stag

        # 3. Diminishing returns
        if not pattern:
            dim = self._detect_diminishing_returns(metrics)
            if dim:
                pattern = "diminishing_returns"
                details = dim

        # 4. Repetitive output
        if not pattern:
            rep = self._detect_repetitive_output(reflex_name)
            if rep:
                pattern = "repetitive_output"
                details = rep

        return {
            "reflex_name": reflex_name,
            "stagnation_detected": pattern is not None,
            "pattern_type": pattern,
            "metrics_analyzed": len(metrics),
            "details": details,
        }

    def break_plateau(self, reflex_name: str, pattern_type: str, context: str) -> Dict[str, Any]:
        """Select persona, generate alternatives, score, store."""
        persona_name, prompt = self._select_persona(pattern_type)
        alternatives = self._generate_alternatives(persona_name, prompt, context)

        # Score and sort
        for alt in alternatives:
            alt["score"] = self._score_alternative(alt)
        alternatives.sort(key=lambda x: x["score"], reverse=True)

        selected = alternatives[0] if alternatives else {"description": "no alternatives generated", "score": 0.0}

        result = {
            "reflex_name": reflex_name,
            "pattern_type": pattern_type,
            "persona_used": persona_name,
            "alternatives": alternatives,
            "selected_alternative": selected.get("description", ""),
            "selected_score": selected.get("score", 0.0),
        }
        entry_id = self._store(result)
        result["stored_id"] = entry_id
        return result

    # ── Pattern Detectors ─────────────────────────────────────────────

    def _detect_oscillation(self, metrics: List[float]) -> Optional[Dict]:
        """Period-2 cycle: A→B→A→B in last 4 values."""
        window = self.detection.get("oscillation_window", 4)
        if len(metrics) < window:
            return None
        recent = metrics[-window:]
        tol = _OSCILLATION_TOLERANCE
        # Check A≈C and B≈D (period-2)
        if abs(recent[0] - recent[2]) < tol and abs(recent[1] - recent[3]) < tol and abs(recent[0] - recent[1]) > tol:
            return {
                "pattern": "period_2_cycle",
                "values": recent,
                "a_value": recent[0],
                "b_value": recent[1],
            }
        return None

    def _detect_stagnation(self, metrics: List[float]) -> Optional[Dict]:
        """Identical metric ≥N consecutive runs."""
        threshold = self.detection.get("stagnation_threshold", 0.99)
        min_consec = self.detection.get("stagnation_min_consecutive", 3)
        if len(metrics) < min_consec:
            return None
        recent = metrics[-min_consec:]
        if not recent:
            return None
        ref = recent[0]
        if ref == 0:
            all_same = all(v == 0 for v in recent)
        else:
            all_same = all(abs(v - ref) / max(abs(ref), 1e-9) < (1 - threshold) for v in recent)
        if all_same:
            return {
                "pattern": "identical_metrics",
                "values": recent,
                "reference": ref,
                "consecutive": min_consec,
            }
        return None

    def _detect_diminishing_returns(self, metrics: List[float]) -> Optional[Dict]:
        """Metric slope < threshold."""
        slope_thresh = self.detection.get("diminishing_returns_slope_threshold", 0.01)
        if len(metrics) < 3:
            return None
        # Compute slope
        n = len(metrics)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(metrics) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, metrics))
        den = sum((x - x_mean) ** 2 for x in xs)
        slope = num / den if den else 0.0

        if abs(slope) < slope_thresh:
            return {
                "pattern": "flat_slope",
                "slope": round(slope, 6),
                "threshold": slope_thresh,
                "window": n,
            }
        return None

    def _detect_repetitive_output(self, reflex_name: str) -> Optional[Dict]:
        """≥70% keyword overlap in last N GKP outputs."""
        overlap_thresh = self.detection.get("repetitive_output_overlap", 0.70)
        window = self.detection.get("repetitive_output_window", 3)

        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT payload FROM genesis_gkp
                   WHERE reflex = ? ORDER BY created_at DESC LIMIT ?""",
                (reflex_name, window),
            ).fetchall()
        except Exception:
            return None
        finally:
            conn.close()

        if len(rows) < 2:
            return None

        # Extract keywords from each output via NLP (regex fallback in air-gap)
        token_sets = []
        for row in rows:
            payload = row[0] if row[0] else ""
            token_sets.append(_NLPExtractor.extract(payload))

        # Compute pairwise Jaccard
        similarities = []
        for i in range(len(token_sets)):
            for j in range(i + 1, len(token_sets)):
                similarities.append(_jaccard(token_sets[i], token_sets[j]))

        avg_sim = sum(similarities) / len(similarities) if similarities else 0.0

        if avg_sim >= overlap_thresh:
            return {
                "pattern": "keyword_overlap",
                "avg_similarity": round(avg_sim, 4),
                "threshold": overlap_thresh,
                "outputs_compared": len(rows),
            }
        return None

    # ── Persona Selection & Alternative Generation ────────────────────

    def _select_persona(self, pattern_type: str) -> Tuple[str, str]:
        """Pick best persona for the detected pattern."""
        for name, cfg in self.personas.items():
            applicable = cfg.get("applicable_patterns", [])
            if pattern_type in applicable:
                return name, cfg.get("description", "Suggest alternatives.")
        # Fallback to contrarian
        fallback = self.personas.get("contrarian", {})
        return "contrarian", fallback.get("description", "What assumption should we challenge?")

    def _generate_alternatives(self, persona: str, prompt: str, context: str) -> List[Dict]:
        """Generate alternatives via LLM (scanner tier) or deterministic fallback."""
        alternatives = []
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            router = LLMRouter()
            system = (
                f"You are the '{persona}' lateral thinking persona. "
                f"Your role: {prompt}\n"
                f"Given the following reflex execution context, propose "
                f"1-{self.max_alts} concrete, actionable alternative approaches. "
                f'Return JSON array: [{{"description": "..."}}, ...]'
            )
            request = LLMRequest(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": context[:_LLM_ALT_CONTEXT_CHARS]},
                ],
                max_tokens=_LLM_ALT_MAX_TOKENS,
                temperature=_LLM_ALT_TEMPERATURE,
            )
            result = router.invoke("code_analysis", request)
            text = result.content if result and result.content else ""
            parsed = _parse_json_array(text)
            if parsed is not None:
                for item in parsed[: self.max_alts]:
                    if isinstance(item, dict) and "description" in item:
                        alternatives.append(item)
        except Exception:
            # Deterministic fallback
            alternatives = [
                {"description": f"[{persona}] Re-examine constraints and try a simpler approach"},
                {"description": f"[{persona}] Increase data diversity in source inputs"},
                {"description": f"[{persona}] Reduce scope to most impactful subset"},
            ]

        return alternatives[: self.max_alts]

    def _score_alternative(self, alt: Dict) -> float:
        """Deterministic keyword scoring: feasibility + novelty."""
        text = alt.get("description", "")
        tokens = _tokenize(text)
        if not tokens:
            return 0.0

        f_hits = len(tokens & _FEASIBILITY_KEYWORDS)
        n_hits = len(tokens & _NOVELTY_KEYWORDS)

        f_score = min(f_hits / _SCORE_DENOMINATOR, 1.0)
        n_score = min(n_hits / _SCORE_DENOMINATOR, 1.0)

        w_f = self.scoring.get("feasibility_weight", 0.6)
        w_n = self.scoring.get("novelty_weight", 0.4)

        return round(w_f * f_score + w_n * n_score, 4)

    # ── Helpers ───────────────────────────────────────────────────────

    def _get_recent_metrics(self, reflex_name: str, limit: int = _RECENT_METRICS_LIMIT) -> List[float]:
        """Fetch last N metric_values from genesis_audit."""
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT metric_value FROM genesis_audit
                   WHERE reflex_name = ? AND event_type = 'genesis.reflex.completed'
                     AND metric_value IS NOT NULL
                   ORDER BY created_at DESC LIMIT ?""",
                (reflex_name, limit),
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in reversed(rows)] if rows else []

    def _store(self, result: Dict) -> str:
        """Append-only INSERT into genesis_stagnation_log."""
        from tools.db.storage import get_connection

        entry_id = str(uuid.uuid4())
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO genesis_stagnation_log
                   (id, reflex_name, pattern_type, persona_used,
                    alternatives_json, selected_alternative, score,
                    details_json, classification, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CUI', ?)""",
                (
                    entry_id,
                    result["reflex_name"],
                    result["pattern_type"],
                    result.get("persona_used"),
                    json.dumps(result.get("alternatives", [])),
                    result.get("selected_alternative"),
                    result.get("selected_score", 0.0),
                    json.dumps(result.get("details", {})),
                    now_isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return entry_id
