# [TEMPLATE: CUI // SP-CTI]
"""exa-refine-02 — call-site prompt bodies read from the registry.

exa-refine-01 gave the registry a read path for SUPPLEMENTAL layers; this is the
write path that gives ``prompt_versions`` a reason to hold a row at all, plus the
conversion of three f-string call sites to registry reads.

The load-bearing assertion is byte-identity. A refactor that moves a prompt into
a database is only safe if the text that reaches the model is unchanged, and
"unchanged" is not something you can establish by reading a diff of a rewrapped
string. So each ``_ORIGINAL_*`` below is a VERBATIM copy of the f-string as it
stood before exa-refine-02 — an independent second copy, kept here on purpose —
and the test asserts the builder reproduces it exactly, in two states:

  * empty registry — the fallback path, i.e. every installation that never seeds;
  * seeded registry — after ``--seed-call-sites``, i.e. every installation that does.

Then the inverse, because byte-identity on its own is also what an INERT read
path looks like: registering a different template must actually change the
rendered prompt.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from tools.llm.prompt_registry import (
        CALL_SITE_PREFIX,
        CALL_SITE_PROMPTS,
        activate_prompt,
        get_active_layers,
        import_from_hardprompts,
        is_base_prompt_name,
        is_call_site_name,
        is_layer_name,
        list_prompts,
        register_prompt,
        render_prompt,
        seed_call_site_prompts,
    )
    from tools.nova.skill_generator import (
        SPEC_PROMPT_NAME,
        _build_spec_prompt,
    )
    from tools.skills.gepa_optimizer import (
        PATCH_PROMPT_NAME,
        _build_patch_prompt,
    )
    from tools.workflow.reflexion_agent import (
        IMPROVEMENT_PROMPT_NAME,
        _build_improvement_prompt,
    )

    _IMPORT_OK = True
except ImportError:  # pragma: no cover - dependency-gated
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="tools.llm.prompt_registry not available")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_db(tmp_path, monkeypatch):
    """Point get_connection() at a fresh temp SQLite DB with an empty registry."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "prompt_registry.db"))
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)

    from tools.llm import prompt_registry

    prompt_registry._invalidate_layer_cache()
    yield tmp_path
    prompt_registry._invalidate_layer_cache()


# ---------------------------------------------------------------------------
# Fixed inputs + verbatim pre-refactor prompts
#
# The inputs deliberately include braces and a value needing a format spec:
# `.format()` must not re-scan substituted values, and ":.2f"/":.1%"/"!r" have to
# be applied in Python because a stored template cannot carry them.
# ---------------------------------------------------------------------------

_GEPA_ARGS = dict(
    current_content="---\nname: icdev-build\n---\n\n## Steps\n1. Use {placeholder} syntax\n",
    improvement_text="Name the failing gate in step 2.",
    skill_name="icdev-build",
    composite_score=0.7351,
    baseline_score=0.6,
    n_traces=12,
)

_ORIGINAL_GEPA_PROMPT = (
    f"You are a skill optimizer for the ICDEV AI platform.\n\n"
    f"SKILL FILE: {_GEPA_ARGS['skill_name']}\n\n"
    f"CURRENT SKILL CONTENT:\n{_GEPA_ARGS['current_content']}\n\n"
    f"IMPROVEMENT SUGGESTION (from {_GEPA_ARGS['n_traces']} execution traces, "
    f"composite_score={_GEPA_ARGS['composite_score']:.2f} vs baseline "
    f"{_GEPA_ARGS['baseline_score']:.2f}):\n"
    f"{_GEPA_ARGS['improvement_text']}\n\n"
    f"Generate the updated skill file content. Rules:\n"
    f"- Keep ALL YAML frontmatter unchanged (everything between --- markers)\n"
    f"- Make targeted improvements to steps/instructions based on the suggestion\n"
    f"- Do not remove existing steps unless they are clearly incorrect\n"
    f"- Do not add padding or unnecessary content\n"
    f"- Keep total length within 20% of the original\n\n"
    f"Return ONLY the updated skill file content. No explanation, no markdown fences."
)

