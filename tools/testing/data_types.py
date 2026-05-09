# CUI // SP-CTI
"""ICDEV™ testing framework data models.

The structured shapes returned by every tool under ``tools/testing/``
plus a few request/response shapes used by the Claude Code agent
helpers.

Pydantic is used when available; the module falls back to a tiny
in-house ``BaseModel`` shim so the framework still imports on hosts
without pydantic installed. Both code paths share the same field
defaults — including the **independent** list defaults that fix the
historical shared-mutable-default bug.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/testing/data_types.md`` (OPT-75 Phase 3
clean-room rewrite).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional


# ────────────────────────────────────────────────────────────────────────────
# Pydantic ↔ shim
# ────────────────────────────────────────────────────────────────────────────

try:
    from pydantic import BaseModel  # type: ignore
    from pydantic import Field as _PydField  # type: ignore

    # Re-export so call sites can `from tools.testing.data_types import Field`.
    Field = _PydField  # noqa: N816 — alias intentional
    _USING_PYDANTIC = True
except ImportError:  # pragma: no cover - exercised in shim-only envs
    _USING_PYDANTIC = False

    class BaseModel:  # type: ignore[no-redef]
        """Minimal shim so the testing framework imports without pydantic.

        Models that depend on this shim must call ``super().__init__``
        in their own ``__init__`` (or omit ``__init__`` entirely and
        let the shim populate from kwargs).
        """

        def __init__(self, **kwargs: Any) -> None:
            # Pull every declared default from the class so callers don't
            # have to set every field. Mutable defaults are deep-copied
            # so two instances never share a list/dict reference — this
            # is the bug that the spec acceptance check 6 calls out.
            for name, default in self.__class__._field_defaults().items():
                if name in kwargs:
                    setattr(self, name, kwargs[name])
                else:
                    setattr(self, name, _clone_default(default))
            # Allow ad-hoc unknown kwargs without raising — matches the
            # shim's old behaviour.
            for k, v in kwargs.items():
                if not hasattr(self, k):
                    setattr(self, k, v)

        @classmethod
        def _field_defaults(cls) -> Dict[str, Any]:
            # Walk the MRO and gather class-level annotations + defaults.
            collected: Dict[str, Any] = {}
            for klass in reversed(cls.__mro__):
                if klass is object or klass is BaseModel:
                    continue
                annotations = getattr(klass, "__annotations__", {}) or {}
                for name in annotations:
                    if name in collected:
                        continue
                    collected[name] = getattr(klass, name, None)
            return collected

        def model_dump(self) -> Dict[str, Any]:
            return dict(self.__dict__)

        def model_dump_json(self, indent: Optional[int] = None) -> str:
            return json.dumps(
                self.__dict__, indent=indent, default=str
            )

    def Field(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return kwargs.get("default", None)


def _clone_default(value: Any) -> Any:
    """Return a fresh copy of a mutable default so model instances do
    not share state. Scalars and immutables are returned as-is."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, set):
        return set(value)
    return value


# ────────────────────────────────────────────────────────────────────────────
# Test result types
# ────────────────────────────────────────────────────────────────────────────


_TestType = Literal["unit", "integration", "bdd", "security", "compliance"]
_E2EStatus = Literal["passed", "failed", "skipped"]
_GateType = Literal["code_review", "merge", "deploy"]
_GateSeverity = Literal["blocking", "warning", "info"]
_AgentModel = Literal["sonnet", "opus", "haiku"]
_AcceptanceStatus = Literal["verified", "failed", "unverified"]
_EvidenceType = Literal[
    "unit_test", "bdd_test", "e2e_test", "page_check", "manual"
]


class TestResult(BaseModel):
    """A single result from a unit, integration, or compliance test."""

    test_name: str = ""
    passed: bool = False
    execution_command: str = ""
    test_purpose: str = ""
    error: Optional[str] = None
    test_type: _TestType = "unit"
    duration_ms: Optional[int] = None
    nist_controls: List[str] = []


