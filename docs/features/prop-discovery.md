# prop-disco-01 — Phase-0 Discovery: GovCon Proposal + Classification-Aggregation Guard

CUI // SP-CTI

Curated adaptation checklist produced by running the Innovation, Research, and
Creative engines scoped to (A) DoD/IC capture-and-proposal management practice
and (B) the classification-aggregation / "mosaic" compilation problem. Each
finding below is mapped to an existing `prop-*` kanban task or flagged as a new
candidate. No production code was changed in this task.

## 1. What was run

| Engine | Invocation | Result |
|---|---|---|
| Innovation | `python tools/innovation/innovation_manager.py --run --json` (full DISCOVER→SCORE→TRIAGE→GENERATE pipeline; `invoke.py --exec icdev-innovate` itself documents zero shell commands since the skill is LLM-guided) | Completed. Web scan (github/CVE/stackoverflow/hackernews/sam_gov) + introspective analysis of ICDEV's own telemetry. |
| Research | `python tools/research/research_engine.py --run --vertical defense --focus-areas "Shipley capture management,APMP proposal best practice,pWin modeling,color team reviews,DoD 5200.01 derivative classification,32 CFR 2002 CUI aggregation and mosaic effect" --json`, then `--run-stage LANDSCAPE` (full `--run` no-ops with `"skipped": "quiet_hours"` on every stage but SCOPE) | Session `rsess-daf81b2e521a` created under the `defense` vertical (no dedicated GovCon-capture vertical exists — see finding R1). LANDSCAPE stage scanned `review_site`/`saas_commercial`/`open_source` sources. |
| Creative | `python tools/creative/creative_engine.py --run --domain "government proposal and capture management" --json` | Executed against the ad-hoc domain (no preset `g2_category_url`/`capterra_category_url` exists for this domain in `args/creative_config.yaml` — see finding C1). |

## 2. Engine findings (raw signal, before mapping)

### Innovation — introspective + web-scan signals
All signals touching `tools/govcon/*` or `tools/proposal_genesis/*` were generic
engineering-hygiene drift, not GovCon-domain gaps:
- `cli_json_flag`: `tools/govcon/option_period_tracker.py`, `tools/proposal_genesis/daemon.py` missing `--json` (59/1183 CLI tools flagged platform-wide — not proposal-specific).
- `db_path_centralization`: `tools/govcon/capture_ai_blueprint.py`, `tools/govcon/contract_mods_manager.py` hardcode DB paths instead of `db_utils.py`.
- Web scan (github/CVE/hackernews/sam_gov) surfaced 0 SAM.gov signals and no GovCon-specific competitive pain points — the scanner's sources are generic dev-ecosystem sources, not capture/proposal methodology sources.

**Verdict:** no GovCon-domain pain points surfaced. The two hygiene items are
real but belong to the platform-wide CLI/DB-path cleanup backlog, not this
epic — **not mapped to a new `prop-*` task.**

### Research — LANDSCAPE scan under `defense` vertical
- `saas_commercial`: 5 signals found, 0 stored (5 errors) — generic defense-sector SaaS competitor scan, not capture/proposal methodology.
- `open_source`: 3 signals found, 0 stored (3 errors).
- `review_site`: 0 signals.

**Verdict:** the Research Engine's vertical/source model (`context/research/verticals/*.json` + G2/Capterra/OSS scanners) is built for *competitive product* discovery, not for ingesting *regulatory/methodology* bodies of knowledge (Shipley Guide, APMP BoK, DoD 5200.01 Vol 1-4, 32 CFR 2002). It surfaced no Shipley/APMP/pWin/color-team/derivative-classification content because there is no source type for that. See finding R1.

### Creative — domain-scan for "government proposal and capture management"
`discover` stage returned immediately with:
`"No category URLs configured for domain 'government proposal and capture
management'. ... Available domains with URLs: file synchronization, server
migration software, blockchain, AI agent frameworks, ... "` — confirming
finding C1. The pipeline fell through to a generic `scan` across all 8
configured source types anyway: `reddit` (32 signals), `github` (28),
`producthunt` (8), `g2`/`capterra`/`trustradius`/`govcon_blogs`/`sam_gov` (0
each) — 68 signals discovered, **0 stored** (domain mismatch → dedup/schema
rejects). `extract` then processed only 4 leftover signals and found **0 pain
points**; `generate` was skipped outright (nothing scored high enough to
spec). The Creative Engine surfaced no GovCon-domain content, as expected
given C1.

