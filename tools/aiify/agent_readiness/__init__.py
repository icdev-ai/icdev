# CUI // SP-CTI
"""Agent Readiness checker — Python port of kodustech/agent-readiness with ICDEV IL/NIST extensions.

Public API:
    from tools.aiify.agent_readiness import run_readiness_check
    result = run_readiness_check(repo_path)

Returns:
    {
        "pillar_scores": {pillar_id: {"passed": int, "total": int, "percentage": float}},
        "overall_readiness_score": float,   # 0.0–1.0
        "icdev_checks": {pillar_id: [criterion_results]},
    }
"""
from tools.aiify.agent_readiness.checker import run_readiness_check

__all__ = ["run_readiness_check"]
