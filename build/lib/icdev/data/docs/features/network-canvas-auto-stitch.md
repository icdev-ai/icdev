# Network Design Canvas — Auto-Stitch (Option A Plan)

**Status:** Plan — in backlog
**Owner:** TBD
**Created:** 2026-04-11
**Related:** `tools/network/blueprint.py` (existing manual `/api/import/stitch`), `/api/conflicts`, `/global/canvas`

---

## Problem

NDC can stitch diagrams today, but only when the user hands in `topology_ids[]` and explicit `interconnects[]`. When an engineer imports six site diagrams, NDC cannot on its own say *"these two topologies share circuit MPLS-12345, fuse them"* — the user has to find the match by eye.

The raw signals for auto-detection are already in the DB (IPAM blocks, circuit IDs, node configs) — the conflict detector at `blueprint.py:6802` already *reads* them. This plan promotes that detection into edge creation, with human-in-the-loop review for ambiguous cases.

## Goal

Deterministic, rule-based auto-detection of cross-topology links with a **preview-then-apply** workflow. No LLM in the matching path; LLM reserved only for optional "explain this match" narrative via the existing `tools/llm/router.py` (model-agnostic — works with whatever model is configured at rollout).

## Non-goals

- ❌ No fuzzy / ML-based matching in v1 — deterministic rules only
- ❌ No modification of existing manual `/api/import/stitch` endpoint
- ❌ No auto-apply on YELLOW tier — human review required
- ❌ No automatic re-stitch on every global canvas load (expensive)

---

## Design

### Matching Rules (v1 scope — R1, R2, R3 only)

| Rule | Signal source | Confidence | Action on match | Risk tier |
|------|---------------|------------|-----------------|-----------|
| **R1** Exact circuit ID | `nc_circuits.circuit_id` appears in ≥2 topologies | 0.95 | Collapse to one circuit edge, preserve both endpoints | GREEN (auto-apply ≥0.7) |
| **R2** Shared IPAM network | Same CIDR in `nc_ipam_blocks` across ≥2 topologies | 0.85 | Create shared-subnet edge between the L3 devices on each side | GREEN |
| **R3** Matching management IP | Same mgmt IP in node `config._mgmt_ip` | 0.90 | Node collapse — same device shown twice | GREEN |

**Deferred to v2:** R4 hostname exact match, R5 BGP peer IP, R6 LLDP/CDP neighbor parsing.

### New Endpoints

```
POST /network/api/import/auto-stitch
```

**Request:**
```json
{
  "topology_ids": ["t1","t2","t3"],      // optional — if omitted, uses all topologies linked to approved/deployed projects
  "rules": ["R1","R2","R3"],              // opt-in per rule; default = all v1 rules
  "min_confidence": 0.8,                  // filter candidates below this
  "mode": "preview",                      // "preview" | "apply"
  "name": "Enterprise Global View"        // required only for apply mode
}
```

**Response (preview mode):**
```json
{
  "mode": "preview",
  "candidates": [
    {
      "rule": "R1",
      "confidence": 0.95,
      "left_topology_id": "t1", "left_topology_name": "Site-A",
      "left_node_id": "r1", "left_node_label": "CORE-R1",
      "right_topology_id": "t2", "right_topology_name": "Site-B",
      "right_node_id": "edge1", "right_node_label": "EDGE-1",
      "evidence": {"circuit_id": "MPLS-12345", "carrier": "AT&T", "bandwidth": "1Gbps"},
      "proposed_action": "merge_edge",
      "proposed_edge": {"source": "t1:r1", "target": "t2:edge1", "label": "MPLS-12345", "protocol": "bgp"}
    }
  ],
  "summary": {"R1": 3, "R2": 1, "R3": 0, "total": 4}
}
```

**Response (apply mode):**
```json
{
  "mode": "apply",
  "topology_id": "new-stitched-uuid",
  "candidates_applied": 4,
  "candidates_skipped_below_threshold": 2,
  "nodes": 28, "edges": 47
}
```

### Module structure

