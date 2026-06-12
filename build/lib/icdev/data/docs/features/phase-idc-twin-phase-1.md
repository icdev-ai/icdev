# CUI // SP-CTI
# IDC IaC Twin — Phase 1

**Phase:** IDC IaC Twin Phase 1 (MVP)
**Status:** Design — implementation pending
**Canvas:** IDC — Infrastructure Design Canvas
**Priority:** #2 per digital-twin market brief (after BDC cATO Twin)
**Source brief:** `docs/briefs/digital-twin-market-canvas-implementation-plan.md` (IDC §7)
**Schema artifact:** `docs/features/phase-idc-twin-schema.md`
**Task IDs:** dt-idc-twin-01 through dt-idc-twin-12
**Date:** 2026-04-18

---

## Problem Statement

IDC is ICDEV's most under-built canvas — 14 routes, minimal IaC generation, no twin semantics. The existing IaC emitters (Terraform, Pulumi, Ansible, Helm) generate from a design graph but cannot:

- Import the **live infrastructure state** back into ICDEV
- Detect **drift** between a saved design and what actually runs in a CSP
- Run a **pre-apply compliance gate** before `terraform apply` executes
- Answer queries like *"which AWS GovCloud EC2 instances lack a CUI tag?"*

Commercial competitors (Azure Digital Twins, AWS IoT TwinMaker) target IoT and industrial use cases, not cloud-infra itself. There is **market whitespace** for a cloud-infra twin that is GovCloud-native, air-gap-capable, and classification-aware. This phase delivers the MVP twin primitive that unlocks all four missing capabilities.

---

## Scope — Phase 1 Deliverables

Phase 1 is the **snapshot foundation**: store + query + gate. No UI, no reflex, no cross-CSP migration (those are Phase 2-4).

### Deliverable 1 — `infra_snapshots` Table

Canonical persistence layer for all twin state. Full schema in `docs/features/phase-idc-twin-schema.md`.

Key design decisions:
- **Append-only.** Rows are never updated or deleted. Each import run produces a new `snapshot_id` batch.
- **Composite PK `(snapshot_id, resource_id)`.** Prevents the same resource appearing twice in one batch.
- **`config_json` redaction.** Snapshot writer must strip `password`, `secret_key`, `private_key`, and related fields before insert. Redacted fields replaced with `"[REDACTED]"`.
- **Classification default `CUI`.** All GovCloud resources default to CUI unless the project's classification policy or a resource tag overrides it.
- **10 CSP values** in a CHECK constraint — covers all 6 CSP families plus government variants (`aws_gov`, `azure_gov`, `gcp_assured`, `oci_gov`).
- **Temporal immutability** — `taken_at` is write-once UTC ISO 8601 via `datetime.now(timezone.utc)`.

### Deliverable 2 — Terraform Importer

`tools/infra_canvas/twin/importers/terraform.py`

Reads `terraform show -json` output (piped or file), maps each resource in `.values.root_module.resources[*]` to an IDC canonical `resource_type`, and calls the snapshot writer.

Mapping logic:
- `type` field (e.g., `aws_instance`) → lookup in `TERRAFORM_TYPE_MAP` constant
- Unmapped types stored as `unknown-<original_type>` — never silently dropped
- Resources without an `id` field use a deterministic hash of `(type, name)` as `resource_id`

CLI:
```bash
terraform show -json tfplan.json | python tools/infra_canvas/twin/importers/terraform.py \
  --project-id proj-govcloud-east-1 \
  --classification CUI \
  --json
```

Output:
```json
{
  "snapshot_id": "a3f9c21b4d8e7f0512bc34d9e6a1052c",
  "project_id": "proj-govcloud-east-1",
  "imported": 47,
  "skipped": 2,
  "unmapped": 3
}
```

### Deliverable 3 — IQE Infra Adapter

`tools/iqe/adapters/infra.py`

Registers three IQE collections against `infra_snapshots`:

| Collection | SQL Equivalent | Use Case |
|------------|----------------|----------|
| `infra.resources` | `SELECT * FROM infra_snapshots` | Full table scan with any predicate |
| `infra.snapshots` | `SELECT DISTINCT snapshot_id, project_id, taken_at FROM infra_snapshots` | List available snapshots |
| `infra.latest` | `WHERE taken_at = MAX(taken_at) GROUP BY project_id` | Query the most recent state |

Example IQE queries registered as seed rules in `idc_iqe_rules`:

