#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed: nWave Methodology Adaptation — 3 epics, 25 tasks.

Epics (encoded in task ID prefix):
  nwa-ttd-*  — Testing Theater Detector
  nwa-rgp-*  — Rigor Gate Profiles
  nwa-des-*  — DES Execution Audit Trail

Run: python tools/db/seeds/seed_nwa_plan.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()


def _task(
    task_id: str,
    title: str,
    description: str,
    task_type: str,
    priority: str = "medium",
    depends_on: str | None = None,
) -> tuple:
    return (
        task_id,           # id
        title,             # title
        description,       # description
        task_type,         # task_type
        priority,          # priority
        "scheduled",       # status
        NOW,               # scheduled_at
        "claude_cli",      # executor_type
        "nwa_plan",        # dispatch_source
        depends_on,        # depends_on_task_id
        NOW,               # created_at
        NOW,               # updated_at
    )


INSERT_SQL = """
INSERT INTO kanban_tasks
    (id, title, description, task_type, priority, status,
     scheduled_at, executor_type, dispatch_source,
     depends_on_task_id, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO NOTHING
"""

TASKS: list[tuple] = [
    # ──────────────────────────────────────────────────────────────────────────
    # EPIC TTD — Testing Theater Detector
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "nwa-ttd-01",
        "Add TheaterDetectionResult dataclass to tools/testing/data_types.py",
        (
            "Append TheaterDetectionResult dataclass to tools/testing/data_types.py "
            "following the existing TestResult/CheckResult pattern. Fields: "
            "file_path: str, antipatterns_found: list[str], "
            "severity: str (none|warn|block), details: dict[str, Any], "
            "passed: bool, duration_ms: int. "
            "Use @dataclass with model_dump() compatibility. "
            "No other file changes in this task."
        ),
        task_type="build",
        priority="high",
    ),
    _task(
        "nwa-ttd-02",
        "Create tools/testing/theater_detector.py with 8 anti-pattern checks",
        (
            "New file tools/testing/theater_detector.py. "
            "TheaterDetector class with detect(test_dir: Path) -> TheaterDetectionResult. "
            "AST + regex checks (no LLM, air-gap safe): "
            "(1) tautological_assertion — assert x == x or assertTrue(True); "
            "(2) mock_dominated — >80% body is mock setup vs assertions; "
            "(3) fixture_theater — fixture delivers feature not precondition; "
            "(4) assertion_free — test function has no assert/assertEqual/etc.; "
            "(5) hardcoded_oracle — magic literals with no business comment; "
            "(6) smoke_masquerade — named 'unit_' but only calls top-level import; "
            "(7) always_green — try/except swallowing AssertionError; "
            "(8) spec_drift — Gherkin .feature step with no step definition. "
            "Severity: >=3 antipatterns in a file → block; 1-2 → warn. "
            "Imports: stdlib + tools.testing.data_types only. "
            "CLI: --scan <dir> --json. Only touch this new file."
        ),
        task_type="build",
        priority="high",
        depends_on="nwa-ttd-01",
    ),
    _task(
        "nwa-ttd-03",
        "Wire theater_detector into tools/testing/test_orchestrator.py",
        (
            "In test_orchestrator.py, after all test stages complete, "
            "call TheaterDetector().detect(project_dir / 'tests') and append result. "
            "Gate: severity=='block' sets overall run to failed. "
            "Wrap import in try/except ImportError for graceful degradation. "
            "Only touch test_orchestrator.py."
        ),
        task_type="build",
        priority="high",
        depends_on="nwa-ttd-02",
    ),
    _task(
        "nwa-ttd-04",
        "Add theater_detector gate block to args/security_gates.yaml",
        (
            "Add gate 'theater_detector' to args/security_gates.yaml following existing "
            "gate structure. blocking: [block_severity]. warning: [warn_severity]. "
            "thresholds: {max_blocking_files: 0, max_warn_files: 5, min_clean_pct: 80}. "
            "NIST controls: SA-11, SI-3. Only touch security_gates.yaml."
        ),
        task_type="build",
        priority="medium",
        depends_on="nwa-ttd-02",
    ),
    _task(
        "nwa-ttd-05",
        "Update .agents/skills/icdev-test/SKILL.md with theater detector step",
        (
            "Add 'Theater Detection' step after existing test run steps in SKILL.md. "
            "Document: command 'python -m tools.testing.theater_detector --scan tests/ --json', "
            "severity levels (none/warn/block), what each of 8 anti-patterns means, "
            "and that block-severity fails the gate. Only touch SKILL.md."
        ),
        task_type="chore",
        priority="low",
        depends_on="nwa-ttd-03",
    ),
    _task(
        "nwa-ttd-06",
        "Write pytest unit tests for theater_detector.py — 10 tests",
        (
            "Create tests/testing/test_theater_detector.py. "
            "One fixture per anti-pattern: minimal synthetic test file triggering exactly "
            "that pattern → assert it appears in antipatterns_found. "
            "One clean-file fixture → assert passed=True, antipatterns_found=[]. "
            "Tests must not hit filesystem except via tmp_path fixture. "
            "Coverage: 8 anti-pattern tests + 1 clean + 1 severity-aggregation = 10 tests."
        ),
        task_type="test",
        priority="high",
        depends_on="nwa-ttd-02",
    ),
    _task(
        "nwa-ttd-07",
        "Manifest + companion sync chore — TTD epic",
        (
            "1. Add theater_detector.py entry to tools/manifest/testing.md "
            "(create shard if missing): name, path, purpose, CLI flags. "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore",
        priority="medium",
        depends_on="nwa-ttd-05",
    ),
    _task(
        "nwa-ttd-08",
        "V&V gate — TTD epic sign-off",
        (
            "1. python tools/testing/test_orchestrator.py --project-dir . "
            "   (theater_detector runs, all 10 unit tests pass) "
            "2. python tools/testing/e2e_runner.py --run-all (no regressions) "
            "3. python tools/workflow/coherence_checker.py --all --gate (clean) "
            "4. python tools/dx/companion.py --sync --json (in sync) "
            "All four must pass before marking done."
        ),
        task_type="run",
        priority="critical",
        depends_on="nwa-ttd-07",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC RGP — Rigor Gate Profiles
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "nwa-rgp-01",
        "Create args/il_tier_profiles.yaml (IL2/IL4/IL5/IL6 rigor definitions)",
        (
            "New file args/il_tier_profiles.yaml. Four profiles: "
            "il2 (lean), il4 (standard), il5 (thorough), il6 (exhaustive). "
            "Each specifies: unit_coverage_pct_min, require_mutation_testing bool, "
            "require_property_based_testing bool, require_bdd bool, "
            "max_cyclomatic_complexity, max_cognitive_complexity, "
            "require_adr_on_arch_change bool, peer_review_required bool, "
            "theater_detector_max_warn_files int. "
            "Also: default_impact_level: il4. "
            "Only create this YAML — no Python changes."
        ),
        task_type="build",
        priority="high",
    ),
    _task(
        "nwa-rgp-02",
        "Create tools/quality/__init__.py and tools/quality/rigor_gates.py",
        (
            "Create tools/quality/__init__.py (empty). "
            "Create tools/quality/rigor_gates.py: "
            "ILTierProfile dataclass (all fields from il_tier_profiles.yaml); "
            "RigorProfileResult dataclass (profile_name, passed, violations, warnings, details); "
            "RigorGateEvaluator with load_profile(impact_level) -> ILTierProfile "
            "and evaluate(project_dir, impact_level) -> RigorProfileResult. "
            "Reads coverage from .coverage XML if present, degrades gracefully if absent. "
            "CLI: --evaluate --impact-level IL4 --project-dir . --json. "
            "No DB. No LLM. Air-gap safe. Only touch these two new files."
        ),
        task_type="build",
        priority="high",
        depends_on="nwa-rgp-01",
    ),
    _task(
        "nwa-rgp-03",
        "Add rigor_profile defaults to args/project_defaults.yaml",
        (
            "Under the existing tdd section in project_defaults.yaml, add: "
            "  rigor_profile:\n    impact_level: il4\n    enforce_gate: true\n"
            "    block_on_violation: false\n"
            "Provides the project-level default rigor_gates.py reads. "
            "Only touch project_defaults.yaml."
        ),
        task_type="build",
        priority="medium",
        depends_on="nwa-rgp-01",
    ),
    _task(
        "nwa-rgp-04",
        "Wire RigorGateEvaluator into .agents/skills/icdev-build/SKILL.md",
        (
            "Add 'Rigor Profile Check' step after test execution in icdev-build SKILL.md. "
            "Command: python -m tools.quality.rigor_gates --evaluate "
            "--impact-level {impact_level} --project-dir . --json. "
            "Explain IL tiers: il2=60% cov, il4=80%+BDD, il5=90%+mutation+ADR, "
            "il6=property-based+peer-review+all gates. Only touch SKILL.md."
        ),
        task_type="chore",
        priority="medium",
        depends_on="nwa-rgp-02",
    ),
    _task(
        "nwa-rgp-05",
        "Add rigor_profile gate block to args/security_gates.yaml",
        (
            "Add 'rigor_profile' gate to security_gates.yaml. "
            "Blocking: impact_level in [il5, il6] AND violation present. "
            "Warning: impact_level in [il2, il4] AND violation present. "
            "Thresholds: il2_coverage_min:60, il4:80, il5:90, il6:95. "
            "NIST controls: SA-11, SA-15, CM-6. Only touch security_gates.yaml."
        ),
        task_type="build",
        priority="medium",
        depends_on="nwa-rgp-02",
    ),
    _task(
        "nwa-rgp-06",
        "Write pytest unit tests for tools/quality/rigor_gates.py — 6 tests",
        (
            "Create tests/quality/test_rigor_gates.py. "
            "(1) load_profile('il4') returns correct ILTierProfile thresholds; "
            "(2) load_profile('invalid') raises ValueError; "
            "(3) evaluate() with mock coverage=85 against il4 → passed=True; "
            "(4) evaluate() with coverage=70 against il5 → passed=False, violation listed; "
            "(5) evaluate() degrades gracefully when .coverage absent; "
            "(6) CLI --json output is valid JSON with 'passed' key. "
            "Use tmp_path for synthetic coverage XML. 6 tests minimum."
        ),
        task_type="test",
        priority="high",
        depends_on="nwa-rgp-02",
    ),
    _task(
        "nwa-rgp-07",
        "Manifest + companion sync chore — RGP epic",
        (
            "1. Add rigor_gates.py entry to tools/manifest/quality.md "
            "(create shard if missing): name, path, purpose, CLI flags, IL-tier table. "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore",
        priority="medium",
        depends_on="nwa-rgp-04",
    ),
    _task(
        "nwa-rgp-08",
        "V&V gate — RGP epic sign-off",
        (
            "1. python -m tools.quality.rigor_gates --evaluate --impact-level il4 "
            "   --project-dir . --json  (passed=true or warn-only) "
            "2. pytest tests/quality/test_rigor_gates.py -v  (all 6 pass) "
            "3. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "4. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "5. python tools/dx/companion.py --sync --json  (in sync) "
            "All must pass before marking done."
        ),
        task_type="run",
        priority="critical",
        depends_on="nwa-rgp-07",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC DES — DES Execution Audit Trail
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "nwa-des-01",
        "Write migration 065_des_execution_events/up.py",
        (
            "Create tools/db/migrations/065_des_execution_events/up.py. "
            "Creates append-only table des_execution_events: "
            "id TEXT PK, task_id TEXT NOT NULL, "
            "event_type TEXT CHECK(event_type IN ('dispatch','completion','verification','gate_override')), "
            "skill_invoked TEXT, inputs_json TEXT, outputs_json TEXT, "
            "executor TEXT, dispatch_source TEXT, task_status TEXT, "
            "duration_ms INTEGER, verification_signals TEXT, "
            "queued_at TEXT, occurred_at TEXT NOT NULL, "
            "classification TEXT DEFAULT 'CUI // SP-CTI'. "
            "Indices: idx_des_task_id, idx_des_occurred_at. "
            "Guard: skip if kanban_tasks missing. Return {status, actions}. "
            "Follow migration 026 pattern. No down() needed."
        ),
        task_type="build",
        priority="critical",
    ),
    _task(
        "nwa-des-02",
        "Add des_execution_events to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py",
        (
            "In .claude/hooks/pre_tool_use.py find APPEND_ONLY_TABLES list and add "
            "'des_execution_events' to it. "
            "This blocks any UPDATE/DELETE on the audit table via the pre-tool hook. "
            "Only touch pre_tool_use.py."
        ),
        task_type="build",
        priority="critical",
        depends_on="nwa-des-01",
    ),
    _task(
        "nwa-des-03",
        "Add des_execution_events to MINIMAL_ICDEV_SCHEMA in tests/conftest.py",
        (
            "In tests/conftest.py find MINIMAL_ICDEV_SCHEMA and add des_execution_events "
            "CREATE TABLE IF NOT EXISTS statement (same DDL as migration 065, "
            "without REFERENCES constraint since minimal schema may lack kanban_tasks). "
            "Only touch conftest.py."
        ),
        task_type="build",
        priority="high",
        depends_on="nwa-des-01",
    ),
    _task(
        "nwa-des-04",
        "Create tools/kanban/des_audit_logger.py (DESAuditLogger runtime CRUD)",
        (
            "New file tools/kanban/des_audit_logger.py. "
            "DESAuditLogger class with four append-only methods (INSERT only): "
            "  log_dispatch(task_id, skill, inputs, executor, source, queued_at) -> str; "
            "  log_completion(task_id, status, outputs, duration_ms) -> str; "
            "  log_verification(task_id, signals: dict) -> str; "
            "  log_gate_override(task_id, reason, operator) -> str. "
            "Each generates UUID id, sets occurred_at=now(UTC). "
            "Use get_connection() — never sqlite3.connect(). "
            "Degrade gracefully if table missing (warn, return ''). "
            "CLI: --query --task-id <id> --json (last 20 events). "
            "Only touch this new file."
        ),
        task_type="build",
        priority="critical",
        depends_on="nwa-des-01",
    ),
    _task(
        "nwa-des-05",
        "Wire DESAuditLogger into tools/genesis/reflexes/kanban.py",
        (
            "In reflexes/kanban.py import DESAuditLogger. "
            "At task dispatch: logger.log_dispatch(task_id, skill='kanban_reflex', "
            "inputs={'task_title':..., 'prompt_file':...}, executor='kanban_scheduler', "
            "source='auto_promote', queued_at=task.scheduled_at). "
            "At task completion: logger.log_completion(task_id, status=new_status, "
            "outputs={'exit_code':..., 'duration_ms':...}, duration_ms=elapsed). "
            "Wrap both in try/except — DES failure must never block task execution. "
            "Only touch reflexes/kanban.py."
        ),
        task_type="build",
        priority="critical",
        depends_on="nwa-des-04",
    ),
    _task(
        "nwa-des-06",
        "Wire DESAuditLogger into tools/dashboard/api/kanban.py",
        (
            "In api/kanban.py import DESAuditLogger. "
            "After _verification_passed() returns: logger.log_verification(task_id, "
            "signals={'codelens':..., 'coherence':..., 'e2e':..., 'passed':bool}). "
            "After _log_verification_bypass(): logger.log_gate_override(task_id, "
            "reason=bypass_reason, operator='operator'). "
            "Wrap in try/except. Only touch api/kanban.py."
        ),
        task_type="build",
        priority="high",
        depends_on="nwa-des-04",
    ),
    _task(
        "nwa-des-07",
        "Write pytest unit tests for tools/kanban/des_audit_logger.py — 7 tests",
        (
            "Create tests/kanban/test_des_audit_logger.py. "
            "Use conftest.py MINIMAL_ICDEV_SCHEMA (includes des_execution_events). "
            "(1) log_dispatch() inserts row with event_type='dispatch'; "
            "(2) log_completion() inserts event_type='completion'; "
            "(3) log_verification() inserts event_type='verification'; "
            "(4) log_gate_override() inserts event_type='gate_override'; "
            "(5) no UPDATE/DELETE issued (patch conn.execute, verify only INSERTs); "
            "(6) degrades gracefully when table missing — no exception, returns ''; "
            "(7) CLI --query --task-id returns valid JSON list. "
            "7 tests."
        ),
        task_type="test",
        priority="high",
        depends_on="nwa-des-04",
    ),
    _task(
        "nwa-des-08",
        "Run migration 065 + manifest + companion sync chore — DES epic",
        (
            "1. python tools/db/migrate.py --up "
            "   (verify des_execution_events table created) "
            "2. Add des_audit_logger.py entry to tools/manifest/kanban.md "
            "   (create shard if missing): name, path, purpose, event types, CLI. "
            "3. python tools/dx/companion.py --sync --write --json "
            "4. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore",
        priority="high",
        depends_on="nwa-des-05",
    ),
    _task(
        "nwa-des-09",
        "V&V gate — DES epic sign-off",
        (
            "1. python tools/db/migrate.py --status  (migration 065 = applied) "
            "2. pytest tests/kanban/test_des_audit_logger.py -v  (all 7 pass) "
            "3. Trigger a real kanban dispatch (promote any backlog task to in_progress) "
            "   and verify des_execution_events row via: "
            "   python -m tools.kanban.des_audit_logger --query --task-id <id> --json "
            "4. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "5. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "All must pass before marking done."
        ),
        task_type="run",
        priority="critical",
        depends_on="nwa-des-08",
    ),
]


def main() -> None:
    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    for task in TASKS:
        cur.execute(INSERT_SQL, task)
        # PostgreSQL ON CONFLICT DO NOTHING: rowcount=-1 means no insert (conflict)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    ttd = sum(1 for t in TASKS if t[0].startswith("nwa-ttd"))
    rgp = sum(1 for t in TASKS if t[0].startswith("nwa-rgp"))
    des = sum(1 for t in TASKS if t[0].startswith("nwa-des"))

    print(f"[seed_nwa_plan] done — {inserted} inserted, {skipped} skipped (conflict)")
    print(f"  Epic ttd (Theater Detector):    nwa-ttd-01..08  ({ttd} tasks)")
    print(f"  Epic rgp (Rigor Gate Profiles): nwa-rgp-01..08  ({rgp} tasks)")
    print(f"  Epic des (DES Audit Trail):     nwa-des-01..09  ({des} tasks)")
    print()
    print("View at: http://localhost:5050/kanban")


if __name__ == "__main__":
    main()