```
tools/network/
├── auto_stitcher.py          NEW — pure-function rule engine
│   ├── Match (dataclass)      fields: rule, confidence, left, right, evidence, proposed_*
│   ├── MatchRule (Protocol)   interface: .detect(topologies) -> list[Match]
│   ├── R1CircuitMatcher
│   ├── R2IpamMatcher
│   ├── R3MgmtIpMatcher
│   ├── detect_matches(topos, rules, min_conf) -> list[Match]
│   └── apply_matches(topos, matches, name) -> stitched_graph
└── blueprint.py              MODIFIED — new route block, reuses existing merge path from /api/import/stitch (lines 2211-2297)
```

### Dependencies (air-gap check required before build)

- **Python stdlib only** for v1: `ipaddress` (IPAM CIDR overlap), `re`, `dataclasses`, `typing`
- **No new PyPI deps** — reuses existing sqlite3, json, flask
- **No LLM dependency in hot path** — deterministic rules
- **Optional LLM for "explain match"** → goes through `tools.llm.router.LLMRouter.invoke(function="narrative_generation")` which is declared as `Scanner` tier in `args/llm_config.yaml` and resolves to whatever local model is configured at rollout (not hardcoded to any specific model family)

### Database changes

**New table:** `nc_auto_stitch_runs` (audit trail, append-only)
```sql
CREATE TABLE nc_auto_stitch_runs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('preview','apply')),
  rules_applied TEXT NOT NULL,            -- JSON array
  min_confidence REAL NOT NULL,
  topology_ids_input TEXT NOT NULL,        -- JSON array
  candidates_found INTEGER NOT NULL,
  candidates_applied INTEGER DEFAULT 0,
  result_topology_id TEXT,                 -- FK → topologies(id), NULL if preview
  evidence_json TEXT NOT NULL              -- full Match list serialized
);
```
- Added to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- Migration `013_nc_auto_stitch_runs`
- Schema added to `tests/conftest.py` MINIMAL_ICDEV_SCHEMA

### UI changes

- **`global.html`** — add "Auto-Detect Links" button next to "Check Conflicts"
- New template `global_auto_stitch.html` — candidate review table with:
  - Checkbox per candidate (pre-checked if confidence ≥ min)
  - Rule badge (R1/R2/R3)
  - Confidence bar
  - Evidence tooltip
  - "Apply Selected" button → POST `mode=apply` with filtered candidate IDs
- Audit trail visible at `/network/audit?event=AUTO_STITCH`

### Security / compliance

- `@nc_login_required` on both routes
- `_audit("AUTO_STITCH_PREVIEW", ...)` and `_audit("AUTO_STITCH_APPLY", ...)` — NIST AU trail
- CUI marking on any generated narrative via `classification_manager.py`
- `args/security_gates.yaml` — add warn gate if >50 candidates returned (suggests dirty input)
- Matches generated topology goes through existing `topology_validator.py`

---

## Implementation checklist

### Phase 1 — Core rule engine (no UI)
- [ ] Create `tools/network/auto_stitcher.py` with `Match` dataclass and `MatchRule` Protocol
- [ ] Implement `R1CircuitMatcher` — group `nc_circuits` by `circuit_id`, emit Match per cross-topology pair
- [ ] Implement `R2IpamMatcher` — group `nc_ipam_blocks` by normalized CIDR (`ipaddress.ip_network`), emit Match per overlap; handle supernet/subnet containment as lower confidence (0.70)
- [ ] Implement `R3MgmtIpMatcher` — scan node.config for mgmt_ip field, group, emit collapse Match
- [ ] Implement `detect_matches(topo_ids, rules, min_conf)` orchestrator
- [ ] Implement `apply_matches(topos, matches, name)` — extend existing stitch merge loop from blueprint.py:2224-2276 to handle node collapse + edge creation
- [ ] Unit tests `tests/test_auto_stitcher.py` — one test class per rule + orchestrator + apply

### Phase 2 — Flask blueprint wiring
- [ ] Migration `tools/db/migrations/013_nc_auto_stitch_runs/up.py` + down.py
- [ ] Add `nc_auto_stitch_runs` to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- [ ] Add table DDL to `tests/conftest.py` MINIMAL_ICDEV_SCHEMA
- [ ] Add `POST /network/api/import/auto-stitch` route in `blueprint.py` next to existing `/api/import/stitch` (line 2211)
- [ ] Reuse existing audit helper `_audit(...)` and `get_connection()`
- [ ] E2E test in `tests/e2e_network_auto_stitch.py` — seed 3 topologies with shared circuit + shared IPAM, call preview, assert candidates, call apply, assert merged topology

