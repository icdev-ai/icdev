#!/usr/bin/env python3
# CUI // SP-CTI
"""Core domain schema models (Phase 44 — D275).

ProjectStatus, AgentHealth, AuditEvent — shared across MCP servers,
dashboard, SaaS gateway, and CLI tools.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ProjectStatus:
    """Unified project status model."""

    project_id: str
    name: str
    type: str
    classification: str = "CUI"
    status: str = "active"
    impact_level: str = "IL4"
    directory_path: str = ""
    ato_status: str = "none"
    compliance: Optional[Dict[str, Any]] = None
    security: Optional[Dict[str, Any]] = None
    deployments: Optional[Dict[str, Any]] = None
    tests: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectStatus":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class AgentHealth:
    """Agent health and status model."""

    agent_id: str
    name: str
    status: str  # active, inactive, error
    url: str = ""
    last_heartbeat: Optional[str] = None
    active_tasks: int = 0
    capabilities: Optional[List[str]] = None
    port: Optional[int] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "AgentHealth":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class AuditEvent:
    """Immutable audit trail event model (D6 — append-only)."""

    event_type: str
    actor: str
    action: str
    project_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    affected_files: Optional[List[str]] = None
    classification: str = "CUI"
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "AuditEvent":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})
