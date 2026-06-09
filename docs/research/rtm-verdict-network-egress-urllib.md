# RTM Verdict — Authorization of `urllib.request` / `urlopen` (`network_egress` capability)

**Task:** task-257b02c294-d2
**Verdict:** **AUTHORIZED (with constraints)**
**Date:** 2026-06-09
**Reviewer:** Claude (kanban session kanban/task-257b02c294-d2)

---

## 1. Question

Does the internal Requirement Traceability Matrix (RTM) and its supporting
governance artifacts explicitly authorize the use of
`urllib.request.Request` / `urllib.request.urlopen` in ICDEV™ tooling code
as the implementation of the `network_egress` capability?

## 2. Scope of the search

Checked (against the live PostgreSQL backend):

| Source | Table / file | Rows searched |
|---|---|---|
| `requirements` | `public.requirements` | 0 rows |
| `intake_requirements` | `public.intake_requirements` | 966 rows |
| `doors_requirements` | `public.doors_requirements` | 0 rows |
| `fr_requirements` | `public.fr_requirements` | 3 rows |
| Capability domain catalog | `context/capabilities/*.yaml` | 17 files |
| Governance yaml | `args/security_gates.yaml` | URL-scheme gate |
| Architecture decisions | `docs/reference/adrs.md` (D-KARL-9, D-FS-TIER-3) | ADRs |
| Feature specs | `docs/features/phase-*.md`, `docs/features/sdc-*.md`, `docs/features/phase-ddc-lineage.md`, `docs/features/internal-awareness-engine.md` | 5 docs |
| IQE capability query | `context/iqe/queries/integrity/04_network_egress_capabilities.iqe` | 1 query |

Narrow patterns queried across the requirement tables: `urllib`, `urlopen`,
`network_egress`, `egress`, `outbound http`, `egress policy`, `allowlist`,
`whitelist`. Broad patterns (network / http / socket / connect / fetch / api /
internet / egress / external / web / download / upload / remote / endpoint /
port / tcp / tls / ssl / proxy) yielded 40+ intake_requirement hits, none of
which grant or deny a specific Python network library — they describe
external integrations (REST APIs, mTLS, IC IE data fabric, JISE portal,
ServiceNow, Splunk, AWS Control Tower, Tenable, OpenMetadata, DataHub,
Caldera) at the capability level.

## 3. Findings — the authorization chain

The RTM does not contain a single requirement that says "use `urllib.request`."
Authorization of `urllib` is granted **structurally**, by ADRs and feature
specs that declare air-gap compatibility as a hard requirement and then name
stdlib `urllib` as the only air-gap-safe way to satisfy it.

### 3.1 Direct ADR authorization (binding)

* **D-KARL-9 (docs/reference/adrs.md):** "GraphRAG semantic search — … Uses `urllib.request` for embedding query (no external deps)." This explicitly authorizes `urllib.request` in a runtime call site, with the rationale "no external deps" — i.e., air-gap compatibility.
* **D-FS-TIER-3 (docs/reference/adrs.md):** "Parent ICDEV™ HTTP client uses stdlib urllib (air-gap safe) with queued retry when parent unreachable." Explicitly names `urllib` as the chosen client for ICDEV's own HTTP egress.

### 3.2 Feature-spec authorization (binding per canvas)

* **docs/features/phase-ddc-lineage.md** §4.4 (OpenMetadata sync) and §4.5 (DataHub sync): "Uses stdlib `urllib` only — no `requests` or external packages." Listed in the air-gap compatibility row of the DDC feature table.
* **docs/features/phase-sdc-attackpath.md** §8 (Caldera integration): "`CalderaAdapter` wraps the Caldera v2 REST API using stdlib `urllib` only (no external deps)."
* **docs/features/internal-awareness-engine.md** (probe implementation): "`http_head`: `urllib.request` HEAD with 5s timeout. Flask routes reachable at `localhost:5050`." Authorized for the health prober of the Awareness Engine.

### 3.3 Governance constraint (security_gates.yaml)

`args/security_gates.yaml` defines the **URL Scheme Validation Gate (B310)**:

* Detects Bandit B310 — `urllib_urlopen` called with a scheme outside the `http`/`https` allowlist.
* **Blocking:** `b310_non_http_scheme_in_urlopen`, `file_scheme_in_external_call`, `custom_scheme_without_allowlist`.
* **Warning:** `ftp_scheme_detected`, `scheme_validation_not_enforced`.
* Permitted schemes: `http`, `https` (and any additional allowlist the system adds via `# nosec B310` with justification).

This gate **does NOT prohibit `urllib.request` itself** — it prohibits passing non-HTTP(S) schemes to it. So the gate is a constraint on *how* `urllib` may be used, not a prohibition.

### 3.4 RTM absence (important nuance)

No row in `requirements`, `intake_requirements`, `doors_requirements`, or
`fr_requirements` explicitly authorizes a Python library. The `intake_requirements`
table (966 rows) contains many functional, security, interface, and
performance requirements that *depend on* outbound network calls (REST API
exposure, IC IE data fabric, ServiceNow/Splunk/Tenable, OpenMetadata, DataHub,
Caldera, OS DNS, NIPR, air-gapped on-prem scanner agent, IL5 ingestion, etc.)
but they do not pin the implementation library. Library choice is delegated
to architecture decisions and feature specs, per the FORGE principle that
"deterministic tools execute; AI orchestrates."

## 4. Verdict

**AUTHORIZED.**

`urllib.request.Request` / `urllib.request.urlopen` is the explicitly chosen
HTTP egress primitive for ICDEV™ tooling where air-gap compatibility is
required. The authorization chain is:

1. **Capability-level:** D-KARL-9 (GraphRAG), D-FS-TIER-3 (parent HTTP client) — binding.
2. **Feature-level:** DDC lineage sync (OpenMetadata, DataHub), SDC attack path (Caldera), Internal Awareness Engine health prober — binding for those canvases.
3. **Governance constraint:** B310 URL-scheme validation gate restricts *which schemes* may flow through `urlopen`; it does not ban the function.

**Conditions that must hold for the authorization to remain valid:**

* The call must use a `permitted_schemes` (http/https) URL, or carry a documented `# nosec B310` justification if it does not.
* It must respect the SIPA/software-integrity posture: outbound destinations are an `integrity.capabilities` row with `capability_type = 'network_egress'` (per the IQE query `04_network_egress_capabilities.iqe`), recorded in the capability catalog.
* The call must be retry-aware and time-bounded (mirrors D-FS-TIER-3 "queued retry when parent unreachable" and the 5 s health-probe timeout).

**Verdict: authorized vs unauthorized → AUTHORIZED (with the B310 scheme-allowlist and SIPA capability-recording constraints above).**
