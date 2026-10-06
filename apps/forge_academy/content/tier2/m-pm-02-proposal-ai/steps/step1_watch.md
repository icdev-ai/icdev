---
ontology_id: icdev:mission:m-pm-02-proposal-ai:step:1
step_class: icdev:Lesson
---

# Proposal AI Assistant — Watch It Run

Government proposals are won or lost in the technical approach section. Here is how ICDEV's proposal tooling maps past performance to a solicitation's requirements, work that normally takes a BD team half a day.

> **Illustrative walkthrough.** Apex Federal Solutions, solicitation W912BU-26-R-0041 and every figure below are fictional, written to show what the workflow produces. They are not the output of a live run on this platform. The *Run it for real* section names the ICDEV tools that do each part.

## What a proposal run looks like

For solicitation **W912BU-26-R-0041** (USACE IT Modernization, $15M IDIQ), the Proposal AI:

1. **Parsed** the solicitation's Statement of Work (SOW) — 47 pages → 23 evaluation criteria
2. **Mapped** each criterion to Apex Federal Solutions' past performance database (12 contracts)
3. **Identified** capability gaps: 2 criteria with no direct past performance match
4. **Profiled** likely competitors for the gaps, to inform teaming decisions
5. **Drafted** the Technical Approach section outline (12 paragraphs with talking points)
6. **Generated** 3 discriminating features — unique capabilities that differentiate Apex from likely competitors

The BD team can now focus on win strategy, not document archaeology.

## The PWin impact

Evaluators score against Section M. Strong, specific past-performance mapping and clear discriminators are what move a technical score; generic responses are what lose them. The tooling does the mapping and surface-level differentiation. Your team provides the strategic judgment, and selects every past-performance reference itself: the suggester only suggests.

## Run it for real

```bash
python tools/govcon/solicitation_parser.py --input solicitation.pdf --json      # parse the SOW / RFP
python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --ingest solicitation.pdf --json  # one compliance matrix
python tools/govcon/gap_analyzer.py --analyze --json                             # capability gaps
python tools/govcon/competitor_profiler.py --leaderboard --naics 541511 --json   # who you are up against
```

Past-performance references come from `tools/govcon/past_performance_suggester.py`, which ranks completed contracts already recorded in the CPMP tables; it never writes into a proposal. Proposal sections are worked on the dashboard at `/proposals`.
