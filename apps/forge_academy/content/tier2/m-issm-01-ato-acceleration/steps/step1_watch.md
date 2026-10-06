---
ontology_id: icdev:mission:m-issm-01-ato-acceleration:step:1
step_class: icdev:Lesson
---

# ATO Acceleration — Watch It Run

An ATO (Authority to Operate) package for an IL4 system can run 300–600 pages. The evidence collection process alone takes 6–12 weeks manually. This walkthrough shows how ICDEV compresses that to days. The system and its numbers are an illustrative scenario.

## What the agent just did

For a fresh IL4 system (`ICDEV-Analytics`), the ATO Acceleration agent:

1. **Scanned** the system's configuration baseline (Ansible inventory + Terraform state)
2. **Mapped** the 325 in-scope NIST SP 800-53 Rev 5 controls (FedRAMP Moderate baseline plus the DoD Cloud Computing SRG's IL4 additions) to the system's implemented capabilities
3. **Identified** 47 controls with evidence gaps (no artifact on file)
4. **Auto-generated** control narrative drafts for 278 controls with sufficient telemetry
5. **Estimated** ATO timeline: 18 days to evidence complete (vs. industry avg: 11 weeks)
6. **Prioritized** the 47 gaps by risk: 3 critical, 12 high, 32 medium

## The standard without AI

- Week 1–2: Control mapping (manual spreadsheet)
- Week 3–6: Evidence collection (emails, screen captures, interviews)
- Week 7–9: Narrative writing (copy-paste from similar systems)
- Week 10–11: Package assembly and ISSM review

ICDEV collapses weeks 1–9 into an automated overnight run.

## Next: Configure for your system

The next step walks through the inputs a run takes: impact level, framework (FedRAMP, RMF or CMMC) and evidence sources. In ICDEV the pieces are real tools. `tools/compliance/crosswalk_engine.py --impact-level IL4` lists the controls in scope, `tools/compliance/ssp_generator.py` drafts the SSP, and `tools/compliance/ato_packager.py` assembles the package.
