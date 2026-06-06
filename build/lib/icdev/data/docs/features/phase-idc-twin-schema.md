# CUI // SP-CTI
# IDC Twin — `infra_snapshots` Schema

**Phase:** IDC Twin Phase 1 (design artifact)
**Status:** Design — no code yet
**Source brief:** `docs/briefs/digital-twin-market-canvas-implementation-plan.md` (IDC §7)
**Task:** dt-idc-twin-01

---

## Purpose

`infra_snapshots` is the canonical persistence layer for the IDC IaC Twin. Each row is a
point-in-time frozen record of a single cloud resource — its CSP, region, resource type,
provider-native config, classification marking, and tags — anchored to a project and a
snapshot timestamp.

The table feeds:
- **IQE queries** — `foreach resource in infra where ...` translates to SQL over this table
- **Pre-apply compliance gate** — compares a delta snapshot against baseline rows
- **Cross-CSP migration twin** — joins two snapshots (different `csp` values) on `resource_type`
- **6h Genesis reflex** — inserts new snapshot batches; existing rows are immutable

---

## Table Definition (SQLite / PostgreSQL)

```sql
CREATE TABLE IF NOT EXISTS infra_snapshots (
    snapshot_id     TEXT        NOT NULL,
    project_id      TEXT        NOT NULL,
    csp             TEXT        NOT NULL
                        CHECK (csp IN (
                            'aws', 'aws_gov',
                            'azure', 'azure_gov',
                            'gcp', 'gcp_assured',
                            'oci', 'oci_gov',
                            'ibm',
                            'onprem'
                        )),
    region          TEXT        NOT NULL,
    resource_type   TEXT        NOT NULL,
    resource_id     TEXT        NOT NULL,
    config_json     TEXT        NOT NULL DEFAULT '{}',
    classification  TEXT        NOT NULL DEFAULT 'CUI'
                        CHECK (classification IN (
                            'UNCLASSIFIED', 'CUI', 'SECRET', 'TOP_SECRET'
                        )),
    tags_json       TEXT        NOT NULL DEFAULT '{}',
    taken_at        TEXT        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (snapshot_id, resource_id)
);
```

---

## Indexes

```sql
-- Lookup by snapshot batch + CSP (e.g., "all AWS resources in this snapshot")
CREATE INDEX IF NOT EXISTS idx_infra_snapshots_sid_csp
    ON infra_snapshots (snapshot_id, csp);

-- Time-range queries per project (e.g., "all snapshots for project X between T1 and T2")
CREATE INDEX IF NOT EXISTS idx_infra_snapshots_proj_time
    ON infra_snapshots (project_id, taken_at);
```

### Index rationale

| Index | Query pattern | Why |
|-------|---------------|-----|
| `(snapshot_id, csp)` | "Give me all Azure resources in snapshot `abc123`" | Batch retrieval of a cross-CSP snapshot by provider — used by IQE cross-CSP join and the pre-apply compliance gate |
| `(project_id, taken_at)` | "Latest snapshot for project `proj-42`, last 24h" | Time-windowed history for drift detection; `taken_at` is monotonically increasing per project so the index is selective |

A composite primary key on `(snapshot_id, resource_id)` enforces uniqueness: the same
resource cannot appear twice in the same snapshot batch.

---

## Column Definitions

### `snapshot_id` — TEXT, NOT NULL (part of PK)

UUIDv4 hex (32 chars). Groups all rows produced in a single ingestion run. Every call to
the snapshot writer generates a fresh `snapshot_id`; all resources written in that run share it.

- **Source:** `uuid.uuid4().hex` at writer entry
- **Cardinality:** one value per run × N resources
- **Example:** `"a3f9c21b4d8e7f0512bc34d9e6a1052c"`

---

### `project_id` — TEXT, NOT NULL

ICDEV project identifier. Foreign key (logical) to the `projects` table in `icdev.db`.

- **Example:** `"proj-govcloud-east-1"`
- **Constraint:** non-empty; validated at write time by the snapshot writer

---

### `csp` — TEXT, NOT NULL

Cloud service provider identifier. Constrained to the 10 recognized values covering all
6 CSP families (commercial + government variants):

| Value | Meaning |
|-------|---------|
| `aws` | AWS Commercial (us-east-1, us-west-2, …) |
| `aws_gov` | AWS GovCloud (us-gov-west-1, us-gov-east-1) |
| `azure` | Azure Commercial |
| `azure_gov` | Azure Government (USGov Virginia, USGov Arizona) |
| `gcp` | GCP Commercial |
| `gcp_assured` | GCP Assured Workloads (FedRAMP/IL4/IL5 boundary) |
| `oci` | OCI Commercial |
| `oci_gov` | OCI Government Cloud |
| `ibm` | IBM Cloud |
| `onprem` | On-premises / air-gapped |

