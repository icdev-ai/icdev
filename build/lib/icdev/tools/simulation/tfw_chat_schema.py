# CUI // SP-CTI
"""Traffic Flow Walkthrough (TFW) dataclass schema — canvas-agnostic.

Provides three dataclasses used by the TFW feature:
  SimulationSession  — top-level session envelope
  ChatMessage        — a single chat turn (user or assistant)
  WalkthroughStep    — one hop in the walkthrough narrative

flow_noun on WalkthroughStep is always resolved from canvas_registry
so no canvas-specific strings are hardcoded here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from tools.canvas.canvas_registry import get_flow_noun


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SimulationSession:
    session_id: str
    canvas_type: str
    topology_id: str
    mode: str
    created_at: str = field(default_factory=_utc_now)

    def session_to_dict(self) -> dict[str, Any]:
        """Serialize session to a plain dict (JSON-safe)."""
        return asdict(self)


@dataclass
class ChatMessage:
    role: str
    content: str
    diagram_mermaid: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WalkthroughStep:
    step_num: int
    node_id: str
    action: str
    persona_responses: dict[str, Any] = field(default_factory=dict)
    flow_noun: str = field(default="")

    def __post_init__(self) -> None:
        # flow_noun must be set; callers should use make_walkthrough_step so it
        # is always resolved from canvas_registry rather than left blank.
        pass


def make_walkthrough_step(
    session: SimulationSession,
    step_num: int,
    node_id: str,
    action: str,
    persona_responses: dict[str, Any] | None = None,
) -> WalkthroughStep:
    """Factory that resolves flow_noun from canvas_registry for the session's canvas type."""
    return WalkthroughStep(
        step_num=step_num,
        node_id=node_id,
        action=action,
        persona_responses=persona_responses or {},
        flow_noun=get_flow_noun(session.canvas_type),
    )