_REFLEXION_ARGS = dict(
    task_type="build",
    skill_used="icdev-build",
    trace_count=17,
    summary="- task_id=t1 outcome=failed pattern={unbalanced notes=boom",
    failure_count=5,
    failure_summary="- task_id=t1 outcome=failed pattern=missing_gate notes=boom",
    baseline_score=0.7058823529411765,
)

_ORIGINAL_REFLEXION_PROMPT = (
    f"You are an AI improvement agent for the ICDEV™ platform.\n\n"
    f"Task type: {_REFLEXION_ARGS['task_type']!r}\n"
    f"Skill invoked: {_REFLEXION_ARGS['skill_used']!r}\n\n"
    f"Recent execution traces (last {_REFLEXION_ARGS['trace_count']} dispatches):\n"
    f"{_REFLEXION_ARGS['summary']}\n\n"
    f"Failure traces ({_REFLEXION_ARGS['failure_count']} total):\n"
    f"{_REFLEXION_ARGS['failure_summary']}\n\n"
    f"Current success rate: {_REFLEXION_ARGS['baseline_score']:.1%}\n\n"
    "Analyze the failure patterns. Propose 2–4 CONCRETE, ACTIONABLE improvements "
    "to the skill instructions or task handling that would increase success rate. "
    "Focus on WHY tasks fail (root cause) and HOW to prevent it. "
    "Be specific: name files, steps, or instructions to change. "
    "Do NOT invent new features — improve the existing skill.\n\n"
    "Output format:\n"
    "## Root Cause\n<1-2 sentences>\n\n"
    "## Proposed Improvements\n<numbered list>\n\n"
    "## Expected Impact\n<1 sentence>"
)

_NOVA_ARGS = dict(
    pattern="python tools/db/migrate.py --create {name}",
    skill_name="icdev-db-migrate",
)

_ORIGINAL_NOVA_PROMPT = (
    f"Generate a concise ICDEV™ skill specification in markdown for automating:\n\n"
    f"Pattern: {_NOVA_ARGS['pattern']}\n\n"
    f"Format:\n"
    f"# {_NOVA_ARGS['skill_name']}\n"
    f"## When to use\n[1-2 sentences]\n\n"
    f"## Steps\n1. ...\n\n"
    f"## Acceptance criteria\n- [ ] ...\n"
)


def _render_gepa() -> str:
    return _build_patch_prompt(**_GEPA_ARGS)


def _render_reflexion() -> str:
    return _build_improvement_prompt(**_REFLEXION_ARGS)


def _render_nova() -> str:
    return _build_spec_prompt(**_NOVA_ARGS)


_CALL_SITES = (
    ("gepa", PATCH_PROMPT_NAME, _render_gepa, _ORIGINAL_GEPA_PROMPT),
    ("reflexion", IMPROVEMENT_PROMPT_NAME, _render_reflexion, _ORIGINAL_REFLEXION_PROMPT),
    ("nova", SPEC_PROMPT_NAME, _render_nova, _ORIGINAL_NOVA_PROMPT),
)


# ---------------------------------------------------------------------------
# Byte-identity — the whole point of the refactor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,name,render,original", _CALL_SITES, ids=[c[0] for c in _CALL_SITES])
def test_unseeded_registry_renders_the_original_prompt_byte_for_byte(
    registry_db, label, name, render, original
):
    """Empty registry → the call site's own template → the pre-refactor bytes."""
    rendered = render()
    assert rendered == original
    assert len(rendered.encode("utf-8")) == len(original.encode("utf-8"))


@pytest.mark.parametrize("label,name,render,original", _CALL_SITES, ids=[c[0] for c in _CALL_SITES])
def test_seeded_registry_renders_the_original_prompt_byte_for_byte(
    registry_db, label, name, render, original
):
    """After --seed-call-sites the prompt is read from the DB — and is unchanged.

    This is the assertion that makes seeding safe to run against a live install:
    turning the read path on must not alter a single byte of what is sent.
    """
    seeded = seed_call_site_prompts()
    assert seeded["status"] == "ok", seeded
    assert name in {entry["name"] for entry in seeded["imported"]}

    assert render() == original


