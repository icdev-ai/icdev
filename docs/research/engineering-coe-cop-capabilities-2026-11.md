# Engineering CoE, communities of practice and platform capabilities

**Date:** 2026-11-17
**Card:** icx-coe-01 (the first card of the programme; this brief goes in with it)
**Scope:** the ICDEV ecosystem: ICDEV[OS] (ICOS, the governed runtime), ICDEV[IT] (the engineering factory) and Cortex (IT's governed AI facade)
**Status:** research input. Nothing here is an adoption decision. The owner adopts standards; the register (`/idp/standards`) records it.

---

## 1. The question

The owner asked for a systems architect's view of eight capabilities for ICOS, plus an engineering centre of
excellence (CoE) and engineering standards, keeping an open mind about where each belongs. Three surveys were run:
ICOS's code, the wider ecosystem's code, and external standards from 2025 and 2026.

**In one line:** the ecosystem is strong at governance enforced by machine, and weak at the human layer (standards
people can browse and change, communities that curate practice, training) and at three technical edges
(bearer-aware networking, OT ingest, natural-language analytics in ICOS).

## 2. Findings: where we are

| # | Area | ICOS | ICDEV[IT] and others | The real gap | External anchors |
|---|---|---|---|---|---|
| 1 | Cloud to edge secure data with AI | Mature: marked data plane (product and column release, signed bindings), dissemination, edge tiering, offline model bundles, a tagger that proposes, lineage | air-gap, IL5 ingest, redaction, data bridge | No label interchange with coalition formats; the tagger does not label columns; over-classification is not measured; DDIL sync priority for data products is not policy-driven | STANAG 4774/4778, ZTDF/OpenTDF |
| 2 | Data analytics and discovery | Partial: catalog on the mesh, profiles, glossary, SQL text, lineage | Built: Data Design Canvas (profiler, quality, PII, anomaly, catalog sync), IQE, NLQ, Cortex Analyst | No source discovery, schema inference or NL-to-SQL in ICOS; the two catalogues are not joined; no data contract standard | OpenLineage, ODCS 3.1 |
| 3 | Multimodal software-defined networking | Partial and measured: Zenoh mesh, DDIL store-and-forward and shaping, mTLS posture, multi-site routing, TAK/ROS 2/AIS gateways | network design and NOC canvases (design, not data plane) | No bearer model (PACE), no multipath or bearer choice, per-client not per-link budgets, 53-77% loss under overload, priority classes off | BPv7/BPSec (RFC 9171-9173) |
| 4 | Industrial twins and continuous optimisation | Partial: strong twin (reconcile, branches, VVUQ, calibration), mission loop, DTDL/AAS export only | twin core (IT and cloud twins), simulation | Factory OT was out of scope (the owner has lifted this for ingest only); no OPC UA, Sparkplug or AAS import; no optimisation solver; VVUQ is not per prediction | IEC 63278 AAS, Sparkplug B (ISO/IEC 20237), OPC UA, NASEM VVUQ |
| 5 | Engineering CoP (AI development life cycle) | Model registry, ML-BOM, evals, use cases, router, agents | kanban harness, CI gates, evals, red team, fine-tune registry, governance goals | No AI-BOM generator; no DORA metrics; no practice people can use | NIST SP 800-218A, NIST AI 600-1, ISO/IEC 5338, 42001, 42005, DORA 2025 |
| 6 | AI and data CoP | Retrieval with no model, gold sets, a small corpus | RAG, GraphRAG, research tools, ACE/Coworker | OWASP Agentic and LLM Top 10 not mapped to controls; no autonomy review gate; no curated knowledge hub | OWASP LLM and Agentic Top 10, DoDD 3000.09, OTel GenAI |
| 7 | Application CoP | Portfolio twin, 7R, modernisation workflows | REA, CAM, translation, modernisation tools | No ADR/C4/arc42 records; no equivalence-test evidence standard; playbooks not published | (ADRs, strangler fig, DORA small batches) |
| 8 | Cloud CoP | Helm, hardened and FIPS images, GovCloud target | cloud abstraction, multi-cloud Terraform, IaC canvas, wave planner | No FinOps (cost only for model calls); no KSI-style evidence. Cloud-vendor training content is out, by owner decision | FedRAMP 20x KSIs, DoD CC SRG, FOCUS 1.2 |
| 9 | Engineering CoE and standards | Enforced by machine: CO-01..31 coherence, conformance audit, interfaces and controls registers, policy as code, signed change workflows, STIG waivers | Much exists in IT's IDP: scorecards as code, exemptions with approval and audit, 675 ADRs, scaffolds, coherence, security gates, red-first, guard hooks, the five-rules audit, a partial standards registry, FORGE Academy | The register was partial and had no lifecycle; exemptions do not expire by rule; ADRs are a flat document, not signed records linked to requirements; no DORA metrics; no single CoE view across IT, OS and FT; no CoE to CoP link | CNCF platform maturity, Backstage/TechDocs, OWASP ASVS 5.0, OWASP SAMM v2, InnerSource |

## 3. Where each belongs

- **ICDEV[IT]** is the factory, so the CoE's machinery lives there, on the IDP it already has: the standards register
  (this card), exemptions, ADRs, scorecards, golden paths; also the AI-BOM generator, the data canvas reused for
  discovery, FinOps and IaC, and FORGE Academy as the learning home for every CoP.
- **ICOS** observes the CoE across every repo in its enterprise twin, and owns what is a governed runtime decision or
  marked data: CoP knowledge hubs on its marking-aware retrieval, and capabilities 1, 3 and 4, and the governed half of 2.
- **Cortex** drafts proposals only (NL-to-SQL drafts, contribution drafts, the tagger), through ICOS's governed router
  when used from ICOS. It never decides.
- **Integration** follows the apptwin pattern: IT exports signed bundles against a pinned ICOS contract.

## 4. What this card (icx-coe-01) builds from it

Row 9's first gap. One entry per standard (id, version, owner role, sources by reference, scope, check-as-code), a
lifecycle of propose, review, adopt and deprecate through declared steps where the author is never the signer, and a
refusal to adopt a standard that has no check ("a standard without a check is a wish"). The seeds are the gates ICDEV
already enforces (security gates, red-first, coherence, the five-rules audit, guard hooks, the 8-point page gate and
the E2E spec rule), plus ICOS's interfaces and controls registers by pinned reference, entered as proposals for the
owner. Design and its reasons: `docs/reference/adrs.md`, D-COE-1.

How the anchors bear on it:

- **CNCF platform engineering maturity** asks for "Interfaces" and "Measurement" a team can use and read. A register
  a person browses, with the check behind each standard, is the interface; the scorecards (icx-coe-03) are the
  measurement.
- **Backstage TechDocs** is "docs like code": the documentation lives with the code. The register's sources are
  references into the repository, pinned by digest, never copies.
- **OWASP ASVS 5.0** and **SAMM v2** are external standards a team adopts at a level. An ICDEV standard may cite a
  catalog entry (`args/standards_registry.yaml`), so adopting one is a recorded decision with a check, not a claim.
- **InnerSource** patterns favour proposals from anyone with review by trusted committers. Anyone (or an agent) may
  propose here; a steward reviews and signs.

## 5. External anchors, verified

Each URL below was fetched on 2026-11-17 and its content read; "unverified" means the fetch failed and the URL is the
best official one found. No vendor product is named unless ICDEV integrates with it. No cloud-vendor training content.

| # | Anchor | Official source | Verified | What the page says |
|---|---|---|---|---|
| 1 | CNCF Platform Engineering Maturity Model | https://tag-app-delivery.cncf.io/whitepapers/platform-eng-maturity-model/ | yes | Version 1.0. Five aspects (Investment, Adoption, Interfaces, Operations, Measurement), each with four levels (Provisional, Operationalized, Scalable, Optimizing). |
| 2 | Backstage and TechDocs (CNCF) | https://www.cncf.io/projects/backstage/ and https://backstage.io/docs/features/techdocs/ | yes | Backstage is a CNCF incubating project (accepted 2020-09-08, incubating 2022-03-15). TechDocs is its "docs like code" solution, Markdown written next to the code. |
| 3 | OWASP ASVS 5.0 | https://asvs.owasp.org/ | yes | The latest stable version is ASVS 5.0.0, published 30 May 2025. |
| 4 | OWASP SAMM v2 | https://owaspsamm.org/model/ | yes | SAMM 2.0 has five business functions and fifteen security practices. |
| 5 | InnerSource Commons patterns | https://patterns.innersourcecommons.org/ and https://innersourcecommons.org/ | yes | The patterns book is published by InnerSource Commons under CC BY-SA 4.0; the community was founded in 2015. |
| 6 | DORA 2025 | https://dora.dev/research/2025/dora-report/ | yes | "State of AI-assisted Software Development 2025": AI acts mainly as an amplifier of an organisation's existing strengths and weaknesses. |
| 7 | NIST SP 800-218A | https://csrc.nist.gov/pubs/sp/800/218/a/final | yes | Final, July 2024: "Secure Software Development Practices for Generative AI and Dual-Use Foundation Models: An SSDF Community Profile". |
| 8 | NIST AI 600-1 | https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence | yes | "AI RMF: Generative Artificial Intelligence Profile", published 26 July 2024. |
| 9 | ISO/IEC 5338 | https://webstore.iec.ch/en/publication/90754 | yes (IEC webstore; iso.org refused the fetch) | ISO/IEC 5338:2023, "AI system life cycle processes", edition 1.0, 2023-12-20, built on ISO/IEC/IEEE 15288 and 12207. |
| 10 | ISO/IEC 42001 | https://webstore.iec.ch/en/publication/90574 | yes (IEC webstore) | ISO/IEC 42001:2023, "Artificial intelligence - Management system", edition 1.0, 2023-12-18. |
| 11 | ISO/IEC 42005 | https://webstore.iec.ch/en/publication/107659 | yes (IEC webstore) | ISO/IEC 42005:2025, "AI system impact assessment", edition 1.0, 2025-05-28. |
| 12 | OWASP Top 10 for LLM Applications 2025; OWASP Top 10 for Agentic Applications 2026 | https://genai.owasp.org/llm-top-10/ and https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ | yes | LLM01:2025 Prompt Injection to LLM10:2025 Unbounded Consumption; the agentic list is dated 9 December 2025, from the OWASP GenAI Security Project. |
| 13 | DoD Directive 3000.09 | https://www.esd.whs.mil/Portals/54/Documents/DD/issuances/dodd/300009p.pdf | **unverified** (HTTP 403 here and on the media.defense.gov copy) | Not read. Search results only: "Autonomy in Weapon Systems", effective 25 January 2023, replacing the 2012 directive. Check at osx-cop-03. |
| 14 | OpenTelemetry GenAI semantic conventions | https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md | yes | Status: Development. The main semconv site says the GenAI conventions moved to this separate repository, which has no release yet. Pin a convention version when one exists. |
| 15 | NATO STANAG 4774 / 4778 | https://nhqc3s.hq.nato.int/apps/DCRA_Report/id-29d4122b072148f5aaf4882ecc5d963c/elements/id-389800bcd08f4393a156e82e80aa8454.html | yes for the registry entry; no public normative text | NATO's architecture registry lists ADatP-4774 Ed A Ver 1, "Confidentiality Metadata Label Syntax", accepted 2017-12-20. Its link to the NSO returned 404; 4778 (the binding) is supported only by a search result. No public text of either was found: the gateway at osx-cap-01 is bridged as a plugin, and no restricted content enters a repo. |
| 16 | ZTDF and OpenTDF | https://github.com/opentdf/spec and https://opentdf.io/ | yes | The specification repository is at 4.3.0; its README says ZTDF "builds directly upon the OpenTDF specification", adding cryptographic assertions for Five Eyes use. |
| 17 | Open Data Contract Standard 3.1 | https://bitol-io.github.io/open-data-contract-standard/latest/ | yes | Governed by Bitol under LF AI & Data; the page refers to v3.1.0 sections. |
| 18 | OpenLineage | https://openlineage.io/ | yes | "An open framework for data lineage collection and analysis", a Linux Foundation project with a technical steering committee. |
| 19 | RFC 9171, 9172, 9173 | https://www.rfc-editor.org/rfc/rfc9171.html, https://www.rfc-editor.org/rfc/rfc9172.html, https://www.rfc-editor.org/rfc/rfc9173.html | yes | All Standards Track, January 2022: Bundle Protocol Version 7; Bundle Protocol Security (BPSec); Default Security Contexts for BPSec. |
| 20 | IEC 63278 Asset Administration Shell | https://webstore.iec.ch/en/publication/65628 and https://industrialdigitaltwin.org/en/content-hub/aasspecifications | yes | IEC 63278-1:2023, "AAS for industrial applications - Part 1: AAS structure", 2023-12-14. The IDTA page lists AAS specification release 26-01 (parts 1 to 5). |
| 21 | Sparkplug B, ISO/IEC 20237 | https://sparkplug.eclipse.org/ and https://newsroom.eclipse.org/node/39754 | yes (Eclipse); the ISO page refused the fetch | The current specification is Sparkplug 3.0. Eclipse announced on 7 November 2023 that it "will also be known as ISO/IEC 20237", through the JTC 1 PAS process. |
| 22 | OPC UA (and OPC UA FX) | https://opcfoundation.org/about/opc-technologies/opc-ua/ and https://reference.opcfoundation.org/specs/OPC-10000-80/full | yes | A platform-independent service-oriented architecture (2008) with client-server and PubSub. UAFX Part 80, "Overview and Concepts", is at 1.00.03. |
| 23 | NASEM, digital twins and VVUQ | https://www.nationalacademies.org/publications/26894 | yes | "Foundational Research Gaps and Future Directions for Digital Twins", consensus study report, 2024, DOI 10.17226/26894; its statement of task covers verification, validation and uncertainty quantification. |
| 24 | FedRAMP 20x and Key Security Indicators | https://www.fedramp.gov/20x/ and https://fedramp.gov/2026/reference/20x/c/key-security-indicators/ | yes | 20x is in Phase 3 (wide-scale adoption). KSIs are in the Consolidated Rules for 2026, in 10 categories, launched 2026-06-24, last changed 2026-10-31. KSI-style evidence in osx-cop-05 is never an authorisation claim. |
| 25 | DoD Cloud Computing SRG | https://public.cyber.mil/dccs/ (redirects to https://www.cyber.mil/dccs/) | **unverified** (the page returned an empty application shell; the document host returned 403) | No version could be read from an official page. Secondary copies show a CSP SRG V1R2 of 30 January 2025, and an archive lists "Cloud Computing SRG Ver 1, Rel 4" as superseded. Check the version at osx-cop-05. |
| 26 | FinOps FOCUS 1.2 | https://focus.finops.org/focus-specification/v1-2/ | yes | "Publication version 1.2". |

**Count:** 24 verified, 2 unverified (DoDD 3000.09, DoD CC SRG). Two partial: STANAG 4774/4778 (the registry entry
only, no public text) and ISO/IEC 20237 (the Eclipse announcement, not the ISO page).

Also named in the plan and **not checked in this pass** (each is checked at the card that uses it): RFC 9315 (intent-
based networking terms), the Zenoh and DDS comparison, FMN Spiral 6, Spider 2.0 (NL-to-SQL accuracy), ODRL, the MCP
2025-11-25 specification and the DoD Responsible AI toolkit.

## 6. The programme (owner-approved order, 2026-11-17)

CoE first (it is the spine every CoP and capability reports into), then the CoP framework and the four CoPs, then the
four capabilities. Cards, one at a time: icx-coe-01 (this), icx-coe-02 (exemptions that expire, ADRs as records),
icx-coe-03 (scorecards with delivery evidence, DORA, `icos-conformance/v1`), osx-coe-01 (the CoE in ICOS's twin),
icx-coe-04 (golden paths), osx-coe-02 (journey); osx-cop-01 to -05 and icx-cop-01, osx-cop-06 (journey); osx-cap-01
to -04 and osx-cap-05 (journey). Every card meets the five rules and the definition of done in the plan.

Owner decisions: the factory-OT exclusion is lifted for INGEST ONLY (no writes to plant; optimisation proposes); the
CoP home is split (IT hosts hubs and Academy, ICOS holds marked knowledge, the signed contribution workflow and the
twin); cloud-vendor training and certification content is skipped.

## 7. Notes for later cards

- `tools/compliance/fedramp_ksi_generator.py` already exists in ICDEV[IT]. Read it before building KSI-style evidence
  in osx-cop-05.
- The register's manual evidence steps for the CI gates (red-first, coherence, the five-rules audit, security scan)
  are named steps a person reads. icx-coe-03 can make them machine-scored per repository.
- OTel GenAI conventions are still in development: pin a version only when one is released.
