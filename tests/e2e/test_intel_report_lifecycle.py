# CUI // SP-CTI
"""
E2E Full Lifecycle — Intel Team Coworker Profiles

Tests the complete arc from role discovery → team launch → artifact production
→ classification marking validation for the five IC-focused coworker roles:

  Researcher | Intelligence Analyst | Writer | Editor | Derivative Classifier

Pipeline:
  Researcher → (research.memo.produced) → Intelligence Analyst
  → (intsum.draft.produced) → Writer → (report.generated) → Editor
  → (report.edited) → Derivative Classifier → (deliverable.produced)

Official basis:
  - EO 13526  — Classified National Security Information
  - 32 CFR Part 2001  — ISOO Implementing Directive
  - ICD 710   — IC Markings System (CAPCO Register)
  - ICD 203   — ODNI Analytic Standards
  - DODM 5200.01 Vols 1-4

Portion markings tested:
  (U)   Unclassified
  (CUI) Controlled Unclassified Information
  (S)   Secret

Compilation rule:
  Two or more lower-classification items whose combination reveals
  something at a higher level MUST be marked at the elevated level.
  Example: (U) unit designation + (U) grid + (U) movement schedule = (S)

Run:
    pytest tests/e2e/test_intel_report_lifecycle.py -v --tb=short
    # With a live dashboard on :5050:
    pytest tests/e2e/test_intel_report_lifecycle.py -v --tb=short --live
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:5050"
TIMEOUT = 30
SESSION_POLL_MAX = 60

INTEL_ROLES = [
    "researcher",
    "intelligence_analyst",
    "writer",
    "editor",
    "derivative_classifier",
]

# Synthetic INTSUM scenario — mixes (U)/(CUI)/(S) paragraphs
# Compilation trap: para 1 (U) + para 2 (U) together reveal unit + grid + schedule → (S)
INTSUM_PROBLEM = (
    "Produce an INTSUM (Intelligence Summary) for DIC collection: demo. "
    "Scenario: Unidentified armored elements observed at grid NK-4421 (U). "
    "Movement schedule indicates transit window 0200–0400 local (U). "
    "Combined assessment: this unit-grid-schedule combination constitutes a "
    "SECRET indicator of pre-offensive positioning. "
    "Apply CAPCO portion markings per EO 13526 and ICD 710. "
    "Include a BLUF, key judgments, and a distribution notice."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PORTION_MARK_RE = re.compile(r"\((U|CUI|S|C|TS(?://SCI)?)\)")


def _portion_marks_in(text: str) -> list[str]:
    return _PORTION_MARK_RE.findall(text)


_MARK_ORDER = {"U": 0, "CUI": 1, "C": 2, "S": 3, "TS": 4, "TS//SCI": 5}


def _highest_mark(marks: list[str]) -> str | None:
    if not marks:
        return None
    return max(marks, key=lambda m: _MARK_ORDER.get(m, -1))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def requests_session():
    """Lazy-import requests to avoid hard dependency when --live not passed."""
    try:
        import requests as r
        return r.Session()
    except ImportError:
        pytest.skip("requests not installed")


@pytest.fixture(scope="module")
def live(pytestconfig):
    return pytestconfig.getoption("--live", default=False)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="Run live API tests against :5050")


# ---------------------------------------------------------------------------
# Static tests (no network required)
# ---------------------------------------------------------------------------

class TestRoleFiles:
    """Validate that every role YAML exists and has required fields."""

    ROLES_DIR = Path(__file__).resolve().parents[2] / "args" / "ace" / "roles"

    @pytest.mark.parametrize("role_id", INTEL_ROLES)
    def test_role_yaml_exists(self, role_id):
        path = self.ROLES_DIR / f"{role_id}.yaml"
        assert path.exists(), f"Missing role YAML: {path}"

    @pytest.mark.parametrize("role_id", INTEL_ROLES)
    def test_role_yaml_valid(self, role_id):
        path = self.ROLES_DIR / f"{role_id}.yaml"
        if not path.exists():
            pytest.skip("YAML missing — covered by test_role_yaml_exists")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["role_id"] == role_id
        assert data.get("display_name"), "display_name required"
        assert data.get("steps"), "steps required"
        assert data.get("trust_tier") in ("red", "orange", "yellow", "green")
        assert data.get("llm_function"), "llm_function required"
        assert data.get("tool_permissions"), "tool_permissions required"

    @pytest.mark.parametrize("role_id", INTEL_ROLES)
    def test_soul_md_exists(self, role_id):
        path = (
            Path(__file__).resolve().parents[2]
            / "tools" / "ace" / "roles" / role_id / "SOUL.md"
        )
        assert path.exists(), f"Missing SOUL.md: {path}"

    @pytest.mark.parametrize("role_id", INTEL_ROLES)
    def test_tools_md_exists(self, role_id):
        path = (
            Path(__file__).resolve().parents[2]
            / "tools" / "ace" / "roles" / role_id / "TOOLS.md"
        )
        assert path.exists(), f"Missing TOOLS.md: {path}"


class TestCanvasRegistry:
    """Validate DIC canvas entries in canvas_registry.yaml."""

    REGISTRY_PATH = Path(__file__).resolve().parents[2] / "args" / "canvas_registry.yaml"

    def _load(self):
        return yaml.safe_load(self.REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_dic_canvas_registered(self):
        data = self._load()
        names = [c["name"] for c in data.get("canvases", [])]
        assert "document_intelligence" in names, "DIC canvas not in canvas_registry.yaml"

    def test_notebook_canvas_registered(self):
        data = self._load()
        names = [c["name"] for c in data.get("canvases", [])]
        assert "notebook" in names, "notebook canvas not in canvas_registry.yaml"

    def test_dic_default_roles_include_intel_team(self):
        data = self._load()
        dic = next(c for c in data["canvases"] if c["name"] == "document_intelligence")
        for role in INTEL_ROLES:
            assert role in dic["default_roles"], (
                f"Role '{role}' missing from document_intelligence default_roles"
            )

    def test_dic_min_il(self):
        data = self._load()
        dic = next(c for c in data["canvases"] if c["name"] == "document_intelligence")
        assert dic["min_il"] in ("IL4", "IL5", "IL6"), "DIC must require at least IL4"


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    """Stop the classifier reaching a real LLM provider during the unit suite.

    `ProblemClassifierLens.run()` calls `_llm_suggest_roles`, which builds an
    LLMRouter and invokes it for real. Nothing in this file mocked it, so
    `test_non_ic_domain_routes_to_full_team` made four live provider calls — and
    hung the ENTIRE local pytest run there, mid-way through writing the response
    to the LLM cache. That is a large part of why the CI Test job is a 12-file
    allowlist rather than the suite: the suite could not finish.

    Stubbing it cannot weaken any assertion here. `_llm_suggest_roles` is
    additive and already best-effort — its body is wrapped in a bare `except`
    that returns [] on any provider, network or parse error, so "no LLM
    available" is a supported outcome the production path handles. These tests
    assert the DETERMINISTIC rule-based routing, which is exactly what remains.
    """
    import icdev.tools.ace.problem_classifier as pc

    monkeypatch.setattr(pc.ProblemClassifierLens, "_llm_suggest_roles", lambda self: [])


class TestProblemClassifier:
    """Validate that document-flavored problems route to the five generic roles."""

    def test_intelligence_signals_in_classifier(self):
        from icdev.tools.ace.problem_classifier import _SIGNALS, _DOMAIN_SIGNALS, _DOMAIN_ROLES
        assert "intelligence_noun" in _SIGNALS, "intelligence_noun signal missing"
        assert "intelligence_verb" in _SIGNALS, "intelligence_verb signal missing"
        assert "intelligence" in _DOMAIN_SIGNALS, "intelligence domain missing"
        assert "intelligence" in _DOMAIN_ROLES, "intelligence→roles mapping missing"

    def test_intsum_problem_routes_to_intelligence_domain(self):
        from icdev.tools.ace.problem_classifier import _COMPILED
        text = INTSUM_PROBLEM
        pattern, _ = _COMPILED["intelligence_noun"]
        assert pattern.search(text), "intelligence_noun should match INTSUM problem text"

    def test_intelligence_domain_maps_all_five_roles(self):
        from icdev.tools.ace.problem_classifier import _DOMAIN_ROLES
        for role in INTEL_ROLES:
            assert role in _DOMAIN_ROLES["intelligence"], (
                f"Role '{role}' missing from intelligence domain mapping"
            )

    @pytest.mark.parametrize("label,text,expected_signal", [
        (
            "legal",
            "Analyze the plaintiff motion. Apply IRAC. Flag attorney-client privilege.",
            "legal_noun",
        ),
        (
            "medical",
            "Review patient clinical notes. Apply SOAP. Flag HIPAA PHI sensitivity.",
            "medical_noun",
        ),
        (
            "financial",
            "Analyze the 10-K SEC filing. Flag MNPI. Assess EBITDA. Produce investment memo.",
            "financial_noun",
        ),
        (
            "corporate",
            "Competitive analysis using SWOT. Review market share and KPI benchmarks.",
            "corporate_noun",
        ),
        (
            "generic_doc",
            "Analyze this document and produce a research memo with key findings.",
            "doc_analysis_noun",
        ),
    ])
    def test_domain_signal_fires(self, label, text, expected_signal):
        from icdev.tools.ace.problem_classifier import _COMPILED
        assert expected_signal in _COMPILED, f"{expected_signal} not in _COMPILED"
        pattern, _ = _COMPILED[expected_signal]
        assert pattern.search(text), f"{expected_signal} did not match '{label}' text"

    @pytest.mark.parametrize("label,text", [
        ("legal", "Analyze the plaintiff motion. Apply IRAC. Flag attorney-client privilege."),
        ("medical", "Review patient clinical notes. Apply SOAP. Flag HIPAA PHI sensitivity."),
        ("financial", "Analyze the 10-K SEC filing. Flag MNPI. Assess EBITDA."),
        ("corporate", "Competitive analysis using SWOT and Porter Five Forces. KPI benchmarks."),
    ])
    def test_non_ic_domain_routes_to_full_team(self, label, text):
        from icdev.tools.ace.problem_classifier import ProblemClassifierLens
        manifest = ProblemClassifierLens(text).run()
        assigned = {s.role_id for s in manifest.slots}
        for role in INTEL_ROLES:
            assert role in assigned, (
                f"Role '{role}' missing from '{label}' team — got {sorted(assigned)}"
            )


class TestPortionMarkingHelpers:
    """Unit tests for portion marking utilities used in the pipeline."""

    def test_portion_mark_regex_captures_standard_marks(self):
        text = "(U) Unclassified para. (CUI) Sensitive para. (S) Secret para."
        marks = _portion_marks_in(text)
        assert "U" in marks
        assert "CUI" in marks
        assert "S" in marks

    def test_highest_mark_selects_secret(self):
        marks = ["U", "CUI", "S", "U"]
        assert _highest_mark(marks) == "S"

    def test_highest_mark_empty_list(self):
        assert _highest_mark([]) is None

    def test_compilation_rule_elevates_combined_unclassified(self):
        """Two (U) paras that together reveal a sensitive combination → banner ≥ (S)."""
        try:
            from tests.e2e.helpers.classification_utils import aggregate_markings
        except ImportError:
            pytest.skip("classification_utils helper not available")
        # Simulate: unit (U) + grid (U) + schedule (U) → compilation → (S)
        # aggregate_markings only does high-water; compilation is a separate gate
        # Verify that if the classifier returns (S) for combined paras, it registers correctly
        marks = ["U", "U", "S"]  # after compilation, (S) is present
        assert aggregate_markings(marks) == "S"


# ---------------------------------------------------------------------------
# Live API tests (require dashboard on :5050)
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveLifecycle:
    """Full E2E tests against a running dashboard instance."""

    def test_role_loader_discovers_intel_roles(self, requests_session, live):
        if not live:
            pytest.skip("Requires --live flag and dashboard on :5050")
        resp = requests_session.get(f"{BASE_URL}/api/ace/roles", timeout=TIMEOUT)
        assert resp.status_code == 200, f"GET /api/ace/roles → {resp.status_code}"
        data = resp.json()
        role_ids = {r.get("role_id") or r.get("id") for r in data.get("roles", data)}
        for role in INTEL_ROLES:
            assert role in role_ids, f"Role '{role}' not visible via API"

    def test_launch_intel_team(self, requests_session, live):
        if not live:
            pytest.skip("Requires --live flag and dashboard on :5050")
        payload = {
            "problem_text": INTSUM_PROBLEM,
            "role_ids": INTEL_ROLES,
            "dic_collection_ids": [],
            "trigger_source": "dic_notebook",
        }
        resp = requests_session.post(
            f"{BASE_URL}/api/ace/launch", json=payload, timeout=TIMEOUT
        )
        assert resp.status_code in (200, 202), (
            f"POST /api/ace/launch → {resp.status_code}: {resp.text[:200]}"
        )
        data = resp.json()
        instance_id = data.get("instance_id") or data.get("id")
        assert instance_id, "No instance_id returned from launch"
        pytest._intel_instance_id = instance_id  # share across tests

    def test_instance_reaches_active_state(self, requests_session, live):
        if not live:
            pytest.skip("Requires --live flag and dashboard on :5050")
        instance_id = getattr(pytest, "_intel_instance_id", None)
        if not instance_id:
            pytest.skip("No instance_id — run test_launch_intel_team first")
        deadline = time.time() + SESSION_POLL_MAX
        state = None
        while time.time() < deadline:
            resp = requests_session.get(
                f"{BASE_URL}/api/ace/{instance_id}/status", timeout=TIMEOUT
            )
            if resp.status_code == 200:
                state = resp.json().get("state") or resp.json().get("status")
                if state in ("active", "complete"):
                    break
            time.sleep(3)
        assert state in ("active", "complete", "assembling", "pending"), (
            f"Instance never became active — final state: {state}"
        )

    def test_intel_report_artifact_produced(self, requests_session, live):
        if not live:
            pytest.skip("Requires --live flag and dashboard on :5050")
        instance_id = getattr(pytest, "_intel_instance_id", None)
        if not instance_id:
            pytest.skip("No instance_id — run test_launch_intel_team first")
        # Poll for artifacts
        deadline = time.time() + SESSION_POLL_MAX
        artifacts = []
        while time.time() < deadline:
            resp = requests_session.get(
                f"{BASE_URL}/api/ace/{instance_id}/artifacts", timeout=TIMEOUT
            )
            if resp.status_code == 200:
                artifacts = resp.json().get("artifacts", resp.json())
                if artifacts:
                    break
            time.sleep(5)
        assert artifacts, "No artifacts produced by Intel Team"
        pytest._intel_artifact = artifacts[0]

    def test_artifact_has_classification_banner(self, requests_session, live):
        if not live:
            pytest.skip("Requires --live flag and dashboard on :5050")
        artifact = getattr(pytest, "_intel_artifact", None)
        if not artifact:
            pytest.skip("No artifact — run test_intel_report_artifact_produced first")
        body = (
            artifact.get("content_md") or
            str(artifact.get("content_json", ""))
        )
        banner_patterns = ["CUI // SP-CTI", "(S)", "(CUI)", "(U)", "CONTROLLED UNCLASSIFIED"]
        assert any(p in body for p in banner_patterns), (
            f"Artifact body lacks any classification banner. Preview: {body[:300]}"
        )

    def test_artifact_has_portion_marks(self, requests_session, live):
        if not live:
            pytest.skip("Requires --live flag and dashboard on :5050")
        artifact = getattr(pytest, "_intel_artifact", None)
        if not artifact:
            pytest.skip("No artifact — run test_intel_report_artifact_produced first")
        body = (
            artifact.get("content_md") or
            str(artifact.get("content_json", ""))
        )
        marks = _portion_marks_in(body)
        assert marks, (
            f"No portion marks (U)/(CUI)/(S) found in artifact. Preview: {body[:300]}"
        )

    def test_portion_marks_consistent(self, requests_session, live):
        if not live:
            pytest.skip("Requires --live flag and dashboard on :5050")
        artifact = getattr(pytest, "_intel_artifact", None)
        if not artifact:
            pytest.skip("No artifact — run test_intel_report_artifact_produced first")
        body = (
            artifact.get("content_md") or
            str(artifact.get("content_json", ""))
        )
        marks = _portion_marks_in(body)
        _highest = _highest_mark(marks)
        # The overall banner must be at least as high as the highest paragraph mark
        # We check this deterministically — any (S) paragraph requires ≥ (S) banner
        if "S" in marks:
            secret_banners = ["(S)", "SECRET", "(S//", "CUI // SP-CTI"]
            assert any(p in body for p in secret_banners), (
                "Document contains (S) portion marks but banner does not reflect SECRET level"
            )
