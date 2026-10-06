---
ontology_id: icdev:mission:m-pm-01-govcon-intel:step:1
step_class: icdev:Lesson
---

# GovCon Opportunity Intel — Watch It Run

Finding relevant government contracts on SAM.gov by hand means checking a large daily volume of new notices, reading each synopsis, and deciding whether to bid. Here is what ICDEV's opportunity pipeline does with that.

> **Illustrative walkthrough.** Apex Federal Solutions, the solicitation numbers and every figure below are fictional, written to show what the workflow produces. They are not the output of a live run on this platform. The *Run it for real* section names the ICDEV tools that do each part.

## What a scan run looks like

For the fictional firm **Apex Federal Solutions** (NAICS 541511, 541512 — IT services), the scanner:

1. **Queried** SAM.gov for recent opportunities matching NAICS 541511/541512
2. **Found** 847 opportunities
3. **Filtered** by agency relevance (DoD, DHS, USAF) → 203 remaining
4. **Matched** each one's extracted requirements against the capability catalog → top 12 highlighted
5. **Flagged** 3 opportunities as **high-fit**:
   - `W912BU-26-R-0041` — USACE IT Modernization, $15M IDIQ, due in 14 days
   - `FA8771-26-R-0022` — AFMC Software Dev Support, $8.5M, due in 21 days
   - `70RSAT26R00000014` — TSA Cybersecurity Operations, $22M, due in 9 days
6. **Drafted** responses to the high-fit opportunities' requirements, for human review

## The competitive advantage

A BD team checking SAM.gov by hand finds the same opportunities days later. A scanner that runs on a schedule turns that delay into pursuit time, which is the difference between a competitive proposal and a rushed one.

## Run it for real

ICDEV's GovCon engine runs four stages: **discover** (SAM.gov scan), **extract** (shall/must/will requirements), **map** (requirements to the capability catalog, with gaps) and **draft** (response drafts).

```bash
python tools/govcon/sam_scanner.py --scan --naics 541512 --json      # discover (needs SAM_GOV_API_KEY)
python tools/govcon/govcon_engine.py --stage extract --json
python tools/govcon/govcon_engine.py --stage map --json
python tools/govcon/govcon_engine.py --pipeline-report --json
```

The dashboard view is `/govcon` (pipeline, recent opportunities), with `/govcon/requirements` and `/govcon/capabilities` behind it.

## Next step

See how the scanner is configured for a firm's NAICS codes and priorities.
