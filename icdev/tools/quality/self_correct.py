# CUI // SP-CTI
"""Bounded MONOTONE revise-and-recheck loop (trust-self-01).

``trust_gate`` says *no*. This says *here is what is wrong, fix exactly that* —
and then re-runs the same validators over the result. One invariant decides
whether the whole thing is worth having:

    **A round is accepted only if the finding count STRICTLY DECREASES.**

Everything else follows from that. A round that adds findings — or holds them
level — is DISCARDED and the best prior state stands, so the loop cannot drift
into optimising for the checker. Without it, "revise until the gate is happy"
degenerates into the two behaviours every self-correction paper warns about:
the model rewrites the sentence the guard complained about and breaks two it did
not, or it deletes the failing claim and calls the document clean.

The second of those is why :data:`DEFAULT_MIN_RETENTION` exists. Finding count
alone has a trivial global optimum — an empty document has zero findings — so a
candidate that drops below a fraction of the original's non-whitespace length is
rejected regardless of how good its score looks. Deleting an unsupportable
sentence is a legitimate fix; deleting the document is not, and the monotone
invariant on its own cannot tell them apart.

The revision prompt is TARGETED, never "here is the document, make it better".
``citation_grounding.decompose_claims`` returns ORIGINAL-TEXT offsets, and every
guard in the TRUST spine reports ``item_number`` as a 1-based index into exactly
that decomposition (``claim_gate``, ``kg_gate``), so a finding can be resolved
back to the precise span it came from and quoted verbatim in the prompt. A
guard that reports at document level (``placeholder_guard``, ``citation_guard``)
is listed separately rather than being silently dropped.

**Fail closed.** Nothing here can UNBLOCK anything:

* Every ``blocked`` value returned was MEASURED by a completed validator run
  over the text being returned. The loop never asserts a posture it did not
  observe.
* No validators supplied → ``unmeasurable`` and ``blocked=True``. Unmeasured is
  not clean — the same discipline as ``trust_gate`` invariant 2 and
  ``chain_sweep``'s pre_cutover-vs-broken split.
* An exception aborts the round it occurred in and stops the loop; the best
  ACCEPTED state stands. An exception in the very first round therefore yields
  exactly "no revision, original text, original verdict preserved", and an
  exception in the INITIAL validation yields the original text with
  ``blocked=True`` — we could not measure it, so we do not vouch for it.
* Budget exhausted with findings remaining → ``needs_hitl``. The loop never
  reports success it did not reach.

No citation parsing, claim decomposition or gate composition is re-implemented
here — the TRUST invariant in CLAUDE.md forbids a second copy. Validators are
INJECTED, so this module holds no opinion about which guards run and stays
importable with no DB, no Flask and (until a revision is actually attempted) no
LLM. Precedent for the bounded review → fix → re-review shape:
``tools/quality/review_loop.py``.

Library only — there is no CLI::

    from tools.quality.self_correct import self_correct
    report = self_correct(draft, sources=sources, validators=gate.evaluate,
                          router=LLMRouter())

Routing: the revision call is issued under the ``trust_self_correct`` function,
declared local-first under ``routing:`` in ``args/llm_config.yaml`` AND its
``icdev/args`` mirror. An undeclared function falls through to
``routing.default``, which is cloud-first — that is a live air-gap violation on a
CUI drafting path, not a style point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from tools.logging.icdev_logger import get_logger
from tools.quality.citation_grounding import decompose_claims, parse_citations

logger = get_logger(__name__)

DEFAULT_FUNCTION = "trust_self_correct"
DEFAULT_MAX_ROUNDS = 2

# Reject a candidate shorter than this fraction of the original's non-whitespace
# length. See the module docstring: the monotone objective's trivial optimum is
# the empty string, so length retention is what separates "deleted the
# unsupportable sentence" from "deleted the document".
DEFAULT_MIN_RETENTION = 0.6

# A single-shot revision must return the WHOLE document. Past this size the
# prompt stops being reliable and the failure mode is silent truncation — i.e.
# content loss that the finding count would score as an improvement. Refuse
# instead of truncating.
MAX_DOCUMENT_CHARS = 24_000

# Prompt budget for evidence. Enough to fix a claim against its own source
# without turning the revision call into a context dump.
MAX_EVIDENCE_SOURCES = 6
MAX_EVIDENCE_CHARS = 1_500

# ── status vocabulary ────────────────────────────────────────────────────────
STATUS_CLEAN = "clean"                  # nothing to fix on entry; no LLM call
STATUS_CORRECTED = "corrected"          # findings reached zero via accepted rounds
STATUS_IMPROVED = "improved"            # strictly fewer findings, some remain
STATUS_UNCHANGED = "unchanged"          # no round was accepted
STATUS_UNMEASURABLE = "unmeasurable"    # no validators — nothing was measured
STATUS_UNAVAILABLE = "unavailable"      # no router / document too long to revise
STATUS_ERROR = "error"                  # validator or revision raised

# ── round outcomes ───────────────────────────────────────────────────────────
REASON_ACCEPTED = "strict_decrease"
REASON_NOT_FEWER = "not_strictly_fewer"
REASON_NO_CHANGE = "no_change"
REASON_EMPTY = "empty_revision"
REASON_RETENTION = "below_retention_floor"


@dataclass
class Verdict:
    """One validator pass, normalised. ``blocked`` is always MEASURED."""

    findings: List[Any] = field(default_factory=list)
    blocked: bool = False

    @property
    def count(self) -> int:
        return len(self.findings)


# ── validator normalisation ──────────────────────────────────────────────────

def _normalize_validators(validators: Any) -> List[Callable[[str], Any]]:
    """Accept one callable or a sequence of them."""
    if validators is None:
        return []
    if callable(validators):
        return [validators]
    if isinstance(validators, Sequence) and not isinstance(validators, (str, bytes)):
        return [v for v in validators if callable(v)]
    return []


def normalize_verdict(raw: Any) -> Verdict:
    """Adapt a validator's return value into a :class:`Verdict`.

    Understands the three shapes this codebase already produces:

    * a ``TrustVerdict`` — anything exposing ``.findings`` and ``.blocked``;
    * a mapping ``{"findings": [...], "blocked": bool}`` (``blocked`` omitted is
      derived from whether there are findings);
    * a bare findings list, as ``claim_gate`` / ``kg_gate`` /
      ``placeholder_findings`` return.

    Anything else raises ``TypeError``. That is deliberate: an unrecognised
    return value must reach the fail-closed handler, never be coerced into an
    empty finding list that reads as clean.
    """
    if raw is None:
        raise TypeError("validator returned None; cannot distinguish clean from unmeasured")
    if hasattr(raw, "findings") and hasattr(raw, "blocked"):
        return Verdict(list(getattr(raw, "findings") or []), bool(getattr(raw, "blocked")))
    if isinstance(raw, Mapping):
        findings = list(raw.get("findings") or [])
        blocked = raw.get("blocked")
        return Verdict(findings, bool(findings) if blocked is None else bool(blocked))
    if isinstance(raw, (list, tuple)):
        findings = list(raw)
        return Verdict(findings, bool(findings))
    raise TypeError(f"unsupported validator result type: {type(raw).__name__}")


def run_validators(validators: Sequence[Callable[[str], Any]], text: str) -> Verdict:
    """Run every validator over ``text`` and merge into one verdict.

    Exceptions are NOT caught here — they propagate to the fail-closed handler
    in :func:`self_correct`. A guard that crashed has not passed.
    """
    findings: List[Any] = []
    blocked = False
    for validator in validators:
        verdict = normalize_verdict(validator(text))
        findings.extend(verdict.findings)
        blocked = blocked or verdict.blocked
    return Verdict(findings, blocked)


# ── targeting ────────────────────────────────────────────────────────────────

def _as_claim_index(value: Any) -> Optional[int]:
    """1-based claim index, or None for a document-level finding."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _finding_field(finding: Any, name: str, default: Any = None) -> Any:
    if isinstance(finding, Mapping):
        return finding.get(name, default)
    return getattr(finding, name, default)


