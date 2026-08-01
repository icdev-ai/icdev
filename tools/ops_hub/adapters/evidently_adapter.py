# CUI // SP-CTI
"""OHC — Evidently AI Adapter.

Data drift detection, model performance monitoring, data quality reports.
Pure Python — runs fully locally, air-gap safe.

Graceful fallback: returns available=False if evidently is not installed.
"""

from __future__ import annotations

import json
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ops_hub.adapters.evidently_adapter")


class EvidentlyAdapter(OpsAdapter):
    ADAPTER_NAME = "evidently"
    ADAPTER_TYPE = "oss"
    DOMAIN = "mlops"

    def available(self) -> bool:
        try:
            import evidently  # noqa: F401
            return True
        except ImportError:
            return False

    def health_check(self) -> AdapterHealth:
        if not self.available():
            return self._stub_health("evidently not installed — pip install evidently")
        try:
            import evidently
            _, elapsed_ms = self._timed_probe(lambda: evidently.__version__)
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                version=evidently.__version__,
                latency_ms=elapsed_ms,
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        """For Evidently, 'resources' are drift check results pulled from OHC DB."""
        if not self.available():
            return []
        try:
            from tools.ops_hub.db.init_db import get_connection
            conn = get_connection()
            query = "SELECT * FROM ohc_data_drift_events WHERE 1=1"
            params: list = []
            dataset_name = kwargs.get("dataset_name", "")
            if dataset_name:
                query += " AND dataset_name=?"
                params.append(dataset_name)
            limit = kwargs.get("limit", 20)
            query += " ORDER BY detected_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            conn.close()
            return [AdapterResource(
                id=str(r["id"]), name=r["dataset_name"],
                resource_type="drift_event",
                status="fail" if not r["passed"] else "pass",
                metadata=dict(r),
            ) for r in rows]
        except Exception:
            return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                               values=[], error="use run_drift_report() instead")

    def run_drift_report(self, reference_data, current_data,
                          column_mapping=None, dataset_name: str = "dataset") -> dict:
        """Run an Evidently data drift report on two pandas DataFrames."""
        if not self.available():
            return {"available": False, "error": "evidently not installed"}
        try:
            from evidently.report import Report
            from evidently.metric_preset import DataDriftPreset

            report = Report(metrics=[DataDriftPreset()])
            report.run(
                reference_data=reference_data,
                current_data=current_data,
                column_mapping=column_mapping,
            )
            result = report.as_dict()
            drift_detected = result.get("metrics", [{}])[0].get("result", {}).get("dataset_drift", False)

            # Persist drift event to OHC DB
            self._persist_drift_event(
                dataset_name=dataset_name,
                drift_type="data_drift",
                drift_score=float(result.get("metrics", [{}])[0].get("result", {}).get(
                    "share_of_drifted_columns", 0.0)),
                passed=not drift_detected,
                details=result,
            )

            return {
                "drift_detected": drift_detected,
                "dataset_name": dataset_name,
                "report": result,
            }
        except Exception as exc:
            return {"error": str(exc), "available": False}

    def run_model_performance_report(self, reference_data, current_data,
                                      column_mapping=None, dataset_name: str = "model") -> dict:
        """Run an Evidently model performance / regression/classification report."""
        if not self.available():
            return {"available": False, "error": "evidently not installed"}
        try:
            from evidently.report import Report
            from evidently.metric_preset import RegressionPreset

            report = Report(metrics=[RegressionPreset()])
            report.run(
                reference_data=reference_data,
                current_data=current_data,
                column_mapping=column_mapping,
            )
            result = report.as_dict()
            return {"dataset_name": dataset_name, "report": result}
        except Exception as exc:
            return {"error": str(exc)}

    def _persist_drift_event(self, dataset_name: str, drift_type: str,
                              drift_score: float, passed: bool, details: dict) -> None:
        """Write drift result to ohc_data_drift_events."""
        try:
            import uuid
            from datetime import datetime, timezone
            from tools.ops_hub.db.init_db import get_connection
            conn = get_connection()
            conn.execute("""
                INSERT INTO ohc_data_drift_events
                    (id, dataset_name, drift_type, drift_score, threshold, passed,
                     details_json, source_adapter, detected_at)
                VALUES (%s, %s, %s, %s, 0.3, %s, %s, 'evidently', %s)
            """, (
                str(uuid.uuid4()), dataset_name, drift_type, drift_score,
                1 if passed else 0,
                json.dumps(details)[:4096],
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "_persist_drift_event: best-effort INSERT into ohc_data_drift_events failed (non-blocking): %s",
                exc,
            )

    def push_event(self, event_type: str, payload: dict) -> dict:
        return {"status": "not_applicable", "note": "Evidently is a reporting tool only"}
