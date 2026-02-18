#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Agentic Fitness Assessor - evaluates component fitness for agentic architecture.

Scores components across 6 dimensions to determine whether they should use:
- Full agent architecture (score >= 6.0)
- NLQ/conversational interface (user_interaction >= 5.0)
- Traditional REST/CRUD (score < 4.0)
- Hybrid approach (in between)

Decision D22: Weighted rule-based with optional LLM override.

CLI: python tools/builder/agentic_fitness.py --spec "..." --project-id "proj-123" --json
"""

import argparse
import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "agentic_fitness.yaml"
PROMPT_PATH = BASE_DIR / "hardprompts" / "agent" / "fitness_evaluation.md"

logger = logging.getLogger("icdev.agentic_fitness")

try:
    import yaml
except ImportError:
    yaml = None

try:
    from tools.agent.bedrock_client import BedrockClient, BedrockRequest
    _BEDROCK_AVAILABLE = True
except ImportError:
    _BEDROCK_AVAILABLE = False
    BedrockClient = None
    BedrockRequest = None

try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable")


# ============================================================
# KEYWORD BANKS — drive rule-based scoring per dimension
# ============================================================

KEYWORD_BANKS: Dict[str, Dict[str, float]] = {
    "data_complexity": {
        "crud": 1.0, "key-value": 1.0, "flat": 1.0, "single table": 1.5,
        "relational": 2.0, "join": 2.0, "foreign key": 2.0, "index": 1.5,
        "versioning": 2.5, "search": 1.5, "schema": 1.5,
        "graph": 3.0, "event-sourcing": 3.5, "cqrs": 3.5, "event sourcing": 3.5,
        "unstructured": 3.0, "multi-tenant": 3.0, "sharding": 3.5,
        "time-series": 3.0, "geospatial": 3.0, "document store": 2.5,
        "polyglot": 3.0, "data lake": 3.0, "streaming": 3.0,
        # Cross-dimension: compliance implies audit data, queries imply data access
        "compliance": 2.0, "queries": 2.0, "monitoring": 2.0,
        "knowledge base": 2.5, "audit trail": 2.5,
    },
    "decision_complexity": {
        "lookup": 1.0, "validation": 1.0, "deterministic": 1.0, "static": 0.5,
        "workflow": 2.5, "state machine": 2.5, "branching": 2.0,
        "rule-based": 2.0, "scoring": 2.0, "approval": 2.0, "routing": 2.5,
        "classification": 3.5, "classify": 3.5, "intent": 3.0,
        "nlp": 3.5, "prediction": 3.5, "anomaly detection": 3.5,
        "inference": 3.0, "adaptive": 3.0, "machine learning": 3.5,
        "recommendation": 3.0, "sentiment": 3.0, "extraction": 2.5,
        "summarization": 3.0, "intelligent": 2.5, "agent": 3.0,
        # Cross-dimension: orchestration + multi-agent imply decision routing
        "orchestration": 2.5, "multi-agent": 2.5, "compliance": 2.0,
    },
    "user_interaction": {
        "api": 1.0, "headless": 1.0, "batch": 1.0, "cli": 1.0, "cron": 0.5,
        "dashboard": 2.5, "form": 2.0, "wizard": 2.5, "report": 2.0,
        "search": 2.0, "filter": 1.5, "portal": 2.0,
        "natural language": 4.0, "nlq": 4.0, "conversational": 4.0,
        "chatbot": 4.0, "voice": 3.5, "exploratory": 3.0,
        "interactive": 2.5, "chat": 3.5, "dialogue": 3.5,
        "question answering": 3.5, "query interface": 3.0,
        # Cross-dimension: queries imply user-facing data access
        "queries": 2.0,
    },
    "integration_density": {
        "standalone": 1.0, "self-contained": 1.0, "isolated": 0.5,
        "api integration": 2.5, "webhook": 2.5, "sso": 2.0, "oauth": 2.0,
        "rest": 1.5, "database connection": 2.0, "external": 2.0,
        "multi-agent": 4.0, "a2a": 4.0, "orchestration": 3.5,
        "event-driven": 3.5, "federated": 3.5, "mesh": 3.5,
        "cross-system": 3.0, "sync": 2.5, "real-time": 3.0,
        "message queue": 3.0, "pub/sub": 3.0, "saga": 3.5,
    },
    "compliance_sensitivity": {
        "public": 0.5, "prototype": 0.5, "demo": 0.5, "internal": 1.0,
        "gdpr": 2.5, "hipaa": 2.5, "rbac": 2.0, "logging": 1.5,
        "audit": 2.0, "privacy": 2.0, "pii": 2.5,
        "cui": 4.0, "secret": 4.0, "fedramp": 4.0, "cmmc": 4.0,
        "nist": 3.5, "fips": 3.5, "stig": 3.5, "ato": 3.5,
        "il4": 3.0, "il5": 3.5, "il6": 4.0, "govcloud": 3.0,
        "compliance": 2.5, "classified": 4.0, "controlled": 3.0,
        "non-repudiation": 3.5, "accreditation": 3.5,
    },
    "scale_variability": {
        "fixed": 0.5, "single instance": 0.5, "low traffic": 0.5,
        "small team": 0.5, "internal tool": 1.0,
        "load balanced": 2.5, "moderate": 1.5, "predictable": 1.5,
        "horizontal": 2.5, "replicas": 2.0, "cluster": 2.5,
        "burst": 3.5, "auto-scaling": 3.5, "autoscale": 3.5,
        "real-time streaming": 4.0, "millions": 4.0,
        "elastic": 3.5, "serverless": 3.0, "high availability": 3.0,
        "concurrent": 3.0, "spike": 3.5, "global": 3.0,
        # Cross-dimension: multi-agent + orchestration imply distributed scale
        "multi-agent": 2.0, "orchestration": 2.0,
    },
}

# Default config fallback (when YAML unavailable)
DEFAULT_CONFIG = {
    "scoring": {
        "weights": {
            "data_complexity": 0.10,
            "decision_complexity": 0.25,
            "user_interaction": 0.20,
            "integration_density": 0.15,
            "compliance_sensitivity": 0.15,
            "scale_variability": 0.15,
        }
    },
    "thresholds": {
        "agent_minimum": 6.0,
        "nlq_minimum": 5.0,
        "traditional_maximum": 4.0,
    },
    "always_on": {
        "self_healing": True,
        "a2a_interop": True,
        "aiops": True,
        "gotcha_framework": True,
        "ai_governance": True,
        "user_feedback": True,
    },
    "llm_override": {
        "model_preference": "sonnet-4-5",
        "effort": "medium",
        "max_tokens": 4096,
    },
}


def _load_config() -> dict:
    """Load scoring configuration from YAML or fall back to defaults."""
    if yaml and CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = yaml.safe_load(f) or {}
            merged = DEFAULT_CONFIG.copy()
            for key in merged:
                if key in cfg:
                    if isinstance(merged[key], dict):
                        merged[key].update(cfg[key])
                    else:
                        merged[key] = cfg[key]
            return merged
        except Exception as e:
            logger.warning("Failed to load %s: %s — using defaults", CONFIG_PATH, e)
    return DEFAULT_CONFIG.copy()


def score_spec(spec: str, config: dict) -> Dict[str, int]:
    """Score a component spec across 6 dimensions using keyword frequency.

    Returns dict of {dimension: score} where each score is 0-10.
    """
    spec_lower = spec.lower()
    scores = {}

    for dimension, keywords in KEYWORD_BANKS.items():
        raw_score = 0.0
        matched_keywords = []
        for keyword, weight in keywords.items():
            count = len(re.findall(re.escape(keyword), spec_lower))
            if count > 0:
                raw_score += weight * min(count, 3)
                matched_keywords.append(keyword)

        # Map raw_score to 1-10 scale.  Single high-value keyword (4.0)
        # should reach ~5; two high-value keywords (8.0) should reach ~7.
        if raw_score <= 0:
            final = 1
        elif raw_score < 2:
            final = 2
        elif raw_score < 3:
            final = 3
        elif raw_score < 4.5:
            final = 4
        elif raw_score < 6:
            final = 5
        elif raw_score < 7.5:
            final = 6
        elif raw_score < 9.5:
            final = 7
        elif raw_score < 12:
            final = 8
        elif raw_score < 16:
            final = 9
        else:
            final = 10

        scores[dimension] = final
        if matched_keywords:
            logger.debug("%s: raw=%.1f final=%d keywords=%s",
                        dimension, raw_score, final, matched_keywords)

    return scores


def compute_overall(scores: Dict[str, int], weights: Dict[str, float]) -> float:
    """Compute weighted overall score from dimension scores."""
    total = sum(scores[dim] * weights.get(dim, 0.0) for dim in scores)
    return round(total, 2)


def map_recommendations(
    overall: float,
    scores: Dict[str, int],
    thresholds: dict,
    spec: str,
) -> dict:
    """Map scores to architecture recommendation."""
    agent_min = thresholds.get("agent_minimum", 6.0)
    nlq_min = thresholds.get("nlq_minimum", 5.0)
    trad_max = thresholds.get("traditional_maximum", 4.0)

    if overall >= agent_min:
        architecture = "agent"
    elif overall < trad_max:
        architecture = "traditional"
    else:
        architecture = "hybrid"

    spec_lower = spec.lower()
    agent_components = []
    nlq_interfaces = []
    traditional_components = []

    if scores.get("user_interaction", 0) >= nlq_min:
        nlq_keywords = ["search", "query", "dashboard", "report", "analytics"]
        for kw in nlq_keywords:
            if kw in spec_lower:
                nlq_interfaces.append(f"{kw}-interface")
        if not nlq_interfaces:
            nlq_interfaces.append("nlq-query-interface")

    agent_kw = ["classify", "route", "decision", "inference", "orchestrat",
                "detect", "recommend", "predict", "analyze", "intelligent"]
    for kw in agent_kw:
        if kw in spec_lower:
            agent_components.append(f"{kw}-agent")

    trad_kw = ["crud", "storage", "config", "static", "lookup", "logging"]
    for kw in trad_kw:
        if kw in spec_lower:
            traditional_components.append(f"{kw}-service")

    if architecture == "agent" and not agent_components:
        agent_components.append("core-agent")
    if architecture == "traditional" and not traditional_components:
        traditional_components.append("core-service")

    return {
        "architecture": architecture,
        "agent_components": agent_components,
        "nlq_interfaces": nlq_interfaces,
        "traditional_components": traditional_components,
    }


def llm_override(
    spec: str,
    rule_scores: Dict[str, int],
    config: dict,
) -> Optional[dict]:
    """Use Bedrock LLM to refine fitness scores (optional)."""
    if not _BEDROCK_AVAILABLE:
        logger.warning("Bedrock not available — skipping LLM override")
        return None

    if not PROMPT_PATH.exists():
        logger.warning("Prompt template not found: %s", PROMPT_PATH)
        return None

    try:
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        prompt = prompt_template.replace("{{spec}}", spec)
        prompt = prompt.replace("{{scores}}", json.dumps(rule_scores, indent=2))

        llm_config = config.get("llm_override", {})
        client = BedrockClient()
        request = BedrockRequest(
            prompt=prompt,
            model=llm_config.get("model_preference", "sonnet-4-5"),
            max_tokens=llm_config.get("max_tokens", 4096),
        )
        response = client.invoke(request)

        text = response.content if hasattr(response, "content") else str(response)
        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        return json.loads(text)

    except Exception as e:
        logger.warning("LLM override failed: %s — using rule-based scores", e)
        return None


def persist_assessment(
    scorecard: dict,
    project_id: Optional[str],
    db_path: Path = None,
) -> str:
    """Persist assessment to database and audit trail. Returns assessment ID."""
    assessment_id = str(uuid.uuid4())
    path = db_path or DB_PATH

    if path.exists():
        try:
            conn = sqlite3.connect(str(path))
            conn.execute(
                """INSERT INTO agentic_fitness_assessments
                   (id, project_id, component_name, spec_text, scores,
                    overall_score, recommendation, rationale, assessed_by, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assessment_id,
                    project_id,
                    scorecard.get("component", "unknown"),
                    scorecard.get("spec_text", ""),
                    json.dumps(scorecard.get("scores", {})),
                    scorecard.get("overall_score", 0.0),
                    scorecard.get("recommendations", {}).get("architecture", "traditional"),
                    scorecard.get("rationale", ""),
                    "architect-agent",
                    scorecard.get("classification", "CUI"),
                ),
            )
            conn.commit()
            conn.close()
            logger.info("Assessment %s persisted to DB", assessment_id)
        except Exception as e:
            logger.warning("DB persist failed: %s", e)

    try:
        audit_log_event(
            event_type="agentic_fitness_assessed",
            actor="builder/agentic_fitness",
            action=f"Fitness assessed: {scorecard.get('component', 'unknown')} -> "
                   f"{scorecard.get('recommendations', {}).get('architecture', '?')}",
            project_id=project_id or "",
            details=json.dumps({
                "assessment_id": assessment_id,
                "overall_score": scorecard.get("overall_score"),
                "architecture": scorecard.get("recommendations", {}).get("architecture"),
            }),
        )
    except Exception as e:
        logger.debug("Audit log failed: %s", e)

    return assessment_id


