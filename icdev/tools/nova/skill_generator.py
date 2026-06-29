# CUI // SP-CTI
"""NOVA Skill Generator — icdev namespace shim (adapt-hermes-04).

All logic lives at tools/nova/skill_generator.py.
"""
from tools.nova.skill_generator import (  # noqa: F401
    analyze_patterns,
    generate_skill_spec,
    list_queued,
    _llm_generate_spec,
    _queue_for_harness,
)

__all__ = [
    "analyze_patterns",
    "generate_skill_spec",
    "list_queued",
]