The CHECK constraint derives from `tools/infra_canvas/constants.py` key set plus
government variants. Any new CSP family must update both the constant and the constraint.

---

### `region` — TEXT, NOT NULL

Provider-native region string. Format varies by CSP; the snapshot writer normalizes to
the canonical provider format.

| CSP | Example values |
|-----|----------------|
| `aws` / `aws_gov` | `us-east-1`, `us-gov-west-1` |
| `azure` / `azure_gov` | `eastus`, `usgovvirginia` |
| `gcp` / `gcp_assured` | `us-central1`, `us-east4` |
| `oci` / `oci_gov` | `us-ashburn-1`, `us-phoenix-1` |
| `ibm` | `us-south`, `eu-de` |
| `onprem` | `datacenter-primary`, `datacenter-dr` (freeform) |

---

### `resource_type` — TEXT, NOT NULL

Maps to the `type` field in `tools/infra_canvas/constants.py :: INFRA_OBJECTS`. Identifies
the service category (compute, storage, container, database, etc.) and CSP.

- **Examples:** `"aws-ec2"`, `"az-aks"`, `"gcp-gcs"`, `"oci-adb"`, `"iac-terraform"`
- **Importer behavior:** `terraform show -json` resource `type` field is mapped to the
  nearest IDC canonical type; unmapped types store the raw provider type prefixed with
  `unknown-`.
- **IQE usage:** `foreach resource in infra where resource_type == 'aws-eks'`

---

### `resource_id` — TEXT, NOT NULL (part of PK)

Provider-native resource identifier. Opaque string; format depends on CSP.

| CSP | Format |
|-----|--------|
| AWS | ARN (`arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123def`) |
| Azure | Resource ID (`/subscriptions/{sub}/resourceGroups/{rg}/providers/{type}/{name}`) |
| GCP | Full resource name (`//compute.googleapis.com/projects/{proj}/zones/{zone}/instances/{name}`) |
| OCI | OCID (`ocid1.instance.oc1.iad.abc…`) |
| IBM | CRN (`crn:v1:bluemix:public:is:us-south-1:a/…::instance:…`) |
| On-prem | FQDN or UUID assigned by the snapshot writer |

---

### `config_json` — TEXT, NOT NULL DEFAULT `'{}'`

Full provider-native configuration blob for the resource, stored as JSON text. This is
the raw payload from the import source (Terraform state, AWS Resource Groups Tagging API,
`pulumi stack export`, etc.) — not normalized.

- **Max expected size:** ~64 KB per resource (Terraform state entries rarely exceed this)
- **Querying:** IQE uses `json_extract(config_json, '$.field')` for SQLite; `config_json->>'field'`
  for PostgreSQL. The compliance gate reads specific keys via the extract helper.
- **Sensitive data:** Secrets must never appear in `config_json`. The snapshot writer
  must redact known secret fields (`password`, `secret_key`, `private_key`, etc.) before
  insert. Redacted fields are replaced with `"[REDACTED]"`.

---

### `classification` — TEXT, NOT NULL DEFAULT `'CUI'`

ICDEV classification marking for this resource. Determines which users and systems may
read the snapshot row. Constrained to four levels:

| Value | Impact Level |
|-------|-------------|
| `UNCLASSIFIED` | IL2 / public |
| `CUI` | IL4 / IL5 (default) |
| `SECRET` | IL6 / SIPR |
| `TOP_SECRET` | Above IL6 |

- **Default:** `CUI` — all GovCloud resources default to CUI unless overridden by the
  project's classification policy.
- **Source:** derived from the project's `classification` field in `icdev.db` at snapshot
  write time; may be overridden per-resource by a tag (`classification=SECRET`).

---

### `tags_json` — TEXT, NOT NULL DEFAULT `'{}'`

Provider-native tags for the resource, stored as a flat JSON object `{"key": "value", …}`.
Populated from the import source; all tag values are strings.

- **Key uses:** `environment`, `owner`, `cost-center`, `classification` override,
  IQE tag-filter queries
- **Example:** `{"Environment": "prod", "CostCenter": "GovOps-42", "classification": "CUI"}`
- **Querying:** `json_extract(tags_json, '$.Environment')` for SQLite

---

### `taken_at` — TEXT, NOT NULL DEFAULT `CURRENT_TIMESTAMP`