def assess_fitness(
    spec: str,
    project_id: Optional[str] = None,
    use_llm_override: bool = False,
    json_output: bool = False,
    db_path: Path = None,
) -> dict:
    """Main orchestrator: assess component fitness for agentic architecture.

    Returns a scorecard dict matching the fitness_scorecard.json schema.
    """
    config = _load_config()
    weights = config.get("scoring", {}).get("weights", DEFAULT_CONFIG["scoring"]["weights"])
    thresholds = config.get("thresholds", DEFAULT_CONFIG["thresholds"])
    always_on = config.get("always_on", DEFAULT_CONFIG["always_on"])

    # Step 1: Rule-based scoring
    scores = score_spec(spec, config)

    # Step 2: Compute weighted overall
    overall = compute_overall(scores, weights)

    # Step 3: Map to recommendations
    recommendations = map_recommendations(overall, scores, thresholds, spec)

    # Add always-on capabilities
    for key, enabled in always_on.items():
        if enabled:
            recommendations[key] = True

    # Step 4: Extract component name from spec
    component = re.sub(r'[^a-z0-9]+', '-', spec.lower().strip())[:60].strip('-')
    if not component:
        component = "unnamed-component"

    scorecard = {
        "component": component,
        "scores": scores,
        "overall_score": overall,
        "recommendations": recommendations,
        "rationale": f"Rule-based assessment: overall {overall:.2f} "
                     f"-> {recommendations['architecture']}",
        "classification": "CUI",
        "spec_text": spec,
    }

    # Step 5: Optional LLM override
    if use_llm_override:
        llm_result = llm_override(spec, scores, config)
        if llm_result and isinstance(llm_result, dict):
            if "scores" in llm_result:
                scorecard["scores"] = llm_result["scores"]
                scorecard["overall_score"] = compute_overall(
                    llm_result["scores"], weights
                )
            if "recommendations" in llm_result:
                for k, v in llm_result["recommendations"].items():
                    scorecard["recommendations"][k] = v
            if "rationale" in llm_result:
                scorecard["rationale"] = llm_result["rationale"]
            scorecard["rationale"] = f"LLM-refined: {scorecard['rationale']}"
            for key, enabled in always_on.items():
                if enabled:
                    scorecard["recommendations"][key] = True

    # Step 6: Persist to DB
    if project_id or db_path:
        assessment_id = persist_assessment(scorecard, project_id, db_path)
        scorecard["assessment_id"] = assessment_id

    return scorecard


