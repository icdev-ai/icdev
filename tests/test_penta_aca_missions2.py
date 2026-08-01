# CUI // SP-CTI
"""penta-aca-05 — FORGE Academy new-missions batch 2 tests.

Covers the 5 new tier-2 missions added to content_loader.BUILTIN_MISSIONS /
BUILTIN_STEPS (Foundry/ACF, Strategos, ZIG zero trust, TRUST grounding, design-
canvas trio):

  1. seed_mission_catalog() seeds all 5 slugs (they appear in the catalog).
  2. Every BUILTIN_STEPS entry for those missions has its content / starter / test
     files on disk under content/tier2/<slug>/steps/.
  3. All 5 starter+test pairs execute GREEN through the hardened code_runner sandbox
     (stdlib-only imports, no escape) when completed with a reference solution.
  4. Every starter and test clears the AST stdlib-safety gate alone.

Mirrors tests/test_penta_aca_missions1.py.
"""

from __future__ import annotations

import pytest

from apps.forge_academy import content_loader, db
from apps.forge_academy.code_runner import run_code
from apps.forge_academy.content_loader import BUILTIN_MISSIONS, BUILTIN_STEPS, CONTENT_ROOT

NEW_SLUGS = [
    "m-foundry-01-capability-pipeline",
    "m-strategos-01-signal-wargaming",
    "m-zig-01-zero-trust-maturity",
    "m-trust-01-citation-grounding",
    "m-canvas-trio-01-design-canvases",
]


# ---------------------------------------------------------------------------
# 1. The 5 missions are registered and seed into the catalog
# ---------------------------------------------------------------------------

def test_new_missions_registered_in_builtins():
    slugs = {m["slug"] for m in BUILTIN_MISSIONS}
    for s in NEW_SLUGS:
        assert s in slugs, f"{s} missing from BUILTIN_MISSIONS"
        assert s in BUILTIN_STEPS, f"{s} missing from BUILTIN_STEPS"


def test_new_missions_are_tier2_coding():
    by_slug = {m["slug"]: m for m in BUILTIN_MISSIONS}
    for s in NEW_SLUGS:
        m = by_slug[s]
        assert m["tier"] == 2, f"{s} should be tier 2"
        assert m["mission_type"] == "coding", f"{s} should be a coding mission"
        assert m["xp_reward"] >= 200, f"{s} xp_reward too low"


def test_seed_mission_catalog_seeds_all_five():
    from tools.db.storage import get_connection

    db.migrate()  # ensure fa_* tables exist in the (SQLite) test DB
    content_loader.seed_mission_catalog()

    conn = get_connection()
    placeholders = ",".join(["?"] * len(NEW_SLUGS))
    rows = conn.execute(
        f"SELECT slug FROM fa_missions WHERE slug IN ({placeholders})", NEW_SLUGS
    ).fetchall()
    seeded = {r[0] for r in rows}
    for s in NEW_SLUGS:
        assert s in seeded, f"{s} was not seeded into fa_missions"


# ---------------------------------------------------------------------------
# 2. Every step's content / starter / test files exist on disk
# ---------------------------------------------------------------------------

def test_step_files_exist_for_every_builtin_step():
    for slug in NEW_SLUGS:
        steps = BUILTIN_STEPS[slug]
        assert steps, f"{slug} has no steps"
        for step in steps:
            for key in ("content_path", "starter_code_path", "test_code_path"):
                rel = step.get(key)
                assert rel, f"{slug} step {step['step_num']} missing {key}"
                full = CONTENT_ROOT / rel
                assert full.exists(), f"missing file for {slug} {key}: {full}"


# ---------------------------------------------------------------------------
# 3. All 5 starter+test pairs run GREEN through the code_runner sandbox
# ---------------------------------------------------------------------------

