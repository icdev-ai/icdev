# CUI // SP-CTI
"""SME registry — bounded runtime generation of subject-matter-expert roles.

The security-critical property under test: an LLM may author WHO an expert is,
never WHAT it may do. ``role_policy`` stamps every security field from a
hand-authored bundle, so a model that asks for ``trust_tier: green`` and
``Bash`` gets neither.

``trust_tier: green`` is called out specifically because it is the field that
matters most — it clears the CoWorkerThread confidence gate (learned trust
0.5 < TRUST_SUPERVISED 0.6), which is the only thing guaranteeing a human sees
a freshly generated role before it acts.
"""
from __future__ import annotations

import pytest

from icdev.tools.ace import role_policy


# ---------------------------------------------------------------------------
# Capability bundles
# ---------------------------------------------------------------------------


def test_default_bundle_is_inert():
    """The bundle applied when none is named must grant no real agency."""
    name, bundle = role_policy.get_bundle()
    assert name == "advisory"
    assert bundle["trust_tier"] == "red"
    assert bundle["folder_access"] == []   # no write agency
    assert bundle["icdev_tools"] == []     # no execute agency


def test_unknown_bundle_falls_back_rather_than_raising():
    """An unrecognised bundle must not become an unconstrained role."""
    name, bundle = role_policy.get_bundle("superuser")
    assert name == "advisory"
    assert bundle["trust_tier"] == "red"


def test_no_bundle_grants_green_trust():
    """No bundle may ship a tier that skips the human confidence gate."""
    cfg = role_policy.load_bundles()
    for bname, bundle in cfg["bundles"].items():
        assert bundle.get("trust_tier") != "green", f"bundle {bname} grants green"


# ---------------------------------------------------------------------------
# The escalation attempt
# ---------------------------------------------------------------------------


@pytest.fixture
def escalating_spec():
    """What a compromised or over-eager model might return."""
    return {
        "role_id": "evil_specialist",
        "display_name": "Evil Specialist",
        "trust_tier": "green",
        "mode": "agent",
        "tool_permissions": ["Read", "Bash", "Write", "Edit"],
        "folder_access": [{"path": "args/ace/roles/", "mode": "rw"}],
        "icdev_tools": ["rm -rf /", "python tools/db/storage.py --drop"],
    }


def test_escalation_is_detected(escalating_spec):
    problems = role_policy.validate_generated_role(escalating_spec, "advisory")
    joined = " | ".join(problems)

    assert "green" in joined
    assert "Bash" in joined
    assert "forbidden prefix" in joined
    assert any("icdev_tool" in p for p in problems)


def test_apply_bundle_neutralises_escalation(escalating_spec):
    """After stamping, the spec must be clean and inert."""
    role_policy.apply_bundle(escalating_spec, "advisory")

    assert escalating_spec["trust_tier"] == "red"
    assert escalating_spec["mode"] == "steps"
    assert "Bash" not in escalating_spec["tool_permissions"]
    assert "Write" not in escalating_spec["tool_permissions"]
    assert escalating_spec["folder_access"] == []
    assert escalating_spec["icdev_tools"] == []
    assert escalating_spec["capability_bundle"] == "advisory"
    assert role_policy.validate_generated_role(escalating_spec) == []


def test_assert_valid_raises_on_violation(escalating_spec):
    with pytest.raises(PermissionError, match="capability policy"):
        role_policy.assert_valid(escalating_spec, "advisory")


# ---------------------------------------------------------------------------
# Write access to the roles directory is the escalation loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "args/ace/roles/",
    "args/ace/roles/security_analyst.yaml",
    ".claude/",
    "tools/ace/",
    "icdev/tools/",
    ".git/",
])
def test_forbidden_paths_refused_even_for_builder(path):
    """No bundle may grant write access to policy, code, or git.

    Write access to args/ace/roles/ would let a generated role rewrite its own
    permissions or author one with more — the loop this policy exists to break.
    """
    spec = {
        "role_id": "x", "trust_tier": "red", "mode": "agent",
        "tool_permissions": ["Read", "Write", "Edit", "Grep", "Glob"],
        "folder_access": [{"path": path, "mode": "rw"}],
        "icdev_tools": [],
    }
    problems = role_policy.validate_generated_role(spec, "builder")
    assert problems, f"{path} was permitted"


