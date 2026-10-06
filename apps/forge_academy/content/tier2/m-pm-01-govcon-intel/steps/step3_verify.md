---
ontology_id: icdev:mission:m-pm-01-govcon-intel:step:3
step_class: icdev:Lesson
---

# Verify: Review Opportunity Results

Review a scan's results before you trust the scheduled cadence. `python tools/govcon/sam_scanner.py --list-cached --json` lists what recent scans cached; `/govcon` shows the same pipeline on the dashboard.

## What to look for

**Capability coverage** — How many of the opportunity's extracted requirements map to your capability catalog, and which are gaps (`python tools/govcon/capability_mapper.py --coverage --json`). Broad coverage with no critical gap is a strong fit; a single gap in a mandatory requirement can be a no-bid on its own.

**Days to due date** — The solicitation response deadline. Less than 7 days: skip unless you already have a team assembled. 14–30 days: standard pursuit window. 30+ days: ideal — enough time for thorough color reviews.

**Contract type** — Understanding the vehicle:

- **IDIQ/GWAC**: On-ramp opportunity — winning gets you a spot to compete for task orders
- **FFP**: Firm-Fixed Price — bid a fixed cost, you own the risk
- **T&M**: Time & Materials — lower risk, often used for R&D and advisory work
- **SBIR**: Small Business only — check your size standard

**Set-aside status** — If `set_aside: 8(a)` or `set_aside: SDVOSB`, confirm your firm qualifies before investing pursuit resources.

## Tuning your results

If the results feel off:

- Too many false positives → narrow `sam_gov.naics_codes` and `notice_types`, and make capability keywords more specific
- Missing obvious fits → add more specific keywords, especially program names
- Wrong agencies or values → filter them out at review; they are not scanner settings today

Scheduled scanning is the GovCon daemon (`python tools/govcon/govcon_engine.py --daemon --json`), started by an operator, not a switch in this lesson.