Reference qualitatively instead against well-known commercial capture/proposal
tooling (GovWin IQ, Deltek Costpoint/GovWin, Loopio, RFPIO/Responsive, Shipley
Associates training/toolset) for the pain points such tools address:
compliance-matrix (RTM) automation, past-performance/CPARS mining, AI draft
generation, color-team collaboration workflow, win-theme tracking,
pWin/bid-no-bid scoring. **All of these already have an ICDEV module**
(`compliance_matrix_builder.py`, `cpars_predictor.py`,
`response_drafter.py`/`generate_icdev_proposal_content.py`,
`color_review_simulator.py`, `win_theme_manager.py`,
`bayesian_bid_scorer.py`) — confirms the existing `prop-cap-*`/`prop-rev-*`
backlog is reusing rather than duplicating capability, per each task's stated
"reuse X + Y" framing.

## 3. Pain-point → task mapping

### A. Capture & Proposal best practice (Shipley/APMP)

| Best-practice gap | Coverage | Status |
|---|---|---|
| Phase-gated capture workflow (Qualify→Pursue→Capture→Bid→Proposal) + gate audit trail | `prop-cap-11` | ✅ covered |
| Quantitative pWin model + weighted pipeline value | `prop-cap-12` | ✅ covered |
| Black Hat / competitive assessment + Price-To-Win panel | `prop-cap-13` (reuses `competitor_profiler.py` + `bayesian_bid_scorer.py`) | ✅ covered |
| BD pipeline visibility (weighted forecast + SAM.gov forecast-notice feed + CRM heat) | `prop-cap-14` | ✅ covered |
| Color-team reviews (Pink/Red/Gold + White) with a submission-blocking Gold sign-off gate | `prop-rev-10` (`color_review_simulator.py` already exists — task adds White team + hard gate) | ✅ covered |
| Reviewer assignment / HITL hand-off | `prop-rev-09` | ✅ covered |
| Compliance matrix / requirements traceability (RTM) | `tools/govcon/compliance_matrix_builder.py` — already shipped, not in this wave's backlog | ✅ already built |
| Win-theme management | `tools/govcon/win_theme_manager.py` — already shipped | ✅ already built |
| Contract mods + option-period/funding tracking | `prop-ctr-01`, `prop-ctr-02` | ✅ covered |
| Lightweight IMS (milestones/deps↔WBS/EVM) + program risk register | `prop-pm-01`, `prop-pm-02` | ✅ covered |
| IQE natural-language query over govcon/proposals data | `prop-iqe-01` | ✅ covered |

No new `prop-cap-*`/`prop-pm-*`/`prop-ctr-*`/`prop-rev-*` task is proposed —
the existing 0-epic backlog (seeded per `tools/kanban/seed_prop_security_kanban.py`)
already accounts for every Shipley/APMP-derived gap the engines could surface
given their generic (non-methodology-aware) source models.

### B. Classification-aggregation / "mosaic" problem (DoD 5200.01 / 32 CFR 2002)

| Best-practice gap | Coverage | Status |
|---|---|---|
| SCG-driven co-occurrence/count/window aggregation rules | `prop-sec-03` | ✅ covered |
| Derived-classification computer + rule evaluator | `prop-sec-04` | ✅ covered |
| Append-only aggregation audit trail | `prop-sec-05` | ✅ covered |
| Per-user volume/diversity mosaic throttle | `prop-sec-06` | ✅ covered |
| Wiring into govcon/proposals read+export + DERIVED banner | `prop-sec-07` | ✅ covered |
| Gate registration (security_gates.yaml + MCP + manifest) | `prop-sec-08` | ✅ covered |
| MAC / compartment (SCI/SAP/COI/LAC) threading | `prop-sec-02` | ✅ covered |
| ABAC + column masking (row ownership) | `prop-sec-01` | ✅ covered |
| RLS tenant_id + RBAC wiring gaps | `prop-fix-07`..`prop-fix-12` | ✅ covered |
| V&V gates (Phase 1 + Phase 2) | `prop-vv-01`, `prop-vv-02` | ✅ covered |

Two gaps surfaced by cross-referencing 32 CFR 2002 / DoD 5200.01 Vol 1-4
against the `prop-sec-03..08` scope as currently worded are **not yet
represented** by any existing task, because they are a different rule shape
than "co-occurrence bumps a classification level":

