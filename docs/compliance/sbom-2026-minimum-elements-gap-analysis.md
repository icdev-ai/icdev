# CUI // SP-CTI

# SBOM 2026 Minimum Elements — Conformance Gap Analysis

**Standard:** *2026 Minimum Elements for a Software Bill of Materials (SBOM)*, published
2026-07-29 by CISA with NSA, FBI and 16 international partners (ASD's ACSC, Cyber Centre,
NÚKIB, ANSSI, BSI, CERT-In, ACN, METI, NCO, NIS/NCSC, KISA, NCSC-NL, NCSC-NZ, NASK, NBU).
Document version 2.1. Marked TLP:CLEAR.

**Supersedes:** *The Minimum Elements For a Software Bill of Materials* (NTIA, 2021-07-12).
The 2021 document is replaced, not amended.

**Analysis date:** 2026-08-02
**Subject under analysis:** ICDEV™ SBOM generation, storage, ingestion and gating.
**Project card:** `sbx` — see `args/projects.yaml`.

---

## 1. What the standard requires

The standard sets baseline expectations in two categories: **Data Fields** (17 elements, split
into SBOM Metadata and Component Data) and **Practices and Processes** (7 elements). The
Access Control element from 2021 is **removed**; its considerations fold into Distribution and
Delivery. SWID tags are **removed** from the list of acceptable data formats.

The standard explicitly states it does *not* create new requirements — it refines how
organizations generate and request SBOMs. It applies to all software including open source, AI
software, and SaaS. It deliberately introduces **no additional elements for AI systems**; the
G7/CISA *SBOM for AI – Minimum Elements* (May 2026) is a separate document and is out of scope
for this card.

### 1.1 SBOM Metadata elements (9 named in the body, 9 in Appendix A)

| Element | Status vs 2021 | Definition |
|---|---|---|
| SBOM Author | Major update (was "Author of SBOM Data") | The name of the entity that creates the SBOM data for the target component. Captures the entity *operating* the tool, not the tool. Full names, no acronyms. |
| SBOM Author Signature | **New** | A digital signature attributable to the SBOM author. Algorithm must be approved by a relevant authority (NIST DSS, ISO/IEC 14888-4:2024, ENISA Agreed Cryptographic Mechanisms). |
| SBOM Data Format Name | **New** | The name of the data format used to represent the SBOM data. |
| SBOM Data Format Version | **New** | Identifier designated by the data format for its version. Deprecated versions should not be used. |
| SBOM Generation Context | **New** | The relative software lifecycle phase and data available when the SBOM was generated — e.g. "before build", "build", "after build". |
| SBOM Timestamp | Minor update | Date and time of the most recent update to the SBOM data. **Must adhere to RFC 9557.** Each version gets a new timestamp. |
| SBOM Tool Name | **New** | The name of the tool used by the SBOM author to generate or amend the SBOM. |
| SBOM Tool Version | **New** | Version of that tool. If unavailable, the author must indicate unknown. |
| SBOM Version | **New** | Identifier specifying a change from a previously identified version, or that it is the first. May use Semantic Versioning; if so, the major version of an SBOM following these elements should be "1". Serial-number style identifiers should conform to RFC 9562. Versioned per component-name/component-version pair. |

### 1.2 Component Data elements

| Element | Status vs 2021 | Definition |
|---|---|---|
| Component Producer | Major update (**replaces Supplier Name**) | The entity that creates, defines and identifies components. Exactly one organization per component. Where no producer is identifiable, the author must explicitly mark the component as **of unknown provenance**. |
| Component Dependency Relationship | Minor update | The relationship between two components where one is necessary for the operation of the other. Must support building a dependency graph. May be embedded or expressed as links to separate SBOM documents. |
| Component Hash Value | **New** | ASCII hex-encoded output of a cryptographic hash over the **executable component artifact**. If the author cannot access the artifact, the value must be marked unknown. |
| Component Hash Algorithm | **New** | The algorithm that produced the hash. Should be named using **IANA Hash Function Textual Names** and approved by a relevant authority such as NIST. |
| Component Identifiers | Major update (was "Other Unique Identifiers") | At least one common software identifier (CPE, PURL). May also carry UUIDs, org-specific identifiers, commit hashes, and intrinsic identifiers (OmniBOR, SWHID). **If multiple exist, include all of them.** |
| Component License | **New** | Identifier(s) for the license(s) the component is available under. Should use SPDX license identifiers where possible; otherwise a URL to full details. Must indicate the existence of proprietary license conditions. Unknown must be stated explicitly. |
| Component Name | Minor update | Name assigned by the component producer. **Formats must allow multiple entries** to capture alternate names. |
| Component Version | Major update | Producer's version identifier. **If the producer does not provide a version, the author must indicate the version is unknown.** |

