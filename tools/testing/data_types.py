# [TEMPLATE: CUI // SP-CTI]
# ICDEV Testing Data Types
# Adapted from ADW data_types.py for Gov/DoD testing workflows

"""Pydantic data models for ICDEV testing framework.

Provides structured types for unit test results, E2E test results,
health checks, compliance gate results, and test orchestration state.
"""

from typing import Optional, List, Dict, Any, Literal

try:
    from pydantic import BaseModel, Field
except ImportError:
    # Fallback for environments without pydantic
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def model_dump(self):
            return self.__dict__
        def model_dump_json(self, indent=None):
            import json
            return json.dumps(self.__dict__, indent=indent, default=str)

    def Field(*args, **kwargs):
        return kwargs.get('default', None)


# --- Test Result Types (adapted from ADW TestResult / E2ETestResult) ---

class TestResult(BaseModel):
    """Individual test result from unit/integration test execution.

    Mirrors ADW TestResult pattern with added NIST compliance fields.
    """
    test_name: str
    passed: bool
    execution_command: str
    test_purpose: str
    error: Optional[str] = None
    # ICDEV additions
    test_type: Literal["unit", "integration", "bdd", "security", "compliance"] = "unit"
    duration_ms: Optional[int] = None
    nist_controls: List[str] = []  # Controls satisfied by this test (e.g., ["SA-11"])


class E2ETestResult(BaseModel):
    """Individual E2E test result from browser automation via Playwright MCP.

    Mirrors ADW E2ETestResult pattern with screenshots and CUI marking verification.
    """
    test_name: str
    status: Literal["passed", "failed"]
    test_path: str  # Path to the .md test spec file
    screenshots: List[str] = []
    error: Optional[str] = None
    # ICDEV additions
    cui_banners_verified: bool = False  # Whether CUI banners were checked in UI
    video_path: Optional[str] = None
    # Vision-based screenshot validation (Phase 23)
    vision_analysis: Optional[List[Dict[str, Any]]] = None

    @property
    def passed(self) -> bool:
        """Check if test passed."""
        return self.status == "passed"


# --- Health Check Types (adapted from ADW health_check.py) ---

class CheckResult(BaseModel):
    """Individual health check result."""
    success: bool
    error: Optional[str] = None
    warning: Optional[str] = None
    details: Dict[str, Any] = {}


class HealthCheckResult(BaseModel):
    """Aggregate health check results for the ICDEV system."""
    success: bool
    timestamp: str
    checks: Dict[str, CheckResult] = {}
    warnings: List[str] = []
    errors: List[str] = []


# --- Compliance Gate Types (ICDEV-specific) ---

class GateResult(BaseModel):
    """Result of a single security/compliance gate evaluation."""
    gate_name: str
    passed: bool
    severity: Literal["blocking", "warning", "info"] = "blocking"
    details: str = ""
    nist_control: Optional[str] = None


class GateEvaluation(BaseModel):
    """Aggregate gate evaluation result for code review / merge / deploy."""
    gate_type: Literal["code_review", "merge", "deploy"]
    overall_pass: bool
    gates: List[GateResult] = []
    timestamp: str = ""
    project_id: Optional[str] = None
    evaluated_by: str = "icdev-testing"


# --- Test Orchestration State (adapted from ADW ADWStateData) ---

class TestRunState(BaseModel):
    """Persistent state for a test orchestration run.

    Stored in .tmp/test_runs/{run_id}/state.json
    """
    run_id: str
    project_id: Optional[str] = None
    project_dir: Optional[str] = None
    branch_name: Optional[str] = None
    # Test counts
    unit_passed: int = 0
    unit_failed: int = 0
    bdd_passed: int = 0
    bdd_failed: int = 0
    e2e_passed: int = 0
    e2e_failed: int = 0
    # Gate results
    security_gate_passed: Optional[bool] = None
    compliance_gate_passed: Optional[bool] = None
    # Timing
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    # Retry tracking
    unit_attempts: int = 0
    e2e_attempts: int = 0


# --- Agent Execution Types (adapted from ADW agent types) ---

class AgentPromptRequest(BaseModel):
    """Request to execute a Claude Code agent prompt."""
    prompt: str
    agent_name: str = "ops"
    model: Literal["sonnet", "opus", "haiku"] = "sonnet"
    output_file: str = ""
    project_dir: str = "."


class AgentPromptResponse(BaseModel):
    """Response from a Claude Code agent execution."""
    output: str
    success: bool
    session_id: Optional[str] = None
    duration_ms: Optional[int] = None


class AgentTemplateRequest(BaseModel):
    """Request to execute a Claude Code skill/slash command."""
    agent_name: str
    slash_command: str  # e.g., "/icdev-test", "/icdev-secure"
    args: List[str] = []
    run_id: str = ""
    model: Literal["sonnet", "opus", "haiku"] = "sonnet"


# --- Acceptance Validation Types (V&V Gate) ---

class AcceptanceCriterionResult(BaseModel):
    """Result of validating a single acceptance criterion against test evidence."""
    criterion: str
    status: Literal["verified", "failed", "unverified"] = "unverified"
    evidence_type: Optional[Literal["unit_test", "bdd_test", "e2e_test", "page_check", "manual"]] = None
    evidence_detail: str = ""


class UIPageCheckResult(BaseModel):
    """Result of checking a rendered page for error patterns (deterministic DOM check)."""
    url: str
    status_code: int = 0
    has_errors: bool = False
    error_patterns_found: List[str] = []
    content_length: int = 0


class AcceptanceReport(BaseModel):
    """Full acceptance validation report — gate artifact for V&V."""
    plan_file: str
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
