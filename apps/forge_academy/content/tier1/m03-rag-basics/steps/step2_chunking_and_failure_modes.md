---
ontology_id: icdev:mission:m03-rag-basics:step:2
step_class: icdev:Lesson
---

# Chunking and Why Retrieval Fails

*Current as of October 2026.*

Step 1 retrieved whole documents. Real corpora are made of 80-page SSPs and STIG
benchmarks, so before anything is embedded it is cut into **chunks** — and most RAG
failures are decided at that moment, long before the LLM sees a single token.

## Chunking: what you are actually retrieving

Retrieval never returns a document. It returns the chunks whose embeddings sit
closest to the query's embedding. The chunk is the unit of retrieval, so the chunk
is the limit of what the model can possibly be shown.

- **Too large** — a chunk covering five controls produces one blurred embedding
  that matches nothing sharply, and it spends context-window space on four controls
  the question never asked about.
- **Too small** — a chunk holding one sentence loses the context that gives it
  meaning. "Set it to 600" is retrievable and useless once it is separated from
  the sentence naming the setting.
- **Overlap** — consecutive chunks share a few sentences, so a fact that straddles
  a boundary still appears whole in at least one chunk.
- **Structure-aware splitting** — split on the document's own boundaries (headings,
  control IDs, table rows) rather than every N characters, so each chunk is one
  coherent thing.

There is no universally correct chunk size; it depends on the documents and the
questions asked of them. In ICDEV, `tools/rag/chunker.py` does the splitting and
`tools/rag/chunking_templates.py` holds per-document-type settings for exactly that
reason.

## Retrieval failure modes

Each of these produces a confident, wrong answer with nothing in the output to say
so:

1. **The answer is not in the corpus.** Similarity search ALWAYS returns its top-K —
   there is always a nearest neighbour. "Retrieved 3 chunks" never means "found the
   answer"; it can mean "found the three least-irrelevant chunks".
2. **Vocabulary mismatch.** Keyword retrieval (what you built in step 1) misses a
   chunk that says "multi-factor authentication" when the user typed "2FA". Embedding
   retrieval narrows this gap; it does not close it for acronyms and control IDs.
3. **The answer was split across a chunk boundary.** Half the fact is in chunk 41,
   half in chunk 42, and only one of them ranks in the top-K.
4. **Top-K is wrong.** Too small and the answering chunk falls just outside the cut;
   too large and distractor chunks crowd the prompt.
5. **Distractors outrank the answer.** A chunk that repeats the query's words
   (a table of contents, a glossary) scores high and says nothing.
6. **The index is stale.** The policy changed last week; the index was built last
   quarter. RAG is only as current as its last re-index.

## The defence: measure retrieval separately

Because these failures are invisible in the final answer, evaluate retrieval on its
own: for a set of questions with known answering chunks, check whether those chunks
appear in the top-K. If retrieval misses, no prompt engineering downstream will fix
it. ICDEV's `tools/rag/corrective_rag.py` attacks the same problem from the other
side, running several retrieval strategies in parallel and merging the results so
one strategy's blind spot is covered by another.
