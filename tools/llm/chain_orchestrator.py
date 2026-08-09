# [TEMPLATE: CUI // SP-CTI]
"""Chain of Thought / Chain of Debate multi-LLM orchestration engine.

Core design:
  - CoT: reason → critic → synthesize (up to max_rounds)
  - CoD: parallel debate turns (num_debaters × debate_rounds) → judge synthesis
  - Self-consistency: run CoT N times in parallel, majority vote
  - Cost cap ($0.50 default), token cap (32K), timeout (120s)
  - Each step flows through full router pipeline (redaction, RAG, cache, gateway, telemetry)
  - ThreadPoolExecutor for parallel steps

Air-gap safe: uses local models when cloud unavailable. Respects per-function
config from args/llm_config.yaml.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Resolve imports relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection
from tools.llm.chain_prompts import ChainPrompts
from tools.llm.provider import LLMRequest, LLMResponse
from tools.llm.router import LLMRouter

logger = get_logger("icdev.llm.chain_orchestrator")

DEFAULT_COST_CAP_USD = 0.50
DEFAULT_TOKEN_CAP = 32000
DEFAULT_TIMEOUT_SECONDS = 120

# Fixed cognitive lenses for Council mode -- NOT generated per-function like
# CoD's bull/bear/neutral positions. These are deliberately reusable across
# any question: the differentiation mechanism is the lens, not the model or
# a generated stance. (name, thinking-style description) pairs, in the order
# advisor slots are assigned.
#
# As of dvg-frames-02 these live in args/ideation_frames.yaml (frame set
# `council_default`, mode `evaluative`) and invoke_council reads them through
# _load_council_advisors(). This constant is retained VERBATIM as the code-level
# fallback: invoke_council is reachable from the cross-repo council_query MCP
# tool (idea_lab) and must not start failing if the config file is missing.
# Keep this list and the YAML set byte-identical (guarded by a regression test).
_COUNCIL_ADVISORS: list[tuple[str, str]] = [
    (
        "The Contrarian",
        "Actively looks for what's wrong, what's missing, what will fail. Assumes "
        "the idea has a fatal flaw and tries to find it. Not a pessimist -- the "
        "friend who saves you from a bad deal by asking the questions you're avoiding.",
    ),
    (
        "The First Principles Thinker",
        "Ignores the surface-level question and asks what you're actually trying "
        "to solve. Strips away assumptions and rebuilds the problem from the "
        "ground up. Sometimes the most valuable output is saying you're asking "
        "the wrong question entirely.",
    ),
    (
        "The Expansionist",
        "Looks for upside everyone else is missing. What could be bigger? What "
        "adjacent opportunity is hiding? Doesn't care about risk -- cares what "
        "happens if this works even better than expected.",
    ),
    (
        "The Outsider",
        "Has zero context about the domain or history involved. Responds purely "
        "to what's in front of them. Catches the curse of knowledge: things "
        "obvious to an expert but confusing to everyone else.",
    ),
    (
        "The Executor",
        "Only cares whether this can actually be done and the fastest path to "
        "doing it. Ignores theory and big-picture strategy. If an idea sounds "
        "brilliant but has no clear first step, says so.",
    ),
]


# Default GENERATIVE frame set for Divergence mode -- the generative counterpart
# to _COUNCIL_ADVISORS' critical lenses. Where advisors CRITIQUE a decision, these
# frames GENERATE candidate ideas: each pushes a branch to widen the option space
# from a distinct angle, with no evaluation. Divergence deliberately runs one round
# in strict isolation (branches never see each other), so the frame -- not a shared
# history -- is the entire differentiation mechanism. (name, generative-instruction)
# pairs. dvg-frames-01 externalizes this to args/ideation_frames.yaml (a versioned
# library); this inline default is the fallback when that file is absent.
_DIVERGENCE_FRAMES: list[tuple[str, str]] = [
    (
        "First-Principles Rebuild",
        "Ignore how this is usually done. Strip the problem to its irreducible "
        "requirements and rebuild candidate approaches from the ground up.",
    ),
    (
        "Analogical Transfer",
        "Borrow the mechanism from an unrelated domain (biology, logistics, games, "
        "another industry) and adapt it into a candidate approach here.",
    ),
    (
        "Constraint Removal",
        "Pick a constraint everyone treats as fixed (budget, latency, headcount, a "
        "dependency) and imagine it gone. Generate ideas that only make sense then.",
    ),
    (
        "Radical Simplification",
        "Find the smallest, cheapest, most boring thing that could plausibly work. "
        "Strip scope aggressively and generate minimal candidate approaches.",
    ),
    (
        "Inversion",
        "Solve the opposite problem, or ask what would guarantee failure, then invert "
        "those into candidate approaches.",
    ),
    (
        "Combination",
        "Merge two or more unrelated existing approaches into hybrid candidates that "
        "neither would produce alone.",
    ),
]


@dataclass
class ChainStepResult:
    """Result of a single step in a chain."""

    step_name: str
    model_id: str
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    stop_reason: str = ""


@dataclass
class ChainResult:
    """Aggregated result of a full CoT/CoD chain."""

    content: str
    chain_mode: str
    models_used: List[str]
    rounds: List[Dict[str, Any]]
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    total_duration_ms: int
    stop_reason: str
    trace_id: str
    confidence: float = 0.0


class BudgetExceededError(RuntimeError):
    """Raised when cost or token budget is exceeded mid-chain."""


class ChainOrchestrator:
    """Multi-LLM orchestration engine for CoT and CoD."""

    def __init__(self, router: Optional[LLMRouter] = None, config_path: Optional[Path] = None):
        self.router = router or LLMRouter()
        self._config = self._load_chain_config(config_path)
        self._session_id = str(uuid.uuid4())

    def _load_chain_config(self, config_path: Optional[Path] = None) -> dict:
        """Load chain_orchestration config from llm_config.yaml."""
        cfg = getattr(self.router, "_config", {})
        return cfg.get("chain_orchestration", {})

    def _get_function_config(self, function: str, mode: str) -> dict:
        """Resolve per-function chain config with defaults."""
        mode_cfg = self._config.get(mode, {})
        per_fn = mode_cfg.get("per_function", {}).get(function, {})

        # Budget/timeout backstops resolve outer -> mode -> per_function (widest to
        # narrowest). The mode rung matters for fan-out modes: a divergence run is
        # ~N parallel branches, so reusing the single-call outer cap as its
        # pre-flight estimate would under-reserve the module budget by ~Nx and let
        # a run start that cannot afford to finish. Modes that omit these keys
        # (cot/cod/council today) are unaffected and keep the outer values.
        defaults = {
            "cost_cap_usd": mode_cfg.get("cost_cap_usd", self._config.get("cost_cap_usd", DEFAULT_COST_CAP_USD)),
            "token_cap": mode_cfg.get("token_cap", self._config.get("token_cap", DEFAULT_TOKEN_CAP)),
            "timeout_seconds": mode_cfg.get(
                "timeout_seconds", self._config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            ),
        }

        if mode == "cot":
            defaults.update({
                "enabled": mode_cfg.get("enabled", True),
                "max_rounds": mode_cfg.get("max_rounds", 3),
                "self_consistency_runs": mode_cfg.get("self_consistency_runs", 1),
                # Role routing keys — each maps to a distinct routing chain for multi-LLM CoT.
                # Takes precedence over *_model keys. A per_function override of null disables
                # the role key so the legacy *_model fallback is used instead.
                "reasoner_role": mode_cfg.get("reasoner_role", "cot_reasoner"),
                "critic_role": mode_cfg.get("critic_role", "cot_critic"),
                "synthesizer_role": mode_cfg.get("synthesizer_role", "cot_synthesizer"),
                # Legacy direct model names — fallback when role key is null or chain is empty
                "reasoner_model": mode_cfg.get("reasoner_model", "qwen3-local"),
                "critic_model": mode_cfg.get("critic_model", "claude-sonnet"),
                "synthesizer_model": mode_cfg.get("synthesizer_model", "claude-sonnet"),
                "excluded_functions": mode_cfg.get("excluded_functions", []),
            })
        elif mode == "cod":
            defaults.update({
                "enabled": mode_cfg.get("enabled", True),
                "num_debaters": mode_cfg.get("num_debaters", 3),
                "debate_rounds": mode_cfg.get("debate_rounds", 2),
                # Role routing keys for multi-LLM CoD
                "judge_role": mode_cfg.get("judge_role", "cod_judge"),
                "debater_pool_role": mode_cfg.get("debater_pool_role", "cod_debater_pool"),
                # Legacy direct model names — fallback when pool returns nothing
                "judge_model": mode_cfg.get("judge_model", "claude-sonnet"),
                "debater_models": mode_cfg.get("debater_models", ["qwen3-local", "claude-sonnet", "openai-gpt4o"]),
                "excluded_functions": mode_cfg.get("excluded_functions", []),
            })
        elif mode == "council":
            defaults.update({
                "enabled": mode_cfg.get("enabled", True),
                "num_advisors": mode_cfg.get("num_advisors", 5),
                # Role routing keys for multi-LLM council
                "chairman_role": mode_cfg.get("chairman_role", "council_chairman"),
                "advisor_pool_role": mode_cfg.get("advisor_pool_role", "council_advisor_pool"),
                # Legacy direct model names — fallback when pool/role resolves to nothing
                "chairman_model": mode_cfg.get("chairman_model", "claude-sonnet"),
                "advisor_models": mode_cfg.get("advisor_models", ["qwen3-local", "claude-sonnet", "openai-gpt4o"]),
                "excluded_functions": mode_cfg.get("excluded_functions", []),
            })
        elif mode == "divergence":
            # Divergence is OPT-IN: enabled defaults to FALSE so it can never
            # become a default generation path (upstream: ~10 agent calls, 5-10x
            # the spend of a direct answer). Callers must explicitly enable it
            # per-function in config.
            defaults.update({
                "enabled": mode_cfg.get("enabled", False),
                "num_branches": mode_cfg.get("num_branches", 6),
                # Named generative frame set (dvg-frames-01 externalizes to YAML);
                # "generative" resolves to the inline _DIVERGENCE_FRAMES fallback.
                "frame_set": mode_cfg.get("frame_set", "generative"),
                # Provider-diverse pool for the branches (get_diverse_models).
                "branch_pool_role": mode_cfg.get("branch_pool_role", "divergence_branch_pool"),
                # Critic role reserved for dvg-critic-* (separate invocation).
                "critic_role": mode_cfg.get("critic_role", "divergence_critic"),
                # Legacy direct model names — fallback when pool/role resolves to nothing.
                "branch_models": mode_cfg.get("branch_models", ["qwen3-local", "claude-sonnet", "gpt-4o"]),
                "excluded_functions": mode_cfg.get("excluded_functions", []),
            })

        # Per-function overrides take highest priority (null values explicitly preserved
        # so per_function can zero out a role key to force legacy *_model path)
        result = copy.deepcopy(defaults)
        result.update(per_fn)  # include null/None overrides — _invoke_model handles them
        return result

    def _is_excluded(self, function: str, mode: str) -> bool:
        """Check if function is in the exclusion list for this mode."""
        mode_cfg = self._config.get(mode, {})
        excluded = mode_cfg.get("excluded_functions", [])
        return function in excluded

    def _load_divergence_frames(self, frame_set: str = "generative") -> List[Tuple[str, str]]:
        """Resolve a named GENERATIVE frame set to a list of (name, prompt_fragment).

        Reads the versioned library at args/ideation_frames.yaml through the single
        config loader (tools.config.ideation_frames, dvg-frames-01/02) -- the one
        source of truth for perspective sets. Falls back to the inline
        _DIVERGENCE_FRAMES default when the file is absent, empty, or the requested
        set has no generative frames. Never raises -- an unreadable frame file
        degrades to the built-in default so a divergence run still proceeds.
        """
        try:
            from tools.config.ideation_frames import get_frame_pairs

            pairs = get_frame_pairs(frame_set, mode="generative")
            if pairs:
                return pairs
        except Exception as exc:  # noqa: BLE001 — frame file is advisory, never load-bearing
            logger.debug("ideation frame load failed for set '%s': %s — using inline default", frame_set, exc)
        return list(_DIVERGENCE_FRAMES)

    def _load_council_advisors(self) -> List[Tuple[str, str]]:
        """Resolve the council's fixed cognitive lenses to (name, style) pairs.

        Reads the EVALUATIVE `council_default` set from the shared frame library
        (dvg-frames-02) so there is one source of truth for perspective sets.
        Behavior is identical to the historical hardcoded list -- same advisors,
        same order, same text. Falls back to the module-level _COUNCIL_ADVISORS
        constant if the YAML is missing, fails to load, or is short: invoke_council
        is reachable from the cross-repo council_query MCP tool (idea_lab) and must
        never start failing because a config file moved.
        """
        try:
            from tools.config.ideation_frames import get_frame_pairs

            pairs = get_frame_pairs("council_default", mode="evaluative")
            if pairs:
                return pairs
        except Exception as exc:  # noqa: BLE001 — never take council offline over a config read
            logger.debug("council frame load failed: %s — using hardcoded fallback", exc)
        return list(_COUNCIL_ADVISORS)

    def _compute_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """Compute USD cost for a model invocation."""
        pricing = self.router.get_model_pricing(model_id)
        if not pricing:
            return 0.0
        inp_rate = pricing.get("input_per_1k", 0.0)
        out_rate = pricing.get("output_per_1k", 0.0)
        return (input_tokens / 1000.0) * inp_rate + (output_tokens / 1000.0) * out_rate

    def _invoke_model(
        self,
        model_name: str,
        request: LLMRequest,
        function: str,
        timeout: float,
    ) -> Tuple[LLMResponse, float]:
        """Invoke a model or routing role via the router, with timeout.

        model_name is interpreted as:
        1. A routing chain key (e.g. 'cot_reasoner') — if present in routing: config,
           uses router.invoke_for_role() with full availability + RL + fallback chain.
        2. A direct logical model name (e.g. 'qwen3-local') — legacy path, calls
           _invoke_model_direct() for backward compat with per_function direct overrides.

        Returns (response, elapsed_seconds).
        Raises RuntimeError or LLMUnavailableError if invocation fails.
        """
        start = time.time()

        routing = getattr(self.router, "_config", {}).get("routing", {})
        if model_name in routing:
            # Full chain-based routing: availability check + RL ranking + fallback
            from tools.llm.router import LLMUnavailableError
            try:
                response = self.router.invoke_for_role(model_name, function, request)
            except LLMUnavailableError as exc:
                raise RuntimeError(
                    f"Role '{model_name}' unavailable for '{function}': {exc}"
                ) from exc
        else:
            # Legacy: direct model name — single attempt, no fallback chain
            response = self.router._invoke_model_direct(model_name, request, function=function)
            if response is None:
                raise RuntimeError(
                    f"Model '{model_name}' returned None for '{function}'. "
                    "Consider using a routing chain key instead of a direct model name."
                )

        elapsed = time.time() - start
        if elapsed > timeout:
            raise TimeoutError(f"Model/role '{model_name}' exceeded {timeout:.1f}s timeout")
        return response, elapsed

    def _build_request(
        self,
        original: LLMRequest,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMRequest:
        """Build a new LLMRequest from original with updated prompts."""
        req = copy.deepcopy(original)
        req.system_prompt = system_prompt
        # Replace user message content
        if req.messages and isinstance(req.messages[0], dict):
            req.messages[0]["content"] = user_prompt
        else:
            req.messages = [{"role": "user", "content": user_prompt}]
        return req

    def _check_budget(
        self,
        spent_cost: float,
        spent_tokens: int,
        cfg: dict,
    ) -> None:
        """Abort chain if cost or token cap exceeded."""
        if spent_cost >= cfg["cost_cap_usd"]:
            raise BudgetExceededError(
                f"Cost cap exceeded: ${spent_cost:.4f} >= ${cfg['cost_cap_usd']:.2f}"
            )
        if spent_tokens >= cfg["token_cap"]:
            raise BudgetExceededError(
                f"Token cap exceeded: {spent_tokens} >= {cfg['token_cap']}"
            )

    def _record_canvas_decision(
        self,
        decision_type: str,
        decision: str,
        rationale: str,
        model_used: str,
        confidence: float,
        alternatives: Optional[List[Any]] = None,
    ) -> None:
        """Best-effort record to canvas_ai_decisions and audit_trail."""
        try:
            from tools.canvas.ai_trace_mixin import record_canvas_decision

            record_canvas_decision(
                canvas_type="llm",
                decision_type=decision_type,
                decision=decision[:4000],
                rationale=rationale[:4000],
                model_used=model_used,
                confidence=confidence,
                alternatives=alternatives or [],
                actor="chain_orchestrator",
            )
        except Exception as exc:
            logger.debug("Canvas decision recording failed (non-blocking): %s", exc)

    def _write_chain_telemetry(self, result: ChainResult, function: str) -> None:
        """Write per-round detail rows to llm_chain_telemetry table."""
        try:
            conn = get_connection()
            for rnd in result.rounds:
                conn.execute(
                    """
                    INSERT INTO llm_chain_telemetry
                        (session_id, function, chain_mode, models_used, rounds,
                         input_tokens, output_tokens, cost_usd, duration_ms,
                         final_model_id, stop_reason, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        result.trace_id,
                        function,
                        result.chain_mode,
                        json.dumps(result.models_used),
                        json.dumps(rnd),
                        rnd.get("input_tokens", 0),
                        rnd.get("output_tokens", 0),
                        rnd.get("cost_usd", 0.0),
                        rnd.get("duration_ms", 0),
                        rnd.get("model_id", ""),
                        result.stop_reason,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.debug("Chain telemetry write failed (non-blocking): %s", exc)

        # Record module-level budget usage for generative_intelligence
        try:
            from tools.budget.module_budget_tracker import record_module_usage

            record_module_usage(
                "generative_intelligence",
                cost_usd=result.total_cost_usd,
                tokens=result.total_input_tokens + result.total_output_tokens,
                function=function,
            )
        except Exception as exc:
            # Best-effort, but not silent — see the note in router.py: a bare
            # `pass` here is how a permanently-failing insert went unnoticed.
            logger.warning("Module budget recording failed: %s", exc)

        self._publish_reasoning_event(result, function)

    def _publish_reasoning_event(self, result: ChainResult, function: str) -> None:
        """Publish cot_reasoning_completed event to the canvas event bus."""
        try:
            from tools.canvas.event_bus import publish as _eb_publish
            _eb_publish(
                "llm",
                "cot_reasoning_completed",
                {
                    "trace_id": result.trace_id,
                    "function": function,
                    "chain_mode": result.chain_mode,
                    "models_used": result.models_used,
                    "total_cost_usd": result.total_cost_usd,
                    "total_duration_ms": result.total_duration_ms,
                    "stop_reason": result.stop_reason,
                    "rounds": len(result.rounds),
                    "confidence": result.confidence,
                },
            )
        except Exception as exc:
            logger.debug("Event bus publish failed (non-blocking): %s", exc)

    def _check_module_budget(self, function: str, estimated_cost: float = 0.0, estimated_tokens: int = 0) -> None:
        """Check generative_intelligence module budget before expensive chain work."""
        try:
            from tools.budget.module_budget_tracker import check_module_budget, ModuleBudgetExceededError

            status = check_module_budget(
                "generative_intelligence",
                function=function,
                estimated_cost_usd=estimated_cost,
                estimated_tokens=estimated_tokens,
            )
            if status["action"] == "block":
                raise ModuleBudgetExceededError("generative_intelligence", status)
            if status["action"] == "warn":
                logger.warning("Module budget warning for %s: %s", function, status["message"])
        except ImportError:
            pass
        except ModuleBudgetExceededError:
            raise
        except Exception as exc:
            logger.debug("Module budget check failed (non-blocking): %s", exc)

    def invoke_chain_of_thought(
        self,
        function: str,
        request: LLMRequest,
    ) -> ChainResult:
        """Run Chain of Thought: reason → critic → synthesize.

        Args:
            function: ICDEV™ function name.
            request: Original LLM request.

        Returns:
            ChainResult with final synthesized response.
        """
        cfg = self._get_function_config(function, "cot")
        trace_id = self._session_id
        start_time = time.time()

        if not cfg["enabled"]:
            raise RuntimeError("Chain of Thought is disabled in config")

        if self._is_excluded(function, "cot"):
            raise RuntimeError(f"Function '{function}' is excluded from CoT")

        self._check_module_budget(function, estimated_cost=cfg.get("cost_cap_usd", 0.0))

        # Self-consistency: run CoT N times in parallel
        runs = cfg.get("self_consistency_runs", 1)
        if runs > 1:
            return self._cot_self_consistency(function, request, cfg, trace_id, start_time)

        return self._cot_single(function, request, cfg, trace_id, start_time)

    def _cot_single(
        self,
        function: str,
        request: LLMRequest,
        cfg: dict,
        trace_id: str,
        start_time: float,
    ) -> ChainResult:
        """Run a single CoT chain (reason → critic → synthesize)."""
        rounds: List[Dict[str, Any]] = []
        models_used: set = set()
        total_cost = 0.0
        total_tokens = 0
        max_rounds = cfg["max_rounds"]
        timeout = cfg["timeout_seconds"]
        deadline = time.time() + timeout

        user_prompt = ""
        if request.messages and isinstance(request.messages[0], dict):
            user_prompt = str(request.messages[0].get("content", ""))

        reasoning = ""
        final_content = ""
        stop_reason = "completed"

        for round_num in range(1, max_rounds + 1):
            if time.time() > deadline:
                stop_reason = "timeout"
                break

            self._check_budget(total_cost, total_tokens, cfg)

            # ---- REASONER ----
            sys_prompt, usr_prompt = ChainPrompts.reasoner(
                user_prompt,
                system_prompt=request.system_prompt or "",
                output_schema=request.output_schema,
                tools=request.tools,
            )
            reasoner_req = self._build_request(request, sys_prompt, usr_prompt)

            reasoner_role = cfg.get("reasoner_role") or cfg["reasoner_model"]
            try:
                resp, elapsed = self._invoke_model(
                    reasoner_role, reasoner_req, function, deadline - time.time()
                )
            except Exception as exc:
                logger.warning("CoT reasoner failed: %s", exc)
                stop_reason = f"reasoner_error: {exc}"
                break

            reasoning = resp.content or ""
            cost = self._compute_cost(resp.model_id or reasoner_role, resp.input_tokens, resp.output_tokens)
            total_cost += cost
            total_tokens += resp.input_tokens + resp.output_tokens
            models_used.add(resp.model_id or reasoner_role)

            rounds.append({
                "round": round_num,
                "step": "reason",
                "model_id": resp.model_id or reasoner_role,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "cost_usd": round(cost, 6),
                "duration_ms": int(elapsed * 1000),
            })

            self._record_canvas_decision(
                decision_type="chain_of_thought",
                decision=f"Round {round_num} reasoning",
                rationale=reasoning[:2000],
                model_used=resp.model_id or reasoner_role,
                confidence=0.0,
            )

            # ---- CRITIC (skip on final round) ----
            if round_num < max_rounds:
                if time.time() > deadline:
                    stop_reason = "timeout"
                    break
                self._check_budget(total_cost, total_tokens, cfg)

                sys_prompt, usr_prompt = ChainPrompts.critic(
                    user_prompt,
                    reasoning,
                    system_prompt=request.system_prompt or "",
                    output_schema=request.output_schema,
                )
                critic_req = self._build_request(request, sys_prompt, usr_prompt)

                critic_role = cfg.get("critic_role") or cfg["critic_model"]
                try:
                    resp, elapsed = self._invoke_model(
                        critic_role, critic_req, function, deadline - time.time()
                    )
                except Exception as exc:
                    logger.warning("CoT critic failed: %s", exc)
                    stop_reason = f"critic_error: {exc}"
                    break

                critique = resp.content or ""
                cost = self._compute_cost(resp.model_id or critic_role, resp.input_tokens, resp.output_tokens)
                total_cost += cost
                total_tokens += resp.input_tokens + resp.output_tokens
                models_used.add(resp.model_id or critic_role)

                rounds.append({
                    "round": round_num,
                    "step": "critic",
                    "model_id": resp.model_id or critic_role,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cost_usd": round(cost, 6),
                    "duration_ms": int(elapsed * 1000),
                })

                # Update reasoning for next round with critique incorporated
                reasoning = f"{reasoning}\n\n[CRITIQUE APPLIED]\n{critique}"
            else:
                # Final round: synthesize
                if time.time() > deadline:
                    stop_reason = "timeout"
                    break
                self._check_budget(total_cost, total_tokens, cfg)

                sys_prompt, usr_prompt = ChainPrompts.synthesizer(
                    user_prompt,
                    reasoning,
                    "",  # No separate critique on final round
                    system_prompt=request.system_prompt or "",
                    output_schema=request.output_schema,
                )
                synth_req = self._build_request(request, sys_prompt, usr_prompt)

                synthesizer_role = cfg.get("synthesizer_role") or cfg["synthesizer_model"]
                try:
                    resp, elapsed = self._invoke_model(
                        synthesizer_role, synth_req, function, deadline - time.time()
                    )
                except Exception as exc:
                    logger.warning("CoT synthesizer failed: %s", exc)
                    stop_reason = f"synthesizer_error: {exc}"
                    break

                final_content = resp.content or ""
                cost = self._compute_cost(resp.model_id or synthesizer_role, resp.input_tokens, resp.output_tokens)
                total_cost += cost
                total_tokens += resp.input_tokens + resp.output_tokens
                models_used.add(resp.model_id or synthesizer_role)

                rounds.append({
                    "round": round_num,
                    "step": "synthesize",
                    "model_id": resp.model_id or synthesizer_role,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cost_usd": round(cost, 6),
                    "duration_ms": int(elapsed * 1000),
                })

        total_duration_ms = int((time.time() - start_time) * 1000)

        result = ChainResult(
            content=final_content or reasoning,
            chain_mode="cot",
            models_used=sorted(models_used),
            rounds=rounds,
            total_input_tokens=sum(r["input_tokens"] for r in rounds),
            total_output_tokens=sum(r["output_tokens"] for r in rounds),
            total_cost_usd=round(total_cost, 6),
            total_duration_ms=total_duration_ms,
            stop_reason=stop_reason,
            trace_id=trace_id,
            confidence=0.0,
        )
        self._write_chain_telemetry(result, function)
        return result

    def _cot_self_consistency(
        self,
        function: str,
        request: LLMRequest,
        cfg: dict,
        trace_id: str,
        start_time: float,
    ) -> ChainResult:
        """Run CoT multiple times in parallel and take majority vote."""
        runs = cfg.get("self_consistency_runs", 1)
        # Temporarily disable self-consistency to avoid infinite recursion
        single_cfg = copy.deepcopy(cfg)
        single_cfg["self_consistency_runs"] = 1

        answers: List[str] = []
        all_rounds: List[Dict[str, Any]] = []
        models_used: set = set()
        total_cost = 0.0
        total_tokens = 0

        with ThreadPoolExecutor(max_workers=min(runs, 5)) as executor:
            futures = {
                executor.submit(
                    self._cot_single, function, request, single_cfg, f"{trace_id}-{i}", start_time
                ): i
                for i in range(runs)
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    res = future.result(timeout=cfg["timeout_seconds"])
                    answers.append(res.content)
                    all_rounds.extend([{**r, "run": i} for r in res.rounds])
                    models_used.update(res.models_used)
                    total_cost += res.total_cost_usd
                    total_tokens += res.total_input_tokens + res.total_output_tokens
                except Exception as exc:
                    logger.warning("CoT self-consistency run %d failed: %s", i, exc)

        if not answers:
            raise RuntimeError("All self-consistency runs failed")

        # Majority vote: use synthesizer to pick the best answer
        sys_prompt, usr_prompt = ChainPrompts.self_consistency_voter(
            answers,
            system_prompt=request.system_prompt or "",
        )
        vote_req = self._build_request(request, sys_prompt, usr_prompt)

        synthesizer_role = cfg.get("synthesizer_role") or cfg["synthesizer_model"]
        try:
            vote_resp, _ = self._invoke_model(
                synthesizer_role, vote_req, function, cfg["timeout_seconds"]
            )
            final_content = vote_resp.content or answers[0]
            cost = self._compute_cost(
                vote_resp.model_id or synthesizer_role,
                vote_resp.input_tokens,
                vote_resp.output_tokens,
            )
            total_cost += cost
            total_tokens += vote_resp.input_tokens + vote_resp.output_tokens
            models_used.add(vote_resp.model_id or synthesizer_role)
            all_rounds.append({
                "step": "majority_vote",
                "model_id": vote_resp.model_id or synthesizer_role,
                "input_tokens": vote_resp.input_tokens,
                "output_tokens": vote_resp.output_tokens,
                "cost_usd": round(cost, 6),
            })
        except Exception as exc:
            logger.warning("Majority vote failed, using first answer: %s", exc)
            final_content = answers[0]

        total_duration_ms = int((time.time() - start_time) * 1000)

        result = ChainResult(
            content=final_content,
            chain_mode="cot_self_consistency",
            models_used=sorted(models_used),
            rounds=all_rounds,
            total_input_tokens=sum(r.get("input_tokens", 0) for r in all_rounds),
            total_output_tokens=sum(r.get("output_tokens", 0) for r in all_rounds),
            total_cost_usd=round(total_cost, 6),
            total_duration_ms=total_duration_ms,
            stop_reason="completed",
            trace_id=trace_id,
            confidence=0.0,
        )
        self._write_chain_telemetry(result, function)
        return result

    def invoke_chain_of_debate(
        self,
        function: str,
        request: LLMRequest,
    ) -> ChainResult:
        """Run Chain of Debate: parallel debate turns → judge synthesis.

        Args:
            function: ICDEV™ function name.
            request: Original LLM request.

        Returns:
            ChainResult with final judged response.
        """
        cfg = self._get_function_config(function, "cod")
        trace_id = self._session_id
        start_time = time.time()

        if not cfg["enabled"]:
            raise RuntimeError("Chain of Debate is disabled in config")

        if self._is_excluded(function, "cod"):
            raise RuntimeError(f"Function '{function}' is excluded from CoD")

        self._check_module_budget(function, estimated_cost=cfg.get("cost_cap_usd", 0.0))

        num_debaters = cfg["num_debaters"]
        debate_rounds = cfg["debate_rounds"]
        timeout = cfg["timeout_seconds"]
        deadline = time.time() + timeout

        # Select distinct debater models — maximize provider diversity for genuine multi-LLM debate.
        # get_diverse_models() picks N models from different provider families (ollama, anthropic,
        # openai, google). Falls back to legacy debater_models list if pool is empty.
        debater_pool_key = cfg.get("debater_pool_role") or ""
        debater_models_assigned: List[str] = []
        if debater_pool_key:
            try:
                debater_models_assigned = self.router.get_diverse_models(debater_pool_key, num_debaters)
            except Exception as exc:
                logger.debug("get_diverse_models failed for '%s': %s — using legacy list", debater_pool_key, exc)
        if not debater_models_assigned:
            debater_models_assigned = cfg.get("debater_models", ["qwen3-local"])

        judge_role = cfg.get("judge_role") or cfg.get("judge_model", "claude-sonnet")

        user_prompt = ""
        if request.messages and isinstance(request.messages[0], dict):
            user_prompt = str(request.messages[0].get("content", ""))

        rounds: List[Dict[str, Any]] = []
        models_used: set = set()
        total_cost = 0.0
        total_tokens = 0

        # Generate diverse positions for debaters
        positions = cfg.get("positions")
        if not positions:
            positions = self._generate_positions(user_prompt, num_debaters)

        # Debate rounds
        debate_history: List[Dict[str, Any]] = []
        for debate_round in range(1, debate_rounds + 1):
            if time.time() > deadline:
                break
            self._check_budget(total_cost, total_tokens, cfg)

            round_args: List[Dict[str, Any]] = []

            with ThreadPoolExecutor(max_workers=num_debaters) as executor:
                futures = {}
                for i in range(num_debaters):
                    # Each debater slot uses a pinned distinct model for the full debate
                    model_name = debater_models_assigned[i % len(debater_models_assigned)]
                    prior = [a["argument"] for a in debate_history if a["debater"] != i + 1]
                    sys_prompt, usr_prompt = ChainPrompts.debater(
                        user_prompt,
                        debater_number=i + 1,
                        position=positions[i],
                        prior_arguments=prior if debate_round > 1 else None,
                        system_prompt=request.system_prompt or "",
                        output_schema=request.output_schema,
                    )
                    debater_req = self._build_request(request, sys_prompt, usr_prompt)
                    fut = executor.submit(
                        self._invoke_model, model_name, debater_req, function, deadline - time.time()
                    )
                    futures[fut] = i

                for future in as_completed(futures):
                    i = futures[future]
                    model_name = debater_models_assigned[i % len(debater_models_assigned)]
                    try:
                        resp, elapsed = future.result(timeout=deadline - time.time())
                        argument = resp.content or ""
                        cost = self._compute_cost(resp.model_id or model_name, resp.input_tokens, resp.output_tokens)
                        total_cost += cost
                        total_tokens += resp.input_tokens + resp.output_tokens
                        models_used.add(resp.model_id or model_name)

                        debate_history.append({
                            "round": debate_round,
                            "debater": i + 1,
                            "position": positions[i],
                            "argument": argument,
                            "model_id": resp.model_id or model_name,
                        })
                        round_args.append({
                            "debater": i + 1,
                            "position": positions[i],
                            "argument": argument,
                        })
                        rounds.append({
                            "round": debate_round,
                            "step": f"debater_{i + 1}",
                            "model_id": resp.model_id or model_name,
                            "input_tokens": resp.input_tokens,
                            "output_tokens": resp.output_tokens,
                            "cost_usd": round(cost, 6),
                            "duration_ms": int(elapsed * 1000),
                        })
                    except Exception as exc:
                        logger.warning("CoD debater %d failed: %s", i + 1, exc)

            self._record_canvas_decision(
                decision_type="chain_of_debate",
                decision=f"Debate round {debate_round}",
                rationale=json.dumps(round_args)[:2000],
                model_used=",".join(sorted(models_used)),
                confidence=0.0,
                alternatives=[a["argument"][:500] for a in round_args],
            )

        # ---- JUDGE ----
        if time.time() > deadline:
            stop_reason = "timeout"
            final_content = debate_history[-1]["argument"] if debate_history else ""
        else:
            self._check_budget(total_cost, total_tokens, cfg)
            args_for_judge = [
                {
                    "debater": d["debater"],
                    "position": d["position"],
                    "argument": d["argument"],
                }
                for d in debate_history
            ]
            sys_prompt, usr_prompt = ChainPrompts.judge(
                user_prompt,
                args_for_judge,
                system_prompt=request.system_prompt or "",
                output_schema=request.output_schema,
            )
            judge_req = self._build_request(request, sys_prompt, usr_prompt)

            try:
                resp, elapsed = self._invoke_model(
                    judge_role, judge_req, function, deadline - time.time()
                )
                final_content = resp.content or ""
                cost = self._compute_cost(resp.model_id or judge_role, resp.input_tokens, resp.output_tokens)
                total_cost += cost
                total_tokens += resp.input_tokens + resp.output_tokens
                models_used.add(resp.model_id or judge_role)

                rounds.append({
                    "step": "judge",
                    "model_id": resp.model_id or judge_role,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cost_usd": round(cost, 6),
                    "duration_ms": int(elapsed * 1000),
                })
                stop_reason = "completed"

                # Extract confidence from judge response
                confidence = self._extract_confidence(final_content)
            except Exception as exc:
                logger.warning("CoD judge failed: %s", exc)
                final_content = debate_history[-1]["argument"] if debate_history else ""
                stop_reason = f"judge_error: {exc}"
                confidence = 0.0

        total_duration_ms = int((time.time() - start_time) * 1000)

        result = ChainResult(
            content=final_content,
            chain_mode="cod",
            models_used=sorted(models_used),
            rounds=rounds,
            total_input_tokens=sum(r.get("input_tokens", 0) for r in rounds),
            total_output_tokens=sum(r.get("output_tokens", 0) for r in rounds),
            total_cost_usd=round(total_cost, 6),
            total_duration_ms=total_duration_ms,
            stop_reason=stop_reason,
            trace_id=trace_id,
            confidence=confidence if "confidence" in dir() else 0.0,
        )
        self._write_chain_telemetry(result, function)
        return result

    def invoke_council(self, function: str, request: LLMRequest) -> ChainResult:
        """Run an LLM Council: fixed-perspective advisors respond independently
        and in parallel -> responses are anonymized -> each advisor peer-
        reviews all anonymized responses -> a chairman synthesizes everything
        (de-anonymized) plus all peer reviews into a structured verdict.

        Distinct from Chain of Debate (see _COUNCIL_ADVISORS' module comment):
        built for decision-quality analysis via structural adversarial
        diversity, not debate-to-a-winner. Primary use case is cross-repo
        callers (e.g. idea_lab) pressure-testing a high-stakes idea/decision
        via the `council_query` MCP tool.

        Degrades cleanly: if every advisor call fails, returns an empty-
        content ChainResult with stop_reason="all_advisors_failed" rather
        than raising -- callers (e.g. the MCP handler) treat that the same
        as any other unavailable-LLM case.
        """
        cfg = self._get_function_config(function, "council")
        trace_id = self._session_id
        start_time = time.time()

        if not cfg["enabled"]:
            raise RuntimeError("Council is disabled in config")
        if self._is_excluded(function, "council"):
            raise RuntimeError(f"Function '{function}' is excluded from Council")

        self._check_module_budget(function, estimated_cost=cfg.get("cost_cap_usd", 0.0))

        # Fixed cognitive lenses read from the shared frame library (dvg-frames-02),
        # with a code-level fallback to the _COUNCIL_ADVISORS constant. Behavior is
        # identical to the historical hardcoded path.
        council_advisors = self._load_council_advisors()
        num_advisors = min(cfg["num_advisors"], len(council_advisors))
        timeout = cfg["timeout_seconds"]
        deadline = time.time() + timeout

        advisor_pool_key = cfg.get("advisor_pool_role") or ""
        advisor_models_assigned: List[str] = []
        if advisor_pool_key:
            try:
                advisor_models_assigned = self.router.get_diverse_models(advisor_pool_key, num_advisors)
            except Exception as exc:
                logger.debug("get_diverse_models failed for '%s': %s — using legacy list", advisor_pool_key, exc)
        if not advisor_models_assigned:
            advisor_models_assigned = cfg.get("advisor_models", ["qwen3-local"])

        chairman_role = cfg.get("chairman_role") or cfg.get("chairman_model", "claude-sonnet")

        user_prompt = ""
        if request.messages and isinstance(request.messages[0], dict):
            user_prompt = str(request.messages[0].get("content", ""))

        advisors = council_advisors[:num_advisors]
        rounds: List[Dict[str, Any]] = []
        models_used: set = set()
        total_cost = 0.0
        total_tokens = 0

        # ---- STEP 1: advisors respond independently, in parallel ----
        advisor_results: List[Optional[Dict[str, Any]]] = [None] * num_advisors
        with ThreadPoolExecutor(max_workers=num_advisors) as executor:
            futures = {}
            for i, (name, style) in enumerate(advisors):
                model_name = advisor_models_assigned[i % len(advisor_models_assigned)]
                sys_prompt, usr_prompt = ChainPrompts.council_advisor(
                    user_prompt, name, style,
                    system_prompt=request.system_prompt or "",
                    output_schema=request.output_schema,
                )
                advisor_req = self._build_request(request, sys_prompt, usr_prompt)
                fut = executor.submit(
                    self._invoke_model, model_name, advisor_req, function, deadline - time.time()
                )
                futures[fut] = i

            for future in as_completed(futures):
                i = futures[future]
                name, _style = advisors[i]
                model_name = advisor_models_assigned[i % len(advisor_models_assigned)]
                try:
                    resp, elapsed = future.result(timeout=max(deadline - time.time(), 0.1))
                    content = resp.content or ""
                    cost = self._compute_cost(resp.model_id or model_name, resp.input_tokens, resp.output_tokens)
                    total_cost += cost
                    total_tokens += resp.input_tokens + resp.output_tokens
                    models_used.add(resp.model_id or model_name)
                    advisor_results[i] = {
                        "name": name, "response": content, "model_id": resp.model_id or model_name,
                    }
                    rounds.append({
                        "step": f"advisor:{name}",
                        "model_id": resp.model_id or model_name,
                        "input_tokens": resp.input_tokens,
                        "output_tokens": resp.output_tokens,
                        "cost_usd": round(cost, 6),
                        "duration_ms": int(elapsed * 1000),
                    })
                except Exception as exc:
                    logger.warning("Council advisor '%s' failed: %s", name, exc)

        answered = [(i, r) for i, r in enumerate(advisor_results) if r and r["response"]]

        if not answered:
            total_duration_ms = int((time.time() - start_time) * 1000)
            result = ChainResult(
                content="", chain_mode="council", models_used=sorted(models_used), rounds=rounds,
                total_input_tokens=total_tokens, total_output_tokens=0,
                total_cost_usd=round(total_cost, 6), total_duration_ms=total_duration_ms,
                stop_reason="all_advisors_failed", trace_id=trace_id,
            )
            self._write_chain_telemetry(result, function)
            return result

        # ---- STEP 2: anonymize + shuffle for peer review ----
        import random

        shuffled = list(answered)
        random.shuffle(shuffled)
        labels = [chr(ord("A") + j) for j in range(len(shuffled))]
        anonymized = [{"label": labels[j], "response": r["response"]} for j, (_i, r) in enumerate(shuffled)]

        # ---- STEP 3: peer review, in parallel (one review per answered advisor) ----
        peer_reviews: List[str] = []
        if time.time() < deadline:
            try:
                self._check_budget(total_cost, total_tokens, cfg)
            except BudgetExceededError:
                peer_reviews = []
            else:
                with ThreadPoolExecutor(max_workers=len(answered)) as executor:
                    futures = {}
                    for i, r in answered:
                        model_name = advisor_models_assigned[i % len(advisor_models_assigned)]
                        sys_prompt, usr_prompt = ChainPrompts.council_peer_review(
                            user_prompt, anonymized, system_prompt=request.system_prompt or "",
                        )
                        review_req = self._build_request(request, sys_prompt, usr_prompt)
                        fut = executor.submit(
                            self._invoke_model, model_name, review_req, function, deadline - time.time()
                        )
                        futures[fut] = i

                    for future in as_completed(futures):
                        i = futures[future]
                        try:
                            resp, elapsed = future.result(timeout=max(deadline - time.time(), 0.1))
                            review_text = resp.content or ""
                            cost = self._compute_cost(resp.model_id or "", resp.input_tokens, resp.output_tokens)
                            total_cost += cost
                            total_tokens += resp.input_tokens + resp.output_tokens
                            if resp.model_id:
                                models_used.add(resp.model_id)
                            peer_reviews.append(review_text)
                            rounds.append({
                                "step": "peer_review",
                                "model_id": resp.model_id or "",
                                "input_tokens": resp.input_tokens,
                                "output_tokens": resp.output_tokens,
                                "cost_usd": round(cost, 6),
                                "duration_ms": int(elapsed * 1000),
                            })
                        except Exception as exc:
                            logger.warning("Council peer review (advisor index %d) failed: %s", i, exc)

        # ---- STEP 4: chairman synthesis ----
        if time.time() > deadline:
            stop_reason = "timeout"
            final_content = answered[0][1]["response"]
            confidence = 0.0
        else:
            try:
                self._check_budget(total_cost, total_tokens, cfg)
                sys_prompt, usr_prompt = ChainPrompts.council_chairman(
                    user_prompt,
                    [{"name": r["name"], "response": r["response"]} for _i, r in answered],
                    peer_reviews,
                    system_prompt=request.system_prompt or "",
                    output_schema=request.output_schema,
                )
                chairman_req = self._build_request(request, sys_prompt, usr_prompt)
                resp, elapsed = self._invoke_model(chairman_role, chairman_req, function, deadline - time.time())
                final_content = resp.content or ""
                cost = self._compute_cost(resp.model_id or chairman_role, resp.input_tokens, resp.output_tokens)
                total_cost += cost
                total_tokens += resp.input_tokens + resp.output_tokens
                models_used.add(resp.model_id or chairman_role)
                rounds.append({
                    "step": "chairman",
                    "model_id": resp.model_id or chairman_role,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cost_usd": round(cost, 6),
                    "duration_ms": int(elapsed * 1000),
                })
                stop_reason = "completed"
                confidence = self._extract_confidence(final_content)
            except Exception as exc:
                logger.warning("Council chairman failed: %s", exc)
                final_content = answered[0][1]["response"]
                stop_reason = f"chairman_error: {exc}"
                confidence = 0.0

        total_duration_ms = int((time.time() - start_time) * 1000)
        result = ChainResult(
            content=final_content,
            chain_mode="council",
            models_used=sorted(models_used),
            rounds=rounds,
            total_input_tokens=sum(r.get("input_tokens", 0) for r in rounds),
            total_output_tokens=sum(r.get("output_tokens", 0) for r in rounds),
            total_cost_usd=round(total_cost, 6),
            total_duration_ms=total_duration_ms,
            stop_reason=stop_reason,
            trace_id=trace_id,
            confidence=confidence,
        )
        self._write_chain_telemetry(result, function)
        self._record_canvas_decision(
            decision_type="council",
            decision="Council verdict",
            rationale=final_content[:2000],
            model_used=",".join(sorted(models_used)),
            confidence=confidence,
            alternatives=[r["response"][:500] for _i, r in answered],
        )
        return result

    def invoke_divergence(self, function: str, request: LLMRequest) -> ChainResult:
        """Run Divergence: a single isolated generative fan-out that produces a
        raw pool of candidate ideas -- the generative counterpart to invoke_council.

        Two behavioral deltas versus invoke_council, and they are the entire point:

        1. STRICT ISOLATION. One round only. No peer review, no anonymized cross-
           reading, no shared history. Branches never see each other's output --
           unlike invoke_chain_of_debate, which threads prior_arguments in from
           round 2. Serializing or cross-feeding the branches collapses divergence
           into a single wider thought, so it is deliberately forbidden here.
        2. GENERATIVE PROMPTS. Each branch gets the problem plus one frame from a
           generative frame set and is told to produce candidate ideas and NOT to
           evaluate, rank, or self-critique. Scoring / clustering / deepening is a
           SEPARATE invocation (dvg-critic-*) with an opposing critic system prompt
           -- the generator/critic split stays mechanical, never one response.

        The return value's ``content`` is the raw, labeled idea pool; downstream
        critic passes consume it. Degrades cleanly: if every branch fails, returns
        an empty-content ChainResult with stop_reason="all_branches_failed" rather
        than raising -- callers treat that like any other unavailable-LLM case.
        """
        cfg = self._get_function_config(function, "divergence")
        trace_id = self._session_id
        start_time = time.time()

        if not cfg["enabled"]:
            raise RuntimeError("Divergence is disabled in config")
        if self._is_excluded(function, "divergence"):
            raise RuntimeError(f"Function '{function}' is excluded from Divergence")

        # Cost is the headline risk (upstream: ~10 agent calls, 5-10x a direct
        # answer). Trip the module budget BEFORE any model call so an over-budget
        # run aborts rather than overspending.
        self._check_module_budget(function, estimated_cost=cfg.get("cost_cap_usd", 0.0))

        frames = self._load_divergence_frames(cfg.get("frame_set", "generative"))
        num_branches = max(1, min(int(cfg["num_branches"]), len(frames)))
        frames = frames[:num_branches]
        timeout = cfg["timeout_seconds"]
        deadline = time.time() + timeout

        branch_pool_key = cfg.get("branch_pool_role") or ""
        branch_models_assigned: List[str] = []
        if branch_pool_key:
            try:
                branch_models_assigned = self.router.get_diverse_models(branch_pool_key, num_branches)
            except Exception as exc:
                logger.debug("get_diverse_models failed for '%s': %s — using legacy list", branch_pool_key, exc)
        if not branch_models_assigned:
            branch_models_assigned = cfg.get("branch_models", ["qwen3-local"])

        user_prompt = ""
        if request.messages and isinstance(request.messages[0], dict):
            user_prompt = str(request.messages[0].get("content", ""))

        rounds: List[Dict[str, Any]] = []
        models_used: set = set()
        total_cost = 0.0
        total_tokens = 0

        # ---- Single round: branches generate independently, in parallel. No
        # prior_arguments are ever threaded in -- strict isolation is the method. ----
        branch_results: List[Optional[Dict[str, Any]]] = [None] * num_branches
        with ThreadPoolExecutor(max_workers=num_branches) as executor:
            futures = {}
            for i, (name, instruction) in enumerate(frames):
                model_name = branch_models_assigned[i % len(branch_models_assigned)]
                sys_prompt, usr_prompt = ChainPrompts.divergence_branch(
                    user_prompt, name, instruction,
                    system_prompt=request.system_prompt or "",
                    output_schema=request.output_schema,
                )
                branch_req = self._build_request(request, sys_prompt, usr_prompt)
                fut = executor.submit(
                    self._invoke_model, model_name, branch_req, function, deadline - time.time()
                )
                futures[fut] = i

            for future in as_completed(futures):
                i = futures[future]
                name, _instr = frames[i]
                model_name = branch_models_assigned[i % len(branch_models_assigned)]
                try:
                    resp, elapsed = future.result(timeout=max(deadline - time.time(), 0.1))
                    content = resp.content or ""
                    cost = self._compute_cost(resp.model_id or model_name, resp.input_tokens, resp.output_tokens)
                    total_cost += cost
                    total_tokens += resp.input_tokens + resp.output_tokens
                    models_used.add(resp.model_id or model_name)
                    branch_results[i] = {
                        "frame": name, "response": content, "model_id": resp.model_id or model_name,
                    }
                    rounds.append({
                        "step": f"branch:{name}",
                        "model_id": resp.model_id or model_name,
                        "input_tokens": resp.input_tokens,
                        "output_tokens": resp.output_tokens,
                        "cost_usd": round(cost, 6),
                        "duration_ms": int(elapsed * 1000),
                    })
                except Exception as exc:
                    logger.warning("Divergence branch '%s' failed: %s", name, exc)

        answered = [r for r in branch_results if r and r["response"]]

        if not answered:
            total_duration_ms = int((time.time() - start_time) * 1000)
            result = ChainResult(
                content="", chain_mode="divergence", models_used=sorted(models_used), rounds=rounds,
                total_input_tokens=total_tokens, total_output_tokens=0,
                total_cost_usd=round(total_cost, 6), total_duration_ms=total_duration_ms,
                stop_reason="all_branches_failed", trace_id=trace_id,
            )
            self._write_chain_telemetry(result, function)
            return result

        # Aggregate the raw idea pool -- labeled by frame so a critic pass can
        # attribute each candidate. NO synthesis / ranking / dedupe happens here;
        # that is deliberately deferred to the opposing critic invocation.
        pool_sections = [
            f"## Frame: {r['frame']}\n{r['response'].strip()}" for r in answered
        ]
        final_content = (
            f"# Divergent Idea Pool ({len(answered)} branch(es), frame set "
            f"'{cfg.get('frame_set', 'generative')}')\n\n" + "\n\n".join(pool_sections)
        )

        total_duration_ms = int((time.time() - start_time) * 1000)
        result = ChainResult(
            content=final_content,
            chain_mode="divergence",
            models_used=sorted(models_used),
            rounds=rounds,
            total_input_tokens=sum(r.get("input_tokens", 0) for r in rounds),
            total_output_tokens=sum(r.get("output_tokens", 0) for r in rounds),
            total_cost_usd=round(total_cost, 6),
            total_duration_ms=total_duration_ms,
            stop_reason="completed",
            trace_id=trace_id,
            confidence=0.0,  # divergence does not self-score; the critic pass owns confidence
        )
        self._write_chain_telemetry(result, function)
        self._record_canvas_decision(
            decision_type="divergence",
            decision=f"Generated {len(answered)}-branch idea pool",
            rationale=final_content[:2000],
            model_used=",".join(sorted(models_used)),
            confidence=0.0,
            alternatives=[r["response"][:500] for r in answered],
        )
        return result

    def _generate_positions(self, user_prompt: str, num_debaters: int) -> List[str]:
        """Generate diverse positions for debaters.

        For simplicity, uses heuristic positions. In production, this could
        use the LLM to generate positions.
        """
        defaults = [
            "Strongly in favor",
            "Moderately in favor with caveats",
            "Neutral / balanced view",
            "Moderately against with reservations",
            "Strongly against",
        ]
        return defaults[:num_debaters]

    @staticmethod
    def _extract_confidence(text: str) -> float:
        """Extract a confidence score 0.0–1.0 from judge response text."""
        import re

        # Look for patterns like "confidence: 0.85" or "confidence score: 0.85"
        patterns = [
            r"confidence[:\s]+([0-9]*\.?[0-9]+)",
            r"confidence score[:\s]+([0-9]*\.?[0-9]+)",
            r"([0-9]*\.?[0-9]+)\s*\/\s*1\.0",
            r"([0-9]*\.?[0-9]+)\s*%",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                if val > 1.0 and val <= 100.0:
                    val = val / 100.0
                return min(max(val, 0.0), 1.0)
        return 0.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chain of Thought / Chain of Debate CLI")
    parser.add_argument("--cot", action="store_true", help="Run Chain of Thought")
    parser.add_argument("--cod", action="store_true", help="Run Chain of Debate")
    parser.add_argument("--divergence", action="store_true", help="Run Divergence (opt-in idea-pool fan-out)")
    parser.add_argument("--self-consistency", type=int, default=1, help="Self-consistency runs for CoT")
    parser.add_argument("--function", type=str, required=True, help="ICDEV function name")
    parser.add_argument("--prompt", type=str, required=True, help="User prompt")
    parser.add_argument("--system-prompt", type=str, default="", help="System prompt")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--stats", action="store_true", help="Show chain telemetry stats")
    parser.add_argument("--show-config", action="store_true", help="Show chain config")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.show_config:
        router = LLMRouter()
        cfg = router._config.get("chain_orchestration", {})
        print(json.dumps(cfg, indent=2))
        return

    if args.stats:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT chain_mode, COUNT(*) as count,
                   AVG(cost_usd) as avg_cost,
                   AVG(duration_ms) as avg_duration
            FROM llm_chain_telemetry
            GROUP BY chain_mode
            """
        ).fetchall()
        conn.close()
        result = [
            {
                "chain_mode": r[0],
                "count": r[1],
                "avg_cost_usd": round(r[2] or 0, 6),
                "avg_duration_ms": round(r[3] or 0, 2),
            }
            for r in rows
        ]
        print(json.dumps(result, indent=2))
        return

    orchestrator = ChainOrchestrator()
    request = LLMRequest(
        messages=[{"role": "user", "content": args.prompt}],
        system_prompt=args.system_prompt,
    )

    try:
        if args.divergence:
            result = orchestrator.invoke_divergence(args.function, request)
        elif args.cod:
            result = orchestrator.invoke_chain_of_debate(args.function, request)
        else:
            result = orchestrator.invoke_chain_of_thought(args.function, request)

        if args.json:
            print(json.dumps({
                "content": result.content,
                "chain_mode": result.chain_mode,
                "models_used": result.models_used,
                "total_cost_usd": result.total_cost_usd,
                "total_input_tokens": result.total_input_tokens,
                "total_output_tokens": result.total_output_tokens,
                "total_duration_ms": result.total_duration_ms,
                "stop_reason": result.stop_reason,
                "trace_id": result.trace_id,
                "confidence": result.confidence,
                "rounds": result.rounds,
            }, indent=2))
        else:
            print(result.content)
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "trace_id": orchestrator._session_id}))
        else:
            print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
