---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Classifies documents by type, sensitivity, and domain using Gemini functionDeclarations.
name: document-classifier
tags:
- classification
- document-ai
- nlp
---
# Document Classifier

CUI // SP-CTI

## Overview

Classifies documents by type, sensitivity, and domain using Gemini functionDeclarations.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** google-deepmind
- **Original URL:** local://official-seed/gemini/gemini-document-classifier
- **Import Date:** 2026-06-14T15:45:42.709340+00:00
- **SHA-256:** 198397b78eb9d6351edce60ebc053b666da999490efa73c93b70b8eede22bfcb
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Document Classifier

CUI // SP-CTI

## Overview

Classifies documents by type, sensitivity, and domain using Gemini functionDeclarations.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** google-deepmind
- **Source:** OpenClaw Community (SkillHub)
- **Author:** google-deepmind
- **Original Version:** 1.0.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "functionDeclarations": [
    {
      "name": "classify_document",
      "description": "Classify a document by type, sensitivity level, and primary domain. Returns structured classification with confidence scores.",
      "parameters": {
        "type": "object",
        "properties": {
          "document_text": {
            "type": "string",
            "description": "The full text content of the document to classify"
          },
          "classification_guide": {
            "type": "string",
            "description": "Optional Security Classification Guide (SCG) reference to apply"
          },
          "domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of candidate domains to classify against (e.g. ['legal', 'technical', 'financial'])"
          }
        },
        "required": ["document_text"]
      }
    },
    {
      "name": "apply_portion_markings",
      "description": "Apply CAPCO portion markings to a classified document. Returns the document with inline markings.",
      "parameters": {
        "type": "object",
        "properties": {
          "document_text": {
            "type": "string",
            "description": "The document text to apply markings to"
          },
          "overall_classification": {
            "type": "string",
            "enum": ["U", "CUI", "C", "S", "TS", "TS//SCI"],
            "description": "Overall document classification level"
          },
          "scg_reference": {
            "type": "string",
            "description": "Security Classification Guide identifier"
          }
        },
        "required": ["document_text", "overall_classification"]
      }
    },
    {
      "name": "extract_requirements",
      "description": "Extract formal requirements from a document (SHALL/SHOULD/MAY/MUST statements).",
      "parameters": {
        "type": "object",
        "properties": {
          "document_text": {
            "type": "string",
            "description": "Document text to extract requirements from"
          },
          "requirement_prefix": {
            "type": "string",
            "description": "Optional prefix for requirement IDs (e.g. 'REQ-', 'PWS-')"
          },
          "include_rationale": {
            "type": "boolean",
            "description": "Whether to extract rationale text alongside requirements"
          }
        },
        "required": ["document_text"]
      }
    }
  ]
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

