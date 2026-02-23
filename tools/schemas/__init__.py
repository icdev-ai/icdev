#!/usr/bin/env python3
# CUI // SP-CTI
"""Shared schema models for ICDEV tool outputs (Phase 44 — D275).

Provides stdlib dataclass models shared across MCP servers, dashboard API,
SaaS gateway, and CLI tools. Backward compatible — existing dict returns
work via to_dict() methods.
"""

from tools.schemas.core import ProjectStatus, AgentHealth, AuditEvent
from tools.schemas.compliance import ComplianceResult, SecurityScanResult
from tools.schemas.chat import ChatMessage, ChatContext
from tools.schemas.innovation import InnovationSignal
from tools.schemas.validation import validate_output, SchemaValidationError

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
