#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Synthesize Reflex — auto-generate goal workflows from observed patterns.

Mines tool execution telemetry (hook_events, ai_telemetry) for recurring
multi-tool sequences, generates FORGE-compliant goal drafts, and stages
them as GKP artifacts for human review.

YELLOW tier (writes goal drafts to staging, never activates autonomously).
Scanner-tier LLM (qwen3.5) for optional polish — zero Claude tokens.

Key design decisions:
  - D-SYN-1: YELLOW tier — human review required before any goal activates
  - D-SYN-2: Confidence 0.55 — always below auto-promote threshold
  - D-SYN-3: Max 3 goals per run — prevent flooding review queue
  - D-SYN-4: Deduplication via chain_hash in genesis_tool_patterns
"""
IMPLEMENTATION_STATUS = "full"

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_reflex_config() -> Dict[str, Any]:
    """Load synthesize reflex config from genesis_config.yaml."""
    config_path = BASE_DIR / "args" / "genesis_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config.get("reflexes", {}).get("synthesize", {})
    except Exception:
        return {}


def run(config: Dict = None, trust: Any = None, **kwargs) -> Dict[str, Any]:
    """Execute the Synthesize reflex.

    Called by Genesis daemon positionally as ``run(config, trust)`` — mirror
    that signature. The keyword-only form was the root cause of breaker
    trips on 2026-03-28 (``run() takes 0 positional arguments but 2 were
    given``). ``trust`` is accepted for API parity; this reflex doesn't
    consume it.

    Pipeline:
      1. Detect recurring tool-chain patterns (pattern_detector)
      2. Filter patterns not yet converted to goals
      3. Generate FORGE goal drafts (goal_template_generator)
      4. Create GKP artifacts with promotion_status='pending_review'

    Returns:
        Dict with results for daemon metric evaluation.
    """
    if config is None:
        config = _load_reflex_config()

    min_frequency = config.get("min_pattern_frequency", 3)
    min_chain_length = config.get("min_chain_length", 3)
    lookback_days = config.get("lookback_days", 7)
    max_goals = config.get("max_goals_per_run", 3)

    result: Dict[str, Any] = {
        "reflex": "synthesize",
        "started_at": _utcnow_iso(),
        "patterns_detected": 0,
        "goals_generated": 0,
        "gkp_artifacts_created": 0,
        "errors": [],
    }

    # Step 1: Detect patterns
    try:
        from tools.genesis.pattern_detector import detect_tool_patterns, store_patterns

        detection = detect_tool_patterns(
            min_frequency=min_frequency,
            min_chain_length=min_chain_length,
            lookback_days=lookback_days,
            top_k=max_goals * 2,  # Detect more than needed for filtering
        )

        patterns = detection.get("patterns", [])
        result["patterns_detected"] = len(patterns)
        result["sessions_analyzed"] = detection.get("sessions_analyzed", 0)
        result["chains_extracted"] = detection.get("chains_extracted", 0)

        if patterns:
            stored = store_patterns(patterns)
            result["patterns_stored"] = stored

    except Exception as exc:
        result["errors"].append(f"pattern_detection: {exc}")
        result["completed_at"] = _utcnow_iso()
        result["success"] = False
        result["metric_value"] = 0.0
        return result

    if not patterns:
        result["message"] = "No recurring patterns found"
        result["completed_at"] = _utcnow_iso()
        result["patterns_detected"] = 0
        result["success"] = True
        result["metric_value"] = 0.0
        return result

    # Step 2: Generate goal drafts
    try:
        from tools.genesis.goal_template_generator import (
            generate_goal_from_pattern,
            polish_with_llm,
        )

        goals = []
        confab_flags = 0
        for pattern in patterns[:max_goals]:
            goal = generate_goal_from_pattern(pattern, lookback_days=lookback_days)
            goal = polish_with_llm(goal)

            # NIST AI 600-1 GAI.1: check generated content for confabulation risk
            goal_text = goal.get("goal_markdown", "") or goal.get("llm_description", "")
            if goal_text:
                try:
                    from tools.security.confabulation_detector import check_output as _confab_check
                    confab = _confab_check("genesis-synthesize", goal_text)
                    goal["confabulation_risk"] = confab.get("risk_score", 0.0)
                    goal["confabulation_findings"] = confab.get("findings_count", 0)
                    if confab.get("risk_score", 0.0) > 0.6:
                        goal["confabulation_flagged"] = True
                        confab_flags += 1
                        try:
                            from tools.canvas.ai_trace_mixin import record_canvas_decision as _rcd
                            _rcd(
                                canvas_type="genesis",
                                decision_type="confabulation_flag",
                                decision=f"High confabulation risk ({confab['risk_score']:.2f}) in synthesized goal",
                                rationale=f"{confab['findings_count']} finding(s): {confab.get('risk_level', 'high')}",
                                confidence=confab["risk_score"],
                                project_id="icdev",
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

            goals.append(goal)

        result["goals_generated"] = len(goals)
        result["confabulation_flags"] = confab_flags

    except Exception as exc:
        result["errors"].append(f"goal_generation: {exc}")
        result["completed_at"] = _utcnow_iso()
        result["success"] = False
        result["metric_value"] = 0.0
        return result

    # Step 3: Create GKP artifacts
    conn = get_connection()
    try:
        for goal in goals:
            gkp_id = f"gkp-syn-{hashlib.sha256(json.dumps(goal['tool_chain']).encode()).hexdigest()[:12]}"

            # Check for duplicate GKP
            existing = conn.execute("SELECT id FROM genesis_gkp WHERE id = %s", (gkp_id,)).fetchone()
            if existing:
                continue

            payload = {
                "title": goal["title"],
                "tool_chain": goal["tool_chain"],
                "goal_markdown": goal["goal_markdown"],
                "frequency": goal["frequency"],
                "caller_diversity": goal["caller_diversity"],
                "composite_score": goal["composite_score"],
                "llm_polished": goal.get("llm_polished", False),
                "llm_description": goal.get("llm_description", ""),
            }

            # Compute SHA256 of payload for dedup
            payload_json = json.dumps(payload, sort_keys=True)
            sha = hashlib.sha256(payload_json.encode()).hexdigest()

            conn.execute(
                """
                INSERT INTO genesis_gkp
                    (id, gkp_version, artifact_type, genesis_reflex,
                     confidence, evidence, payload, sha256,
                     promotion_status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    gkp_id,
                    "2.0",
                    "suggested_goal",
                    "synthesize",
                    goal["confidence"],
                    json.dumps(
                        {
                            "pattern_id": goal["pattern_id"],
                            "frequency": goal["frequency"],
                            "diversity": goal["caller_diversity"],
                        }
                    ),
                    payload_json,
                    sha,
                    "pending_review",
                    _utcnow_iso(),
                ),
            )
            result["gkp_artifacts_created"] = result.get("gkp_artifacts_created", 0) + 1

        conn.commit()
    except Exception as exc:
        conn.rollback()
        result["errors"].append(f"gkp_creation: {exc}")
    finally:
        conn.close()

    result["completed_at"] = _utcnow_iso()
    result["success"] = len(result.get("errors", [])) == 0
    result["metric_value"] = float(result.get("patterns_detected", 0))
    return result


# ---------------------------------------------------------------------------
# CLI (for manual testing)
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Genesis Synthesize Reflex — generate goals from tool patterns")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument("--min-frequency", type=int, default=None)
    parser.add_argument("--max-goals", type=int, default=None)
    args = parser.parse_args()

    config = _load_reflex_config()
    if args.lookback_days:
        config["lookback_days"] = args.lookback_days
    if args.min_frequency:
        config["min_pattern_frequency"] = args.min_frequency
    if args.max_goals:
        config["max_goals_per_run"] = args.max_goals

    result = run(config=config)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Synthesize Reflex ({result.get('started_at', '')})")
        print(f"  Patterns detected:    {result.get('patterns_detected', 0)}")
        print(f"  Goals generated:      {result.get('goals_generated', 0)}")
        print(f"  GKP artifacts created: {result.get('gkp_artifacts_created', 0)}")
        if result.get("errors"):
            print(f"  Errors: {result['errors']}")


if __name__ == "__main__":
    main()
