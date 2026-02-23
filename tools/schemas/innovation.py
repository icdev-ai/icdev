#!/usr/bin/env python3
# CUI // SP-CTI
"""Innovation signal schema model (Phase 44 — D275)."""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class InnovationSignal:
    """Innovation pipeline signal — discovered pattern or opportunity."""

    signal_id: str
    source: str
    source_type: str  # github, stackoverflow, nvd, external_framework_analysis, etc.
    title: str
    description: str = ""
    url: str = ""
    category: Optional[str] = None
    innovation_score: Optional[float] = None
    score_breakdown: Optional[Dict[str, float]] = None
    triage_result: Optional[str] = None  # auto_queue, suggest, log_only, blocked
    status: str = "new"
    gotcha_layer: Optional[str] = None  # goal, tool, arg, context, hardprompt
    boundary_tier: Optional[str] = None  # GREEN, YELLOW, ORANGE, RED
    estimated_effort: Optional[str] = None  # S, M, L, XL
    implementation_status: Optional[str] = None  # pending, in_progress, completed
    content_hash: Optional[str] = None
    discovered_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "InnovationSignal":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})
