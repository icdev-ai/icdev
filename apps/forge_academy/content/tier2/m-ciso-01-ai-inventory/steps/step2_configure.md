---
ontology_id: icdev:mission:m-ciso-01-ai-inventory:step:2
step_class: icdev:Lesson
---

# Configure AI Governance Inventory

These are the settings an AI inventory run takes. This step is a read-through. There is no form to fill in. When you've read what each setting does, click **Understood → Continue**.

## Settings

**Organization Name** — Your agency or component name. Used in the OMB-format inventory report header.

**Scan Scope** — What to include:
- **All registered systems** — Scans the full ICDEV system registry (recommended for first run)
- **New systems only** — Only systems added since the last inventory run
- **Specific systems** — Comma-separated system IDs for targeted scan

**OMB M-25-21 Check**: classifies each discovered AI use case as high-impact or not, and flags any high-impact use that is missing the required minimum practices (impact assessment, testing, monitoring, human oversight). In ICDEV this is `python tools/compliance/omb_m25_21_assessor.py --project-id <id> --json`.

**Include Shadow AI Detection** — Scans service dependencies, API call logs, and container manifests for undocumented AI components. Recommended: ON.

**Output Format**: a machine-readable inventory export (`python tools/compliance/ai_inventory_manager.py --project-id <id> --export --json`), an executive summary for the CISO, or both.

## Privacy note

The scanner reads system metadata and dependency manifests only — it does not read data processed by the systems. All scan results are stored locally in your ICDEV instance.

## What you get

- A complete AI use case inventory
- Each use case classified as high-impact or not
- Gap list: systems missing required governance documentation
- Executive summary for CISO briefing (auto-generated, plain English, no jargon)
