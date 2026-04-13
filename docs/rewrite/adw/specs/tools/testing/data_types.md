# Spec: `tools/testing/data_types.py`

_OPT-75 Phase 1 clean-room spec. Written from external contract only._

## Purpose

A library of Pydantic data models used by the ICDEV testing framework.
Defines the structured shapes returned by every tool under
`tools/testing/`, plus a few request/response shapes used by the
Claude-Code agent invocation helpers.

The module **must** import successfully even when `pydantic` is not
installed. A minimal in-house `BaseModel` shim takes over so the
testing framework can still run on a stripped-down host.

## Public surface

### Pydantic-or-shim base

A `BaseModel` (real one if importable) and a `Field` callable. The shim:

* `BaseModel.__init__(**kwargs)` assigns each kwarg as an attribute.
* `BaseModel.model_dump()` → `self.__dict__`.
* `BaseModel.model_dump_json(indent=None)` → JSON via `json.dumps` with
  `default=str` so non-serialisable objects degrade to their repr.
* `Field(*args, default=None, **kwargs)` → returns the `default` kwarg
  when called as a default value, otherwise `None`.

### Models (all subclass BaseModel)

All field definitions are documented as `name: type = default`. The
rewrite must preserve every field name and default — call sites set or
read these by name.

#### `TestResult`
- `test_name: str`
- `passed: bool`
- `execution_command: str`
- `test_purpose: str`
- `error: str | None = None`
- `test_type: Literal["unit","integration","bdd","security","compliance"] = "unit"`
- `duration_ms: int | None = None`
- `nist_controls: list[str] = []`

#### `E2ETestResult`
- `test_name: str`
- `status: Literal["passed","failed"]`
- `test_path: str`
- `screenshots: list[str] = []`
- `error: str | None = None`
- `cui_banners_verified: bool = False`
- `video_path: str | None = None`
- `vision_analysis: list[dict[str, Any]] | None = None`
- Computed property `passed -> bool` returning `self.status == "passed"`.

#### `CheckResult`
- `success: bool`
- `error: str | None = None`
- `warning: str | None = None`
- `details: dict[str, Any] = {}`

#### `HealthCheckResult`
- `success: bool`
- `timestamp: str`
- `checks: dict[str, CheckResult] = {}`
- `warnings: list[str] = []`
- `errors: list[str] = []`

#### `GateResult`
- `gate_name: str`
- `passed: bool`
- `severity: Literal["blocking","warning","info"] = "blocking"`
- `details: str = ""`
- `nist_control: str | None = None`

#### `GateEvaluation`
- `gate_type: Literal["code_review","merge","deploy"]`
- `overall_pass: bool`
- `gates: list[GateResult] = []`
- `timestamp: str = ""`
- `project_id: str | None = None`
- `evaluated_by: str = "icdev-testing"`

#### `TestRunState`
- `run_id: str`
- `project_id: str | None = None`
- `project_dir: str | None = None`
- `branch_name: str | None = None`
- `unit_passed: int = 0`
- `unit_failed: int = 0`
- `bdd_passed: int = 0`
- `bdd_failed: int = 0`
- `e2e_passed: int = 0`
- `e2e_failed: int = 0`
- `security_gate_passed: bool | None = None`
- `compliance_gate_passed: bool | None = None`
- `started_at: str | None = None`
- `completed_at: str | None = None`
- `unit_attempts: int = 0`
- `e2e_attempts: int = 0`

#### `AgentPromptRequest`
- `prompt: str`
- `agent_name: str = "ops"`
- `model: Literal["sonnet","opus","haiku"] = "sonnet"`
- `output_file: str = ""`
- `project_dir: str = "."`

#### `AgentPromptResponse`
- `output: str`
- `success: bool`
- `session_id: str | None = None`
- `duration_ms: int | None = None`

#### `AgentTemplateRequest`
- `agent_name: str`
- `slash_command: str`
- `args: list[str] = []`
- `run_id: str = ""`
- `model: Literal["sonnet","opus","haiku"] = "sonnet"`

#### `AcceptanceCriterionResult`
- `criterion: str`
- `status: Literal["verified","failed","unverified"] = "unverified"`
- `evidence_type: Literal["unit_test","bdd_test","e2e_test","page_check","manual"] | None = None`
- `evidence_detail: str = ""`

#### `UIPageCheckResult`
- `url: str`
- `status_code: int = 0`
- `has_errors: bool = False`
- `error_patterns_found: list[str] = []`
- `content_length: int = 0`

#### `AcceptanceReport`
- `plan_file: str`
- `criteria_count: int = 0`
- `criteria_verified: int = 0`
- `criteria_failed: int = 0`
- `criteria_unverified: int = 0`
- `pages_checked: int = 0`
- `pages_with_errors: int = 0`
- `overall_pass: bool = False`
- `criteria: list[AcceptanceCriterionResult] = []`
- `page_checks: list[UIPageCheckResult] = []`
- `timestamp: str = ""`

## Forbidden

* No DB / LLM / network imports.
* Importing this module on a host without pydantic must succeed.
* No mutable default values shared across instances (a recurring
  Pydantic-1 vs Pydantic-2 footgun the rewrite should explicitly
  guard against by using `Field(default_factory=list)` or its shim
  equivalent).

## Acceptance

1. Importing the module succeeds with and without pydantic.
2. Every model name listed above exists at module level.
3. Every documented field name and default exists on the model
   (introspection works for both pydantic and the shim path).
4. `E2ETestResult.passed` returns True when `status == "passed"`.
5. The shim's `model_dump_json()` returns parseable JSON.
6. Two instances of any list-defaulted field are independent (mutating
   one does not change the other) — fixes the historical
   shared-mutable-default bug class.
