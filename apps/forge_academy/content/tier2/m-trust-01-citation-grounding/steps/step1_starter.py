
"""
Tier 2 — TRUST: grounding, provenance, and fail-closed egress
Goal: Measure how well an AI claim is grounded in its evidence, decide whether to include
      / flag / abstain, stamp a provenance record, and enforce a fail-closed redaction gate.

Every LLM-generated artifact in ICDEV must pass the enforced TRUST chain (anti-hallucination
+ provenance + masking). The shared, surface-agnostic layer is tools/quality/
(content_grounding.py + citation_grounding.py — both mirrored into icdev/tools/quality/ and
guarded by coherence_checker.check_trust_coverage). This lab models four real moves:

  * attribution — compute_attribution_score(chunk, output): token-overlap RECALL of the
    evidence chunk inside the generated text (how much of the source actually shows up).
  * confidence — classify_confidence(score) -> include / flag / abstain (bands CONF_INCLUDE
    0.7, CONF_ABSTAIN 0.4).
  * provenance — the Provenance record (source_id, sha256, classification, attribution_score)
    persisted to the append-only rag_provenance_ledger (NIST AU-3).
  * fail-closed egress — the redaction gate (args/redaction_config.yaml: fail_closed) that
    BLOCKS egress when a required sanitizer cannot run, with an audited force override.

Everything here is pure stdlib — no LLM, no DB.
"""

import re

# Confidence bands (from citation_grounding.classify_confidence).
CONF_INCLUDE = 0.7
CONF_ABSTAIN = 0.4


def _tokens(text: str) -> set:
    """Lowercased alphanumeric word tokens of `text`."""
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


# ── Step 1: Attribution score (evidence recall) ───────────────────────────────

def attribution_score(chunk_text: str, output_text: str) -> float:
    """TODO: What fraction of the evidence chunk's tokens appear in the output?

    Using _tokens():  |chunk_tokens & output_tokens| / |chunk_tokens|
    (token-overlap recall, case-insensitive). Round to 4 decimals.
    An empty chunk (no tokens) scores 0.0 — you cannot attribute to nothing.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Confidence classification ─────────────────────────────────────────

def classify_confidence(score: float) -> str:
    """TODO: Bucket a grounding score into an action.

    score >= CONF_INCLUDE (0.7) -> "include"
    score <  CONF_ABSTAIN (0.4) -> "abstain"
    otherwise                   -> "flag"   (human should review)
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Provenance record ─────────────────────────────────────────────────

def build_provenance(source_id: str, sha256: str,
                     attribution_score: float = 0.0,
                     classification: str = "CUI") -> dict:
    """TODO: Build a provenance record (mirrors citation_grounding.Provenance.to_dict()).

    Return a dict with EXACTLY these keys:
        {"source_id": source_id, "sha256": sha256,
         "classification": classification, "attribution_score": attribution_score}
    Default classification is "CUI".
    """
    # YOUR CODE HERE
    pass


# ── Step 4: Fail-closed egress gate ───────────────────────────────────────────

def egress_gate(sanitizer_available: bool, fail_closed: bool = True,
                force: bool = False) -> dict:
    """TODO: The LLM-egress redaction gate.

    When the sanitizer IS available it runs and egress proceeds:
        {"allowed": True, "reason": "sanitized", "audited": False}
    When the sanitizer is UNAVAILABLE (a required sanitizer cannot run):
        * force=True        -> allow, but audited override:
              {"allowed": True, "reason": "override_audited", "audited": True}
        * fail_closed=True  -> BLOCK (this is the safe default — prem-p0-03 armed it True):
              {"allowed": False, "reason": "redaction_unavailable", "audited": False}
        * fail_closed=False -> allow, fail-open:
              {"allowed": True, "reason": "fail_open", "audited": False}
    """
    # YOUR CODE HERE
    pass


# Demo
if __name__ == "__main__":
    chunk = "Enable MFA for all privileged accounts"
    output = "The system requires MFA for all privileged accounts per policy."
    a = attribution_score(chunk, output)
    print("attribution:", a, "->", classify_confidence(a))
    print("provenance:", build_provenance("kb-42", "9f2c...", a))
    print("egress (no sanitizer, fail-closed):", egress_gate(False))
    print("egress (forced override):", egress_gate(False, force=True))
