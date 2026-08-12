#!/usr/bin/env python3
# CUI // SP-CTI
"""exa-policy-08: compliance generators must assert enforcement, not file existence.

The defect these tests pin: four generators evidenced per-tool MCP
authorization with ``Path("tools/security/mcp_tool_authorizer.py").exists()``.
That module shipped with zero call sites, so every one of those checks reported
a satisfied FedRAMP KSI / OWASP-ASI-02 control for a module that authorized
nothing.

The load-bearing assertion in this file is therefore a *negative* one:
``mcp_tool_authorizer.py`` is present in the checkout during every test below
(asserted explicitly), and every generator must still refuse to call the
control satisfied while enforcement is disabled. A regression to a file-existence
check would flip all of them green with the source file untouched.
"""

import importlib

import pytest

authz_evidence = importlib.import_module("tools.security.mcp_authz_evidence")
mcp_tool_authorizer = importlib.import_module("tools.security.mcp_tool_authorizer")


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
class FakeSurface:
    """An MCP surface that consults the real D261 policy, in a chosen mode."""

    TOOL_REGISTRY = [
        {"name": "terraform_apply"},
        {"name": "run_tests"},
        {"name": "generate_code"},
    ]

    def __init__(self, mode="enforce", authorizer=None):
        self.mode = mode
        self._authorizer = authorizer or mcp_tool_authorizer.MCPToolAuthorizer(config=DEFAULT_MATRIX)

    def authorize_tool(self, tool_name, user_role):
        verdict = self._authorizer.authorize((user_role or "").strip().lower(), tool_name)
        return dict(verdict, enforced=self.mode == "enforce", mode=self.mode)


class UnwiredSurface:
    """The surface as it is with an inert authorizer: no decision function."""

    TOOL_REGISTRY = [{"name": "terraform_apply"}]


class PermissiveSurface(FakeSurface):
    """A surface that ignores the policy and lets everything through."""

    def authorize_tool(self, tool_name, user_role):
        return {"allowed": True, "enforced": True, "mode": "enforce", "reason": "wide open"}


DEFAULT_MATRIX = {
    "default_policy": "deny",
    "role_tool_matrix": {
        "admin": {"allow": ["*"]},
        "developer": {
            "allow": ["run_tests", "generate_code"],
            "deny": ["terraform_apply"],
        },
    },
}

OPEN_MATRIX = {"default_policy": "allow", "role_tool_matrix": {}}


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    authz_evidence.reset_cache()
    yield
    authz_evidence.reset_cache()


@pytest.fixture
def enforcing():
    return authz_evidence.probe_mcp_authorization(
        authorizer=mcp_tool_authorizer.MCPToolAuthorizer(config=DEFAULT_MATRIX),
        surface=FakeSurface("enforce"),
    )


@pytest.fixture
def disabled():
    """Enforcement disabled the way it is actually disabled today: the policy
    module is on disk and nothing on a principal-bearing surface calls it."""
    return authz_evidence.probe_mcp_authorization(
        authorizer=mcp_tool_authorizer.MCPToolAuthorizer(config=DEFAULT_MATRIX),
        surface=UnwiredSurface(),
    )


# ---------------------------------------------------------------------------
# The premise: the file is there. That must not be enough.
# ---------------------------------------------------------------------------
def test_authorizer_source_file_is_present():
    """Guard for every test below — they only mean something while this holds."""
    assert (authz_evidence.BASE_DIR / "tools" / "security" / "mcp_tool_authorizer.py").exists()


# ---------------------------------------------------------------------------
# Layer 1: the policy must actually decide
# ---------------------------------------------------------------------------
def test_policy_probe_passes_on_deny_first_matrix():
    result = authz_evidence.probe_policy(mcp_tool_authorizer.MCPToolAuthorizer(config=DEFAULT_MATRIX))
    assert result["ok"], result["reason"]
    assert len(result["checks"]) == len(authz_evidence.POLICY_PROBES)


def test_policy_probe_fails_on_default_allow_matrix():
    """An empty, allow-by-default config is a disabled policy. The file that
    holds it exists either way."""
    result = authz_evidence.probe_policy(mcp_tool_authorizer.MCPToolAuthorizer(config=OPEN_MATRIX))
    assert not result["ok"]
    assert "did not decide" in result["reason"]


def test_policy_probe_covers_every_branch_of_the_deny_first_contract():
    proves = {p[3] for p in authz_evidence.POLICY_PROBES}
    assert "explicit deny rule fires" in proves
    assert "allow rule fires" in proves
    assert "default policy denies an unlisted tool" in proves
    assert "unknown role gets default policy, not admin" in proves


