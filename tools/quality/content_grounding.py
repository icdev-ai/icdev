"""Surface-agnostic anti-hallucination utilities for LLM-drafted content.

Extracted from tools/govcon/rfi_grounding.py so any drafting surface
(RFI workbench, proposals, DIC document generator, Tech Writer) can reuse
the deterministic pieces. The deterministic pieces are pure regex/dict —
no DB, no Flask — and ``ground_content`` is LLM-FREE by default (an optional
LLM-assisted pass is off unless a caller injects a router callable).

  - find_placeholders(text)         -> unresolved [BRACKETED] tokens
  - substitute_facts(text, pairs)   -> replace tokens with known values
  - placeholder_findings(sections)  -> per-section unresolved-token report,
                                       ready for an export/publish gate
  - check_numeric_claims(sections)  -> cross-section numeric conflicts
                                       (ROM totals, prototype timelines)
  - ground_content(out, snippets)   -> semantic claim-vs-context grounding
                                       score + ungrounded-claim list. This is
                                       the real grounding check (not a
                                       placeholder scan): it segments the
                                       output into sentences and scores how
                                       well each is supported by the retrieval
                                       snippets injected into the LLM call.

Where ``find_placeholders`` removes the *opportunity* to hallucinate,
``ground_content`` measures whether the claims that were made are actually
*supported* by the evidence. Both are used by the Cortex governance gate.

Surface-specific logic (which facts are substitutable, which citation
structures are valid) stays in the caller — see rfi_grounding.py for the
RFI-structure validator built on top of this module.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# ── Placeholders ──────────────────────────────────────────────────────────────

# [UPPERCASE_TOKEN] style placeholders: starts with an uppercase letter, then
# uppercase letters/digits/space/underscore/dash/slash/&/#. Excludes markdown
# links ("[Title](url)" — mixed case and followed by "(") and checkboxes.
_PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9 _/&#.-]{1,40})\](?!\()")


def find_placeholders(text: str) -> list[str]:
    """Return sorted unique unresolved placeholder tokens like [UEI_NUMBER]."""
    if not text:
        return []
    return sorted({f"[{m.group(1)}]" for m in _PLACEHOLDER_RE.finditer(text)})


def substitute_facts(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """Replace placeholder tokens with deterministically-known values.

    Args:
        text: draft content.
        pairs: (regex_pattern, replacement) tuples; patterns are matched
            case-insensitively. Pairs with empty replacements are skipped.

    Returns (new_text, substitutions) where substitutions is a list of
    {pattern, value, count}. Tokens without a known value are left for
    find_placeholders() / the gate to flag.
    """
    if not text:
        return text, []
    substitutions = []
    for pattern, value in pairs:
        if not value:
            continue
        compiled = re.compile(pattern, re.IGNORECASE)
        new_text, count = compiled.subn(str(value), text)
        if count:
            substitutions.append({"pattern": pattern, "value": str(value), "count": count})
            text = new_text
    return text, substitutions


def placeholder_findings(sections: list[dict], content_keys: tuple[str, ...] = ("content", "ai_draft")) -> list[dict]:
    """Scan sections for unresolved placeholder tokens.

    Each section dict needs an identifying field (item_number / title / id —
    first one present is used) and content under one of content_keys.
    Returns [{item_number, placeholders[]}] — empty list means gate passes.
    """
    findings = []
    for sec in sections:
        content = ""
        for key in content_keys:
            if sec.get(key):
                content = sec[key]
                break
        tokens = find_placeholders(content)
        if tokens:
            label = sec.get("item_number") or sec.get("title") or sec.get("id") or "?"
            findings.append({"item_number": label, "placeholders": tokens})
    return findings


# ── Semantic content grounding (claim vs. context) ────────────────────────────
#
# The Cortex governance content-grounding gate used to be a bare placeholder
# scan (find_placeholders) plus a single token-overlap recall of the whole
# output against a chunk. ground_content is the real thing: per-sentence
# support scoring of the output against the retrieval snippets injected into
# the LLM call. LLM-free heuristic by default (token + bigram overlap
# precision), with an optional LLM-assisted pass a caller can inject.

# Confidence bands are the single source of truth for "grounded enough" — the
# same >=0.7 include / 0.4 abstain bands citation_grounding uses. Imported
# here (not re-declared) so every TRUST surface shares one seam. Defensive
# fallback keeps this module importable if the companion is ever absent.
try:  # pragma: no cover - companion is always present in tools/quality
    from .citation_grounding import CONF_ABSTAIN as _CONF_ABSTAIN
except ImportError:  # pragma: no cover
    _CONF_ABSTAIN = 0.4

# Small English stopword set: grounding must not be satisfied by shared
# function words ("the", "is", "of"). Kept intentionally short.
_STOPWORDS = frozenset("""
a an and are as at be but by for from has have if in into is it its of on or
that the their this to was were will with not no be been being do does did
you your we our they them he she his her which who whom whose can could should
would may might must shall over under between within per via such than then
""".split())

# Citation tags must not count as evidence overlap — strip them before scoring.
_CITE_TAG_RE = re.compile(r"\[source:[^\]]*\]|\[SOURCE-\d+\]", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_RE = re.compile(r"\b\w+\b")


def _content_tokens(text: str) -> list[str]:
    """Lowercase word tokens with stopwords and pure-digit noise removed."""
    return [
        t for t in _WORD_RE.findall(text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return set(zip(tokens, tokens[1:]))


def _sentences(text: str) -> list[str]:
    """Segment text into sentences, citation tags stripped, blanks dropped."""
    cleaned = _CITE_TAG_RE.sub(" ", text or "")
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(cleaned) if s.strip()]


def _sentence_support(sentence: str, ctx_tokens: set[str], ctx_bigrams: set) -> float:
    """Fraction of a sentence's content supported by the context [0,1].

    Blends unigram-overlap precision (0.7) with bigram-overlap precision (0.3):
    the unigram term rewards vocabulary reuse, the bigram term penalizes
    word-salad that reuses tokens in an order the context never states.
    """
    tokens = _content_tokens(sentence)
    if not tokens:
        return 1.0  # no factual content (heading, "Yes.") — neutral, not ungrounded
    uni = sum(1 for t in tokens if t in ctx_tokens) / len(tokens)
    bg = _bigrams(tokens)
    if bg:
        bi = len(bg & ctx_bigrams) / len(bg)
        return round(0.7 * uni + 0.3 * bi, 4)
    return round(uni, 4)


def ground_content(
    output_text: str,
    context_snippets,
    *,
    method: str = "heuristic",
    support_floor: Optional[float] = None,
    llm_invoke: Optional[Callable[[str], str]] = None,
) -> dict:
    """Score how well ``output_text`` is grounded in ``context_snippets``.

    This is semantic claim-vs-context grounding, NOT a placeholder scan. Each
    output sentence is scored for how much of its content is supported by the
    union of the retrieval snippets; the overall score is the token-weighted
    mean of the per-sentence scores.

    Args:
        output_text: the model output to check.
        context_snippets: the retrieval snippets injected into the LLM call —
            an iterable of strings (or dicts/objects carrying ``content``/
            ``text``). Empty/None means there is nothing to ground against.
        method: ``"heuristic"`` (LLM-free, default) or ``"llm"`` (only used
            when ``llm_invoke`` is also supplied; any failure degrades to the
            heuristic — the heuristic is always the floor).
        support_floor: per-sentence score below which a sentence is reported
            as an ungrounded claim. Defaults to the shared ``CONF_ABSTAIN``
            band from ``citation_grounding`` — one source of truth for "grounded
            enough" across every TRUST surface.
        llm_invoke: optional ``(prompt) -> str`` callable routed by a caller
            through ``LLMRouter`` (no model ids here). Enables ``method="llm"``.

    Returns:
        ``{score, ungrounded_claims, method, sentence_count}`` where ``score``
        is in [0, 1]. When there is no context to ground against, returns
        ``score=0.0`` and ``method="no_context"`` so the caller can fall back
        to the placeholder scan safely rather than mistaking "no evidence" for
        "fabricated".
    """
    snippets = _snippet_texts(context_snippets)
    floor = _CONF_ABSTAIN if support_floor is None else float(support_floor)

    if not snippets or not (output_text or "").strip():
        return {
            "score": 0.0,
            "ungrounded_claims": [],
            "method": "no_context",
            "sentence_count": 0,
        }

    if method == "llm" and llm_invoke is not None:
        llm_result = _ground_content_llm(output_text, snippets, llm_invoke, floor)
        if llm_result is not None:
            return llm_result
        # fall through to the heuristic — it is always the floor

    ctx_tokens: set[str] = set()
    ctx_bigrams: set = set()
    for snip in snippets:
        toks = _content_tokens(snip)
        ctx_tokens.update(toks)
        ctx_bigrams.update(_bigrams(toks))

    weighted_sum = 0.0
    weight_total = 0
    scored = 0
    ungrounded: list[str] = []
    for sentence in _sentences(output_text):
        toks = _content_tokens(sentence)
        if not toks:
            continue
        scored += 1
        s = _sentence_support(sentence, ctx_tokens, ctx_bigrams)
        weighted_sum += s * len(toks)
        weight_total += len(toks)
        if s < floor:
            ungrounded.append(sentence)

    score = round(weighted_sum / weight_total, 4) if weight_total else 1.0
    return {
        "score": score,
        "ungrounded_claims": ungrounded,
        "method": "heuristic",
        "sentence_count": scored,
    }


def _snippet_texts(context_snippets) -> list[str]:
    """Normalize injected context into a list of non-empty snippet strings."""
    if not context_snippets or isinstance(context_snippets, (int, bool)):
        return []
    if isinstance(context_snippets, str):
        return [context_snippets] if context_snippets.strip() else []
    texts: list[str] = []
    for src in context_snippets:
        if isinstance(src, str):
            text = src
        elif isinstance(src, dict):
            text = src.get("content") or src.get("text") or ""
        else:
            text = getattr(src, "content", "") or getattr(src, "text", "") or ""
        if text and str(text).strip():
            texts.append(str(text))
    return texts


def _ground_content_llm(
    output_text: str,
    snippets: list[str],
    llm_invoke: Callable[[str], str],
    floor: float,
) -> Optional[dict]:
    """Optional LLM-assisted grounding. Returns None on any failure.

    ``llm_invoke`` is a caller-provided ``(prompt) -> str`` closure over the
    platform ``LLMRouter`` routing chains — this module never names a model.

    Deterministic-picker (agx-pick-02): the LLM no longer emits a free-form
    grounding score. It labels EACH answer claim with one of a 3-value enum
    (grounded | partial | ungrounded) and Python composes the support ratio
    (:func:`compose_grounding`). A 3-token vocabulary is portable across model
    families, and an unknown/malformed token is treated as UNGROUNDED (fail
    closed) rather than silently upgrading an unsupported claim. Anything
    unparseable returns None so the caller falls back to the heuristic floor.
    """
    import json

    from tools.quality.categorical_scoring import GROUNDING_VOCAB, compose_grounding

    claims = _sentences(output_text)
    if not claims:
        return None
    context_block = "\n\n".join(f"[{i + 1}] {s}" for i, s in enumerate(snippets))
    claim_block = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    prompt = (
        "You are a grounding judge. For EACH numbered CLAIM decide whether the "
        "CONTEXT supports it. Choose exactly one label per claim:\n"
        "  grounded   = fully supported by the context\n"
        "  partial    = partially supported / needs an unstated leap\n"
        "  ungrounded = not supported by the context\n\n"
        "Respond with STRICT JSON only, no prose — a list of labels in claim "
        'order:\n{"labels": ["grounded"|"partial"|"ungrounded", ...]}\n\n'
        f"CONTEXT:\n{context_block}\n\nCLAIMS:\n{claim_block}\n"
    )
    try:
        raw = llm_invoke(prompt)
        if not raw:
            return None
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
        labels = data.get("labels")
        if not isinstance(labels, list) or not labels:
            return None
        # Align labels to claims: pad short lists with "ungrounded" (fail closed),
        # truncate long ones. compose_grounding maps unknown tokens to ungrounded.
        labels = [str(x) for x in labels][: len(claims)]
        if len(labels) < len(claims):
            labels += ["ungrounded"] * (len(claims) - len(labels))
        composed = compose_grounding(labels)
        ungrounded = [
            claims[i]
            for i, lab in enumerate(labels)
            if GROUNDING_VOCAB.get(str(lab).strip().lower(), 0.0) < floor
        ]
        return {
            "score": composed["score"],
            "ungrounded_claims": ungrounded,
            "method": "llm",
            "sentence_count": composed["claim_count"],
            "vocabulary_version": composed["vocabulary_version"],
        }
    except Exception:  # noqa: BLE001 — LLM path is best-effort; heuristic is the floor
        return None


# ── Cross-section numeric consistency ─────────────────────────────────────────

_MONEY_RE = re.compile(
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(M\b|K\b|million|thousand)?", re.IGNORECASE
)
_MONTHS_RE = re.compile(r"\b(\d{1,2})\s*(?:-|to\s+)?\s*months?\b", re.IGNORECASE)


def _normalize_money(num: str, suffix: str | None) -> float:
    val = float(num.replace(",", ""))
    sfx = (suffix or "").lower()
    if sfx in ("m", "million"):
        val *= 1_000_000
    elif sfx in ("k", "thousand"):
        val *= 1_000
    return val


def check_numeric_claims(sections: list[dict]) -> list[dict]:
    """Detect cross-section numeric conflicts in ROM totals and prototype
    timeline months. Returns conflict dicts:
    {type, sections[], message, severity}."""
    rom_totals: dict[str, set[float]] = {}
    proto_months: dict[str, set[int]] = {}

    for s in sections:
        text = s.get("content") or s.get("ai_draft") or ""
        item = s.get("item_number", "?")
        if not text:
            continue
        for m in _MONEY_RE.finditer(text):
            ctx = text[max(0, m.start() - 60):m.start()].lower()
            if "rom total" in ctx or "total rom" in ctx or ("total" in ctx and "rom" in ctx):
                rom_totals.setdefault(item, set()).add(
                    _normalize_money(m.group(1), m.group(2)))
        for m in _MONTHS_RE.finditer(text):
            ctx = text[max(0, m.start() - 80):min(len(text), m.end() + 40)].lower()
            if "prototype" in ctx and ("award" in ctx or "deliver" in ctx or "working" in ctx):
                proto_months.setdefault(item, set()).add(int(m.group(1)))

    conflicts = []
    all_roms = {v for vals in rom_totals.values() for v in vals}
    if len(all_roms) > 1:
        conflicts.append({
            "type": "rom_total_mismatch",
            "sections": sorted(rom_totals.keys()),
            "message": "ROM total differs across sections: "
                       + ", ".join(f"${v:,.0f}" for v in sorted(all_roms)),
            "severity": "error",
        })
    all_months = {v for vals in proto_months.values() for v in vals}
    if len(all_months) > 1:
        conflicts.append({
            "type": "prototype_timeline_mismatch",
            "sections": sorted(proto_months.keys()),
            "message": "Prototype timeline differs across sections: "
                       + ", ".join(f"{v} months" for v in sorted(all_months)),
            "severity": "warning",
        })
    return conflicts
