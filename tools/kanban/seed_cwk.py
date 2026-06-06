# CUI // SP-CTI — Seed Co-Workers (cwk-*) + re-queue genuine ACF/ACE gaps
"""Decompose the "AI Co-Workers across every domain" initiative onto the Task Board.

Plan: ~/.claude/plans/i-want-to-shift-federated-bunny.md

This seeder does TWO things, both grounded in a true-state reconciliation that
compared the board against origin/irad/feature (the board lied: many acf-* tasks
were marked 'done' but their artifacts exist on NO branch):

1. RE-QUEUE GENUINE GAPS (done -> scheduled), but ONLY for tasks whose claimed
   artifact is genuinely absent on the current branch. Tasks whose artifacts
   already live on origin (blueprint.py, novelty_gate.py, foundry IQE adapter,
   foundry index.html, manifest shard) are LEFT done — local was merely 32
   commits behind, not missing the work. harvester.py + novelty_gate.py were
   committed in the Phase-0 reconcile (97ecfb253) so their tasks stay done too.

2. SEED the net-new cwk-* chain: the unified /coworkers canvas + CoWorkerLoader
   + generic CoWorkerRAG + chat->ACE execute bridge + 5 reference co-workers +
   Strategos-as-bespoke, then the Phase-1 ACF extensions that let the foundry
   AUTONOMOUSLY build the remaining ~50 domain co-workers (gap collector,
   coworker novelty catalog, coworker spec/task-graph contract, HITL merge gate).

A co-worker = persistent domain expert (Strategos-style chat persona + RAG) that
can ALSO spin up ACE multi-role teams (ACEController.launch) to do real work.
Each co-worker is ONE args/coworkers/<domain>.yaml row, never a new canvas, so
adding one (by hand or autonomously) never touches a merge-conflict hot file.

The cwk chain is a single-parent linear chain rooted on acf-vv-04 (the ACF
end-to-end verification) so the foundry is genuinely operational before the
autonomous-fill machinery is extended onto it (plan critical path P0->P1->P3->P4).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()
CLASSIF = "CUI // SP-CTI"

# --------------------------------------------------------------------------- #
# 1. Genuine gaps to re-queue (done -> scheduled). Verified MISSING on origin
#    AND local. Each maps to the artifact the dispatcher must rebuild.
# --------------------------------------------------------------------------- #
REQUEUE = {
    # ACF core pipeline — exists on no branch despite "done"
    "acf-db-04": "args/foundry_config.yaml",
    "acf-synth-01": "tools/foundry/synthesizer.py",
    "acf-synth-03": "tools/foundry/scorer.py",
    "acf-deliberate-01": "tools/foundry/deliberator.py",
    "acf-design-01": "tools/foundry/spec_generator.py",
    "acf-design-02": "tools/foundry/task_graph.py",
    "acf-design-03": "tools/foundry/seeder.py",
    "acf-engine-01": "tools/foundry/engine.py",
    "acf-engine-02": "tools/foundry/engine.py",
    "acf-engine-03": "tools/foundry/engine.py",
    "acf-learn-01": "tools/foundry/learner.py",
    "acf-reflex-01": "tools/genesis/reflexes/foundry_cycle.py",
    "acf-doc-01": "goals/autonomous_capability_foundry.md",
    # ACE canvas wiring — genuinely missing everywhere
    "ace-canvas-01": "tools/dashboard/templates/coworker/index.html",
    "ace-canvas-04": "icdev/tools/ace/blueprint.py",
    "ace-runtime-05": "tests/test_ace_runtime.py",
}


def _artifact_present(conn, rel_path):
    """True if the file exists in the working tree (re-verify before re-queueing)."""
    return (_BASE / rel_path).exists()


# --------------------------------------------------------------------------- #
# 2. Net-new cwk-* tasks (single linear chain, root gated on acf-vv-04).
# --------------------------------------------------------------------------- #
CWK_TASKS = [
    # ── Epic foundation: registry + loader (co-worker = one YAML) ───────────
    {
        "id": "cwk-loader-01",
        "title": "CWK: CoWorkerLoader + registry schema (clone ACE role_loader)",
        "description": (
            "Create icdev/tools/coworkers/registry.py by cloning the pattern in "
            "icdev/tools/ace/role_loader.py (dir glob, dataclass with required-field "
            "validation, 60s hot-reload). Loads args/coworkers/<domain>.yaml. Schema fields: "
            "coworker_id, display_name, domain, status (active|draft|autobuilt_pending_review), "
            "trust_tier (reuse ACE _TIER_ORDER red<orange<yellow<green), persona_prompt, "
            "rag{tables[], manifest_shards[], goals[], mode: generic|bespoke}, "
            "ace_role_set[{role_id,count,priority}], tool_permissions[], "
            "execute_policy{auto_launch_confidence, require_hitl}, mcp_expose. "
            "Create empty args/coworkers/ dir with a README. Public API: load_all(), "
            "get(coworker_id), reload_if_stale(). Unit test the validator (reject missing "
            "required fields). DO NOT add a canvas yet."
        ),
        "task_type": "build", "priority": "critical", "depends_on_task_id": "acf-vv-04",
    },
    {
        "id": "cwk-db-01",
        "title": "CWK: cwk_coworkers + cwk_sessions tables (canvas conn, append-only)",
        "description": (
            "icdev/tools/coworkers/db/init_db.py. Two tables via get_canvas_connection("
            "'ICDEV_COWORKERS_DB_URL') (canvas tables have NO classification/tenant_id — using "
            "get_connection() would inject RLS and raise UndefinedColumn): "
            "cwk_coworkers (mirror of the YAML registry for query/IQE) and cwk_sessions "
            "(append-only: id, coworker_id, chat_context_id, ace_instance_id, created_at — links a "
            "chat turn to a launched ACE team). PG-first dual schema. Register in "
            "tools/db/init_icdev_db.py, add a migration, add both tables to conftest.py "
            "MINIMAL_ICDEV_SCHEMA, add cwk_sessions to APPEND_ONLY_TABLES in "
            ".claude/hooks/pre_tool_use.py, add ICDEV_CWK_ENABLED + ICDEV_COWORKERS_DB_URL to .env."
        ),
        "task_type": "build", "priority": "critical", "depends_on_task_id": "cwk-loader-01",
    },
    {
        "id": "cwk-rag-01",
        "title": "CWK: generalize StrategosRAG -> config-driven CoWorkerRAG",
        "description": (
            "Generalize tools/strategos/strategos_chat.py::StrategosRAG into "
            "icdev/tools/coworkers/rag.py CoWorkerRAG(tables, manifest_shards, goals) — "
            "deterministic, citation-bearing retrieval over the configured domain tables + "
            "manifest shards + goal docs, NO new LLM dependency. Do NOT rewrite Strategos; "
            "CoWorkerRAG must support mode='bespoke' that delegates to an existing surface "
            "(Strategos) so the gold-standard chat is wrapped, not replaced. Tests assert "
            "retrieval returns cited rows for a known table."
        ),
        "task_type": "build", "priority": "high", "depends_on_task_id": "cwk-db-01",
    },
    {
        "id": "cwk-ctx-01",
        "title": "CWK: context_factory — persona+RAG chat context from a co-worker YAML",
        "description": (
            "icdev/tools/coworkers/context_factory.py. Build a chat context for a co-worker via "
            "tools/dashboard/chat_manager.py create_context(system_prompt=persona_prompt) — exactly "
            "how tools/strategos/strategos_chat.py::create_strategos_context works. Inject the "
            "CoWorkerRAG retriever for the co-worker's domain. Stamp the context with coworker_id so "
            "the execute trigger (cwk-trigger-01) can detect it. Test: context carries the right "
            "persona + coworker_id."
        ),
        "task_type": "build", "priority": "high", "depends_on_task_id": "cwk-rag-01",
    },
    {
        "id": "cwk-exec-01",
        "title": "CWK: additive manifest override on ACEController.launch + cwk_sessions link",
        "description": (
            "icdev/tools/ace/controller.py: add optional param manifest: TeamManifest|None=None to "
            "launch(); when provided, _run() SKIPS ProblemClassifierLens and uses the supplied "
            "manifest (the co-worker's curated ace_role_set wins over the classifier guess). Default "
            "None preserves ALL 29 existing ACE tests (assert they still pass). Add a helper to build "
            "a TeamManifest from a co-worker's ace_role_set. On launch from a co-worker, insert a "
            "cwk_sessions row (coworker_id, chat_context_id, ace_instance_id). Use trigger_source="
            "'coworker', trigger_ref=coworker_id."
        ),
        "task_type": "build", "priority": "high", "depends_on_task_id": "cwk-ctx-01",
    },
    {
        "id": "cwk-trigger-01",
        "title": "CWK: chat->execute bridge builtin (RAG-answer vs launch ACE team + HITL)",
        "description": (
            "tools/extensions/builtins/100_coworker_execute_trigger.py exporting EXTENSION_HOOKS on "
            "chat_message_after, firing ONLY when the active context carries coworker_id. Per turn: "
            "run icdev/tools/ace/problem_classifier.py ProblemClassifierLens(message) -> capture the "
            "confidence-ranked route AND the fallback TeamManifest. Below execute_policy."
            "auto_launch_confidence -> answer via CoWorkerRAG (no team). At/above -> if require_hitl, "
            "post a confirm card and launch ACEController.launch(manifest=coworker.ace_role_set) on "
            "click; else auto-launch. Stream the controller's existing ace_progress SSE back to chat. "
            "Tests: low-confidence message answers via RAG; high-confidence offers a team."
        ),
        "task_type": "build", "priority": "high", "depends_on_task_id": "cwk-exec-01",
    },
    {
        "id": "cwk-canvas-01",
        "title": "CWK: unified /coworkers canvas (8-component completeness gate, ONCE)",
        "description": (
            "ONE hub canvas, key 'cwk'. Ship ALL 8 completeness components together: "
            "(1) tools/dashboard/templates/coworkers/index.html (lists co-workers from the registry; "
            "selecting one opens the shared chat with that persona/RAG), (2) icdev/ mirror via "
            "FOREGROUND companion sync, (3) @bp.route in icdev/tools/coworkers/blueprint.py "
            "(create_coworkers_blueprint factory: index + per-coworker chat + JSON status), "
            "(4) backing module already built (registry/context_factory/rag), (5) any constants, "
            "(6) DB migration already added, (7) nav link in base.html + start.md Pages line, "
            "(8) IQE handled in cwk-iqe-01. Register ('cwk', 'ICDEV_CWK_ENABLED', "
            "'icdev.tools.coworkers.blueprint', 'coworkers_bp') in tools/dashboard/app.py _CANVAS_DEFS "
            "(+ icdev mirror app.py). Verify with Playwright (screenshot to "
            "playwright/screenshots/coworkers.png)."
        ),
        "task_type": "build", "priority": "high", "depends_on_task_id": "cwk-trigger-01",
    },
    {
        "id": "cwk-iqe-01",
        "title": "CWK: IQE integration for the /coworkers canvas",
        "description": (
            "tools/iqe/adapters/cwk.py registering cwk.coworkers + cwk.sessions collections; add "
            "cwk to _CANVAS_MAP in app.py iqe_dispatch(); add PATH_CANVAS entry in base.html mini-bar; "
            "{% include 'includes/iqe_query_widget.html' %} in the coworkers template (+ icdev mirror); "
            ">=3 seed queries in context/iqe/queries/cwk/ (e.g. 'co-workers by domain', 'active ACE "
            "sessions per co-worker', 'autobuilt co-workers pending review'). Add POST /api/iqe-query "
            "wiring per the blueprint pattern."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-canvas-01",
    },
    {
        "id": "cwk-roles-01",
        "title": "CWK: shared ACE role library for domain co-workers",
        "description": (
            "Author reusable archetype roles in args/ace/roles/ (mirror the schema of "
            "args/ace/roles/ai_developer.yaml: role_id, display_name, trust_tier, steps[], "
            "communication{a2a topics}, llm_function, tool_permissions[], genesis_reflex): "
            "security_analyst, data_analyst, compliance_manager, requirements_engineer, "
            "devops_engineer. Each defines a real step workflow for its domain. These compose into "
            "co-worker ace_role_set teams and are the few-shot basis for autonomously-authored roles. "
            "Verify role_loader.py hot-loads them."
        ),
        "task_type": "build", "priority": "high", "depends_on_task_id": "cwk-iqe-01",
    },
    # ── Epic reference co-workers (the few-shot exemplars for autonomous fill) ─
    {
        "id": "cwk-ref-01",
        "title": "CWK: reference co-worker — Security (Sentinel)",
        "description": (
            "args/coworkers/security.yaml: persona = security analyst (BLUF), rag.tables=[sdc_findings, "
            "sdc_controls], manifest_shards=[security.md], goals=[secure, comply], "
            "ace_role_set=[security_analyst:1, ai_developer:1, qa_manager:1], trust_tier=yellow, "
            "execute_policy{auto_launch_confidence:0.6, require_hitl:true}. VERIFY end-to-end: open "
            "/coworkers, pick Security, ask a RAG question (cited answer), then a remediation/build "
            "request -> HITL confirm -> ACE team launches -> ace_progress SSE -> cwk_sessions row. This "
            "is the canonical exemplar; get it genuinely working before the others."
        ),
        "task_type": "build", "priority": "high", "depends_on_task_id": "cwk-roles-01",
    },
    {
        "id": "cwk-ref-02",
        "title": "CWK: reference co-worker — Data (ddc)",
        "description": (
            "args/coworkers/data.yaml: data analyst persona over the Data Canvas domain "
            "(rag.tables from the ddc/data_canvas schema, manifest_shards data-mesh/ddc, goals as "
            "appropriate), ace_role_set=[data_analyst:1, ai_developer:1, qa_manager:1]. Verify "
            "RAG-answer + ACE-launch via the /coworkers canvas."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-ref-01",
    },
    {
        "id": "cwk-ref-03",
        "title": "CWK: reference co-worker — Compliance",
        "description": (
            "args/coworkers/compliance.yaml: compliance manager persona over the compliance domain "
            "(NIST 800-53 / ATO artifacts; rag manifest_shards compliance-engine, goals comply), "
            "ace_role_set=[compliance_manager:1, ai_developer:1, qa_manager:1]. Verify via /coworkers."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-ref-02",
    },
    {
        "id": "cwk-ref-04",
        "title": "CWK: reference co-worker — Requirements (RICOAS)",
        "description": (
            "args/coworkers/requirements.yaml: requirements engineer persona over RICOAS intake "
            "(rag manifest_shards requirements, goals requirements_intake), "
            "ace_role_set=[requirements_engineer:1, qa_manager:1]. Verify via /coworkers."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-ref-03",
    },
    {
        "id": "cwk-ref-05",
        "title": "CWK: reference co-worker — Observability (odc)",
        "description": (
            "args/coworkers/observability.yaml: SRE/observability persona over the Observability "
            "Canvas + monitoring domain (rag manifest_shards monitoring/observability, goals "
            "monitoring), ace_role_set=[devops_engineer:1, ai_developer:1]. Verify via /coworkers."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-ref-04",
    },
    {
        "id": "cwk-ref-06",
        "title": "CWK: register Strategos as the bespoke co-worker exemplar",
        "description": (
            "args/coworkers/strategos.yaml with rag.mode=bespoke that DELEGATES to the existing "
            "tools/strategos/strategos_chat.py (STRATEGOS_SYSTEM_PROMPT + StrategosRAG) rather than "
            "the generic CoWorkerRAG. Proves the abstraction wraps the gold-standard surface without "
            "a rewrite. Verify Strategos still answers identically through the /coworkers entry."
        ),
        "task_type": "chore", "priority": "medium", "depends_on_task_id": "cwk-ref-05",
    },
    # ── Epic autobuild: extend ACF so it can build the remaining ~50 co-workers ─
    {
        "id": "cwk-auto-01",
        "title": "CWK: foundry gap collector — emit 'coworker:<domain>' signals",
        "description": (
            "REQUIRES ACF complete (engine/synthesizer/scorer/etc. — gated upstream via acf-vv-04). "
            "Add _collect_coworker_gaps() to tools/foundry/harvester.py telemetry collector: "
            "target_domains = _CANVAS_DEFS keys + the platform/cross-cutting list (Strategos, GovCon/"
            "CPMP, FathomDesk, Pulse, Marketplace, Requirements, Simulation, Innovation); "
            "subtract domains that already have args/coworkers/<domain>.yaml with status in "
            "(active, autobuilt_pending_review). Emit one foundry_signals row per remainder "
            "(theme='coworker:<domain>', source_engine='telemetry'). Test the set-difference logic."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-ref-06",
    },
    {
        "id": "cwk-auto-02",
        "title": "CWK: novelty_gate coworker catalog (forbid duplicate co-workers)",
        "description": (
            "Extend tools/foundry/novelty_gate.py build_catalog() to ALSO ingest args/coworkers/*.yaml "
            "(display_name + domain + persona summary) as catalog entries, so a candidate concept that "
            "duplicates an existing co-worker scores is_duplicate and is rejected. This is the "
            "structural guarantee against rebuilding a co-worker that already exists (layer 2; layer 1 "
            "is the pre-harvest subtraction in cwk-auto-01). Test: a concept identical to security.yaml "
            "is rejected as duplicate."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-auto-01",
    },
    {
        "id": "cwk-auto-03",
        "title": "CWK: 'coworker' spec + lean task-graph contract",
        "description": (
            "tools/foundry/spec_generator.py: when a concept theme is 'coworker:<domain>', emit a "
            "canvas_contract of type 'coworker'. tools/foundry/task_graph.py: for a 'coworker' "
            "contract, collapse the db/dash/blueprint epics (the /coworkers canvas already exists) and "
            "produce a LEAN ~6-task chain per domain: cwk-<d>-yaml (registry YAML) -> cwk-<d>-roles "
            "(net-new args/ace/roles/*.yaml) -> cwk-<d>-rag (resolve/seed tables + queries) -> "
            "cwk-<d>-iqe (register in the shared cwk IQE collection, NO new adapter) -> cwk-<d>-doc -> "
            "cwk-<d>-vv (Playwright: select, RAG-answer, dry-run ACE launch, assert SSE). Test the "
            "emitted graph shape for one synthetic domain."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-auto-02",
    },
    {
        "id": "cwk-auto-04",
        "title": "CWK: HITL merge gate — CoD + SIPA + tests, NO auto-merge for cwk-*",
        "description": (
            "Enforce the approved gate on autonomously-built co-workers. On the per-domain cwk-<d>-vv "
            "task: re-run the deliberator (CoD go/no-go) on the diff, run SIPA Mode-B integrity self-vet "
            "of the generated YAML/roles (static + quarantine on fail), require tests green, then HITL "
            "approval. EXCLUDE the cwk-* prefix from the pr_watcher (OPT-70) hybrid auto-merge "
            "allow-list so no co-worker merges without a human. Autobuilt co-workers land with "
            "status='autobuilt_pending_review' (uncounted/unusable until a human flips to active). "
            "Document the gate in args/security_gates.yaml."
        ),
        "task_type": "build", "priority": "medium", "depends_on_task_id": "cwk-auto-03",
    },
    # ── Epic vv: end-to-end verification of the framework ───────────────────
    {
        "id": "cwk-vv-01",
        "title": "CWK V&V: full E2E + gates green + companion sync",
        "description": (
            "1. Playwright E2E: open /coworkers, pick Security, ask a RAG question (cited answer, no "
            "team), then a build request -> HITL confirm -> ACE team launches -> ace_progress SSE "
            "streams -> cwk_sessions row written (screenshot to playwright/screenshots/coworkers-e2e.png). "
            "2. Seed one fake gap, run a foundry cycle, confirm a cwk-<d>-* chain appears gated behind "
            "CoD+SIPA+HITL and excluded from auto-merge. 3. ruff check . (exact CI cmd) + pytest tests/ "
            "-v --tb=short green (incl. the 29 ACE tests unbroken) + coherence_checker --all --fix --gate "
            "green + FOREGROUND companion.py --sync --write. 4. Confirm 5 reference co-workers + "
            "Strategos answer + launch."
        ),
        "task_type": "test", "priority": "critical", "depends_on_task_id": "cwk-auto-04",
    },
]


def run():
    conn = get_connection()
    # 1. Re-queue genuine gaps (done -> backlog), re-verifying absence.
    requeued, kept_done = [], []
    for tid, artifact in REQUEUE.items():
        row = conn.execute(
            "SELECT status FROM kanban_tasks WHERE id=?", (tid,)
        ).fetchone()
        if not row:
            print(f"  MISSING TASK (skip): {tid}")
            continue
        if _artifact_present(conn, artifact):
            kept_done.append((tid, artifact))
            continue  # artifact now exists -> leave as-is (truthful)
        if row["status"] == "done":
            conn.execute(
                "UPDATE kanban_tasks SET status='backlog', updated_at=? WHERE id=?",
                (NOW, tid),
            )
            requeued.append(tid)
        else:
            print(f"  ALREADY {row['status']} (skip): {tid}")
    conn.commit()

    conn.commit()
    conn.close()

    # 2. Seed the cwk-* chain. Status MUST be 'backlog' (not 'scheduled'):
    # the dispatcher's scheduled-task query requires scheduled_at IS NOT NULL,
    # so a 'scheduled' row with NULL scheduled_at never dispatches and deadlocks
    # the whole chain. The backlog path applies the same depends_on gate. See
    # seed_tch.py / seed_uclb.py for the canonical pattern. The factory defaults
    # status to 'backlog', so omitting status preserves that invariant.
    report = create_tasks("cwk", CWK_TASKS, strict=False, register_project=False)
    print(report.summary())

    print(f"\nRe-queued {len(requeued)} genuine gaps -> scheduled: {requeued}")
    if kept_done:
        print(f"Left done (artifact now present): {[t for t,_ in kept_done]}")
    print(f"Seeded {len(report.seeded)} cwk-* tasks (project 'cwk', rooted on acf-vv-04).")


if __name__ == "__main__":
    run()
