#!/usr/bin/env python3
# CUI // SP-CTI
"""TimesFM-backed time-series forecasting adapter for ICDEV™.

Provides a lightweight microservice wrapper around Google Research's open
TimesFM model (Apache-2.0). Designed for air-gapped / regulated deployments:
- Lazy model loading with local-checkpoint path support.
- Optional dependency on `timesfm` / `torch`; graceful degradation if absent.
- All job state in PostgreSQL via `icdev.tools.db.storage.get_connection()`.
- Append-only audit trail via `forecast_audit` table.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_ICDEV_ROOT = _REPO_ROOT if (_REPO_ROOT / "icdev").is_dir() else _REPO_ROOT.parent
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from icdev.tools.db.storage import get_connection


try:
    from icdev.tools.audit.audit_logger import log_event as _audit_log_event
except Exception:  # pragma: no cover
    def _audit_log_event(**kwargs):
        return -1


# ---------------------------------------------------------------------------
# Optional TimesFM import
# ---------------------------------------------------------------------------
try:
    import timesfm  # type: ignore[import]

    _HAS_TIMESFM = True
except Exception:
    timesfm = None  # type: ignore[assignment]
    _HAS_TIMESFM = False

try:
    import numpy as np  # type: ignore[import]

    _HAS_NUMPY = True
except Exception:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MODEL_ID = "timesfm-2.5-200m"
DEFAULT_HORIZON = 24
DEFAULT_QUANTILE = 0.5
VALID_FREQUENCIES = ("H", "T", "S", "D", "W", "M", "Y", "Q")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _model_path() -> str:
    """Return local TimesFM checkpoint directory, or empty string to use
    HuggingFace cache / default loading path."""
    return _get_env("TIMESFM_MODEL_PATH", "")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------
@dataclass
class ForecastPayload:
    values: list[float]
    freq: str
    horizon: int
    quantile: float | None = None
    context: str = ""
    source: str = "manual"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_payload(payload: dict[str, Any]) -> ForecastPayload:
    """Validate and normalize a forecast request payload.

    Raises ValueError with a clear message on bad input.
    """
    values = payload.get("values")
    if not isinstance(values, list) or len(values) < 2:
        raise ValueError("values must be a list with at least two numeric entries")
    parsed: list[float] = []
    for i, v in enumerate(values):
        try:
            parsed.append(float(v))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"values[{i}] is not numeric: {v}") from exc

    freq = str(payload.get("freq", "H")).upper()
    if freq not in VALID_FREQUENCIES:
        raise ValueError(
            f"freq must be one of {VALID_FREQUENCIES}; got {freq}"
        )

    horizon = int(payload.get("horizon", DEFAULT_HORIZON))
    if horizon < 1 or horizon > 512:
        raise ValueError("horizon must be between 1 and 512")

    quantile = payload.get("quantile", DEFAULT_QUANTILE)
    if quantile is not None:
        quantile = float(quantile)
        if not 0.0 < quantile < 1.0:
            raise ValueError("quantile must be between 0 and 1")

    context = str(payload.get("context", "") or "")[:500]
    source = str(payload.get("source", "manual") or "manual")[:100]

    return ForecastPayload(
        values=parsed,
        freq=freq,
        horizon=horizon,
        quantile=quantile,
        context=context,
        source=source,
    )


# ---------------------------------------------------------------------------
# Lazy model loader
# ---------------------------------------------------------------------------
_model_instance: Any | None = None


def load_model(model_id: str = DEFAULT_MODEL_ID) -> Any:
    """Lazy-load TimesFM model. Caches per process.

    Returns None if `timesfm` is not installed or checkpoint not found.
    """
    global _model_instance
    if _model_instance is not None:
        return _model_instance
    if not _HAS_TIMESFM:
        return None

    try:
        path = _model_path()
        # TimesFM 2.5 convention: instantiate with checkpoint path, then load.
        backend = _get_env("TIMESFM_BACKEND", "pytorch")
        tfm = timesfm.TimesFm(
            hparams=timesfm.TimesFmHparams(
                backend=backend,
                per_core_batch_size=1,
                horizon_len=DEFAULT_HORIZON,
            ),
            checkpoint_path=path or None,
        )
        _model_instance = tfm
        return tfm
    except Exception:
        return None


def health() -> dict[str, Any]:
    """Return adapter health and availability."""
    model = load_model()
    available = _HAS_TIMESFM and _HAS_NUMPY and model is not None
    return {
        "available": available,
        "has_timesfm": _HAS_TIMESFM,
        "has_numpy": _HAS_NUMPY,
        "model_id": DEFAULT_MODEL_ID,
        "model_path": _model_path() or "default/huggingface",
        "model_loaded": model is not None,
    }


# ---------------------------------------------------------------------------
# Forecast inference
# ---------------------------------------------------------------------------
def run_forecast(payload: ForecastPayload, model_id: str = DEFAULT_MODEL_ID) -> dict[str, Any]:
    """Run a single forecast against the loaded TimesFM model.

    Returns a dict with point forecast and optional quantile bounds.
    """
    model = load_model(model_id)
    if model is None:
        raise RuntimeError(
            "TimesFM model is not available. Install 'timesfm' and set "
            "TIMESFM_MODEL_PATH or ensure HuggingFace cache is populated."
        )

    if not _HAS_NUMPY:
        raise RuntimeError("numpy is required for forecast conversion")

    inputs = [np.array(payload.values, dtype=np.float32)]
    forecast_input = {
        "inputs": inputs,
        "freq": [payload.freq],
    }

    try:
        # TimesFM API shape: returns (batch, horizon) point forecasts.
        # Some versions accept quantiles via kwargs; we use point output
        # and optionally compute simple bounds.
        point_forecast = model.forecast(**forecast_input)
        if hasattr(point_forecast, "tolist"):
            point = point_forecast[0].tolist()
        else:
            point = list(point_forecast[0])
    except Exception as exc:
        raise RuntimeError(f"TimesFM inference failed: {exc}") from exc

    horizon = payload.horizon
    point = point[:horizon]
    # Pad if model returns shorter horizon
    while len(point) < horizon:
        point.append(point[-1] if point else 0.0)

    lower: list[float] | None = None
    upper: list[float] | None = None
    if payload.quantile is not None:
        # Approximate quantile bounds using point ± scaled MAD when available.
        # In production, use TimesFM's native quantile outputs if supported.
        residuals = [abs(payload.values[i] - payload.values[i - 1]) for i in range(1, len(payload.values))]
        scale = max(float(np.mean(residuals)) if residuals else 0.0, 1e-6)
        # Use simple normal-ish factor for the requested quantile.
        factor = float(np.sqrt(2.0)) * _probit(payload.quantile)
        margin = [scale * factor * ((i + 1) ** 0.5) for i in range(horizon)]
        lower = [max(p - m, 0.0) for p, m in zip(point, margin)]
        upper = [p + m for p, m in zip(point, margin)]

    return {
        "horizon": horizon,
        "freq": payload.freq,
        "point": point,
        "lower": lower,
        "upper": upper,
        "model_id": model_id,
        "quantile": payload.quantile,
    }


def _probit(q: float) -> float:
    """Rough inverse CDF factor for quantile bounds (placeholder).

    Uses a simple approximation suitable for non-critical visual bounds.
    """
    if not _HAS_NUMPY:
        return 1.0
    try:
        return float(np.sqrt(2.0) * _erfinv(2.0 * q - 1.0))
    except Exception:
        return 1.0


def _erfinv(x: float) -> float:
    """Approximation of inverse error function."""
    # Abramowitz & Stegun approximation
    a = 0.147
    sign = 1 if x >= 0 else -1
    x = abs(x)
    ln = np.log(1.0 - x * x)
    term1 = 2.0 / (np.pi * a) + ln / 2.0
    term2 = ln / a
    return sign * np.sqrt(np.sqrt(term1 * term1 - term2) - term1)


# ---------------------------------------------------------------------------
# Job lifecycle + audit
# ---------------------------------------------------------------------------
def _input_summary(payload: ForecastPayload) -> dict[str, Any]:
    arr = np.array(payload.values, dtype=np.float32) if _HAS_NUMPY else payload.values
    if _HAS_NUMPY:
        return {
            "count": int(len(payload.values)),
            "mean": round(float(np.mean(arr)), 6),
            "std": round(float(np.std(arr)), 6),
            "min": round(float(np.min(arr)), 6),
            "max": round(float(np.max(arr)), 6),
        }
    return {
        "count": len(payload.values),
        "mean": sum(payload.values) / len(payload.values),
        "min": min(payload.values),
        "max": max(payload.values),
    }


def create_job(conn, payload: ForecastPayload, model_id: str = DEFAULT_MODEL_ID) -> str:
    """Create a pending forecast job and audit record."""
    job_id = f"fcj-{uuid.uuid4().hex[:16]}"
    summary = _input_summary(payload)
    now = _now()
    conn.execute(
        """
        INSERT INTO forecast_jobs
          (id, source, context, input_rows, input_summary, status,
           model_id, created_at, updated_at, classification)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            job_id,
            payload.source,
            payload.context,
            len(payload.values),
            json.dumps(summary),
            "pending",
            model_id,
            now,
            now,
            "CUI",
        ),
    )
    _write_audit(conn, job_id, "created", {"input_summary": summary})
    return job_id


