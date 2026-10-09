# CUI // SP-CTI

# DMX — Document Modernization Extension

| Field | Value |
|-------|-------|
| Phase | DMX — DocMod Extension (Phase 73) |
| Module | `tools/doc_modernization/`, `tools/document_intelligence/` |
| Surface | `/standards-catalog` + DIC modernization page (no NEW dashboard page) |
| Kanban project | `dmx-` (behind `dmx-gate-00`, manual-gated) |
| Status | Complete — closeout `dmx-xcut-01` |
| Date | 2026-07-24 |
| Author | ICDEV™ Architect Agent |

---

## 1. Summary

DMX extends the Document Modernization Engine (docmod) built by the earlier
`docmod-` project. The guiding decision (ADR **D367**) is **extend, don't
redesign**: every new capability reuses the existing scanner, the append-only
`docmod_findings` supersede chain, the `drift_bridge` → ACOIC compliance sink,
and the shared `tools.quality` grounding modules. No parallel finding store, no
new compliance path, and no new LLM verdict surface were introduced.

DMX added **no new dashboard page** — the one UI-bearing candidate
(`dmx-claims-02`, a claims panel) is parked pending human sign-off (see §8), so
there is no new route and no Playwright E2E for this phase.

## 2. New domain packs

Both packs are pure-YAML rulebooks on the shared `RulebookPack` — a new
rules-driven domain needs no Python (ADR **D368**). Both ship
`enabled: false` pending real-corpus validation; org/role-specific rules ship
commented (this repo has no org catalog).