### 1.3 Practices and Processes

| Element | Status vs 2021 | Requirement |
|---|---|---|
| Accommodation of Updates to SBOM Data | Major update (was "Accommodation of Mistakes") | Accommodate updates including corrections; correct errors promptly. Recipients may now weigh SBOM errors in risk decisions — the 2021 tolerance for immature data is withdrawn. |
| Coverage | Major update (**replaces Depth**) | All components including **transitive dependencies**. **No minimum depth.** Multiple instances of a component with differing metadata are listed separately with their dependency relationship. May exclude non-code files but may include security-relevant files such as configuration. Linking to subcomponent SBOMs is acceptable **only if the recipient has access to all linked SBOMs**. |
| Distribution and Delivery | Minor update | Available promptly to those who need them. Access controls may limit unauthorized sharing but must not block authorized parties or prevent integration into trusted security tools. Acceptable mechanisms include accompanying installation, a **version-specific URL**, an API, or a public repository. |
| Explicitly Identifying Unknown Information | Major update (was "Known Unknowns") | Where a field is not provided, state explicitly whether it is **unknown to the author** or **withheld by the author** — these are now distinct. The author must have a process for recipients to ask about redacted security-related information. An SBOM withholding essential component data may be considered incomplete. |
| Frequency | Minor update | Every software version or update has an associated SBOM. New build or release → new SBOM. New details discovered or error corrected → revised SBOM. |
| Machine-Processable Data | Major update (was "Automation Support") | **SPDX and CycloneDX** are named as the two widely used formats. SWID removed. Accept any widely used, interoperable, machine-processable format; avoid deprecated versions of any format. Reassess supported formats regularly. |
| ~~Access Control~~ | **Removed** | Folded into Distribution and Delivery. |

---

## 2. What ICDEV has today

### 2.1 Generation

`tools/compliance/sbom_generator.py` (mirrored at `icdev/tools/compliance/sbom_generator.py`)
is the only SBOM producer in the tree. It:

- emits **CycloneDX only**, default spec **1.4**, selectable 1.4–1.7 via `--spec-version`;
- parses **declared dependency manifests** — `requirements.txt`, `pyproject.toml`,
  `package.json`, `package-lock.json`, `go.mod`, `Cargo.toml`, `pom.xml`, `build.gradle`,
  `*.csproj`, `packages.config`;
- emits per component: `type`, `bom-ref`, `name`, `version`, `group`, `purl`, `scope`;
- emits metadata: `timestamp`, `tools[{vendor,name,version}]`, target `component`, and four
  ICDEV `properties` (classification, project-id, cui-category, distribution);
- writes one row to `sbom_records` and logs an `sbom_generated` audit event.

**Two defects found while reading it, independent of the new standard:**

1. **The file is marked `DEPRECATED: unused as of 2026-05-09. Remove after 2026-08-01`** — that
   removal date is *yesterday* — yet roughly 25 live call sites depend on it:
   `tools/ci/workflows/icdev_comply.py`, MCP `sbom_generate` (`tools/mcp/tool_registry.py`),
   `tools/dashboard/api/batch.py`, `tools/govcon/cdrl_generator.py`,
   `tools/testing/production_audit.py` and `production_remediate.py`,
   `tools/infra/ironbank_metadata_generator.py`, `tools/studio/workflow_editor.py`,
   `args/bdc_canvas_config.yaml` (a **blocking** gate), `tools/compliance/fedramp_ksi_generator.py`,
   `fedramp_authorization_packager.py`, `owasp_llm_assessor.py`, `swft_evidence_bundler.py`.
   The deprecation marker is wrong and must be resolved before anything is built on this module.
2. **`tools/sbom/sbom_generator.py` does not exist**, but is cited as a real path in
   `tools/ace/roles/compliance_manager/TOOLS.md`, `.../SOUL.md` and
   `tools/ace/roles/devops_engineer/TOOLS.md`.

