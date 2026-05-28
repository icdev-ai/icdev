# CUI // SP-CTI
# Federated Query via IQE + DataBridge + DDC Data Mesh

> **Status:** Phase I ✅ COMPLETE | Phase II ✅ COMPLETE | Phase III ✅ COMPLETE | Phase IV ✅ COMPLETE | Phase V — PLANNED

Extends IQE with an `ext.*` namespace that routes queries through DataBridge
connectors, wires DDC lineage tagging, and enforces Data Mesh OPA governance
for external data sources.

---

## Problem Statement

ICDEV's DDC data mesh manages domain ownership, contracts, and lineage for
internal designs. DataBridge holds live connectors to 25+ external systems
(ServiceNow, Splunk, Tenable, GDELT, etc.). Today these two systems do not
talk: there is no way to query external sources through IQE, no lineage trace
back to external provenance, and no OPA governance gate on cross-domain
federated reads.

**Goal:** Let any IQE query target an external system through the `ext.*`
namespace, tag results with DDC lineage, and enforce data mesh governance —
reusing all existing infrastructure.

---

## Architecture

```
User NL question
        │
        ▼
nl_to_iqe()               (existing — pattern + Ollama LLM)
        │
        ▼
IQE Parser                (existing — hand-rolled, zero deps)
        │
        ▼
IQE Executor
  ├── union() / join()    (Phase II — built-in multi-source fan-out)
  ├── ext.* adapter       (Phase I  — thin DataBridge wrappers)
  ├── ddc.* adapter       (existing — lineage, classifications)
  └── all canvas adapters (existing — 31 adapters)
        │
        ▼
filter + project           (existing — Python predicate eval)
        │
        ▼
DDC lineage tag            (Phase III — dd_lineage rows written)
        │
        ▼
Data Mesh OPA gate         (Phase IV — governance_engine.check_access)
        │
        ▼
IQE widget response        (existing — dashboard widget unchanged)
```

---

## Phase I — DataBridge IQE Adapter Foundation ✅ COMPLETE

**Objective:** Register `ext.*` collections in IQE so external sources are
immediately queryable.

### Delivered

| File | Change |
|------|--------|
| `tools/iqe/adapters/ext_databridge.py` | NEW — 12 `ext.*` collections |
| `icdev/tools/iqe/adapters/ext_databridge.py` | NEW — mirrored to icdev package |
| `tools/dashboard/app.py` | MOD — `"ext"` entry in `_CANVAS_MAP` |
| `context/iqe/queries/ext/01–08.iqe` | NEW — 8 seed queries |
| `tests/test_iqe_ext_databridge.py` | NEW — 8 tests, all pass |
| `tools/manifest/databridge.md` | MOD — IQE adapter entry |

### Collections Registered (12)

| Collection | Source |
|---|---|
| `ext.databridge.connectors` | Registry listing (no creds needed) |
| `ext.servicenow.incidents` | ServiceNow ITSM |
| `ext.servicenow.change_requests` | ServiceNow ITSM |
| `ext.servicenow.applications` | ServiceNow CMDB |
| `ext.servicenow.servers` | ServiceNow CMDB |
| `ext.splunk.events` | Splunk |
| `ext.splunk.devices` | Splunk |
| `ext.splunk.alerts` | Splunk |
| `ext.tenable.scans` | Tenable |
| `ext.tenable.assets` | Tenable |
| `ext.tenable.vulnerabilities` | Tenable |
| `ext.gdelt.events` | GDELT |

---

## Phase II — Multi-Source Fan-Out Executor ✅ COMPLETE

**Objective:** Enable a single IQE query to fetch from multiple collections
in parallel and merge results via `union()` and `join()` built-ins.

### Delivered

| File | Change |
|------|--------|
| `tools/iqe/executor.py` | `union()` + `join()` in `_fetch()`; `_fetch_union()`, `_fetch_join()`, `_python_join()`, `_duckdb_join()` added |
| `context/iqe/queries/ext/09_union_incidents_and_alerts.iqe` | NEW |
| `context/iqe/queries/ext/10_join_assets_and_vulnerabilities.iqe` | NEW |
| `tests/test_iqe_executor_fanout.py` | NEW — 12 tests, all pass |

### Syntax

```iqe
# Union — parallel fetch, concatenated rows, order-preserving
foreach x in union("ext.servicenow.incidents", "ext.splunk.alerts")
  where x.priority == "1"
  select x.number, x.event_id, x.hostname

# Join — parallel fetch, inner join on shared key field
foreach r in join("ext.tenable.assets", "ext.tenable.vulnerabilities", "asset_id")
  where r.severity == "critical"
  select r.fqdn, r.severity, r.plugin_name
```

### Implementation Notes
- **No grammar changes** — `union()`/`join()` parse as existing `CollectionCall` AST nodes
- `union()` — `ThreadPoolExecutor` (max 8 workers), results merged in declaration order
- `join()` — parallel 2-worker fetch; DuckDB join (type coercion) with `_python_join` fallback
- DuckDB results post-processed: left-row values overlay DuckDB output (left-wins on conflict)
- Both built-ins work on **any** registered collection, not just `ext.*`

