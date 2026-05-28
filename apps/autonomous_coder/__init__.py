# CUI // SP-CTI
"""Autonomous Coder — agentic AI application generated from AADC Solution Pack."""

try:
    from apps.autonomous_coder.agents.orchestrator import CoderResult, Orchestrator
    from apps.autonomous_coder.llm_bridge import make_llm_call
    from apps.autonomous_coder.safety.circuit_breaker import BreakerConfig

    def run(task: str, backend: str = "auto", **breaker_kwargs) -> CoderResult:
        """High-level entry point: run the full agentic coding pipeline."""
        llm = make_llm_call(backend)
        cfg = BreakerConfig(**breaker_kwargs) if breaker_kwargs else None
        orch = Orchestrator(llm, cfg)
        return orch.run(task)

    __all__ = ["run", "Orchestrator", "CoderResult", "make_llm_call", "BreakerConfig"]
except ImportError:
    __all__ = []