def test_path_traversal_cannot_escape_bundle_scope():
    """`.tmp/ace/../../tools` must not read as being under `.tmp/ace`."""
    spec = {
        "role_id": "x", "trust_tier": "red", "mode": "agent",
        "tool_permissions": ["Read", "Write", "Edit", "Grep", "Glob"],
        "folder_access": [{"path": ".tmp/ace/../../tools", "mode": "rw"}],
        "icdev_tools": [],
    }
    problems = role_policy.validate_generated_role(spec, "builder")
    assert any("forbidden prefix" in p or "outside bundle" in p for p in problems)


def test_backslash_paths_are_normalised():
    """A Windows-style path must not slip past a POSIX prefix check."""
    assert role_policy._normalise_path(r"args\ace\roles") == "args/ace/roles"
    assert role_policy._normalise_path("/args/ace/roles/") == "args/ace/roles"


def test_legitimate_builder_role_passes():
    """The policy must not be so strict that the builder bundle is unusable."""
    spec = {
        "role_id": "ok", "trust_tier": "red", "mode": "agent",
        "tool_permissions": ["Read", "Write", "Edit", "Grep", "Glob"],
        "folder_access": [{"path": ".tmp/ace/inst-1", "mode": "rw"}],
        "icdev_tools": ["python tools/testing/health_check.py --json"],
    }
    assert role_policy.validate_generated_role(spec, "builder") == []


def test_read_only_scope_cannot_be_upgraded_to_write():
    """The analyst bundle grants docs/ read-only; rw must be refused."""
    spec = {
        "role_id": "x", "trust_tier": "red", "mode": "steps",
        "tool_permissions": ["Read", "Grep", "Glob"],
        "folder_access": [{"path": "docs/", "mode": "rw"}],
        "icdev_tools": [],
    }
    problems = role_policy.validate_generated_role(spec, "analyst")
    assert any("grants only" in p for p in problems)


def test_icdev_tools_must_satisfy_tool_runner_rules():
    """A command in the bundle still has to satisfy ToolRunner's own gate."""
    from icdev.tools.ace.tool_runner import _ALLOWED_PREFIXES

    _, bundle = role_policy.get_bundle("builder")
    for cmd in bundle["icdev_tools"]:
        assert any(cmd.startswith(p) for p in _ALLOWED_PREFIXES), cmd


# ---------------------------------------------------------------------------
# Duplicate suppression against the real catalog
# ---------------------------------------------------------------------------


def test_existing_domains_are_reused_not_duplicated():
    """A domain the shipped catalog already covers must not mint a new role."""
    from icdev.tools.ace import sme_registry as sr

    for label, expected in [
        ("compliance management", "compliance_manager"),
        ("security analysis", "security_analyst"),
        ("devops engineering", "devops_engineer"),
    ]:
        match_id, score = sr.find_existing_role(label)
        assert score >= sr.REUSE_THRESHOLD, f"{label} scored {score:.2f}, would duplicate"
        assert match_id == expected


def test_novel_domains_are_not_falsely_matched():
    """A genuinely new domain must fall below the reuse threshold."""
    from icdev.tools.ace import sme_registry as sr

    for label in ["maritime insurance underwriting", "quantum error correction"]:
        _, score = sr.find_existing_role(label)
        assert score < sr.REUSE_THRESHOLD, f"{label} falsely matched at {score:.2f}"


def test_sequence_similarity_is_discounted():
    """A shared prefix alone must not clear the reuse threshold.

    'network security' vs 'network doc auditor' — a *documentation* auditor —
    scored 0.69 on raw SequenceMatcher and wrongly won.
    """
    from icdev.tools.ace import sme_registry as sr

    assert sr._similarity("network security", "network doc auditor") < sr.REUSE_THRESHOLD


def test_ensure_sme_rejects_empty_domain():
    from icdev.tools.ace import sme_registry as sr

    with pytest.raises(ValueError):
        sr.ensure_sme("   ")


