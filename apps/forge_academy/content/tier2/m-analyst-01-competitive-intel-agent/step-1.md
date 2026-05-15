---
ontology_id: icdev:mission:m-analyst-01-competitive-intel-agent:step:1
step_class: icdev:Lesson
---

# Competitive Intelligence Agents

A competitive intelligence agent is an automated system that continuously monitors open-source data streams, surfaces relevant signals, and delivers actionable intelligence products — without requiring an analyst to manually search every source every day.

## What an AI-Powered COMPINT Pipeline Looks Like

A modern OSINT/COMPINT pipeline has four stages:

1. **Collect** — Pull data from structured and unstructured sources on a schedule
2. **Filter** — Remove noise using keyword rules and relevance scoring
3. **Analyze** — Identify patterns, anomalies, and notable changes against a baseline
4. **Deliver** — Produce a formatted intelligence summary for the named consumer

## Key Data Sources for Government and DoD Analysts

| Source | What It Reveals |
|---|---|
| **SAM.gov award data** | Competitor contract wins, agencies served, award sizes, vehicle usage |
| **USASpending.gov** | Historical spending patterns, incumbent concentration, NAICS footprint |
| **News and trade feeds** | Leadership changes, M&A activity, capability announcements, security incidents |
| **Federal Register** | Upcoming procurements, regulatory changes, agency priorities |
| **LinkedIn / public profiles** | Staffing changes, growth signals, new capability hires |

## How RAG Enables Cross-Source Querying

A Retrieval-Augmented Generation (RAG) system lets you ask natural language questions across all of these sources simultaneously — without building a custom database query for each. You ask: "What contracts has Competitor X won in the past 18 months in the cyber domain?" The system retrieves and synthesizes the answer from indexed award data.

This is configure-level work — no code required. You define the sources, the query types, and the output format.

---

**Your task:** Identify one competitor or market segment you monitor today using manual methods. Note how many hours per week are spent on that monitoring. This is the target for your intelligence agent configuration in Step 2.