class E2ETestResult(BaseModel):
    """A single result from a Playwright/Selenium E2E test."""

    test_name: str = ""
    status: _E2EStatus = "failed"
    test_path: str = ""
    screenshots: List[str] = []
    error: Optional[str] = None
    cui_banners_verified: bool = False
    video_path: Optional[str] = None
    vision_analysis: Optional[List[Dict[str, Any]]] = None

    @property
    def passed(self) -> bool:
        return self.status in ("passed", "skipped")


# ────────────────────────────────────────────────────────────────────────────
# Health check types
# ────────────────────────────────────────────────────────────────────────────


class CheckResult(BaseModel):
    success: bool = False
    error: Optional[str] = None
    warning: Optional[str] = None
    details: Dict[str, Any] = {}


class HealthCheckResult(BaseModel):
    success: bool = False
    timestamp: str = ""
    checks: Dict[str, CheckResult] = {}
    warnings: List[str] = []
    errors: List[str] = []


# ────────────────────────────────────────────────────────────────────────────
# Compliance gate types
# ────────────────────────────────────────────────────────────────────────────


class GateResult(BaseModel):
    gate_name: str = ""
    passed: bool = False
    severity: _GateSeverity = "blocking"
    details: str = ""
    nist_control: Optional[str] = None


class GateEvaluation(BaseModel):
    gate_type: _GateType = "code_review"
    overall_pass: bool = False
    gates: List[GateResult] = []
    timestamp: str = ""
    project_id: Optional[str] = None
    evaluated_by: str = "icdev-testing"


# ────────────────────────────────────────────────────────────────────────────
# Test orchestration state
# ────────────────────────────────────────────────────────────────────────────


class TestRunState(BaseModel):
    run_id: str = ""
    project_id: Optional[str] = None
    project_dir: Optional[str] = None
    branch_name: Optional[str] = None
    unit_passed: int = 0
    unit_failed: int = 0
    bdd_passed: int = 0
    bdd_failed: int = 0
    e2e_passed: int = 0
    e2e_failed: int = 0
    security_gate_passed: Optional[bool] = None
    compliance_gate_passed: Optional[bool] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    unit_attempts: int = 0
    e2e_attempts: int = 0


# ────────────────────────────────────────────────────────────────────────────
# Agent execution types
# ────────────────────────────────────────────────────────────────────────────


class AgentPromptRequest(BaseModel):
    prompt: str = ""
    agent_name: str = "ops"
    model: _AgentModel = "sonnet"
    output_file: str = ""
    project_dir: str = "."


class AgentPromptResponse(BaseModel):
    output: str = ""
    success: bool = False
    session_id: Optional[str] = None
    duration_ms: Optional[int] = None


class AgentTemplateRequest(BaseModel):
    agent_name: str = ""
    slash_command: str = ""
    args: List[str] = []
    run_id: str = ""
    model: _AgentModel = "sonnet"


# ────────────────────────────────────────────────────────────────────────────
# Acceptance validation (V&V gate)
# ────────────────────────────────────────────────────────────────────────────


class AcceptanceCriterionResult(BaseModel):
    criterion: str = ""
    status: _AcceptanceStatus = "unverified"
    evidence_type: Optional[_EvidenceType] = None
    evidence_detail: str = ""


class UIPageCheckResult(BaseModel):
    url: str = ""
    status_code: int = 0
    has_errors: bool = False
    error_patterns_found: List[str] = []
    content_length: int = 0


class AcceptanceReport(BaseModel):
    plan_file: str = ""
    criteria_count: int = 0
    criteria_verified: int = 0
    criteria_failed: int = 0
    criteria_unverified: int = 0
    pages_checked: int = 0
    pages_with_errors: int = 0
    overall_pass: bool = False
    criteria: List[AcceptanceCriterionResult] = []
    page_checks: List[UIPageCheckResult] = []
    timestamp: str = ""


# ────────────────────────────────────────────────────────────────────────────
# Theater detection types
# ────────────────────────────────────────────────────────────────────────────

_TheaterSeverity = Literal["none", "warn", "block"]


class TheaterDetectionResult(BaseModel):
    """Result of a theater-pattern antipattern scan on a single file."""

    file_path: str = ""
    antipatterns_found: List[str] = []
    severity: _TheaterSeverity = "none"
    details: Dict[str, Any] = {}
    passed: bool = False
    duration_ms: int = 0
