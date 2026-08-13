#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the AHX / ARR / CLX project cards onto the kanban board.

These three cards carry the *adopted* third of the five agent-harness audit
documents in ``C:\\AI\\searches``.  The rejected two thirds are recorded in
``docs/spikes/ahx-00-agent-audit-docs-disposition.md`` and deliberately have no
board surface.

Ordering matters: each card's ``*-gate-00`` task is inserted **first** and held
``in_progress`` so ``promote_backlog_to_scheduled`` never dispatches the tasks
behind it.  AHX is built by a human-driven worktree session; ARR and CLX stay
gated until AHX lands, because neither can be evaluated without a working
measurement loop.

Idempotent — ``create_tasks`` skips ids that already exist, so re-running is
safe.

Usage::

    python tools/kanban/seed_ahx_arr_clx.py --json
    python tools/kanban/seed_ahx_arr_clx.py --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import sys

GATES: list[dict] = [
    {
        "id": "ahx-gate-00",
        "title": "AHX gate — held (human-driven worktree build)",
        "description": (
            "RISK: AHX is built by a human in a driven worktree session; dispatching it "
            "unattended edits the harness's own evaluation loop with nobody watching.\n"
            "MANUAL GATE. Held in_progress so promote_backlog_to_scheduled never dispatches "
            "the ahx- tasks behind it. AHX is implemented by a human-driven Claude Code session "
            "in an isolated worktree, not by the kanban runner. Close this gate only when every "
            "other ahx- task is done and the PR has merged to origin/main."
        ),
        "priority": "high",
    },
    {
        "id": "arr-gate-00",
        "title": "ARR gate — held pending AHX",
        "description": (
            "RISK: until AHX closes the harness_eval outcome loop there is no signal to judge "
            "ARR by, so an unattended build produces agent-loop changes nobody can evaluate.\n"
            "MANUAL GATE. Held in_progress. ARR adds an error taxonomy and structured tool results "
            "to the agent loop; its effect on loop quality is unmeasurable until AHX closes the "
            "harness_eval outcome loop. Do not open this gate until ahx-eval-02 is done and "
            "harness_eval shows a falling NULL-outcome share. When opening, first read "
            "tools/agent_runtime/ end to end and ADR D384-D390 — the sag card already built much "
            "of this surface, and D384's governing rule is 'compose existing primitives, don't rebuild'."
        ),
        "priority": "high",
    },
    {
        "id": "clx-gate-00",
        "title": "CLX gate — held pending AHX",
        "description": (
            "RISK: a control loop built before AHX supplies a sensor and an outcome signal is "
            "tuned against noise, and an unattended session here rebuilds review_loop.py and "
            "re-proposes the LangGraph orchestration ADR D391 rejected.\n"
            "MANUAL GATE. Held in_progress. A control loop cannot be tuned without a working sensor "
            "and a working outcome signal; AHX supplies both. Do not open until AHX has merged. "
            "When opening, read tools/quality/review_loop.py first — it is already the deterministic "
            "sensor (ruff + coherence_checker + SIPA over a diff, emitting a fix brief). Wrap it; do "
            "not build a second scanner. Do not re-propose LangGraph/LangChain orchestration — "
            "formally rejected in ADR D391."
        ),
        "priority": "high",
    },
]

