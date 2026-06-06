# CUI // SP-CTI
"""Agentic Research Pipeline — Governance Layer.

Implements the three governance nodes from AADC Template #5 that sit
between the Synthesis LLM and the final output:

  SynthesisLLM → ConfidenceGate → OutputValidator → PipelineAuditLogger

Each node is independently testable and wired together by GovernanceLayer,
the public entry-point consumed by AgenticResearchPipeline.run().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.aadc.governance_layer")

_DEFAULT_CONFIDENCE_THRESHOLD = 0.5
_MIN_ANSWER_LEN = 10


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceGateResult:
    passed: bool
    confidence: float
    threshold: float
    reason: str = ""


@dataclass
class OutputValidationResult:
    valid: bool
    reason: str = ""


@dataclass
class GovernanceResult:
    confidence_gate: ConfidenceGateResult
    output_validation: OutputValidationResult
    audit_entry_id: int = -1
    """Row ID returned by audit_logger.log_event(), or -1 on failure/skip."""


# ---------------------------------------------------------------------------
# ConfidenceGate — confidence-threshold node
# ---------------------------------------------------------------------------

class ConfidenceGate:
    """Validates that the synthesis confidence meets the required threshold.

    NIST AI RMF MEASURE 2.5: outputs must meet defined confidence criteria
    before being passed downstream.

    Args:
        threshold: Minimum acceptable confidence score (0–1).  Syntheses
            that return 0.0 (e.g. LLM error) always fail the gate.
    """

    def __init__(self, threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD) -> None:
        self.threshold = threshold

    def evaluate(self, confidence: float) -> ConfidenceGateResult:
        """Return a ConfidenceGateResult for the given confidence score."""
        passed = confidence >= self.threshold
        reason = (
            f"confidence {confidence:.3f} >= threshold {self.threshold:.3f}"
            if passed
            else f"confidence {confidence:.3f} < threshold {self.threshold:.3f}"
        )
        return ConfidenceGateResult(
            passed=passed,
            confidence=confidence,
            threshold=self.threshold,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# OutputValidator — output-validator node
# ---------------------------------------------------------------------------

class OutputValidator:
    """Validates the synthesized answer before it leaves the pipeline.

    Checks:
      1. Answer is non-empty and meets a minimum length (guards against
         LLM truncation or silent failures).
      2. Answer does not contain a raw error sentinel from SynthesisLLM.

    This is a lightweight, deterministic gate — no LLM calls.
    """

    def validate(self, answer: str, error: str = "") -> OutputValidationResult:
        """Validate the synthesis output.

        Args:
            answer: The synthesized answer string.
            error:  Any error string from SynthesisLLM (signals degraded run).

        Returns:
            OutputValidationResult with valid=True only when the output
            meets all quality checks.
        """
        if error:
            return OutputValidationResult(valid=False, reason=f"synthesis error: {error}")
        stripped = (answer or "").strip()
        if not stripped:
            return OutputValidationResult(valid=False, reason="answer is empty")
        if len(stripped) < _MIN_ANSWER_LEN:
            return OutputValidationResult(
                valid=False,
                reason=f"answer too short ({len(stripped)} chars, min {_MIN_ANSWER_LEN})",
            )
        return OutputValidationResult(valid=True, reason="answer passed validation")


# ---------------------------------------------------------------------------
# PipelineAuditLogger — audit-logger node
# ---------------------------------------------------------------------------

class PipelineAuditLogger:
    """Governance audit logger for the Agentic Research Pipeline.

    Wraps tools.audit.audit_logger.log_event() to write immutable,
    NIST AU-2-compliant audit entries for every pipeline execution.

    Logging is non-fatal: a DB write failure is logged to the Python
    logger but never surfaces as a pipeline exception.
    """

    def log_run_started(
        self,
        *,
        design_id: str,
        query: str,
        actor: str = "pipeline",
        session_id: Optional[str] = None,
    ) -> int:
        """Log that a pipeline run has started."""
        return self._write(
            event_type="pipeline.run_started",
            actor=actor,
            action=f"Research pipeline started for design {design_id}",
            design_id=design_id,
            details={"query": query[:500], "design_id": design_id},
            session_id=session_id,
        )

    def log_confidence_gate(
        self,
        *,
        design_id: str,
        gate: ConfidenceGateResult,
        actor: str = "pipeline",
        session_id: Optional[str] = None,
    ) -> int:
        """Log the confidence gate evaluation result."""
        event_type = (
            "pipeline.confidence_gate_passed"
            if gate.passed
            else "pipeline.confidence_gate_failed"
        )
        return self._write(
            event_type=event_type,
            actor=actor,
            action=f"Confidence gate {'passed' if gate.passed else 'failed'}: {gate.reason}",
            design_id=design_id,
            details={
                "confidence": gate.confidence,
                "threshold": gate.threshold,
                "passed": gate.passed,
                "reason": gate.reason,
                "design_id": design_id,
            },
            session_id=session_id,
        )

    def log_output_validation(
        self,
        *,
        design_id: str,
        validation: OutputValidationResult,
        actor: str = "pipeline",
        session_id: Optional[str] = None,
    ) -> int:
        """Log the output validation result."""
        event_type = (
            "pipeline.output_validated"
            if validation.valid
            else "pipeline.output_rejected"
        )
        return self._write(
            event_type=event_type,
            actor=actor,
            action=f"Output {'validated' if validation.valid else 'rejected'}: {validation.reason}",
            design_id=design_id,
            details={
                "valid": validation.valid,
                "reason": validation.reason,
                "design_id": design_id,
            },
            session_id=session_id,
        )

    def log_run_completed(
        self,
        *,
        design_id: str,
        query: str,
        chunks_used: int,
        model: str,
        confidence: float,
        confidence_gate_passed: bool,
        output_valid: bool,
        actor: str = "pipeline",
        session_id: Optional[str] = None,
    ) -> int:
        """Log successful pipeline completion (final audit-logger node)."""
        return self._write(
            event_type="pipeline.run_completed",
            actor=actor,
            action=f"Research pipeline completed for design {design_id}",
            design_id=design_id,
            details={
                "query": query[:500],
                "chunks_used": chunks_used,
                "model": model,
                "confidence": confidence,
                "confidence_gate_passed": confidence_gate_passed,
                "output_valid": output_valid,
                "design_id": design_id,
            },
            session_id=session_id,
        )

    def log_run_failed(
        self,
        *,
        design_id: str,
        query: str,
        error: str,
        actor: str = "pipeline",
        session_id: Optional[str] = None,
    ) -> int:
        """Log a pipeline execution failure."""
        return self._write(
            event_type="pipeline.run_failed",
            actor=actor,
            action=f"Research pipeline failed for design {design_id}: {error[:200]}",
            design_id=design_id,
            details={
                "query": query[:500],
                "error": error[:1000],
                "design_id": design_id,
            },
            session_id=session_id,
        )

    def _write(
        self,
        *,
        event_type: str,
        actor: str,
        action: str,
        design_id: str,
        details: dict,
        session_id: Optional[str],
    ) -> int:
        try:
            from tools.audit.audit_logger import log_event
            return log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                project_id=design_id,
                details=details,
                classification="CUI",
                session_id=session_id,
            )
        except Exception as exc:
            logger.warning("PipelineAuditLogger._write failed (%s): %s", event_type, exc)
            return -1


# ---------------------------------------------------------------------------
# GovernanceLayer — composes the three governance nodes
# ---------------------------------------------------------------------------

class GovernanceLayer:
    """Governance layer for the Agentic Research Pipeline.

    Composes the three governance nodes in execution order:
      ConfidenceGate → OutputValidator → PipelineAuditLogger

    Designed to be called once per pipeline run *after* synthesis
    completes.  All logging is non-fatal.

    Args:
        confidence_threshold: Minimum confidence score to pass the gate.
        design_id:            AADC design ID (used as project_id in audit).
        actor:                Audit actor label (default "pipeline").
        session_id:           Correlation/session ID forwarded to audit log.
    """

    def __init__(
        self,
        *,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
        design_id: str = "",
        actor: str = "pipeline",
        session_id: Optional[str] = None,
    ) -> None:
        self.confidence_gate = ConfidenceGate(threshold=confidence_threshold)
        self.output_validator = OutputValidator()
        self.audit_logger = PipelineAuditLogger()
        self.design_id = design_id
        self.actor = actor
        self.session_id = session_id

    def log_started(self, query: str) -> None:
        """Call at the start of AgenticResearchPipeline.run()."""
        self.audit_logger.log_run_started(
            design_id=self.design_id,
            query=query,
            actor=self.actor,
            session_id=self.session_id,
        )

    def evaluate(
        self,
        *,
        query: str,
        answer: str,
        confidence: float,
        chunks_used: int,
        model: str,
        error: str = "",
    ) -> GovernanceResult:
        """Run the full governance chain after synthesis.

        Evaluates confidence gate, validates output, and writes the
        final audit-logger entry.  Returns a GovernanceResult summarising
        the outcome of all three nodes.
        """
        gate = self.confidence_gate.evaluate(confidence)
        self.audit_logger.log_confidence_gate(
            design_id=self.design_id,
            gate=gate,
            actor=self.actor,
            session_id=self.session_id,
        )

        validation = self.output_validator.validate(answer=answer, error=error)
        self.audit_logger.log_output_validation(
            design_id=self.design_id,
            validation=validation,
            actor=self.actor,
            session_id=self.session_id,
        )

        entry_id = self.audit_logger.log_run_completed(
            design_id=self.design_id,
            query=query,
            chunks_used=chunks_used,
            model=model,
            confidence=confidence,
            confidence_gate_passed=gate.passed,
            output_valid=validation.valid,
            actor=self.actor,
            session_id=self.session_id,
        )

        return GovernanceResult(
            confidence_gate=gate,
            output_validation=validation,
            audit_entry_id=entry_id,
        )

    def log_failed(self, query: str, error: str) -> None:
        """Call when AgenticResearchPipeline.run() raises an exception."""
        self.audit_logger.log_run_failed(
            design_id=self.design_id,
            query=query,
            error=error,
            actor=self.actor,
            session_id=self.session_id,
        )