# Reference solutions (stdlib-only). Each redefines the starter's TODO functions;
# appended AFTER the starter (minus its Demo block), so the completed definitions win.
_REFERENCES: dict[str, str] = {
    "m-foundry-01-capability-pipeline": '''
def novelty_score(capability, catalog):
    if not capability:
        return 0.0
    if not catalog:
        return 1.0
    cap = set(capability)
    best = 0.0
    for entry in catalog:
        e = set(entry)
        union = cap | e
        sim = (len(cap & e) / len(union)) if union else 0.0
        best = max(best, sim)
    return round(1 - best, 4)


def apply_novelty_gate(capability, catalog, min_novelty=0.35, duplicate_similarity=0.8):
    n = novelty_score(capability, catalog)
    max_sim = round(1 - n, 4)
    if max_sim >= duplicate_similarity:
        verdict = "duplicate"
    elif n < min_novelty:
        verdict = "low_novelty"
    else:
        verdict = "pass"
    return {"novelty": n, "max_similarity": max_sim, "verdict": verdict}


def cod_go_no_go(composite_score, min_composite=0.6):
    return "go" if composite_score >= min_composite else "no_go"


def seed_task_graph(slug, counts=None):
    counts = counts or {}
    out = []
    prev = None
    for epic in EPIC_ORDER:
        for k in range(1, counts.get(epic, 1) + 1):
            tid = f"{slug}-{epic}-{k:02d}"
            out.append({"id": tid, "epic": epic,
                        "integrity_gate": epic in BUILD_EPICS, "depends_on": prev})
            prev = tid
    return out
''',
    "m-strategos-01-signal-wargaming": '''
def score_signal(signal):
    domain_w = DOMAIN_WEIGHTS.get(signal["domain"], 0.5)
    rel = SOURCE_RELIABILITY.get(signal.get("source_grade", "F"), 0.0)
    decay = 0.5 ** (signal.get("age_days", 0) / HALF_LIFE_DAYS)
    return round(signal["raw_score"] * domain_w * rel * decay, 4)


def prioritize_signals(signals, top_n):
    if top_n <= 0:
        return []
    scored = [dict(s, score=score_signal(s)) for s in signals]
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:top_n]


def score_coa(coa):
    s = 0.4 * coa["feasibility"] + 0.4 * coa["impact"] - 0.2 * coa["risk"]
    return round(max(0.0, min(1.0, s)), 4)


def lanchester_square(a, A, b, D):
    ap = a * A ** 2
    dp = b * D ** 2
    winner = "attacker" if ap > dp else "defender" if dp > ap else "draw"
    return {"attacker_power": ap, "defender_power": dp, "winner": winner}
''',
    "m-zig-01-zero-trust-maturity": '''
def pillar_score(activities_done, activities_total, caps_impl, caps_total):
    ar = activities_done / activities_total if activities_total else 0.0
    cr = caps_impl / caps_total if caps_total else 0.0
    return round(0.6 * ar + 0.4 * cr, 4)


def maturity_level(score):
    for level, floor in MATURITY_BANDS:
        if score >= floor:
            return level
    return "preparation"


def aggregate_zig_score(pillar_scores):
    if not pillar_scores:
        return 0.0
    tw = sum(PILLAR_WEIGHTS.get(p, 0.0) for p in pillar_scores)
    if tw == 0:
        return 0.0
    ws = sum(PILLAR_WEIGHTS.get(p, 0.0) * s for p, s in pillar_scores.items())
    return round(ws / tw, 4)


def weakest_pillar(pillar_scores):
    if not pillar_scores:
        return None
    order = list(PILLAR_WEIGHTS.keys())
    return min(pillar_scores, key=lambda p: (pillar_scores[p], order.index(p)))
''',
    "m-trust-01-citation-grounding": '''
def attribution_score(chunk_text, output_text):
    ct = _tokens(chunk_text)
    if not ct:
        return 0.0
    ot = _tokens(output_text)
    return round(len(ct & ot) / len(ct), 4)


def classify_confidence(score):
    if score >= CONF_INCLUDE:
        return "include"
    if score < CONF_ABSTAIN:
        return "abstain"
    return "flag"


def build_provenance(source_id, sha256, attribution_score=0.0, classification="CUI"):
    return {"source_id": source_id, "sha256": sha256,
            "classification": classification, "attribution_score": attribution_score}


def egress_gate(sanitizer_available, fail_closed=True, force=False):
    if sanitizer_available:
        return {"allowed": True, "reason": "sanitized", "audited": False}
    if force:
        return {"allowed": True, "reason": "override_audited", "audited": True}
    if fail_closed:
        return {"allowed": False, "reason": "redaction_unavailable", "audited": False}
    return {"allowed": True, "reason": "fail_open", "audited": False}
''',
    "m-canvas-trio-01-design-canvases": '''
def match_signals(description):
    toks = _tokens(description)
    return {c: len(toks & sigs) for c, sigs in CANVAS_SIGNALS.items()}


def classify_design_need(description):
    scores = match_signals(description)
    if all(v == 0 for v in scores.values()):
        return None
    return max(CANVAS_ORDER, key=lambda c: scores[c])


def route_design_request(description):
    key = classify_design_need(description)
    if key is None:
        return {"canvas": None, "purpose": None, "route": None, "matched": False}
    return {"canvas": key, "purpose": CANVAS_PURPOSE[key],
            "route": CANVAS_ROUTE[key], "matched": True}
''',
}


def _load(slug: str, name: str) -> str:
    return (CONTENT_ROOT / "tier2" / slug / "steps" / name).read_text(encoding="utf-8")


def _starter_and_test(slug: str) -> tuple[str, str]:
    starter = _load(slug, "step1_starter.py").split("# Demo", 1)[0]
    test_code = _load(slug, "step1_test.py")
    return starter, test_code


@pytest.mark.parametrize("slug", NEW_SLUGS)
def test_starter_runs_green_through_code_runner(slug):
    starter, test_code = _starter_and_test(slug)
    ref = _REFERENCES[slug]
    result = run_code(starter + "\n" + ref, test_code=test_code)
    assert result["passed"] is True, (
        f"{slug}: reference solution should pass the grader; "
        f"exit={result['exit_code']} stderr={result['stderr'][-800:]}"
    )


def test_all_starters_are_stdlib_safe_for_sandbox():
    """Each starter and test alone must clear the AST allowlist gate (no blocked imports)."""
    from apps.forge_academy.code_runner import _check_code_safety

    for slug in NEW_SLUGS:
        starter = _load(slug, "step1_starter.py")
        test_code = _load(slug, "step1_test.py")
        for label, code in (("starter", starter), ("test", test_code)):
            safe, reason = _check_code_safety(code)
            assert safe, f"{slug} {label} rejected by sandbox gate: {reason}"


def test_stub_starters_fail_before_completion():
    """A starter's `pass` stubs must NOT already pass the grader (proves the exercise
    actually requires the learner to write code)."""
    for slug in NEW_SLUGS:
        starter, test_code = _starter_and_test(slug)
        result = run_code(starter, test_code=test_code)
        assert result["passed"] is False, (
            f"{slug}: unsolved starter unexpectedly passed its own grader"
        )
