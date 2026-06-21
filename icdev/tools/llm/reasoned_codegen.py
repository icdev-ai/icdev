# [TEMPLATE: CUI // SP-CTI]
"""Reasoned + verified code generation wrapper.

Composes ICDEV's EXISTING multi-LLM reasoning and adversarial-critique
infrastructure into a single, opt-in, cost-bounded control loop:

    Generate (CoT / CoD / plain)  ->  Critique (adversary)  ->  Verify
    ->  Repair  (loop up to max_repair_rounds)

Nothing here re-implements reasoning or critique. It orchestrates:
  - ``LLMRouter.invoke`` / ``invoke_chain_of_thought`` / ``invoke_chain_of_debate``
    for the generation + repair calls (provider-agnostic, cost/token-capped).
  - ``tools.agent.anvil_critique.AtlasCritique`` for adversarial domain review.
  - A *pluggable* verifier callback supplied by the caller — this module never
    imports a pipeline/canvas module, so verification (FORGE gate, translation
    validator, code_lens, acceptance criteria, ...) is injected, not hard-wired.

Drop-in upgrade for a single ``router.invoke(fn, req)`` call: a caller swaps
that line for ``generate_reasoned_code(function=fn, request=req, verifier=...)``
and reads ``.code``. When config resolves ``mode == "off"`` and ``critique`` is
false, the wrapper delegates straight to ``router.invoke`` — byte-identical
passthrough with zero added overhead or cost.

Config lives in ``args/llm_config.yaml`` under ``reasoned_codegen`` (global
defaults + per_function overrides). All generation defaults to OFF; only
``code_translation`` ships ON. See that file for the cost-scaling notes.

Apache-2.0 / LLM-agnostic: stdlib + existing ``tools.*`` only. No third-party
deps, no provider SDK import — every LLM call routes through ``LLMRouter``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger
from tools.llm.provider import LLMRequest, LLMResponse

logger = get_logger("icdev.llm.reasoned_codegen")

# Modes
MODE_OFF = "off"
MODE_COT = "cot"
MODE_COD = "cod"
_VALID_MODES = {MODE_OFF, MODE_COT, MODE_COD}

# Stop reasons
STOP_COMPLETED = "completed"
STOP_MAX_ROUNDS = "max_rounds"
STOP_BUDGET = "budget"
STOP_VERIFY_FAIL = "verify_fail"
STOP_VETO = "veto"
STOP_PASSTHROUGH = "passthrough"

# Global defaults (overridden by args/llm_config.yaml: reasoned_codegen)
_DEFAULTS = {
    "enabled": True,
    "default_mode": MODE_OFF,
    "default_critique": False,
    "max_repair_rounds": 2,
    "cost_cap_usd": 1.00,
    "token_cap": 64000,
}


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------
@dataclass
class VerificationResult:
    """Outcome of an injected verifier. Mirrors code_lens/forge_validator gates."""

    passed: bool
    score: float = 0.0  # 0..1
    findings: List[str] = field(default_factory=list)  # fed into the repair prompt
    gate_result: str = "pass"  # pass | warn | fail
    detail: Dict[str, Any] = field(default_factory=dict)


# A verifier receives (code, context) and returns a VerificationResult.
Verifier = Callable[[str, Dict[str, Any]], VerificationResult]


@dataclass
class ReasonedCodegenResult:
    """Aggregated result of a reasoned-codegen run."""

    code: str
    passed: bool
    mode: str  # off | cot | cod
    rounds_used: int = 0
    critique_consensus: Optional[str] = None  # go | conditional | nogo | None
    verification: Optional[VerificationResult] = None
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    chain_trace_id: Optional[str] = None
    critique_session_id: Optional[str] = None
    stop_reason: str = STOP_COMPLETED
    history: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------
def section_enabled(router: Any) -> bool:
    """Return the section-level kill-switch (``reasoned_codegen.enabled``).

    When false, reasoned codegen is OFF everywhere regardless of per_function
    settings OR explicit caller overrides — the absolute kill-switch. Callers
    that honor a runtime enable option (e.g. ``--reasoned on``) should gate on
    this before forcing a mode.
    """
    try:
        root = (getattr(router, "_config", {}) or {}).get("reasoned_codegen", {}) or {}
        return bool(root.get("enabled", _DEFAULTS["enabled"]))
    except Exception:
        return bool(_DEFAULTS["enabled"])


def resolve_config(function: str, router: Any) -> dict:
    """Resolve effective reasoned_codegen config for ``function``.

    Merges global ``reasoned_codegen`` defaults with the per_function override.
    Returns a dict with keys: enabled, mode, critique, max_repair_rounds,
    cost_cap_usd, token_cap.
    """
    root = {}
    try:
        root = (getattr(router, "_config", {}) or {}).get("reasoned_codegen", {}) or {}
    except Exception:
        root = {}

    # Section-level enabled is an absolute kill-switch (see below).
    section_enabled = root.get("enabled", _DEFAULTS["enabled"])
    cfg = {
        "enabled": section_enabled,
        "mode": root.get("default_mode", _DEFAULTS["default_mode"]),
        "critique": root.get("default_critique", _DEFAULTS["default_critique"]),
        "max_repair_rounds": root.get("max_repair_rounds", _DEFAULTS["max_repair_rounds"]),
        "cost_cap_usd": root.get("cost_cap_usd", _DEFAULTS["cost_cap_usd"]),
        "token_cap": root.get("token_cap", _DEFAULTS["token_cap"]),
    }

    per_fn = root.get("per_function", {}).get(function, {}) or {}
    if "enabled" in per_fn:
        cfg["enabled"] = per_fn["enabled"]
    if "mode" in per_fn:
        cfg["mode"] = per_fn["mode"]
    if "critique" in per_fn:
        cfg["critique"] = per_fn["critique"]
    if "max_repair_rounds" in per_fn:
        cfg["max_repair_rounds"] = per_fn["max_repair_rounds"]
    if "cost_cap_usd" in per_fn:
        cfg["cost_cap_usd"] = per_fn["cost_cap_usd"]
    if "token_cap" in per_fn:
        cfg["token_cap"] = per_fn["token_cap"]

    # Section-level enabled:false is an absolute kill-switch — it overrides any
    # per_function enabled:true. A per_function enabled:false also forces OFF.
    if not section_enabled:
        cfg["enabled"] = False
    if not cfg["enabled"]:
        cfg["mode"] = MODE_OFF
        cfg["critique"] = False

    if cfg["mode"] not in _VALID_MODES:
        logger.warning("reasoned_codegen: invalid mode %r for %s — using off", cfg["mode"], function)
        cfg["mode"] = MODE_OFF
    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _estimate_cost(router: Any, resp: LLMResponse) -> float:
    """Best-effort USD cost from token counts. 0.0 when pricing is unknown
    (e.g. chain responses whose model_id is a comma-joined list)."""
    try:
        pricing = router.get_model_pricing(resp.model_id) or {}
    except Exception:
        return 0.0
    inp = float(pricing.get("input_per_1k", 0.0) or 0.0)
    out = float(pricing.get("output_per_1k", 0.0) or 0.0)
    return (resp.input_tokens / 1000.0) * inp + (resp.output_tokens / 1000.0) * out


def _generate(router: Any, function: str, request: LLMRequest, mode: str) -> LLMResponse:
    """Single generation call routed by mode. Exceptions propagate (matches
    router.invoke semantics so callers' existing fallbacks still fire)."""
    if mode == MODE_COT:
        return router.invoke_chain_of_thought(function, request)
    if mode == MODE_COD:
        return router.invoke_chain_of_debate(function, request)
    return router.invoke(function, request)


def _repair_function(router: Any, function: str) -> str:
    """Use ``<function>_repair`` if a routing chain exists, else the function itself."""
    try:
        routing = (getattr(router, "_config", {}) or {}).get("routing", {}) or {}
    except Exception:
        routing = {}
    candidate = f"{function}_repair"
    return candidate if candidate in routing else function


def _build_repair_request(request: LLMRequest, code: str, findings: List[str]) -> LLMRequest:
    """Clone the original request and append a repair instruction carrying the
    current code plus the verifier/critique findings."""
    new_req = copy.deepcopy(request)
    finding_block = "\n".join(f"- {f}" for f in findings) if findings else "- (no detail provided)"
    repair_msg = {
        "role": "user",
        "content": (
            "The previous output did not pass verification. Fix ALL of the "
            "following findings and return ONLY the corrected code.\n\n"
            f"FINDINGS:\n{finding_block}\n\n"
            f"CURRENT CODE:\n{code}"
        ),
    }
    if new_req.messages is None:
        new_req.messages = []
    new_req.messages = list(new_req.messages) + [repair_msg]
    return new_req


def _critique_findings(critique_result: dict) -> List[str]:
    """Flatten anvil_critique findings into repair-prompt strings."""
    out: List[str] = []
    for f in critique_result.get("findings", []) or []:
        if not isinstance(f, dict):
            continue
        sev = f.get("severity", "")
        title = f.get("title", "")
        fix = f.get("suggested_fix", "")
        out.append(f"[{sev}] {title}" + (f" — fix: {fix}" if fix else ""))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_reasoned_code(
    *,
    function: str,
    request: LLMRequest,
    verifier: Optional[Verifier] = None,
    verifier_context: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None,
    mode: Optional[str] = None,
    critique: Optional[bool] = None,
    max_repair_rounds: Optional[int] = None,
    router: Optional[Any] = None,
) -> ReasonedCodegenResult:
    """Generate code with optional reasoning, adversarial critique, verification,
    and a bounded repair loop.

    Args:
        function: ICDEV function key (e.g. "code_translation", "code_generation").
        request: Vendor-agnostic LLM request the caller already builds.
        verifier: Optional (code, ctx) -> VerificationResult callback. If omitted,
            verification is treated as PASS (generation-only / critique-only run).
        verifier_context: Passed through to ``verifier`` unchanged.
        project_id: Correlation id for critique/audit.
        mode/critique/max_repair_rounds: Explicit overrides that take precedence
            over config. Leave None to use args/llm_config.yaml.
        router: Optional pre-built LLMRouter (tests inject a mock). Created lazily
            if omitted.

    Returns:
        ReasonedCodegenResult. Generation-call exceptions propagate to the caller.
    """
    if router is None:
        from tools.llm.router import LLMRouter

        router = LLMRouter()

    cfg = resolve_config(function, router)
    eff_mode = mode if mode is not None else cfg["mode"]
    if eff_mode not in _VALID_MODES:
        eff_mode = MODE_OFF
    eff_critique = critique if critique is not None else cfg["critique"]
    eff_rounds = max_repair_rounds if max_repair_rounds is not None else cfg["max_repair_rounds"]
    cost_cap = cfg["cost_cap_usd"]
    token_cap = cfg["token_cap"]

    ctx = verifier_context or {}
    history: List[Dict[str, Any]] = []

    # ---- Passthrough: zero behavior change when reasoning + critique are off ----
    if eff_mode == MODE_OFF and not eff_critique:
        resp = router.invoke(function, request)
        return ReasonedCodegenResult(
            code=resp.content or "",
            passed=True,
            mode=MODE_OFF,
            rounds_used=0,
            total_cost_usd=_estimate_cost(router, resp),
            total_tokens=(resp.input_tokens + resp.output_tokens),
            stop_reason=STOP_PASSTHROUGH,
            history=[{"step": "generate", "mode": MODE_OFF, "model_id": resp.model_id}],
        )

    # ---- Generation ----
    gen = _generate(router, function, request, eff_mode)
    code = gen.content or ""
    total_tokens = gen.input_tokens + gen.output_tokens
    total_cost = _estimate_cost(router, gen)
    chain_trace_id = getattr(gen, "chain_trace_id", None)
    history.append({"step": "generate", "mode": eff_mode, "model_id": gen.model_id, "tokens": total_tokens})

    repair_fn = _repair_function(router, function)
    last_verification: Optional[VerificationResult] = None
    consensus: Optional[str] = None
    critique_session_id: Optional[str] = None
    stop_reason = STOP_COMPLETED
    rounds_used = 0

    # rounds = number of (critique+verify[+repair]) passes; round 1 evaluates the
    # initial generation. A repair call only fires when a further round remains.
    for round_num in range(1, max(1, eff_rounds) + 1):
        rounds_used = round_num

        # 1. Adversarial critique (optional)
        critique_finding_strs: List[str] = []
        if eff_critique:
            try:
                from tools.agent.anvil_critique import AtlasCritique

                crit = AtlasCritique().run_critique(
                    project_id or "reasoned-codegen", code
                )
                consensus = crit.get("consensus")
                critique_session_id = crit.get("session_id")
                critique_finding_strs = _critique_findings(crit)
                history.append({"step": "critique", "round": round_num, "consensus": consensus,
                                "session_id": critique_session_id})
                if consensus == "nogo":
                    stop_reason = STOP_VETO
                    break
            except Exception as exc:  # critique must never harden a soft path into a failure
                logger.warning("reasoned_codegen: critique unavailable (%s) — continuing", exc)
                consensus = None

        # 2. Verification (injected; PASS when no verifier supplied)
        if verifier is not None:
            try:
                last_verification = verifier(code, ctx)
            except Exception as exc:
                logger.warning("reasoned_codegen: verifier raised (%s) — treating as fail", exc)
                last_verification = VerificationResult(
                    passed=False, gate_result="fail", findings=[f"verifier error: {exc}"]
                )
        else:
            last_verification = VerificationResult(passed=True, score=1.0, gate_result="pass")
        history.append({"step": "verify", "round": round_num,
                        "passed": last_verification.passed, "gate": last_verification.gate_result})

        # 3. Acceptance: verify passed AND (no critique, or critique not blocking)
        critique_ok = (not eff_critique) or (consensus in ("go", "conditional", None))
        if last_verification.passed and critique_ok:
            stop_reason = STOP_COMPLETED
            break

        # 4. No rounds left to repair
        if round_num >= max(1, eff_rounds):
            stop_reason = STOP_MAX_ROUNDS if last_verification.passed else STOP_VERIFY_FAIL
            break

        # 5. Budget guard before spending another LLM call
        if total_tokens >= token_cap or (cost_cap and total_cost >= cost_cap):
            stop_reason = STOP_BUDGET
            break

        # 6. Repair: feed verify + critique findings back into a fresh call
        findings = list(last_verification.findings) + critique_finding_strs
        repair_req = _build_repair_request(request, code, findings)
        try:
            rep = router.invoke(repair_fn, repair_req)
        except Exception as exc:
            logger.warning("reasoned_codegen: repair call failed (%s) — stopping", exc)
            stop_reason = STOP_VERIFY_FAIL
            break
        if rep.content:
            code = rep.content
        total_tokens += rep.input_tokens + rep.output_tokens
        total_cost += _estimate_cost(router, rep)
        history.append({"step": "repair", "round": round_num, "model_id": rep.model_id})

    passed = bool(last_verification.passed) if last_verification else True
    if eff_critique and consensus == "nogo":
        passed = False

    return ReasonedCodegenResult(
        code=code,
        passed=passed,
        mode=eff_mode,
        rounds_used=rounds_used,
        critique_consensus=consensus,
        verification=last_verification,
        total_cost_usd=round(total_cost, 6),
        total_tokens=total_tokens,
        chain_trace_id=chain_trace_id,
        critique_session_id=critique_session_id,
        stop_reason=stop_reason,
        history=history,
    )
