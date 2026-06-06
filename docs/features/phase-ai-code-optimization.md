# CUI // SP-CTI
# Feature: AI Code Optimization (Reuse-First, No-Placeholder) + Graphify Adaptation

## Context

AI-generated code tends to be bloated, re-implements helpers that already exist,
and ships placeholder/stub bodies. This feature keeps generated code lean across
two enforcement points and adapts the useful ideas from
`github.com/safishamsi/graphify`.

**Graphify finding:** its core (a local, deterministic code knowledge graph) is
already shipped in ICDEV as `tools/awareness/component_indexer.py`
(`kg_nodes`/`kg_edges`, `/components-map`). We did **not** rebuild graph
infrastructure. We adapted graphify's *query-the-graph-for-reuse*, *call-flow
export*, and *PR-impact via communities* onto that existing graph.

## What shipped

### Phase 1 — Post-generation zero-tolerance gate
- `tools/workflow/coherence_checker.py`:
  - `check_no_placeholders` (**BLOCKING**) — any TODO/FIXME/pass-only/ellipsis/
    `NotImplementedError`/placeholder-return in a changed **non-test** source file
    fails the gate. Reuses `tools/testing/stub_detector.py:check_substantive`
    (no detection logic duplicated). Scope: `--changed-files` only (no-op under
    `--all`, so existing gates stay green); `tests/`, `test_*.py`, `conftest.py`,
    and `.tmp/` are exempt.
  - `check_duplicate_code` (**WARN**) — a changed function that is a verbatim
    (rename-insensitive) copy of an existing `tools/` function. Exact
    normalized-body hashing → near-zero false positives.
- `args/security_gates.yaml` → `codegen_quality` gate.
- `hardprompts/karpathy_principles.md` — filled the file referenced by CLAUDE.md
  but previously missing.
- Wired into ANVIL VERIFY (`.claude/commands/feature.md`, step 11b).

### Phase 2 — Pre-generation reuse brief
- `tools/codegen/reuse_scout.py` — queries the self-awareness KG + manifest
  shards + `api_surface_extractor` to produce a **REUSE THESE / GENERATE ONLY**
  brief. Deterministic, air-gap safe (degrades to manifest grep).
- `hardprompts/minimal_generation.md` — generation guardrail prompt.
- Wired into `feature.md` (step 7b), `goals/tdd_workflow.md` (Step 4), and
  `tools/llm/router.py` (`_codegen_augment` appends the guardrail to
  `code_generation` requests).

### Phase 3 — Graphify call-flow + PR-impact
- `tools/awareness/callflow.py` — function/module call graph; standalone
  call-flow HTML export; persists module-level `calls` edges to `kg_edges`
  (1308 edges over the live tree).
- `tools/awareness/change_impact.py` — PR blast radius (reverse call-graph
  reachability), connected-component communities, routes affected. Wired into
  `.claude/commands/review.md`.

## Verification

- `pytest tests/test_coherence_codegen_quality.py tests/test_reuse_scout.py tests/test_callflow_change_impact.py` — 23 tests pass.
- Gate dogfood: `coherence_checker --check no_placeholders,duplicate_code --changed-files <this feature's files> --gate` → exit 0.
- `reuse_scout --intent "open a database connection" --symbols get_connection,make_widget` → `get_connection` in `already_exists`, `make_widget` in `generate_only`.
- `change_impact --changed-files tools/awareness/callflow.py` → blast radius includes `change_impact.py`.

## Non-goals

- No tree-sitter / multi-language graph rebuild (ICDEV KG already covers the Python tree).
- No new DB tables (reuse `kg_nodes`, `kg_edges`, `stub_detection_results`).
- Duplicate detection is a warning, not a merge blocker (heuristic).