def _write_audit(conn, job_id: str, event_type: str, details: dict[str, Any]) -> None:
    audit_id = f"fca-{uuid.uuid4().hex[:16]}"
    conn.execute(
        """
        INSERT INTO forecast_audit
          (id, job_id, event_type, actor, details, created_at, classification)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            audit_id,
            job_id,
            event_type,
            "timesfm_adapter",
            json.dumps(details),
            _now(),
            "CUI",
        ),
    )
    try:
        _audit_log_event(
            event_type="forecast.audit",
            actor="timesfm_adapter",
            action=f"{event_type} job {job_id}",
            details=json.dumps(details),
            project_id="forecast",
        )
    except Exception:
        pass


def run_job(conn, job_id: str, payload: ForecastPayload, model_id: str = DEFAULT_MODEL_ID) -> dict[str, Any]:
    """Execute a forecast job and update job state."""
    conn.execute(
        "UPDATE forecast_jobs SET status=%s, updated_at=%s WHERE id=%s",
        ("running", _now(), job_id),
    )
    _write_audit(conn, job_id, "started", {"model_id": model_id})

    try:
        prediction = run_forecast(payload, model_id)
        completed_at = _now()
        conn.execute(
            """
            UPDATE forecast_jobs
               SET status=%s, prediction=%s, completed_at=%s, updated_at=%s
             WHERE id=%s
            """,
            ("completed", json.dumps(prediction), completed_at, completed_at, job_id),
        )
        _write_audit(conn, job_id, "completed", {"prediction": prediction})
        return {"job_id": job_id, "status": "completed", "prediction": prediction}
    except Exception as exc:
        error_message = str(exc)
        conn.execute(
            "UPDATE forecast_jobs SET status=%s, error_message=%s, updated_at=%s WHERE id=%s",
            ("failed", error_message, _now(), job_id),
        )
        _write_audit(conn, job_id, "failed", {"error": error_message})
        raise


def get_job(conn, job_id: str) -> dict[str, Any] | None:
    """Fetch a forecast job by ID."""
    row = conn.execute("SELECT * FROM forecast_jobs WHERE id=%s", (job_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def forecast(payload: dict[str, Any], conn=None, model_id: str = DEFAULT_MODEL_ID) -> dict[str, Any]:
    """High-level entrypoint: validate, create job, run, return result.

    If conn is None, opens/closes a connection automatically.
    """
    validated = validate_payload(payload)
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        job_id = create_job(conn, validated, model_id)
        conn.commit()
        result = run_job(conn, job_id, validated, model_id)
        conn.commit()
        return result
    except Exception:
        if close_conn:
            conn.rollback()
        raise
    finally:
        if close_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main() -> int:
    parser = argparse.ArgumentParser(description="TimesFM Forecast Adapter")
    parser.add_argument("--health", action="store_true", help="print health JSON")
    parser.add_argument("--forecast", metavar="PAYLOAD_JSON", help="run forecast from JSON string")
    parser.add_argument("--json", action="store_true", help="emit JSON output")
    args = parser.parse_args()

    if args.health:
        print(json.dumps(health(), indent=2))
        return 0

    if args.forecast:
        try:
            payload = json.loads(args.forecast)
            result = forecast(payload)
            print(json.dumps(result, indent=2))
            return 0
        except Exception as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 1

    print(json.dumps(health(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
