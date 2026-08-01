#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the OSS-02 nine-project adaptation outcome onto the kanban board.

Backs ``docs/spikes/oss-02-nine-project-adaptation.md``.

Note the shape of this card: FIVE of the seven work tasks are defect fixes, not
adaptations. Nine projects yielded exactly one narrow idea worth evaluating
(agent-chief's worthiness filter); mapping the rest against the tree found more
broken-in-place capability than missing capability.

Every task declares ``depends_on_task_id = oss2-gate-00``. That FK is what
actually blocks auto-dispatch — ``state_machine`` resolves dependants through it.
The ``-gate-00`` id suffix only protects the sentinel itself from being
promoted/reaped (``tools/kanban/gates.py::is_manual_gate``).

Usage::

    python tools/kanban/seed_oss02_kanban.py            # seed
    python tools/kanban/seed_oss02_kanban.py --json     # machine-readable report
    python tools/kanban/seed_oss02_kanban.py --dry-run  # print, insert nothing
"""

from __future__ import annotations

import argparse
import json
import sys

SPIKE = "docs/spikes/oss-02-nine-project-adaptation.md"

GATE_ID = "oss2-gate-00"


def _t(
    task_id: str,
    title: str,
    description: str,
    *,
    priority: str = "medium",
    task_type: str = "build",
    status: str = "backlog",
) -> dict:
    """Build a task spec; everything except the gate depends on the gate."""
    spec = {
        "id": task_id,
        "title": title,
        "description": description.strip(),
        "task_type": task_type,
        "priority": priority,
        "status": status,
        "dispatch_source": "oss02_spike_seed",
        "idempotency_key": f"oss-02::{task_id}",
    }
    if task_id != GATE_ID:
        spec["depends_on_task_id"] = GATE_ID
    return spec


TASKS: list[dict] = [
    _t(
        GATE_ID,
        "MANUAL-MODE GATE — OSS-02 nine-project adaptation (held)",
        f"""
MANUAL GATE — do not complete via automation, do not open a PR for this task.

Holds every `oss2-*` task until a human has read {SPIKE}.

WHAT THIS REVIEW CONCLUDED (so nobody re-runs it):

  Six of the nine projects were ALREADY DECIDED before this review:
    anything-llm  — competitive-scan target only; npm/Docker runtime excluded by
                    standing policy (args/innovation_config.yaml:552)
    DeepDoc       — oss-00 "reject the implementation, take the goal"; successor
                    work is LIVE on the board as oss-table-01/02 (scheduled).
                    DO NOT create a second table-extraction card.
    LocalAI       — already supported via type: openai_compatible (ADR D67,
                    pyproject.toml:86). Config, not code.
    AutoGen       — upstream is in MAINTENANCE MODE (no new features; Microsoft
                    steers users to Agent Framework). Already credited as
                    inspiration at NOTICE:374. Not imported anywhere.
    CrewAI        — already credited at NOTICE:369; skill-format interop exists
                    at tools/ace/skill_adapter.py:274. Also requires
                    py >=3.10,<3.14, which excludes both our 3.9 floor and the
                    3.14 interpreter in use.
    mem0          — already partly adopted (chat_memory.py:9 cites it); the rest
                    of its architecture is already built. See oss2-fix-04.

  Three had NEVER been evaluated (zero matches anywhere in the tree):
    agent-chief   — ADOPT ONE IDEA (oss2-triage-01)
    watch-skill   — REJECT; and the premise fails here (playwright/videos/ is
                    empty; screenshot_validator.py already analyses screenshots)
    rocketplaneIO — REJECT; Go, and its approval-gate idea is already our model

Release procedure:

  1. Confirm the §1 verdict table still reads correctly.
  2. Note the ordering constraint: oss2-fix-04 (repair memory consolidation)
     comes BEFORE oss2-meas-01 (measure the memory tier). Measuring a subsystem
     whose consolidation stage silently no-ops would produce a meaningless number.
  3. Set this task done:
     python tools/kanban/cli.py --set-status {GATE_ID} done
""",
        priority="high",
        task_type="chore",
        status="in_progress",
    ),
    # ── Epic: fix ─────────────────────────────────────────────────────────────
    _t(
        "oss2-fix-04",
        "Repair memory consolidation — it is dead code (D5)",
        f"""
Defect D5 in {SPIKE} — severity HIGH. Do this BEFORE oss2-meas-01.

`MemoryConsolidator` cannot work. Both paths fail and both failures are
swallowed by broad excepts, so the failure is completely silent:

  1. PRIMARY PATH — tools/memory/memory_consolidation.py:103 does
       from tools.memory.hybrid_search import hybrid_search
     That symbol DOES NOT EXIST. hybrid_search.py exports: search, hybrid_rank,
     bm25_search, semantic_search, fts5_search, get_all_entries.
     Caught by `except (ImportError, Exception)`.

  2. JACCARD FALLBACK — :128 and :353 run
       SELECT id, content, entry_type FROM memory_entries
     The column is `type`. `entry_type` does not exist. Caught at :159,
     returns [].

Both reproduced against live PostgreSQL on 2026-07-26:
  import hybrid_search : ImportError -> cannot import name 'hybrid_search'
  entry_type SQL       : UndefinedColumn -> column "entry_type" does not exist

NET EFFECT: check_for_consolidation() ALWAYS returns KEEP_SEPARATE /
should_write=True. Semantically redundant memories accumulate forever and
nothing is ever merged. Exact-hash dedup still works, which is exactly why this
went unnoticed — the table grows with near-duplicates, not exact duplicates.

Do:
  - Fix the import to use the real symbol (`search`), matching its signature.
  - Fix `entry_type` -> `type` in both SQL sites (:128, :353) and the row-dict
    accessors that follow (:116, :150, :364).
  - NARROW THE EXCEPTS. A broad `except (ImportError, Exception)` that turns a
    typo into a silent no-op is the actual root cause; both bugs would have
    surfaced immediately without it.
  - Add a regression test that FAILS against the current code — assert
    check_for_consolidation() returns a non-KEEP_SEPARATE decision for a
    deliberately near-duplicate pair.

Acceptance: consolidation demonstrably merges a seeded near-duplicate pair;
the regression test fails on the pre-fix code.
""",
        priority="high",
        task_type="fix",
    ),
    _t(
        "oss2-fix-05",
        "memory_read cannot render DB entries; decay_weight is a dead column (D6, D7)",
        f"""
Defects D6 (Medium) and D7 (Low) in {SPIKE}.

D6 — tools/memory/memory_read.py
  `read_db_recent()` selects SIX columns at :70
     content, type, importance, created_at, classification, compartment
  but `format_markdown()` unpacks FOUR at :114
     for content, type_, importance, created_at in db_entries
  Composing them raises `ValueError: too many values to unpack (expected 4)` —
  reproduced directly on 2026-07-26 (read_db_recent returned 5 rows, width 6).

  Separately, read_db_recent builds SQL with BARE `?` PLACEHOLDERS (:73, :76,
  :78) — SQLite dialect. On the configured PostgreSQL backend this trips the
  translate_sql bare-placeholder warning. Per CLAUDE.md, runtime SQL is authored
  for PostgreSQL and translate_sql is an init-only fallback, never load-bearing.

  This matters because it is the module behind
    python tools/memory/memory_read.py --format markdown
  which is the FIRST command in CLAUDE.md's Session Start Protocol.

D7 — `decay_weight` is a dead column.
  Written as 1.0 on every insert (memory_write.py:104, :115, :125).
  memory_write.py:88 comments it is "managed by hybrid_search decay pass" —
  no such pass exists; grep for decay_weight in hybrid_search.py and
  time_decay.py returns nothing. The only writer is reset_decay() (:282), which
  has no callers. Actual decay is computed on the fly from created_at, so
  retrieval never strengthens a memory.

Do: fix the unpack, convert the placeholders to %s, and either wire decay_weight
into the retrieval path or drop it and the misleading comment. Decide explicitly
rather than leaving a column that implies behaviour that does not exist.

Acceptance: `python tools/memory/memory_read.py --format markdown` renders DB
entries on PostgreSQL without warnings; a test covers the 6-column render.
""",
        task_type="fix",
    ),
    _t(
        "oss2-fix-01",
        "LocalAI is unreachable by default — wrong probe port, no named provider (D1)",
        f"""
Defect D1 in {SPIKE} — severity Low.

tools/airgap/detector.py:93 documents "LocalAI (8080)" but :105 defaults to
  ("localai", os.environ.get("LOCALAI_BASE_URL", "http://localhost:8081/v1"))
8080 having already been claimed by llama_cpp at :103. LocalAI's actual upstream
default is 8080, so probe_local_llm_servers() does not detect a stock install
unless LOCALAI_BASE_URL is set explicitly.

Also: there is no named `localai:` provider in args/llm_config.yaml. Only
openai, vllm, mistral and mistral_vllm use `type: openai_compatible`. Since
OpenAICompatibleProvider serves BOTH inference and embeddings, adding LocalAI is
a ~4-line YAML block and no code (ADR D67; pyproject.toml:86 already states
LocalAI is supported this way).

Do:
  - Resolve the port conflict honestly — probe both 8080 and 8081 for localai,
    or document why 8081 is correct here. Do not just silently change the number.
  - Add a `localai:` provider entry to args/llm_config.yaml, default-off,
    following the vllm entry's shape.

Acceptance: a stock LocalAI on :8080 is detected by
`python -m tools.airgap --detect --json`, and is routable via config alone.
""",
        priority="low",
        task_type="fix",
    ),
    _t(
        "oss2-fix-02",
        "Template chunking shipped but DIC ingest never uses it (D2)",
        f"""
Defect D2 in {SPIKE} — severity Medium.

oss-chunk-01 delivered args/chunking_templates.yaml (234 lines) and
tools/rag/chunking_templates.py (484 lines) with 10 templates — general,
oscal_catalog, stig_checklist, rfp_sow, contract, sop_runbook, slide_deck,
spreadsheet, canvas_graph, canvas_assessment — and three strategies
(sliding_window, structural, row_groups). Documented at
docs/features/phase-oss-chunk-01-template-chunking.md.

But tools/document_intelligence/ingest_orchestrator.py:1740 calls:
    chunks = chunk_content(
        text, source_type="dic_document", source_id=..., source_table=...,
        metadata=..., tenant_id=..., project_id=..., classification=...,
    )
with NO `template=` argument. So every DIC-ingested document falls back to
`general` sliding-window chunking regardless of type.

The capability built specifically to stop splitting NIST controls mid-control and
STIG rules mid-check is not reached by the pipeline it was built for.

Do:
  - Pass a template through from the ingest path. `suggest_template(content)`
    already exists for detection — per oss-00 A2, auto-detection must be a
    SUGGESTION surfaced to the operator, never a silent choice, and the template
    used must be recorded on the chunk for auditability.
  - Verify with a control-catalog fixture: chunks should map 1:1 to controls
    with zero split controls.
  - Guard against regression: assert no recall/MRR/nDCG regression on
    args/rag/golden_query_set.yaml via tools/rag/rag_benchmark.py.

Acceptance: a DIC-ingested OSCAL catalog chunks by control, not by window.
""",
        task_type="fix",
    ),
    _t(
        "oss2-fix-03",
        "Four skill cards describe AutoGen agents that nothing executes (D3)",
        f"""
Defect D3 in {SPIKE} — severity Low.

These four files embed JSON agent definitions (system_message, human_input_mode,
max_consecutive_auto_reply) with provenance `local://official-seed/autogen/...`
and Trust Score 0.3:
  .claude/commands/code_review_agent.md
  .claude/commands/test_orchestrator_agent.md
  .claude/commands/security_researcher.md
  .claude/commands/senior_software_engineer.md
(plus mirrors under icdev/data/claude_bootstrap/claude/commands/)

AutoGen is NOT imported anywhere in the tree — no `import autogen`/`pyautogen`
in any .py, and it is absent from requirements.txt and pyproject.toml. So these
are inert data blobs presented as capabilities.

This is the same CLASS of defect as the phantom `AirgapDriverMissingError` found
in cdp-00 — documentation asserting a capability the code does not provide —
though materially lower severity, since these are seeded marketplace cards
rather than a safety guarantee. Note tools/ace/skill_adapter.py:289
`_normalize_autogen` DOES exist and works: it converts AutoGen-format skills INTO
ICDEV skills. So the format is understood; only these four cards are inert.

Do: pick one and apply it consistently —
  (a) make them executable by routing through the ACE agent-loop path, or
  (b) relabel them explicitly as imported format samples, or
  (c) remove them.
Whichever is chosen, the SkillHub seed (tools/skillhub/seed_official_skills.py)
must stop re-seeding inert cards.

Acceptance: no .claude/commands/*.md advertises an executable agent that no code
path can execute.
""",
        priority="low",
        task_type="chore",
    ),
    # ── Epic: meas ────────────────────────────────────────────────────────────
    _t(
        "oss2-meas-01",
        "Measure the memory tier before building anything mem0-shaped",
        f"""
§3.4 of {SPIKE}. BLOCKED ON oss2-fix-04 — do not start first.

Measuring a memory subsystem whose consolidation stage silently no-ops produces
a meaningless number. Fix D5 first, then measure.

Context: mem0 is NOT being adopted. ICDEV already has the architecture it
describes — tiering (memory_tier="episodic|semantic", on by DEFAULT at
icdev/tools/llm/agent_loop.py:747-756), auto-capture, consolidation, hybrid
lexical+dense retrieval, decay, and scheduled upkeep across tools/memory/.

The open questions are empirical:
  1. Is the agent-loop memory tier earning its keep? It is on by default and
     injects into every fresh loop — but nobody has measured whether it improves
     outcomes or just spends tokens.
  2. Would intent-relative temporal ranking (mem0's "past / current / upcoming"
     framing) beat the plain recency decay in tools/memory/time_decay.py?
  3. Does entity linking ACROSS memories add anything the KG does not already
     provide separately?

Use the existing harness: tools/rag/rag_benchmark.py against
args/rag/golden_query_set.yaml — the same one that produced the
KEEP(contextual_retrieval) / DROP(raptor) decisions.

DISCIPLINE: the RAPTOR DROP was later WITHDRAWN on re-measurement against the v2
48-query set (+0.0208 recall@5, oss-00:67). Upstream benchmark numbers measure
upstream's corpus. Report the delta against committed baselines, both directions.

Acceptance: a recorded measurement with numbers, and an explicit
build / do-not-build recommendation derived from them. Building without this is
out of scope.
""",
        task_type="research",
    ),
    # ── Epic: triage ──────────────────────────────────────────────────────────
    _t(
        "oss2-triage-01",
        "Evaluate a worthiness stage in front of notification routing (agent-chief)",
        f"""
§4 of {SPIKE}. The ONE genuinely new idea out of nine projects.

Source: github.com/SmileLikeYe/agent-chief (MIT, Python 3.12+, ~1k stars, 418
offline tests). A three-stage worthiness engine decides per event whether to
INTERRUPT the user, DISPATCH work to an agent, or FILE it for later. Upstream
claims 24 events in -> 1 interruption.

ADOPT THE PATTERN, NOT THE PACKAGE — it requires Python 3.12+ and our floor is
3.9 (pyproject.toml:10). Needs an _ATTRIBUTION_REGISTRY entry in
tools/workflow/coherence_checker.py BEFORE any file cites it.

SCOPE THIS AGAINST WHAT EXISTS — tools/notifications/ is a real subsystem:
  gateway.py        — dispatch entry point
  routing_rules.py  — load_rules, resolve_channels, _dimension_matches
  escalation.py     — register_alert, acknowledge, process_escalations,
                      get_escalation, ack_link (ack tokens, timers, audited)
  preferences.py    — per-recipient preferences
  plus digest behaviour in tools/genesis/reflexes/dic_digest.py

So routing, escalation, acknowledgement and preferences ALREADY EXIST. The gap
is narrower than agent-chief's README implies: there is no SCORED WORTHINESS
DECISION UPSTREAM OF ROUTING — nothing decides whether an event deserves
attention at all before choosing where to send it.

Why it plausibly matters here: dozens of Genesis reflexes fire on schedules
(awareness 3h, foundry 12h, OSINT 4h, and more), the kanban board generates cards
autonomously, and the awareness engine promotes predictions into suggested tasks.
That is the event-volume profile agent-chief targets.

Do: EVALUATE FIRST, build second. Measure current event volume and how much of it
reaches a human. If the ratio is already sane, close this as no-action. If not,
propose a worthiness stage in front of resolve_channels — explicitly NOT a new
notification system, and explicitly not a second escalation path.

Acceptance: a measured baseline (events generated vs events surfaced) and a
build / no-build recommendation derived from it.
""",
        task_type="research",
    ),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed OSS-02 nine-project adaptation tasks")
    ap.add_argument("--json", action="store_true", help="JSON report to stdout")
    ap.add_argument("--dry-run", action="store_true", help="Print, insert nothing")
    args = ap.parse_args(argv)

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "count": len(TASKS),
            "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]} for t in TASKS],
        }, indent=2))
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)
    report = {
        "created": created,
        "created_count": len(created),
        "submitted_count": len(TASKS),
        "skipped_existing": [t["id"] for t in TASKS if t["id"] not in created],
        "gate": GATE_ID,
        "spike": SPIKE,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Seeded {len(created)}/{len(TASKS)} OSS-02 tasks (gate: {GATE_ID})")
        for tid in created:
            print(f"  + {tid}")
        if report["skipped_existing"]:
            print("  (already present: " + ", ".join(report["skipped_existing"]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
