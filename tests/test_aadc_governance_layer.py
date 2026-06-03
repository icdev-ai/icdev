# CUI // SP-CTI
"""Tests for AADC governance_layer — ConfidenceGate, OutputValidator,
PipelineAuditLogger, GovernanceLayer, and integration with AgenticResearchPipeline.

All audit writes and LLM calls are mocked so the suite runs offline.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agentic_ai_canvas.governance_layer import (
    ConfidenceGate,
    ConfidenceGateResult,
    GovernanceLayer,
    GovernanceResult,
    OutputValidationResult,
    OutputValidator,
    PipelineAuditLogger,
)


# ---------------------------------------------------------------------------
# ConfidenceGate
# ---------------------------------------------------------------------------

class TestConfidenceGate(unittest.TestCase):

    def test_passes_above_threshold(self):
        gate = ConfidenceGate(threshold=0.5)
        result = gate.evaluate(0.8)
        self.assertIsInstance(result, ConfidenceGateResult)
        self.assertTrue(result.passed)
        self.assertAlmostEqual(result.confidence, 0.8)
        self.assertAlmostEqual(result.threshold, 0.5)

    def test_fails_below_threshold(self):
        gate = ConfidenceGate(threshold=0.5)
        result = gate.evaluate(0.3)
        self.assertFalse(result.passed)

    def test_fails_at_zero(self):
        gate = ConfidenceGate(threshold=0.5)
        result = gate.evaluate(0.0)
        self.assertFalse(result.passed)

    def test_passes_at_exact_threshold(self):
        gate = ConfidenceGate(threshold=0.7)
        result = gate.evaluate(0.7)
        self.assertTrue(result.passed)

    def test_custom_threshold(self):
        gate = ConfidenceGate(threshold=0.9)
        self.assertFalse(gate.evaluate(0.89).passed)
        self.assertTrue(gate.evaluate(0.91).passed)

    def test_reason_string_included(self):
        gate = ConfidenceGate(threshold=0.5)
        result = gate.evaluate(0.3)
        self.assertIn("0.300", result.reason)
        self.assertIn("0.500", result.reason)


# ---------------------------------------------------------------------------
# OutputValidator
# ---------------------------------------------------------------------------

class TestOutputValidator(unittest.TestCase):

    def test_valid_answer(self):
        v = OutputValidator()
        result = v.validate(answer="This is a well-formed answer.", error="")
        self.assertTrue(result.valid)

    def test_empty_answer_fails(self):
        v = OutputValidator()
        result = v.validate(answer="", error="")
        self.assertFalse(result.valid)
        self.assertIn("empty", result.reason)

    def test_whitespace_only_fails(self):
        v = OutputValidator()
        result = v.validate(answer="   ", error="")
        self.assertFalse(result.valid)

    def test_too_short_fails(self):
        v = OutputValidator()
        result = v.validate(answer="Short.", error="")
        self.assertFalse(result.valid)
        self.assertIn("short", result.reason)

    def test_error_present_fails(self):
        v = OutputValidator()
        result = v.validate(answer="Answer text here", error="LLM timeout")
        self.assertFalse(result.valid)
        self.assertIn("LLM timeout", result.reason)

    def test_none_answer_fails(self):
        v = OutputValidator()
        result = v.validate(answer=None, error="")  # type: ignore[arg-type]
        self.assertFalse(result.valid)


# ---------------------------------------------------------------------------
# PipelineAuditLogger
# ---------------------------------------------------------------------------

class TestPipelineAuditLogger(unittest.TestCase):

    def _audit_logger(self):
        return PipelineAuditLogger()

    def test_log_run_started(self):
        al = self._audit_logger()
        with patch("tools.audit.audit_logger.log_event", return_value=1) as mock_log:
            entry_id = al.log_run_started(
                design_id="d1", query="What is CMMC?", actor="test"
            )
        self.assertEqual(entry_id, 1)
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(kwargs["event_type"], "pipeline.run_started")
        self.assertIn("d1", kwargs["action"])

    def test_log_confidence_gate_passed(self):
        al = self._audit_logger()
        gate = ConfidenceGateResult(passed=True, confidence=0.8, threshold=0.5, reason="ok")
        with patch("tools.audit.audit_logger.log_event", return_value=2) as mock_log:
            al.log_confidence_gate(design_id="d1", gate=gate, actor="test")
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.confidence_gate_passed")

    def test_log_confidence_gate_failed(self):
        al = self._audit_logger()
        gate = ConfidenceGateResult(passed=False, confidence=0.2, threshold=0.5, reason="low")
        with patch("tools.audit.audit_logger.log_event", return_value=3) as mock_log:
            al.log_confidence_gate(design_id="d1", gate=gate, actor="test")
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.confidence_gate_failed")

    def test_log_output_validated(self):
        al = self._audit_logger()
        v = OutputValidationResult(valid=True, reason="ok")
        with patch("tools.audit.audit_logger.log_event", return_value=4) as mock_log:
            al.log_output_validation(design_id="d1", validation=v, actor="test")
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.output_validated")

    def test_log_output_rejected(self):
        al = self._audit_logger()
        v = OutputValidationResult(valid=False, reason="empty")
        with patch("tools.audit.audit_logger.log_event", return_value=5) as mock_log:
            al.log_output_validation(design_id="d1", validation=v, actor="test")
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.output_rejected")

    def test_log_run_completed(self):
        al = self._audit_logger()
        with patch("tools.audit.audit_logger.log_event", return_value=6) as mock_log:
            entry_id = al.log_run_completed(
                design_id="d1",
                query="test query",
                chunks_used=3,
                model="claude-sonnet-4-6",
                confidence=0.85,
                confidence_gate_passed=True,
                output_valid=True,
                actor="test",
            )
        self.assertEqual(entry_id, 6)
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.run_completed")
        self.assertIn("chunks_used", kwargs["details"])

    def test_log_run_failed(self):
        al = self._audit_logger()
        with patch("tools.audit.audit_logger.log_event", return_value=7) as mock_log:
            al.log_run_failed(
                design_id="d1", query="q", error="RuntimeError: timeout", actor="test"
            )
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.run_failed")
        self.assertIn("timeout", kwargs["details"]["error"])

    def test_write_failure_is_nonfatal(self):
        al = self._audit_logger()
        with patch("tools.audit.audit_logger.log_event", side_effect=Exception("DB down")):
            entry_id = al.log_run_started(design_id="d1", query="q", actor="test")
        self.assertEqual(entry_id, -1)


# ---------------------------------------------------------------------------
# GovernanceLayer
# ---------------------------------------------------------------------------

class TestGovernanceLayer(unittest.TestCase):

    def _gov(self, threshold=0.5):
        return GovernanceLayer(
            confidence_threshold=threshold,
            design_id="design-42",
            actor="test-actor",
            session_id="sess-1",
        )

    def test_evaluate_successful_run(self):
        gov = self._gov()
        with patch("tools.audit.audit_logger.log_event", return_value=10):
            result = gov.evaluate(
                query="What is FedRAMP?",
                answer="FedRAMP is a government-wide cloud security standard.",
                confidence=0.85,
                chunks_used=3,
                model="claude-sonnet-4-6",
                error="",
            )
        self.assertIsInstance(result, GovernanceResult)
        self.assertTrue(result.confidence_gate.passed)
        self.assertTrue(result.output_validation.valid)
        self.assertEqual(result.audit_entry_id, 10)

    def test_evaluate_low_confidence(self):
        gov = self._gov(threshold=0.7)
        with patch("tools.audit.audit_logger.log_event", return_value=11):
            result = gov.evaluate(
                query="q",
                answer="A sufficiently long answer for validation.",
                confidence=0.4,
                chunks_used=1,
                model="mock-llm",
                error="",
            )
        self.assertFalse(result.confidence_gate.passed)
        self.assertTrue(result.output_validation.valid)

    def test_evaluate_empty_answer(self):
        gov = self._gov()
        with patch("tools.audit.audit_logger.log_event", return_value=12):
            result = gov.evaluate(
                query="q",
                answer="",
                confidence=0.9,
                chunks_used=2,
                model="mock-llm",
                error="",
            )
        self.assertTrue(result.confidence_gate.passed)
        self.assertFalse(result.output_validation.valid)

    def test_evaluate_with_synthesis_error(self):
        gov = self._gov()
        with patch("tools.audit.audit_logger.log_event", return_value=13):
            result = gov.evaluate(
                query="q",
                answer="Some text",
                confidence=0.0,
                chunks_used=0,
                model="",
                error="LLM unavailable",
            )
        self.assertFalse(result.confidence_gate.passed)
        self.assertFalse(result.output_validation.valid)

    def test_log_started_calls_audit(self):
        gov = self._gov()
        with patch("tools.audit.audit_logger.log_event") as mock_log:
            gov.log_started("test query")
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.run_started")

    def test_log_failed_calls_audit(self):
        gov = self._gov()
        with patch("tools.audit.audit_logger.log_event") as mock_log:
            gov.log_failed("test query", "RuntimeError: bang")
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "pipeline.run_failed")

    def test_evaluate_writes_four_audit_events(self):
        gov = self._gov()
        with patch("tools.audit.audit_logger.log_event", return_value=1) as mock_log:
            gov.log_started("q")
            gov.evaluate(
                query="q",
                answer="A valid and sufficiently long answer.",
                confidence=0.8,
                chunks_used=2,
                model="llm",
                error="",
            )
        # started + confidence_gate + output_validation + run_completed = 4
        self.assertEqual(mock_log.call_count, 4)


# ---------------------------------------------------------------------------
# Integration: AgenticResearchPipeline includes governance fields
# ---------------------------------------------------------------------------

class TestPipelineGovernanceIntegration(unittest.TestCase):

    def _mock_pipeline(self, design_id="d-test", confidence=0.8, answer="Answer is here."):
        from tools.agentic_ai_canvas.model_layer import (
            AgenticResearchPipeline,
            EmbedResult,
            RankedChunk,
        )

        pipeline = AgenticResearchPipeline(top_k=3, design_id=design_id)

        pipeline.embedder.embed = MagicMock(
            return_value=EmbedResult(query="q", vector=[0.1], model="mock-embed")
        )
        pipeline.reranker.rerank = MagicMock(
            return_value=[RankedChunk(content="Relevant fact.", score=0.9)]
        )
        mock_synth = MagicMock()
        mock_synth.answer = answer
        mock_synth.chunks_used = 1
        mock_synth.model = "mock-llm"
        mock_synth.confidence = confidence
        mock_synth.error = ""
        pipeline.synthesis_llm.synthesize = MagicMock(return_value=mock_synth)
        return pipeline

    def test_pipeline_result_has_governance_fields(self):
        pipeline = self._mock_pipeline(confidence=0.8, answer="A well-formed long answer here.")
        with patch("tools.audit.audit_logger.log_event", return_value=99):
            result = pipeline.run("What is NIST?", chunks=["context"])
        self.assertTrue(result.confidence_gate_passed)
        self.assertTrue(result.output_valid)
        self.assertEqual(result.audit_entry_id, 99)

    def test_pipeline_low_confidence_fails_gate(self):
        pipeline = self._mock_pipeline(confidence=0.1, answer="A well-formed long answer.")
        with patch("tools.audit.audit_logger.log_event", return_value=100):
            result = pipeline.run("query", chunks=["ctx"])
        self.assertFalse(result.confidence_gate_passed)
        self.assertTrue(result.output_valid)

    def test_pipeline_logs_started_on_run(self):
        pipeline = self._mock_pipeline()
        with patch("tools.audit.audit_logger.log_event", return_value=1) as mock_log:
            pipeline.run("test query", chunks=[])
        event_types = [c.kwargs["event_type"] for c in mock_log.call_args_list]
        self.assertIn("pipeline.run_started", event_types)
        self.assertIn("pipeline.run_completed", event_types)

    def test_pipeline_logs_failure_on_exception(self):
        from tools.agentic_ai_canvas.model_layer import AgenticResearchPipeline
        pipeline = AgenticResearchPipeline(top_k=3, design_id="d-fail")
        pipeline.embedder.embed = MagicMock(side_effect=RuntimeError("embed crash"))

        with patch("tools.audit.audit_logger.log_event") as mock_log:
            with self.assertRaises(RuntimeError):
                pipeline.run("query", chunks=["ctx"])

        event_types = [c.kwargs["event_type"] for c in mock_log.call_args_list]
        self.assertIn("pipeline.run_started", event_types)
        self.assertIn("pipeline.run_failed", event_types)


if __name__ == "__main__":
    unittest.main()