**Both defects are RESOLVED (sbx-fnd-01).** The decision on record: `tools/compliance/sbom_generator.py`
is **CANONICAL** — it is the sole SBOM producer and everything the remaining `sbx` tasks add is
built on it. The false deprecation marker is gone from both the root file and the
`icdev/tools/compliance/` mirror, replaced by a `# CANONICAL:` note that names the call sites and
the blocking gate so no future session re-deprecates it. `tools/manifest/compliance-engine.md`
now lists it as `[CANONICAL]`. The three ACE role cards were corrected to the real path (and two
further phantom paths in the same list — `tools/compliance/compliance_mapper.py` and
`tools/classification/classification_manager.py` — were corrected to `control_mapper.py` and
`tools/compliance/classification_manager.py`). `tests/test_sbom_generator_canonical.py` pins the
outcome: both import namespaces expose `generate_sbom`, no removal-date comment survives in either
copy, the two copies stay byte-identical, and no ACE role card cites a non-existent module path.

### 2.2 Storage

Baseline as analysed:

- `sbom_records` — `project_id, version, format, file_path, component_count,
  vulnerability_count, generated_at` (`tools/db/init_icdev_db.py`, `tools/saas/db/pg_schema.py`).
- `sbom_components` (migration 209) — `component_name, version, vendor, component_type, purl,
  license, classification`. **The generator never writes to this table**, so the existing
  `license` and `vendor` columns are dead.

**Resolved by sbx-fnd-02** — migration
`tools/db/migrations/20260808030213_sbom_2026_minimum_elements`, dual-engine
(`@sqlite-only` / `@pg-only`), applied and round-trip verified on both backends:

- `sbom_records` gains the nine SBOM Metadata fields plus the signature pair and the
  supersedes link: `sbom_author`, `author_signature`, `signature_algorithm`,
  `data_format_name`, `data_format_version`, `generation_context`, `tool_name`,
  `tool_version`, `sbom_version`, `serial_number`, `supersedes_sbom_id`.
- `sbom_components` gains `producer`, `hash_value`, `hash_algorithm`, `identifiers_json`,
  `unknown_fields_json`, `withheld_fields_json`. The dead `license` and `vendor` columns
  are **reused** — `license` is the 2026 Component License element — not duplicated.
- `sbom_dependencies` is new: the Component Dependency Relationship element as an edge
  table (`sbom_record_id`, `parent_component_id`, `child_component_id`,
  `relationship_type`, `scope`) rather than a parent-ref column, because a component has
  many parents inside one document. Edges are scoped by document, since the same
  component row sits at different points of the graph in two different SBOMs.
  `relationship_type` carries no CHECK vocabulary — that is sbx-cov-02's to define, and
  it has to cover both CycloneDX `dependsOn` and SPDX `RELATIONSHIP` kinds.
- **RLS:** `sbom_records` had neither `classification` nor `tenant_id`, so every query
  from a request context would have raised `UndefinedColumn` once the table was read
  through `get_connection()` — the trap migrations 305/309/311/326 each documented. Both
  columns are now ensured on `sbom_records` and `sbom_components`, and declared on
  `sbom_dependencies`, with 326's defaults: `classification NOT NULL DEFAULT 'CUI'`,
  `tenant_id` nullable.
- **Not covered:** `tools/saas/db/pg_schema.py` still declares the pre-2026
  `sbom_records` shape for tenant/platform databases. Extending it is deliberately
  deferred — `migrate.py --up --all-tenants` would then apply this migration's `ALTER`
  to a tenant database that already had the columns, and SQLite has no
  `ADD COLUMN IF NOT EXISTS`. Sequence it with sbx-gov-02, which is where multi-tenant
  SBOM retrieval lands.

Shape is pinned by `tests/test_sbom_2026_schema.py`, which applies the real migration to
a pre-migration database and round-trips a value through every new column, and asserts
`MINIMAL_ICDEV_SCHEMA` in `tests/conftest.py` declares the identical column set.

### 2.3 Signing

ICDEV does **not** sign its own SBOMs. `tools/crypto/attestation_signer.py::sign_artifact` and
`tools/crypto/key_manager.py::sign_payload` exist and are unused by the SBOM path. `cosign`
appears only inside **CI YAML that ICDEV generates for downstream projects**
(`tools/devsecops/pipeline_security_generator.py`, `tools/devsecops/attestation_manager.py`) —
ICDEV instructs others to attest SBOMs while producing unsigned ones itself.

