# CUI // SP-CTI
"""Seed the BOM Evidence Engine + CONCORD initiative onto the kanban board.

Two streams, two repos — and the prefixes are what keep them apart:

  bom-*   builds in ICDEV (open-source engine, tools/bom/). Default dispatch.
  cncd-*  builds in the CONCORD repo. Registered in args/kanban_external_repos.yaml
          so it can never fall through to the ICDev default, and held behind
          cncd-gate-00 (parked in_progress) until that repo is scaffolded.

Descriptions are written for a session arriving cold. That is the point of
seeding: token exhaustion mid-build must not lose the plan.

NOTE ON CONTENT. ICDEV is a PUBLIC repository. These descriptions carry the
engineering — which is ours — and none of the customer evidence the engine was
developed against, which is not. Real figures, filenames, quoted notes and part
numbers belong in the private CONCORD repo and in the local board, never here.
The distinction is the same one the engine itself enforces: publish the
technique, never the data.

    python -m tools.kanban.seed_bom_concord            # seed
    python -m tools.kanban.seed_bom_concord --dry-run  # print, change nothing
"""
from __future__ import annotations

import argparse
import json

from tools.kanban.task_factory import create_tasks

# ── The ICDEV-side engine ────────────────────────────────────────────────────
ENGINE: list[dict] = [
    {
        "id": "bom-schema-01",
        "title": "BOM schema + migration 267",
        "description": (
            "tools/bom/constants.py (closed vocabularies), tools/bom/db/init_db.py "
            "(SCHEMA_PG, 21 tables), emit_migration.py, and migration "
            "322_bom_evidence_engine.sql GENERATED from SCHEMA_PG so the two cannot "
            "drift. CHECK constraints are derived from the Python tuples. "
            "bom_match_decisions + bom_audit are append-only.\n\n"
            "Invariants the schema encodes, each because losing it would put a wrong "
            "number in front of an executive rather than raise an error: price_basis "
            "defaults to 'unknown', not to something convenient; an unchosen option "
            "group is NULL and therefore contributes zero; claimed_qty and "
            "verified_qty are separate facts and their disagreement is a 'decision', "
            "never a 'defect'."
        ),
        "priority": "high",
    },
    {
        "id": "bom-extract-01",
        "title": "Cell-grid extractor — keep formulas and coordinates",
        "description": (
            "tools/bom/extract_grid.py. THE foundation; nothing downstream works "
            "without it.\n\n"
            "Why a NEW module rather than a fix to the existing one: "
            "document_intelligence/extractors.py::_extract_xlsx loads data_only=True "
            "(formulas gone) and joins cells with ' | ' (coordinates gone); "
            "_extract_pptx keeps only shapes with .text, so PPTX tables "
            "(GraphicFrame, no .text) are dropped entirely. Both are right for a RAG "
            "index and fatal here. Bending extract_file() to carry cell provenance "
            "would break every existing DIC/RAG consumer.\n\n"
            "XLSX: load_workbook TWICE (data_only True for values, False for "
            "formulas) and zip cell by cell. Without both halves you cannot tell a "
            "double-count from a genuine second unit — the difference lives entirely "
            "in which SUM() consumes which cell. PPTX: real tables via has_table, "
            "PLUS geometric reconstruction of grids drawn out of loose text boxes "
            "(what every script-generated deck looks like). PDF: "
            "pdfplumber.extract_tables. Header detection deterministic; escalate to "
            "an LLM column-role mapping only when scoring fails."
        ),
        "priority": "high",
    },
    {
        "id": "bom-extract-02",
        "title": "Register .drawio in the extractor registry",
        "description": (
            "parse_drawio(xml) exists in tools/simulation/parsers/ and was never "
            "registered in extractors._EXTRACTORS. It also could not read a real "
            "draw.io file: it did find('root'), which searches direct children only, "
            "while a saved file is <mxfile>/<diagram>/<mxGraphModel>/<root>. It "
            "returned an empty graph with no error — a diagram full of components "
            "read as 'no components'. Fix additively and cover it; there was no test "
            "file, which is how it survived.\n\n"
            "Parse each <diagram> tab separately and tag its nodes. A rack elevation "
            "is the only drawing that says HOW MANY, and merging it into a floor plan "
            "throws that away. Also handle the deflate+base64 <diagram> payload "
            "draw.io often writes."
        ),
        "priority": "high",
    },
    {
        "id": "bom-forensic-01",
        "title": "Hidden-data forensics",
        "description": (
            "tools/bom/forensics.py. Everything here is IN the file and NOT on the "
            "screen.\n\n"
            "A worksheet can report its dimensions as A1:A1 — Excel says empty, it "
            "opens empty — and still carry an anchored image holding a screenshot of "
            "a constraint that never made it into a cell. OCR it, and keep the "
            "confidence WITH the text: quoting an OCR misread at somebody who "
            "controls a budget is its own kind of disaster.\n\n"
            "Also: gaps in the sheetId sequence (Excel never reuses one, so a hole is "
            "a deleted sheet — in a costing workbook, a category somebody removed); "
            "speaker notes (which hold the constraint the slide was too polite to "
            "show); cell comments; hyperlinks out to the real source; DOCX tracked "
            "changes; embedded photographs; sensitivity labels; and unfinished values "
            "(TBD) left in a document that is being costed.\n\n"
            "Documents written by a script (creator=openpyxl / python-pptx) are "
            "flagged: not a scandal, but a fact about how much their numbers are "
            "worth, and it belongs in the credibility assessment."
        ),
        "priority": "high",
    },
    {
        "id": "bom-cred-01",
        "title": "Source credibility scoring",
        "description": (
            "tools/bom/credibility.py + args/bom_credibility.yaml.\n\n"
            "Users encode credibility informally — by renaming files, by folder, by "
            "tone. Capture it as DATA; never parse it out of a filename in Python. "
            "The status vocabulary lives in args/bom_credibility.yaml as a weighted "
            "signal lexicon so a customer adds their own words without a code change.\n\n"
            "Deterministic signals, no LLM: is this file the native source of another "
            "in the corpus (native up, derived capped); live formulas present; part "
            "numbers present; serials with no price columns -> propose "
            "inventory_truth; named human author + revision count (up); "
            "machine-generated (down); unresolved placeholders (down).\n\n"
            "AI PROPOSES a tier with a rationale; only a human's setting is BINDING. "
            "Credibility is the FIRST tiebreaker in winner selection. Two sources "
            "both marked authoritative that disagree are NEVER auto-resolved — that "
            "is a real dispute between two things the customer vouched for, and the "
            "tool does not get to pick a side."
        ),
        "priority": "high",
    },
    {
        "id": "bom-cred-02",
        "title": "Derivative-representation detection",
        "description": (
            "tools/bom/derivative.py. The same document in two formats must never be "
            "counted twice — a PDF print of a workbook, emailed alongside the "
            "workbook, will otherwise double the entire BOM.\n\n"
            "Fingerprint by normalized row-content Jaccard + heading overlap; >= "
            "constants.DERIVATIVE_OVERLAP means same document. Prefer the most "
            "structured representation (REPRESENTATION_FIDELITY: xlsx_formulas > xlsx "
            "> drawio > csv > pptx_tables > docx > pptx > pdf) — formulas are where "
            "the errors hide, so a copy that still has them is worth more. The loser "
            "gets role='derived' and is EXCLUDED from every rollup: it is the same "
            "money, not deprioritized money. Kept for audit, never deleted."
        ),
        "priority": "high",
    },
    {
        "id": "bom-find-01",
        "title": "Findings: the formula-graph detectors",
        "description": (
            "tools/bom/findings.py. Deterministic, NO LLM. These are the ones that "
            "sell the product.\n\n"
            "Build bom_rollup_edges by parsing every SUM/SUBTOTAL/SUMIF into "
            "(target_cell -> consumed_cells).\n\n"
            "intra_doc_double_count is NOT dedup. A licence can legitimately appear "
            "on two sheets as a cross-reference; the BUG is when both sheet subtotals "
            "include it. If two cells are consumed by DIFFERENT rollups that both "
            "feed the grand total, the money is counted twice. If they feed the SAME "
            "rollup, it is a genuine 2x quantity. That distinction is impossible "
            "without the formula graph and it is the entire ballgame. A note matching "
            "'shared with|see also|counted in' plus a literal sheet name upgrades "
            "severity and is quoted verbatim as evidence.\n\n"
            "hardcoded_rollup: a cell with value_is_formula=FALSE whose siblings in "
            "the same summary block are TRUE — edit the source sheets and the total "
            "silently will not move. stale_rollup: recompute each subtotal and "
            "compare.\n\n"
            "unpriced_line_zeroed: qty>0, unit_price NULL, formula yields 0. The line "
            "looks costed and costs nothing; the total is understated by whatever the "
            "item is worth. Report it as understated by an UNQUANTIFIED amount — do "
            "not invent the missing price."
        ),
        "priority": "high",
    },
    {
        "id": "bom-find-02",
        "title": "Findings: owned hardware and the sustainment ask",
        "description": (
            "READ THIS FIRST. None of these findings is an accusation. Repurposing "
            "hardware you already own is good engineering and real avoided CapEx, and "
            "it belongs on the leadership slide as a WIN.\n\n"
            "A serial number proves a machine EXISTS. Its ABSENCE proves nothing — "
            "inventories go stale, and a rack of real servers can be missing from a "
            "spreadsheet. The engine must NEVER conclude that hardware is fictional. "
            "asset_count_disputed is kind='decision': 'the design leans on N units; "
            "the inventory has serials for M — is the inventory incomplete, or is the "
            "design over-drawn?' Offer both dispositions. Somebody has to go and look.\n\n"
            "Refresh reserve: for existing_asset lines past warranty_end, compute "
            "refresh_reserve_usd = verified_qty * replacement_unit_price, where the "
            "replacement price is sourced from a REAL clustered new-buy line of the "
            "same function elsewhere in the corpus. If nothing prices it, leave NULL "
            "and raise no_replacement_price_basis. Never guess. Emit it as a "
            "line_kind='reserve' line carrying its derivation in words a reviewer can "
            "check.\n\n"
            "Also compute avoided_capex_usd — the value of the fleet being "
            "contributed. Leadership should be told what they are getting for free."
        ),
        "priority": "high",
    },
    {
        "id": "bom-find-03",
        "title": "Findings: money-shape (capex/opex, price basis, budget)",
        "description": (
            "capex_opex_conflation: recurring costs (monthly ISP, subscriptions, "
            "multi-year support) sitting inside a one-time CapEx table understate the "
            "true multi-year cost. Regex for '/mo', 'per month', 'annual', "
            "'recurring', 'subscription', 'N-yr'. Bonus signal: a SUMIF that excludes "
            "a sentinel PROVES that sheet is already filtering recurring rows, so two "
            "sheets in the same workbook have different bases.\n\n"
            "price_basis_mismatch: distinct price_basis within a cluster. If any "
            "member is 'unknown', REFUSE to normalize — emit a finding rather than "
            "invent a discount.\n\n"
            "budget_variance: bom_projects.budget_floor/ceiling vs the committed "
            "total. A BOM that ignores its own stated envelope is a finding, not a "
            "surprise at the review.\n\n"
            "arithmetic_mismatch: extended != qty * unit."
        ),
        "priority": "high",
    },
    {
        "id": "bom-conform-01",
        "title": "Baseline-architecture conformance",
        "description": (
            "tools/bom/conformance.py. A design everyone signed off on is not just "
            "another source — it is the YARDSTICK. A BOM is only defensible if it "
            "funds the design that was agreed, and spends only on what that design "
            "justifies.\n\n"
            "role='baseline_architecture'. Diagram nodes become "
            "bom_architecture_components: CLAIMED components, never priced lines. A "
            "drawing is the most persuasive kind of claim precisely because it looks "
            "like a photograph of something that already exists.\n\n"
            "Coverage runs BOTH directions, reusing the reconciler's own cluster "
            "machinery (a diagram says a product family; a BOM says a part number — "
            "that needs the trigram rung):\n"
            "  unfunded_component (CRITICAL): in the agreed design, no BOM line. This "
            "is how projects end up 80% funded.\n"
            "  unjustified_line: in the BOM, absent from the agreed design. Scope "
            "creep, or a stale line from an option that died.\n"
            "  baseline_asset_gap: the design draws more owned units than the "
            "inventory records — the same dispute as asset_count_disputed, reached "
            "from the drawing, and resolved the same way: by ASKING.\n\n"
            "Architecture-level option groups (scope='architecture'): two competing "
            "whole-system designs are mutually exclusive. Selecting one is a top-level "
            "pivot that re-filters the BOM and recomputes every total. With neither "
            "selected, BOTH contribute $0."
        ),
        "priority": "high",
    },
    {
        "id": "bom-conform-02",
        "title": "Declared-scope coverage — check intent, not just evidence",
        "description": (
            "An evidence-only engine is blind by construction to a workstream that "
            "exists solely in someone's head. You cannot detect the absence of "
            "something nobody wrote down — and that is the workstream that surfaces "
            "late, unfunded, in front of the wrong audience.\n\n"
            "Populate bom_scope_items from the project's stated intent (an LLM may "
            "PROPOSE items; a human confirms). Then check coverage against BOTH the "
            "architecture and the BOM:\n"
            "  scope_declared_undesigned: no architecture component covers it\n"
            "  scope_declared_unpriced: no BOM line prices it -> emit a "
            "line_kind='placeholder' with a NULL price. NULL, not zero: zero claims "
            "the work is free, and an invented figure gets quoted back at somebody in "
            "a budget meeting. A budget owner can earmark against a placeholder; they "
            "cannot earmark against silence.\n"
            "  scope_priced_only_by_weak_source: a whole workstream priced ONLY by a "
            "source nobody trusts, and present in no agreed design. That reads as "
            "'covered' on a spreadsheet and is not — credibility, conformance and "
            "scope all meet on this one."
        ),
        "priority": "high",
    },
    {
        "id": "bom-find-04",
        "title": "Time-phasing: waves, enablers and the critical path",
        "description": (
            "A BOM sorted by cost buries its own best news.\n\n"
            "Hardware already sitting in the building costs nothing and is often the "
            "reason a team can start building NOW instead of waiting on a facility. "
            "On a cost-sorted table it is the last row. That is a product failure.\n\n"
            "Populate wave_label / wave_order / unblocks / is_enabler / "
            "lead_time_days. Emit:\n"
            "  unblocks_now (kind='opportunity'): a zero-cost or already-owned "
            "capability that removes a dependency from the critical path. Surfaced as "
            "GOOD NEWS, not buried as a cheap row.\n"
            "  schedule_blocker: a long-lead item gating everything behind it. These "
            "are routinely stated only in a speaker note.\n"
            "  sequencing_absent: a large ask with no phasing. All-or-nothing funding "
            "requests get deferred; phased ones get approved.\n\n"
            "The reframed ask is not 'give us the whole number'. It is 'we start now "
            "at zero cost — here is what to earmark so we do not stall later.'"
        ),
        "priority": "high",
    },
    {
        "id": "bom-recon-01",
        "title": "Reconciliation: blocking ladder + deterministic scoring",
        "description": (
            "tools/bom/reconcile.py, stages 2-3. A pair is a candidate on ANY "
            "collision:\n\n"
            "B1 exact part_number_norm.\n"
            "B2 MinHash/LSH over char-3-grams, gated on matching manufacturer. "
            "REQUIRED: the same product written two ways (hyphens, suffixes, a "
            "truncated SKU) scores 0 on exact match and ~0.5 on trigram Jaccard. A "
            "single matcher fails this; the ladder catches it.\n"
            "B3 function_slug — two different products from two different vendors "
            "competing for the same job. This is the rung that works when a whole "
            "source has no part numbers at all.\n"
            "B4 category + price band, review-only.\n\n"
            "Scoring: deterministic feature vector using difflib.SequenceMatcher "
            "(stdlib, air-gap safe — there is no rapidfuzz in requirements and the "
            "embeddings path is network-bound). >= AUTO_CLUSTER_SCORE with an exact "
            "part match -> cluster, no LLM. <= DISCARD_SCORE -> discard, no LLM. Only "
            "the ambiguous middle band ever reaches a model."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-recon-02",
        "title": "Reconciliation: the LLM adjudicator (constrained in CODE)",
        "description": (
            "The highest-risk surface in the product. An LLM must NEVER invent or "
            "alter a number, and that is enforced structurally — not by asking nicely "
            "in a prompt:\n\n"
            "1. NO numeric property in the response schema except confidence. Prices "
            "are referenced by line_id only, so the model never SEES the money during "
            "identity adjudication and cannot anchor on it.\n"
            "2. Every returned string passes _ground_token (ingest_orchestrator.py) "
            "against the raw_text of both lines. Ungrounded -> verdict discarded -> "
            "pair falls to pending_review, unmerged.\n"
            "3. _MONEY_RE (content_grounding.py) runs over the model's 'reason' prose. "
            "ANY currency match VOIDS the entire response.\n"
            "4. It proposes a canonical LINE; the price is copied verbatim from that "
            "line's stored numeric.\n"
            "5. It may not create a category (SELECT-only via "
            "_ai_classify_into_taxonomy) and may not auto-approve (same_item sets "
            "pending_review, never accepted).\n"
            "6. Reject unless CortexResult.metadata['schema_valid'].\n\n"
            "Verdict vocabulary is constants.MATCH_VERDICTS. Route through "
            "tools/cortex/api.py::extract for the TRUST chain. Ship a --no-llm mode "
            "that still produces every deterministic finding — that mode is the demo "
            "that sells this."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-recon-03",
        "title": "Clustering, winner selection, and decision replay",
        "description": (
            "Union-Find over ACCEPTED same_item edges only. Guard: a cluster may not "
            "absorb two lines from the same document unless the double-count detector "
            "cleared them — otherwise the merger silently swallows the intra-doc "
            "duplicate and HIDES the finding.\n\n"
            "Winner selection is a stored policy (winner_policy_json), not code: "
            "credibility_tier -> authority_rank -> has part number -> price_basis rank "
            "-> newest -> confidence -> else pending_review. Numbers copied VERBATIM. "
            "Cluster qty defaults to max, not sum.\n\n"
            "HARD RULES: a basis-normalized price spread above "
            "FORCED_REVIEW_PRICE_RATIO forces human review regardless of match "
            "confidence — two products doing the same job at wildly different prices "
            "is a decision somebody has to make out loud, and averaging them produces "
            "a fiction. same_function_different_item contributes $0 to the committed "
            "total until someone picks, while carrying the RANGE. Two authoritative "
            "sources in conflict are never auto-resolved. A 'derived' source can never "
            "win.\n\n"
            "IDEMPOTENCY — the bug that would destroy the customer's work in week two: "
            "human decisions are keyed on the unordered pair of LINE_HASHES, never on "
            "cluster_id. Clusters are recomputed every run; key approvals to them and "
            "the next upload renumbers everything and orphans every prior decision, "
            "silently. Replay decisions BEFORE scoring; pinned pairs are never re-sent "
            "to the LLM. A previously-approved cluster whose winner WOULD flip on new "
            "evidence emits reopened_decision quoting the prior decision — the human "
            "re-confirms, or the old winner stands."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-pivot-01",
        "title": "True cross-tab pivot",
        "description": (
            "tools/bom/pivot.py. The one genuine engineering gap — ICDEV has only 1-D "
            "groupby (tools/viz/dataset.py::aggregate and its JS mirror in "
            "viz_story.js). Build rows x cols x measure on that primitive; do not "
            "rewrite it.\n\n"
            "Dimensions must include the ones that make this a BI tool rather than a "
            "report: category, wave, capex/opex, price_basis, source credibility, "
            "option group, funded/unfunded, scope item, architecture baseline. AI "
            "proposes the pivots that matter for the stated intent; the user can also "
            "drag freely. Curated views become deck slides."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-export-01",
        "title": "XLSX export with full provenance",
        "description": (
            "Reuse tools/govcon/bom_generator.py::export_bom_xlsx (money formats, "
            "frozen panes, by-category rollup) — copy the export half, replace the "
            "DB-bound fetch half. Every number traces to a source cell. Add a Findings "
            "sheet sorted by impact_usd and a Sources sheet showing the credibility "
            "ladder."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-export-02",
        "title": "Themed persuasive deck",
        "description": (
            "Reuse tools/slides/pptx_builder.py::build(slides, theme, title) (9 themes "
            "in THEME_PALETTES; slide types include table, card_grid, speaker_notes) "
            "and tools/viz/story_builder.py::build_dataset_slides (dataset -> KPI "
            "tiles + charts + a deterministic insight line, NO LLM). Audience arc from "
            "slides/constants.py AUDIENCE_MODE_HINTS / TONE_STYLE_HINTS / "
            "PITCH_TEMPLATES. Optional customer brand shell via template_fill.py.\n\n"
            "Trust model from compass MSR (tools/reporting/data_pack.py): FREEZE an "
            "immutable figures snapshot (bom_snapshots + content_sha256, cited in the "
            "footer), let the LLM write ONLY the narrative around it, turn [source: X] "
            "markers into numbered endnotes, and gate export on citation_gate() — "
            "empty list means pass.\n\n"
            "Required slides: the ASK; Findings sorted by impact_usd; the Sources / "
            "credibility ladder; the wave plan; and 'what you already own' (avoided "
            "CapEx and what it unblocks)."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-cortex-01",
        "title": "Cortex REST surface + cortex:bom scope",
        "description": (
            "POST /cortex/api/v1/bom/{ingest,extract,reconcile,taxonomy,findings,pivot,"
            "export} in tools/cortex/rest_v1.py behind a new cortex:bom scope "
            "(service_keys.py). Add the methods to the vendorable stdlib-only "
            "tools/cortex/client.py.\n\n"
            "Respect the existing security model: rebuild caller specs from a "
            "CONTENT-ONLY ALLOWLIST — image_path and every path-bearing key is "
            "stripped, because on a remote surface that is an arbitrary-file-read "
            "primitive. classification comes from the KEY's ceiling, never the body. "
            "Scopes are frozen at key creation."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-cortex-02",
        "title": "/bom canvas + MCP tools + registration checklist",
        "description": (
            "Canvas at /bom so the open-source core gets the capability too. ALL 8 "
            "completeness components must ship together (this has caused repeated "
            "failures): template, icdev/ mirror, @bp.route, backing module, constants, "
            "migration, nav link, IQE integration (adapter + POST /api/iqe-query + "
            "widget include + _CANVAS_MAP + PATH_CANVAS + >=3 seed queries).\n\n"
            "Plus the 8-point new-tool checklist: manifest shard, "
            "docs/reference/commands.md, args/security_gates.yaml, MCP tool_registry + "
            "gap_handlers, APPEND_ONLY_TABLES (done), tests/conftest.py (done), "
            "companion --sync, coherence_checker --gate. Add /bom to the Pages: line "
            "in .claude/commands/start.md — the route verifier reads the MAIN "
            "checkout, not the worktree."
        ),
        "priority": "medium",
    },
    {
        "id": "bom-find-05",
        "title": "Acceptance: synthetic corpora, and the no-hardcoding proof",
        "description": (
            "tests/bom/fixtures.py BUILDS documents that reproduce the SHAPES of the "
            "failures real ones have: a formula multiplying a quantity by an empty "
            "cell; a worksheet reporting A1:A1 that carries an anchored image; a gap "
            "in the sheetId sequence; a grid drawn out of loose text boxes; a "
            "multi-tab diagram. Hermetic, CI-runnable, and no customer content — "
            "ICDEV is a public repo.\n\n"
            "That is also the STRONGER claim. An engine proved against documents we "
            "constructed will find the same defects in files nobody here has ever "
            "seen, which is the actual requirement.\n\n"
            "The no-hardcoding proof: run a second synthetic corpus from a completely "
            "different domain (a vehicle fleet, a factory line) with its own reference "
            "architecture, and require it to run end to end with a DIFFERENT "
            "AI-derived taxonomy and a DIFFERENT credibility vocabulary, zero code "
            "changes.\n\n"
            "Also assert --no-llm mode still produces every deterministic finding.\n\n"
            "The golden test against the real customer corpus lives in the PRIVATE "
            "CONCORD repo (cncd-*), never here."
        ),
        "priority": "high",
    },
]

# ── The CONCORD standalone app ───────────────────────────────────────────────
CONCORD: list[dict] = [
    {
        "id": "cncd-gate-00",
        "title": "MANUAL GATE — hold the CONCORD stream (do not close)",
        "description": (
            "RISK: the CONCORD repo does not exist yet, so every cncd-* task would be "
            "dispatched against a target the runner cannot reach.\n\n"
            "Pipeline-exempt holding gate. While this task is in_progress, "
            "promote_backlog_to_scheduled will not dispatch any cncd-* task.\n\n"
            "Release only once the CONCORD repo exists AND "
            "$env:ICDEV_KANBAN_REPO_CONCORD points at it. Until then the ICDEV-side "
            "engine (bom-*) is the whole critical path.\n\n"
            "Do NOT open a kanban/<id> PR for this task."
        ),
        "status": "in_progress",
        "priority": "high",
    },
    {
        "id": "cncd-app-01",
        "title": "Scaffold CONCORD (FastAPI 8020 / React 5175 / SQLite)",
        "description": (
            "Third sibling to compass and idea_lab. Copy their conventions exactly — "
            "they are load-bearing:\n"
            "  - ports offset +10/+1 (idea_lab 8000/5173, compass 8010/5174, concord "
            "8020/5175)\n"
            "  - FORGE layering: args/*.yaml (all tunable behavior), hardprompts/, "
            "tools/ (ALL business logic), backend/routers/ (thin, no logic), goals/\n"
            "  - tools/db/storage.py::get_connection() is the ONE db entry point. It "
            "is a CACHED thread-local — NEVER conn.close() in a store; it poisons the "
            "cache for the whole thread.\n"
            "  - locate repo root by sentinel walk-up, never parents[N]\n"
            "  - NO compliance machinery — strip it from anything vendored out of ICDEV\n"
            "  - UI MUST use the ICDEV theme tokens in frontend/src/styles/global.css. "
            "Plain UI has been explicitly rejected before.\n\n"
            "Add concord's identifiers to _PREMIUM_PATTERNS in "
            "tests/ci/test_premium_leak_guard.py. ICDEV is PUBLIC; concord is private; "
            "code flows ICDEV -> premium by vendoring, never back."
        ),
        "priority": "high",
    },
    {
        "id": "cncd-app-02",
        "title": "Vendor the Cortex client + mint a service key",
        "description": (
            "Copy tools/cortex/client.py verbatim to tools/integrations/cortex_client.py "
            "with the provenance header (see compass/docs/VENDORED_FROM.md). It is "
            "stdlib-only BY DESIGN — never add app or icdev imports.\n\n"
            "Mint in ICDEV: python -m tools.cortex.service_keys create --label concord "
            "--tenant concord --scopes cortex:bom,cortex:extract,cortex:complete,"
            "cortex:classify,cortex:slides,cortex:dashboard --json\n\n"
            "Degradation contract, non-negotiable: every cross-repo call returns None "
            "when the peer is unreachable and NEVER raises. No workflow may depend on "
            "a sibling being up. A 4xx JSON body is RETURNED — a refusal is an answer."
        ),
        "priority": "high",
    },
    {
        "id": "cncd-corpus-01",
        "title": "Golden test against the real customer corpus (PRIVATE)",
        "description": (
            "This belongs HERE, not in ICDEV, because ICDEV is a public repository and "
            "the corpus is customer-proprietary — every file in it carries a "
            "sensitivity label, which the forensics module itself detects and reports.\n\n"
            "Point the engine at the real evidence folder (path from an env var, never "
            "committed) and assert the known-true findings end to end: the double-count "
            "with its incriminating note quoted; the hidden screenshot OCR'd; the "
            "hardcoded rollup cells; the zeroed chassis line; the PDF detected as a "
            "print of its own workbook and excluded from rollups; the same product "
            "matched across two spellings at two prices; the mutually exclusive option "
            "BOMs contributing zero; recurring costs reclassified out of CapEx; the "
            "disputed asset count raised as a DECISION and not a defect; and the owned "
            "hardware raised as an OPPORTUNITY.\n\n"
            "Fixtures and expected values live in this repo only. Nothing from the "
            "corpus — figures, filenames, quoted text, part numbers, names — may ever "
            "appear in an ICDEV commit, test, or commit message."
        ),
        "priority": "high",
    },
    {
        "id": "cncd-ui-01",
        "title": "Upload + intent capture",
        "description": (
            "Drop any mix of PPTX/DOCX/XLSX/PDF/drawio/CSV. Free-text intent box — this "
            "is what the extraction schema, the category taxonomy and the declared-scope "
            "items are all derived FROM, so it is a first-class input, not a note.\n\n"
            "Per-source: role dropdown (bom_claim / inventory_truth / quote / "
            "baseline_architecture / narrative / diagram) and credibility tier, both "
            "AI-PROPOSED with a visible rationale and confidence, both requiring human "
            "confirmation to become binding.\n\n"
            "Reuse the parse -> preview(dry_run) -> commit contract from "
            "compass/tools/pm/scheduler/xlsx_import.py, including its 'real-world "
            "messiness handled explicitly, never silently' warning model."
        ),
        "priority": "high",
    },
    {
        "id": "cncd-ui-02",
        "title": "Review queues — nothing merges silently",
        "description": (
            "Four queues: (1) conflicts — AI-proposed merges with confidence, rationale "
            "and BOTH candidate values side by side; accept / override / reject. (2) "
            "taxonomy — the AI-proposed category tree; rename/merge/split, then APPROVE "
            "and version it (later uploads classify against the approved version, which "
            "is what holds the numbers still across leadership reviews). (3) ground-truth "
            "+ credibility designation. (4) option selection, including the top-level "
            "architecture baseline.\n\n"
            "The losing value is always still visible and cited. Never deleted."
        ),
        "priority": "high",
    },
    {
        "id": "cncd-ui-03",
        "title": "Findings register",
        "description": (
            "Sorted by impact_usd, because that is the only ordering an executive has "
            "ever cared about. Each row: severity, kind (defect / risk / decision / "
            "opportunity), the exact file+sheet+cell citation, the dollar impact, and a "
            "disposition.\n\n"
            "Show the detector column (deterministic vs llm_assisted) — a reader is "
            "entitled to know which claims lean on a model and which are simply "
            "arithmetic.\n\n"
            "Do NOT make this a wall of red. An 'opportunity' finding — hardware already "
            "owned that lets the team start now — is the best news in the package."
        ),
        "priority": "high",
    },
    {
        "id": "cncd-ui-04",
        "title": "Pivot UI",
        "description": (
            "Interactive cross-tab: drag dimensions to rows/cols, measures to values. "
            "Plus the AI-curated views for the stated intent, which become deck slides.\n\n"
            "Flipping the architecture baseline re-filters the BOM and recomputes every "
            "total live — that single control is the sharpest 'different point of view' "
            "this product offers: the same evidence, priced two ways, with the delta "
            "explained."
        ),
        "priority": "medium",
    },
    {
        "id": "cncd-deck-01",
        "title": "Narrative + deck builder UI",
        "description": (
            "Theme picker (9 ICDEV themes), audience mode (technical vs leadership — the "
            "same evidence, two registers), optional customer .pptx shell upload. Live "
            "preview. Export PPTX + XLSX via Cortex.\n\n"
            "Export is BLOCKED on citation_gate: no uncited claim ships. Figures come "
            "from a frozen snapshot; the LLM writes only the prose around them."
        ),
        "priority": "medium",
    },
    {
        "id": "cncd-integ-01",
        "title": "Push the approved BOM into the Compass tracker",
        "description": (
            "Compass's Project Scheduler was itself modelled on a lab-tracker "
            "spreadsheet of exactly this shape (compass/tools/pm/scheduler/), so the "
            "seam already fits.\n\n"
            "POST the approved BOM + wave plan to /api/premium/scheduler/projects "
            "(+ phases + tasks). Committed total -> funded_value. Procurement "
            "lead_time_days -> task durations; the long-lead order is the critical path. "
            "Then pull EVM/actuals back for plan-vs-actual on the deck. Precedent: "
            "prem-ideal-04."
        ),
        "priority": "medium",
    },
    {
        "id": "cncd-integ-02",
        "title": "idea_lab Specialists + Council review of the ask",
        "description": (
            "Three new Specialists. A Specialist is NOT a class in idea_lab — it is a "
            "routing entry pointing at an ICDEV ACE persona. Each one is: a new ICDEV "
            "tools/ace/roles/<id>/{SOUL.md,MEMORY.md,TOOLS.md} PLUS a role_routing entry "
            "with keywords in idea_lab args/icdev_integration.yaml.\n\n"
            "  capital_planner          — budget defensibility, TCO, capex vs opex, reserves\n"
            "  infrastructure_estimator — sizing / power / space sanity\n"
            "  procurement_analyst      — price basis, lead time, vendor risk\n\n"
            "Council: 'is this ask defensible to leadership?' -> 5 fixed perspectives "
            "(Contrarian, First Principles, Expansionist, Outsider, Executor) -> "
            "anonymous peer review -> chairman synthesis. ~11 LLM calls, 90-150s. The "
            "verdict becomes a devil's-advocate slide.\n\n"
            "CRITICAL: specialist_consult.py FAILS CLOSED — if the sanitizer cannot run "
            "it sends NOTHING. idea_lab is cloud-only, so anything crossing that seam "
            "leaves the network boundary. Degrading to 'no consult' costs a third "
            "opinion; degrading to 'send it raw' costs a spill."
        ),
        "priority": "medium",
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    specs = ENGINE + CONCORD
    if args.dry_run:
        for t in specs:
            print(f"{t['id']:<18} [{t.get('status', 'backlog'):<11}] {t['title']}")
        print(f"\n{len(specs)} tasks ({len(ENGINE)} bom-, {len(CONCORD)} cncd-)")
        return 0

    created = create_tasks(specs)
    print(json.dumps({"created": created, "count": len(created)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