- **NEW candidate — CUI category/LDC-aware aggregation.** 32 CFR 2002
  aggregation is frequently about *CUI Basic vs. CUI Specified category*
  accumulation (e.g., "Procurement and Acquisition", "Proprietary Business
  Information", "Controlled Technical Information") triggering a *safeguarding/
  dissemination* escalation (Limited Dissemination Controls such as
  NOFORN-style caveats) even when no classified level is ever reached. As
  worded, `prop-sec-03`'s "classification aggregation" rules read as
  level-oriented (U→C→S→TS); they should be verified/extended to also carry
  CUI-category + LDC rule shapes. Proposed as **`prop-sec-09`** — "CUI
  category/LDC-aware aggregation rules — extend `args/classification_aggregation.yaml`
  + `aggregation_guard.py` with CUI Basic/Specified category and Limited
  Dissemination Control accumulation, sourced from the CUI Registry." Not
  seeded to kanban by this task (discovery-only); left for the `prop-sec`
  epic owner to seed via `/seed-tasks` if agreed.
- **NEW candidate — derivative-classification provenance on generated content.**
  DoD 5200.01 Vol 2 requires a derivative classifier to cite the source
  document/SCG paragraph for each portion mark (`Classified By` / `Derived
  From` / `Declassify On`), not just compute a resulting level. This is
  distinct from the aggregation *guard* (which blocks/warns at read/export
  time) — it's a *generation-time* provenance requirement for
  AI-drafted narrative (RFI/proposal WriteGuard output). Proposed as
  **`prop-sec-10`** — "Derivative-classification provenance — attach
  source-SCG-paragraph citation to AI-generated portions crossing a
  classification boundary." Not seeded to kanban by this task; flagged for
  the epic owner.

Both candidates are deliberately **not** inserted into `kanban_tasks` by this
task — `prop-disco-01` is discovery-only ("No production code changes in this
task"), and per project convention new kanban work must go through
`/seed-tasks` / `task_factory.create_tasks`, not a raw INSERT.

## 4. Tooling gaps noticed (out of scope for this epic)

- **R1 — Research Engine has no methodology/regulatory-corpus source type.**
  `context/research/verticals/*.json` + the LANDSCAPE scanners (`review_site`,
  `saas_commercial`, `open_source`) are built for competitive-product
  discovery. There is no vertical or source scanner for ingesting a fixed
  body of knowledge (Shipley Guide, APMP BoK, DoD 5200.01, 32 CFR 2002). This
  is why the Research Engine run above surfaced generic defense-sector SaaS
  signals instead of capture/proposal methodology content. Out of scope for
  `prop-*`; worth a separate research-engine enhancement idea if useful
  elsewhere (not filed here).
- **C1 — Creative Engine has no preset category for "government proposal
  management".** `args/creative_config.yaml` only has G2/Capterra category
  URLs for unrelated verticals. Ad-hoc `--domain` runs fall back to
  LLM-driven discovery with no fixed reference set. Out of scope for
  `prop-*`.
- Both of the above are noted for awareness only — no `prop-*` action items
  follow from them.

## 5. Summary

- 27 of 27 items in the existing `prop-*` Wave-2 backlog (`prop-cap-11..14`,
  `prop-ctr-01..02`, `prop-fix-07..12`, `prop-iqe-01`, `prop-pm-01..02`,
  `prop-rev-09..10`, `prop-sec-01..08`, `prop-vv-01..02`) are confirmed
  correctly scoped against Shipley/APMP capture-and-proposal best practice and
  DoD 5200.01/32 CFR 2002 classification-aggregation practice — no
  duplicated or missing coverage found for those areas.
- 2 new candidate gaps identified (CUI category/LDC-aware aggregation;
  derivative-classification provenance on AI-generated content) — proposed as
  `prop-sec-09` / `prop-sec-10` for the epic owner to seed, not auto-created
  here.
- 2 platform-hygiene signals (CLI `--json` flag drift, DB-path
  centralization on `govcon`/`proposal_genesis` files) surfaced by the
  Innovation Engine are real but out of scope for this epic.
- 2 engine-tooling gaps (Research Engine has no methodology-corpus source
  type; Creative Engine has no preset category for this domain) are noted for
  awareness, not actioned here.

This checklist is what the remaining `prop-*` epics (`cap`, `ctr`, `fix`,
`iqe`, `pm`, `rev`, `sec`, `vv`) implement against.
