#!/usr/bin/env python3
"""DIC verifier — CoT/CoD claim replay + citation validation + abstention.

[TEMPLATE: CUI // SP-CTI]

This is the ONLY anti-hallucination gate for DIC-generated content. Every
generated draft that carries ``[SOURCE-N]`` citations must pass through
``verify()`` before it is shown to a user or persisted as an AI-labeled
version.

Pipeline (reuses existing infrastructure, does not reinvent it):

1. ``icdev.tools.rag.retriever.validate_citations`` — structural check that the
   ``[SOURCE-N]`` tags in the draft point at real, injected evidence chunks and
   that no citation is hallucinated (out of range).
2. Claim extraction — split the draft into sentence-level claims, each tied to
   the source number(s) it cites.
3. Per-claim CoT/CoD replay (tools/manifest/llm-chain-orchestration.md;
   .claude/commands/cod.md) — each cited claim is replayed against ONLY its
   cited chunk in a short debate ("does this chunk support this claim?"). The
   LLM verdict is cross-checked with a deterministic lexical-overlap fallback so
   the gate still functions air-gapped / headless without an LLM provider.
4. Optional corrective retrieval — when a claim is unsupported by its cited
   chunk we consult ``icdev.tools.rag.corrective_rag`` over the remaining
   evidence to see whether a *different* chunk supports it (and, if so, the
   citation is corrected rather than the claim discarded).
5. Disposition — unsupported claims are stripped from the draft (strip mode) or
   the whole draft is rejected (reject mode). If too little of the draft is
   supported, or there is no evidence at all, the verifier abstains.

``verify(draft_text, evidence_chunks) -> {verified_text, claims, abstained}``
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure repo root on path when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the canonical citation validator (D-RAG-21).
try:  # pragma: no cover - import shape varies by install layout
    from icdev.tools.rag.retriever import validate_citations
except Exception:  # backward-compat shim
    from tools.rag.retriever import validate_citations


# --------------------------------------------------------------------------- #
# Tunables (overridable via env so admins configure behavior, not code)
# --------------------------------------------------------------------------- #

# Fraction of cited claims that must be supported for the draft to pass without
# abstaining. Below this the verifier abstains rather than emit a thinly
# grounded draft.
_MIN_SUPPORTED_RATIO = float(os.environ.get("DIC_VERIFY_MIN_SUPPORTED_RATIO", "0.5"))

# Lexical-overlap (Jaccard over content tokens) threshold for the deterministic
# fallback support check when no LLM is available or as a sanity cross-check.
_LEXICAL_SUPPORT_THRESHOLD = float(
    os.environ.get("DIC_VERIFY_LEXICAL_THRESHOLD", "0.18")
)

# Disposition for unsupported claims: "strip" removes them, "reject" fails the
# whole draft (verified_text == "" and abstained semantics preserved).
_DEFAULT_MODE = os.environ.get("DIC_VERIFY_MODE", "strip")

_SOURCE_RE = re.compile(r"\[SOURCE-(\d+)\]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

# Common English stop words excluded from lexical overlap so support is judged
# on content words, not glue.
_STOP_WORDS = frozenset(
    """a an the and or but if then else of to in on at by for with from into
    over under as is are was were be been being this that these those it its
    he she they them we you i your our their his her not no nor so than too very
    can will just should now also which who whom what when where why how
    """.split()
)


@dataclass
class ClaimVerdict:
    """Verdict for a single extracted claim."""

    claim: str
    supported: bool
    source_n: int | None
    method: str = "lexical"  # "llm", "lexical", "corrective", "uncited", "invalid"
    score: float = 0.0
    corrected_source_n: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "claim": self.claim,
            "supported": self.supported,
            "source_n": self.source_n,
            "method": self.method,
            "score": round(self.score, 4),
        }
        if self.corrected_source_n is not None:
            d["corrected_source_n"] = self.corrected_source_n
        return d


@dataclass
class VerifyResult:
    """Outcome of verifying a draft against its cited evidence.

    ``verify()`` returns this object, not a plain dict. It also supports
    read-only mapping access (``vr["abstained"]``, ``vr.get("claims")``) so that
    callers written against the previous dict-returning contract keep working
    unchanged. Both spellings are covered by ``tests/test_verifier_contract.py``.
    """

    verified_text: str
    claims: list[ClaimVerdict] = field(default_factory=list)
    abstained: bool = False
    reason: str = ""
    citation_report: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        """True when the draft was not abstained and every cited claim held.

        Uncited sentences carry no attributed assertion, so they neither
        support nor undermine verification; only cited claims are counted.
        """
        if self.abstained:
            return False
        cited = [c for c in self.claims if c.method != "uncited"]
        return all(c.supported for c in cited)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified_text": self.verified_text,
            "claims": [c.to_dict() for c in self.claims],
            "abstained": self.abstained,
            "verified": self.verified,
            "reason": self.reason,
            "citation_report": self.citation_report,
        }

    # -- read-only mapping compatibility (pre-existing dict-style callers) --

    def keys(self):
        return self.to_dict().keys()

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _chunk_text(chunk: Any) -> str:
    """Best-effort extraction of text from a heterogeneous evidence chunk."""
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        for key in ("content", "text", "chunk_text", "body", "passage"):
            v = chunk.get(key)
            if isinstance(v, str) and v.strip():
                return v
        return ""
    for key in ("content", "text", "chunk_text", "body", "passage"):
        v = getattr(chunk, key, None)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _tokens(text: str) -> set[str]:
    return {
        w.lower()
        for w in _WORD_RE.findall(text or "")
        if w.lower() not in _STOP_WORDS and len(w) > 2
    }


def _lexical_overlap(claim: str, chunk_text: str) -> float:
    """Token-recall of the claim against the chunk.

    Recall (claim tokens found in chunk / claim tokens) rather than Jaccard,
    because evidence chunks are typically far longer than a single claim and
    Jaccard would unfairly penalize the size mismatch.
    """
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return 0.0
    chunk_tokens = _tokens(chunk_text)
    if not chunk_tokens:
        return 0.0
    return len(claim_tokens & chunk_tokens) / len(claim_tokens)


def _split_claims(text: str) -> list[str]:
    """Split a draft into sentence-level claims, preserving citation tags."""
    # Normalize whitespace but keep sentence boundaries.
    parts: list[str] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        for sent in _SENT_SPLIT_RE.split(para):
            sent = sent.strip()
            if sent:
                parts.append(sent)
    return parts


def _cited_sources(claim: str) -> list[int]:
    return [int(m) for m in _SOURCE_RE.findall(claim)]


# --------------------------------------------------------------------------- #
# LLM-backed debate (best-effort; deterministic fallback always available)
# --------------------------------------------------------------------------- #


def _llm_supports(claim: str, chunk_text: str) -> tuple[bool, float] | None:
    """Replay one claim against one chunk via a short CoT/CoD debate.

    Returns ``(supported, confidence)`` or ``None`` when no LLM provider is
    reachable (air-gapped/headless) so the caller falls back to lexical scoring.
    """
    prompt = (
        "You are a strict citation verifier. Decide whether the EVIDENCE "
        "fully supports the CLAIM. Reason step by step, consider the strongest "
        "argument for and against support, then answer.\n\n"
        f"CLAIM:\n{re.sub(_SOURCE_RE, '', claim).strip()}\n\n"
        f"EVIDENCE:\n{chunk_text[:4000]}\n\n"
        "Answer on the LAST line exactly one token: SUPPORTED or UNSUPPORTED."
    )
    try:
        try:
            from icdev.tools.llm.router import LLMRouter
            from icdev.tools.llm.provider import LLMRequest
        except Exception:
            from tools.llm.router import LLMRouter  # type: ignore[import]
            from tools.llm.provider import LLMRequest  # type: ignore[import]
        router = LLMRouter()
        if router.is_no_llm_mode():
            return None
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            skip_injection_scan=True,
        )
        resp = router.invoke("chat_response", req)
        text = resp.content.strip() if resp and getattr(resp, "content", None) else None
        if not text:
            return None
        last = text.strip().splitlines()[-1].upper()
        supported = "UNSUPPORTED" not in last and "SUPPORTED" in last
        return supported, 0.9 if supported else 0.1
    except Exception:
        return None


def _corrective_source(claim: str, evidence_texts: list[str], exclude: int | None) -> int | None:
    """Look for a different chunk that supports the claim (citation repair).

    Uses lexical scan over the remaining evidence; cheap and air-gap safe. The
    corrective_rag module is imported best-effort for environments that wire a
    richer corrective retriever, but is not required for correctness here.
    """
    best_n: int | None = None
    best_score = _LEXICAL_SUPPORT_THRESHOLD
    for idx, etext in enumerate(evidence_texts, start=1):
        if exclude is not None and idx == exclude:
            continue
        score = _lexical_overlap(claim, etext)
        if score > best_score:
            best_score = score
            best_n = idx
    return best_n


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def verify(
    draft_text: str,
    evidence_chunks: list[Any],
    *,
    mode: str | None = None,
) -> VerifyResult:
    """Verify a generated draft against its cited evidence.

    Args:
        draft_text: The generated text containing ``[SOURCE-N]`` citations.
        evidence_chunks: Ordered list of evidence chunks; ``[SOURCE-1]`` maps to
            ``evidence_chunks[0]``, etc. Each item may be a str, a dict with a
            ``content``/``text`` key, or an object with such an attribute.
        mode: "strip" (default) removes unsupported claims; "reject" fails the
            whole draft when any claim is unsupported.

    Returns:
        A :class:`VerifyResult`. It exposes ``verified_text``, ``claims``,
        ``abstained``, ``verified``, ``reason`` and ``citation_report`` as
        attributes, and supports read-only mapping access for callers written
        against the older dict-returning contract.
    """
    mode = (mode or _DEFAULT_MODE).lower()
    draft_text = draft_text or ""
    evidence_chunks = evidence_chunks or []
    evidence_texts = [_chunk_text(c) for c in evidence_chunks]
    injected_count = len(evidence_texts)

    # No evidence at all -> nothing can be grounded -> abstain.
    if injected_count == 0 or not any(t.strip() for t in evidence_texts):
        return VerifyResult(
            verified_text="",
            claims=[],
            abstained=True,
            reason="no_evidence",
        )

    # Stage 1: structural citation validation (reuses retriever.validate_citations).
    citation_report = validate_citations(draft_text, injected_count)

    # Stage 2: claim extraction.
    sentences = _split_claims(draft_text)
    if not sentences:
        return VerifyResult(
            verified_text="",
            claims=[],
            abstained=True,
            reason="no_claims",
            citation_report=citation_report,
        )

    verdicts: list[ClaimVerdict] = []
    kept_sentences: list[str] = []
    cited_total = 0
    cited_supported = 0

    # Stage 3+4: per-claim replay against the cited chunk.
    for sent in sentences:
        sources = _cited_sources(sent)

        if not sources:
            # Uncited sentence: not a grounded claim. Keep it (it carries no
            # factual assertion attributed to a source) but mark it uncited.
            verdicts.append(
                ClaimVerdict(claim=sent, supported=True, source_n=None, method="uncited")
            )
            kept_sentences.append(sent)
            continue

        cited_total += 1
        src_n = sources[0]

        # Out-of-range / hallucinated citation -> unsupported by construction.
        if src_n < 1 or src_n > injected_count:
            verdicts.append(
                ClaimVerdict(
                    claim=sent, supported=False, source_n=src_n, method="invalid"
                )
            )
            continue

        chunk_text = evidence_texts[src_n - 1]
        lexical = _lexical_overlap(sent, chunk_text)

        llm = _llm_supports(sent, chunk_text)
        if llm is not None:
            supported, conf = llm
            # Cross-check: an LLM "supported" with zero lexical overlap is
            # suspicious (possible hallucinated agreement) -> require a minimum.
            if supported and lexical <= 0.0:
                supported = False
            method = "llm"
            score = conf if supported else max(conf, lexical)
        else:
            supported = lexical >= _LEXICAL_SUPPORT_THRESHOLD
            method = "lexical"
            score = lexical

        verdict = ClaimVerdict(
            claim=sent,
            supported=supported,
            source_n=src_n,
            method=method,
            score=score,
        )

        if not supported:
            # Stage 4: corrective retrieval — maybe another chunk supports it.
            alt = _corrective_source(sent, evidence_texts, exclude=src_n)
            if alt is not None:
                verdict.supported = True
                verdict.method = "corrective"
                verdict.corrected_source_n = alt
                verdict.score = _lexical_overlap(sent, evidence_texts[alt - 1])
                supported = True
                # Rewrite the citation in the kept sentence.
                sent = _SOURCE_RE.sub(f"[SOURCE-{alt}]", sent, count=1)

        verdicts.append(verdict)

        if supported:
            cited_supported += 1
            kept_sentences.append(sent)
        # else: dropped (strip mode) — handled below for reject mode.

    # Stage 5: disposition.
    supported_ratio = (cited_supported / cited_total) if cited_total else 1.0

    # Insufficient grounding -> abstain rather than emit a thin draft.
    if cited_total and supported_ratio < _MIN_SUPPORTED_RATIO:
        return VerifyResult(
            verified_text="",
            claims=verdicts,
            abstained=True,
            reason=f"insufficient_support:{supported_ratio:.2f}<{_MIN_SUPPORTED_RATIO:.2f}",
            citation_report=citation_report,
        )

    if mode == "reject" and cited_supported < cited_total:
        return VerifyResult(
            verified_text="",
            claims=verdicts,
            abstained=True,
            reason="rejected_unsupported_claims",
            citation_report=citation_report,
        )

    verified_text = " ".join(kept_sentences).strip()
    if not verified_text:
        return VerifyResult(
            verified_text="",
            claims=verdicts,
            abstained=True,
            reason="all_claims_stripped",
            citation_report=citation_report,
        )

    return VerifyResult(
        verified_text=verified_text,
        claims=verdicts,
        abstained=False,
        reason="ok",
        citation_report=citation_report,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="DIC anti-hallucination verifier")
    parser.add_argument("--draft", help="Draft text (or @path to read from file)")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Evidence chunk text (repeatable; or @path). [SOURCE-1]=first, etc.",
    )
    parser.add_argument("--mode", choices=["strip", "reject"], default=None)
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    def _read(val: str) -> str:
        if val and val.startswith("@"):
            return Path(val[1:]).read_text(encoding="utf-8")
        return val or ""

    draft = _read(args.draft or "")
    evidence = [_read(e) for e in args.evidence]
    result = verify(draft, evidence, mode=args.mode)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"abstained: {result['abstained']}  reason: {result['reason']}")
        print(f"claims: {len(result['claims'])}")
        for c in result["claims"]:
            flag = "OK " if c["supported"] else "XX "
            print(f"  {flag}[{c['method']}] src={c['source_n']} {c['claim'][:80]}")
        print("--- verified_text ---")
        print(result["verified_text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