```
# Rule IDC-001: unencrypted S3 buckets
foreach resource in infra.latest
  where resource_type == 'aws-s3'
    and json_extract(config_json, '$.server_side_encryption_configuration') IS NULL
  select resource.resource_id, resource.region, resource.classification

# Rule IDC-002: CUI resources in non-GovCloud regions
foreach resource in infra.latest
  where classification == 'CUI'
    and csp NOT IN ('aws_gov', 'azure_gov', 'gcp_assured', 'oci_gov', 'onprem')
  select resource.resource_id, resource.csp, resource.region, resource.resource_type

# Rule IDC-003: high-cost resources missing cost-center tag
foreach resource in infra.latest
  where json_extract(tags_json, '$.CostCenter') IS NULL
    and resource_type IN ('aws-ec2', 'az-vm', 'gcp-ce', 'oci-instance')
  select resource.resource_id, resource.csp, resource.region

# Rule IDC-004: missing classification tag
foreach resource in infra.latest
  where json_extract(tags_json, '$.classification') IS NULL
  select resource.resource_id, resource.resource_type, resource.csp

# Rule IDC-005: cross-IL boundary exposure
foreach resource in infra.latest
  where classification == 'SECRET'
    and csp NOT IN ('onprem', 'aws_gov', 'azure_gov')
  select resource.resource_id, resource.csp, resource.region

# Rule IDC-006: containers missing non-root enforcement
foreach resource in infra.latest
  where resource_type IN ('aws-eks', 'az-aks', 'gcp-gke')
    and json_extract(config_json, '$.security_context.run_as_non_root') IS NULL
  select resource.resource_id, resource.csp

# Rule IDC-007: storage resources missing encryption at rest
foreach resource in infra.latest
  where resource_type IN ('aws-ebs', 'az-disk', 'gcp-pd', 'oci-bv')
    and json_extract(config_json, '$.encrypted') != 'true'
  select resource.resource_id, resource.resource_type, resource.region

# Rule IDC-008: resources without owner tag
foreach resource in infra.latest
  where json_extract(tags_json, '$.owner') IS NULL
  select resource.resource_id, resource.resource_type, resource.csp

# Rule IDC-009: public-facing resources in IL5+ projects
foreach resource in infra.latest
  where classification IN ('SECRET', 'TOP_SECRET')
    and json_extract(config_json, '$.public_access_enabled') == 'true'
  select resource.resource_id, resource.resource_type, resource.csp

# Rule IDC-010: IAM roles with wildcard actions
foreach resource in infra.latest
  where resource_type IN ('aws-iam-role', 'az-role-assignment')
    and json_extract(config_json, '$.policy') LIKE '%"Action":"*"%'
  select resource.resource_id, resource.csp, resource.region
```

### Deliverable 4 — Pre-Apply Compliance Gate

`tools/infra_canvas/preapply_gate.py`

Runs before `terraform apply` in CI/CD. Accepts a `terraform plan -json` output, computes the resource delta (add/modify/delete), and evaluates all 10 seed IQE rules against the **planned final state**.

```bash
terraform plan -json > plan.json
python tools/infra_canvas/preapply_gate.py --gate --project-id proj-govcloud-east-1 plan.json
# exits 1 on any violation
```

Output schema:
```json
{
  "gate": "fail",
  "delta": {"add": 5, "modify": 2, "delete": 1},
  "violations": [
    {
      "rule_id": "IDC-002",
      "severity": "critical",
      "resource_id": "aws_instance.web_server",
      "message": "CUI resource planned in commercial (non-GovCloud) region us-east-1"
    }
  ]
}
```

Gate semantics:
- **`critical`** → always blocks (`gate: fail`)
- **`high`** → blocks unless `--warn-on-high` flag set
- **`medium` / `low`** → never blocks; appears in output for observability

---

## Sequence: Phase 1 → Phase 4

| Phase | Capability Added | Precondition |
|-------|-----------------|--------------|
| **Phase 1** (this doc) | `infra_snapshots` table, Terraform importer, 10 IQE seed rules, pre-apply gate | IDC schema stability |
| **Phase 2** | Pulumi + AWS importers, 6h Genesis reflex, drift detection (`idc_drift_events`), Twin Dashboard API (`/infra/twin/*`) | Phase 1 shipped + stable |
| **Phase 3** | Azure + GCP importers, full FIPS 199/200 + STIG evaluators in compliance gate, IL boundary crossing detection | Phase 2 + FIPS/STIG modules stable |
| **Phase 4** | Cross-CSP migration twin (`/infra/twin/simulate`), cost delta, performance delta, Azure Gov ↔ AWS GovCloud parity report | Phase 3 + 6-CSP catalog current |

