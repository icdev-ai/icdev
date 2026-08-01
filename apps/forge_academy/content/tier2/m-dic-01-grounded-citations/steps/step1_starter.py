
"""
Tier 2 — Document Intelligence Canvas (DIC): Grounded Citations
Goal: Parse [source: ...] citations from generated text, validate each against the
      evidence set, and gate the artifact so nothing ungrounded ships.

The DIC pipeline is ingest -> search -> generate -> HITL review (MCP tools
dic_ingest / dic_search / dic_chat / dic_generate). Every generated claim must carry
an inline [source: <id>] marker that resolves to real ingested evidence. The shared
citation-grounding layer (tools/quality/citation_grounding.py) parses those markers
and a citation_guard blocks promote/export on defects (mirroring the placeholder
guard). This exercise builds a miniature version of that guard with the stdlib `re`.
"""

import re

# Matches [source: doc-1], [source:doc-1], [source:  Doc 1 ] — id is the inner text.
_CITATION_RE = re.compile(r"\[source:\s*([^\]]+?)\s*\]", re.IGNORECASE)


# ── Step 1: Extract citations ─────────────────────────────────────────────────

def extract_citations(text: str) -> list[str]:
    """TODO: Return every cited source id in order of appearance.

    Use _CITATION_RE.findall(text). Each match is the id inside the marker.
    Preserve order and duplicates (two claims may cite the same source).
    An empty / None text returns [].
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Validate citations against the evidence set ───────────────────────

def validate_citations(text: str, evidence_ids: set) -> dict:
    """TODO: Check that every cited source exists in the ingested evidence.

    1. cited = extract_citations(text)
    2. grounded   = [c for c in cited if c in evidence_ids]
    3. ungrounded = [c for c in cited if c not in evidence_ids]
    4. ok = there is at least one citation AND there are no ungrounded citations
    Return:
        {"cited": cited, "grounded": grounded, "ungrounded": ungrounded, "ok": ok}
    """
    # YOUR CODE HERE
    pass


# ── Step 3: The citation guard (promote/export gate) ──────────────────────────

def citation_guard(text: str, evidence_ids: set) -> dict:
    """TODO: Return a gate decision with human-readable defects.

    Build a list of defect strings:
      * If extract_citations(text) is empty:
            "no citations: every claim must carry a [source:] marker"
      * For each UNGROUNDED citation id `c` (not in evidence_ids):
            f"ungrounded citation: [source: {c}] has no matching evidence"
    passed = (defects list is empty)
    Return: {"passed": passed, "defects": defects}

    (In real DIC a HITL reviewer may force_export past defects, with an audit
    record — but the default gate fails closed.)
    """
    # YOUR CODE HERE
    pass


# Demo
if __name__ == "__main__":
    evidence = {"stig-001", "nist-ac-2", "sbom-42"}
    good = "Enable MFA [source: nist-ac-2]. The image is signed [source: sbom-42]."
    bad = "The system is fully compliant [source: made-up-7]. No findings remain."
    print("good:", citation_guard(good, evidence))
    print("bad: ", citation_guard(bad, evidence))