### 2.4 Ingestion and assessment

- `icdev/tools/security_canvas/zig_external_adapter.py::ingest_sbom` accepts **CycloneDX JSON
  only** and maps components to ZIG activities.
- `fedramp_assessor.py`, `sbd_assessor.py`, `cssp_assessor.py`, `ivv_assessor.py` check for SBOM
  presence by **filename glob** (`*sbom*`, `*spdx*`, `*cyclonedx*`). None parses or validates.

### 2.5 Gating

`args/security_gates.yaml` already carries `sbom_not_generated` (deployment, swft),
`sbom_attestation_missing` (devsecops), `sbom_stale_over_30_days` / `sbom_max_age_days: 30`
(sbd, swft), and `sbom_generation_failed` / `sbom_generation_skipped` (marketplace, production).
Every one of these is a **presence, freshness or exit-code check**. Nothing validates
conformance to the minimum elements.

### 2.6 SPDX

There is **no SPDX generator and no SPDX parser** anywhere in the tree. SPDX appears only in
assessor glob patterns, in third-party CI templates ICDEV emits, and in GovCon proposal /
knowledge-base seed content that states ICDEV produces SBOMs "in SPDX and CycloneDX formats"
(`tools/govcon/generate_icdev_proposal_content.py`,
`tools/govcon/seed_icdev_knowledge_base.py`, `tools/govcon/seed_solicitation_requirements.py`).
That claim is not currently true and is customer-facing.

---

## 3. Conformance matrix

Legend: **MET** — emitted correctly today · **PARTIAL** — present but non-conforming ·
**GAP** — absent.

### 3.1 SBOM Metadata

| Element | Status | Evidence / what is missing |
|---|---|---|
| SBOM Author | **GAP** | No author field. `metadata.tools[].vendor = "ICDEV™"` identifies the tool vendor, which the standard explicitly says is *not* the author. |
| SBOM Author Signature | **GAP** | No signing in the SBOM path. |
| SBOM Data Format Name | **MET** | `bomFormat: "CycloneDX"`. |
| SBOM Data Format Version | **PARTIAL** | `specVersion` present, but defaults to **1.4** (2022). The standard cites ECMA-424 (Dec 2025) and warns against deprecated versions. Default should move up. |
| SBOM Generation Context | **GAP** | Nothing records lifecycle phase. ICDEV generates from source manifests, i.e. "before build" — knowable and currently unstated. |
| SBOM Timestamp | **PARTIAL** | Emitted as `%Y-%m-%dT%H:%M:%SZ`. Needs explicit RFC 9557 conformance and a test. |
| SBOM Tool Name | **MET** | `metadata.tools[].name = "icdev-sbom-generator"`. |
| SBOM Tool Version | **PARTIAL** | **Hardcoded `"1.0.0"`** — not derived from anything. It is a constant that will never change and therefore misidentifies the code delivery. |
| SBOM Version | **PARTIAL** | Document always carries `"version": 1` while `sbom_records.version` independently counts 1.0, 2.0, 3.0… The two disagree, and neither follows the "major version should be 1, use minor/patch for content changes" guidance. |

### 3.2 Component Data

| Element | Status | Evidence / what is missing |
|---|---|---|
| Component Producer | **GAP** | No supplier/producer emitted at all. `group` is a Maven/npm namespace, not a producer. No unknown-provenance fallback. |
| Component Dependency Relationship | **GAP** | The SBOM is a **flat component list**. No CycloneDX `dependencies` array is emitted, so no dependency graph can be built from ICDEV output. |
| Component Hash Value | **GAP** | The generator reads manifests, never artifacts, so no hash is computable on the current design. |
| Component Hash Algorithm | **GAP** | Consequent to the above. |
| Component Identifiers | **MET (sbx-fld-05)** | Was: `purl` only. Now `tools/compliance/sbom_identifiers.py` derives every identifier the coordinates support — see §3.2.1. |
| Component License | **GAP** | Not emitted, though `sbom_components.license` already exists in schema. |
| Component Name | **PARTIAL** | Single name only; the standard requires formats to allow alternate names. |
| Component Version | **PARTIAL** | Unresolved versions are written as the literal strings `"unspecified"` (most parsers) and `"managed"` (Maven). These are not machine-interpretable unknown markers and collide with the Explicitly Identifying Unknown Information element. |

#### 3.2.1 Component Identifiers — resolved (sbx-fld-05)