# ---------------------------------------------------------------------------
# Layer 2: the policy must be wired to a surface that has a principal
# ---------------------------------------------------------------------------
def test_surface_probe_fails_when_nothing_calls_the_authorizer(disabled):
    """THE regression test for this card. Source file present, zero call sites."""
    assert disabled["status"] == authz_evidence.STATUS_NOT_SATISFIED
    assert disabled["enforced"] is False
    assert "no authorize_tool()" in disabled["reason"]


def test_surface_probe_fails_when_surface_ignores_the_policy():
    result = authz_evidence.probe_mcp_authorization(
        authorizer=mcp_tool_authorizer.MCPToolAuthorizer(config=DEFAULT_MATRIX),
        surface=PermissiveSurface(),
    )
    assert result["status"] == authz_evidence.STATUS_NOT_SATISFIED
    assert "not consulting the policy" in result["reason"]


def test_monitor_mode_is_never_satisfied():
    """A denial that is logged and then ignored is evidence of monitoring."""
    result = authz_evidence.probe_mcp_authorization(
        authorizer=mcp_tool_authorizer.MCPToolAuthorizer(config=DEFAULT_MATRIX),
        surface=FakeSurface("monitor"),
    )
    assert result["status"] == authz_evidence.STATUS_PARTIAL
    assert result["enforced"] is False
    assert "does not bind" in result["reason"]


def test_enforcing_surface_is_satisfied(enforcing):
    assert enforcing["status"] == authz_evidence.STATUS_SATISFIED
    assert enforcing["enforced"] is True
    assert enforcing["surface"]["denied_for_role"]["denied"] >= 1


# ---------------------------------------------------------------------------
# The stdio scope-out: recorded, with rationale and compensating controls
# ---------------------------------------------------------------------------
def test_scope_is_named_not_platform_wide(enforcing):
    assert enforcing["scope"]["in_scope_surfaces"] == [authz_evidence.IN_SCOPE_SURFACE]
    assert "not claimed platform-wide" in enforcing["scope"]["claim"]


def test_stdio_scope_out_records_its_rationale(enforcing):
    scoped = enforcing["scope"]["scoped_out"][0]
    assert scoped["surface"] == "stdio"
    assert scoped["in_scope"] is False
    assert "No caller identity" in scoped["reason"]
    assert "self-asserted" in scoped["reason"]
    assert "tools/mcp/unified_server.py" in scoped["modules"]


def test_stdio_scope_out_records_its_compensating_controls(enforcing):
    scoped = enforcing["scope"]["scoped_out"][0]
    ids = {c["id"] for c in scoped["compensating_controls"]}
    assert ids == {"reversibility-gate", "pre-tool-use-hard-blocks", "file-access-tiers"}
    for control in scoped["compensating_controls"]:
        assert control["control"]
        assert control["bounds"]


def test_compensating_controls_actually_behave():
    """The scope-out is only defensible if what it points at holds."""
    probes = {p["id"]: p for p in authz_evidence.probe_compensating_controls()}
    assert probes["reversibility-gate"]["passed"], probes["reversibility-gate"]["detail"]
    assert probes["pre-tool-use-hard-blocks"]["passed"], probes["pre-tool-use-hard-blocks"]["detail"]
    assert probes["file-access-tiers"]["passed"], probes["file-access-tiers"]["detail"]


def test_scope_note_names_the_surface_and_the_controls():
    note = authz_evidence.scope_note()
    assert authz_evidence.IN_SCOPE_SURFACE in note
    assert "OUT OF SCOPE" in note
    assert "approval_gate.py" in note
    assert "pre_tool_use.py" in note
    assert "file_access_tiers.yaml" in note


# ---------------------------------------------------------------------------
# The four generators must all fail while enforcement is disabled
# ---------------------------------------------------------------------------
def _patch_probe(monkeypatch, result):
    monkeypatch.setattr(authz_evidence, "cached_probe", lambda: result)


def test_fedramp_ksi_evidence_is_zero_when_enforcement_disabled(monkeypatch, disabled, enforcing):
    ksi = importlib.import_module("tools.compliance.fedramp_ksi_generator")
    collector = ksi.EVIDENCE_COLLECTORS["mcp_tool_authorizer"]

    _patch_probe(monkeypatch, disabled)
    assert collector(None, "proj-1") == 0

    _patch_probe(monkeypatch, enforcing)
    assert collector(None, "proj-1") == 1