AHX_TASKS: list[dict] = [
    {
        "id": "ahx-eval-01",
        "title": "record_outcome: fail loudly instead of silently no-opping a 0-row UPDATE",
        "description": (
            "tools/genesis/harness/eval_harness.py record_outcome() issues:\n"
            "    UPDATE harness_eval SET actual_outcome=%s, resolved_at=%s\n"
            "     WHERE task_id=%s AND actual_outcome IS NULL\n"
            "It never inspects cursor.rowcount. When no decision row exists for task_id (or the row "
            "already has an outcome), the UPDATE touches zero rows and the function returns None — "
            "indistinguishable from success. Every caller therefore believes the outcome was recorded.\n\n"
            "Fix: capture rowcount, log a warning naming the task_id and reflex when it is 0, and "
            "return a structured status the callers can act on (the function currently returns None). "
            "Keep it non-raising — callers wrap it in try/except and must not start failing.\n\n"
            "MIRROR PARITY: edit BOTH tools/genesis/harness/eval_harness.py and "
            "icdev/tools/genesis/harness/eval_harness.py, or reconcile with mirror_parity.py --fix. "
            "Editing one alone trips test_mirror_drift_baseline on every branch.\n\n"
            "Test: assert a warning is emitted and a non-success status returned when record_outcome "
            "is called for a task_id with no decision row."
        ),
        "priority": "high",
    },
    {
        "id": "ahx-eval-02",
        "title": "Close the harness_eval outcome loop (123 of 129 rows have NULL actual_outcome)",
        "description": (
            "Live PG state at time of carding: harness_eval holds 129 rows — codegen 65 (60 unresolved), "
            "sampled:oracle_triage 63 (63 unresolved), oracle_triage 1 (0 unresolved). compute_metrics() "
            "counts only actual_outcome in (resolved, false_positive, self_resolved, failed), so "
            "precision, recall, ECE and false-heal-rate are all derived from ~6 rows. The adaptive "
            "Z-score gate thresholds built on top of those metrics are therefore statistically void.\n\n"
            "Note the audit doc that prompted this card claims the table is starved of DECISIONS "
            "('13 rows, all oracle_triage, zero codegen'). That is stale — decisions are being written "
            "fine. The defect is that OUTCOMES are not written back.\n\n"
            "Two paths to investigate:\n"
            "  - codegen: tools/genesis/reflexes/kanban.py:~7613 records the decision when the "
            "claude-cli subprocess finishes; the outcome is written at ~3016 on kanban status "
            "transition to done/token_exhausted/failed. Determine why 60 of 65 never pair up — likely "
            "candidates are task_id mismatch between the two call sites, or terminal statuses that "
            "bypass the transition guard.\n"
            "  - sampled:oracle_triage: these are pending-by-design human-label rows created by "
            "tools/genesis/reflexes/confidence_sampler.py. They only resolve when a human runs "
            "`--verdict <task_id> --outcome correct|incorrect`. Nobody ever does, because the pending "
            "queue is not surfaced anywhere. Either surface it or stop counting these rows against "
            "the resolvable population.\n\n"
            "ALSO: confidence_sampler writes actual_outcome values 'correct'/'incorrect', but "
            "compute_metrics only recognises resolved/false_positive/self_resolved/failed. Confirm "
            "whether this vocabulary mismatch means sampled rows could never contribute to a metric "
            "even once labelled, and reconcile the vocabulary if so.\n\n"
            "Depends on ahx-eval-01 — the rowcount signal is how you will find the mismatches."
        ),
        "priority": "high",
    },
    {
        "id": "ahx-eval-03",
        "title": "Add the missing numbered migration for harness_eval",
        "description": (
            "harness_eval is live and read/written, but its schema exists only in "
            "tools/db/schema/pg_consolidated.sql (~line 16411, plus a PK constraint ~41364) and in "
            "tests/conftest.py MINIMAL_ICDEV_SCHEMA (~line 284). There is no file under "
            "tools/db/migrations/ that creates it — a fresh migrate run would not produce the table "
            "except via the consolidated bootstrap.\n\n"
            "Add a numbered migration matching the consolidated schema exactly (idempotent, "
            "CREATE TABLE IF NOT EXISTS). Do not alter the shape — this is a consistency fix, not a "
            "schema change. Verify against pg_consolidated.sql column for column before writing.\n\n"
            "Note migrate --target N applies ALL pending migrations, not just N — use "
            "MigrationRunner.apply_migration for a single one when testing."
        ),
        "priority": "medium",
    },
    {
        "id": "ahx-doc-01",
        "title": "context/capabilities/harness.yaml advertises three tools that do not exist",
        "description": (
            "context/capabilities/harness.yaml lines 13, 26-27 and 40-41 advertise five CLI "
            "invocations:\n"
            "    python tools/harness/maturity_assessor.py --project-dir . --detailed --json\n"
            "    python tools/harness/trace_analyzer.py --last-n 5 --json\n"
            "    python tools/harness/trace_analyzer.py --recommendations --limit 20 --json\n"
            "    python tools/harness/exit_criteria_evaluator.py --list --json\n"
            "    python tools/harness/exit_criteria_evaluator.py --workflow build --json\n"
            "A repo-wide grep for maturity_assessor / trace_analyzer / exit_criteria_evaluator returns "
            "ZERO hits — no implementation, no other reference. tools/harness/ contains only "
            "cli_generator.py and mcp_wrapper_generator.py.\n\n"
            "This violates the CLAUDE.md guardrail: 'Never document a command whose file does not "
            "exist' — an agent reading the capability catalogue will run these and burn a cycle "
            "deciding whether the tree is broken or the doc is.\n\n"
            "Decision required (record it in the task before acting): remove the claims, or implement "
            "the tools. Default to REMOVAL — ADR D-HARNESS-1..8 describes these as read-only/advisory "
            "scanner-tier helpers, and nothing in the repo consumes them. Removing a false claim is "
            "strictly better than shipping three thin tools nobody calls.\n\n"
            "Then reconcile args/doc_command_gate.yaml and confirm "
            "coherence_checker.py::check_doc_command_paths passes."
        ),
        "priority": "medium",
    },
    {
        "id": "ahx-path-01",
        "title": "De-hardcode the per-machine Claude auto-memory path",
        "description": (
            "Three sites hardcode a machine- and repo-slug-specific path:\n"
            "  tools/memory/wiki_tool_query.py:~81\n"
            "  tools/ace/controller.py:~261 and ~318\n"
            "all building  $USERPROFILE/.claude/projects/C--AI-ICDev/memory .\n\n"
            "The slug 'C--AI-ICDev' is Claude Code's auto-memory directory naming convention for one "
            "specific checkout path. On a differently-named checkout, a non-Windows host, or a case-"
            "sensitive filesystem this silently resolves to nothing and ACE's cross-session memory "
            "breaks with no error.\n\n"
            "The correct pattern already exists in the repo: tools/memory/memory_write.py:~225-233 "
            "update_crossrefs() derives the equivalent slug dynamically from BASE_DIR. Extract that "
            "derivation into a shared helper and call it from all three sites. Allow an explicit env "
            "override for operators who relocate the directory.\n\n"
            "Test on a path that does NOT match the hardcoded slug — that is the whole point."
        ),
        "priority": "medium",
    },
    {
        "id": "ahx-heal-01",
        "title": "Reconcile three divergent self-heal rate limits",
        "description": (
            "Conceptually the same guardrail is implemented three times with three different numbers:\n"
            "  tools/knowledge/self_heal_analyzer.py:24-25 — CONFIDENCE_THRESHOLD 0.7, "
            "ESCALATION_THRESHOLD 0.3, MAX_HEAL_ATTEMPTS 3 per pattern per hour\n"
            "  tools/mcp/knowledge_server.py:~266-304 — the fallback path used when trigger_self_heal "
            "fails to import: same 0.7 floor but 5/hour\n"
            "  args/heal_constitution.yaml — min_confidence_floor 0.70, max_false_heal_rate 0.30, "
            "circuit_breaker_daily_cap max_per_day 3\n\n"
            "CLAUDE.md states self-healing is limited to confidence >= 0.7 and max 5/hour, which "
            "matches only the fallback path. Pick one source of truth (heal_constitution.yaml is the "
            "natural home — it is already the MAC-style rule file read by heal.py), have the other two "
            "read from it, and correct CLAUDE.md to match whatever is chosen.\n\n"
            "Do not change the effective safety posture without saying so explicitly in the PR — "
            "tightening or loosening an auto-heal rate limit is a security-relevant change."
        ),
        "priority": "medium",
    },
    {
        "id": "ahx-vv-01",
        "title": "V&V — prove the outcome loop closes on a real run",
        "description": (
            "Acceptance evidence for the whole card. Not satisfied by unit tests alone.\n\n"
            "1. pytest tests/ -v --tb=short from the repo root with an absolute PYTHONPATH.\n"
            "2. python tools/workflow/coherence_checker.py --all --gate — must pass "
            "check_doc_command_paths after ahx-doc-01.\n"
            "3. Baseline then re-measure the live table:\n"
            "     SELECT reflex, COUNT(*), SUM(CASE WHEN actual_outcome IS NULL THEN 1 ELSE 0 END)\n"
            "       FROM harness_eval GROUP BY reflex;\n"
            "   Baseline at carding time: 129 rows, 123 unresolved. Show the unresolved share falling "
            "after a real agent/kanban run. A passing test suite with an unchanged NULL share means "
            "the card did NOT achieve its purpose.\n"
            "4. python tools/dx/companion.py --sync --write --json (foreground only — never background it).\n"
            "5. PR to main; wait for Lint / Test / Security Scan / Helm Lint. No auto-merge."
        ),
        "priority": "high",
    },
]