`tools/compliance/sbom_identifiers.py` (mirrored under `icdev/tools/compliance/`) derives the
full identifier set from the coordinates a manifest parser already produces, and
`_build_cyclonedx_sbom` applies it to every component.

| Type | Derivation |
|---|---|
| PURL (ECMA-427) | From the parser, as before. |
| CPE 2.3 | Derived for every component with a name. Vendor comes from the reverse-DNS Maven group (`org.apache.logging.log4j` → `apache`), the npm scope (`@babel` → `babel`), the Go module path (`github.com/spf13/cobra` → `spf13`/`cobra`), or a dotted NuGet id (`Newtonsoft.Json` → `newtonsoft`/`json`). |
| UUID (RFC 9562) | Deterministic v5 over the coordinates, namespace `uuid5(NAMESPACE_DNS, "sbom.icdev.ai")`. |
| Organization-specific | `icdev:component:<16 hex>` — the same value as the CycloneDX `bom-ref` and the `sbom_components` primary key, from one formula. Derivable from coordinates alone, so **every** component carries at least one identifier even when purl, version and vendor are all unknown. |
| Commit hash | Read out of a Go pseudo-version (`v0.0.0-20191109021931-daa7c04131f5`), or taken from a parser-supplied `commit_hash`. |
| SWHID (ISO/IEC 18670:2025) | `swh:1:rev:<sha1>` — only from a **full** 40-hex revision. An abbreviated Go pseudo-version hash deliberately does not produce one. |
| OmniBOR | Pass-through only. It is computed over artifact bytes, which the generator does not read until sbx-fld-03 / sbx-cov-01; nothing is fabricated. |

Two decisions worth recording:

- **What is emitted is a CPE *match string*, not a claim of NVD dictionary membership.**
  Attributes that cannot be derived with confidence stay as the ANY wildcard `*` rather than a
  guessed vendor. A guess narrows a CVE join and loses findings; `*` widens it. `target_sw` is
  left ANY for the same reason. Unresolved versions (`unspecified`, `managed`) become `*` instead
  of being written through as a literal that would match nothing.
- **Escaping follows real NVD data, not a maximal reading of NIST IR 7695**: everything outside
  `[A-Za-z0-9._-]` is backslash-escaped and `.` / `-` are left bare, because that is how NVD
  writes them (`cpe:2.3:a:node-red:node-red:1.0.0:*:...`). Escaping them would break the string
  match this element exists to enable.

Emission is spec-version aware: `purl` and `cpe` use the native CycloneDX fields on every
version, `swhid` and `omniborId` use the arrays CycloneDX added in 1.6, and everything else —
plus anything that overflows a single-valued native field, such as a second CPE — goes to
`icdev:identifier:<type>` properties. Nothing is dropped on 1.4 or 1.5.

Round-trip is pinned in both directions by `tests/test_sbom_component_identifiers.py` (64 tests):
`parse_identifiers_from_cyclonedx()` returns the identical set on all four spec versions, and
`identifiers_to_json()` / `identifiers_from_json()` round-trip through the real
`sbom_components.identifiers_json` column — including one end-to-end test that drives
`generate_sbom()` through `tools.db.storage`, so the `%s` translation and the upsert are
exercised by the production path rather than by a shim.

`generate_sbom()` now writes `sbom_components` rows at all; before this task nothing in the tree
ever did, which is why `license` and `vendor` were dead columns. The table is a coordinate-keyed
catalog (it has no per-document key — `sbom_dependencies` is what scopes a component to one SBOM),
so persistence is an upsert on the deterministic component id and a re-run updates in place.

`python tools/compliance/sbom_identifiers.py --validate <sbom.cdx.json>` reports identifier
totals, how many components are NVD-joinable, and exits non-zero on a conformance failure —
the foothold sbx-sig-02's full minimum-elements validator composes.

**Two pre-existing defects the validator surfaced and this task fixed**, both in
`sbom_generator.py`'s parsers:

1. Scoped npm packages were encoded `pkg:npm/@babel%2Fcore@7.24.0` — the `/` namespace separator
   percent-encoded and the reserved `@` left bare, exactly backwards. ECMA-427 rejects it. Now
   `pkg:npm/%40babel/core@7.24.0`.
2. `_parse_go_mod`'s single-line `^require\s+(\S+)\s+(\S+)` matched across the newline after
   `require (`, emitting a phantom component named `(` whose version was the first module path.
   The separator is now horizontal whitespace only.

