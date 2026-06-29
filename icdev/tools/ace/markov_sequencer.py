from __future__ import annotations

# CUI // SP-CTI
"""Markov step sequencer — learns step-to-step transition probabilities from ACE history.

Reads historical step execution records from ace_step_transitions table, builds a
Markov transition matrix, and reorders candidate next steps by learned probability.

Inspired by the VanillaMarkov module in DeepSpec (deepseek-ai/DeepSpec), which adds
a low-rank learned bigram bias to draft model logits. Here the same pattern is applied
to ACE workflow steps instead of vocabulary tokens.

Usage:
    seq = MarkovSequencer(role_id="ai_developer")
    seq.record_transition("write_tests", "implement_code", success=True)
    probs = seq.get_transition_probs("write_tests")
    # {"implement_code": 0.72, "refactor": 0.18, "run_tests": 0.10}
    best_next = seq.recommend_next("write_tests", ["run_tests", "implement_code"])
    # ["implement_code", "run_tests"]  (reordered by probability)
"""

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_CACHE_TTL = 300  # 5 minutes
_GREEDY_MAX_HOPS = 10


# ---------------------------------------------------------------------------
# TransitionMatrix
# ---------------------------------------------------------------------------


@dataclass
class TransitionMatrix:
    """Immutable snapshot of observed step-to-step transition counts for one role."""

    role_id: str
    counts: dict[str, dict[str, int]]  # from_step → {to_step → count}
    built_at: str  # ISO8601

    def get_probs(self, from_step: str) -> dict[str, float]:
        """Return probability distribution over next steps given from_step."""
        row = self.counts.get(from_step)
        if not row:
            return {}
        total = sum(row.values())
        if total == 0:
            return {}
        return {step: count / total for step, count in row.items()}

    def recommend_next(self, from_step: str, candidates: list[str]) -> list[str]:
        """Reorder candidates by transition probability (highest first). Unknown steps go last."""
        probs = self.get_probs(from_step)
        if not probs:
            return list(candidates)
        known = [c for c in candidates if c in probs]
        unknown = [c for c in candidates if c not in probs]
        known_sorted = sorted(known, key=lambda c: probs[c], reverse=True)
        return known_sorted + unknown


# ---------------------------------------------------------------------------
# MarkovSequencer
# ---------------------------------------------------------------------------