def test_no_prompt_wording_changed_across_all_three_call_sites(registry_db):
    """One assertion covering the acceptance criterion in a single place."""
    assert _render_gepa() == _ORIGINAL_GEPA_PROMPT
    assert _render_reflexion() == _ORIGINAL_REFLEXION_PROMPT
    assert _render_nova() == _ORIGINAL_NOVA_PROMPT


# ---------------------------------------------------------------------------
# ...and the inverse: the read is LIVE, not decorative
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,name,render,original", _CALL_SITES, ids=[c[0] for c in _CALL_SITES])
def test_registering_a_new_version_changes_the_rendered_prompt(
    registry_db, label, name, render, original
):
    """Byte-identity alone is also what an inert read path looks like."""
    result = register_prompt(name, "REPLACED TEMPLATE for exa-refine-02", "t")
    activate_prompt(name, result["version"])

    assert render() == "REPLACED TEMPLATE for exa-refine-02"
    assert render() != original


def test_rollback_restores_the_previous_call_site_prompt(registry_db):
    """register → activate → rollback, observed through the call site itself."""
    from tools.llm.prompt_registry import rollback_prompt

    seed_call_site_prompts()
    assert _render_nova() == _ORIGINAL_NOVA_PROMPT

    v2 = register_prompt(SPEC_PROMPT_NAME, "v2 spec prompt for {pattern}", "t")
    activate_prompt(SPEC_PROMPT_NAME, v2["version"])
    assert _render_nova() == f"v2 spec prompt for {_NOVA_ARGS['pattern']}"

    rollback_prompt(SPEC_PROMPT_NAME, v2["version"] - 1)
    assert _render_nova() == _ORIGINAL_NOVA_PROMPT


def test_draft_version_is_not_applied(registry_db):
    """A registered-but-unactivated version must not reach the model."""
    register_prompt(SPEC_PROMPT_NAME, "draft only", "t")
    assert _render_nova() == _ORIGINAL_NOVA_PROMPT


# ---------------------------------------------------------------------------
# Failure modes — a bad registry row must not take down the call site
# ---------------------------------------------------------------------------


def test_unrenderable_registered_template_falls_back_to_the_call_site_default(registry_db):
    """A placeholder the call site does not supply is a bad ROW, not a bad caller."""
    result = register_prompt(SPEC_PROMPT_NAME, "spec for {no_such_variable}", "t")
    activate_prompt(SPEC_PROMPT_NAME, result["version"])

    assert _render_nova() == _ORIGINAL_NOVA_PROMPT


def test_registered_template_with_a_stray_brace_falls_back(registry_db):
    result = register_prompt(SPEC_PROMPT_NAME, "spec for { pattern", "t")
    activate_prompt(SPEC_PROMPT_NAME, result["version"])

    assert _render_nova() == _ORIGINAL_NOVA_PROMPT


def test_render_prompt_lets_a_broken_default_raise(registry_db):
    """A broken DEFAULT is a programming error and must be loud, not swallowed."""
    with pytest.raises(KeyError):
        render_prompt("call_site/does-not-exist", "hello {missing}")


def test_render_prompt_does_not_rescan_substituted_values(registry_db):
    """A value containing braces is data, not template — .format must not recurse."""
    out = render_prompt("call_site/does-not-exist", "value={v}", v="{other}")
    assert out == "value={other}"