---

## DB Registration

Per the 8-point new module checklist in `CLAUDE.md`:

| Step | Action | Status |
|------|--------|--------|
| 1 | `tools/manifest/idc-twin.md` — shard created, linked from `tools/manifest.md` | ✅ done (this task) |
| 2 | `docs/reference/commands.md` — snapshot writer CLI entry | ⬜ Phase 2 |
| 3 | `args/security_gates.yaml` — gate: plaintext secret in `config_json` → BLOCK | ⬜ Phase 2 |
| 4 | `tools/mcp/tool_registry.py` + `gap_handlers.py` — register snapshot writer + IQE infra adapter | ⬜ Phase 2 |
| 5 | `.claude/hooks/pre_tool_use.py` — add `infra_snapshots`, `idc_drift_events`, `idc_gate_runs` to `APPEND_ONLY_TABLES` | ⬜ Phase 2 |
| 6 | `tests/conftest.py` — add `infra_snapshots` minimal schema to `MINIMAL_ICDEV_SCHEMA` | ⬜ Phase 2 |
| 7 | `python tools/dx/companion.py --sync --write --json` — sync to all AI platforms | ✅ done (this task) |
| 8 | `python tools/workflow/coherence_checker.py --all --fix --gate` | ✅ done (this task) |

---

## Market Positioning

| Competitor | Gap | ICDEV Advantage |
|------------|-----|----------------|
| Azure Digital Twins | IoT/industrial; no cloud-infra twin; no IL4/5/6 support | GovCloud-native; IL5/IL6 air-gap; cloud-infra only |
| AWS IoT TwinMaker | IoT/industrial; AWS-only; no classification model | Multi-CSP (6 providers); classification-aware snapshots |
| Pulumi / Terraform | Deployment engines, not simulation engines; no twin semantics | Snapshot + query + pre-apply gate over live state |
| RegScale | ATO/compliance twin (BDC space); no infra-resource twin | IDC is infra-resource layer; BDC cATO Twin is the compliance layer |

Key differentiator: **no competitor unifies GovCloud + commercial + on-prem in one classification-aware infra twin.** Azure DT and AWS TwinMaker don't touch IL5/IL6.

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Schema normalization scope creep | Treat `infra_snapshots` schema as a committed artifact; ERB review gate on any change |
| Secret leakage via `config_json` | Snapshot writer must redact before insert; pre_tool_use hook blocks writes to `infra_snapshots` without redaction flag |
| `terraform show -json` format changes | Pin importer to Terraform 1.x JSON schema version; emit warning on unrecognized version field |
| Cross-DB IQE joins are slow at scale | Phase 1 queries are single-table only; cross-DB joins are Phase 3+ and must go through the IQE execution engine fallback |
| LLM-assisted query authoring too early | Deterministic engine must be proven first; LLM query assist is explicitly deferred to Phase 3+ |

---

## Non-Goals (Phase 1)

- No 3D visualization (Azure/AWS IoT TwinMaker slot — not ours)
- No device-level twin, no factory/industrial use cases
- No UI pages (dashboard API is Phase 2)
- No automatic reflex / scheduled import (Phase 2)
- No Pulumi, AWS, Azure, GCP, or OCI importers (Phase 2)
- No OSCAL export from infra state (that's the BDC cATO Twin's domain)
- No LLM-assisted query authoring

---

## Acceptance Criteria (Phase 1 Complete)

- [ ] `infra_snapshots` DDL merged into `tools/infra_canvas/db/init_db.py`
- [ ] Terraform importer round-trips a fixture `terraform show -json` output and produces a verifiable snapshot row
- [ ] All 10 IQE seed rules are stored in `idc_iqe_rules` and produce expected results on fixture data
- [ ] Pre-apply gate returns `gate: fail` for a plan containing a CUI resource in a non-GovCloud region
- [ ] Pre-apply gate returns `gate: pass` for a clean plan
- [ ] `tests/conftest.py` updated with `infra_snapshots` schema
- [ ] `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py` includes `infra_snapshots`, `idc_gate_runs`
- [ ] Ruff + bandit clean on all new files
- [ ] Coherence gate green
