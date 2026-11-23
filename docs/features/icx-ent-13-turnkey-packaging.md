# icx-ent-13: turnkey packaging, ICDEV[IT]'s side (with osx-ent-13)

The enterprise turnkey programme's packaging card spans both repos. ICDEV[OS] (osx-ent-13) adds the umbrella Helm
profile, tenant onboarding as a signed workflow, the CRM template and guide, and the day-0 to first-SPRS guide. ICDEV[IT]
adds two things.

## 1. Academy tracks

FORGE Academy has four tier-2 guided missions, one per track. Each has three lessons, and each lesson is graded by an
item bank (`apps/forge_academy/content/item_banks/<slug>.yaml`).

| Track | Mission | What it teaches |
|---|---|---|
| ISSO (`isso`) | `m-isso-07-turnkey-first-sprs` | register a customer system; the CRM from the platform's template; evidence by the collectors; the first SPRS score |
| ISSM (`issm`) | `m-issm-07-turnkey-review` | review a tenant onboarding and a system registration ("signed as reviewed"); the POA&M rules; the affirmation |
| Assessor (`assessor`, new role) | `m-assessor-01-objective-evidence` | objective-level evidence: met, not met, inherited, N/A, or unverified; freshness; claims; the binder; replay |
| Agent-store reviewer (`store_reviewer`, new role) | `m-store-reviewer-01-certify` | the signed package; the gates run in ICDEV[OS]; certify, install, revoke |

Each mission is a `mission.json` fragment beside its steps, so the catalogue's shared list is never edited. The two new
roles are guided roles in `apps/forge_academy/constants.py`.

The lessons:
- date their facts: "as of November 2026";
- carry **no AWS training or certification content**;
- never call a system compliant, certified or authorised.

`tests/test_icx_ent_13_turnkey.py` holds each of these.

## 2. Retiring the deprecated duplicates

Commit 7b8223a4d marked the top-level generators DEPRECATED. The rule is that a duplicate is **retired** (deleted) only
once its replacement exists and passes, proven by a test that exercises the replacement. Deleting a tool with **no**
replacement is the owner's decision. `args/retired_tools.yaml` records each one:

| Tool | Status | Replacement / why kept |
|---|---|---|
| `tools/marketplace/publish_pipeline.py` | **retired** (deleted) | `tools/store_publisher` (icx-ent-04). This file's test builds a signed package with it that meets ICDEV[OS]'s pinned contract |
| `tools/marketplace/review_queue.py` | **retired** (deleted) | ICDEV[OS]'s `store-review` (osx-ent-11), fed by the store publisher |
| `cmmc_assessor`, `fedramp_assessor` | kept | partial: the evidence collectors export objective evidence, and ICDEV[OS] scores. The `cmmc_assess`/`fedramp_assess` MCP tools and the REST API still call them |
| `ssp_generator`, `poam_generator`, `emass_sync` | kept | nothing in ICDEV replaces them. ICDEV[OS] generates the OSCAL package. MCP tools, icdev_comply, the RMF stage clock and the reflexes still call them |
| `stig_checker` | kept | no replacement. The govlift and sdc checkers are different scopes |
| `cve_triager` | kept | partial (vulnresearch, factory builds). SLA, triage records and blast radius have no replacement, and `cve_passive_watcher` imports it |
| `simulation_engine` | kept | no replacement. ICDEV[OS]'s compliance sandbox is a different job, and studio and the simulation MCP server import it |

The two marketplace MCP tools still refuse with `superseded` and point to the store publisher. Their refusal lives in
`tools/marketplace/superseded.py`.

## Rules held

- The register is reviewed config.
- A kept duplicate keeps its DEPRECATED marker.
- A retired one is gone, with its packaged copy, and nothing imports it.
- Its replacement's test is CI-listed.
