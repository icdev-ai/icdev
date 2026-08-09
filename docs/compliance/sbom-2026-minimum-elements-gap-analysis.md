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
- parsed **declared dependency manifests** — `requirements.txt`, `pyproject.toml`,
  `package.json`, `package-lock.json`, `go.mod`, `Cargo.toml`, `pom.xml`, `build.gradle`,
  `*.csproj`, `packages.config`. *(Superseded by sbx-cov-01: components now come from
  `dependency_resolver.resolve_project`, and those manifest parsers survive only as the
  declared-only fallback — see §2.7.)*;
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
  `license` is no longer dead: **sbx-fld-04** made the generator write `sbom_components`
  at all, and it is the single writer of that table (see 3.2.1). It populates `license`,
  `producer` and the two disclosure blobs from the finished document. `vendor` stays
  deliberately NULL — Component Producer is the 2026 element that replaced Supplier Name,
  and `producer` is the column that carries it.
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

**None of it existed on a fresh PostgreSQL until sbx-fld-04.** `bootstrap_pg.py` loads
`tools/db/schema/pg_consolidated.sql` and marks every migration at or below
`through_version` (301) applied *without running it*. Migration 209 is below that pivot
but its three tables — `sbom_components`, `supply_chain_vulnerabilities`,
`supply_chain_risk_scores` — are **not in the snapshot**, which was dumped from a
canonical database where 209 had never actually run. So a freshly bootstrapped PG had no
`sbom_components` at all, and the migration above therefore failed every one of its
`sbom_components` ALTERs and its `sbom_dependencies` CREATE with `UndefinedTable` — each
swallowed by the runner's skip-failed-statement guard and then recorded as applied. The
entire SBOM storage layer was absent on the primary backend while every version marker
said otherwise. Repaired by `20260808043009_restore_migration_209_tables_on_postgresql`,
which sits above the pivot, recreates the three tables idempotently (plus
`sbom_dependencies`, and `sbom_components` in its full post-fnd-02 shape since it sorts
after that migration), and no-ops on any database that already has them. Found by
`tests/pg_tier/test_sbom_component_license_pg.py`, the first runtime test to write
`sbom_components` on the ambient backend — the SQLite suite could not have caught it.
**Not audited:** whether other pre-301 migrations are missing from the snapshot the same
way. That is a schema-wide sweep, not this card.

### 2.3 Signing — SBOM Author Signature (sbx-sig-01)

**Closed.** `tools/compliance/sbom_signer.py` (mirrored at `icdev/tools/compliance/`) signs every
generated SBOM through the two primitives that already existed and had no caller on this path:
`tools/crypto/attestation_signer.py::sign_artifact` over
`tools/crypto/key_manager.py::sign_payload`. `cosign` still appears only inside CI YAML ICDEV
generates for downstream projects (`tools/devsecops/pipeline_security_generator.py`,
`tools/devsecops/attestation_manager.py`); ICDEV no longer produces unsigned SBOMs while
instructing others to attest theirs.

What is signed is the **canonicalized SBOM document** (sorted keys, no whitespace) — not the
file's bytes, because re-indenting a file does not change the bill of materials and a signature
that broke on formatting is one people learn to ignore. The exact bytes are digested separately
(`file_sha256`) and a mismatch is reported as `bytes_modified` rather than as a failure. The
signature is written **detached** to `<sbom>.sig.json`, which keeps the CycloneDX output
byte-identical for every existing consumer and avoids embedding a signature in the document it
covers. `author_signature` and `signature_algorithm` (migration `20260808030213`, sbx-fnd-02)
are persisted on the `sbom_records` row.

**Approved algorithms.** `APPROVED_ALGORITHMS` is the intersection of "approved by an authority
the standard names" and "producible by `key_manager`": ECDSA over P-256/P-384/P-521 with
SHA-256, and Ed25519 — all FIPS 186-5 (NIST DSS) and all on the ENISA Agreed Cryptographic
Mechanisms list. RSA-PSS is approved by FIPS 186-5 and is deliberately **absent**, because
`sign_payload` has no RSA branch and listing it would be a claim rather than a capability.

Two things are refused outright rather than emitted under the element's name:

- **HMAC-SHA256.** `key_manager` degrades to it so audit logging never breaks. It is a symmetric
  MAC — every party who can verify it can also forge it — so it cannot be *attributable to the
  SBOM author*, which is the entire property this element exists to provide.
- **`"none"`**, the empty-signature no-op returned when no key is configured.

Signing also required a correctness fix in `key_manager`: `sign_payload` labelled **every**
elliptic-curve key `ECDSA-P256-SHA256`, so a `secp256k1` key — approved by no authority for this
use — would have been recorded and reported under a NIST-approved name, and any allowlist check
reading `algorithm` would have passed it. The label is now derived from the key's actual curve
(`_ecdsa_algorithm`). The digest stays SHA-256 across all curves, which FIPS 186-5 permits and
which keeps every previously-issued signature verifiable.

**Air gap.** Neither path touches the network: no sigstore, no Fulcio, no Rekor, no OCSP.
Signing reads a local PEM private key; verification reads the public key embedded in the
detached file, or a locally held one. `key_manager.verify_payload` gained a `public_key_pem`
parameter for this — deriving the public key from the *private* key, which was the only path it
had, cannot serve a consumer who legitimately does not hold the private key.

