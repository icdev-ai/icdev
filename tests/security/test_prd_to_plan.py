"""OPT-53 — tests for tools/planning/prd_to_plan.py."""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.planning import prd_to_plan as p2p  # noqa: E402


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
def test_validator_flags_file_names():
    md = """\
# Plan
### Phase 1
- Touch the src/router.py module.
"""
    r = p2p.validate_plan(md)
    assert not r.ok
    assert any(f.kind == "file_name" and f.match.endswith("router.py")
               for f in r.findings)


def test_validator_flags_function_calls():
    md = """\
# Plan
### Phase 1
- Call compute_score() on each row.
"""
    r = p2p.validate_plan(md)
    assert not r.ok
    assert any(f.kind == "function_call" and "compute_score" in f.match
               for f in r.findings)


def test_validator_allows_route_paths():
    md = """\
# Plan
### Phase 1
- Expose POST /api/intake/score with fields session_id, score.
"""
    r = p2p.validate_plan(md)
    assert r.ok, f"unexpected findings: {r.findings}"


def test_validator_allows_durable_anchors():
    """docs/, plans/, README, CLAUDE.md, AGENTS.md are allowlisted."""
    md = """\
# Plan
- See docs/prds/my_feature.md for context.
- Output lands in plans/feature.md.
"""
    r = p2p.validate_plan(md)
    assert r.ok


def test_validator_skips_code_fences():
    """Code fences may legitimately include file names."""
    md = """\
# Plan
### Phase 1
```
cat src/x.py
```
"""
    r = p2p.validate_plan(md)
    assert r.ok


def test_validator_ignores_natural_language_parens():
    """Plain prose like 'do()' shouldn't trigger the function-call rule."""
    md = """\
# Plan
- You can do() this incrementally.
"""
    r = p2p.validate_plan(md)
    assert r.ok


# ---------------------------------------------------------------------------
# PRD parsing
# ---------------------------------------------------------------------------
def test_extract_durable_decisions_bullet_list():
    prd = """\
# Feature X

## Durable Decisions
- Route: POST /api/foo
- Schema: foo_record
- UI: /foo page

## Goal
Do X.
"""
    items = p2p._extract_durable_decisions(prd)
    assert len(items) == 3
    assert "Route: POST /api/foo" in items


def test_extract_durable_decisions_none_returns_empty():
    assert p2p._extract_durable_decisions("# Nothing\n\ntext\n") == []


def test_title_from_prd_uses_first_h1(tmp_path):
    prd = tmp_path / "x.md"
    prd.write_text("# Intake Readiness Scoring\n\nbody\n", encoding="utf-8")
    assert p2p._title_from_prd(prd.read_text(), prd) == "Intake Readiness Scoring"


def test_title_from_prd_fallback_to_filename(tmp_path):
    prd = tmp_path / "my_feature.md"
    prd.write_text("no h1 here\n", encoding="utf-8")
    assert p2p._title_from_prd(prd.read_text(), prd) == "My Feature"


def test_slugify_basic():
    assert p2p._slugify("Intake Readiness Scoring") == "intake-readiness-scoring"
    assert p2p._slugify("Foo v2.0!") == "foo-v2-0"


# ---------------------------------------------------------------------------
# build_plan end-to-end (no-LLM path)
# ---------------------------------------------------------------------------
def test_build_plan_deterministic_skeleton(tmp_path):
    prd = tmp_path / "sample.md"
    prd.write_text("""\
# Feature X

## Durable Decisions
- Route: POST /api/x
- Schema: x_record with (id, name, created_at)

## Goal
Add X.
""", encoding="utf-8")
    rendered, meta = p2p.build_plan(prd, use_llm=False)
    assert meta["mode"] == "deterministic-skeleton"
    assert meta["phase_count"] == len(p2p.DEFAULT_PHASES)
    assert meta["decision_count"] == 2
    assert "# Feature X — Implementation Plan" in rendered
    # Decisions propagated in
    assert "POST /api/x" in rendered
    # Rendered plan passes the validator
    lint = p2p.validate_plan(rendered)
    assert lint.ok, f"skeleton fails its own validator: {lint.findings}"
