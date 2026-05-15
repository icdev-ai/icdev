---
ontology_id: icdev:mission:m-analyst-02-market-signal-rag:step:2
step_class: icdev:Lesson
---

# Configure Your RAG Pipeline

A RAG pipeline has four configuration decisions. Getting these right determines whether your system produces clean, citable answers or noisy, unreliable ones.

## Configuration Walkthrough

### 1. Select Document Types
Choose the document formats you will include in your pipeline. Prioritize sources where you currently spend the most search time.

**Common choices for government analysts:**
- Solicitations and award documents (PDF)
- Intelligence assessments and SITREPs (PDF, Word)
- Budget justification documents (PDF)
- Structured award data exports (CSV)
- Policy and regulatory documents (PDF)

### 2. Choose Your Chunking Strategy
Documents are broken into segments before indexing. The right segment size depends on your query type.

| Query Type | Recommended Chunk Size |
|---|---|
| Factual lookup ("What was the award value?") | Small chunks — paragraph or page |
| Synthesis ("What are the trends in X domain?") | Larger chunks — full section |
| Document comparison | Paired chunks with metadata tagging |

### 3. Define Query Types
Be explicit about the questions you expect to ask. This shapes how the system is configured.

**Examples:**
- Factual: "What is the ceiling value of Contract X?"
- Synthesis: "What are the three most common award patterns for NAICS 541519 in the past year?"
- Comparison: "How does Solicitation A differ from Solicitation B in scope?"

### 4. Set Grounding Requirements
For every query, define whether the system must cite a source.

**Recommended rule:** All factual and synthesis outputs must include document title, section or page reference, and publication date. Responses with no supporting document should be flagged as "inference only."

---

**Your task:** Complete all four configuration decisions for your document corpus. Write out your query types as specific example questions — at least three. These questions become the acceptance test for your RAG pipeline.
