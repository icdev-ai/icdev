# [TEMPLATE: CUI // SP-CTI]
"""Advisor: should reasoned codegen be enabled for THIS task?

Reasoned codegen (CoT/CoD + adversarial critique + verify/repair) multiplies
LLM cost. It pays off for complex, risky, or security/compliance-sensitive
generation and is wasteful for trivial edits. This advisor recommends whether
to enable it — and at what intensity (mode + critique) — for a given task.

Design: **hybrid, deterministic-first** (matches ICDEV's air-gap ethos).
  1. Heuristic pass over the spec + context produces a baseline recommendation
     at ZERO LLM cost. This is the air-gap / no-LLM answer and the audit anchor.
  2. When an LLM is available (and not in no-LLM mode), an optional cheap-tier
     call refines the verdict (mode/critique/confidence/rationale). The LLM may
     adjust within bounds; on any failure the heuristic baseline stands.

The recommendation is advisory. Callers (Phase 2/3 codegen) pass
``reasoned: "auto"|"on"|"off"``: ``on``/``off`` force the decision; ``auto``
consults ``recommend()``. The static ``args/llm_config.yaml`` kill-switch
(``reasoned_codegen.enabled: false`` or a per_function ``enabled: false``) always
wins — the advisor can never enable a disabled section.

Apache-2.0 / LLM-agnostic: stdlib only; the optional LLM call routes through
``LLMRouter``. No third-party deps, no provider SDK import.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger
from tools.llm.reasoned_codegen import MODE_OFF, MODE_COT, MODE_COD

logger = get_logger("icdev.llm.reasoned_codegen_advisor")

# Decision thresholds on the 0..1 benefit score.
_LOW = 0.30   # below → off (not worth the cost)
_HIGH = 0.65  # at/above → escalate to debate (cod)

# Keyword signals (lowercased substring match).
_SECURITY_KW = (
    "auth", "password", "secret", "token", "credential", "crypto", "encrypt",
    "decrypt", "tls", "ssl", "sql", "injection", "deserialize", "pickle", "eval(",
    "exec(", "subprocess", "command", "privilege", "rbac", "abac", "session",
)
_COMPLIANCE_KW = (
    "nist", "800-53", "cui", "classification", "fedramp", "cmmc", "stig",
    "audit", "ato", "fips", "il4", "il5", "il6", "compliance", "control",
)
_COMPLEXITY_KW = (
    "schema", "migration", "table", "endpoint", "api", "concurrency", "async",
    "transaction", "state machine", "parser", "protocol", "algorithm",
    "recursion", "distributed", "race condition", "optimi",
)
_TRIVIAL_KW = (
    "typo", "rename", "comment", "docstring", "formatting", "whitespace",
    "lint", "import order", "spelling", "wording",
)


def _heuristic(function: str, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Zero-cost baseline recommendation from spec text + context."""
    text = (spec or "").lower()
    signals: Dict[str, Any] = {}

    # Length / requirement count.
    words = len(text.split())
    req_markers = len(re.findall(r"(?m)^\s*(?:[-*]|\d+\.)\s", spec or ""))
    signals["word_count"] = words
    signals["requirement_markers"] = req_markers

    sec = [k for k in _SECURITY_KW if k in text]
    comp = [k for k in _COMPLIANCE_KW if k in text]
    cplx = [k for k in _COMPLEXITY_KW if k in text]
    triv = [k for k in _TRIVIAL_KW if k in text]
    signals["security_hits"] = sec
    signals["compliance_hits"] = comp
    signals["complexity_hits"] = cplx
    signals["trivial_hits"] = triv

    # Context signals.
    files = context.get("file_count") or context.get("files")
    if isinstance(files, list):
        files = len(files)
    file_count = int(files) if isinstance(files, int) else 0
    signals["file_count"] = file_count
    past_failures = int(context.get("past_failures", 0) or 0)
    signals["past_failures"] = past_failures

    # Score 0..1.
    score = 0.0
    score += min(words / 400.0, 0.25)            # up to 0.25 for length
    score += min(req_markers / 10.0, 0.15)       # up to 0.15 for many requirements
    score += min(len(cplx) * 0.07, 0.21)         # complexity keywords
    score += 0.15 if sec else 0.0                # any security signal
    score += 0.12 if comp else 0.0               # any compliance signal
    score += min(file_count / 10.0, 0.15)        # multi-file change
    score += min(past_failures * 0.1, 0.2)       # history of failures here
    if triv:
        score -= 0.4                             # strong trivial penalty
    score = max(0.0, min(score, 1.0))
    signals["score"] = round(score, 3)

    # Decide mode + critique.
    if score < _LOW:
        mode, critique, recommended = MODE_OFF, False, False
    elif score >= _HIGH:
        mode, critique, recommended = MODE_COD, True, True
    else:
        mode = MODE_COT
        critique = bool(sec or comp)
        recommended = True

    rationale = _format_rationale(score, sec, comp, cplx, triv, file_count, past_failures, mode, critique)
    return {
        "recommended": recommended,
        "mode": mode,
        "critique": critique,
        "confidence": round(0.5 + abs(score - _LOW) * 0.5, 2) if recommended else round(0.5 + (_LOW - score), 2),
        "rationale": rationale,
        "signals": signals,
        "source": "heuristic",
    }