def test_fedramp_ksi_attaches_the_scope_note(monkeypatch, disabled):
    ksi = importlib.import_module("tools.compliance.fedramp_ksi_generator")
    _patch_probe(monkeypatch, disabled)

    notes = ksi._scope_notes_for(["mcp_tool_authorizer", "audit_trail"])
    assert "OUT OF SCOPE" in notes["mcp_tool_authorizer"]
    assert ksi._scope_notes_for(["audit_trail"]) == {}


def test_owasp_agentic_mcp_rbac_not_satisfied_when_enforcement_disabled(monkeypatch, disabled, enforcing):
    mod = importlib.import_module("tools.compliance.owasp_agentic_assessor")
    assessor = mod.OWASPAgenticAssessor()

    _patch_probe(monkeypatch, disabled)
    status, detail = assessor._check_mcp_rbac()
    assert status == "not_satisfied"
    assert "OUT OF SCOPE" in detail

    _patch_probe(monkeypatch, enforcing)
    assert assessor._check_mcp_rbac()[0] == "satisfied"


def test_owasp_asi_02_needs_both_halves(monkeypatch, disabled, enforcing):
    mod = importlib.import_module("tools.compliance.owasp_asi_assessor")
    status_of = mod.OWASPASIAssessor._asi_02_status

    _patch_probe(monkeypatch, disabled)
    # Chain events alone used to be enough for "satisfied".
    assert status_of(True)[0] == "partially_satisfied"
    assert status_of(False)[0] == "not_satisfied"

    _patch_probe(monkeypatch, enforcing)
    assert status_of(True)[0] == "satisfied"
    assert status_of(False)[0] == "partially_satisfied"


def test_red_team_rt_os_003_fails_when_enforcement_disabled(monkeypatch, disabled, enforcing):
    mod = importlib.import_module("tools.security.red_team_registry")

    _patch_probe(monkeypatch, disabled)
    result = mod.OutputSafetyPlugin().run_checks()
    failed = [f["test"] for f in result["findings"]]
    assert any("RT-OS-003: MCP per-tool authorization enforced" in t for t in failed)
    assert result["passed"] is False

    _patch_probe(monkeypatch, enforcing)
    result = mod.OutputSafetyPlugin().run_checks()
    failed = [f["test"] for f in result["findings"]]
    assert not any("RT-OS-003" in t for t in failed), failed


def test_production_audit_sec_005_warns_when_enforcement_disabled(monkeypatch, disabled, enforcing):
    mod = importlib.import_module("tools.testing.production_audit")

    _patch_probe(monkeypatch, disabled)
    check = mod.check_owasp_agentic()
    assert check.status == "warn"
    assert check.details["mcp_per_tool_authorization"]["status"] == "not_satisfied"
    assert check.details["mcp_per_tool_authorization"]["scope"]["scoped_out"][0]["surface"] == "stdio"

    _patch_probe(monkeypatch, enforcing)
    assert mod.check_owasp_agentic().status == "pass"


GENERATORS = (
    "tools/compliance/fedramp_ksi_generator.py",
    "tools/compliance/owasp_agentic_assessor.py",
    "tools/compliance/owasp_asi_assessor.py",
    "tools/security/red_team_registry.py",
    "tools/testing/production_audit.py",
)


def _code_string_constants(source):
    """Every string literal in a module except docstrings.

    Prose describing the old defect is fine and is expected; a *path* to the
    authorizer source is what would mean a generator is back to building a
    filesystem check out of it.
    """
    import ast

    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


@pytest.mark.parametrize("rel", GENERATORS)
def test_no_generator_evidences_this_control_by_file_existence(rel):
    """No generator may reference the authorizer as a filesystem path again."""
    src = (authz_evidence.BASE_DIR / rel).read_text(encoding="utf-8", errors="ignore")
    offenders = [s for s in _code_string_constants(src) if "mcp_tool_authorizer.py" in s]
    assert not offenders, (
        f"{rel} names the authorizer source file as a path {offenders}; "
        "evidence for this control must come from tools/security/mcp_authz_evidence.py"
    )


# ---------------------------------------------------------------------------
# Cache behaviour — a memoized verdict must not be served to an injecting caller
# ---------------------------------------------------------------------------
def test_cached_probe_does_not_leak_into_injected_probes():
    first = authz_evidence.cached_probe()
    assert authz_evidence.cached_probe() is first

    injected = authz_evidence.probe_mcp_authorization(
        authorizer=mcp_tool_authorizer.MCPToolAuthorizer(config=DEFAULT_MATRIX),
        surface=FakeSurface("enforce"),
    )
    assert injected is not first
    assert injected["status"] == authz_evidence.STATUS_SATISFIED