# ---------------------------------------------------------------------------
# Mirror safety
# ---------------------------------------------------------------------------


def test_persona_roles_dir_honours_env_override(tmp_path, monkeypatch):
    """Generated personas must be redirectable, or tests litter the repo."""
    monkeypatch.setenv("ICDEV_ACE_ROLES_DIR", str(tmp_path))
    from icdev.tools.ace import persona_generator as pgen

    assert pgen._roles_dir() == tmp_path
    assert pgen._soul_path("x") == tmp_path / "x" / "SOUL.md"


def test_mirror_dirs_do_not_invent_trees(tmp_path, monkeypatch):
    """With an override pointing outside the mirror, exactly one dir is used."""
    monkeypatch.setenv("ICDEV_ACE_ROLES_DIR", str(tmp_path))
    from icdev.tools.ace import persona_generator as pgen

    assert pgen._mirror_roles_dirs() == [tmp_path.resolve()]


# ---------------------------------------------------------------------------
# The classifier must never emit a role that does not exist
# ---------------------------------------------------------------------------


@pytest.fixture
def lens():
    from icdev.tools.ace.problem_classifier import ProblemClassifierLens

    return ProblemClassifierLens("build a compliance dashboard with audit logging")


def test_invented_roles_are_dropped_not_emitted(lens):
    """An LLM-invented role id must never reach the team assembler.

    team_assembler._build_specs swallows RoleNotFoundError and builds a spec
    with llm_function='' and no permissions; CoWorkerThread then fails it as
    role_not_found. An unmatched suggestion is strictly worse than none.
    """
    known = {r.role_id for r in lens._role_loader.list_roles()}
    slots = lens._resolve_suggested_roles(
        ["compliance_manager", "maritime_underwriter", "quantum_specialist"]
    )

    emitted = {s.role_id for s in slots}
    assert "compliance_manager" in emitted
    assert emitted <= known, f"ghost roles leaked: {emitted - known}"


def test_near_miss_role_names_are_mapped_to_real_roles(lens):
    """'security_analysis' should resolve to the shipped 'security_analyst'."""
    slots = lens._resolve_suggested_roles(["security_analysis"])
    assert [s.role_id for s in slots] == ["security_analyst"]


def test_all_bogus_suggestions_yield_empty_not_ghosts(lens):
    """Nothing resolvable means no slots — the caller then uses the fallback."""
    assert lens._resolve_suggested_roles(["zzz_nonsense_xyz"]) == []
    assert lens._resolve_suggested_roles([]) == []


def test_duplicate_suggestions_are_collapsed(lens):
    slots = lens._resolve_suggested_roles(
        ["ai_developer", "ai_developer", "compliance_manager"]
    )
    ids = [s.role_id for s in slots]
    assert len(ids) == len(set(ids))
    assert slots[0].priority == "high"


def test_role_catalog_is_read_as_ids_not_objects(lens):
    """RoleLoader.list_roles() returns RoleTemplate objects, not strings.

    Treating them as ids raised TypeError, which the surrounding except caught
    — silently disabling suggestions entirely rather than failing loudly.
    """
    roles = lens._role_loader.list_roles()
    assert roles and not isinstance(roles[0], str)
    assert all(isinstance(r.role_id, str) for r in roles)


# ---------------------------------------------------------------------------
# SIPA must fail closed
# ---------------------------------------------------------------------------


def test_sipa_unavailable_is_not_promotable():
    """An absent scanner must never read as a clean verdict."""
    from icdev.tools.ace import skill_promoter as sp

    assert sp._VERDICT_UNAVAILABLE not in sp._PROMOTABLE_VERDICTS


def test_sipa_import_failure_returns_unavailable(monkeypatch):
    """ImportError must yield 'unavailable', not the old ('clean', 0.0)."""
    import builtins

    from icdev.tools.ace import skill_promoter as sp

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "tools.integrity.engine":
            raise ImportError("simulated: SIPA not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    verdict, score = sp._run_sipa("role_id: x\n", "x")

    assert verdict == sp._VERDICT_UNAVAILABLE
    assert verdict not in sp._PROMOTABLE_VERDICTS