def main():
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Agentic Fitness Assessor - evaluate component fitness for agentic architecture"
    )
    parser.add_argument(
        "--spec", required=True,
        help="Component specification text to evaluate"
    )
    parser.add_argument(
        "--project-id", default=None,
        help="Project ID for DB persistence"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output as JSON (for piping to other tools)"
    )
    parser.add_argument(
        "--llm-override", action="store_true",
        help="Use Bedrock LLM to refine rule-based scores"
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override database path"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scorecard = assess_fitness(
        spec=args.spec,
        project_id=args.project_id,
        use_llm_override=args.llm_override,
        json_output=args.json_output,
        db_path=args.db_path,
    )

    if args.json_output:
        output = {k: v for k, v in scorecard.items() if k != "spec_text"}
        print(json.dumps(output, indent=2))
    else:
        print(f"\n{'='*60}")
        print("  AGENTIC FITNESS ASSESSMENT")
        print(f"{'='*60}")
        print(f"  Component: {scorecard['component']}")
        print(f"  Overall Score: {scorecard['overall_score']:.2f} / 10.0")
        print(f"  Architecture: {scorecard['recommendations']['architecture'].upper()}")
        print(f"{'='*60}")
        print("\n  Dimension Scores:")
        for dim, score in scorecard["scores"].items():
            bar = "#" * score + "." * (10 - score)
            print(f"    {dim:<25s} [{bar}] {score}/10")
        print("\n  Recommendations:")
        recs = scorecard["recommendations"]
        if recs.get("agent_components"):
            print(f"    Agent Components: {', '.join(recs['agent_components'])}")
        if recs.get("nlq_interfaces"):
            print(f"    NLQ Interfaces:   {', '.join(recs['nlq_interfaces'])}")
        if recs.get("traditional_components"):
            print(f"    Traditional:      {', '.join(recs['traditional_components'])}")
        print("\n  Always-On: self_healing, a2a_interop, aiops, gotcha, governance, feedback")
        print(f"\n  Rationale: {scorecard['rationale']}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
