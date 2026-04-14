# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Air-Gap Compatibility Layer.

Detects air-gapped environments and patches ICDEV™ subsystems to operate
without Claude Code CLI, Anthropic API, AWS Bedrock, or any cloud LLM.
All routing falls back to local Ollama models.

Usage::

    from tools.airgap import is_airgap, activate_airgap

    if is_airgap():
        activate_airgap()   # patches router, hooks, health checks

    # Or from CLI:
    #   python -m tools.airgap --activate
    #   python -m tools.airgap --check
"""

from tools.airgap.detector import is_airgap, detect_environment, has_vendored_driver
from tools.airgap.config_patcher import activate_airgap, deactivate_airgap

__all__ = [
    "is_airgap",
    "detect_environment",
    "has_vendored_driver",
    "activate_airgap",
    "deactivate_airgap",
]
