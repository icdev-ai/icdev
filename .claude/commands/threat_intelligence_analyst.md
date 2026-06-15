---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Analyzes threat intelligence reports and extracts structured IOCs, TTPs,
  and risk context.
name: threat-intelligence-analyst
tags:
- cybersecurity
- threat-intel
- ioc
- ttp
---
# Threat Intelligence Analyst

CUI // SP-CTI

## Overview

Analyzes threat intelligence reports and extracts structured IOCs, TTPs, and risk context.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** nous-research
- **Original URL:** local://official-seed/hermes/hermes-threat-intel-analyst
- **Import Date:** 2026-06-14T15:45:42.613976+00:00
- **SHA-256:** dcf4000180b15fd78def9a49e2e819cc10a63b5a465030c114bdf4418c914b8c
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Threat Intelligence Analyst

CUI // SP-CTI

## Overview

Analyzes threat intelligence reports and extracts structured IOCs, TTPs, and risk context.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** nous-research
- **Source:** OpenClaw Community (SkillHub)
- **Author:** nous-research
- **Original Version:** 2.0.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "name": "threat_intelligence_analyst",
  "description": "Analyzes threat intelligence reports and extracts structured IOCs, TTPs, and risk context.",
  "system_prompt": "You are a senior Cyber Threat Intelligence (CTI) analyst with expertise in MITRE ATT&CK, Diamond Model, and structured threat reporting. Extract and structure all threat indicators from the provided content.\n\nOutput format:\n1. THREAT ACTOR: Name, aliases, suspected origin, motivation\n2. CAMPAIGN: Timeline, targets, TTPs (MITRE ATT&CK IDs)\n3. INDICATORS OF COMPROMISE:\n   - IPs: [list]\n   - Domains: [list]\n   - File Hashes (MD5/SHA256): [list]\n   - URLs: [list]\n   - Email: [list]\n4. MITRE ATT&CK MAPPING: List each TTP with technique ID and description\n5. RISK ASSESSMENT: Severity (Critical/High/Medium/Low), affected sectors, recommended mitigations\n\nAlways cite the source sentence for each IOC extracted.",
  "tools": [
    {"name": "search_threat_db", "description": "Search internal threat database for historical context"},
    {"name": "enrich_ioc", "description": "Enrich an IOC with reputation data from local feed"},
    {"name": "mitre_lookup", "description": "Look up MITRE ATT&CK technique details by ID"},
    {"name": "generate_stix", "description": "Generate STIX 2.1 bundle from extracted intelligence"}
  ],
  "steps": [
    "parse_report_content",
    "extract_threat_actor_profile",
    "extract_campaign_details",
    "extract_all_iocs",
    "map_to_mitre_attack",
    "enrich_iocs_from_local_feeds",
    "assess_risk_and_impact",
    "generate_stix_bundle",
    "produce_analyst_summary"
  ],
  "parameters": {
    "report_text": "Raw threat intelligence report text",
    "classification": "Classification level: U, CUI, SECRET",
    "output_format": "Output format: json, stix, markdown (default: markdown)"
  }
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

