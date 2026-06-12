#!/usr/bin/env python3
# CUI // SP-CTI
"""Compliance and security scan schema models (Phase 44 — D275)."""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ComplianceResult:
    """Multi-framework compliance assessment result."""

    framework: str
    assessment_date: str
    overall_status: str  # pass, fail, warning
    controls_assessed: int = 0
    controls_satisfied: int = 0
    controls_other_than_satisfied: int = 0
    blocking_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    coverage_pct: float = 0.0
    gate_passed: Optional[bool] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "ComplianceResult":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class SecurityScanResult:
    """Unified security scan result across SAST, deps, secrets, containers, patterns."""

    scan_type: str  # sast, dependency, secret, container, injection, code_pattern
    status: str  # pass, fail, warning
    findings_count: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    findings: List[Dict[str, Any]] = field(default_factory=list)
    scanned_at: Optional[str] = None
    scanned_files: int = 0
    language: Optional[str] = None
    gate_passed: Optional[bool] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "SecurityScanResult":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})