def target_findings(text: str, findings: Sequence[Any]) -> List[Dict[str, Any]]:
    """Resolve each finding back to the exact span it came from.

    ``decompose_claims`` offsets index the ORIGINAL text, and every claim-level
    guard numbers its findings 1-based over that same decomposition — so
    ``item_number`` is a direct index into it and the prompt can quote the span
    verbatim instead of describing it. A finding that does not resolve (a
    document-level ``item_number``, or an index past the end because the text
    was revised since) keeps its issue and detail and is reported without a
    span rather than being dropped.
    """
    spans = decompose_claims(text)
    out: List[Dict[str, Any]] = []
    for finding in findings or []:
        entry: Dict[str, Any] = {
            "guard": _finding_field(finding, "guard", "") or "",
            "issue": str(_finding_field(finding, "issue", "finding") or "finding"),
            "item_number": _finding_field(finding, "item_number", "document"),
            "detail": _finding_field(finding, "detail"),
            "claim": "",
            "start": None,
            "end": None,
        }
        index = _as_claim_index(entry["item_number"])
        if index is not None and 1 <= index <= len(spans):
            claim, start, end = spans[index - 1]
            entry.update(claim=claim, start=start, end=end)
        out.append(entry)
    return out


def _detail_text(detail: Any) -> str:
    if detail is None:
        return ""
    if isinstance(detail, (list, tuple, set)):
        return ", ".join(str(d) for d in detail if str(d).strip())[:300]
    return str(detail)[:300]


