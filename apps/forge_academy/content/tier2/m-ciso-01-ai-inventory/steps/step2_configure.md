# Configure AI Governance Inventory

Set up the AI Inventory scanner for your organization.

## Configuration Fields

**Organization Name** — Your agency or component name. Used in the OMB-format inventory report header.

**Scan Scope** — What to include:
- **All registered systems** — Scans the full ICDEV system registry (recommended for first run)
- **New systems only** — Only systems added since the last inventory run
- **Specific systems** — Comma-separated system IDs for targeted scan

**OMB M-25-21 Check** — Enable to automatically classify each discovered AI system against the OMB M-25-21 use case taxonomy and flag any safety-impacting or rights-impacting systems missing required human oversight documentation.

**Include Shadow AI Detection** — Scans service dependencies, API call logs, and container manifests for undocumented AI components. Recommended: ON.

**Output Format** — `OMB Inventory CSV` (for official submission), `Executive Summary PDF`, or `Both`.

## Privacy note

The scanner reads system metadata and dependency manifests only — it does not read data processed by the systems. All scan results are stored locally in your ICDEV instance.

## What you get

- Complete AI system inventory in OMB M-25-21 format
- Classification of each system by use case (safety-impacting, rights-impacting, mission-operational)
- Gap list: systems missing required governance documentation
- Executive summary for CISO briefing (auto-generated, plain English, no jargon)
