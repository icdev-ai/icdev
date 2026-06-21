---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Air-gap RAG tool using local FAISS vector store and sentence-transformers
  embeddings.
name: local-rag-tool
tags:
- rag
- local
- air-gap
- faiss
---
# Local RAG Tool

CUI // SP-CTI

## Overview

Air-gap RAG tool using local FAISS vector store and sentence-transformers embeddings.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** langchain-ai
- **Original URL:** local://official-seed/langchain/langchain-local-rag-tool
- **Import Date:** 2026-06-14T15:45:42.845711+00:00
- **SHA-256:** 0695f55394e55c3669ee7d8617dddb75b63298ff9ffd5795f35e5a6b775cb5ce
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Local RAG Tool

CUI // SP-CTI

## Overview

Air-gap RAG tool using local FAISS vector store and sentence-transformers embeddings.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** langchain-ai
- **Source:** OpenClaw Community (SkillHub)
- **Author:** langchain-ai
- **Original Version:** 0.3.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "name": "local_rag_search",
  "description": "Search a local FAISS vector store using sentence-transformers embeddings. No external API calls — fully air-gap safe. Returns top-k documents with relevance scores and source metadata.",
  "args_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search query in natural language"
      },
      "vector_store_path": {
        "type": "string",
        "description": "Local path to FAISS index directory"
      },
      "top_k": {
        "type": "integer",
        "description": "Number of documents to retrieve (default: 4)"
      },
      "score_threshold": {
        "type": "number",
        "description": "Minimum similarity score 0.0-1.0 (default: 0.5)"
      }
    },
    "required": ["query", "vector_store_path"]
  },
  "return_direct": false,
  "verbose": true
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