ARR_TASKS: list[dict] = [
    {
        "id": "arr-tax-01",
        "title": "Error taxonomy: exception type to remediation disposition",
        "description": (
            "icdev/tools/llm/agent_loop.py:~981-988 (and the mirrored sequential path at ~1014-1021) "
            "collapses every tool exception to f'{type(exc).__name__}: {exc}' and hands it back to the "
            "LLM as tool-result text. ModuleNotFoundError, ConnectionRefusedError, PermissionError and "
            "TimeoutError are indistinguishable to the loop.\n\n"
            "Add a taxonomy mapping exception type to a disposition: retry_safe, degrade, escalate, "
            "terminal. Classification only — no actuation in this task.\n\n"
            "CORRECTION to the source audit: the loop is NOT unguarded. It already has a "
            "consecutive-all-error circuit breaker, a duplicate-call guard, a stall detector, per-tool "
            "timeouts and hard token/cost caps (~1023-1189). Do not rebuild any of those."
        ),
        "priority": "high",
    },
    {
        "id": "arr-res-01",
        "title": "Structured tool result carrying error_type and remediation_hint",
        "description": (
            "Tool handlers currently return flat strings, and tool_call_log stores "
            "{turn, name, input, result, error} with result as a string. Introduce a structured result "
            "so the loop can react programmatically rather than making the LLM parse prose.\n\n"
            "Must be backward compatible — every existing handler returns a string today and they "
            "cannot all be migrated at once. Accept both shapes.\n\n"
            "Depends on arr-tax-01."
        ),
        "priority": "high",
    },
    {
        "id": "arr-res-02",
        "title": "Inline single retry for genuinely safe remediations only",
        "description": (
            "For dispositions classified retry_safe by arr-tax-01 (stale cache, transient reconnect, "
            "uninitialised table), run the remediation and retry the original call ONCE, then fall "
            "through to the existing error path.\n\n"
            "Single retry, not a loop — the existing circuit breaker and stall detector must remain "
            "the outer bound. Record each attempt so ahx's harness_eval can measure whether "
            "remediation actually works.\n\n"
            "Depends on arr-res-01."
        ),
        "priority": "high",
    },
    {
        "id": "arr-deg-01",
        "title": "Declare-and-degrade for missing capabilities (NOT install)",
        "description": (
            "When a tool fails because a capability is genuinely absent, emit a structured, actionable "
            "result naming the missing capability and route it to the vendored-wheel + HITL path. "
            "NEVER mutate the runtime environment.\n\n"
            "This deliberately replaces the source audit's install_dependency proposal. Rationale, "
            "recorded so it is not relitigated: ICDEV targets air-gapped IL4-IL6; args/security_gates.yaml "
            "enforces sbom_max_age_days 30, min_slsa_level 2 and attestation presence; "
            "tools/airgap/wheel_vendor.py exists so packages arrive as vetted vendored wheels. A runtime "
            "pip install invalidates the SBOM and defeats the air-gap story.\n\n"
            "The canonical pattern to copy is tools/airgap/pdf_fallback.py, which handles a missing PDF "
            "library via a declared local fallback chain (pypdf, then LLaVA via Ollama) instead of "
            "installing anything. Full disposition: docs/spikes/ahx-00-agent-audit-docs-disposition.md."
        ),
        "priority": "medium",
    },
    {
        "id": "arr-esc-01",
        "title": "Escalation path producing a kanban card with full failure context",
        "description": (
            "When classification says escalate, or a retry_safe remediation fails, create a kanban card "
            "carrying the trace_id, tool name, input, exception type and attempted remediation.\n\n"
            "Reuse tools/kanban/task_factory.create_tasks — never raw INSERT. Check "
            "tools/workflow/self_debug.py first: it already creates diagnostic cards after 3 recurrences "
            "and quarantines the task. Extend it rather than adding a second card-creating path.\n\n"
            "Depends on arr-tax-01."
        ),
        "priority": "medium",
    },
]