### Phase 3 — UI + Dashboard
- [ ] Add "Auto-Detect Links" button to `tools/dashboard/templates/network/global.html` (next to "Check Conflicts")
- [ ] Create `tools/dashboard/templates/network/global_auto_stitch.html` — candidate review modal
- [ ] Add JS handler for preview → table render → apply flow
- [ ] Selenium E2E test for button → modal → apply cycle

### Phase 4 — Docs + registration
- [ ] Update `tools/manifest.md` — add `auto_stitcher` entry under Network Intelligence section
- [ ] Update `goals/network_intelligence.md` — add auto-stitch as D-NII-6
- [ ] Add route to `Pages:` line in `.claude/commands/start.md`
- [ ] Add CLI command (if any) to `docs/reference/commands.md`
- [ ] Create `docs/features/network-canvas-auto-stitch-impl.md` (post-implementation summary)

### Phase 5 — Mandatory validation (per CLAUDE.md)
- [ ] `python -m py_compile` on all new/modified .py files
- [ ] `ruff check` — zero findings
- [ ] `python -m pytest tests/test_auto_stitcher.py tests/e2e_network_auto_stitch.py -q`
- [ ] `python -m bandit -r tools/network/auto_stitcher.py --severity-level medium` — zero medium+
- [ ] `python tools/db/init_icdev_db.py` — verify new migration applies
- [ ] Selenium E2E — `/network/global` button cycle, screenshot to `playwright/screenshots/auto-stitch-*.png`
- [ ] `python tools/dx/companion.py --sync --write --json`
- [ ] `python tools/workflow/coherence_checker.py --all --fix --gate`

---

## LLM Rollout Portability

**Critical constraint:** At rollout, the configured local LLM will not be qwen3.5 — it may be Llama, Mistral, Phi, DeepSeek, or a cloud model.

**How this plan stays portable:**
1. **Zero LLM in the matching hot path** — all rules are exact-match deterministic (circuit_id, CIDR, mgmt_ip). No prompt is executed during detection.
2. **Optional "explain match" narrative uses the router abstraction only** — call `LLMRouter().invoke(function="narrative_generation", prompt=...)` which reads `args/llm_config.yaml`. Whatever model is configured at rollout, it will route there. No model ID is hardcoded in `auto_stitcher.py`.
3. **No model-specific prompt formatting** — any prompt text uses the generic instruction style already used by other Scanner-tier functions; no qwen-specific chat templates, no thinking-mode workarounds.
4. **Fallback path is graceful** — if the configured LLM is unavailable, the UI shows rule + evidence without narrative. The feature still works end-to-end.
5. **No embedding calls** — rule matching does not use vector similarity.

## Risks

| Risk | Mitigation |
|------|------------|
| False positive collapses on R3 (same mgmt IP used on two different devices by mistake) | Require human review when R3 confidence < 0.95; log to audit trail even on auto-apply |
| IPAM overlap between unrelated networks (10.0.0.0/8 in two labs) | R2 lowers confidence when CIDR is RFC1918 /8 or /12; config `args/network_config.yaml` can whitelist "never match these CIDRs" |
| Dirty imports creating 100s of candidates | Warn gate at >50; UI paginates; default to top-20 highest confidence |
| Existing manual stitch users want the new preview UX | Both endpoints stay; manual remains authoritative for ATO-sensitive designs where heuristics are undesired |

## Success criteria

- [ ] Given 2 topologies sharing a circuit ID, preview returns exactly 1 R1 candidate at 0.95 confidence
- [ ] Given 3 topologies with partial IPAM overlap, preview returns N R2 candidates with correct CIDR evidence
- [ ] Apply mode produces a new row in `topologies` and `nc_auto_stitch_runs`
- [ ] All 15 validation checks pass (phase 5)
- [ ] Works when LLM router is configured with any non-qwen model (verified by swapping `args/llm_config.yaml` Scanner tier model and re-running E2E)
- [ ] CUI markings present on all generated artifacts
- [ ] Feature doc committed