**Integrity is not authenticity.** Because the detached file carries its own public key, an
attacker who rewrites an SBOM can re-sign it with their own key; unpinned verification then
truthfully reports the document matches its signature. `verify_sbom` therefore returns
`verified` and `trusted` as two separate fields, and `trusted` is `True` only when the caller
pinned the expected fingerprint (`--expect-fp`) from an out-of-band source.

Unsigned generation remains the **default** and is announced on stdout: `sbom_generator` has
~25 live call sites and a blocking `bdc_canvas` gate, so hard-failing on a missing key would
turn "SBOMs are not signed yet" into "SBOM generation is broken" everywhere at once. Operators
who require signatures set `ICDEV_SBOM_REQUIRE_SIGNATURE=1` for fail-closed behaviour. What is
never permitted, in either mode, is a *non-conformant* signature.

Sandbox posture for the verification path (attacker-supplied SBOM + signature + PEM):
`docs/security/sandbox-coverage.md` Gap 19.

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

**RESOLVED (sbx-gov-01).** Two conditions now gate on what the document says:
`sbom_minimum_elements_not_met` (blocking) and `sbom_conformance_below_threshold` (warning),
wired into `deployment_gates`, `swft` and `devsecops`, and evaluated by
`tools/compliance/sbom_conformance_gate.py`.

The concrete case that motivated the card is now covered: `{"bomFormat": "CycloneDX",
"specVersion": "1.4"}` clears all five pre-existing conditions — it was generated, is not
stale, neither failed nor was skipped, and can be signed and attested — and is blocked by the
new one, because an SBOM that lists no components meets no Component Data element at all.

Every number the gate applies lives in `args/security_gates.yaml` under
`sbom_conformance.thresholds` (`block_below_pct`, `warn_below_pct`, `require_components`).
The module carries **no default for any of them** and raises `SbomGateConfigError` when the
block is missing or incomplete, so the gate's strictness cannot silently become whatever a
Python literal happened to say. `tests/test_sbom_conformance_gate.py` covers both directions
against real documents, proves retuning the YAML retunes the decision, and asserts that
removing any single threshold raises rather than falls back.

Scoring is **not** this module's job. It imports sbx-sig-02's
`sbom_minimum_elements_validator` and delegates the moment that module is importable, with no
edit needed here on the day it merges; the interim structural check that keeps the gate from
being inert until then reports `scored_by: structural-interim` on every result so no caller
can mistake one for the other. Component Producer is delegated to
`component_producer.validate_sbom_producers`.

Measured against the generator's own output as of this writing, ICDEV scores **9 of 17**
data fields (52.94%) and is blocked — the eight gaps are precisely the elements the open
`sbx` tasks add: SBOM Author and Generation Context (sbx-fld-01), Author Signature
(sbx-sig-01), Hash Value and Hash Algorithm (sbx-fld-03), Component License (sbx-fld-04),
Component Identifiers (sbx-fld-05) and the Dependency Relationship graph (sbx-cov-02).
That is the gate working, not the gate misconfigured. Note that the conditions in this file
are declarative — no central engine evaluates the `block_on` lists automatically — so the
score above is what a caller of the gate sees today, not a CI failure.

**Also updated by sbx-prc-02.** Conformance is one half of the question; currency is the
other, and a document can be fully conformant and still describe a build that shipped three
commits ago. The freshness check now asks the per-build question first —
`sbom_not_regenerated_for_current_build` and `sbom_build_identity_unknown` joined
`sbd.warning`, the first also joined `swft.warning`, and `sbom_required_per_build: true`
sits beside `sbom_max_age_days` in both threshold blocks. See §2.9 and §3.3.

### 2.6 SPDX

### 2.6 SPDX and the CycloneDX default (sbx-fmt-01)

Baseline as analysed: there was **no SPDX generator and no SPDX parser** anywhere in the tree.
SPDX appeared only in assessor glob patterns, in third-party CI templates ICDEV emits, and in
GovCon proposal / knowledge-base seed content that states ICDEV produces SBOMs "in SPDX and
CycloneDX formats" — a customer-facing claim that was not true.

**Resolved for generation by sbx-fmt-01.** `tools/compliance/spdx_writer.py` (mirrored at
`icdev/tools/compliance/`) emits **SPDX 2.3 JSON**, reachable as
`sbom_generator.py --format spdx`. Ingest parity — parsing and validating a *received* SPDX
document instead of glob-matching its filename — remains **sbx-fmt-02**, and correcting the
customer-facing claim remains **sbx-gov-03**.

Four decisions carry the design:

1. **The SPDX document is derived from the CycloneDX document, not built beside it.** The
   acceptance criterion is that one project scores identically in both formats, and the only
   way to hold that as the remaining element tasks land is for there to be one producer of the
   elements and one translation of them. A second independent builder would drift the first
   time someone added a field to one of them. `compare_element_coverage()` is that criterion in
   executable form and is what sbx-sig-02 can call: it fails if either document makes an
   element statement the other does not.
2. **Native where SPDX has a field, annotation where it does not.** Component Producer becomes
   `originator` — not `supplier`, because SPDX's `supplier` is "the immediate supplier", which
   is exactly the ambiguity the 2026 standard removed when it replaced *Supplier Name*.
   Name, `versionInfo`, `licenseDeclared`, `checksums` and `externalRefs` (purl, CPE) likewise
   map natively, and the mappings for License and Hash Value/Algorithm are already in place so
   sbx-fld-03/04 reach both formats the day they land. ICDEV's `icdev:*` properties have no
   native home — SPDX 2.3 has no extension point equivalent to CycloneDX `properties` — so they
   travel losslessly in one `annotations` entry per element whose comment is a JSON object,
   which is SPDX's own mechanism for a statement the SBOM author makes about an element. The
   Coverage element's `compositions` array rides in the same annotation, because dropping it
   would make the SPDX document score lower on Coverage than the CycloneDX one.
