# Configure Your Report Generator

A report generator is only as good as its configuration. The four decisions below determine what the system produces and whether it meets your quality bar.

## Configuration Walkthrough

### 1. Report Template Selection
Choose the report type your generator produces. Each type has a different structure and different citation requirements.

| Template | Structure | Primary Use |
|---|---|---|
| **SITREP** | Situation / Background / Current Activity / Outlook | Operational briefings, leadership updates |
| **Intelligence Assessment** | Key Judgments / Analysis / Evidence / Confidence Levels | Strategic analysis, decision support |
| **Trend Report** | Executive Summary / Trend Narrative / Data Charts / Implications | Periodic domain monitoring |
| **Ad Hoc Brief** | Flexible, issue-specific | Rapid-response, emerging developments |

### 2. Data Source Inputs
Define what data flows into this report:

- Primary structured source (e.g., award database, spending data)
- Primary unstructured source (e.g., news corpus, document library)
- Any manual analyst inputs required (e.g., field observations, leadership guidance)

### 3. Citation Requirements
Specify the grounding standard for this report type:

- **Every quantitative claim** must cite data source and time period
- **Every named entity** (organization, person, program) must be verified against a structured source
- **Inferences and outlooks** must be labeled as analyst judgment, not retrieved facts

### 4. Review Workflow

| Step | Actor | Action | Deadline |
|---|---|---|---|
| Draft generation | AI system | Generate from template + sources | T+0 |
| Factual review | Primary analyst | Verify citations, edit tone | T+2 hours |
| Approval | Team lead or supervisor | Authorize distribution | T+4 hours |
| Distribution | Analyst | Deliver to named consumer | T+4 hours |

---

**Your task:** Complete all four configuration decisions for the report you identified in Step 1. Pay particular attention to the citation requirements — this is where most report generators fail the first time they go into production.
