# CUI // SP-CTI
"""The engine must work for industries nobody enumerated in advance.

`problem_classifier._DOMAIN_ROLES` is a hand-written table of ~15 domains, every
one software-delivery shaped (build, devops, monitoring, analytics, compliance,
product management). Anything it does not recognise falls back to
`_FALLBACK_SLOTS` — an AI developer and a QA manager.

So an agricultural co-op, a maritime insurer and a veterinary practice all got
the same two software roles. The engine knew *how software is built* and nothing
about *what it is about*. Extending the table would only move the boundary; there
is no finite list of industries to enumerate.

These tests pin that the gap is detected from the problem text itself, with no
industry list anywhere in the path.
"""
from __future__ import annotations

import pytest

from icdev.tools.ace import sme_gap_detector as gd


def _manifest(*role_ids):
    from icdev.tools.ace.problem_classifier import RoleSlot, TeamManifest

    return TeamManifest(slots=[RoleSlot(role_id=r, count=1) for r in role_ids])


# Deliberately drawn from industries with no representation in the catalog, and
# phrased as ordinary requirements so the classifier matches on process wording.
AGRICULTURE = (
    "The system shall track grain moisture and silo aeration across forty tenant "
    "farms. It must alert the co-op agronomist when spoilage risk rises above the "
    "seasonal threshold. Acceptance criteria: every silo reports hourly, and harvest "
    "yield reconciles against the weighbridge ticket at intake."
)
VETERINARY = (
    "The system shall schedule large-animal veterinary callouts across rural "
    "practices. It must record controlled drug administration per animal against the "
    "practice licence. Acceptance criteria: every callout logs mileage, and withdrawal "
    "periods are enforced before an animal returns to the food chain."
)
MARITIME = (
    "The system shall price hull and cargo cover for coastal freight operators. It "
    "must apply war-risk exclusions per voyage and cite the underwriting clause relied "
    "upon. Acceptance criteria: every quote is auditable back to the policy wording and "
    "the surveyor condition report."
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("problem", [AGRICULTURE, VETERINARY, MARITIME])
def test_process_only_roster_is_a_gap(problem):
    """A roster of process roles and no subject expert is a gap.

    `requirements_engineer` is what these all match, because requirement
    phrasing is what the classifier recognises.
    """
    gap = gd.detect_sme_gap(problem, _manifest("requirements_engineer"))

    assert gap is not None
    assert "subject matter" in gap.reason
    assert gap.domain_description == problem.strip()


def test_fallback_only_roster_is_a_gap():
    from icdev.tools.ace.problem_classifier import _FALLBACK_SLOTS

    manifest = _manifest(*[s.role_id for s in _FALLBACK_SLOTS])
    gap = gd.detect_sme_gap(MARITIME, manifest)

    assert gap is not None
    assert gap.fallback_only is True


def test_matching_subject_expert_is_not_a_gap():
    """When the roster genuinely covers the subject, generate nothing."""
    problem = (
        "The system shall assess the threat surface of our container images and "
        "review vulnerabilities before release. It must map controls to the "
        "applicable framework. Acceptance criteria: every release carries a "
        "documented risk assessment and the compliance evidence is retained."
    )
    assert gd.detect_sme_gap(problem, _manifest("security_analyst")) is None


def test_short_problems_never_generate_a_specialist():
    """Without a length floor every one-liner would mint an expert."""
    assert gd.detect_sme_gap("fix the login bug", _manifest("requirements_engineer")) is None
    assert gd.detect_sme_gap("", _manifest()) is None


def test_confidence_is_not_the_signal():
    """A well-written requirements doc scores ~0.80 in ANY industry.

    Confidence measures recognition of requirement *phrasing*, so it is high and
    uninformative exactly when the subject matter is least familiar. Using it as
    the trigger is what made every industry look covered.
    """
    gap = gd.detect_sme_gap(
        MARITIME, _manifest("requirements_engineer"), max_confidence=0.80
    )
    assert gap is not None, "high confidence must not mask a missing subject expert"


# ---------------------------------------------------------------------------
# No industry knowledge anywhere in the path
# ---------------------------------------------------------------------------


def _executable_source(module) -> str:
    """Module source with comments and docstrings stripped.

    Industry names in *prose* are fine and often the clearest way to explain a
    heuristic; what matters is that none appear in code, where they would make
    behaviour depend on a list somebody has to extend. Asserting against raw
    source would fail on an explanatory comment, which is testing the writing
    rather than the logic.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)  # docstring
    return ast.unparse(tree).lower()  # comments are already absent from the AST


_FORBIDDEN_IN_CODE = (
    "biotech", "maritime", "agriculture", "veterinary", "healthcare",
    "fintech", "insurance", "retail", "logistics",
    "ai_developer", "qa_manager", "security_analyst", "devops_engineer",
)


def test_detector_code_names_no_industry_or_role():
    """No industry or role id may appear in the detector's executable code."""
    source = _executable_source(gd)
    for term in _FORBIDDEN_IN_CODE:
        assert term not in source, f"industry/role {term!r} hardcoded in gap detector"


def test_stopwords_are_not_a_domain_list():
    """The only word list present is requirement scaffolding, not subject matter.

    It says nothing about which domains exist, so it never needs extending when
    a new industry appears.
    """
    assert "shall" in gd._NON_DOMAIN_TOKENS
    assert "acceptance" in gd._NON_DOMAIN_TOKENS
    for subject_word in ("grain", "hull", "veterinary", "cargo", "diagnostic"):
        assert subject_word not in gd._NON_DOMAIN_TOKENS


def test_registry_code_names_no_industry():
    from icdev.tools.ace import sme_registry

    source = _executable_source(sme_registry)
    for term in ("biotech", "maritime", "agriculture", "veterinary", "fintech"):
        assert term not in source, f"industry {term!r} hardcoded in sme_registry"


# ---------------------------------------------------------------------------
# Resolution degrades safely
# ---------------------------------------------------------------------------


def test_resolution_failure_degrades_to_none(monkeypatch):
    """An unresolvable gap must fall back to the classifier roster, not block."""
    from icdev.tools.ace import sme_registry

    def _boom(*a, **k):
        raise RuntimeError("no provider")

    monkeypatch.setattr(sme_registry, "ensure_sme", _boom)
    assert gd.resolve_gap(gd.SmeGap(domain_description="anything", reason="test")) is None


def test_policy_rejection_degrades_to_none(monkeypatch):
    """A role refused by capability policy is not written and not returned."""
    from icdev.tools.ace import sme_registry

    def _refuse(*a, **k):
        raise PermissionError("capability policy")

    monkeypatch.setattr(sme_registry, "ensure_sme", _refuse)
    assert gd.resolve_gap(gd.SmeGap(domain_description="anything", reason="test")) is None


def test_adjudicator_sees_the_whole_catalog():
    """A candidate filtered out can never be chosen.

    Lexical pre-filtering could not connect "container image vulnerability
    scanning" to security_analyst, whose description says "reviews
    vulnerabilities" — and a recall bug in a filter is invisible and permanent.
    """
    from icdev.tools.ace.sme_registry import _catalog_listing

    listing = _catalog_listing()
    assert len(listing) > 50
    assert any(role_id == "security_analyst" for role_id, _ in listing)


def test_adjudicator_cannot_invent_a_role(monkeypatch):
    """A model answer outside the catalog must degrade to 'generate'.

    Returning an invented id would create a co-worker that fails
    role_not_found at launch — worse than generating a real specialist.
    """
    from icdev.tools.ace import sme_registry

    class _Resp:
        content = "totally_invented_role"

    class _Router:
        def invoke(self, *a, **k):
            return _Resp()

    monkeypatch.setattr("tools.llm.router.LLMRouter", lambda: _Router())
    assert sme_registry._adjudicate_near_miss("some domain") == ""