### 3.3 Practices and Processes

| Element | Status | Evidence / what is missing |
|---|---|---|
| Accommodation of Updates | **PARTIAL** | `sbom_records` versions rows, but there is no correction/revision workflow and no way to mark a prior SBOM superseded. |
| Coverage | **GAP** | The generator parses **declared** dependencies, so it captures direct dependencies only for every ecosystem except npm (`package-lock.json` alone yields a resolved tree). Transitive coverage is the single largest gap and the one the standard changed most decisively — "no minimum depth". |
| Distribution and Delivery | **PARTIAL** | Writes a file to disk and records a path. No version-specific URL and no retrieval API. |
| Explicitly Identifying Unknown Information | **GAP** | No unknown/withheld distinction anywhere; `"unspecified"` conflates them and is not machine-processable. No documented process for recipients to query redactions. |
| Frequency | **PARTIAL** | `CLAUDE.md` asserts "SBOM regenerated on every build"; the enforced gate is a **30-day staleness** threshold, which is materially weaker than per-release. |
| Machine-Processable Data | **PARTIAL** | CycloneDX yes; **SPDX absent** although the standard names both. No SWID emitted, which the 2026 removal makes correct by accident. |
| Access Control (removed) | **N/A** | ICDEV's CUI classification properties remain appropriate under Distribution and Delivery. |

**Score: 3 of 17 data-field elements fully met** (SBOM Data Format Name, SBOM Tool Name,
Component Name is partial — counting strictly, 2 fully met plus 7 partial), **0 of 7 practices
fully met.**

---

## 4. Implementation approach

Five workstreams, in dependency order. The card `sbx` carries one task per shippable unit.

1. **Foundation (`sbx-fnd-*`)** — resolve the deprecated-vs-load-bearing contradiction in
   `sbom_generator.py` and fix the phantom `tools/sbom/` references; then land the schema
   migration that gives the new fields somewhere to live. Per the repo's INSERT/schema-parity
   rule this must be a **new numbered migration**, not an edit to `CREATE TABLE` in
   `init_icdev_db.py`.
2. **Data fields (`sbx-fld-*`)** — the 17 elements, metadata block first (cheap, self-contained),
   then component fields.
3. **Coverage (`sbx-cov-*`)** — the expensive one. Move from manifest parsing to **resolved**
   dependency sets per ecosystem, and emit a real dependency graph. This is what unlocks hashes
   too, since resolution yields artifact locations.
4. **Practices (`sbx-prc-*`)** — unknown/withheld semantics and revision/frequency behaviour.
5. **Signature, formats and governance (`sbx-sig-*`, `sbx-fmt-*`, `sbx-gov-*`)** — sign with the
   existing `tools/crypto` primitives, add an SPDX writer, add a conformance validator, and wire
   that validator into the gates so conformance is enforced rather than assumed.

### Deliberate scope exclusions

- **No AI-specific SBOM elements.** The 2026 document introduces none; the G7 AI SBOM minimum
  elements are a separate standard and a separate card if wanted.
- **No SaaS-specific elements.** The document flags SaaS frequency as unresolved future work.
- **No VEX/CSAF work.** Correlation with security advisories is discussion material in the
  standard, and ICDEV already has `vex_generate`.
- **No change to `translate_sql` or storage backend behaviour.** PostgreSQL remains primary.

### Success criteria for the card as a whole

An SBOM produced by `python tools/compliance/sbom_generator.py` for any supported ecosystem
scores **17/17 data fields and 6/6 applicable practices** under
`sbom_minimum_elements_validator.py --json`, is emitted in both CycloneDX and SPDX, carries a
verifiable author signature, and a deploy gate blocks when that score regresses.

---

## References

- CISA et al., *2026 Minimum Elements for a Software Bill of Materials (SBOM)*, 2026-07-29 (TLP:CLEAR).
- NTIA, *The Minimum Elements For a Software Bill of Materials*, 2021-07-12 (superseded).
- Ecma International, ECMA-424 (CycloneDX) and ECMA-427 (PURL), December 2025.
- ISO/IEC 5962:2021 (SPDX); ISO/IEC 18670:2025 (SWHID).
- RFC 9557 (timestamps); RFC 9562 (UUIDs).
- IANA Hash Function Textual Names; NIST Digital Signature Standard.

# CUI // SP-CTI
