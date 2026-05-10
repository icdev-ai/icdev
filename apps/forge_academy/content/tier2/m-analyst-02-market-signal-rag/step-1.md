# RAG for Structured and Unstructured Intelligence

RAG — Retrieval-Augmented Generation — is the technology that lets you ask questions of your own document library in plain English, without needing a developer to write a database query for every question you have.

## What RAG Means for an Analyst

Think of RAG as giving an AI assistant access to your filing cabinet. Instead of the AI drawing only on its general training, it can retrieve specific documents — your contracts, your briefings, your research — and use them to answer your question with citations.

Without RAG: The AI answers from general knowledge and may hallucinate specifics.
With RAG: The AI retrieves the relevant document, quotes from it directly, and tells you exactly where the answer came from.

## How Different Data Types Are Handled

| Data Type | How It Gets Indexed | Query Type It Supports |
|---|---|---|
| **PDFs** (reports, contracts) | Chunked by page or section | Full-text search, summary, comparison |
| **Spreadsheets / CSVs** | Row-by-row structured index | Lookup, filter, aggregation queries |
| **Databases** | Direct query or export-then-index | Precise factual retrieval |
| **News articles** | Chunked with source metadata | Trend analysis, event lookup |

## Citation Grounding — Why It Matters

An AI response without a citation is an assertion. An AI response with a citation is a verifiable claim. In intelligence work, verifiability is not optional.

A well-configured RAG system includes source attribution on every answer: which document, which section, which date. This is what separates a RAG-powered intelligence tool from an AI chatbot.

---

**Your task:** Before Step 2, identify the most important document corpus in your domain — the collection of files you consult most often for your analytical work. Estimate how many documents it contains and what formats they are in. This is what you will configure a RAG pipeline around.
