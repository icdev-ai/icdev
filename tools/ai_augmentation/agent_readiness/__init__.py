# CUI // SP-CTI
"""Agent Readiness checker for the AI Augmentation Canvas.

Public API:
    from tools.ai_augmentation.agent_readiness import run_readiness_check
    result = run_readiness_check(repo_path)
"""
from tools.ai_augmentation.agent_readiness.checker import run_readiness_check

__all__ = ["run_readiness_check"]
