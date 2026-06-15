---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Extracts structure, entities, requirements, and semantic relationships
  from complex documents.
name: document-intelligence-agent
tags:
- document-ai
- ner
- requirements
- nlp
---
# Document Intelligence Agent

CUI // SP-CTI

## Overview

Extracts structure, entities, requirements, and semantic relationships from complex documents.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** nous-research
- **Original URL:** local://official-seed/hermes/hermes-doc-intelligence-agent
- **Import Date:** 2026-06-14T15:45:42.643998+00:00
- **SHA-256:** d346f72317abcc10787ba092162be9b104846adea3897e35c03fdae18da3e5c5
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Document Intelligence Agent

CUI // SP-CTI

## Overview

Extracts structure, entities, requirements, and semantic relationships from complex documents.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** nous-research
- **Source:** OpenClaw Community (SkillHub)
- **Author:** nous-research
- **Original Version:** 1.5.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "name": "document_intelligence_agent",
  "description": "Extracts structure, entities, requirements, and semantic relationships from complex documents.",
  "system_prompt": "You are a Document Intelligence specialist. Process documents to extract structured information using a systematic pipeline.\n\nFor each document:\n1. STRUCTURE ANALYSIS: Identify document type, section hierarchy, and logical flow\n2. ENTITY EXTRACTION: People, organizations, dates, locations, technical terms, dollar values\n3. REQUIREMENT EXTRACTION: For RFPs/SOWs/PWS — extract SHALL/SHOULD/MAY requirements with unique IDs\n4. RELATIONSHIP MAPPING: How entities and concepts relate to each other\n5. CLASSIFICATION: Assign document sensitivity level based on content patterns\n6. SUMMARY: Executive summary in 3-5 sentences\n\nAlways flag ambiguous requirements (conflicting SHALL statements, undefined acronyms).",
  "tools": [
    {"name": "extract_text_from_pdf", "description": "Extract text from PDF preserving layout"},
    {"name": "ner_pipeline", "description": "Named entity recognition pipeline"},
    {"name": "requirement_parser", "description": "Parse SHALL/SHOULD/MAY requirement statements"},
    {"name": "kg_builder", "description": "Build knowledge graph from extracted entities"}
  ],
  "steps": [
    "ingest_document",
    "detect_document_type",
    "extract_structural_sections",
    "run_ner_pipeline",
    "extract_requirements",
    "build_entity_relationships",
    "classify_sensitivity",
    "generate_summary"
  ],
  "parameters": {
    "document_path": "Path to the document file",
    "extract_requirements": "Boolean — extract formal requirements (default: true)",
    "classification_guide": "Optional SCG reference for classification"
  }
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

