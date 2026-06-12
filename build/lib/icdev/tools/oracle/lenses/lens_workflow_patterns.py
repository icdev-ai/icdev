# CUI // SP-CTI
"""Oracle Workflow Pattern Lens — Mine frequent multi-step tool sequences.

Analyzes audit_trail and kanban_tasks to surface:
  - Frequent 3–5 step sequential event patterns (sliding window, hash counting)
  - Tool pairs with >80% co-occurrence rate (composition candidates)
  - Tasks that fail then succeed (backlog→in_progress→backlog→done) as
    self-healing candidates

All mining is deterministic — zero LLM calls, scanner-tier.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from tools.db.storage import get_connection
from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)

# Minimum number of times a pattern must appear to be reported
_MIN_PATTERN_FREQ = 3
# Co-occurrence threshold above which a pair is flagged as a composition candidate
_COOCCURRENCE_THRESHOLD = 0.80
# Window sizes for N-gram mining
_WINDOW_SIZES = (3, 4, 5)
# How many days of audit history to consider
_LOOKBACK_DAYS = 30


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lookback_ts() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    return cutoff.isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ngrams(seq: list[str], n: int) -> list[tuple[str, ...]]:
    """Return all N-grams of length n from seq."""
    return [tuple(seq[i : i + n]) for i in range(len(seq) - n + 1)]


def _score_pattern(freq: int, session_count: int) -> float:
    """Compute a [0, 1] confidence score for a pattern.

    Higher frequency and broader spread across sessions → higher score.
    """
    freq_score = min(1.0, freq / 20.0)
    consistency = min(1.0, session_count / 10.0)
    return round(0.6 * freq_score + 0.4 * consistency, 3)


def _automation_potential(pattern: tuple[str, ...]) -> str:
    """Heuristic: rate automation potential based on event types present."""
    automated = {
        "code_generated", "test_executed", "test_passed",
        "security_scan", "compliance_check", "deployment_initiated",
        "agent_task_completed",
    }
    manual = {"approval_granted", "approval_denied", "decision_made"}
    hits_auto = sum(1 for e in pattern if e in automated)
    hits_manual = sum(1 for e in pattern if e in manual)
    if hits_manual:
        return "low"
    if hits_auto >= len(pattern) // 2:
        return "high"
    return "medium"


# ---------------------------------------------------------------------------
# Lens
# ---------------------------------------------------------------------------

class WorkflowPatternLens(BaseLens):
    """Mine frequent workflow patterns from audit_trail and kanban_tasks."""

    name = "workflow_pattern"
    description = (
        "Surfaces frequent sequential tool patterns, composition candidates, "
        "and self-healing opportunities from audit and kanban history"
    )

    # ── Phase 1: Analyze ──────────────────────────────────────────────────

    def analyze(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "sessions": {},          # session_id → ordered list of event_types
            "kanban_done": [],       # task rows with status=done
            "failed_then_done": [],  # (actor, action) pairs that failed then completed
        }

        try:
            conn = get_connection()
            self._load_audit_sequences(conn, data)
            self._load_kanban_done(conn, data)
            self._load_self_healing_candidates(conn, data)
            conn.close()
        except Exception as exc:
            logger.warning("WorkflowPatternLens.analyze error: %s", exc)

        return data

    def _load_audit_sequences(self, conn, data: dict) -> None:
        """Load audit_trail rows for last 30 days, grouped into sessions."""
        cutoff = _lookback_ts()
        rows = conn.execute(
            "SELECT event_type, actor, action, session_id, created_at "
            "FROM audit_trail "
            "WHERE created_at >= ? "
            "ORDER BY session_id NULLS LAST, created_at ASC",
            (cutoff,),
        ).fetchall()

        sessions: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            # Fall back to actor as session key when session_id is absent
            key = row["session_id"] or f"actor:{row['actor']}"
            sessions[key].append(row["event_type"])

        # Keep only sessions with enough events for N-gram mining
        data["sessions"] = {
            k: v for k, v in sessions.items() if len(v) >= min(_WINDOW_SIZES)
        }

    def _load_kanban_done(self, conn, data: dict) -> None:
        """Load completed kanban tasks."""
        rows = conn.execute(
            "SELECT id, title, task_type, priority, created_at, completed_at "
            "FROM kanban_tasks "
            "WHERE status = 'done' "
            "ORDER BY created_at DESC "
            "LIMIT 500",
        ).fetchall()
        data["kanban_done"] = [dict(r) for r in rows]

    def _load_self_healing_candidates(self, conn, data: dict) -> None:
        """Detect actor+action combos that appear under both failed and completed."""
        cutoff = _lookback_ts()

        failed = conn.execute(
            "SELECT actor, action FROM audit_trail "
            "WHERE event_type = 'agent_task_failed' AND created_at >= ?",
            (cutoff,),
        ).fetchall()
        failed_set = {(r["actor"], r["action"]) for r in failed}

        if not failed_set:
            return

        completed = conn.execute(
            "SELECT actor, action FROM audit_trail "
            "WHERE event_type = 'agent_task_completed' AND created_at >= ?",
            (cutoff,),
        ).fetchall()
        completed_set = {(r["actor"], r["action"]) for r in completed}

        # Pairs that appear in both failed and completed → self-healing candidates
        heal_candidates = []
        for actor, action in failed_set & completed_set:
            fail_count = sum(
                1 for r in failed if r["actor"] == actor and r["action"] == action
            )
            success_count = sum(
                1 for r in completed if r["actor"] == actor and r["action"] == action
            )
            heal_candidates.append(
                {
                    "actor": actor,
                    "action": action,
                    "fail_count": fail_count,
                    "success_count": success_count,
                    "heal_rate": round(
                        success_count / max(1, fail_count + success_count), 3
                    ),
                }
            )

        data["failed_then_done"] = sorted(
            heal_candidates, key=lambda x: x["fail_count"], reverse=True
        )

    # ── Phase 2: Score ────────────────────────────────────────────────────

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []

        predictions.extend(self._score_ngram_patterns(analysis))
        predictions.extend(self._score_cooccurrence_pairs(analysis))
        predictions.extend(self._score_kanban_task_patterns(analysis))
        predictions.extend(self._score_self_healing(analysis))

        # Sort by confidence descending
        predictions.sort(key=lambda p: p.confidence, reverse=True)
        return predictions

    def _score_ngram_patterns(
        self, analysis: dict[str, Any]
    ) -> list[OraclePrediction]:
        """Mine frequent 3–5 step sequential event patterns."""
        sessions = analysis.get("sessions", {})
        if not sessions:
            return []

        # Count: ngram_tuple → {count, sessions}
        ngram_counts: Counter[tuple[str, ...]] = Counter()
        ngram_session_sets: dict[tuple[str, ...], set[str]] = defaultdict(set)

        for session_id, events in sessions.items():
            seen_in_session: set[tuple[str, ...]] = set()
            for n in _WINDOW_SIZES:
                for gram in _ngrams(events, n):
                    ngram_counts[gram] += 1
                    if gram not in seen_in_session:
                        ngram_session_sets[gram].add(session_id)
                        seen_in_session.add(gram)

        predictions: list[OraclePrediction] = []
        for gram, freq in ngram_counts.most_common(50):
            if freq < _MIN_PATTERN_FREQ:
                break
            session_spread = len(ngram_session_sets[gram])
            conf = _score_pattern(freq, session_spread)
            auto_potential = _automation_potential(gram)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Frequent {len(gram)}-Step Pattern",
                    description=(
                        f"Sequence '{' → '.join(gram)}' appears {freq}× "
                        f"across {session_spread} session(s)"
                    ),
                    confidence=conf,
                    severity="info",
                    category="workflow_pattern",
                    data={
                        "pattern": list(gram),
                        "frequency": freq,
                        "session_spread": session_spread,
                        "automation_potential": auto_potential,
                    },
                )
            )

        return predictions

    def _score_cooccurrence_pairs(
        self, analysis: dict[str, Any]
    ) -> list[OraclePrediction]:
        """Detect tool pairs with >80% co-occurrence as composition candidates."""
        sessions = analysis.get("sessions", {})
        if not sessions:
            return []

        # session sets per event_type
        event_sessions: dict[str, set[str]] = defaultdict(set)
        for session_id, events in sessions.items():
            for evt in set(events):
                event_sessions[evt].add(session_id)

        # Count pair co-occurrences
        pair_counts: Counter[tuple[str, str]] = Counter()
        for session_id, events in sessions.items():
            unique_evts = sorted(set(events))
            for i in range(len(unique_evts)):
                for j in range(i + 1, len(unique_evts)):
                    pair_counts[(unique_evts[i], unique_evts[j])] += 1

        predictions: list[OraclePrediction] = []
        for (evt_a, evt_b), pair_freq in pair_counts.most_common(100):
            union = len(event_sessions[evt_a] | event_sessions[evt_b])
            if union == 0:
                continue
            cooccurrence_rate = pair_freq / union
            if cooccurrence_rate < _COOCCURRENCE_THRESHOLD:
                continue
            conf = min(0.95, 0.5 + cooccurrence_rate * 0.45)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Tool Composition Candidate: {evt_a} + {evt_b}",
                    description=(
                        f"'{evt_a}' and '{evt_b}' co-occur in "
                        f"{cooccurrence_rate:.0%} of sessions ({pair_freq}/{union})"
                    ),
                    confidence=round(conf, 3),
                    severity="info",
                    category="tool_composition_candidate",
                    data={
                        "tool_a": evt_a,
                        "tool_b": evt_b,
                        "cooccurrence_rate": round(cooccurrence_rate, 3),
                        "pair_frequency": pair_freq,
                        "union_sessions": union,
                    },
                )
            )

        return predictions

    def _score_kanban_task_patterns(
        self, analysis: dict[str, Any]
    ) -> list[OraclePrediction]:
        """Detect recurring task_type patterns among completed kanban tasks."""
        tasks = analysis.get("kanban_done", [])
        if len(tasks) < _MIN_PATTERN_FREQ:
            return []

        type_counts: Counter[str] = Counter(
            t.get("task_type", "unknown") for t in tasks
        )

        predictions: list[OraclePrediction] = []
        total = len(tasks)
        for task_type, count in type_counts.most_common(10):
            if count < _MIN_PATTERN_FREQ:
                continue
            rate = count / total
            conf = min(0.90, 0.4 + rate * 0.6)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Recurring Task Type: {task_type}",
                    description=(
                        f"Task type '{task_type}' accounts for {count}/{total} "
                        f"({rate:.0%}) of completed kanban tasks"
                    ),
                    confidence=round(conf, 3),
                    severity="info",
                    category="recurring_task_type",
                    data={
                        "task_type": task_type,
                        "count": count,
                        "total_done": total,
                        "rate": round(rate, 3),
                    },
                )
            )

        return predictions

    def _score_self_healing(
        self, analysis: dict[str, Any]
    ) -> list[OraclePrediction]:
        """Surface actor+action combos that fail then eventually succeed."""
        candidates = analysis.get("failed_then_done", [])
        predictions: list[OraclePrediction] = []

        for c in candidates:
            if c["fail_count"] < 2:
                continue
            # Higher heal rate → higher confidence this is a recoverable pattern
            conf = min(0.90, 0.4 + c["heal_rate"] * 0.5)
            severity = "warning" if c["fail_count"] >= 5 else "info"
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Self-Healing Candidate: {c['actor']} / {c['action']!r}",
                    description=(
                        f"'{c['actor']}' failed {c['fail_count']}× then succeeded "
                        f"{c['success_count']}× for action '{c['action']}' "
                        f"(heal rate {c['heal_rate']:.0%})"
                    ),
                    confidence=round(conf, 3),
                    severity=severity,
                    category="self_healing_candidate",
                    data=c,
                )
            )

        return predictions

    # ── Phase 3: Propose ──────────────────────────────────────────────────

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Enrich each prediction with actionable recommendations."""
        for pred in predictions:
            if pred.category == "workflow_pattern":
                pattern_str = " → ".join(pred.data.get("pattern", []))
                auto = pred.data.get("automation_potential", "medium")
                pred.recommendations = [
                    f"Template this workflow: '{pattern_str}'",
                    "Add as a reusable goal in goals/manifest.md",
                    f"Automation potential: {auto} — "
                    + (
                        "consider adding to Genesis reflexes"
                        if auto == "high"
                        else "review for semi-automation"
                    ),
                    "Cross-reference with tools/manifest.md to identify missing helpers",
                ]

            elif pred.category == "tool_composition_candidate":
                a = pred.data.get("tool_a", "")
                b = pred.data.get("tool_b", "")
                pred.recommendations = [
                    f"Create a composite tool that runs '{a}' then '{b}' atomically",
                    "Check tools/manifest.md for an existing wrapper before building",
                    "Add the composite call as a goal step to reduce manual chaining",
                    f"Rate: {pred.data.get('cooccurrence_rate', 0):.0%} co-occurrence — "
                    "strong signal for a dedicated pipeline step",
                ]

            elif pred.category == "recurring_task_type":
                task_type = pred.data.get("task_type", "")
                pred.recommendations = [
                    f"Create a standard goal template for '{task_type}' tasks",
                    "Add acceptance criteria checklist to kanban card template",
                    "Consider scheduling recurring tasks in kanban_scheduler.py",
                    "Review common failure modes for this task type in audit_trail",
                ]

            elif pred.category == "self_healing_candidate":
                actor = pred.data.get("actor", "")
                action = pred.data.get("action", "")
                heal_rate = pred.data.get("heal_rate", 0)
                pred.recommendations = [
                    f"Add retry logic for actor='{actor}', action='{action}'",
                    "Register in Genesis Heal reflex if heal rate ≥ 0.7"
                    if heal_rate >= 0.7
                    else "Monitor: heal rate below 0.7, manual review recommended",
                    "Investigate root cause of initial failures in audit_trail",
                    "python tools/genesis/reflexes/heal.py --actor "
                    + actor
                    + " --dry-run",
                ]

        return predictions


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    lens = WorkflowPatternLens()
    preds = lens.run()
    print(json.dumps([p.to_dict() for p in preds], indent=2, default=str))
    print(f"\n# {len(preds)} prediction(s) generated", file=sys.stderr)
