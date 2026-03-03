#!/usr/bin/env python3
# CUI // SP-CTI
"""Agent Trust Scorer — dynamic inter-agent trust scoring (D260).

Computes and tracks agent trust scores using exponential decay for
violations (vetoes, anomalies, chain violations, output violations)
and linear recovery for clean behavior. Trust levels determine
autonomous action eligibility.

Pattern: tools/security/ai_telemetry_logger.py (DB reads, append-only writes, CLI)
ADRs: D260 (trust scoring with exponential decay), D6 (append-only score history)

Trust Levels:
    - normal (>= 0.70): Full autonomous operation
    - degraded (>= 0.50): Require HITL confirmation
    - untrusted (>= 0.30): Block autonomous actions
    - blocked (< 0.30): All actions blocked

CLI:
    python tools/security/agent_trust_scorer.py --score --agent-id agent-1 --json
    python tools/security/agent_trust_scorer.py --check --agent-id agent-1 --json
    python tools/security/agent_trust_scorer.py --history --agent-id agent-1 --json
    python tools/security/agent_trust_scorer.py --all --json
    python tools/security/agent_trust_scorer.py --gate --project-id proj-123 --json
"""

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "owasp_agentic_config.yaml"


def _load_config() -> Dict:
    """Load trust scoring config from YAML."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("trust_scoring", {})
    return {}


class AgentTrustScorer:
    """Dynamic trust scoring with decay and recovery (D260).

    Queries signal sources (vetoes, anomalies, chain violations,
    output violations) to compute per-agent trust scores. Scores
    are stored append-only in agent_trust_scores (D6).
    """

    def __init__(self, db_path: Optional[Path] = None, config: Optional[Dict] = None):
        self._db_path = db_path or DB_PATH
        self._config = config or _load_config()
        self._initial = self._config.get("initial_score", 0.85)
        self._min = self._config.get("min_score", 0.0)
        self._max = self._config.get("max_score", 1.0)
        self._decay = self._config.get("decay_factors", {})
        self._recovery = self._config.get("recovery", {})
        self._thresholds = self._config.get("thresholds", {})

    def compute_score(
        self,
        agent_id: str,
        project_id: Optional[str] = None,
        window_hours: int = 24,
    ) -> Dict:
        """Compute trust score for an agent based on recent signals.

        Applies decay for negative events and recovery for clean periods.

        Returns:
            Dict with trust_score, trust_level, factors, previous_score.
        """
        previous = self.get_current_score(agent_id)
        base_score = previous if previous is not None else self._initial

        factors = {}
        total_decay = 0.0

        if not self._db_path.exists():
            return self._build_result(agent_id, base_score, base_score, factors, project_id)

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

        try:
            conn = sqlite3.connect(str(self._db_path))

            # Factor 1: Hard vetoes (agent_vetoes table)
            hard_vetoes = self._count_events(
                conn, "agent_vetoes", "vetoed_agent_id", agent_id,
                "veto_type = 'hard'", cutoff
            )
            if hard_vetoes > 0:
                decay = hard_vetoes * self._decay.get("veto_hard", -0.15)
                factors["veto_hard"] = {"count": hard_vetoes, "decay": decay}
                total_decay += decay

            # Factor 2: Soft vetoes
            soft_vetoes = self._count_events(
                conn, "agent_vetoes", "vetoed_agent_id", agent_id,
                "veto_type = 'soft'", cutoff
            )
            if soft_vetoes > 0:
                decay = soft_vetoes * self._decay.get("veto_soft", -0.05)
                factors["veto_soft"] = {"count": soft_vetoes, "decay": decay}
                total_decay += decay

            # Factor 3: Tool chain violations
            chain_critical = self._count_events(
                conn, "tool_chain_events", "agent_id", agent_id,
                "severity = 'critical'", cutoff
            )
            chain_high = self._count_events(
                conn, "tool_chain_events", "agent_id", agent_id,
                "severity = 'high'", cutoff
            )
            chain_count = chain_critical + chain_high
            if chain_count > 0:
                decay = chain_count * self._decay.get("tool_chain_violation", -0.12)
                factors["tool_chain_violation"] = {"count": chain_count, "decay": decay}
                total_decay += decay

            # Factor 4: Output violations by severity
            for sev, key in [("critical", "output_violation_critical"),
                             ("high", "output_violation_high"),
                             ("medium", "output_violation_medium")]:
                output_count = self._count_events(
                    conn, "agent_output_violations", "agent_id", agent_id,
                    f"severity = '{sev}'", cutoff
                )
                if output_count > 0:
                    decay = output_count * self._decay.get(key, -0.05)
                    factors[key] = {"count": output_count, "decay": decay}
                    total_decay += decay

            # Recovery: bonus for clean check intervals
            recovery_bonus = 0.0
            if total_decay == 0 and previous is not None and previous < self._initial:
                clean_bonus = self._recovery.get("clean_check_bonus", 0.02)
                max_daily = self._recovery.get("max_recovery_per_day", 0.10)
                recovery_bonus = min(clean_bonus, max_daily)
                factors["recovery"] = {"bonus": recovery_bonus}

            conn.close()
        except Exception:
            pass

        new_score = max(self._min, min(self._max, base_score + total_decay + recovery_bonus))

        # Store the score
        self._store_score(agent_id, new_score, base_score, factors, project_id)

        return self._build_result(agent_id, new_score, base_score, factors, project_id)

    def get_current_score(self, agent_id: str) -> Optional[float]:
        """Get the latest trust score for an agent from DB."""
        if not self._db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(self._db_path))
            row = conn.execute(
                "SELECT trust_score FROM agent_trust_scores "
                "WHERE agent_id = ? ORDER BY created_at DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def get_trust_level(self, score: float) -> str:
        """Map numeric score to trust level."""
        normal = self._thresholds.get("normal", 0.70)
        degraded = self._thresholds.get("degraded", 0.50)
        untrusted = self._thresholds.get("untrusted", 0.30)

        if score >= normal:
            return "normal"
        elif score >= degraded:
            return "degraded"
        elif score >= untrusted:
            return "untrusted"
        else:
            return "blocked"

    def evaluate_agent_access(self, agent_id: str, action_type: str = "autonomous") -> Dict:
        """Evaluate whether an agent should be allowed to act.

        Returns:
            Dict with allowed bool, trust_level, trust_score, reason.
        """
        score = self.get_current_score(agent_id)
        if score is None:
            score = self._initial

        level = self.get_trust_level(score)

        if level == "normal":
            return {"allowed": True, "trust_level": level, "trust_score": score,
                    "reason": "Agent has normal trust — autonomous actions permitted"}
        elif level == "degraded":
            return {"allowed": action_type != "autonomous", "trust_level": level,
                    "trust_score": score,
                    "reason": "Agent trust degraded — HITL confirmation required"}
        elif level == "untrusted":
            return {"allowed": False, "trust_level": level, "trust_score": score,
                    "reason": "Agent untrusted — autonomous actions blocked"}
        else:
            return {"allowed": False, "trust_level": level, "trust_score": score,
                    "reason": "Agent trust below minimum — all actions blocked"}

    def get_score_history(self, agent_id: str, limit: int = 20) -> List[Dict]:
        """Get recent trust score history for an agent."""
        if not self._db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT trust_score, previous_score, score_delta, factor_json, "
                "trigger_event, created_at FROM agent_trust_scores "
                "WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            conn.close()
            return [
                {
                    "trust_score": r[0],
                    "previous_score": r[1],
                    "delta": r[2],
                    "factors": json.loads(r[3]) if r[3] else {},
                    "trigger": r[4],
                    "timestamp": r[5],
                }
                for r in rows
            ]
        except Exception:
            return []

    def get_all_scores(self) -> List[Dict]:
        """Get current trust scores for all known agents."""
        if not self._db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT agent_id, trust_score, created_at FROM agent_trust_scores "
                "WHERE (agent_id, created_at) IN ("
                "  SELECT agent_id, MAX(created_at) FROM agent_trust_scores GROUP BY agent_id"
                ") ORDER BY trust_score ASC"
            ).fetchall()
            conn.close()
            return [
                {
                    "agent_id": r[0],
                    "trust_score": r[1],
                    "trust_level": self.get_trust_level(r[1]),
                    "last_updated": r[2],
                }
                for r in rows
            ]
        except Exception:
            return []

    def evaluate_gate(self, project_id: Optional[str] = None) -> Dict:
        """Evaluate security gate for trust scoring.

        Returns:
            Dict with pass/fail, blocking conditions.
        """
        result = {
            "gate": "owasp_agentic_trust",
            "passed": True,
            "blocking": [],
            "warnings": [],
        }

        if not self._db_path.exists():
            result["warnings"].append("Database not found")
            return result

        try:
            conn = sqlite3.connect(str(self._db_path))
            untrusted_threshold = self._thresholds.get("untrusted", 0.30)

            # Check for any agent below untrusted threshold
            where = "1=1"
            params: list = []
            if project_id:
                where = "project_id = ?"
                params = [project_id]

            rows = conn.execute(
                f"SELECT agent_id, trust_score FROM agent_trust_scores "
                f"WHERE {where} AND (agent_id, created_at) IN ("
                f"  SELECT agent_id, MAX(created_at) FROM agent_trust_scores "
                f"  WHERE {where} GROUP BY agent_id"
                f")",
                params + params,
            ).fetchall()

            for agent_id, score in rows:
                if score < untrusted_threshold:
                    result["passed"] = False
                    result["blocking"].append(
                        f"Agent '{agent_id}' trust score {score:.2f} below untrusted threshold {untrusted_threshold}"
                    )
                elif score < self._thresholds.get("degraded", 0.50):
                    result["warnings"].append(
                        f"Agent '{agent_id}' trust score {score:.2f} in degraded state"
                    )

            conn.close()
        except Exception as e:
            result["warnings"].append(f"Gate evaluation error: {str(e)}")

        return result

    def _count_events(
        self, conn: sqlite3.Connection, table: str, agent_col: str,
        agent_id: str, condition: str, cutoff: str,
    ) -> int:
        """Count events in a table for an agent since cutoff."""
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {agent_col} = ? AND {condition} AND created_at >= ?",
                (agent_id, cutoff),
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def _store_score(
        self, agent_id: str, score: float, previous: float,
        factors: Dict, project_id: Optional[str],
    ) -> Optional[str]:
        """Store trust score (append-only, D6)."""
        if not self._db_path.exists():
            return None

        entry_id = str(uuid.uuid4())
        delta = round(score - previous, 6)

        # Determine trigger event
        if factors:
            trigger = ", ".join(factors.keys())
        else:
            trigger = "scheduled_check"

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO agent_trust_scores
                   (id, agent_id, project_id, trust_score, previous_score,
                    score_delta, factor_json, trigger_event, classification, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CUI', ?)""",
                (
                    entry_id, agent_id, project_id,
                    round(score, 6), round(previous, 6), delta,
                    json.dumps(factors), trigger,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            return entry_id
        except Exception:
            return None

    def _build_result(
        self, agent_id: str, score: float, previous: float,
        factors: Dict, project_id: Optional[str],
    ) -> Dict:
        """Build standardized result dict."""
        level = self.get_trust_level(score)
        return {
            "agent_id": agent_id,
            "trust_score": round(score, 4),
            "previous_score": round(previous, 4),
            "delta": round(score - previous, 4),
            "trust_level": level,
            "factors": factors,
            "thresholds": self._thresholds,
            "project_id": project_id,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Agent Trust Scorer — dynamic trust scoring (D260)"
    )
    parser.add_argument("--score", action="store_true", help="Compute trust score")
    parser.add_argument("--check", action="store_true", help="Check agent access eligibility")
    parser.add_argument("--history", action="store_true", help="Show score history")
    parser.add_argument("--all", action="store_true", help="Show all agent scores")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gate")
    parser.add_argument("--agent-id", help="Agent ID")
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    scorer = AgentTrustScorer()

    if args.score:
        if not args.agent_id:
            print("Error: --score requires --agent-id", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = scorer.compute_score(args.agent_id, project_id=args.project_id)
    elif args.check:
        if not args.agent_id:
            print("Error: --check requires --agent-id", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = scorer.evaluate_agent_access(args.agent_id)
    elif args.history:
        if not args.agent_id:
            print("Error: --history requires --agent-id", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = {"agent_id": args.agent_id, "history": scorer.get_score_history(args.agent_id)}
    elif args.all:
        result = {"agents": scorer.get_all_scores()}
    elif args.gate:
        result = scorer.evaluate_gate(project_id=args.project_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.score:
            print(f"Agent: {result['agent_id']}")
            print(f"  Trust Score: {result['trust_score']:.4f} ({result['trust_level']})")
            print(f"  Previous: {result['previous_score']:.4f} (delta: {result['delta']:+.4f})")
            for k, v in result.get("factors", {}).items():
                print(f"  Factor: {k} = {v}")
        elif args.check:
            allowed = "ALLOWED" if result["allowed"] else "DENIED"
            print(f"Agent: {result.get('agent_id', args.agent_id)} — {allowed}")
            print(f"  Trust: {result['trust_score']:.4f} ({result['trust_level']})")
            print(f"  Reason: {result['reason']}")
        elif args.history:
            for h in result.get("history", []):
                print(f"  {h['timestamp']}: {h['trust_score']:.4f} (delta: {h['delta']:+.4f}) — {h['trigger']}")
        elif args.all:
            for a in result.get("agents", []):
                print(f"  {a['agent_id']}: {a['trust_score']:.4f} ({a['trust_level']})")
        elif args.gate:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"Trust Gate: {status}")
            for b in result.get("blocking", []):
                print(f"  [BLOCK] {b}")
            for w in result.get("warnings", []):
                print(f"  [WARN] {w}")


if __name__ == "__main__":
    main()
