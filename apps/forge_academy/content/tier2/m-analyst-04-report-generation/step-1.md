# AI-Assisted Report Generation with Citation Grounding

Every analyst writes the same types of reports repeatedly. A weekly SITREP. A quarterly trend assessment. An ad hoc brief on an emerging development. AI-assisted generation does not write the report for you — it drafts the structure and populates it from your data, leaving judgment and review to the human.

## How AI-Assisted Report Generation Works

The workflow has three stages:

1. **Ingest** — The system pulls from defined data sources (your RAG corpus, structured databases, news feeds, award data)
2. **Draft** — The system generates a structured document following your report template, with each claim linked to a source
3. **Review** — A named human reviewer reads, edits, and approves the draft before distribution

## Why Citation Grounding Is Non-Negotiable

In intelligence work, every claim must be traceable to a source. An AI-generated report without citations is an opinion document — and a liability. With citations, every statement can be verified, challenged, or updated.

**Rule:** If the AI cannot cite a source for a claim, it must either flag the claim as "inferred" or omit it. Analysts decide which.

## Human-in-the-Loop Review Workflow

AI-generated drafts are starting points, not finished products. The review workflow should include:

- **Factual accuracy check** — Verify key figures against primary sources
- **Tone and judgment layer** — The AI cannot assess significance; the analyst does
- **Classification review** — Ensure markings are correct before distribution
- **Approval authority** — Named individual who authorizes distribution

---

**Your task:** Identify the one report you produce most frequently. Note its template (if one exists), its primary data sources, and its typical time-to-produce. This is the target for your report generator configuration in Step 2.
