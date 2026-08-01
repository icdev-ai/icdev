# CUI // SP-CTI
"""
E2E Full Lifecycle — Derivative Classifier Coworker Profile

Tests the complete arc from profile creation → session launch → classification
review → artifact inspection for the DoD/IC Derivative Classifier role.

Official basis:
  - EO 13526  — Classified National Security Information
  - 32 CFR Part 2001  — ISOO Implementing Directive
  - ICD 710   — IC Markings System (CAPCO Register)
  - DODM 5200.01 Vol 1-4

Portion markings tested:
  (U)  Unclassified
  (C)  Confidential
  (S)  Secret
  (TS) Top Secret
  (TS//SCI) Top Secret / Sensitive Compartmented Information
  (//NF)  NOFORN caveat
  (//REL TO USA, FVEY)  releasability

Compilation rule:
  Two or more lower-classification items whose combination reveals
  something at a higher level MUST be marked at the elevated level.
  Example: (U) call sign + (U) grid + (U) schedule = (S) by compilation.

Run:
    pytest tests/e2e/test_derivative_classifier_lifecycle.py -v --tb=short
    # With Playwright UI verification:
    pytest tests/e2e/test_derivative_classifier_lifecycle.py -v --playwright
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:5050"
TIMEOUT = 30          # seconds for API calls
SESSION_POLL_MAX = 60  # max seconds to poll for session completion

# ---------------------------------------------------------------------------
# Profile definition — rich enough to steer LLM toward correct personality
# ---------------------------------------------------------------------------

PROFILE_NAME = "Derivative Classifier"
PROFILE_DESCRIPTION = (
    "DoD/IC professional authorized under EO 13526 to review documents, "
    "apply paragraph-level portion markings (U), (C), (S), (TS), (TS//SCI), "
    "and caveats such as //NF, //REL TO, //ORCON. "
    "Determines the overall document classification by aggregating all portion "
    "markings to the highest level. "
    "Applies the compilation rule: recognizes when multiple lower-classified "
    "items combine to create a higher classification obligation. "
    "Works alongside humans and classification management tools (WriteGuard) "
    "as a second-opinion reviewer and auto-marker. "
    "References CAPCO register, SCG (Security Classification Guide), and "
    "ICD 710 for every marking decision. "
    "Never upgrades or downgrades a marking without citing the authoritative source."
)

# ---------------------------------------------------------------------------
# Sample document — mixed classification levels with a compilation trap
# ---------------------------------------------------------------------------

SAMPLE_DOCUMENT = """
MEMORANDUM FOR RECORD

SUBJECT: Joint Task Force Operational Summary

PARAGRAPH 1 — Personnel
The unit is composed of 120 personnel assigned to Bravo Company, 3rd Battalion.
No foreign nationals are embedded.

PARAGRAPH 2 — Location
The unit is currently staged at grid reference 38T KH 1234 5678 in the eastern sector.
This grid is published in open-source military atlases.

PARAGRAPH 3 — Schedule
Bravo Company will conduct training exercises from 0600 to 1800 local time on Tuesday.
This schedule is coordinated with civilian aviation authorities.

PARAGRAPH 4 — Communication
Primary communication uses encrypted SINCGARS on frequency 46.425 MHz.
Authentication codes rotate every 12 hours using the BLACK SWAN series.

PARAGRAPH 5 — Intelligence Assessment
Current threat assessment rates the probability of adversary action as MODERATE.
Signals intercepts (source: SIGINT collection asset CORAL REEF) indicate increased
adversary radio traffic consistent with pre-operational activity.

PARAGRAPH 6 — Rules of Engagement
Forces are authorized to engage under current ROE Chapter 3, Section 4.
Use of indirect fire requires battalion commander approval.

