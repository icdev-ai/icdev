---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Semantic search over a local document corpus with relevance ranking and
  citation.
name: semantic-search
tags:
- search
- rag
- embeddings
- retrieval
---
# Semantic Search

CUI // SP-CTI

## Overview

Semantic search over a local document corpus with relevance ranking and citation.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** openai
- **Original URL:** local://official-seed/openai/openai-semantic-search
- **Import Date:** 2026-06-14T15:45:42.772760+00:00
- **SHA-256:** 3dad9e895ebf30e6bc79d12cae75f7eb036ab8a265683c7bedb5642cb9c3d5f8
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Semantic Search

CUI // SP-CTI

## Overview

Semantic search over a local document corpus with relevance ranking and citation.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** openai
- **Source:** OpenClaw Community (SkillHub)
- **Author:** openai
- **Original Version:** 1.0.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "functions": [
    {
      "name": "semantic_search",
      "description": "Search a local document corpus using semantic similarity. Returns ranked results with source citations. Air-gap safe — uses local embeddings only.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Natural language search query"
          },
          "corpus_path": {
            "type": "string",
            "description": "Path to local document corpus or vector store"
          },
          "top_k": {
            "type": "integer",
            "description": "Number of results to return (default: 5, max: 20)"
          },
          "min_score": {
            "type": "number",
            "description": "Minimum relevance score threshold 0.0-1.0 (default: 0.7)"
          },
          "include_context": {
            "type": "boolean",
            "description": "Whether to include surrounding paragraph context (default: true)"
          }
        },
        "required": ["query", "corpus_path"]
      }
    },
    {
      "name": "index_documents",
      "description": "Index a directory of documents into a local vector store for semantic search.",
      "parameters": {
        "type": "object",
        "properties": {
          "document_dir": {
            "type": "string",
            "description": "Directory path containing documents to index"
          },
          "store_path": {
            "type": "string",
            "description": "Output path for the vector store"
          },
          "chunk_size": {
            "type": "integer",
            "description": "Token chunk size for document splitting (default: 512)"
          },
          "embedding_model": {
            "type": "string",
            "description": "Local embedding model to use (default: sentence-transformers/all-MiniLM-L6-v2)"
          }
        },
        "required": ["document_dir", "store_path"]
      }
    }
  ]
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