3. **Relationships are translated, never invented.** Dependency edges come from the CycloneDX
   `dependencies` array (sbx-cov-02) and become SPDX `DEPENDS_ON` RELATIONSHIP entries;
   `DESCRIBES` is emitted because it is document structure. Until cov-02 lands, the CycloneDX
   document asserts no edges and so does the SPDX one — synthesizing a root-depends-on-
   everything graph on one side is precisely how the two would stop scoring identically.
4. **Validation is offline.** The official SPDX 2.3 JSON schema is vendored at
   `context/compliance/schemas/spdx-2.3.schema.json` (mirrored under `icdev/context/`), so
   `spdx_writer.py --validate` works in an air-gapped enclave. A missing `jsonschema` is
   reported as an error, never as a pass.

**Version choices.** SPDX **2.3**: ISO/IEC 5962:2021 standardizes SPDX 2.2.1, 2.3 is its
backward-compatible successor and is what current tooling reads, and SPDX 3.0's JSON-LD
serialization is not yet what consumers ingest — the standard asks for widely used formats.
CycloneDX default **1.7**, up from 1.4: the standard warns against deprecated versions of a
format and cites ECMA-424 of December 2025, and below 1.6 there is no `manufacturer` field, so
Component Producer had to travel in `supplier`. 1.4, 1.5 and 1.6 remain selectable via
`--spec-version` for consumers whose tooling has not caught up; all four validate against their
official CycloneDX schema.

**SWID.** Removed from the accepted format list in 2026. ICDEV has never emitted SWID, so
nothing had to be undone; `SUPPORTED_FORMATS` names only the two formats the standard does.

Tests live in `tests/test_sbom_spdx_format.py`: the generated SPDX validates against the
official schema, the same project generated twice scores identically across the two formats, a
field added to one document and not the other breaks the parity check, and the CycloneDX
default is asserted to be at least the version that can name a producer.

**Resolved (sbx-gov-03, 2026-08-08).** `sbx-fmt-01` had not landed on `main`, so the claim was
softened rather than made true. Every ICDEV capability and past-performance claim now states
**CycloneDX only**, matching what `tools/compliance/sbom_generator.py` actually emits:

| Site | Was | Now |
|---|---|---|
| `generate_icdev_proposal_content.py` (technical approach, ×2) | "at every build via Syft in SPDX and CycloneDX formats" | "at every build in CycloneDX format (spec 1.4-1.7)" |
| `generate_icdev_proposal_content.py` (past performance, ×2) | "SBOMs included SPDX and CycloneDX formats" | "SBOMs were delivered in CycloneDX format" |
| `seed_icdev_knowledge_base.py` (approach + past performance) | same as above; `spdx` retrieval keyword | CycloneDX only; `spdx` keyword removed so the record no longer surfaces on SPDX queries |

Three SPDX/Syft mentions were **deliberately retained** because they are not claims about ICDEV:

