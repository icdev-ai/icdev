#!/usr/bin/env python3
# CUI // SP-CTI
"""Shared schema models for ICDEV™ tool outputs (Phase 44 — D275).

Provides stdlib dataclass models shared across MCP servers, dashboard API,
SaaS gateway, and CLI tools. Backward compatible — existing dict returns
work via to_dict() methods.
"""

from icdev.tools.schemas.core import ProjectStatus, AgentHealth, AuditEvent
from icdev.tools.schemas.compliance import ComplianceResult, SecurityScanResult
from icdev.tools.schemas.chat import ChatMessage, ChatContext
from icdev.tools.schemas.innovation import InnovationSignal
from icdev.tools.schemas.validation import validate_output, SchemaValidationError

__all__ = [
    "ProjectStatus",
    "AgentHealth",
    "AuditEvent",
    "ComplianceResult",
    "SecurityScanResult",
    "ChatMessage",
    "ChatContext",
    "InnovationSignal",
    "validate_output",
    "SchemaValidationError",
]