CLX_TASKS: list[dict] = [
    {
        "id": "clx-fb-01",
        "title": "Versioned feedback file loaded into loop context every run",
        "description": (
            "Adapted from Kyle (Human Layer): a feedback file tracked in version control, loaded into "
            "the agent's context on every run and updated when a human corrects the loop's output. "
            "Turns a stateless prompt-repeater into something that accumulates corrections.\n\n"
            "Verified absent: no .icdev/ directory, and a repo-wide grep for feedback.md, "
            "golden_pattern and golden_dataset returns nothing.\n\n"
            "Wire the update side into the existing HITL gate rather than inventing a new review "
            "surface. Keep the file small enough to sit in a system prompt without crowding out task "
            "context."
        ),
        "priority": "high",
    },
    {
        "id": "clx-flow-01",
        "title": "Backpressure — do not start new autonomous work while prior output is unreviewed",
        "description": (
            "The highest-value idea in the control-loop doc, and it maps directly onto known ICDEV pain: "
            "kanban churn, stacked PRs and stranded branches.\n\n"
            "Before an autonomous loop creates new work, check whether its previous output is still "
            "unreviewed (open PR carrying the loop's label, or a kanban task not yet done). If so, skip "
            "the run. Target: exactly one open output per loop at a time.\n\n"
            "Read tools/databridge/scale/backpressure.py first — a BackpressureController already exists "
            "for the ETL consumer and the shape may be reusable. Also check the existing kanban pipeline "
            "hardening work (3 coherence gates + stranded-branch auditor) before adding a fourth gate."
        ),
        "priority": "high",
    },
    {
        "id": "clx-sense-01",
        "title": "Deterministic sensor interface wrapping review_loop.py",
        "description": (
            "Expose structured violations (file, line, rule_id, severity) to a controller so it can pick "
            "what to fix next from measured deviation rather than from conversation.\n\n"
            "CORRECTION to the source doc, which asks for a new 'code_sensor' tool: the deterministic "
            "sensor already exists as tools/quality/review_loop.py — it runs ruff, "
            "tools/workflow/coherence_checker.py and SIPA (tools/integrity/pr_gates.assess_changed_files) "
            "over a diff and emits a fix brief, explicitly designed as deterministic execution with LLM "
            "orchestration outside. Wrap it behind the sensor interface. Do NOT build a second scanner, "
            "and do NOT add ast-grep as a dependency before proving review_loop's output is insufficient."
        ),
        "priority": "medium",
    },
    {
        "id": "clx-gold-01",
        "title": "Golden-pattern directory of human-written reference implementations",
        "description": (
            "Hand-written before/after reference implementations for high-frequency ICDEV tasks (RLS "
            "bypass fix, canvas get_canvas_connection migration, PG-native JSON rewrite), loaded into "
            "the loop's context so output matches house style instead of generic LLM idiom.\n\n"
            "Human-authored by design — this is the counterweight to NOVA's auto-generated prompt "
            "templates, not a replacement for them.\n\n"
            "Caution from prior experience: a golden set with no headroom launders bad results. Choose "
            "patterns where current output is measurably wrong, not ones already handled correctly."
        ),
        "priority": "medium",
    },
]


