#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the SBX card - SBOM 2026 Minimum Elements conformance.

Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)",
CISA with NSA, FBI and 16 international partners, published 2026-07-29
(document version 2.1, TLP:CLEAR). It REPLACES the 2021 NTIA minimum elements.

Gap analysis: docs/compliance/sbom-2026-minimum-elements-gap-analysis.md

MANUAL-ONLY card. sbx-gate-00 is seeded at ``in_progress`` and every other task
carries ``depends_on_task_id="sbx-gate-00"``, so
``promote_backlog_to_scheduled._deps_satisfied()`` returns False and the runner
cannot dispatch any of them. Release the epic by setting the gate to ``done``.

Usage:
    python tools/kanban/seed_sbx_sbom2026.py --json
    python tools/kanban/seed_sbx_sbom2026.py --dry-run --json
"""

import argparse
import json
import sys

GATE = "sbx-gate-00"
DOC = "docs/compliance/sbom-2026-minimum-elements-gap-analysis.md"


def _tasks() -> list[dict]:
    """Return the full task spec list, gate first."""
    return [
        {
            "id": GATE,
            "title": "SBX MANUAL GATE - hold, do not dispatch",
            "task_type": "chore",
            "priority": "critical",
            "status": "in_progress",
            "description": (
                "RISK: SBX rewrites the SBOM and conformance evidence path; letting the "
                "runner build it unattended ships compliance artefacts no human decided to "
                "ship.\n\n"
                "Manual hold for the SBX card. Every sbx-* task depends on this one, so "
                "while it sits at in_progress the kanban runner cannot promote or dispatch "
                "any of them. Do NOT move this to done without an explicit decision to let "
                "the runner build the card autonomously.\n\n"
                "Hazard: /start's reset moves in_progress tasks back to backlog, which "
                "releases this gate and unblocks every dependent task. Re-hold it after "
                "any /start.\n\n"
                f"Card context and the full conformance analysis: {DOC}"
            ),
            "acceptance_criteria": (
                "Remains in_progress until a human decides to release the card. "
                "Verify the hold with promote_backlog_to_scheduled._deps_satisfied() "
                "returning False for every sibling task."
            ),
        },
        # ---- FND: foundation -------------------------------------------------
        {
            "id": "sbx-fnd-01",
            "title": "Resolve the sbom_generator deprecation contradiction",
            "priority": "critical",
            "description": (
                "tools/compliance/sbom_generator.py line 3 reads "
                "'# DEPRECATED: unused as of 2026-05-09. Remove after 2026-08-01.' "
                "That removal date has passed, the claim 'unused' is false, and the module "
                "is the ONLY SBOM producer in the tree. Roughly 25 live call sites depend "
                "on it, including: tools/ci/workflows/icdev_comply.py, MCP sbom_generate "
                "(tools/mcp/tool_registry.py), tools/dashboard/api/batch.py, "
                "tools/govcon/cdrl_generator.py, tools/testing/production_audit.py and "
                "production_remediate.py, tools/infra/ironbank_metadata_generator.py, "
                "tools/studio/workflow_editor.py, tools/compliance/fedramp_ksi_generator.py, "
                "fedramp_authorization_packager.py, owasp_llm_assessor.py, "
                "swft_evidence_bundler.py, and args/bdc_canvas_config.yaml where it backs a "
                "BLOCKING gate.\n\n"
                "Decide and record that it is canonical, then strip the false marker and "
                "update tools/manifest/compliance-engine.md, which currently lists it as "
                "'SBOM Generator [DEPRECATED]'.\n\n"
                "Separately: tools/sbom/sbom_generator.py DOES NOT EXIST but is cited as a "
                "real path in tools/ace/roles/compliance_manager/TOOLS.md, "
                "compliance_manager/SOUL.md and devops_engineer/TOOLS.md. Fix those to the "
                "real path.\n\n"
                "Mirror any change to icdev/tools/compliance/sbom_generator.py - the "
                "canonical/legacy split means mirror-only or root-only authoring drifts.\n\n"
                f"Analysis: {DOC} section 2.1"
            ),
            "acceptance_criteria": (
                "grep for 'tools/sbom/' returns no hits under tools/ace/. The manifest shard "
                "no longer marks the generator DEPRECATED. The module carries no removal-date "
                "comment. A test asserts both tools.compliance.sbom_generator and "
                "icdev.tools.compliance.sbom_generator import and expose generate_sbom."
            ),
        },
        {
            "id": "sbx-fnd-02",
            "title": "Migration - storage for the 2026 SBOM elements",
            "priority": "critical",
            "description": (
                "Give the new elements somewhere to live. This MUST be a NEW numbered "
                "migration under tools/db/migrations/ with both @sqlite-only and @pg-only "
                "blocks - never an edit to CREATE TABLE in tools/db/init_icdev_db.py, per the "
                "INSERT/schema-parity rule (CREATE TABLE IF NOT EXISTS never alters an "
                "existing table, so editing the DDL silently does nothing on a live DB).\n\n"
                "sbom_records today: project_id, version, format, file_path, component_count, "
                "vulnerability_count, generated_at. Needs: sbom_author, author_signature, "
                "signature_algorithm, data_format_name, data_format_version, "
                "generation_context, tool_name, tool_version, sbom_version, serial_number, "
                "supersedes_sbom_id.\n\n"
                "sbom_components (migration 209) today: component_name, version, vendor, "
                "component_type, purl, license, classification. Needs: producer, hash_value, "
                "hash_algorithm, identifiers_json, unknown_fields_json, withheld_fields_json, "
                "and a dependency edge (either a parent ref column or a companion "
                "sbom_dependencies table - prefer the latter, a component can have many "
                "parents). NOTE: the existing license and vendor columns are currently DEAD - "
                "the generator has never written a single sbom_components row. Reuse them "
                "rather than adding duplicates.\n\n"
                "Also decide whether sbom_records needs RLS columns; sbom_components already "
                "has classification but sbom_records has none.\n\n"
                "Add the new/changed table shapes to MINIMAL_ICDEV_SCHEMA in tests/conftest.py."
            ),
            "acceptance_criteria": (
                "Migration applies cleanly on PostgreSQL and on SQLite. "
                "coherence_checker.py:check_insert_schema_parity passes. tests/conftest.py "
                "MINIMAL_ICDEV_SCHEMA updated. A test writes and reads back one row per new "
                "column against the live schema, not the DDL."
            ),
        },
        # ---- FLD: the 17 data fields ----------------------------------------
        {
            "id": "sbx-fld-01",
            "title": "SBOM Metadata block - the 9 document-level elements",
            "priority": "high",
            "description": (
                "Emit the SBOM Metadata half of the minimum elements in "
                "_build_cyclonedx_sbom().\n\n"
                "- SBOM Author (GAP): the ENTITY that generated the SBOM, explicitly not the "
                "tool. metadata.tools[].vendor = 'ICDEV' is the tool vendor and does not "
                "satisfy this. Full names, no acronyms unless official. Distinct from "
                "Component Producer, though they may be identical.\n"
                "- SBOM Data Format Name (MET): bomFormat already correct.\n"
                "- SBOM Data Format Version (PARTIAL): specVersion is emitted but defaults to "
                "1.4 (2022). The standard cites ECMA-424 (Dec 2025) and says deprecated "
                "versions should not be used - raise the default, keep 1.4-1.7 selectable.\n"
                "- SBOM Generation Context (GAP): lifecycle phase. ICDEV parses source "
                "manifests, so today it is 'before build' - that is knowable now and simply "
                "unstated. Use CycloneDX metadata.lifecycles.\n"
                "- SBOM Timestamp (PARTIAL): currently '%Y-%m-%dT%H:%M:%SZ'. Must conform to "
                "RFC 9557; add a test that parses it.\n"
                "- SBOM Tool Name (MET).\n"
                "- SBOM Tool Version (PARTIAL): HARDCODED '1.0.0'. It is a constant that will "
                "never change and therefore misidentifies the code delivery. Derive it.\n"
                "- SBOM Version (PARTIAL): the document always carries \"version\": 1 while "
                "sbom_records.version independently counts 1.0, 2.0, 3.0. The two disagree. "
                "Reconcile them; if using semver the major version should be 1, with "
                "minor/patch for content changes; serial numbers should follow RFC 9562.\n\n"
                f"Analysis: {DOC} sections 1.1 and 3.1"
            ),
            "acceptance_criteria": (
                "sbx-sig-02 validator scores all 9 metadata elements as met. A test asserts "
                "the emitted tool version equals the package version and is not the literal "
                "'1.0.0' constant. A test parses the timestamp under RFC 9557. The document "
                "SBOM Version and the sbom_records row agree."
            ),
        },
        {
            "id": "sbx-fld-02",
            "title": "Component Producer - replaces Supplier Name",
            "priority": "high",
            "description": (
                "The 2026 standard REPLACES Supplier Name with Component Producer because "
                "'supplier' proved ambiguous around distributors. ICDEV emits neither - there "
                "is no supplier field at all. CycloneDX 'group' is a Maven/npm namespace, not "
                "a producer, and must not be used as one.\n\n"
                "Definition: the entity that creates, defines and identifies the component. "
                "Exactly ONE organization per component. Full names, no acronyms unless "
                "official.\n\n"
                "Resolve per ecosystem: PyPI Author/Maintainer metadata, npm author field, "
                "Maven groupId to organization, Go module host path, crates.io owner, NuGet "
                "authors. For open source with no clear producer, the standard says use the "
                "original project or maintaining organization; if there is still no clear "
                "indication, the author MUST explicitly mark the component as being of "
                "unknown provenance - silence is not acceptable. That marker depends on "
                "sbx-prc-01.\n\n"
                f"Analysis: {DOC} section 3.2"
            ),
            "acceptance_criteria": (
                "Every component in a generated SBOM carries either a Component Producer or "
                "an explicit unknown-provenance marker. Per-ecosystem resolution has a test "
                "each. Producer is never populated from 'group'."
            ),
        },
        {
            "id": "sbx-fld-03",
            "title": "Component Hash Value and Hash Algorithm",
            "priority": "high",
            "description": (
                "Both are NEW elements in 2026 and both are absent. The generator reads "
                "dependency manifests and never touches an artifact, so no hash is computable "
                "on the current design - this task therefore depends on sbx-cov-01, whose "
                "resolution work is what yields artifact locations.\n\n"
                "Component Hash Value: ASCII, hexadecimal-encoded output of a cryptographic "
                "hash over the EXECUTABLE COMPONENT ARTIFACT. If the artifact is not "
                "accessible, mark the value unknown - do not omit the field.\n\n"
                "Component Hash Algorithm: name it using IANA Hash Function Textual Names, "
                "and use an algorithm approved by a relevant authority such as NIST. The repo "
                "rule already mandates sha256 over md5.\n\n"
                "Digest sources worth preferring over recomputation only where trustworthy: "
                "wheel/sdist RECORD digests, package-lock integrity fields, go.sum, "
                "Cargo.lock checksum, Maven .sha1 sidecars. Prefer recomputing from the local "
                "artifact where one is present.\n\n"
                f"Analysis: {DOC} section 3.2"
            ),
            "acceptance_criteria": (
                "Every component carries a hash value plus algorithm, or an explicit unknown "
                "marker with a reason. Algorithm strings validate against the IANA Hash "
                "Function Textual Names registry. A test covers the artifact-inaccessible path."
            ),
        },
        {
            "id": "sbx-fld-04",
            "title": "Component License",
            "priority": "high",
            "description": (
                "NEW element in 2026, entirely absent from generated output - although "
                "sbom_components.license already exists in the schema and has never been "
                "written to.\n\n"
                "Capture the identifier(s) for the license(s) the component is available "
                "under. Use SPDX license identifiers where possible so the recipient can look "
                "up full terms. If no identifier is available, point at full details another "
                "way, such as a URL. The field must also convey the existence of PROPRIETARY "
                "license conditions. If the author is unaware of the license, that must be "
                "stated explicitly (depends on sbx-prc-01).\n\n"
                "Rationale in the standard: incorrect or fraudulent license information "
                "affects an organization's ability to rely on the software or obtain support, "
                "so this is a risk-management field, not a legal nicety.\n\n"
                f"Analysis: {DOC} section 3.2"
            ),
            "acceptance_criteria": (
                "Every component carries a license identifier, a URL to full terms, a "
                "proprietary-conditions flag, or an explicit unknown marker. Emitted SPDX IDs "
                "validate against the SPDX license list. sbom_components.license is populated."
            ),
        },
        {
            "id": "sbx-fld-05",
            "title": "Component Identifiers - all of them, not one",
            "priority": "high",
            "description": (
                "Major update from 'Other Unique Identifiers'. ICDEV emits purl only.\n\n"
                "The standard requires at least one common software identifier and says that "
                "if MULTIPLE identifiers exist the author should include ALL of them. Named "
                "identifier types: CPE, PURL (ECMA-427), UUIDs (RFC 9562), organization-"
                "specific identifiers, commit hashes, and intrinsic identifiers - OmniBOR and "
                "SWHID (ISO/IEC 18670:2025).\n\n"
                "CPE is the operationally important addition: it is the NVD lookup key, and "
                "without it SBOM components cannot be joined to CVE records by the existing "
                "tools/supply_chain/cve_triager.py path. Derive CPE where possible.\n\n"
                f"Analysis: {DOC} section 3.2"
            ),
            "acceptance_criteria": (
                "Every component carries at least one identifier; where more than one is "
                "derivable all are emitted. CPE is present wherever derivable. The multi-"
                "identifier structure round-trips through the validator and through "
                "sbom_components.identifiers_json."
            ),
        },
        {
            "id": "sbx-fld-06",
            "title": "Component Name alternates and Component Version unknown-marking",
            "priority": "medium",
            "description": (
                "Two smaller field fixes.\n\n"
                "Component Name (minor update): data formats implementing this element must "
                "allow MULTIPLE entries to capture alternate names. ICDEV emits a single name.\n\n"
                "Component Version (major update): if the producer does not provide a "
                "version, the SBOM author must indicate the version is UNKNOWN. ICDEV instead "
                "writes the literal strings 'unspecified' (requirements.txt, pyproject, "
                "package.json, Cargo.toml, csproj parsers) and 'managed' (_parse_pom_xml when "
                "a Maven version is dependency-managed). Neither is a machine-interpretable "
                "unknown marker, and both collide with the Explicitly Identifying Unknown "
                "Information element. Replace with the convention from sbx-prc-01. Note "
                "'managed' is not even unknown - it means resolvable from the parent POM, "
                "which sbx-cov-01 should actually resolve.\n\n"
                f"Analysis: {DOC} section 3.2"
            ),
            "acceptance_criteria": (
                "No literal 'unspecified' or 'managed' version string appears in generated "
                "output. Alternate component names round-trip. A test covers each parser that "
                "previously emitted a placeholder."
            ),
        },
        # ---- COV: coverage ---------------------------------------------------
        {
            "id": "sbx-cov-01",
            "title": "Transitive dependency resolution - Coverage element",
            "priority": "critical",
            "description": (
                "THE structural gap, and the element the 2026 standard changed most "
                "decisively. Depth (top-level dependencies only) is REPLACED by Coverage: all "
                "components that make up the target software INCLUDING TRANSITIVE "
                "DEPENDENCIES, with NO MINIMUM DEPTH.\n\n"
                "ICDEV parses DECLARED dependency manifests, so it captures direct "
                "dependencies only for every ecosystem except npm, where package-lock.json "
                "happens to already be a resolved tree.\n\n"
                "Move to resolved sets: Python via importlib.metadata over the target "
                "environment rather than requirements.txt; npm via package-lock and "
                "yarn.lock; Go via go.sum / go list -m all; Rust via Cargo.lock; Maven via "
                "dependency:list; Gradle via the dependencies task; .NET via "
                "project.assets.json.\n\n"
                "Offline-first constraint: this environment has no npm and prefers pure "
                "Python, and toolchains may be absent in air-gap. Prefer lockfile parsing "
                "over shelling out. Where resolution is genuinely impossible, degrade to "
                "declared dependencies AND state explicitly that coverage is incomplete - "
                "never silently present a partial tree as complete, because the standard's "
                "whole point is that a recipient must be able to conclude a vulnerability "
                "does not affect them from the component's absence.\n\n"
                "Also required: if multiple instances of a component differ in metadata, list "
                "each separately with its own dependency relationship.\n\n"
                f"Analysis: {DOC} sections 1.3 and 3.3"
            ),
            "acceptance_criteria": (
                "For a fixture project with a known transitive tree, the generated SBOM "
                "contains every transitive component. Where resolution is impossible the SBOM "
                "carries an explicit incomplete-coverage statement. A test per ecosystem. No "
                "regression in generation time beyond an agreed budget."
            ),
        },
        {
            "id": "sbx-cov-02",
            "title": "Component Dependency Relationship - emit a real graph",
            "priority": "high",
            "description": (
                "ICDEV output is a FLAT component list. No CycloneDX 'dependencies' array is "
                "emitted at all, so no dependency graph can be built from an ICDEV SBOM - the "
                "element is not partially met, it is entirely absent.\n\n"
                "Emit dependencies with ref/dependsOn, rooted at the target component, "
                "reconstructing the tree resolved by sbx-cov-01. The definition is narrow: a "
                "relationship where one component is NECESSARY FOR THE OPERATION of the "
                "other.\n\n"
                "The standard permits either embedding subcomponents or linking to separate "
                "SBOM documents per dependency - but linking only satisfies Coverage if the "
                "recipient has access to ALL linked SBOMs, so prefer embedding unless that "
                "access is guaranteed.\n\n"
                "Design the graph representation so sbx-fmt-01 can express the same edges as "
                "SPDX RELATIONSHIP entries.\n\n"
                f"Analysis: {DOC} section 3.2"
            ),
            "acceptance_criteria": (
                "A 'dependencies' array is present and every bom-ref resolves. The graph "
                "reconstructs the tree from sbx-cov-01 and is cycle-checked. Duplicate "
                "component instances appear separately with their own relationships. The "
                "validator scores the element as met."
            ),
        },
        # ---- PRC: practices --------------------------------------------------
        {
            "id": "sbx-prc-01",
            "title": "Explicitly Identifying Unknown Information - unknown vs withheld",
            "priority": "high",
            "description": (
                "Major update: 'Known Unknowns' becomes 'Explicitly Identifying Unknown "
                "Information', and the standard now SEPARATES two states that ICDEV conflates "
                "into the single literal 'unspecified' - information UNKNOWN TO THE AUTHOR, "
                "versus information the author is deliberately WITHHOLDING.\n\n"
                "Design one machine-processable convention covering both states and apply it "
                "across every field. sbx-fld-02 through sbx-fld-06 all depend on this, so "
                "land it before or alongside them.\n\n"
                "The standard also requires the SBOM author to have a PROCESS by which "
                "recipients can ask about redacted security-related information, and notes "
                "that an SBOM withholding essential component data may be considered "
                "incomplete. ICDEV already emits CUI classification and distribution "
                "properties, which is exactly the withholding case - document the enquiry "
                "route alongside them.\n\n"
                f"Analysis: {DOC} sections 1.3 and 3.3"
            ),
            "acceptance_criteria": (
                "Both states are representable and distinguishable in CycloneDX output and "
                "(after sbx-fmt-01) SPDX. The validator distinguishes them and never counts "
                "'withheld' as 'unknown'. The recipient enquiry process is documented in the "
                "feature doc."
            ),
        },
        {
            "id": "sbx-prc-02",
            "title": "Frequency and Accommodation of Updates to SBOM Data",
            "priority": "medium",
            "description": (
                "Frequency: every software version or update has an associated SBOM. A new "
                "build or release means a new SBOM, including builds that merely integrate "
                "updated dependencies. Newly discovered component detail, or a correction to "
                "existing data, means a REVISED SBOM.\n\n"
                "What ICDEV enforces today is weaker: a 30-day staleness threshold "
                "(sbom_max_age_days: 30 under both sbd and swft in args/security_gates.yaml), "
                "while CLAUDE.md separately asserts 'SBOM regenerated on every build'. The "
                "gate and the claim do not match.\n\n"
                "Accommodation of Updates (major update, was Accommodation of Mistakes): "
                "corrections must be accommodated and made promptly. The 2021 tolerance for "
                "immature data is explicitly withdrawn - recipients may now weigh SBOM errors "
                "in their risk decisions about the producer.\n\n"
                "Implement supersedes/revision linkage in sbom_records (column added by "
                "sbx-fnd-02) and bump SBOM Version on content change per sbx-fld-01.\n\n"
                f"Analysis: {DOC} section 3.3"
            ),
            "acceptance_criteria": (
                "Regenerating after a dependency change produces a new SBOM Version linked to "
                "its predecessor. A correction flow test asserts the superseded row is marked, "
                "not mutated - the audit trail stays append-only. The staleness gate and the "
                "per-build claim are reconciled in both the gate config and CLAUDE.md."
            ),
        },
        # ---- SIG: signature and validation -----------------------------------
        {
            "id": "sbx-sig-01",
            "title": "SBOM Author Signature",
            "priority": "high",
            "description": (
                "NEW element. A digital signature attributable to the SBOM author, providing "
                "assurance the claimed signatory signed the data and that it was not modified "
                "after signing. The algorithm must be approved for secure use by a relevant "
                "authority - NIST DSS, ISO/IEC 14888-4:2024, or ENISA Agreed Cryptographic "
                "Mechanisms. The standard says to build on existing software signing "
                "infrastructure, tools and key management (NIST SP 800-57 Part 1 Rev 5).\n\n"
                "ICDEV does not sign its own SBOMs. Two primitives already exist and are "
                "unused by this path: tools/crypto/attestation_signer.py::sign_artifact and "
                "tools/crypto/key_manager.py::sign_payload. Use them.\n\n"
                "Worth noting for consistency: ICDEV emits 'cosign attest --type cyclonedx' "
                "steps in the CI YAML it GENERATES FOR OTHER PROJECTS "
                "(tools/devsecops/pipeline_security_generator.py, "
                "tools/devsecops/attestation_manager.py) while shipping unsigned SBOMs "
                "itself.\n\n"
                "Air-gap constraint: signing and verification must work with no sigstore/"
                "Fulcio network reachability. Store the signature detached alongside the SBOM "
                "and record it plus the algorithm in sbom_records (columns from sbx-fnd-02).\n\n"
                f"Analysis: {DOC} sections 1.1 and 2.3"
            ),
            "acceptance_criteria": (
                "Sign-then-verify round-trips. A tampered SBOM fails verification. The "
                "algorithm is on the NIST-approved list. Both paths work with no network. The "
                "signature and algorithm are persisted."
            ),
        },
        {
            "id": "sbx-sig-02",
            "title": "SBOM minimum-elements conformance validator",
            "priority": "high",
            "description": (
                "New tool: tools/compliance/sbom_minimum_elements_validator.py. Scores any "
                "CycloneDX or SPDX document against all 17 data fields and the 6 applicable "
                "practices, emitting per-element met/partial/gap with a rationale, plus a "
                "total. --json output.\n\n"
                "This is the measurement instrument for the whole card: sbx-gov-01 gates on "
                "it, sbx-fmt-02 uses it to replace filename-glob presence checks in the "
                "assessors, and it is also how ICDEV can grade SBOMs RECEIVED FROM VENDORS - "
                "the standard is aimed at organizations that procure software as much as "
                "those that produce it.\n\n"
                "It must distinguish 'withheld' from 'unknown' per sbx-prc-01 rather than "
                "scoring both as absent.\n\n"
                "Register per the 8-point new-tool checklist: manifest shard, "
                "docs/reference/commands.md, args/security_gates.yaml, MCP tool_registry plus "
                "gap_handlers, append-only tables if any, tests/conftest.py schema, companion "
                "sync, coherence check.\n\n"
                f"Analysis: {DOC} section 3"
            ),
            "acceptance_criteria": (
                "Run against the CURRENT generator output it reproduces the documented "
                "baseline in the gap analysis (2 of 17 fields fully met, 0 of 7 practices). "
                "Run against post-fld/cov output it reports 17 of 17. Registered at all 8 "
                "checklist points. Grades a third-party SPDX fixture."
            ),
        },
        # ---- FMT: formats ----------------------------------------------------
        {
            "id": "sbx-fmt-01",
            "title": "SPDX writer and CycloneDX default uplift",
            "priority": "medium",
            "description": (
                "The standard names SPDX (ISO/IEC 5962:2021) and CycloneDX (ECMA-424) as the "
                "two data formats widely used to generate and consume SBOMs, and says minimum "
                "automation support means supporting all data formats that are widely used, "
                "open source and compatible. ICDEV has NO SPDX generator and NO SPDX parser "
                "anywhere in the tree - generate_sbom() raises on any format other than "
                "'cyclonedx'.\n\n"
                "Add an SPDX writer carrying the same 17 elements, exposed as --format spdx. "
                "Express the sbx-cov-02 dependency edges as SPDX RELATIONSHIP entries.\n\n"
                "Also raise the CycloneDX default off 1.4 (a 2022 spec) - the standard warns "
                "against deprecated format versions and cites ECMA-424 of December 2025. Keep "
                "1.4 through 1.7 selectable for consumers that need them.\n\n"
                "Note SWID tags were REMOVED from the accepted format list in 2026. ICDEV "
                "emits no SWID, so nothing to undo.\n\n"
                f"Analysis: {DOC} sections 1.3 and 2.6"
            ),
            "acceptance_criteria": (
                "The same project emitted as CycloneDX and as SPDX scores identically under "
                "sbx-sig-02. The SPDX document validates against the official schema. The "
                "CycloneDX default is no longer 1.4 and 1.4-1.7 remain selectable."
            ),
        },
        {
            "id": "sbx-fmt-02",
            "title": "SBOM ingest parity - parse and validate instead of glob",
            "priority": "medium",
            "description": (
                "ICDEV consumes SBOMs in two places and both are weak.\n\n"
                "1. icdev/tools/security_canvas/zig_external_adapter.py::ingest_sbom accepts "
                "CycloneDX JSON only. Add SPDX.\n"
                "2. fedramp_assessor.py, sbd_assessor.py, cssp_assessor.py and ivv_assessor.py "
                "detect SBOMs by FILENAME GLOB - '*sbom*', '*spdx*', '*cyclonedx*', '*bom*.xml'. "
                "None of them opens the file. An empty file named sbom.json currently passes "
                "every one of these checks.\n\n"
                "Replace the presence checks with sbx-sig-02 validation so the assessment "
                "answer changes from 'an SBOM-shaped filename exists' to 'a conforming SBOM "
                "exists, scoring N of 17'.\n\n"
                f"Analysis: {DOC} section 2.4"
            ),
            "acceptance_criteria": (
                "An SPDX fixture ingests through the ZIG adapter. Each of the four assessors "
                "reports a conformance score rather than a boolean. A test proves an empty "
                "file named sbom.json no longer passes."
            ),
        },
        # ---- GOV: governance -------------------------------------------------
        {
            "id": "sbx-gov-01",
            "title": "Gate on conformance, not just presence",
            "priority": "high",
            "description": (
                "args/security_gates.yaml already carries five SBOM conditions - "
                "sbom_not_generated (deployment_gates, swft), sbom_attestation_missing "
                "(devsecops), sbom_stale_over_30_days with sbom_max_age_days: 30 (sbd, swft), "
                "sbom_generation_failed and sbom_generation_skipped (marketplace, production). "
                "Every one is a presence, freshness or exit-code check. NOTHING validates that "
                "the SBOM actually conforms to the minimum elements, so a two-line CycloneDX "
                "file with no components satisfies all of them.\n\n"
                "Add sbom_minimum_elements_not_met (blocking) and "
                "sbom_conformance_below_threshold (warning) driven by sbx-sig-02, wired into "
                "deployment_gates, swft and devsecops, with an explicit threshold under the "
                "thresholds block rather than a hardcoded constant.\n\n"
                f"Analysis: {DOC} section 2.5"
            ),
            "acceptance_criteria": (
                "The gate blocks on a deliberately non-conforming SBOM and passes on a "
                "conforming one. Thresholds live in args/security_gates.yaml, never in Python. "
                "A test covers both directions."
            ),
        },
        {
            "id": "sbx-gov-02",
            "title": "Distribution and Delivery - version-specific retrieval",
            "priority": "medium",
            "description": (
                "SBOMs should be available promptly to those who need them. The 2026 update "
                "REMOVED the standalone Access Control element and folded its considerations "
                "here: access controls may limit sharing with unauthorized parties but must "
                "NOT prevent sharing between authorized parties or stop organizations "
                "integrating SBOM data into trusted security tools. Acceptable mechanisms "
                "include accompanying installation, a VERSION-SPECIFIC URL, an API to a "
                "database, or a public repository.\n\n"
                "ICDEV writes a file to disk and stores the path in sbom_records. There is no "
                "version-specific URL and no retrieval API.\n\n"
                "Add a retrieval endpoint keyed on the sbom_records row (project plus SBOM "
                "version), RBAC-enforced, and surface SBOM records with their sbx-sig-02 "
                "conformance score in the dashboard. Keep the CUI classification and "
                "distribution properties - they are the legitimate withholding case, not a "
                "reason to block authorized access.\n\n"
                "If this adds a dashboard page, all 8 components of the new-page gate apply, "
                "including IQE integration.\n\n"
                f"Analysis: {DOC} section 3.3"
            ),
            "acceptance_criteria": (
                "A version-specific URL returns the exact bytes of the SBOM for a given "
                "sbom_records row. RBAC denies unauthorized callers and a test covers the DENY "
                "case, not just the allow. Any new page satisfies all 8 gate components."
            ),
        },
        {
            "id": "sbx-gov-03",
            "title": "Correct the customer-facing SPDX claim",
            "priority": "medium",
            "description": (
                "GovCon content states that ICDEV produces SBOMs 'in SPDX and CycloneDX "
                "formats', and in one place that 'SBOM generation occurs at every build via "
                "Syft in SPDX and CycloneDX formats'. ICDEV has no SPDX writer and does not "
                "use Syft. Affected files: tools/govcon/generate_icdev_proposal_content.py "
                "(around lines 218, 267, 368), tools/govcon/seed_icdev_knowledge_base.py "
                "(around lines 471 and 477), tools/govcon/seed_solicitation_requirements.py "
                "(lines 62 and 73 - though note these two are seeded SOLICITATION "
                "requirements, i.e. what a customer asks for, so check whether they are "
                "claims about ICDEV at all before editing).\n\n"
                "Two acceptable resolutions: land sbx-fmt-01 first and the claim becomes true, "
                "or soften the claim now. Do not leave an untrue capability statement in "
                "proposal content.\n\n"
                "Sweep for the same claim on other customer-facing surfaces - capability "
                "sheets, README, docs/features - before closing.\n\n"
                f"Analysis: {DOC} section 2.6"
            ),
            "acceptance_criteria": (
                "No untrue SBOM format or tooling claim remains in customer-facing content. "
                "If sbx-fmt-01 has landed, the retained claim is verified by sbx-sig-02 output "
                "on both formats. The sweep covers proposal content, knowledge base, README "
                "and docs/features."
            ),
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the SBX SBOM-2026 card")
    ap.add_argument("--dry-run", action="store_true", help="Do not write; print the plan")
    ap.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = ap.parse_args()

    specs = _tasks()
    # Gate every non-gate task on the manual hold. This is the mechanism that
    # actually stops dispatch; the gate-00 naming convention alone does not.
    for spec in specs:
        if spec["id"] != GATE:
            spec.setdefault("task_type", "build")
            spec.setdefault("status", "backlog")
            spec["depends_on_task_id"] = GATE

    if args.dry_run:
        out = {
            "dry_run": True,
            "count": len(specs),
            "gate": GATE,
            "tasks": [
                {
                    "id": s["id"],
                    "title": s["title"],
                    "priority": s.get("priority"),
                    "status": s.get("status"),
                    "depends_on": s.get("depends_on_task_id"),
                }
                for s in specs
            ],
        }
        print(json.dumps(out, indent=2) if args.as_json else out)
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(specs)
    out = {"created": created, "count": len(created), "requested": len(specs), "gate": GATE}
    print(json.dumps(out, indent=2) if args.as_json else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