def test_unreachable_registry_still_renders(tmp_path, monkeypatch):
    """A DB that has never initialised the registry reads as 'use the default'."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "no-such-dir" / "nope.db"))
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)

    from tools.llm import prompt_registry

    prompt_registry._invalidate_layer_cache()
    try:
        assert _render_nova() == _ORIGINAL_NOVA_PROMPT
    finally:
        prompt_registry._invalidate_layer_cache()


# ---------------------------------------------------------------------------
# Namespace invariants — seeding call sites must not create router layers
# ---------------------------------------------------------------------------


def test_call_site_names_are_neither_base_nor_layer_names():
    for name, _module, _attr, _fn in CALL_SITE_PROMPTS:
        assert name.startswith(CALL_SITE_PREFIX)
        assert is_call_site_name(name)
        assert not is_base_prompt_name(name)
        assert not is_layer_name(name)


def test_seeding_call_sites_registers_no_router_layers(registry_db):
    """The router appends `layer/` rows to every call; call-site bodies are not that."""
    seed_call_site_prompts()

    for _name, _module, _attr, function_name in CALL_SITE_PROMPTS:
        assert get_active_layers(function_name, use_cache=False) == []
    assert get_active_layers("*", use_cache=False) == []


def test_seed_call_site_prompts_is_idempotent(registry_db):
    """Re-running against an unchanged tree writes no row (hash dedupe)."""
    first = seed_call_site_prompts()
    assert first["total_imported"] == len(CALL_SITE_PROMPTS)
    assert first["total_errors"] == 0

    second = seed_call_site_prompts()
    assert second["total_imported"] == 0
    assert second["total_skipped"] == len(CALL_SITE_PROMPTS)
    assert second["total_errors"] == 0

    rows = [r for r in list_prompts() if r["prompt_name"].startswith(CALL_SITE_PREFIX)]
    assert len(rows) == len(CALL_SITE_PROMPTS)


def test_every_declared_call_site_template_is_importable(registry_db):
    """A typo in CALL_SITE_PROMPTS must fail here, not silently seed two of three."""
    result = seed_call_site_prompts()
    assert result["errors"] == []
    assert result["total_imported"] == len(CALL_SITE_PROMPTS)


# ---------------------------------------------------------------------------
# Seeding from hardprompts/ — prompt_versions is non-empty, --list returns rows
# ---------------------------------------------------------------------------


def test_import_from_hardprompts_makes_prompt_versions_non_empty(registry_db):
    assert list_prompts() == []

    result = import_from_hardprompts()
    assert result["status"] == "ok", result
    assert result["total_imported"] > 0

    rows = list_prompts()
    assert rows, "prompt_versions is still empty after --import-hardprompts"
    assert all(r["status"] in ("active", "deprecated", "draft") for r in rows)
    # Every imported prompt is activated, so the registry gate passes.
    assert any(r["status"] == "active" for r in rows)


def test_hardprompts_import_creates_no_base_or_layer_rows(registry_db):
    """hardprompts/ files are documents, not router layers and not base prompts."""
    import_from_hardprompts()

    for row in list_prompts():
        assert not is_base_prompt_name(row["prompt_name"])
        assert not is_layer_name(row["prompt_name"])


def test_cli_seed_then_list_returns_results(tmp_path):
    """End-to-end through the documented CLI, which is what an operator runs."""
    env = dict(os.environ)
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env["ICDEV_DB_PATH"] = str(tmp_path / "cli.db")
    env["PYTHONPATH"] = str(REPO_ROOT)
    for leaked in ("ICDEV_DATABASE_URL", "ICDEV_PG_DATABASE", "ICDEV_PG_HOST"):
        env.pop(leaked, None)

    script = str(REPO_ROOT / "tools" / "llm" / "prompt_registry.py")

    for flag in ("--import-hardprompts", "--seed-call-sites"):
        proc = subprocess.run(
            [sys.executable, script, flag, "--json"],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=300,
        )
        assert proc.returncode == 0, f"{flag} failed: {proc.stderr[-2000:]}"

    proc = subprocess.run(
        [sys.executable, script, "--list", "--json"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    listed = json.loads(proc.stdout)
    assert isinstance(listed, list) and listed, "--list returned no results after seeding"

    names = {row["prompt_name"] for row in listed}
    for name, _module, _attr, _fn in CALL_SITE_PROMPTS:
        assert name in names

    proc = subprocess.run(
        [sys.executable, script, "--gate", "--json"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=300,
    )
    assert proc.returncode == 0, f"gate failed after seeding: {proc.stdout[-2000:]}"
