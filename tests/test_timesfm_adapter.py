# CUI // SP-CTI
"""Tests for icdev.tools.forecast.timesfm_adapter.

Covers payload validation, job lifecycle, audit logging, and graceful fallback
when the optional `timesfm` package is not installed.
"""
from __future__ import annotations

import json

import pytest

from icdev.tools.forecast.timesfm_adapter import (
    ForecastPayload,
    create_job,
    forecast,
    get_job,
    health,
    run_job,
    validate_payload,
)


SAMPLE_PAYLOAD = {
    "values": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    "freq": "H",
    "horizon": 4,
    "quantile": 0.9,
    "context": "test run",
    "source": "unit_test",
}


class _TestConn:
    """SQLite test shim that converts runtime PG %s placeholders to ?."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        translated = sql.replace("%s", "?")
        if params is None:
            return self._conn.execute(translated)
        return self._conn.execute(translated, params)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()


@pytest.fixture
def forecast_conn(icdev_db):
    """Yield a SQLite connection translated for runtime PG-style SQL."""
    import sqlite3

    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    yield _TestConn(conn)
    conn.close()


class TestValidatePayload:
    def test_valid_payload(self) -> None:
        p = validate_payload(SAMPLE_PAYLOAD)
        assert isinstance(p, ForecastPayload)
        assert p.values == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert p.freq == "H"
        assert p.horizon == 4
        assert p.quantile == pytest.approx(0.9)

    def test_defaults(self) -> None:
        p = validate_payload({"values": [1, 2, 3, 4, 5]})
        assert p.freq == "H"
        assert p.horizon == 24
        assert p.quantile == pytest.approx(0.5)
        assert p.source == "manual"

    def test_too_few_values(self) -> None:
        with pytest.raises(ValueError, match="at least two numeric"):
            validate_payload({"values": [1.0]})

    def test_non_numeric_value(self) -> None:
        with pytest.raises(ValueError, match=r"values\[2\] is not numeric"):
            validate_payload({"values": [1.0, 2.0, "x"]})

    def test_invalid_freq(self) -> None:
        with pytest.raises(ValueError, match="freq must be one of"):
            validate_payload({"values": [1.0, 2.0, 3.0], "freq": "Z"})

    def test_invalid_horizon(self) -> None:
        with pytest.raises(ValueError, match="horizon must be between"):
            validate_payload({"values": [1.0, 2.0, 3.0], "horizon": 0})

    def test_invalid_quantile(self) -> None:
        with pytest.raises(ValueError, match="quantile must be between"):
            validate_payload({"values": [1.0, 2.0, 3.0], "quantile": 1.5})


class TestHealth:
    def test_health_without_timesfm(self, monkeypatch) -> None:
        monkeypatch.setenv("TIMESFM_MODEL_PATH", "/tmp/mock")
        h = health()
        assert h["has_timesfm"] is False
        assert h["available"] is False
        assert h["model_id"] == "timesfm-2.5-200m"


class TestJobLifecycle:
    def test_create_job(self, forecast_conn) -> None:
        payload = validate_payload(SAMPLE_PAYLOAD)
        job_id = create_job(forecast_conn, payload)
        assert job_id.startswith("fcj-")
        row = forecast_conn.execute("SELECT * FROM forecast_jobs WHERE id=?", (job_id,)).fetchone()
        assert row["status"] == "pending"
        assert row["input_rows"] == 6

    def test_run_job_fails_gracefully_when_model_missing(self, forecast_conn) -> None:
        payload = validate_payload(SAMPLE_PAYLOAD)
        job_id = create_job(forecast_conn, payload)
        forecast_conn.commit()

        with pytest.raises(RuntimeError, match="TimesFM model is not available"):
            run_job(forecast_conn, job_id, payload)

        row = forecast_conn.execute("SELECT * FROM forecast_jobs WHERE id=?", (job_id,)).fetchone()
        assert row["status"] == "failed"
        assert "TimesFM model is not available" in row["error_message"]

        audit = forecast_conn.execute(
            "SELECT COUNT(*) as cnt FROM forecast_audit WHERE job_id=? AND event_type='failed'",
            (job_id,),
        ).fetchone()["cnt"]
        assert audit == 1

    def test_get_job(self, forecast_conn) -> None:
        payload = validate_payload(SAMPLE_PAYLOAD)
        job_id = create_job(forecast_conn, payload)
        forecast_conn.commit()
        found = get_job(forecast_conn, job_id)
        assert found is not None
        assert found["id"] == job_id
        assert get_job(forecast_conn, "missing") is None


class TestForecastEntrypoint:
    def test_forecast_rolls_back_and_raises_without_model(self, forecast_conn) -> None:
        with pytest.raises(RuntimeError, match="TimesFM model is not available"):
            forecast(SAMPLE_PAYLOAD, conn=forecast_conn)
        # No orphan pending job should remain
        pending = forecast_conn.execute(
            "SELECT COUNT(*) as cnt FROM forecast_jobs WHERE status='pending'"
        ).fetchone()["cnt"]
        assert pending == 0


class TestWithMockModel:
    def test_run_job_with_mock_model(self, forecast_conn, monkeypatch) -> None:
        """Patch the lazy model to return a deterministic forecast."""
        payload = validate_payload(SAMPLE_PAYLOAD)

        class FakeModel:
            def forecast(self, inputs, freq):
                import numpy as np

                return np.array([[10.0, 11.0, 12.0, 13.0]])

        import icdev.tools.forecast.timesfm_adapter as adapter

        monkeypatch.setattr(adapter, "_HAS_TIMESFM", True)
        monkeypatch.setattr(adapter, "_HAS_NUMPY", True)
        monkeypatch.setattr(adapter, "_model_instance", FakeModel())

        job_id = create_job(forecast_conn, payload)
        forecast_conn.commit()
        result = run_job(forecast_conn, job_id, payload)
        assert result["status"] == "completed"
        assert result["prediction"]["point"] == [10.0, 11.0, 12.0, 13.0]

        row = get_job(forecast_conn, job_id)
        assert row["status"] == "completed"
        prediction = json.loads(row["prediction"])
        assert prediction["horizon"] == 4
        assert prediction["freq"] == "H"
