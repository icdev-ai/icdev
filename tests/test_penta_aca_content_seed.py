# CUI // SP-CTI
"""penta-aca-07 — FORGE Academy content-seed coverage.

Two gaps not covered by test_penta_aca_missions1/2:

  1. seed_mission_catalog() is IDEMPOTENT — seeding twice creates no duplicate
     missions or steps.
  2. A step-file existence sweep over EVERY BUILTIN_STEPS entry (all batches, all
     tiers), so a missing content/starter/test file for ANY mission is caught,
     not just the newest batch.
"""

from __future__ import annotations

from apps.forge_academy import content_loader, db
from apps.forge_academy.content_loader import BUILTIN_MISSIONS, BUILTIN_STEPS, CONTENT_ROOT


def _mission_count():
    from tools.db.storage import get_connection
    return get_connection().execute(
        "SELECT COUNT(*) FROM fa_missions"
    ).fetchone()[0]


def _distinct_slug_count():
    from tools.db.storage import get_connection
    return get_connection().execute(
        "SELECT COUNT(DISTINCT slug) FROM fa_missions"
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# 1. Idempotent seeding
# ---------------------------------------------------------------------------

def test_builtin_mission_slugs_are_unique():
    # penta-fix-02: the pre-existing m-analyst-05-capstone duplicate was removed
    # and seed_mission_catalog now asserts uniqueness, so this is a hard invariant
    # (no known-dup carve-out / xfail) for every current and future batch.
    slugs = [m["slug"] for m in BUILTIN_MISSIONS]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    assert not dupes, f"duplicate mission slugs in BUILTIN_MISSIONS: {dupes}"


def test_seed_is_idempotent():
    db.migrate()
    content_loader.seed_mission_catalog()
    count1 = _mission_count()
    distinct1 = _distinct_slug_count()
    # Second seed must not duplicate anything (ON CONFLICT(slug) upsert).
    content_loader.seed_mission_catalog()
    count2 = _mission_count()
    assert count2 == count1, f"double-seed changed mission count {count1} -> {count2}"
    assert count2 == distinct1, "duplicate slug rows present after seeding"


def test_double_seed_does_not_duplicate_steps():
    """Re-seeding must not stack duplicate step rows for a mission (INSERT OR
    IGNORE + the 'seed only when step count is 0' guard keep it idempotent)."""
    from tools.db.storage import get_connection

    db.migrate()
    content_loader.seed_mission_catalog()
    conn = get_connection()

    def _counts():
        return {
            slug: conn.execute(
                "SELECT COUNT(*) FROM fa_mission_steps s "
                "JOIN fa_missions m ON m.id=s.mission_id WHERE m.slug=?", (slug,)
            ).fetchone()[0]
            for slug in BUILTIN_STEPS
        }

    before = _counts()
    content_loader.seed_mission_catalog()  # second seed
    after = _counts()
    assert before == after, (
        "re-seeding changed step counts (duplicates): "
        + ", ".join(f"{k}:{before[k]}->{after[k]}" for k in before if before[k] != after[k])
    )


# ---------------------------------------------------------------------------
# 2. Step-file existence sweep over ALL BUILTIN_STEPS (every batch)
# ---------------------------------------------------------------------------

def test_every_builtin_step_content_file_exists():
    missing = []
    for slug, steps in BUILTIN_STEPS.items():
        for step in steps:
            for key in ("content_path", "starter_code_path", "test_code_path"):
                rel = step.get(key)
                if not rel:
                    continue  # guided steps have no starter/test — only content_path
                if not (CONTENT_ROOT / rel).exists():
                    missing.append(f"{slug} step {step.get('step_num')} {key}: {rel}")
    assert not missing, "missing Academy content files:\n" + "\n".join(missing)


def test_every_builtin_step_has_content_path():
    """Every step must at least declare a content_path (the lesson markdown)."""
    for slug, steps in BUILTIN_STEPS.items():
        for step in steps:
            assert step.get("content_path"), f"{slug} step {step.get('step_num')} has no content_path"


def test_declared_starter_and_test_files_exist():
    """Where a coding step DECLARES a starter/test path, the file must exist on
    disk. (Not every coding step ships a starter — some are inline — so this only
    checks the paths that are declared, catching broken references.)"""
    for slug, steps in BUILTIN_STEPS.items():
        for step in steps:
            for key in ("starter_code_path", "test_code_path"):
                rel = step.get(key)
                if rel:
                    assert (CONTENT_ROOT / rel).exists(), f"{slug} {key} file missing: {rel}"
