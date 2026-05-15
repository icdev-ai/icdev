---
ontology_id: icdev:mission:m-analyst-01-competitive-intel-agent:step:2
step_class: icdev:Lesson
---

# Set Up Your Intel Agent

Configuring an intelligence agent requires four decisions: who to watch, what data sources to pull from, what signals matter, and when to be alerted.

## Configuration Walkthrough

### 1. Target Domain / Organization
Define the scope of your agent. Be specific — a narrowly defined target produces higher-quality intelligence than a broad sweep.

**Examples:**
- "Competitors pursuing OCONUS intelligence support contracts under NAICS 541990"
- "All contract awards to [named competitor] in the past 24 months"
- "Leadership changes at the top 5 firms in the C2 modernization market"

### 2. Data Sources to Monitor

Select the sources most relevant to your target:

| Source | Monitoring Type | Update Frequency |
|---|---|---|
| SAM.gov | Award notifications by CAGE code | Daily |
| USASpending | Historical and new award pulls | Weekly |
| Google News / RSS | Keyword-filtered news feed | Daily |
| Federal Register | Solicitations and notices | As published |
| Agency press releases | Announcements | As published |

### 3. Key Indicators to Track

Define what a "signal" looks like for your target:

- **Competitor wins** — Any award above $[X]M in your target domain
- **Leadership changes** — New C-suite, division VP, or program director appointments
- **Contract patterns** — Trends in agency concentration, vehicle usage, teaming partners
- **Capability signals** — New certifications, cleared facility upgrades, capability announcements

### 4. Alert Thresholds

Set the conditions that trigger an immediate alert vs. a weekly digest:

- **Immediate:** Competitor wins contract ≥ $10M in your primary domain
- **Weekly digest:** All other indicator activity from the past 7 days

---

**Your task:** Complete all four configuration decisions for your target. Write them out in the format above. A complete configuration is a brief you can hand to a developer or operations analyst to implement — no interpretation required.