class MarkovSequencer:
    """Learns and applies step-to-step transition probabilities for an ACE role."""

    def __init__(
        self,
        role_id: str,
        conn: Any = None,
        min_samples: int = 5,
    ) -> None:
        self.role_id = role_id
        self._conn = conn
        self.min_samples = min_samples
        self._matrix_cache: TransitionMatrix | None = None
        self._cache_built_at: float = 0.0

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create ace_step_transitions table if not exists (idempotent)."""
        ddl = (
            "CREATE TABLE IF NOT EXISTS ace_step_transitions ("
            "id BIGSERIAL PRIMARY KEY, "
            "role_id TEXT NOT NULL, "
            "from_step TEXT NOT NULL, "
            "to_step TEXT NOT NULL, "
            "success BOOLEAN NOT NULL DEFAULT TRUE, "
            "session_id TEXT, "
            "tenant_id TEXT, "
            "created_at TIMESTAMPTZ DEFAULT NOW()"
            ")"
        )
        idx1 = (
            "CREATE INDEX IF NOT EXISTS idx_ace_step_trans_role "
            "ON ace_step_transitions(role_id, from_step)"
        )
        idx2 = (
            "CREATE INDEX IF NOT EXISTS idx_ace_step_trans_session "
            "ON ace_step_transitions(session_id)"
        )
        conn = self._get_conn()
        try:
            conn.execute(ddl)
            conn.execute(idx1)
            conn.execute(idx2)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_transition(
        self,
        from_step: str,
        to_step: str,
        *,
        success: bool = True,
        session_id: str = "",
        tenant_id: str = "",
    ) -> None:
        """Insert one step-to-step transition record."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO ace_step_transitions "
                "(role_id, from_step, to_step, success, session_id, tenant_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    self.role_id,
                    from_step,
                    to_step,
                    success,
                    session_id or None,
                    tenant_id or None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        # Invalidate cache on new data
        self._matrix_cache = None

    # ------------------------------------------------------------------
    # Matrix
    # ------------------------------------------------------------------

    def build_matrix(self) -> TransitionMatrix:
        """Build transition matrix from successful transition history for this role."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT from_step, to_step, COUNT(*) AS cnt "
                "FROM ace_step_transitions "
                "WHERE role_id = %s AND success = TRUE "
                "GROUP BY from_step, to_step",
                (self.role_id,),
            ).fetchall()
        finally:
            conn.close()

        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            if isinstance(row, dict):
                from_s = row["from_step"]
                to_s = row["to_step"]
                cnt = int(row["cnt"])
            else:
                from_s, to_s, cnt = row[0], row[1], int(row[2])
            counts.setdefault(from_s, {})[to_s] = cnt

        return TransitionMatrix(
            role_id=self.role_id,
            counts=counts,
            built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _get_matrix(self) -> TransitionMatrix:
        """Return cached matrix, rebuilding if stale (5-minute TTL)."""
        now = time.monotonic()
        if self._matrix_cache is None or (now - self._cache_built_at) > _CACHE_TTL:
            self._matrix_cache = self.build_matrix()
            self._cache_built_at = now
        return self._matrix_cache

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def get_transition_probs(self, from_step: str) -> dict[str, float]:
        """Return probability dict from from_step; empty if fewer than min_samples seen."""
        matrix = self._get_matrix()
        total_transitions = sum(sum(row.values()) for row in matrix.counts.values())
        if total_transitions < self.min_samples:
            return {}
        return matrix.get_probs(from_step)

    def recommend_next(self, from_step: str, candidates: list[str]) -> list[str]:
        """Return candidates reordered by descending probability; unchanged if no history."""
        probs = self.get_transition_probs(from_step)
        if not probs:
            return list(candidates)
        matrix = self._get_matrix()
        return matrix.recommend_next(from_step, candidates)

    def entropy(self, from_step: str, candidates: list[str] | None = None) -> float:
        """Shannon entropy of transition distribution from from_step.

        Returns max entropy (log2 of candidate count) when no data available.
        """
        probs = self.get_transition_probs(from_step)
        if not probs:
            n = len(candidates) if candidates else 2
            return math.log2(max(n, 2))
        return -sum(p * math.log2(p) for p in probs.values() if p > 0)

    def top_sequences(self, n: int = 5) -> list[list[str]]:
        """Return the N most-common step sequences by greedy highest-probability traversal."""
        matrix = self._get_matrix()
        if not matrix.counts:
            return []

        # Rank starting steps by total outgoing transitions (most-traversed first)
        start_steps = sorted(
            matrix.counts.keys(),
            key=lambda s: sum(matrix.counts[s].values()),
            reverse=True,
        )

        sequences: list[list[str]] = []
        seen: set[str] = set()

        for start in start_steps:
            if len(sequences) >= n:
                break
            seq: list[str] = [start]
            current = start
            for _ in range(_GREEDY_MAX_HOPS - 1):
                row = matrix.counts.get(current)
                if not row:
                    break
                next_step = max(row, key=lambda s: row[s])
                seq.append(next_step)
                current = next_step
                if not matrix.counts.get(current):
                    break
            seq_key = "->".join(seq)
            if seq_key not in seen:
                seen.add(seq_key)
                sequences.append(seq)

        return sequences[:n]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_conn(self) -> Any:
        if self._conn is not None:
            return self._conn
        from icdev.tools.db.storage import get_connection

        return get_connection()


# ---------------------------------------------------------------------------
# patch_role_template
# ---------------------------------------------------------------------------


def patch_role_template(template: Any, sequencer: MarkovSequencer) -> Any:
    """Return a copy of RoleTemplate with steps reordered by Markov probability.

    Only reorders when the sequencer has sufficient history for the first step.
    Returns the original template unchanged if insufficient history.
    """
    steps = list(getattr(template, "steps", []))
    if len(steps) < 2:
        return template

    step1_name = getattr(steps[0], "name", "")
    if not sequencer.get_transition_probs(step1_name):
        return template

    # Greedy chain reordering: from each chosen step, pick best remaining next
    ordered = [steps[0]]
    remaining = list(steps[1:])
    current = step1_name

    while remaining:
        candidate_names = [getattr(s, "name", "") for s in remaining]
        reordered_names = sequencer.recommend_next(current, candidate_names)
        next_name = reordered_names[0] if reordered_names else ""
        next_step = next(
            (s for s in remaining if getattr(s, "name", "") == next_name),
            remaining[0],
        )
        ordered.append(next_step)
        remaining = [s for s in remaining if s is not next_step]
        current = getattr(next_step, "name", "")

    # Build a shallow copy of the template dataclass with updated steps
    import dataclasses

    return dataclasses.replace(template, steps=ordered)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_sequencer(role_id: str) -> MarkovSequencer:
    """Create a MarkovSequencer for role_id and ensure the DB schema exists."""
    seq = MarkovSequencer(role_id=role_id)
    try:
        seq.ensure_schema()
    except Exception:
        pass  # non-critical — table may already exist or DB may be unavailable
    return seq


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="ACE Markov step sequencer CLI")
    parser.add_argument("--role", required=True, help="role_id to query")
    parser.add_argument("--probs", metavar="FROM_STEP", help="print transition probs from a step")
    parser.add_argument("--top-sequences", action="store_true", help="print top 5 sequences")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    seq = get_sequencer(args.role)

    if args.probs:
        result: Any = seq.get_transition_probs(args.probs)
        if args.as_json:
            print(json.dumps({"from_step": args.probs, "probs": result}))
        else:
            if result:
                for step, prob in sorted(result.items(), key=lambda x: -x[1]):
                    print(f"  {step}: {prob:.3f}")
            else:
                print(f"  (no data for step={args.probs!r})")
    elif args.top_sequences:
        sequences = seq.top_sequences(5)
        if args.as_json:
            print(json.dumps({"role_id": args.role, "sequences": sequences}))
        else:
            print(f"Top sequences for role={args.role!r}:")
            for i, s in enumerate(sequences, 1):
                print(f"  {i}. {' -> '.join(s)}")
    else:
        parser.print_help()
        sys.exit(1)
