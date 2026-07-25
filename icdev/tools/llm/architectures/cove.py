# CUI // SP-CTI
"""Chain-of-Verification (CoVe) architecture (agx-verify-01).

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). Pattern only; no upstream code vendored.

CoVe is the missing *enforcement* half of ICDEV's TRUST invariant.
``tools/quality/citation_grounding.py`` validates that citations PARSE and
RESOLVE against the available evidence; nothing else checks whether the CLAIM
itself survives independent interrogation. CoVe does:

    1. BASELINE   — produce (or accept) the draft answer.
    2. PLAN       — derive atomic verification questions from the baseline's
                    claims.
    3. VERIFY     — answer each question INDEPENDENTLY: the verifier prompt does
                    NOT contain the baseline, so the model cannot simply agree
                    with itself. This independence is the load-bearing property
                    and is asserted in the tests.
    4. REVISE     — Python composes a pass/revise decision from the per-question
                    ENUM verdicts (deterministic-picker); on any contradiction
                    the baseline is revised against the verification answers.

Deterministic-picker: the per-question verdict is a 3-value enum
({supported, contradicted, unsupported}), never a confidence float; Python
(:func:`compose_verification`) composes the decision. A small vocabulary is what
lets a 7B local model return the same token as a frontier model.

LLM-agnostic: every call routes through ``LLMRouter``; there are zero vendor-SDK
imports and no hardcoded model IDs. The verification (question-answering) step is
routed to a caller-supplied cheap-tier ``verify_function`` because CoVe multiplies
calls per artifact, so it is opt-in and budget-capped.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, List, Optional

from tools.llm.architectures.envelope import (
    ArchitectureBudget,
    ArchitectureResult,
    ArchitectureStep,
)
from tools.llm.architectures.registry import register
from tools.llm.provider import LLMRequest

# ── Verdict vocabulary (deterministic-picker) ───────────────────────────────
# Kept local to this module: CoVe's verdict axis (does the independent answer
# support the claim?) is distinct from the fitness/grounding vocabularies.
VERDICT_VOCAB = {"supported": 1.0, "partial": 0.5, "contradicted": 0.0, "unsupported": 0.0}
VOCABULARY_VERSION = "cove-1.0"
# A contradicted claim is worse than merely unsupported: it means the evidence
# actively disagrees, so it always forces a revision.
_REVISE_VERDICTS = {"contradicted", "unsupported"}


def classify_verdict(token: Any) -> str:
    """Normalize a model verdict token to the vocabulary; unknown -> unsupported.

    Fail-closed: a malformed/out-of-vocabulary token from a small local model is
    treated as ``unsupported`` (needs revision), never silently ``supported``.
    """
    t = str(token or "").strip().lower()
    return t if t in VERDICT_VOCAB else "unsupported"


def compose_verification(verdicts: List[str]) -> dict:
    """Compose the pass/revise decision from per-question enum verdicts.

    Policy (documented, Python-composed): any ``contradicted`` or ``unsupported``
    verdict fails the check and triggers revision — an ATO-bearing artifact
    cannot ship an unverified claim. Returns
    ``{passed, needs_revision, support_score, counts, vocabulary_version}``.
    """
    normalized = [classify_verdict(v) for v in verdicts]
    counts = {k: 0 for k in ("supported", "partial", "contradicted", "unsupported")}
    for v in normalized:
        counts[v] += 1
    needs_revision = any(v in _REVISE_VERDICTS for v in normalized)
    support_score = (
        round(sum(VERDICT_VOCAB[v] for v in normalized) / len(normalized), 4)
        if normalized
        else 1.0
    )
    return {
        "passed": not needs_revision,
        "needs_revision": needs_revision,
        "support_score": support_score,
        "counts": counts,
        "verdicts": normalized,
        "vocabulary_version": VOCABULARY_VERSION,
    }


# ── Helpers ─────────────────────────────────────────────────────────────────
def _coerce_request(task: Any) -> LLMRequest:
    if isinstance(task, LLMRequest):
        return copy.deepcopy(task)
    if isinstance(task, str):
        return LLMRequest(messages=[{"role": "user", "content": task}])
    raise TypeError(f"task must be str or LLMRequest, got {type(task).__name__}")


def _first_user_content(request: LLMRequest) -> str:
    for msg in request.messages or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        return json.loads(match.group(0) if match else raw)
    except Exception:
        return None


def _snippet_texts(sources) -> List[str]:
    if not sources:
        return []
    out: List[str] = []
    for s in sources:
        if isinstance(s, str):
            text = s
        elif isinstance(s, dict):
            text = s.get("content") or s.get("text") or ""
        else:
            text = getattr(s, "content", "") or getattr(s, "text", "") or ""
        if text and str(text).strip():
            out.append(str(text))
    return out


def _content(resp) -> str:
    return (getattr(resp, "content", "") or "").strip()


def _degraded(reason: str, exc: Optional[Exception] = None, output: str = "") -> ArchitectureResult:
    return ArchitectureResult(
        architecture="chain_of_verification",
        output=output,
        method="cove",
        degraded=True,
        stop_reason=reason,
        metadata={"error": f"{type(exc).__name__}: {exc}"} if exc else {},
    )


# ── The architecture ────────────────────────────────────────────────────────
def chain_of_verification(
    task,
    *,
    router=None,
    budget: Optional[ArchitectureBudget] = None,
    function: str = "architecture_run",
    baseline: Optional[str] = None,
    citation_sources=None,
    max_questions: int = 5,
    verify_function: str = "cove_verify",
    revise_function: Optional[str] = None,
    **kwargs,
) -> ArchitectureResult:
    """Run CoVe over a task (or a supplied ``baseline``).

    Args:
        task: str or LLMRequest — the original request/question.
        baseline: an already-drafted answer to verify. When omitted, CoVe
            generates the baseline first via ``function``.
        citation_sources: optional evidence snippets (strings / dicts with
            ``content``/``text``) the independent verifier may consult. The
            baseline text is NEVER placed in the verifier context.
        max_questions: cap on verification questions (budget control).
        verify_function: cheap-tier routing function for the many independent
            question-answering calls.
        revise_function: routing function for the single revision call
            (defaults to ``function``).

    Returns an :class:`ArchitectureResult` whose ``metadata`` carries the full
    verification trace (questions, per-question verdicts, the composed decision).
    """
    from tools.llm.router import LLMRouter

    router = router or LLMRouter()
    revise_function = revise_function or function
    request = _coerce_request(task)
    original_question = _first_user_content(request)
    steps: List[ArchitectureStep] = []

    # 1. BASELINE
    try:
        if baseline is None:
            base_resp = router.invoke(function, request)
            baseline_text = _content(base_resp)
            steps.append(ArchitectureStep(
                name="baseline",
                model_ids=[getattr(base_resp, "model_id", "") or ""],
                detail={"chars": len(baseline_text)},
            ))
        else:
            baseline_text = str(baseline)
            steps.append(ArchitectureStep(name="baseline", detail={"supplied": True}))
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        return _degraded("baseline_unavailable", exc)

    if not baseline_text.strip():
        return _degraded("empty_baseline")

    # 2. PLAN — derive verification questions from the baseline's claims.
    try:
        plan_prompt = (
            "Extract the atomic, independently-checkable factual claims from the "
            "TEXT below and phrase each as a short verification question. Return "
            "STRICT JSON only:\n"
            '{"questions": ["<question>", ...]}\n\n'
            f"Return at most {max_questions} questions.\n\nTEXT:\n{baseline_text}"
        )
        plan_resp = router.invoke(function, LLMRequest(
            messages=[{"role": "user", "content": plan_prompt}],
            max_tokens=400, temperature=0.0,
        ))
        plan_data = _extract_json(_content(plan_resp)) or {}
        questions = [str(q).strip() for q in (plan_data.get("questions") or []) if str(q).strip()]
        questions = questions[:max_questions]
        steps.append(ArchitectureStep(name="plan_questions", detail={"count": len(questions)}))
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        return _degraded("planning_unavailable", exc, output=baseline_text)

    if not questions:
        # Nothing to verify — return the baseline honestly, not a fake pass.
        return ArchitectureResult(
            architecture="chain_of_verification",
            output=baseline_text,
            steps=steps,
            method="cove",
            degraded=False,
            stop_reason="no_verifiable_claims",
            metadata={"questions": [], "decision": compose_verification([])},
        )

    # 3. VERIFY — answer each question INDEPENDENTLY of the baseline.
    evidence = _snippet_texts(citation_sources)
    evidence_block = (
        "\n\nEVIDENCE (the only source you may rely on):\n"
        + "\n".join(f"[{i + 1}] {s}" for i, s in enumerate(evidence))
        if evidence else ""
    )
    qa_trace = []
    verdicts: List[str] = []
    for q in questions:
        # NOTE: the baseline text is deliberately absent from this prompt — the
        # verifier must answer from the question + evidence alone. This is the
        # load-bearing independence property (asserted in the tests).
        verify_prompt = (
            "Answer the QUESTION using only your knowledge and the EVIDENCE (if "
            "any). Then judge whether the evidence supports a confident answer. "
            "Return STRICT JSON only:\n"
            '{"answer": "<short answer>", "verdict": '
            '"supported"|"partial"|"contradicted"|"unsupported"}\n\n'
            f"QUESTION: {q}"
            f"{evidence_block}"
        )
        try:
            v_resp = router.invoke(verify_function, LLMRequest(
                messages=[{"role": "user", "content": verify_prompt}],
                max_tokens=250, temperature=0.0,
            ))
            v_data = _extract_json(_content(v_resp)) or {}
            verdict = classify_verdict(v_data.get("verdict"))
            answer = str(v_data.get("answer", ""))
        except Exception as exc:
            if isinstance(exc, (TypeError, ValueError, AttributeError)):
                raise
            verdict, answer = "unsupported", ""  # fail-closed on verifier failure
        verdicts.append(verdict)
        qa_trace.append({"question": q, "answer": answer, "verdict": verdict})
    steps.append(ArchitectureStep(name="verify", detail={"answered": len(qa_trace)}))

    decision = compose_verification(verdicts)

    # 4. REVISE — only when Python's composed decision says so.
    revised_text = baseline_text
    if decision["needs_revision"]:
        try:
            flagged = "\n".join(
                f"- Q: {t['question']}\n  independent answer: {t['answer']} "
                f"[{t['verdict']}]"
                for t in qa_trace if t["verdict"] in _REVISE_VERDICTS
            )
            revise_prompt = (
                "Revise the DRAFT so every claim is consistent with the "
                "independent verification findings below. Remove or correct any "
                "claim a finding contradicts or cannot support. Keep everything "
                "that was verified. Return only the revised text.\n\n"
                f"ORIGINAL REQUEST: {original_question}\n\n"
                f"DRAFT:\n{baseline_text}\n\n"
                f"VERIFICATION FINDINGS:\n{flagged}"
            )
            r_resp = router.invoke(revise_function, LLMRequest(
                messages=[{"role": "user", "content": revise_prompt}],
                max_tokens=1200, temperature=0.2,
            ))
            candidate = _content(r_resp)
            if candidate:
                revised_text = candidate
            steps.append(ArchitectureStep(name="revise", detail={"revised": bool(candidate)}))
        except Exception as exc:
            if isinstance(exc, (TypeError, ValueError, AttributeError)):
                raise
            # Revision failed: return the baseline but mark degraded + not-passed
            # so a gate never mistakes an un-revised draft for a verified one.
            return ArchitectureResult(
                architecture="chain_of_verification",
                output=baseline_text,
                steps=steps,
                method="cove",
                degraded=True,
                stop_reason="revision_unavailable",
                metadata={"questions": questions, "qa_trace": qa_trace, "decision": decision},
            )

    return ArchitectureResult(
        architecture="chain_of_verification",
        output=revised_text,
        steps=steps,
        method="cove",
        degraded=False,
        stop_reason="revised" if decision["needs_revision"] else "verified",
        metadata={
            "questions": questions,
            "qa_trace": qa_trace,
            "decision": decision,
            "revised": decision["needs_revision"],
            "baseline": baseline_text,
        },
    )


register("chain_of_verification", chain_of_verification, overwrite=True)