ISO 8601 UTC timestamp of when the snapshot was taken. Format: `YYYY-MM-DDTHH:MM:SS`.

- **Timezone:** always UTC; the snapshot writer uses `datetime.now(timezone.utc).isoformat()`
- **Immutability:** rows are never updated after insert — `taken_at` is write-once
- **Retention:** no automatic expiry; retention policy TBD in Phase 2

---

## Constraints Summary

| Constraint | Type | Rule |
|------------|------|------|
| `(snapshot_id, resource_id)` | PRIMARY KEY | One row per resource per snapshot batch |
| `csp` | CHECK | Must be one of the 10 recognized CSP values |
| `classification` | CHECK | Must be one of 4 levels |
| `config_json` | Convention | No plaintext secrets; redact before insert |
| `taken_at` | Convention | UTC ISO 8601; write-once |

---

## Usage Patterns

### Pattern 1 — Latest snapshot for a project

```sql
SELECT *
FROM infra_snapshots
WHERE project_id = 'proj-govcloud-east-1'
  AND taken_at = (
      SELECT MAX(taken_at)
      FROM infra_snapshots
      WHERE project_id = 'proj-govcloud-east-1'
  );
```

### Pattern 2 — IQE: all unencrypted S3 buckets in a snapshot

```sql
SELECT resource_id, config_json
FROM infra_snapshots
WHERE snapshot_id = :sid
  AND resource_type = 'aws-s3'
  AND json_extract(config_json, '$.server_side_encryption_configuration') IS NULL;
```

### Pattern 3 — Cross-CSP comparison (migration twin)

```sql
SELECT
    a.resource_id  AS aws_resource,
    b.resource_id  AS azure_resource,
    a.resource_type
FROM infra_snapshots a
JOIN infra_snapshots b
  ON a.resource_type = b.resource_type   -- logical equivalence via CSP_EQUIVALENCE map
  AND a.snapshot_id  = :aws_sid
  AND b.snapshot_id  = :azure_sid
WHERE a.csp IN ('aws', 'aws_gov')
  AND b.csp IN ('azure', 'azure_gov');
```

### Pattern 4 — Resources added between two snapshots (drift detection)

```sql
SELECT resource_id, resource_type, csp, region
FROM infra_snapshots
WHERE project_id = :proj
  AND snapshot_id = :new_sid

EXCEPT

SELECT resource_id, resource_type, csp, region
FROM infra_snapshots
WHERE project_id = :proj
  AND snapshot_id = :old_sid;
```

---

## Import Sources (Phase 2)

When Phase 2 implementation begins, the snapshot writer will populate this table from:

| Source | Command | Notes |
|--------|---------|-------|
| Terraform | `terraform show -json` | State file; maps `.values.root_module.resources[*]` |
| Pulumi | `pulumi stack export --json` | Stack checkpoint; maps `.deployment.resources[*]` |
| AWS | `aws resourcegroupstaggingapi get-resources` | Tag-based discovery across services |
| Azure | `az resource list --output json` | Subscription-wide resource list |
| GCP | `gcloud asset search-all-resources --format=json` | Org/project asset inventory |

---

## Relationship to Existing IDC Tables

```
infra_designs  ─── (project_id) ───► infra_snapshots
                                          │
                         (snapshot_id) ───┤
                                          ▼
                              idc_assessments (future: snapshot_id FK)
```

`infra_snapshots` is additive — it does not replace any existing IDC table. The snapshot
importer reads from `infra_designs` (the design graph) or from external IaC sources, and
writes into `infra_snapshots`. The compliance gate reads from `infra_snapshots` and writes
findings into `idc_assessments`.

---

## Registration Checklist (Phase 2 — before code lands)

- [ ] `tools/manifest/infra-canvas.md` — add snapshot writer tool entry
- [ ] `tools/infra_canvas/db/init_db.py` — append `infra_snapshots` DDL + indexes to `SCHEMA`
- [ ] `tests/conftest.py` — add `infra_snapshots` minimal schema to `MINIMAL_ICDEV_SCHEMA`
- [ ] `args/security_gates.yaml` — add gate: plaintext secret in `config_json` → BLOCK
- [ ] `.claude/hooks/pre_tool_use.py` — table is append-only; add to `APPEND_ONLY_TABLES`
- [ ] `tools/mcp/tool_registry.py` — register snapshot writer + IQE query handler
- [ ] `docs/reference/databases.md` — add `infra_snapshots` to IDC table list
- [ ] `docs/reference/commands.md` — add snapshot writer CLI entry
