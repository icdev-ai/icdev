# CUI // SP-CTI
"""Tests for anomaly-detection helpers in RAG ingestion_manager.

Covers the adaptive threshold calibration (_compute_ingestion_thresholds) that
replaces the previously-hardcoded embedding batch size, plus the module-level
fallback constants.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.rag import ingestion_manager
from tools.rag.ingestion_manager import (
    _compute_ingestion_thresholds,
    _load_anomaly_cfg,
    _embed_chunks,
    _EMBED_BATCH_SIZE,
    _DB_BUSY_TIMEOUT_MS,
    _SKIP_RATE_ANOMALY,
)


def _mock_conn(row_dict):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row_dict
    return conn


# ─────────────────────────────────────────────────────────────────
# Module-level fallback constants
# ─────────────────────────────────────────────────────────────────

class TestIngestionConstants:
    def test_all_sane(self):
        assert _EMBED_BATCH_SIZE > 0
        assert _DB_BUSY_TIMEOUT_MS > 0
        assert 0.0 < _SKIP_RATE_ANOMALY <= 1.0


# ─────────────────────────────────────────────────────────────────
# _compute_ingestion_thresholds
# ─────────────────────────────────────────────────────────────────

class TestComputeIngestionThresholds:

    def test_disabled_returns_fallbacks(self):
        cfg = {"enabled": False,
               "fallback_embed_batch": 32,
               "fallback_skip_rate_anomaly": 0.88}
        result = _compute_ingestion_thresholds(cfg)
        assert result["embed_batch_size"] == 32
        assert result["skip_rate_anomaly"] == 0.88
        assert result["computed"] is False

    def test_none_cfg_returns_defaults(self):
        # No config + DB unavailable → module defaults, never raises.
        with patch("tools.rag.ingestion_manager.get_connection",
                   side_effect=Exception("no db")):
            result = _compute_ingestion_thresholds(None)
        assert result["embed_batch_size"] == _EMBED_BATCH_SIZE
        assert result["skip_rate_anomaly"] == _SKIP_RATE_ANOMALY
        assert result["computed"] is False

    def test_insufficient_history_returns_defaults(self):
        cfg = {"enabled": True, "min_samples": 30}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 50.0, "skip_rate": 0.4, "n": 5})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is False
        assert result["embed_batch_size"] == _EMBED_BATCH_SIZE

    def test_high_throughput_scales_batch_up(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"batch_floor": 5, "batch_ceil": 100}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 75.0, "skip_rate": 0.2, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert result["embed_batch_size"] == 75

    def test_low_throughput_clamped_to_floor(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"batch_floor": 5, "batch_ceil": 100}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 1.0, "skip_rate": 0.1, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert result["embed_batch_size"] == 5  # floor

    def test_batch_clamped_to_ceiling(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"batch_floor": 5, "batch_ceil": 50}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 5000.0, "skip_rate": 0.3, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert result["embed_batch_size"] == 50  # ceil

    def test_skip_rate_anomaly_bounds_respected(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"skip_floor": 0.50, "skip_ceil": 0.99}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 20.0, "skip_rate": 0.99, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert 0.50 <= result["skip_rate_anomaly"] <= 0.99

    def test_db_error_returns_defaults(self):
        cfg = {"enabled": True, "min_samples": 10}
        with patch("tools.rag.ingestion_manager.get_connection",
                   side_effect=Exception("DB error")):
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is False
        assert result["embed_batch_size"] == _EMBED_BATCH_SIZE


# ─────────────────────────────────────────────────────────────────
# _load_anomaly_cfg
# ─────────────────────────────────────────────────────────────────

class TestLoadAnomalyCfg:
    def test_returns_dict(self):
        # Reads the real rag_config.yaml; section is present so should be a dict.
        cfg = _load_anomaly_cfg()
        assert isinstance(cfg, dict)


# ─────────────────────────────────────────────────────────────────
# _embed_chunks — honours adaptive batch size
# ─────────────────────────────────────────────────────────────────

class _StubChunk:
    def __init__(self, content):
        self.content = content
        self.embedding = None

    def text_for_embedding(self):
        """The method _embed_chunks actually calls (rce-ctx-01).

        This stub had only .content, so every chunk raised AttributeError inside
        _embed_chunks, the per-chunk ``except Exception: continue`` ate it, and
        the function returned 0. The two tests below asserted 0 == 5 and 0 == 1
        and had been red on main ever since — invisible because this file has
        never been CI-gated. Both halves are fixed together: the stub here, and
        the silent-total-failure reporting in _embed_chunks (fli-ing-02).
        """
        return self.content


class _StubProvider:
    def __init__(self):
        self.calls = 0

    def embed(self, content):
        self.calls += 1
        return [0.1, 0.2, 0.3]


class TestEmbedChunks:
    def test_no_provider_returns_zero(self):
        assert _embed_chunks([_StubChunk("x")], None) == 0

    def test_empty_chunks_returns_zero(self):
        assert _embed_chunks([], _StubProvider()) == 0

    def test_embeds_all_chunks(self):
        provider = _StubProvider()
        chunks = [_StubChunk(f"c{i}") for i in range(5)]
        embedded = _embed_chunks(chunks, provider, batch_size=2)
        assert embedded == 5
        assert all(c.embedding is not None for c in chunks)

    def test_default_batch_size_used_when_none(self):
        provider = _StubProvider()
        chunks = [_StubChunk("c")]
        # batch_size=None falls back to _EMBED_BATCH_SIZE, still embeds.
        assert _embed_chunks(chunks, provider, batch_size=None) == 1


class _FailingProvider:
    """Raises for every chunk — a provider outage, a wrong model, a renamed method."""

    def __init__(self, exc=None):
        self.calls = 0
        self._exc = exc or RuntimeError("embedding backend unreachable")

    def embed(self, content):
        self.calls += 1
        raise self._exc


class _FlakyProvider:
    """Fails only on a named chunk. One bad chunk is not a broken pipeline."""

    def __init__(self, bad):
        self.bad = bad

    def embed(self, content):
        if content == self.bad:
            raise ValueError(f"cannot embed {content}")
        return [0.1, 0.2, 0.3]


class _LogRecorder:
    """Stands in for the module logger.

    Not caplog: get_logger() sets ``logger.propagate = False`` (icdev_logger.py
    line ~276, so an NDJSON record is not also emitted to root), which means
    caplog observes nothing and every assertion below would pass vacuously —
    the exact fabricated-pass this card exists to stop. Swapping the module's
    logger asserts what the code actually calls, and does not depend on how
    logging happens to be configured when the suite runs.
    """

    def __init__(self):
        self.warnings, self.errors = [], []

    def warning(self, fmt, *args):
        self.warnings.append(fmt % args if args else fmt)

    def error(self, fmt, *args):
        self.errors.append(fmt % args if args else fmt)

    def debug(self, *a, **kw):
        pass

    info = debug


@pytest.fixture()
def logs(monkeypatch):
    rec = _LogRecorder()
    monkeypatch.setattr(ingestion_manager, "logger", rec)
    return rec


class TestEmbedChunksReportsFailures:
    """A TOTAL embedding failure must not read as "nothing to do" (fli-ing-02).

    The old loop discarded the exception entirely — not even its type — so an
    error hitting every chunk returned 0, exactly like an empty input. Both call
    sites then filter to ``[c for c in chunks if c.embedding is not None]``, get
    an empty list, skip the upsert and carry on: the source is ingested with ZERO
    vectors and the run reports success. It is invisible in retrieval too, since
    those chunks simply never come back.
    """

    def test_total_failure_is_logged_as_an_error(self, logs):
        chunks = [_StubChunk(f"c{i}") for i in range(3)]
        assert _embed_chunks(chunks, _FailingProvider()) == 0

        assert logs.errors, (
            "every chunk failed and nothing was logged at ERROR — this is the "
            "state where the pipeline stores nothing and reports success"
        )
        msg = logs.errors[0]
        assert "ALL 3" in msg, f"the message must say how many failed: {msg!r}"
        assert "RuntimeError" in msg and "unreachable" in msg, (
            f"the first exception's type AND message must survive: {msg!r}. "
            "Discarding them is why a one-line AttributeError hid for weeks."
        )

    def test_an_empty_chunk_list_is_NOT_reported_as_a_failure(self, logs):
        """The distinction that matters: 0-because-nothing vs 0-because-broken."""
        assert _embed_chunks([], _FailingProvider()) == 0
        assert not logs.errors and not logs.warnings, (
            "an empty input is not a failure; logging it would train readers to "
            "ignore the message that matters"
        )

    def test_one_bad_chunk_still_embeds_the_rest_and_warns(self, logs):
        """Partial failure stays tolerated — one bad chunk must not abort an ingest."""
        chunks = [_StubChunk(f"c{i}") for i in range(5)]
        embedded = _embed_chunks(chunks, _FlakyProvider(bad="c2"), batch_size=2)

        assert embedded == 4, "the four good chunks must still be embedded"
        assert [c.content for c in chunks if c.embedding is None] == ["c2"]

        assert logs.warnings, "a dropped chunk must still be reported"
        assert "1 of 5" in logs.warnings[0]
        assert not logs.errors, (
            "a partial failure is not a broken pipeline — reserving ERROR for "
            "total failure is what keeps the total case worth reading"
        )

    def test_no_provider_is_silent(self, logs):
        """No provider configured is a deployment choice, not a failure."""
        assert _embed_chunks([_StubChunk("x")], None) == 0
        assert not logs.errors and not logs.warnings