def _evidence_block(targeted: Sequence[Mapping[str, Any]], sources: Any) -> str:
    """Source texts for the claims being fixed.

    Prefers the sources the FAILING claims actually cite — a revision is
    grounded in the evidence the draft claimed to use, the same narrowing
    ``cove_guard._resolve_cited_sources`` applies. Falls back to everything on
    offer when nothing resolves (e.g. the defect IS a missing citation).
    """
    if not isinstance(sources, Mapping) or not sources:
        return ""
    lookup = {str(k): str(v) for k, v in sources.items()}
    cited: List[str] = []
    for item in targeted:
        for sid in parse_citations(str(item.get("claim") or "")):
            if sid in lookup and sid not in cited:
                cited.append(sid)
    chosen = cited or list(lookup)
    lines: List[str] = []
    for sid in chosen[:MAX_EVIDENCE_SOURCES]:
        body = lookup.get(sid, "").strip()
        if not body:
            continue
        lines.append(f"[source: {sid}]\n{body[:MAX_EVIDENCE_CHARS]}")
    return "\n\n".join(lines)


def build_revision_prompt(
    text: str,
    targeted: Sequence[Mapping[str, Any]],
    sources: Any = None,
) -> str:
    """Compose the targeted revision prompt.

    Names only the failing spans and their character offsets. The instruction to
    WEAKEN or DELETE an unsupportable claim is deliberate — the alternative is a
    model that invents support to satisfy the guard, which is the failure this
    whole subsystem exists to catch. The retention floor in
    :func:`self_correct` is what stops that licence being abused wholesale.
    """
    spanned = [t for t in targeted if t.get("start") is not None]
    document_level = [t for t in targeted if t.get("start") is None]

    parts: List[str] = [
        "You are revising a document that FAILED automated verification. "
        "Fix ONLY the defects listed below.",
        "",
        "RULES",
        "- Change only what a listed defect requires. Leave every other sentence "
        "byte-for-byte intact.",
        "- Every factual claim must be supported by the EVIDENCE below and must "
        "carry its [source: <id>] citation.",
        "- If the evidence does not support a claim, WEAKEN it to what the "
        "evidence does support, or DELETE that claim. Never invent support, and "
        "never delete content that was not named as a defect.",
        "- Do not add new claims, headings, commentary or markdown fences.",
        "- Return the COMPLETE revised document as plain text, nothing else.",
        "",
    ]

    if spanned:
        parts.append(f"DEFECTS ({len(spanned)}) — character offsets index the DOCUMENT below")
        for n, item in enumerate(spanned, start=1):
            detail = _detail_text(item.get("detail"))
            parts.append(
                f"{n}. chars {item['start']}-{item['end']} [{item['issue']}] "
                f"{item.get('claim', '')!r}"
            )
            if detail:
                parts.append(f"   problem: {detail}")
        parts.append("")

    if document_level:
        parts.append(f"DOCUMENT-LEVEL DEFECTS ({len(document_level)})")
        for item in document_level:
            detail = _detail_text(item.get("detail"))
            parts.append(f"- [{item['issue']}]{(' ' + detail) if detail else ''}")
        parts.append("")

    evidence = _evidence_block(targeted, sources)
    if evidence:
        parts.extend(["EVIDENCE", evidence, ""])

    parts.extend(["DOCUMENT", text])
    return "\n".join(parts)


# ── revision ─────────────────────────────────────────────────────────────────