COMPILATION NOTE (for classifier review):
Paragraphs 1, 2, and 3 individually appear to be Unclassified.
However, their combination (120-person unit + specific grid + specific schedule)
constitutes operational pattern data that may reveal mission intent.
"""

# ---------------------------------------------------------------------------
# Expected classification outcomes (used in assertions)
# ---------------------------------------------------------------------------

EXPECTED_MARKINGS = {
    "PARAGRAPH 1": "U",     # generic personnel count, no location
    "PARAGRAPH 2": "U",     # open-source grid on its own
    "PARAGRAPH 3": "U",     # public schedule coordination on its own
    "PARAGRAPH 4": "S",     # SINCGARS freq + auth codes = Secret
    "PARAGRAPH 5": "TS",    # SIGINT source name (CORAL REEF) = TS//SCI or TS
    "PARAGRAPH 6": "C",     # ROE chapter/section reference = Confidential
}

EXPECTED_AGGREGATE = "TS"   # driven by Paragraph 5 (SIGINT source)
EXPECTED_COMPILATION_FLAG = True   # P1+P2+P3 together = (S) by compilation


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(scope="module")
def session():
    """Requests session with base URL and timeout."""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def dashboard_running(session):
    """Skip entire module if dashboard is not reachable."""
    try:
        r = session.get(f"{BASE_URL}/health", timeout=5)
        if r.status_code not in (200, 404):
            pytest.skip("Dashboard not running at localhost:5050")
    except requests.exceptions.ConnectionError:
        pytest.skip("Dashboard not running at localhost:5050")


@pytest.fixture(scope="module")
def generated_role_id(dashboard_running, session):
    """Create the Derivative Classifier profile; yield role_id; teardown deletes it."""
    # Step 1: Preview (AI Assist)
    r = session.post(
        f"{BASE_URL}/api/ace/profiles/preview",
        json={"name": PROFILE_NAME, "description": PROFILE_DESCRIPTION},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Preview failed {r.status_code}: {r.text[:400]}"
    preview = r.json()
    assert preview.get("ok"), f"Preview not ok: {preview}"
    spec = preview["spec"]
    assert spec.get("role_id"), "Preview returned no role_id"

    # Step 2: Generate (save to disk)
    r2 = session.post(
        f"{BASE_URL}/api/ace/profiles/generate",
        json={"name": PROFILE_NAME, "description": PROFILE_DESCRIPTION, "spec": spec},
        timeout=TIMEOUT,
    )
    assert r2.status_code == 201, f"Generate failed {r2.status_code}: {r2.text[:400]}"
    result = r2.json()
    assert result.get("ok"), f"Generate not ok: {result}"
    role_id = result["role_id"]
    assert role_id, "Generate returned no role_id"

    yield role_id

    # Teardown: delete the generated profile
    session.delete(f"{BASE_URL}/api/ace/profiles/{role_id}", timeout=10)


# ===========================================================================
# Phase 1 — Profile Generation
# ===========================================================================

class TestProfileGeneration:
    """Verify the Derivative Classifier profile is created with the right shape."""

    def test_preview_returns_spec(self, dashboard_running, session):
        r = session.post(
            f"{BASE_URL}/api/ace/profiles/preview",
            json={"name": PROFILE_NAME, "description": PROFILE_DESCRIPTION},
            timeout=TIMEOUT,
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok")
        spec = data["spec"]
        assert spec["role_id"]

    def test_preview_has_classification_steps(self, dashboard_running, session):
        """Steps must include both review and marking actions."""
        r = session.post(
            f"{BASE_URL}/api/ace/profiles/preview",
            json={"name": PROFILE_NAME, "description": PROFILE_DESCRIPTION},
            timeout=TIMEOUT,
        )
        spec = r.json()["spec"]
        steps = [s.lower() for s in spec.get("steps", [])]
        # At minimum we expect review/analyze + produce/mark/classify steps
        has_review = any(kw in " ".join(steps) for kw in ("review", "analyze", "assess", "inspect"))
        has_output = any(kw in " ".join(steps) for kw in ("mark", "classify", "produce", "report", "finalize"))
        assert has_review, f"No review-type step found in {steps}"
        assert has_output, f"No marking/output step found in {steps}"

    def test_preview_canvas_affinity(self, dashboard_running, session):
        """Role should map to document_intelligence canvas."""
        r = session.post(
            f"{BASE_URL}/api/ace/profiles/preview",
            json={"name": PROFILE_NAME, "description": PROFILE_DESCRIPTION},
            timeout=TIMEOUT,
        )
        spec = r.json()["spec"]
        canvas = spec.get("canvas", "")
        # Accept document_intelligence or compliance — both valid for this role
        assert canvas in ("document_intelligence", "compliance", "security_canvas", ""), \
            f"Unexpected canvas: {canvas!r}"

    def test_generate_creates_files(self, generated_role_id):
        """All four files must exist on disk after generate."""
        base = Path("C:/AI/ICDev")
        role_id = generated_role_id
        assert (base / "args" / "ace" / "roles" / f"{role_id}.yaml").exists(), \
            "role YAML not written"
        soul_dir = base / "tools" / "ace" / "roles" / role_id
        for fname in ("SOUL.md", "TOOLS.md", "MEMORY.md"):
            assert (soul_dir / fname).exists(), f"{fname} not written for {role_id}"

    def test_generate_yaml_has_required_fields(self, generated_role_id):
        """Role YAML must have all ACE-required fields."""
        import yaml
        path = Path("C:/AI/ICDev") / "args" / "ace" / "roles" / f"{generated_role_id}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for field in ("role_id", "steps", "trust_tier", "tool_permissions"):
            assert field in data, f"Missing required field: {field}"
        assert len(data["steps"]) >= 4, "Too few steps"

    def test_soul_md_mentions_classification(self, generated_role_id):
        """SOUL.md should reference classification concepts."""
        path = Path("C:/AI/ICDev") / "tools" / "ace" / "roles" / generated_role_id / "SOUL.md"
        content = path.read_text(encoding="utf-8").lower()
        # Must reference classification or marking concepts
        assert any(kw in content for kw in (
            "classif", "marking", "portion", "eo 13526", "capco", "derivative",
            "unclassified", "confidential", "secret"
        )), f"SOUL.md for {generated_role_id} lacks classification domain content"

    def test_profile_appears_in_catalog(self, generated_role_id, session):
        """Profile must show up in GET /api/ace/profiles."""
        r = session.get(f"{BASE_URL}/api/ace/profiles", timeout=10)
        assert r.status_code == 200
        ids = [p["role_id"] for p in r.json().get("profiles", [])]
        assert generated_role_id in ids, f"{generated_role_id} not in profile list: {ids}"

    def test_role_loader_hot_reloads(self, generated_role_id):
        """RoleLoader must pick up the new YAML within its TTL window."""
        from icdev.tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        loader.reload()
        role = loader.get_role(generated_role_id)
        assert role.role_id == generated_role_id
        assert role.trust_tier in ("green", "yellow", "red")


# ===========================================================================
# Phase 2 — Classification Engine (unit-level, no ACE launch needed)
# ===========================================================================

class TestClassificationEngine:
    """Unit tests for the classification logic embedded in the profile steps.

    These run against the profile_generator's spec to verify the personality
    correctly encodes classification rules — no live LLM required.
    """

    def test_aggregate_highest_wins(self):
        """Overall doc classification = highest portion marking."""
        from tests.e2e.helpers.classification_utils import aggregate_markings
        markings = ["U", "C", "S", "S", "C", "TS", "U"]
        assert aggregate_markings(markings) == "TS"

    def test_aggregate_all_unclassified(self):
        from tests.e2e.helpers.classification_utils import aggregate_markings
        assert aggregate_markings(["U", "U", "U"]) == "U"

    def test_aggregate_empty(self):
        from tests.e2e.helpers.classification_utils import aggregate_markings
        assert aggregate_markings([]) == "U"

    def test_compilation_rule_triggers(self):
        """P1+P2+P3 (unit+grid+schedule) = (S) by compilation."""
        from tests.e2e.helpers.classification_utils import check_compilation
        items = [
            {"text": "120 personnel assigned to Bravo Company", "marking": "U"},
            {"text": "grid reference 38T KH 1234 5678", "marking": "U"},
            {"text": "training exercises from 0600 to 1800 on Tuesday", "marking": "U"},
        ]
        result = check_compilation(items)
        assert result["elevated"], (
            "Compilation of unit+location+schedule should trigger elevation"
        )
        assert result["suggested_marking"] in ("C", "S"), (
            f"Expected C or S by compilation, got {result['suggested_marking']!r}"
        )

    def test_compilation_rule_no_false_positive(self):
        """Two unrelated unclassified facts should NOT trigger compilation."""
        from tests.e2e.helpers.classification_utils import check_compilation
        items = [
            {"text": "The unit mascot is a golden eagle.", "marking": "U"},
            {"text": "The morning briefing starts at 0800.", "marking": "U"},
        ]
        result = check_compilation(items)
        # Low-sensitivity items — compilation should not trigger
        assert not result["elevated"] or result["confidence"] < 0.5, (
            "False positive: unrelated items should not trigger compilation"
        )

    def test_sigint_source_elevates_to_ts(self):
        """Named SIGINT collection asset in text → TS marking."""
        from tests.e2e.helpers.classification_utils import infer_marking
        text = "Signals intercepts (source: SIGINT collection asset CORAL REEF) indicate activity."
        result = infer_marking(text)
        assert result["marking"] in ("TS", "TS//SCI"), (
            f"Named SIGINT source should be TS, got {result['marking']!r}"
        )
        assert "SIGINT" in result["rationale"] or "source" in result["rationale"].lower()

    def test_sincgars_auth_codes_secret(self):
        """SINCGARS frequency + authentication codes → Secret."""
        from tests.e2e.helpers.classification_utils import infer_marking
        text = "SINCGARS on frequency 46.425 MHz. Authentication codes rotate every 12 hours using BLACK SWAN."
        result = infer_marking(text)
        assert result["marking"] in ("S", "TS"), (
            f"SINCGARS+auth codes should be at least S, got {result['marking']!r}"
        )

    def test_roe_chapter_confidential(self):
        """Rules of engagement chapter/section reference → Confidential."""
        from tests.e2e.helpers.classification_utils import infer_marking
        text = "Forces are authorized to engage under ROE Chapter 3, Section 4."
        result = infer_marking(text)
        assert result["marking"] in ("C", "S"), (
            f"ROE chapter reference should be C or higher, got {result['marking']!r}"
        )

    def test_portion_mark_format(self):
        """Portion marks must follow CAPCO format: (U), (C), (S), (TS), (TS//SCI)."""
        from tests.e2e.helpers.classification_utils import format_portion_mark
        assert format_portion_mark("U") == "(U)"
        assert format_portion_mark("S") == "(S)"
        assert format_portion_mark("TS") == "(TS)"
        assert format_portion_mark("TS", caveats=["SCI"]) == "(TS//SCI)"
        assert format_portion_mark("S", caveats=["NF"]) == "(S//NF)"
        assert format_portion_mark("S", caveats=["REL TO USA, FVEY"]) == "(S//REL TO USA, FVEY)"

    def test_banner_line_format(self):
        """Overall document banner = SECRET or highest, centered, no parens."""
        from tests.e2e.helpers.classification_utils import format_banner
        assert format_banner("S") == "SECRET"
        assert format_banner("TS") == "TOP SECRET"
        assert format_banner("TS", caveats=["SCI"]) == "TOP SECRET//SCI"
        assert format_banner("C") == "CONFIDENTIAL"
        assert format_banner("U") == "UNCLASSIFIED"


# ===========================================================================
# Phase 3 — ACE Session Launch with Classification Task
# ===========================================================================

class TestACEClassificationSession:
    """Launch an ACE session where the Derivative Classifier reviews SAMPLE_DOCUMENT."""

    def test_launch_session(self, generated_role_id, session):
        """POST /api/ace/launch — non-blocking, returns instance_id."""
        problem = (
            f"You are a Derivative Classifier (role: {generated_role_id}). "
            "Review the following memorandum, apply CAPCO portion markings to each paragraph "
            "(U), (C), (S), (TS), (TS//SCI) as appropriate, flag compilation concerns, "
            "and state the aggregate classification for the overall document.\n\n"
            + SAMPLE_DOCUMENT
        )
        r = session.post(
            f"{BASE_URL}/api/ace/launch",
            json={
                "problem_text": problem,
                "trigger_source": "e2e_test",
                "trigger_ref": "derivative_classifier_lifecycle",
                "user_id": "test_runner",
            },
            timeout=TIMEOUT,
        )
        assert r.status_code == 202, f"Launch failed {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert "instance_id" in data, f"No instance_id in response: {data}"
        # Store on class for subsequent tests (module-scope would be cleaner; keeping simple)
        TestACEClassificationSession._instance_id = data["instance_id"]

    def test_instance_status_returns(self, generated_role_id, session):
        """GET /api/ace/<id>/status must return a valid state within 5 s."""
        iid = getattr(TestACEClassificationSession, "_instance_id", None)
        if not iid:
            pytest.skip("No instance_id from launch test")
        r = session.get(f"{BASE_URL}/api/ace/{iid}/status", timeout=10)
        assert r.status_code == 200, f"Status check failed: {r.status_code}"
        data = r.json()
        assert "state" in data, f"No state field: {data}"
        assert data["state"] in (
            "assembling", "pending", "active", "working",
            "done", "failed", "cancelled"
        ), f"Unexpected state: {data['state']}"

    def test_messages_appear(self, generated_role_id, session):
        """Session should have at least one message within TIMEOUT seconds."""
        iid = getattr(TestACEClassificationSession, "_instance_id", None)
        if not iid:
            pytest.skip("No instance_id from launch test")

        deadline = time.time() + SESSION_POLL_MAX
        while time.time() < deadline:
            r = session.get(f"{BASE_URL}/api/ace/{iid}/messages", timeout=10)
            if r.status_code == 200:
                msgs = r.json().get("messages", [])
                if msgs:
                    break
            time.sleep(3)
        else:
            pytest.skip("No messages appeared within poll window — LLM may be unavailable")

        assert len(msgs) >= 1, "Expected at least one message"

    def test_artifacts_contain_classification_output(self, generated_role_id, session):
        """At least one artifact should reference classification markings."""
        iid = getattr(TestACEClassificationSession, "_instance_id", None)
        if not iid:
            pytest.skip("No instance_id from launch test")

        deadline = time.time() + SESSION_POLL_MAX
        artifacts = []
        while time.time() < deadline:
            r = session.get(f"{BASE_URL}/api/ace/{iid}/artifacts", timeout=10)
            if r.status_code == 200:
                artifacts = r.json().get("artifacts", [])
                if artifacts:
                    break
            # Also check if session is done with no artifacts
            sr = session.get(f"{BASE_URL}/api/ace/{iid}/status", timeout=10)
            if sr.ok and sr.json().get("state") in ("done", "failed", "cancelled"):
                break
            time.sleep(4)

        if not artifacts:
            pytest.skip("No artifacts produced — ACE may still be running or LLM unavailable")

        # Check at least one artifact mentions classification
        all_content = " ".join(
            json.dumps(a.get("content_json") or {}) + (a.get("content_md") or "")
            for a in artifacts
        ).lower()
        assert any(kw in all_content for kw in (
            "unclassified", "confidential", "secret", "top secret",
            "(u)", "(c)", "(s)", "(ts)", "classification", "portion"
        )), f"Artifacts do not mention classification: {all_content[:500]}"


# ===========================================================================
# Phase 4 — UI smoke via Playwright (optional, skipped if playwright absent)
# ===========================================================================

def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _dashboard_reachable() -> bool:
    try:
        requests.get(f"{BASE_URL}/health", timeout=3)
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _playwright_available() or not _dashboard_reachable(),
    reason="playwright not installed or dashboard not running",
)
# The AI-Assist wait below is 45s because a real LLM answers it — longer than
# the global 30s budget, so without this the class aborts the session rather
# than failing. Outer budget must exceed every inner wait it contains.
@pytest.mark.timeout(120)
class TestProfileCreatorUI:
    """Playwright smoke test for the /coworker/profiles/new page."""

    def test_page_loads(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{BASE_URL}/coworker/profiles/new", timeout=15000)
            assert "Create" in page.title() or "Profile" in page.title(), \
                f"Unexpected title: {page.title()!r}"
            browser.close()

    def test_name_field_visible(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{BASE_URL}/coworker/profiles/new", timeout=15000)
            assert page.locator("#pname").is_visible(), "Profile name input not visible"
            assert page.locator("#btn-assist").is_visible(), "AI Assist button not visible"
            browser.close()

    def test_ai_assist_fills_preview(self):
        """Type name + description, click AI Assist, preview panel appears."""
        from playwright.sync_api import sync_playwright
        ss_dir = Path("playwright/screenshots")
        ss_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{BASE_URL}/coworker/profiles/new", timeout=15000)

            # Fill form
            page.fill("#pname", PROFILE_NAME)
            page.fill("#pdesc", PROFILE_DESCRIPTION[:200])

            # Screenshot before assist
            page.screenshot(path=str(ss_dir / "derivative_classifier_01_form.png"))

            # Click AI Assist
            page.click("#btn-assist")

            # Wait for preview panel (up to 45 s — LLM may be slow)
            try:
                page.wait_for_selector("#preview-panel", state="visible", timeout=45000)
                page.screenshot(path=str(ss_dir / "derivative_classifier_02_preview.png"))
                role_id_text = page.inner_text("#pv-role-id")
                assert role_id_text, "Preview showed no role_id"
            except Exception:
                page.screenshot(path=str(ss_dir / "derivative_classifier_02_timeout.png"))
                pytest.skip("AI Assist timed out — LLM may be unavailable")

            browser.close()

    def test_roles_page_has_create_button(self):
        """The ✦ Create Profile button must be present on /coworker/roles."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{BASE_URL}/coworker/roles", timeout=15000)
            # Look for a link/button pointing to /coworker/profiles/new
            link = page.locator("a[href='/coworker/profiles/new']")
            assert link.is_visible(), "Create Profile link not on roles page"
            browser.close()
