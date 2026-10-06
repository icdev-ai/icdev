---
ontology_id: icdev:mission:m03-rag-basics:step:3
step_class: icdev:Lesson
---

# Grounding Is Not Retrieval

*Current as of October 2026.*

Retrieval and grounding are easy to conflate, and the difference is where RAG
systems lose their users' trust.

- **Retrieval** is the act of putting documents into the prompt. It succeeds when
  the right chunks arrive.
- **Grounding** is a property of the ANSWER: every claim it makes is supported by
  the evidence it was given. It succeeds only when the answer stays inside that
  evidence.

Retrieval can succeed while grounding fails. The right chunk is in the context and
the model still answers from its training data, blends two sources into a claim
neither one makes, or "rounds" a 30-day deadline into "about a month". Having the
documents in the prompt is necessary for a grounded answer; it is not sufficient.

## Making grounding checkable

An ungrounded claim looks exactly like a grounded one, so grounding has to be made
visible:

1. **Cite inline.** Every claim carries a marker naming the chunk it came from, such
   as `[source: stig-002]`. A claim with no citation is a claim nobody can check.
2. **Validate the citations.** A citation is itself a claim. Check that the cited
   source was actually retrieved for this question and that it supports the
   sentence — a fabricated or misattributed citation is worse than none, because it
   borrows authority the answer does not have.
3. **Abstain when the evidence is silent.** If the retrieved context does not contain
   the answer, the correct output is "the provided documents do not answer this",
   not a fluent guess. This is the answer to the first failure mode in the previous
   step: retrieval always returns something, so the GENERATION step is where
   "not found" has to be said.

## In ICDEV

ICDEV requires every LLM-generated artifact to carry inline `[source: …]` citations
that are validated against its evidence, and blocks export on citation defects. The
shared implementation is `tools/quality/citation_grounding.py`; every drafting
surface builds on it rather than re-implementing citation parsing.

## The takeaway

Evaluate the two separately. Retrieval is measured by whether the answering chunks
came back. Grounding is measured by whether each claim in the answer is supported by
a cited chunk. A system can score well on one and badly on the other, and a single
"the answer looks right" check hides which one failed.
