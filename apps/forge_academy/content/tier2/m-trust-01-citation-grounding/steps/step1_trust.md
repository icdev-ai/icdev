---
ontology_id: icdev:mission:m-trust-01-citation-grounding:step:1
step_class: icdev:Lab
---

# TRUST — Grounding, Provenance, and Fail-Closed Egress

An LLM that sounds confident and cites nothing is a liability. ICDEV's **TRUST** invariants
(anti-hallucination + provenance + masking) make grounding non-optional: *every* generated
artifact runs the enforced TRUST chain before it can be promoted or exported. The shared,
surface-agnostic engine lives in `tools/quality/` — `content_grounding.py` and
`citation_grounding.py`, mirrored into `icdev/tools/quality/` and guarded by
`coherence_checker.py::check_trust_coverage` so the invariant can never silently drift.

The DIC citations lab taught you to **parse and gate `[source:]` markers**. This lab goes one
layer deeper into the *quality* of grounding and what happens at egress.

## 1. Attribution — how much of the source actually shows up?

A citation only means something if the generated text is really supported by the cited
evidence. `citation_grounding.compute_attribution_score(chunk_text, output_text)` measures
**recall** of the evidence chunk inside the output:

```
attribution = |chunk_tokens ∩ output_tokens| / |chunk_tokens|
```

High recall means the claim is well grounded in that source; low recall means the model
name-dropped a citation it barely used. Your `attribution_score()` computes this (case-
insensitive, empty chunk → 0.0).

## 2. Confidence — include, flag, or abstain

`classify_confidence(score)` turns a grounding score into an action using two bands
(`CONF_INCLUDE = 0.7`, `CONF_ABSTAIN = 0.4`):

- `>= 0.7` → **include** (well grounded, ship it)
- `0.4 – 0.7` → **flag** (a human should review)
- `< 0.4` → **abstain** (don't emit the claim at all)

Abstention is a feature: a system that declines to answer when it lacks support hallucinates
far less than one that always answers.

## 3. Provenance — an auditable chain of custody

Grounded output still needs a paper trail. Each source carries a **Provenance** record
(`citation_grounding.Provenance`: `source_id`, `sha256`, `classification`, `attribution_score`,
…) persisted to the append-only **`rag_provenance_ledger`** (NIST **AU-3**). Your
`build_provenance()` stamps the core record — content-addressed by hash, classification-marked,
and scored — so any claim can be traced back to the exact evidence that produced it.

## 4. Fail-closed egress

The last gate is redaction at LLM **egress**. The toggle `redaction.fail_closed`
(`args/redaction_config.yaml`) decides what happens when a *required* sanitizer cannot run
(missing, erroring): the router raises `RedactionUnavailableError` and **blocks** rather than
leak un-redacted CUI. This is the safe default (prem-p0-03 armed it to `true`). A human can
**force** past it — but the override is **audited** (`_audit(..., "..._override")`), never
silent. Only an explicit `fail_closed: false` lets output through un-sanitized (fail-open).
Your `egress_gate()` encodes exactly that decision tree.

> The companion toggle `redaction.mask_at_ingestion` (default off, opt-in) masks PII *before*
> chunking/hashing in `tools/rag/ingestion_manager.py`. Both toggles ship off-by-default and
> must never be removed — `check_trust_coverage` asserts their presence in config.

Open `step1_starter.py` and implement the four `TODO`s.