- `seed_solicitation_requirements.py:62,73` — seeded **solicitation** requirements ("The
  Contractor shall …"), i.e. what a customer *asks for*. These are demand-side text; a
  solicitation may legitimately require SPDX, and that is precisely the gap `sbx-fmt-01` closes.
  Editing them would corrupt the fixture and hide the gap.
- `synthetic_proposal_generator.py:224` — templated synthetic proposal text whose subject is a
  randomly generated fictional offeror (`_COMPANY_PREFIXES`/`_COMPANY_SUFFIXES`); ICDEV never
  appears as the company.

Note that ICDEV is not entirely Syft-free: `tools/network/airgap_bundle.py` shells out to `syft`
opportunistically when it is on `PATH`, but requests `-o cyclonedx-json` and falls back to a
minimal CycloneDX document otherwise. It never produces SPDX, and it is the air-gap bundler
rather than "every build" — so the corrected wording holds.

**When `sbx-fmt-01` lands, revisit this section**: the CycloneDX-only wording above becomes an
understatement, and the SPDX claim may be restored once `sbx-sig-02` verifies conformance on
both formats.

### 2.7 Coverage — resolved dependency sets (sbx-cov-01)

`tools/compliance/dependency_resolver.py` (mirrored at `icdev/tools/compliance/`) replaced
declared-manifest parsing as the generator's component source. Per ecosystem it reads the
**resolved** set, in precedence order:

| Ecosystem | Resolved source | Edges? |
|---|---|---|
| python | `uv.lock` → `poetry.lock` → `pdm.lock` → `Pipfile.lock` → installed environment via `importlib.metadata` over a venv's `site-packages` | yes, except `Pipfile.lock` |
| npm | `package-lock.json` (v1 and v2/v3) → `yarn.lock` (v1 text and Berry YAML) | yes |
| golang | `go.mod` with `go >= 1.17` (the pruned module graph lists every indirect module) → `go.sum` | no |
| cargo | `Cargo.lock` | yes |
| maven | `mvn dependency:list` output at `target/dependency-list.txt`, `target/dependencies.txt` or `dependency-list.txt` | no |
| gradle | `gradle.lockfile` / `gradle/dependency-locks/*.lockfile` | no |
| nuget | `obj/project.assets.json` → `packages.lock.json` | yes |

Three properties of the design matter more than the table:

1. **Offline-first.** Nothing shells out to a package manager — every source above is parsed
   with `json`, `tomllib`, `yaml.safe_load` or `importlib.metadata`. Resolution therefore
   behaves identically in an air-gapped enclave. The cost is that Maven and Gradle have no
   offline resolved form unless the project committed one, which is why both degrade.
2. **Degradation is stated, never silent.** Where the resolved set is unavailable, the
   ecosystem falls back to the generator's declared parsers and the SBOM carries an explicit
   incomplete-coverage statement — CycloneDX `compositions[].aggregate` (`complete` /
   `incomplete` / `unknown`, valid from spec 1.3) plus `icdev:sbom:coverage*` metadata
   properties naming each unresolved ecosystem and why. The statement closes with the reason
   this matters: a component's absence does **not** establish that the software is unaffected.
   A project with no manifest at all reports `unknown`, not `complete`.
3. **Instances, not names.** Components deduplicate on their full emitted metadata tuple
   rather than on `purl`, so two instances that differ in version *or* scope are listed
   separately with their own `bom-ref`. The nested-`node_modules` case is the concrete one:
   the previous `_parse_package_lock_json` skipped every nested entry, dropping installed
   components outright. That parser has been removed.

Per-ecosystem tests live in `tests/test_sbom_coverage_resolution.py`, each over a fixture
project whose transitive tree is known by construction. The agreed generation-time budget is
recorded there as `RESOLUTION_BUDGET_SECONDS`.

**Still open:** emitting the CycloneDX `dependencies` array from the edges the resolver now
collects is **sbx-cov-02**; artifact hashes, which resolution unlocks, are **sbx-fld-03**.

### 2.8 Component Producer (sbx-fld-02)

`tools/compliance/component_producer.py` (mirrored at `icdev/tools/compliance/`) resolves the
2026 **Component Producer** element for every component the generator emits. The element replaced
*Supplier Name* because "supplier" was ambiguous about distributors, so ICDEV states the entity
that **creates, defines and identifies** the component — exactly one organization each.

**Evidence first, registry second, explicit unknown last.** Per ecosystem:

| Ecosystem | Evidence, in precedence order |
|---|---|
| python | `*.dist-info`/`*.egg-info` `METADATA`: `Author` → `Maintainer` → the domain of `Author-email`/`Maintainer-email` through the registry |
| npm | the installed package's own `package.json`: `author` → `maintainers[0]` → `contributors[0]`, then the author e-mail domain |
| maven / gradle | the artifact's POM in the local repository: `<organization><name>` → a `<developers>` organization → the groupId read as reverse-DNS through the registry |
| golang | the module host path through the registry; a bare forge host is explicitly **not** a producer |
| cargo | the crate's `Cargo.toml` `[package] authors`, from a vendor directory or the Cargo registry source cache |
| nuget | the package's `.nuspec` `<authors>`, from the project's `packages/` directory or the global packages folder |

Four properties of the design matter more than the table:

1. **`group` is never the answer.** `org.apache.commons` is a Maven coordinate and `@types` is an
   npm scope; neither names an organization. No resolver reads `group` as a candidate, and
   `_reject_namespace_echo` drops a candidate that turns out to echo the component's own
   namespace — which is what `authors = ["com.acme"]` amounts to. `args/sbom_producer_registry.yaml`
   is what *maps* a namespace to an organization, and it maps only where the mapping is
   unambiguous.
2. **Offline-first, like sbx-cov-01.** "PyPI maintainer" and "crates.io owner" are registry-side
   facts, and querying a registry is exactly what an air-gapped build cannot do. The offline
   stand-in is the same fact obtained from the artifact: crates.io renders the crate's `authors`,
   PyPI renders the distribution's `Author`. Nothing shells out and nothing goes to the network.
3. **Unknown provenance is stated, never implied.** The standard says that where there is no clear
   indication the author *must* explicitly mark the component as being of unknown provenance.
   Every component therefore carries `icdev:component-producer` (a name, or the literal `unknown`),
   `icdev:component-provenance` (`known`/`unknown`) and, when unknown, a machine-readable
   `icdev:component-producer-unknown-reason` — `forge-host-is-not-a-producer`,
   `package-metadata-names-no-producer`, `namespace-maps-to-no-known-organization` and so on. There
   is no third outcome in which the element is simply absent. This is the *unknown* half of the
   convention sbx-prc-01 owns; ICDEV withholds nothing about a producer, so the *withheld* half
   does not arise here.
4. **The native CycloneDX field depends on the spec version.** CycloneDX only grew a field meaning
   "the organization that created the component" in 1.6 — `component.manufacturer`. Below that the
   nearest expressible field is `component.supplier`, whose ambiguity is the very thing the 2026
   standard set out to remove. So 1.6/1.7 get `manufacturer`, 1.4/1.5 get `supplier`, and the
   authoritative statement always travels in the `icdev:component-producer*` properties, which
   every supported version can carry.

The document's own target component is a component too, so it carries the element as well;
`ICDEV_SBOM_PRODUCER` names your organization and outranks the project manifest, because a legal
name is rarely what `pyproject.toml` says.

`validate_sbom_producers()` is the acceptance criterion in executable form — it fails a document
with a silent component, an unknown with no reason, or a producer equal to the component's `group`
— and is reachable as `component_producer.py --validate <sbom.cdx.json>`. sbx-sig-02's conformance
validator can call it as-is. Per-ecosystem tests live in `tests/test_sbom_component_producer.py`.

**Still open:** persisting the producer to `sbom_components.producer` (the column sbx-fnd-02 added)
waits on the `_persist_components` writer that sbx-fld-04 introduces — this task exports
`producer_db_value()`, which never returns `NULL`, for that writer to call in one line. Adding a
second writer here would only collide with it.

### 2.9 Frequency and Accommodation of Updates (sbx-prc-02)

Two practices, one mechanism, in `tools/compliance/sbom_revision.py`. Migration
`20260808063350_sbom_revision_frequency` adds the three columns the mechanism needs on top of the
`supersedes_sbom_id` that sbx-fnd-02 left for it: `content_digest`, `source_revision`,
`revision_reason`.

**The chain is append-only, and supersession is derived.** `apply_correction` inserts a
*successor* row whose `supersedes_sbom_id` points at the row it replaces, and appends an
`sbom_corrected` audit event. It issues no `UPDATE` and no `DELETE`. The corrected row keeps every
value it had — file path, signature, component count, version — because a recipient may hold that
exact document and its record has to keep describing it. There is deliberately **no `superseded`
column**: a row is superseded exactly when another row points at it, which
`revision_chain` computes at read time and marks there. `tests/test_sbom_revision_2026.py` asserts
both halves — the predecessor row compared field-by-field before and after, and every statement the
correction issues inspected for a mutation.

**Content digest, not file hash.** Every regeneration mints a fresh `serialNumber`, timestamp and
SBOM Version, so the file hash of two SBOMs of one unchanged tree never matches.
`content_digest` strips exactly those fields and digests the rest, which is what distinguishes a
substantive **revision** (new or corrected component data) from a **re-issue** (same bill of
materials, new build). Frequency requires a new SBOM for both; `revision_reason` records which
happened — `initial`, `new_build`, `dependency_change`, `correction`, `detail_discovered`. That
vocabulary is a Python constant with no DDL `CHECK` behind it, for the reason sbx-fnd-02 gave for
`sbom_dependencies.relationship_type`.

**Version bump.** The per-build bump stays in the generator (sbx-fld-01's). A correction is a
*patch* bump of an already-published version rather than a new revision of the software, so
`next_sbom_version(prior, correction=True)` owns that one case, and reads both the semver
`1.<minor>.<patch>` spelling and the legacy `<N>.0` float so it works either side of that merge.

**The reconciliation.** CLAUDE.md asserted "SBOM regenerated on every build" while the only
enforced rule was a 30-day file-mtime threshold, and the weaker rule was the one with teeth — six
releases in a fortnight off one SBOM passed. Per-build conformance is not checkable without
recording which build each SBOM came from, hence `source_revision` (explicit `--build-id`, then
`$ICDEV_BUILD_ID`, then the project directory's git commit, then **unknown and reported as such**).
`evaluate_frequency` answers the build question first and the age question second; `sbd_assessor`'s
SBD-21 now calls it, and its own requirement text already demanded both rules.

Two defects in that check were fixed in passing. It subtracted a naive `utcfromtimestamp` from an
aware `now` — a `TypeError` on every file, swallowed by `except Exception`, which put every SBOM in
`stale_files` with age `-1`. SBD-21 could not return `satisfied` at all. And the 30-day threshold
was a literal in the function while `sbom_max_age_days` sat in the YAML, free to drift; the
config value now applies, read through `gate_threshold`, which tries both nesting shapes the file
uses (`thresholds.sbd.*` and `swft.thresholds.*`) rather than guessing one and silently returning
its own default.

`sbom_revised` and `sbom_corrected` were also added to `VALID_EVENT_TYPES`, with migration
`20260808064841_audit_event_types_sbom_revision` rebuilding `audit_trail`'s generated CHECK.
Without it the correction event is rejected by the constraint, swallowed by the caller's `except`,
and the correction looks recorded while nothing was written.

### 2.10 Explicitly Identifying Unknown Information (sbx-prc-01)

`tools/compliance/unknown_information.py` (mirrored at `icdev/tools/compliance/`) defines the one
convention for the two states the 2026 element **separates**: a field **unknown to the author**
versus a field **withheld by the author**. ICDEV wrote both as the literal `"unspecified"` (and
`"managed"` for a Maven version held by a parent POM), which says neither.

**The convention, applied uniformly to all 17 data-field elements:**

1. **An in-band sentinel** in the native field — `unknown` or `withheld` — so a schema that
   requires the field still validates and no plausible-looking value stands in for one that was
   never established.
2. **An out-of-band property per undisclosed field**, which is the authoritative statement:
   `icdev:unknown:<field>` or `icdev:withheld:<field>`, whose value is a machine-readable reason
   code. The *name* carries the state and the *value* carries the why, so a reader that
   understands only the two prefixes already has the distinction the standard asks for. Fields
   whose CycloneDX carrier is not a plain string (`hashes`, `licenses`, `dependencies`) have no
   sentinel and live only here — which is why the properties, not the sentinel, are authoritative.
3. **Disjoint reason vocabularies.** `UNKNOWN_REASONS` and `WITHHELD_REASONS` share no member, so
   a reason code identifies its own state and a withheld reason filed under the unknown prefix is
   a structural error rather than a matter of discipline. `Disclosure.unknown()` raises on a
   withheld reason and `.withheld()` raises on an unknown one, so the mistake cannot be made at
   authoring time either. A field is in at most one state: recording either evicts the other.

Four properties of the design matter more than the mechanics:

1. **Unknown is discovered; withheld is declared.** An unknown is a fact found at generation time
   — nobody can configure a field into being unknown. A withholding is an operator decision in
   `args/sbom_disclosure_policy.yaml`. A rule whose field or reason is unrecognised is **dropped,
   not defaulted**, and `--policy` reports every dropped rule and exits non-zero: guessing which
   redaction category the operator meant is the invention this element exists to prevent. An
   unmatched `match` key is a non-match, never a wildcard, because a redaction that accidentally
   covered the whole tree is worse than one covering nothing — only the second is visible on
   inspection.
2. **The enquiry route rides with the markings that make it necessary.** ICDEV emits
   `CUI // SP-CTI` and `Distribution D`; those *are* the withholding case, so
   `icdev:sbom:enquiry-*` is emitted next to them on **every** document, not only when a field is
   withheld. Withholding anything without it is a validation error, and a missing or corrupt
   policy file degrades to a default that withholds nothing and still names a route — the route
   can never come back empty.
3. **Completeness is stated, and unknowns never affect it.** `icdev:sbom:disclosure-completeness`
   is `incomplete-withheld` when one of the essential component fields (`producer`, `name`,
   `version`, `identifiers`, `license`) is withheld — the standard's "may be considered
   incomplete" sentence made machine-readable. `hash_value` is deliberately not essential: the
   standard's own example of legitimately absent data is an author who cannot reach the artifact.
   The document totals are two properties, `icdev:sbom:fields-unknown` and
   `icdev:sbom:fields-withheld`; a single number would re-create the conflation the split removes.
   This is a different property from `icdev:sbom:coverage` — Coverage is which components are
   listed, this is which fields on a listed component are disclosed.
4. **The distinction survives SPDX.** SPDX has one marker and no way to say "withheld", so both
   states map to `NOASSERTION` in the native field and the state moves into an annotation whose
   wording is fixed on the words `UNKNOWN`/`WITHHELD`. `spdx_mapping()` renders it for
   sbx-fmt-01's writer to emit rather than re-derive.

The Component Producer element joins the convention rather than keeping a private marker:
sbx-fld-02's `icdev:component-producer*` properties are untouched (its finer-grained reason
survives as the unknown's detail) and the component *additionally* states
`icdev:unknown:producer`, so one validator reads every undisclosed field of every element from one
pair of prefixes.

`validate_sbom_disclosure()` is the acceptance criterion in executable form — it fails a document
on a cross-filed reason, a field in both states, a bare sentinel, a sentinel disagreeing with its
property, a surviving `unspecified`/`managed`, an unrecognised field name, a withholding with no
enquiry process, a false completeness claim, and a detail property that explains a redaction — and
is reachable as `unknown_information.py --validate <sbom.cdx.json>`. Its summary keeps
`fields_unknown` and `fields_withheld` as two numbers that are never added. Tests live in
`tests/test_sbom_unknown_information.py`; the feature doc, including the recipient enquiry process
itself, is [docs/features/sbom-2026-unknown-vs-withheld.md](../features/sbom-2026-unknown-vs-withheld.md).

**Still open:** persisting to `sbom_components.unknown_fields_json` / `withheld_fields_json` waits
on sbx-fld-04's `_persist_components`, exactly as the producer does — `Disclosure.db_values()` is
exported for that writer.

---

---

## 3. Conformance matrix

Legend: **MET** — emitted correctly today · **PARTIAL** — present but non-conforming ·
**GAP** — absent.

### 3.1 SBOM Metadata

| Element | Status | Evidence / what is missing |
|---|---|---|
| SBOM Author | **MET (sbx-fld-01)** | `metadata.authors[0].name`, plus the `icdev:sbom:author` property that carries the element into SPDX as an `Organization:` creator. Resolved from `--author`, then `$ICDEV_SBOM_AUTHOR`, then a full-name default. `metadata.tools[].vendor` is untouched and still means the tool's vendor — the two are separate statements. |
| SBOM Author Signature | **MET** (sbx-sig-01) | `sbom_signer.sign_sbom` writes a detached `<sbom>.sig.json` over the canonicalized document and persists `author_signature` + `signature_algorithm`. FIPS 186-5 algorithms only (ECDSA P-256/384/521, Ed25519); HMAC and empty signatures refused. Offline both ways. See §2.3. |
| SBOM Data Format Name | **MET** | `bomFormat: "CycloneDX"`, or `spdxVersion` naming SPDX. |
| SBOM Data Format Version | **MET (sbx-fmt-01)** | `specVersion` defaults to **1.7** (ECMA-424, Dec 2025) rather than the 2022 spec 1.4; 1.4-1.6 stay selectable for lagging consumers. SPDX emits `SPDX-2.3`. See §2.6. |
| SBOM Generation Context | **MET (sbx-fld-01)** | `icdev:sbom-generation-context = "before build"` on every document — this generator reads source manifests and never opens a built artifact — plus `metadata.lifecycles = [{phase: "pre-build"}]` on 1.5+. Both vocabularies, not redundancy: only the property carries the standard's own term, and 1.4 has no `lifecycles` field to hold it. |
| SBOM Timestamp | **MET (sbx-fld-01)** | `_rfc9557_timestamp()` — the RFC 3339 profile RFC 9557 extends, which is what CycloneDX's `format: date-time` and SPDX 2.3's `created` both accept; the `[UTC]` suffix form is deliberately not used because it would fail both. A naive datetime now raises instead of being stamped `Z`, and a non-UTC one is converted rather than relabelled. |
| SBOM Tool Name | **MET** | `metadata.tools[].name = "icdev-sbom-generator"`. |
| SBOM Tool Version | **MET (sbx-fld-01)** | Derived from `icdev._version`, then installed distribution metadata, then `pyproject.toml`, then the literal `"unknown"` the standard requires when the version is unavailable. The `"1.0.0"` constant is gone from both copies and an AST check keeps it gone. |
| SBOM Version | **MET (sbx-fld-01)** | One counter, two spellings, settled before the document is built. CycloneDX `version` carries it as 1, 2, 3…; `icdev:sbom:version` and `sbom_records.sbom_version` carry it as semver `1.<N-1>.0` — major pinned to 1 per the standard, minor counting content revisions, patch reserved for corrections (sbx-prc-02). `sbom_records.version` remains the legacy `"N.0"` spelling of the same N. Legacy float rows still parse to their revision, so an existing project continues its count rather than restarting. Serial numbers are `uuid4`, the random UUID of RFC 9562 §5.4. |

### 3.2 Component Data

| Element | Status | Evidence / what is missing |
|---|---|---|
| Component Producer | **MET (sbx-fld-02)** | `tools/compliance/component_producer.py` resolves the producing organization per ecosystem from the package's own metadata, maps a Go host path or a reverse-DNS groupId through `args/sbom_producer_registry.yaml`, and marks anything left over as being of unknown provenance. `group` is never a candidate. See §2.8. |
| Component Dependency Relationship | **GAP** | The SBOM is a **flat component list**. No CycloneDX `dependencies` array is emitted, so no dependency graph can be built from ICDEV output. |
| Component Hash Value | **GAP** | The generator reads manifests, never artifacts, so no hash is computable on the current design. |
| Component Hash Algorithm | **GAP** | Consequent to the above. |
| Component Identifiers | **PARTIAL** | `purl` only. No CPE (needed for NVD lookup), no UUID / commit hash / SWHID / OmniBOR, and no support for carrying multiple identifiers. |
| Component License | **MET (sbx-fld-04)** | `tools/compliance/component_licenser.py` emits the element for every component and for the target component, in one of the four shapes the standard allows — a validated SPDX expression, a URL to full terms, a license name, or an explicit unknown/withheld marker — plus a tri-state proprietary-conditions flag. Never omitted. `sbom_components.license` is populated by the same resolution. See §3.2.1. |
| Component Name | **PARTIAL** | Single name only; the standard requires formats to allow alternate names. |
| Component Version | **PARTIAL** | The conflating literals are **gone (sbx-prc-01)**: an unresolved version is now the `unknown` sentinel plus `icdev:unknown:version` naming why (`declared-without-a-version`, `version-managed-by-parent`, `not-provided-by-producer`), and the purl no longer claims a version segment it does not have. What remains for the element itself is stating the version the *producer* assigned where ICDEV currently reports a resolver's normalization. |

#### 3.2.1 Component License — what sbx-fld-04 landed

`tools/compliance/component_licenser.py` (mirrored to `icdev/tools/compliance/`) resolves
the element for every component, and `tools/compliance/spdx_license_data.py` carries the
698 SPDX license identifiers and 88 exception identifiers it validates against.

- **Emitted for every component without exception**, as CycloneDX `licenses` plus the
  `icdev:component-license*` properties. The properties are what make the element
  unomittable: CycloneDX has no way to spell "unknown" inside `licenses`, so an
  undisclosed license leaves that array empty and states itself in
  `icdev:component-license`. `icdev:component-license-proprietary` is always present as
  `true`, `false` or `unknown`.
- **Only validated SPDX identifiers are emitted as identifiers.** A declaration that is
  not a well-formed expression over the SPDX List — `Apache License 2.0`, a misspelling,
  an invented id, a license used where an exception belongs — is carried through as a
  license *name* or a URL instead. Nothing is dropped, and nothing the recipient cannot
  resolve is presented as resolvable. Expressions are parsed with the ISO/IEC 5962
  Annex D grammar and canonicalized, so a manifest writing `mit` yields `MIT`.
- **The proprietary flag is tri-state and set only from positive evidence**: npm
  `UNLICENSED` / `SEE LICENSE IN <file>`, an SPDX `LicenseRef-`/`DocumentRef-` custom
  reference, the `NONE` keyword, or proprietary vocabulary in the declared text. `false`
  means "no conditions the recipient cannot look up", *not* "open source" — a
  source-available licence with an SPDX id (BUSL-1.1, SSPL-1.0) flags `false` because its
  terms are published and identified, which is what the element is for. Where it cannot
  be determined (a bare URL, a bare name) it is stated as `unknown` rather than defaulted
  to `false`, which would assert the absence of conditions nobody checked for.
- **The list is vendored, not fetched and not imported.** ICDEV runs air-gapped, and an
  SBOM generator whose notion of a valid license changes with an undeclared transitive
  dependency's version is the "incorrect license information" failure the standard calls a
  risk-management problem. Validation is allow-list only, so a stale list can only
  downgrade an id to a name — never emit an unvalidated id.
- **Unknown and withheld go through sbx-prc-01, not a marker of this element's own.** An
  absent license records `FIELD_LICENSE` on the component's `Disclosure` with a reason
  from the closed `UNKNOWN_REASONS` vocabulary; the finer-grained local reason
  (`license-not-declared` and friends) travels as the disclosure *detail*, so precision
  survives without a second vocabulary a recipient's tooling would not recognise. This
  module emits **no** unknown-reason property of its own. Which shared reason applies
  depends on where the component came from: reading a package's real metadata and finding
  no license means the producer published none (`not-provided-by-producer`), while a
  `requirements.txt` line means nothing about the package at all — that format cannot
  carry a license, so the value is one an offline build cannot reach
  (`not-resolvable-offline`). A license the operator's disclosure policy **withholds**
  suppresses the `licenses` array, the declaration shape, the evidence and the URL, and
  reduces to the `withheld` sentinel: leaving any of those in place would publish exactly
  what was withheld.
- **`sbom_components` is now written, and this is its single writer.** The generator had
  never written that table, which is why `license` sat dead since migration 209. Rows are
  built from the **finished** CycloneDX components rather than re-resolved from the parsed
  input, so the table cannot become a second, drifting opinion about the element — the
  same dedup, the same policy and the same resolution produced both. The row id is the
  component's `bom-ref`, so a row and its document entry correlate by identity and
  regeneration updates rather than accumulates. `license`, `producer` and the
  `unknown_fields_json` / `withheld_fields_json` blobs are all written from that one row.
- **The target component is covered too.** Its license is read from the project's own
  manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, `pom.xml`), so ICDEV's own SBOM
  reports `Apache-2.0` rather than unknown.
- **Coverage today.** A real run over this repo against its installed Python environment
  and `package-lock.json` yields **558 components: 399 with a validated SPDX expression**
  (`MIT` 223, `Apache-2.0` 61, `BSD-3-Clause` 43, `ISC` 35, …), **147 carried as license
  names** — `MIT License`, `BSD License`, `Apache 2.0` and other trove-classifier or
  free-text spellings that are deliberately *not* laundered into SPDX ids — **and 12 with
  the explicit unknown marker. Zero omitted, zero NULL or empty**, and zero invalid SPDX
  identifiers emitted. `dependency_resolver` reads `License-Expression`, `License` and the
  `License ::` trove classifier from each installed distribution's `*.dist-info/METADATA`
  in PEP 639 order, which is the only place an air-gapped build can learn a Python
  package's license; a `License:` field that is multi-line or over 120 characters is
  discarded rather than carried, because several distributions paste their entire license
  body into it.

Pinned by `tests/test_sbom_component_license.py` (107 tests), which runs the real
generator against a real database and asserts the rows land with populated licenses, and
by `tests/pg_tier/test_sbom_component_license_pg.py` against a live PostgreSQL.

### 3.3 Practices and Processes

| Element | Status | Evidence / what is missing |
|---|---|---|
| Accommodation of Updates | **MET (sbx-prc-02)** | `tools/compliance/sbom_revision.py::apply_correction` records a correction as a **successor** row carrying `supersedes_sbom_id`, plus an `sbom_corrected` audit event. The corrected row is never written to — supersession is derived at read time by `revision_chain`. See §2.9. |
| Coverage | **MET (sbx-cov-01)** | `tools/compliance/dependency_resolver.py` resolves each ecosystem from its **lockfile**, not its declared manifest, and the generator consumes that instead of parsing manifests itself. See §2.7. |
| Distribution and Delivery | **PARTIAL** | Writes a file to disk and records a path. No version-specific URL and no retrieval API. |
| Explicitly Identifying Unknown Information | **MET (sbx-prc-01)** | `tools/compliance/unknown_information.py` defines one convention for both states across all 17 elements — sentinel plus `icdev:unknown:<field>` / `icdev:withheld:<field>` properties over two **disjoint** reason vocabularies — and the generator emits it. The recipient enquiry route ships in `args/sbom_disclosure_policy.yaml` and is emitted on every document beside the CUI and distribution markings. See §2.10. |
| Frequency | **MET (sbx-prc-02)** | Every generation appends a linked row; `sbom_records.source_revision` records the build, and `sbom_revision.evaluate_frequency` — which SBD-21 now calls — answers the per-build question first and treats `sbom_max_age_days` as the stale-evidence backstop. CLAUDE.md and `args/security_gates.yaml` now say the same thing. See §2.9. |
| Machine-Processable Data | **MET for generation (sbx-fmt-01)** | Both named formats are emitted — CycloneDX 1.4-1.7 (default 1.7) and SPDX 2.3 — and the two carry identical elements by construction. No SWID, which the 2026 removal makes correct. **Ingest** still glob-matches filenames rather than parsing (sbx-fmt-02). See §2.6. |
| Access Control (removed) | **N/A** | ICDEV's CUI classification properties remain appropriate under Distribution and Delivery. |

**Baseline score, as analysed before any `sbx` task landed: 3 of 17 data-field elements fully
met** (SBOM Data Format Name, SBOM Tool Name, Component Name is partial — counting strictly, 2
fully met plus 7 partial), **0 of 7 practices fully met.**

**Current: 5 of 17 data-field elements** — Component Producer joined them with
sbx-fld-02, SBOM Author Signature with sbx-sig-01 and SBOM Data Format Version with
sbx-fmt-01 — **and 5 of 7 practices**: Coverage (sbx-cov-01), Frequency plus
Accommodation of Updates (sbx-prc-02), Explicitly Identifying Unknown Information
(sbx-prc-01), and Machine-Processable Data for generation (sbx-fmt-01, whose ingest
half is sbx-fmt-02). The matrix rows above are kept current as each task lands, so
they, not this paragraph, are the authoritative statement.

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
