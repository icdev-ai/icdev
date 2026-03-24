#!/usr/bin/env python3
# CUI // SP-CTI
"""Chat and conversation schema models (Phase 44 — D275).

Used by multi-stream parallel chat (D257), dashboard API, and SaaS portal.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ChatMessage:
    """Single chat message within a context."""

    role: str  # user, assistant, system, intervention
    content: str
    turn_number: int = 0
    content_type: str = "text"  # text, tool_result, error, intervention, summary
    metadata: Optional[Dict[str, Any]] = None
    is_compressed: bool = False
    compression_tier: Optional[str] = None  # current, historical, bulk
    classification: str = "CUI"
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "ChatMessage":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ChatContext:
    """Multi-stream chat context (D257)."""

    context_id: str
    user_id: str
    title: str = ""
    status: str = "active"  # active, paused, completed, error, archived
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None
    intake_session_id: Optional[str] = None
    agent_model: str = "sonnet"
    message_count: int = 0
    dirty_version: int = 0
    queue_depth: int = 0
    is_processing: bool = False
    last_activity_at: Optional[str] = None
    classification: str = "CUI"
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "ChatContext":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})
