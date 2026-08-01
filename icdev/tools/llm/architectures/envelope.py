"""Uniform result envelope for AGX reasoning architectures.

Adapted from the ``.run(task) -> ArchitectureResult`` contract in
github.com/FareedKhan-dev/all-agentic-architectures (MIT, Copyright (c) 2025
Fareed Khan). ICDEV adapts the *pattern* and vendors no upstream code.

Honesty invariants (mirroring ``tools/twin_core/``): wrap, never obscure; never
fabricate a verdict. When an architecture cannot complete — budget exceeded,
malformed structured output from a small local model, an unavailable provider —
it returns an envelope with ``degraded=True`` and an honest ``stop_reason``
rather than a fabricated ``output``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Envelope schema version. Bump on any breaking change to the field set so the
# benchmark suite (agx-bench-01) can detect envelopes it cannot compare.
ENVELOPE_SCHEMA_VERSION = "1.0"


@dataclass
class ArchitectureStep:
    """One observable step inside an architecture's run.

    Kept deliberately small and provider-neutral: an architecture records the
    logical step it took (``name``), which logical model(s) served it, and the
    resource cost. Free-form detail goes in ``detail`` — never a fabricated
    score.
    """

    name: str
    model_ids: List[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "model_ids": list(self.model_ids),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "detail": dict(self.detail),
        }


@dataclass
class ArchitectureBudget:
    """Hard budget ceiling for a single architecture run.

    ``None`` on any field means "no ceiling from the caller" — the underlying
    implementation's own config-level caps still apply. Passed into
    ``run(task, *, router, budget)`` and honored through the existing
    ``BudgetExceededError`` path in ChainOrchestrator.
    """

    max_cost_usd: Optional[float] = None
    max_tokens: Optional[int] = None
    max_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost_usd,
            "max_tokens": self.max_tokens,
            "max_seconds": self.max_seconds,
        }


@dataclass
class ArchitectureResult:
    """Standardized envelope returned by every registered architecture.

    Any canvas or router function can swap reasoning strategies by name because
    every architecture returns this same shape. Carries provenance so the
    benchmark suite can grade strategies against one another honestly.

    Fields:
        architecture:   Registered name of the architecture that produced this.
        output:         The final text output (empty string when degraded).
        steps:          Ordered list of :class:`ArchitectureStep`.
        model_ids_used: Distinct logical model names that served any step.
        input_tokens / output_tokens / cost_usd / duration_ms:
                        Aggregate resource usage across all steps.
        method:         Provenance — how the output was produced (e.g.
                        ``"wrapped:chain_of_thought"``). Never fabricated.
        degraded:       True if the architecture could not complete cleanly and
                        fell back (budget exceeded, malformed output, provider
                        unavailable). A degraded envelope MUST NOT present a
                        fabricated verdict as if complete.
        stop_reason:    Machine-readable termination reason.
        trace_id:       Correlation id from the underlying implementation.
        schema_version: Envelope schema version for compatibility checks.
        metadata:       Architecture-specific extras (never a hidden verdict).
    """

    architecture: str
    output: str = ""
    steps: List[ArchitectureStep] = field(default_factory=list)
    model_ids_used: List[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    method: str = ""
    degraded: bool = False
    stop_reason: str = ""
    trace_id: str = ""
    schema_version: str = ENVELOPE_SCHEMA_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "architecture": self.architecture,
            "output": self.output,
            "steps": [s.to_dict() for s in self.steps],
            "model_ids_used": list(self.model_ids_used),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "method": self.method,
            "degraded": self.degraded,
            "stop_reason": self.stop_reason,
            "trace_id": self.trace_id,
            "schema_version": self.schema_version,
            "metadata": dict(self.metadata),
        }
