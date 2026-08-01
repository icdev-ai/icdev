# CUI // SP-CTI
"""penta-aca-04 — FORGE Academy new-missions batch 1 tests.

Covers the 5 new tier-2 missions added to content_loader.BUILTIN_MISSIONS /
BUILTIN_STEPS (Cortex, DIC citations, GraphRAG/KG, IQE, governed kanban pipeline):

  1. seed_mission_catalog() seeds all 5 slugs (they appear in the catalog).
  2. Every BUILTIN_STEPS entry for those missions has its content / starter / test
     files on disk under content/tier2/<slug>/steps/.
  3. One starter+test pair executes GREEN through the hardened code_runner sandbox
     (stdlib-only imports, no escape) when completed with a reference solution.
"""

from __future__ import annotations

from apps.forge_academy import content_loader, db
from apps.forge_academy.code_runner import run_code
from apps.forge_academy.content_loader import BUILTIN_MISSIONS, BUILTIN_STEPS, CONTENT_ROOT

NEW_SLUGS = [
    "m-cortex-01-unified-ai-layer",
    "m-dic-01-grounded-citations",
    "m-graphrag-01-kg-traversal",
    "m-iqe-01-collections-adapters",
    "m-kanban-01-governed-pipeline",
]


# ---------------------------------------------------------------------------
# 1. The 5 missions are registered and seed into the catalog
# ---------------------------------------------------------------------------

def test_new_missions_registered_in_builtins():
    slugs = {m["slug"] for m in BUILTIN_MISSIONS}
    for s in NEW_SLUGS:
        assert s in slugs, f"{s} missing from BUILTIN_MISSIONS"
        assert s in BUILTIN_STEPS, f"{s} missing from BUILTIN_STEPS"


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
# 3. A starter+test pair runs GREEN through the code_runner sandbox
# ---------------------------------------------------------------------------

# Reference solution for m-kanban-01-governed-pipeline (stdlib-only).
_KANBAN_REFERENCE = """
def can_transition(current, target):
    return target in LIFECYCLE.get(current, set())


def is_gate_task(task_id):
    return task_id.endswith("-gate-00")


def project_is_gated(tasks, project):
    return any(
        t["project"] == project and is_gate_task(t["id"]) and t["status"] != "done"
        for t in tasks
    )


def promote_backlog_to_scheduled(tasks):
    out = []
    for t in tasks:
        if (
            t["status"] == "backlog"
            and not is_gate_task(t["id"])
            and not project_is_gated(tasks, t["project"])
        ):
            out.append(t["id"])
    return out


def verify_done(task_id, merged_to_origin_main):
    if merged_to_origin_main:
        return {"id": task_id, "status": "done", "verified": True}
    return {"id": task_id, "status": "validating", "verified": False}
"""


def _load(slug: str, name: str) -> str:
    return (CONTENT_ROOT / "tier2" / slug / "steps" / name).read_text(encoding="utf-8")


def test_one_starter_runs_green_through_code_runner():
    slug = "m-kanban-01-governed-pipeline"
    starter = _load(slug, "step1_starter.py").split("# Demo", 1)[0]
    test_code = _load(slug, "step1_test.py")
    result = run_code(starter + "\n" + _KANBAN_REFERENCE, test_code=test_code)
    assert result["passed"] is True, (
        f"reference solution should pass the grader; "
        f"exit={result['exit_code']} stderr={result['stderr'][-800:]}"
    )


def test_all_starters_are_stdlib_safe_for_sandbox():
    """Each starter alone must clear the AST allowlist gate (no blocked imports)."""
    from apps.forge_academy.code_runner import _check_code_safety

    for slug in NEW_SLUGS:
        starter = _load(slug, "step1_starter.py")
        test_code = _load(slug, "step1_test.py")
        for label, code in (("starter", starter), ("test", test_code)):
            safe, reason = _check_code_safety(code)
            assert safe, f"{slug} {label} rejected by sandbox gate: {reason}"