def _gate_for(task_id: str) -> str:
    """The gate sentinel a task must wait behind — ``arr-res-01`` -> ``arr-gate-00``.

    Holding the sentinel ``in_progress`` is NOT on its own enough to stop the
    runner. The dispatcher's eligibility predicate (see
    ``tools/genesis/reflexes/kanban.py``: ``depends_on_task_id IS NULL OR the
    dependency is done``) only holds a task back when something actually points
    at the gate. The first cut of this script seeded the sentinels and the work
    but wired no dependency, so every gate was decorative and the runner
    dispatched nine tasks that a human was already implementing by hand.
    """
    return task_id.split("-", 1)[0] + "-gate-00"


def _specs() -> tuple[list[dict], list[dict]]:
    gates = [
        {**g, "task_type": "chore", "status": "in_progress"}
        for g in GATES
    ]
    work = [
        {**t, "task_type": "build", "status": "backlog",
         "depends_on_task_id": _gate_for(t["id"])}
        for t in (AHX_TASKS + ARR_TASKS + CLX_TASKS)
    ]
    return gates, work


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed AHX/ARR/CLX kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be seeded; write nothing")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args(argv)

    gates, work = _specs()

    if args.dry_run:
        out = {
            "dry_run": True,
            "gates": [g["id"] for g in gates],
            "tasks": [t["id"] for t in work],
            "total": len(gates) + len(work),
        }
        print(json.dumps(out, indent=2) if args.as_json else out)
        return 0

    from tools.kanban.task_factory import create_tasks

    # Gates first, held in_progress, so the runner can never see an unguarded backlog.
    created_gates = create_tasks(gates)
    created_work = create_tasks(work)

    out = {
        "created_gates": created_gates,
        "created_tasks": created_work,
        "skipped_existing": (
            len(gates) + len(work) - len(created_gates) - len(created_work)
        ),
    }
    print(json.dumps(out, indent=2) if args.as_json else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