- **SOP Workflows** (`args/docmod/packs/sop_workflows.yaml`,
  `rulebook_sop_workflows.yaml`, 18 rules, PR #639) — TOOL/COMMAND/PLATFORM
  drift in procedures: retired CI (Travis), container/k8s tooling
  (docker-compose v1, `helm init`, PodSecurityPolicy, gcr.io), legacy build
  commands (python2, easy_install, nosetests, apt-key), host commands
  (ifconfig, netstat, wmic), shut-down platforms (HipChat, Skype for Business,
  Bitbucket Server). Entity type `tool_reference`.
- **Architecture Patterns** (`args/docmod/packs/architecture_patterns.yaml`,
  `rulebook_architecture_patterns.yaml`, 7 rules, PR #642) — obsolete→modern
  architecture-pattern drift in design docs: Hystrix→Resilience4j/mesh,
  CORBA/DCOM→gRPC/REST, SOAP/WSDL→REST/gRPC, ESB→API-gateway+event-streaming,
  hand-rolled crypto→vetted library, in-process session state→stateless,
  monolithic three-tier→microservices. New entity type `architecture_pattern`;
  NIST SA-8/PL-8. Each rule narrowed with a false-positive guard (plain
  "three-tier"/"session affinity"/non-crypto "custom" stay clean). On-prem /
  air-gap is a supported posture, deliberately NOT flagged.

## 3. Temporal validity (`temporal.py`, PR #643)

PROACTIVE standards-validity checking that complements the REACTIVE
supersession packs (ADR **D369**). A rulebook rule may carry OPTIONAL ISO-8601
`effective_date` / `sunset_date` / `review_by` fields; the evaluator flags a
cited standard as its sunset approaches (within `sunset_warning_window_days`,
default 90 → `expiring_reference`, medium) or passes (`stale_reference`, high),
independent of any supersession map. Deterministic (TRUST rule 1; timezone-aware
UTC clock, never naive; no LLM). The temporal PHASE is encoded in `finding_type`
so the scanner's `sha256(doc|pack|entity|type)` dedupe stays phase-aware. A rule
without date fields behaves exactly as before.

## 4. Cross-reference cascade (`cross_reference_tracker.py`, PR #644)

Inter-document cross-reference tracking (`dic_cross_references` table). Regex
extraction of explicit textual references ("see Section 3 of the Backup SOP",
"per <Title> §N") — complements `consistency_checker` (which detects KG concept
overlap). Pipeline: **extract** → rows; **resolve** → match `target_doc_ref` to
a known DIC document, filling `target_doc_id` (unresolved → `dangling_reference`
finding); **cascade** → on a version approval whose changed sections intersect
an inbound reference, raise a `cross_reference_cascade` finding on each citing
document. Findings flow through `docmod_findings` → `drift_bridge` → ACOIC
unchanged. Deterministic, air-gap safe, HITL-preserving (findings only, never
edits). Registered in the DIC manifest shard.

## 5. Link-rot egress guard (`link_check.py`, PR #645)

Egress-safe cited-URL link-rot findings (`docmod_link_checks` cache). Extracts
the URLs a document cites and records deterministic `broken`/`moved`/`changed`
findings on the same supersede + DocDrift dedup path (no new sink, no LLM
verdict). The SSRF **egress guard** is load-bearing (ADR **D371**): https-only;
each hostname is resolved to its IP(s) FIRST, then EVERY resolved address is
checked against loopback / RFC1918 / IPv6 ULA / link-local (incl. the cloud
instance-metadata address) / multicast / reserved before any socket opens —
post-resolution checking defeats DNS-rebinding. Operator allow/denylist is
honored (denylist wins); redirects are never auto-followed (each hop is
re-resolved and re-checked, depth-capped); the per-sweep URL count is capped. An
air-gap-unreachable URL is never scored as "rotted". Landed a decision in
`docs/security/sandbox-coverage.md` (Gap 32).

## 6. Freshness owner notifications (`freshness_notifier.py`, PR #646)

Fires an owner/steward notification (via the shared `tools/notifications/
gateway.py`) the first time a document CROSSES into `aging`/`stale` freshness.
The freshness SCORING (`freshness_engine._score_doc`) stays pure; the notifier
is the side-effecting boundary. Crossing-only (a document already `stale` does
not re-alert), cooldown/de-dup via the MUTABLE `dic_doc_freshness.last_notified_at`
column, notify-only (never edits a document), air-gap safe (unreachable channel
logs + skips, leaving `last_notified_at` unchanged for a later retry), and
config-gated (`freshness_notifications`, DEFAULT OFF).

## 7. NIST / CVE feed wiring (`nist_pubs_sync.py`, `cve_bridge.py`, PR #647)

External-source refresh is **scheduled pull, not webhook/push** (ADR **D370**) —
the dashboard is not internet-reachable, so inbound webhooks cannot be delivered
in the target isolated topologies.

- **NIST Publications Sync** — a mutable revision cache (`docmod_nist_pubs`)
  structurally cloned from `eol_products_sync`. https-only, TLS-verified,
  cadence-gated pull, with a YAML seed and an `import_dataset()` air-gap bundle.
  The `policy_refs` pack flags a document citing an OLDER revision than the cache
  records (deterministic numeric compare). Swallows every network/parse error —
  the sweep never fails because egress is down.

  **Live source corrected (cef-fnd-02).** This shipped pulling the CSRC RSS feed
  at `/CSRC/media/feeds/rss/publications.xml`. That feed is **retired**: measured
  2026-08-17 it returns HTTP 404 while `csrc.nist.gov` answers 200, so the live
  pull had never landed a single row and every cached row was `source='seed'`.
  Two changes:

  1. The live source is now CSRC's spreadsheet of current draft + final
     publications (`NIST-Cybersecurity-Publications.xlsx`, parsed with the
     already-declared `openpyxl`). It carries a `Stage` column, so the sync keeps
     **Final rows only**. The one publications feed CSRC still advertises,
     `drafts-open-for-comment.xml`, is deliberately NOT used: a draft does not
     supersede a final publication, and caching a draft revision would flag every
     document citing the current final revision — a manufactured finding. A
     publication with no revision in its number (e.g. SP 800-207) is not cached
     at all rather than being assigned an invented "Rev 1". `parse_feed()` is
     retained for operators who configure a working RSS/Atom mirror.
  2. **A dead URL and a dead network are no longer the same observable.**
     `_fetch` returns a status token — `url_dead_http_404` (the server answered
     and refused: a misconfiguration, logged at warning) is distinct from
     `unreachable` (no egress: a normal air-gap state, logged at info), and from
     `not_configured` / `refused_non_https` / `oversized`. Reporting the first as
     `"feed unavailable (offline?)"` is precisely how a retired feed stayed
     broken while every sweep reported a benign-looking skip. `sync()` surfaces
     the per-source tokens in `sources`.

  Measured after the fix: 54 publications land from the live catalog with
  `source='nist.gov'`, and `policy_refs` emits a real finding (SP 800-171 Rev 1
  superseded by Rev 3, evidence dated 05/14/2024). A bare
  `python -m tools.doc_modernization.nist_pubs_sync` now runs `refresh()`, which
  falls back to the static seed when the live pull lands nothing — an empty cache
  makes `policy_refs` answer `unknown` forever — and reports which path supplied
  the rows so the seed fallback is never presented as a live pull.
- **CVE Bridge** — adds NO poller; it REUSES the existing supply-chain
  `cve_triage` store. For each document it re-runs the network_hardware +
  software extractors to learn cited products, matches them against
  `cve_triage.package_name`, and routes each hit through the SAME sink as
  `drift_bridge` (`acoic.handle_drift`) — a HITL regen/triage item plus a
  NIST RA-5 / SI-2 re-map. It emits to ACOIC rather than inserting a
  `docmod_findings` row so the scanner's finding-ownership never auto-resolves
  CVE evidence. Deterministic, idempotent (`dedup_key`), air-gap safe (a
  missing/empty store → zero emissions).

## 8. Regeneration quality gate (`regen_quality_gate.py`, PR #648)

A deterministic gate on `regen_orchestrator.regenerate_document`, evaluated at
the moment a regenerated version would enter `pending_review`, that BLOCKS a
defective regeneration unless a human forces the override (ADR **D372**):
(1) **citation re-validation** — every `[source: …]` tag re-checked against the
CURRENT evidence source ids via the shared `tools.quality.citation_grounding`
(a hallucinated citation, or a non-abstained section with none, blocks);
(2) **internal-consistency** — unresolved `[PLACEHOLDER]` tokens block,
cross-section numeric conflicts surfaced, reusing `content_grounding` /
`consistency_checker`; (3) **claim-preservation diff** of the old-APPROVED vs
the new draft (informational, never blocks). Pure regex/difflib/dict — no LLM
gates promotion; READ-only (never mutates `dic_versions` / `dic_edit_history`;
status persistence + force-override audit live in `regen_orchestrator`).

## 9. Spike outcomes

Two design spikes were run to size the hardest gaps before committing code:

- **Semantic claim tracking** (`docs/design/dmx-claims-tracking-spike.md`,
  PR #640) — **GO, conditional / single-domain / human-approval-gated**
  (ADR **D373**). The LLM proposes claim *structure* only; claim *validity*
  comes solely from a deterministic `docmod_findings` verdict; extracted claims
  land `pending_review` for HITL promotion; sub-0.7-confidence candidates never
  surface. Implementation (`dmx-claims-02`) is **PARKED** behind `dmx-gate-00`
  until a human signs off the spike — no code, migration, tables, or
  claims-panel UI ship under DMX.
- **Living-document mode** (`docs/design/dmx-living-document-spike.md`,
  PR #641) — **adopt-later** (ADR **D374**). Reuse the DIC Tech Writer
  workspace, the existing `dic_suggestions` queue, and the existing approve gate
  plus ONE thin *batch-approve → single new version* action. A parallel
  "baseline/redlines/batch-approval" data model was rejected (YAGNI — the
  baseline is the latest approved `dic_versions`, the redlines are pending
  `dic_suggestions`, the audit is append-only `dic_suggestion_decisions`).

## 10. Data model / RLS / append-only

Three new DMX tables, all carrying `tenant_id` + `classification` (RLS) and all
MUTABLE — correctly NOT in `APPEND_ONLY_TABLES`:

| Table | Migration / DDL | RLS cols | Mutability |
|-------|-----------------|----------|------------|
| `docmod_link_checks` | `link_check.py` init DDL | `tenant_id`, `classification` (DEFAULT 'CUI') | MUTABLE — upserted per sweep |
| `docmod_nist_pubs` | migration `20260817024050` (legacy `282_docmod_nist_pubs.sql` retained) | `tenant_id`, `classification` (DEFAULT 'CUI') | MUTABLE — revision cache upsert |
| `dic_cross_references` | `document_intelligence/db/init_db.py` | `tenant_id`, `classification` (DEFAULT 'CUI') | MUTABLE — resolution UPDATEs `target_doc_id` |

The `dic_doc_freshness.last_notified_at` column (cooldown store) is also
MUTABLE (updated in place). Only `docmod_findings`, `docmod_scan_runs`, and
`docmod_catalog_audit` remain append-only. All DMX findings still land on the
append-only `docmod_findings` supersede chain.

## 11. Invariants preserved

All five DOCMOD invariants hold: deterministic TRUST verdicts (no LLM in any
currency/validity evaluation), append-only `docmod_findings`, HITL
`pending_review` gating, air-gap safety (every outbound path degrades to zero
findings and never raises), and RLS `tenant_id`/`classification` on every new
table. This closeout (`dmx-xcut-01`) is docs + registration + coherence only and
changes no runtime behavior.