---

## Phase III — DDC Lineage Tagging for External Queries

**Objective:** Write a `dd_lineage` edge for every `ext.*` query execution,
recording where the data came from and its classification.

### Changes

| File | Change |
|------|--------|
| `tools/iqe/adapters/ext_databridge.py` | MOD — emit lineage after fetch |
| `tools/data_canvas/lineage.py` | MOD — add `record_external_fetch()` helper |
| `tests/test_iqe_ext_lineage.py` | NEW — lineage write tests |

### Design

After `_safe_fetch()` returns rows, call:

```python
record_external_fetch(
    source_node_id=f"ext.{connector_name}.{table}",
    target_node_id="iqe.query.result",
    lineage_type="col-passthrough",
    classification=_classify_from_connector(connector_name),
    row_count=len(rows),
)
```

Classification defaults to `CUI // SP-CTI`; overrideable from connector's
`db_connections` config record.

### Acceptance Criteria
- Each `ext.*` fetch writes exactly one `dd_lineage` row
- Lineage rows queryable via `data.lineage.edges` collection
- Classification propagates from connector config → lineage edge

---

## Phase IV — Data Mesh OPA Governance Gate

**Objective:** Before returning results from an `ext.*` collection, evaluate
OPA governance policy to enforce domain ownership and access control.

### Changes

| File | Change |
|------|--------|
| `tools/iqe/adapters/ext_databridge.py` | MOD — call `check_access` before return |
| `tools/data_canvas/data_mesh/governance_engine.py` | MOD — add `ext_resource` action type |
| `tests/test_iqe_ext_governance.py` | NEW — OPA gate tests |

### Design

```python
from tools.data_canvas.data_mesh.governance_engine import check_access

decision = check_access(
    user=user,
    resource=f"ext.{connector_name}.{table}",
    action="read",
)
if not decision.get("allowed", True):
    return []  # OPA deny → empty, audit written by check_access()
```

Local mode (OPA URL blank) passes all queries through.

### Acceptance Criteria
- OPA deny → empty list returned, no rows leaked
- Audit entry written to `dm_policy_audit_log`
- Local mode (OPA URL blank) is a pass-through

---

## Phase V — NL Enhancement + Dashboard Widget

**Objective:** Make `ext.*` and `union()`/`join()` queries discoverable via
NL translation and surface them on the DDC mesh pages.

### Changes

| File | Change |
|------|--------|
| `tools/iqe/nl_to_iqe.py` | MOD — `ext.*` namespace patterns; union/join templates |
| `tools/dashboard/templates/data_canvas/mesh.html` | MOD — IQE widget |
| `tools/dashboard/templates/data_canvas/products.html` | MOD — IQE widget |
| `context/iqe/queries/ext/` | MOD — expand to 15 seed queries |
| `docs/features/phase-fed-query-iqe.md` | MOD — mark phases complete |

### NL Patterns Added

| User says | Translated IQE |
|---|---|
| "show servicenow incidents" | `foreach i in ext.servicenow.incidents select *` |
| "critical splunk alerts" | `foreach a in ext.splunk.alerts where a.urgency == "high" select *` |
| "tenable critical vulnerabilities" | `foreach v in ext.tenable.vulnerabilities where v.severity == "critical" select *` |
| "what connectors are registered" | `foreach c in ext.databridge.connectors select *` |
| "combine incidents and alerts" | `foreach x in union("ext.servicenow.incidents", "ext.splunk.alerts") select *` |

---

## Registration Checklist (per CLAUDE.md 8-point rule)

| # | Item | Phase | Status |
|---|------|-------|--------|
| 1 | `tools/manifest/databridge.md` — IQE adapter entry | I | ✅ |
| 2 | `docs/reference/commands.md` — CLI commands | I | pending |
| 3 | `args/security_gates.yaml` — no new gate (graceful degrade) | — | n/a |
| 4 | `tools/mcp/tool_registry.py` — uses existing IQE dispatch | — | n/a |
| 5 | `.claude/hooks/pre_tool_use.py` — no append-only tables in I/II | — | n/a |
| 6 | `tests/conftest.py` — ext.* is stateless; no schema | — | n/a |
| 7 | `python tools/dx/companion.py --sync --write --json` | post-II | pending |
| 8 | `python tools/workflow/coherence_checker.py --all --fix --gate` | post-II | pending |

---

## Compliance Notes

- All `ext.*` adapters carry `# CUI // SP-CTI` header
- Graceful degradation prevents data leakage on connector failure
- Phase III lineage tags satisfy NIST AU provenance requirements
- Phase IV OPA gate enforces data mesh domain ownership (CMMC SC controls)
- No secrets in adapter code — all credentials via DataBridge secret resolver chain