def _revision_max_tokens(text: str) -> int:
    """Room for the whole document back, bounded.

    A revision returns the full document, so a fixed cap silently truncates a
    long one — and a truncated document scores FEWER findings, which the
    monotone check would read as progress.
    """
    return min(8192, max(1024, len(text) // 3 + 512))


def _invoke_revision(router: Any, function: str, prompt: str, text: str) -> str:
    from tools.llm.provider import LLMRequest

    response = router.invoke(function, LLMRequest(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=_revision_max_tokens(text),
        temperature=0.2,
    ))
    return (getattr(response, "content", "") or "").strip()


def _dense_len(text: str) -> int:
    """Non-whitespace length — reformatting must not read as deletion."""
    return sum(1 for ch in text if not ch.isspace())


# ── the loop ─────────────────────────────────────────────────────────────────

def _report(
    *,
    text: str,
    status: str,
    blocked: bool,
    initial: int,
    final: int,
    rounds: List[Dict[str, Any]],
    max_rounds: int,
    function: str,
    revised: bool = False,
    error: str = "",
) -> Dict[str, Any]:
    return {
        "status": status,
        "text": text,
        "revised": revised,
        "blocked": bool(blocked),
        # Budget spent, a round rejected, an error, or anything still open — all
        # of them mean a human has to look. Only a measured, unblocked, empty
        # finding set clears this.
        "needs_hitl": bool(blocked or final > 0 or error),
        "initial_findings": initial,
        "final_findings": final,
        "rounds": rounds,
        "rounds_used": len(rounds),
        "max_rounds": max_rounds,
        "function": function,
        "error": error,
    }


def self_correct(
    text: str,
    *,
    sources: Any = None,
    validators: Any = None,
    router: Any = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    function: str = DEFAULT_FUNCTION,
    min_retention: float = DEFAULT_MIN_RETENTION,
) -> Dict[str, Any]:
    """Revise ``text`` against ``validators`` until the findings stop shrinking.

    Args:
        text: the draft to correct.
        sources: ``{source_id: source_text}`` — the evidence quoted back to the
            model so it can fix a claim rather than reword it. Not used for
            validation; the injected validators own that.
        validators: one callable, or a sequence of them, each ``(text) -> result``
            where result is a ``TrustVerdict``, a ``{"findings", "blocked"}``
            mapping, or a bare findings list. NONE supplied means nothing was
            measured, which is reported as ``unmeasurable`` + ``blocked=True``,
            never as clean.
        router: an ``LLMRouter``. Without one no revision is attempted and the
            measured verdict is returned unchanged.
        max_rounds: hard cap on revise→recheck cycles.
        function: routing function for the revision call. Declared under
            ``routing:`` in ``args/llm_config.yaml``; an undeclared name falls
            through to the cloud-first ``routing.default``.
        min_retention: reject a candidate whose non-whitespace length falls below
            this fraction of the text it revised. Set to ``0`` to disable — but
            read the module docstring first, because the monotone objective's
            trivial optimum is the empty document.

    Returns a dict::

        {status, text, revised, blocked, needs_hitl, initial_findings,
         final_findings, rounds[], rounds_used, max_rounds, function, error}

    ``text`` is the best state reached and ``blocked`` is the verdict MEASURED
    over exactly that text — never an assumption, and never relaxed by anything
    other than a completed validator run.
    """
    original = text or ""
    max_rounds = max(0, int(max_rounds or 0))
    checks = _normalize_validators(validators)

    if not checks:
        logger.warning("self_correct: no validators supplied — nothing measured")
        return _report(
            text=original, status=STATUS_UNMEASURABLE, blocked=True,
            initial=0, final=0, rounds=[], max_rounds=max_rounds, function=function,
            error="no validators supplied; nothing was measured",
        )

    # Initial measurement. If this cannot complete we know nothing about the
    # draft, so it is blocked — the one posture that is never wrong here.
    try:
        baseline = run_validators(checks, original)
    except Exception as exc:  # noqa: BLE001 — fail closed, never crash the caller
        logger.warning("self_correct: initial validation failed: %s", exc)
        return _report(
            text=original, status=STATUS_ERROR, blocked=True,
            initial=0, final=0, rounds=[], max_rounds=max_rounds, function=function,
            error=f"{type(exc).__name__}: {exc}",
        )

    best_text, best = original, baseline
    initial_count = baseline.count

    if initial_count == 0:
        # Nothing to fix. Note this still reports the MEASURED block state: a
        # guard can refuse on `unmeasurable` while producing no findings.
        return _report(
            text=original, status=STATUS_CLEAN, blocked=baseline.blocked,
            initial=0, final=0, rounds=[], max_rounds=max_rounds, function=function,
        )

    if router is None or max_rounds == 0:
        return _report(
            text=original,
            status=STATUS_UNAVAILABLE if router is None else STATUS_UNCHANGED,
            blocked=baseline.blocked, initial=initial_count, final=initial_count,
            rounds=[], max_rounds=max_rounds, function=function,
            error="no LLM router supplied" if router is None else "",
        )

    if len(original) > MAX_DOCUMENT_CHARS:
        # Truncating the prompt would return a truncated document, and a
        # truncated document scores fewer findings. Refuse instead.
        logger.info(
            "self_correct: document of %d chars exceeds the %d-char single-shot limit",
            len(original), MAX_DOCUMENT_CHARS,
        )
        return _report(
            text=original, status=STATUS_UNAVAILABLE, blocked=baseline.blocked,
            initial=initial_count, final=initial_count, rounds=[],
            max_rounds=max_rounds, function=function,
            error=f"document exceeds {MAX_DOCUMENT_CHARS} chars; revise in sections",
        )

    rounds: List[Dict[str, Any]] = []
    accepted_any = False
    error = ""

    for index in range(1, max_rounds + 1):
        record: Dict[str, Any] = {
            "index": index,
            "findings_before": best.count,
            "findings_after": None,
            "blocked_after": None,
            "accepted": False,
            "reason": "",
            "targeted": [],
            "candidate_chars": None,
        }
        try:
            # Re-target every round: `best_text` is what the current findings
            # were measured over, so its offsets are the ones that are valid.
            targeted = target_findings(best_text, best.findings)
            record["targeted"] = targeted
            prompt = build_revision_prompt(best_text, targeted, sources)
            candidate = _invoke_revision(router, function, prompt, best_text)
            record["candidate_chars"] = len(candidate)

            if not candidate:
                record["reason"] = REASON_EMPTY
                rounds.append(record)
                break
            if candidate.strip() == best_text.strip():
                # The model has nothing further to offer; resampling burns
                # budget for a verdict that is already known.
                record["reason"] = REASON_NO_CHANGE
                rounds.append(record)
                break
            floor = _dense_len(best_text) * float(min_retention or 0.0)
            if _dense_len(candidate) < floor:
                # Deleting the findings is not fixing them.
                record["reason"] = REASON_RETENTION
                rounds.append(record)
                logger.warning(
                    "self_correct: round %d discarded — %d/%d non-whitespace chars "
                    "is below the %.0f%% retention floor",
                    index, _dense_len(candidate), _dense_len(best_text),
                    float(min_retention or 0.0) * 100,
                )
                continue

            after = run_validators(checks, candidate)
            record["findings_after"] = after.count
            record["blocked_after"] = after.blocked

            # THE invariant. Not "<=" — a level round has bought nothing and
            # accepting it would let the text churn indefinitely at the same
            # score, which is how a loop ends up optimising for the checker.
            if after.count < best.count:
                best_text, best = candidate, after
                accepted_any = True
                record.update(accepted=True, reason=REASON_ACCEPTED)
                rounds.append(record)
                if best.count == 0:
                    break
            else:
                record["reason"] = REASON_NOT_FEWER
                rounds.append(record)
        except Exception as exc:  # noqa: BLE001 — fail closed
            # Only THIS round is lost. Every state below was measured by a
            # completed validator run, so returning the best accepted one
            # cannot smuggle out unvalidated text — and if nothing was ever
            # accepted, that state is the untouched original.
            logger.warning("self_correct: round %d failed: %s", index, exc)
            record["reason"] = f"error: {type(exc).__name__}"
            rounds.append(record)
            error = f"{type(exc).__name__}: {exc}"
            break

    if best.count == 0:
        status = STATUS_CORRECTED if accepted_any else STATUS_CLEAN
    elif accepted_any:
        status = STATUS_IMPROVED
    else:
        status = STATUS_UNCHANGED

    return _report(
        text=best_text, status=status, blocked=best.blocked,
        initial=initial_count, final=best.count, rounds=rounds,
        max_rounds=max_rounds, function=function,
        revised=accepted_any, error=error,
    )
