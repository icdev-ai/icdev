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

### 2.6 SPDX

There is **no SPDX generator and no SPDX parser** anywhere in the tree. SPDX appears only in
assessor glob patterns, in third-party CI templates ICDEV emits, and in GovCon proposal /
knowledge-base seed content that states ICDEV produces SBOMs "in SPDX and CycloneDX formats"
(`tools/govcon/generate_icdev_proposal_content.py`,
`tools/govcon/seed_icdev_knowledge_base.py`, `tools/govcon/seed_solicitation_requirements.py`).
That claim is not currently true and is customer-facing.

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

---

---

## 3. Conformance matrix

Legend: **MET** — emitted correctly today · **PARTIAL** — present but non-conforming ·
**GAP** — absent.

### 3.1 SBOM Metadata

| Element | Status | Evidence / what is missing |
|---|---|---|
| SBOM Author | **GAP** | No author field. `metadata.tools[].vendor = "ICDEV™"` identifies the tool vendor, which the standard explicitly says is *not* the author. |
| SBOM Author Signature | **MET** (sbx-sig-01) | `sbom_signer.sign_sbom` writes a detached `<sbom>.sig.json` over the canonicalized document and persists `author_signature` + `signature_algorithm`. FIPS 186-5 algorithms only (ECDSA P-256/384/521, Ed25519); HMAC and empty signatures refused. Offline both ways. See §2.3. |
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
| Component Producer | **MET (sbx-fld-02)** | `tools/compliance/component_producer.py` resolves the producing organization per ecosystem from the package's own metadata, maps a Go host path or a reverse-DNS groupId through `args/sbom_producer_registry.yaml`, and marks anything left over as being of unknown provenance. `group` is never a candidate. See §2.8. |
| Component Dependency Relationship | **GAP** | The SBOM is a **flat component list**. No CycloneDX `dependencies` array is emitted, so no dependency graph can be built from ICDEV output. |
| Component Hash Value | **GAP** | The generator reads manifests, never artifacts, so no hash is computable on the current design. |
| Component Hash Algorithm | **GAP** | Consequent to the above. |
| Component Identifiers | **PARTIAL** | `purl` only. No CPE (needed for NVD lookup), no UUID / commit hash / SWHID / OmniBOR, and no support for carrying multiple identifiers. |
| Component License | **GAP** | Not emitted, though `sbom_components.license` already exists in schema. |
| Component Name | **PARTIAL** | Single name only; the standard requires formats to allow alternate names. |
| Component Version | **PARTIAL** | Unresolved versions are written as the literal strings `"unspecified"` (most parsers) and `"managed"` (Maven). These are not machine-interpretable unknown markers and collide with the Explicitly Identifying Unknown Information element. |

### 3.3 Practices and Processes

| Element | Status | Evidence / what is missing |
|---|---|---|
| Accommodation of Updates | **PARTIAL** | `sbom_records` versions rows, but there is no correction/revision workflow and no way to mark a prior SBOM superseded. |
| Coverage | **MET (sbx-cov-01)** | `tools/compliance/dependency_resolver.py` resolves each ecosystem from its **lockfile**, not its declared manifest, and the generator consumes that instead of parsing manifests itself. See §2.7. |
| Distribution and Delivery | **PARTIAL** | Writes a file to disk and records a path. No version-specific URL and no retrieval API. |
| Explicitly Identifying Unknown Information | **GAP** | No unknown/withheld distinction anywhere; `"unspecified"` conflates them and is not machine-processable. No documented process for recipients to query redactions. |
| Frequency | **PARTIAL** | `CLAUDE.md` asserts "SBOM regenerated on every build"; the enforced gate is a **30-day staleness** threshold, which is materially weaker than per-release. |
| Machine-Processable Data | **PARTIAL** | CycloneDX yes; **SPDX absent** although the standard names both. No SWID emitted, which the 2026 removal makes correct by accident. |
| Access Control (removed) | **N/A** | ICDEV's CUI classification properties remain appropriate under Distribution and Delivery. |

**Baseline score, as analysed before any `sbx` task landed: 3 of 17 data-field elements fully
met** (SBOM Data Format Name, SBOM Tool Name, Component Name is partial — counting strictly, 2
fully met plus 7 partial), **0 of 7 practices fully met.**

**Current: 4 of 17 data-field elements** — Component Producer joined them with sbx-fld-02 — **and
1 of 7 practices**, Coverage, with sbx-cov-01. The matrix rows above are kept current as each task
lands, so they, not this paragraph, are the authoritative statement.

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