def _format_rationale(score, sec, comp, cplx, triv, file_count, past_failures, mode, critique) -> str:
    parts: List[str] = [f"benefit score {score:.2f}"]
    if triv:
        parts.append(f"trivial markers ({', '.join(triv[:3])}) → low payoff")
    if sec:
        parts.append(f"security-sensitive ({', '.join(sec[:3])})")
    if comp:
        parts.append(f"compliance-relevant ({', '.join(comp[:3])})")
    if cplx:
        parts.append(f"complexity signals ({', '.join(cplx[:3])})")
    if file_count:
        parts.append(f"{file_count} files in scope")
    if past_failures:
        parts.append(f"{past_failures} prior failure(s) here")
    decision = "off" if mode == MODE_OFF else f"{mode}{' + critique' if critique else ''}"
    parts.append(f"recommend: {decision}")
    return "; ".join(parts)


_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended": {"type": "boolean"},
        "mode": {"type": "string", "enum": [MODE_OFF, MODE_COT, MODE_COD]},
        "critique": {"type": "boolean"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["recommended", "mode", "critique", "rationale"],
}


def _llm_refine(function: str, spec: str, baseline: Dict[str, Any], router: Any) -> Optional[Dict[str, Any]]:
    """Optional cheap-tier LLM refinement. Returns a verdict dict or None."""
    try:
        if router is None:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
        if router.is_no_llm_mode():
            return None
        from tools.llm.provider import LLMRequest

        prompt = (
            "You decide whether 'reasoned code generation' (Chain-of-Thought or "
            "multi-agent Chain-of-Debate, plus adversarial critique and a verify/"
            "repair loop) is worth its extra LLM cost for a code task. It helps for "
            "complex, risky, security/compliance-sensitive work; it wastes budget on "
            "trivial edits.\n\n"
            f"Task function: {function}\n"
            f"Heuristic baseline: {json.dumps({k: baseline[k] for k in ('recommended','mode','critique','signals')})}\n\n"
            f"TASK SPEC:\n{spec[:2000]}\n\n"
            "Return JSON: recommended(bool), mode(one of off|cot|cod), critique(bool), "
            "confidence(0..1), rationale(short)."
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=400,
            output_schema=_LLM_SCHEMA,
            skip_injection_scan=True,
        )
        resp = router.invoke(function="reasoned_codegen_advisor", request=req)
        verdict = resp.structured_output
        if not verdict and resp.content:
            verdict = _try_parse_json(resp.content)
        if not isinstance(verdict, dict):
            return None
        mode = verdict.get("mode")
        if mode not in (MODE_OFF, MODE_COT, MODE_COD):
            return None
        return {
            "recommended": bool(verdict.get("recommended", baseline["recommended"])),
            "mode": mode,
            "critique": bool(verdict.get("critique", baseline["critique"])),
            "confidence": float(verdict.get("confidence", baseline["confidence"]) or baseline["confidence"]),
            "rationale": str(verdict.get("rationale", baseline["rationale"])),
            "signals": baseline["signals"],
            "source": "llm",
        }
    except Exception as exc:
        logger.warning("reasoned_codegen_advisor: LLM refine failed (%s) — using heuristic", exc)
        return None


def _try_parse_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def recommend(
    function: str,
    spec: str,
    context: Optional[Dict[str, Any]] = None,
    router: Optional[Any] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Recommend whether to enable reasoned codegen for a task.

    Args:
        function: ICDEV function key (e.g. "code_generation", "child_app").
        spec: The task spec / requirement text to assess.
        context: Optional signals — file_count, past_failures, classification, etc.
        router: Optional LLMRouter (tests inject a mock).
        use_llm: When False, force the heuristic-only baseline (no LLM call).

    Returns:
        dict: recommended(bool), mode(off|cot|cod), critique(bool),
        confidence(0..1), rationale(str), signals(dict), source("heuristic"|"llm").
    """
    context = context or {}
    baseline = _heuristic(function, spec or "", context)
    if not use_llm:
        return baseline
    refined = _llm_refine(function, spec or "", baseline, router)
    return refined or baseline


def main():
    ap = argparse.ArgumentParser(description="Advise whether to enable reasoned codegen for a task.")
    ap.add_argument("--function", default="code_generation", help="ICDEV function key")
    ap.add_argument("--spec", help="Task spec text")
    ap.add_argument("--spec-file", help="Read spec from a file")
    ap.add_argument("--file-count", type=int, default=0, help="Number of files in scope")
    ap.add_argument("--past-failures", type=int, default=0, help="Prior failures on this task")
    ap.add_argument("--no-llm", action="store_true", help="Heuristic only (no LLM refine)")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    spec = args.spec or ""
    if args.spec_file:
        spec = Path(args.spec_file).read_text(encoding="utf-8")

    result = recommend(
        args.function,
        spec,
        context={"file_count": args.file_count, "past_failures": args.past_failures},
        use_llm=not args.no_llm,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"recommended={result['recommended']} mode={result['mode']} "
              f"critique={result['critique']} confidence={result['confidence']}")
        print(result["rationale"])


if __name__ == "__main__":
    main()
