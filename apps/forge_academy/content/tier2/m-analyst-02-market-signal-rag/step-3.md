# RAG Quality Check

A RAG system is only as valuable as the questions it can answer well. The quality check step forces you to define what "well" means for your specific use case — before you deploy.

## The Three Quality Dimensions

### 1. Accuracy
Does the system retrieve the right documents and quote from them correctly? The test: ask a question you already know the answer to and verify the system returns the correct citation.

### 2. Coverage
Does the corpus include the documents that matter most? A RAG system cannot retrieve what was never indexed. Coverage gaps — missing time periods, missing document types, missing sources — are the most common failure mode.

### 3. Grounding Rate
What percentage of the system's responses include a citation? A high-quality intelligence RAG system should cite sources on 90%+ of factual queries. Below 70% means the system is filling gaps with inference, not retrieval.

---

## Reflection Prompt

Answer three questions:

1. **What is the most important document corpus in your domain?**
   Name it specifically — not "our documents" but the actual collection, its owner, its format, and an estimate of its size.

2. **What are the top 3 questions you wish you could ask it instantly?**
   Write them as specific queries, not topic areas. "What is the trend in X?" is better than "Tell me about X."

3. **What is the biggest coverage gap in that corpus today?**
   What documents should be in the system but currently are not? (Older records? Classified materials that would require a separate deployment? Partner organization data?)

---

**Your task:** Write your answers to all three questions. The answer to question 3 is your first RAG improvement backlog item — document it and assign an owner before this step closes.
