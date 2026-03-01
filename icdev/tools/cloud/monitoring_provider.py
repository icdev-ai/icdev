#!/usr/bin/env python3
# CUI // SP-CTI
"""Monitoring Provider — cloud-agnostic monitoring and logging.

ABC + 6 implementations: CloudWatch, Azure Monitor, Cloud Monitoring, OCI Monitoring, IBM Cloud Monitoring, Local (Prometheus+ELK).
Pattern: tools/llm/provider.py (D66 provider ABC).
Each implementation ~40-60 lines with try/except ImportError.
"""

import json
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("icdev.cloud.monitoring")


class MonitoringProvider(ABC):
    """Abstract base class for monitoring and logging."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""

    @abstractmethod
    def send_metric(self, namespace: str, metric_name: str, value: float,
                    dimensions: Optional[Dict] = None) -> bool:
        """Send a metric data point."""

    @abstractmethod
    def send_log(self, log_group: str, message: str, level: str = "INFO") -> bool:
        """Send a log entry."""

    @abstractmethod
    def query_metrics(self, namespace: str, metric_name: str,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None) -> List[Dict]:
        """Query metric data points within a time range."""

    @abstractmethod
    def create_alarm(self, name: str, namespace: str, metric_name: str,
                     threshold: float, comparison: str = "GreaterThanThreshold") -> bool:
        """Create a metric alarm/alert."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if monitoring provider is available."""


# ============================================================
# AWS CloudWatch
# ============================================================
try:
    import boto3 as _boto3_cw
    _HAS_BOTO3_CW = True
except ImportError:
    _HAS_BOTO3_CW = False


class AWSCloudWatchProvider(MonitoringProvider):
    """AWS CloudWatch Metrics + Logs implementation."""

    def __init__(self, region: str = "us-gov-west-1"):
        self._region = region
        self._cw_client = None
        self._logs_client = None

    @property
    def provider_name(self) -> str:
        return "aws_cloudwatch"

    def _get_cw_client(self):
        if self._cw_client is None and _HAS_BOTO3_CW:
            self._cw_client = _boto3_cw.client("cloudwatch", region_name=self._region)
        return self._cw_client

    def _get_logs_client(self):
        if self._logs_client is None and _HAS_BOTO3_CW:
            self._logs_client = _boto3_cw.client("logs", region_name=self._region)
        return self._logs_client

    def send_metric(self, namespace: str, metric_name: str, value: float,
                    dimensions: Optional[Dict] = None) -> bool:
        client = self._get_cw_client()
        if not client:
            return False
        try:
            dim_list = [{"Name": k, "Value": str(v)} for k, v in (dimensions or {}).items()]
            client.put_metric_data(
                Namespace=namespace,
                MetricData=[{
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "None",
                    "Dimensions": dim_list,
                }],
            )
            return True
        except Exception:
            return False

    def send_log(self, log_group: str, message: str, level: str = "INFO") -> bool:
        client = self._get_logs_client()
        if not client:
            return False
        try:
            stream = f"icdev-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            try:
                client.create_log_group(logGroupName=log_group)
            except Exception:
                pass
            try:
                client.create_log_stream(logGroupName=log_group, logStreamName=stream)
            except Exception:
                pass
            client.put_log_events(
                logGroupName=log_group,
                logStreamName=stream,
                logEvents=[{
                    "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                    "message": f"[{level}] {message}",
                }],
            )
            return True
        except Exception:
            return False

    def query_metrics(self, namespace: str, metric_name: str,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None) -> List[Dict]:
        client = self._get_cw_client()
        if not client:
            return []
        try:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            start = datetime.fromisoformat(start_time) if start_time else now - timedelta(hours=1)
            end = datetime.fromisoformat(end_time) if end_time else now
            resp = client.get_metric_statistics(
                Namespace=namespace, MetricName=metric_name,
                StartTime=start, EndTime=end,
                Period=60, Statistics=["Average", "Maximum", "Minimum"],
            )
            return [{"timestamp": str(d["Timestamp"]), "average": d.get("Average"),
                      "maximum": d.get("Maximum"), "minimum": d.get("Minimum")}
                     for d in resp.get("Datapoints", [])]
        except Exception:
            return []

    def create_alarm(self, name: str, namespace: str, metric_name: str,
                     threshold: float, comparison: str = "GreaterThanThreshold") -> bool:
        client = self._get_cw_client()
        if not client:
            return False
        try:
            client.put_metric_alarm(
                AlarmName=name, Namespace=namespace, MetricName=metric_name,
                ComparisonOperator=comparison, Threshold=threshold,
                EvaluationPeriods=1, Period=300, Statistic="Average",
            )
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        if not _HAS_BOTO3_CW:
            return False
        try:
            client = self._get_cw_client()
            client.list_metrics(Namespace="AWS/EC2", Limit=1)
            return True
        except Exception:
            return False


# ============================================================
# Azure Monitor
# ============================================================
try:
    from azure.monitor.ingestion import LogsIngestionClient
    from azure.identity import DefaultAzureCredential as _AzureCredMon
    _HAS_AZURE_MON = True
except ImportError:
    _HAS_AZURE_MON = False


class AzureMonitorProvider(MonitoringProvider):
    """Azure Monitor implementation."""

    def __init__(self, endpoint: str = "", rule_id: str = "", stream_name: str = ""):
        self._endpoint = endpoint or os.environ.get("AZURE_MONITOR_ENDPOINT", "")
        self._rule_id = rule_id or os.environ.get("AZURE_MONITOR_DCR_ID", "")
        self._stream_name = stream_name or os.environ.get("AZURE_MONITOR_STREAM", "Custom-ICDev")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "azure_monitor"

    def _get_client(self):
        if self._client is None and _HAS_AZURE_MON and self._endpoint:
            credential = _AzureCredMon()
            self._client = LogsIngestionClient(endpoint=self._endpoint, credential=credential)
        return self._client

    def send_metric(self, namespace: str, metric_name: str, value: float,
                    dimensions: Optional[Dict] = None) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            body = [{
                "TimeGenerated": datetime.now(timezone.utc).isoformat(),
                "Namespace": namespace,
                "MetricName": metric_name,
                "Value": value,
                "Dimensions": json.dumps(dimensions or {}),
            }]
            client.upload(rule_id=self._rule_id, stream_name=self._stream_name, logs=body)
            return True
        except Exception:
            return False

    def send_log(self, log_group: str, message: str, level: str = "INFO") -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            body = [{
                "TimeGenerated": datetime.now(timezone.utc).isoformat(),
                "LogGroup": log_group,
                "Level": level,
                "Message": message,
            }]
            client.upload(rule_id=self._rule_id, stream_name=self._stream_name, logs=body)
            return True
        except Exception:
            return False

    def query_metrics(self, namespace: str, metric_name: str,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None) -> List[Dict]:
        # Azure Monitor queries require Log Analytics — simplified stub
        return []

    def create_alarm(self, name: str, namespace: str, metric_name: str,
                     threshold: float, comparison: str = "GreaterThanThreshold") -> bool:
        # Azure Alerts require ARM API — simplified stub
        return False

    def check_availability(self) -> bool:
        return _HAS_AZURE_MON and bool(self._endpoint) and bool(self._rule_id)


# ============================================================
# GCP Cloud Monitoring
# ============================================================
try:
    from google.cloud import monitoring_v3 as _gcp_mon
    _HAS_GCP_MON = True
except ImportError:
    _HAS_GCP_MON = False


class GCPMonitoringProvider(MonitoringProvider):
    """Google Cloud Monitoring implementation."""

    def __init__(self, project_id: str = ""):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "gcp_cloud_monitoring"

    def _get_client(self):
        if self._client is None and _HAS_GCP_MON:
            self._client = _gcp_mon.MetricServiceClient()
        return self._client

    def send_metric(self, namespace: str, metric_name: str, value: float,
                    dimensions: Optional[Dict] = None) -> bool:
        client = self._get_client()
        if not client or not self._project_id:
            return False
        try:
            from google.protobuf import timestamp_pb2
            from google.api import metric_pb2, monitored_resource_pb2
            project_name = f"projects/{self._project_id}"
            series = _gcp_mon.TimeSeries()
            series.metric.type = f"custom.googleapis.com/{namespace}/{metric_name}"
            for k, v in (dimensions or {}).items():
                series.metric.labels[k] = str(v)
            series.resource.type = "global"
            now = datetime.now(timezone.utc)
            interval = _gcp_mon.TimeInterval(
                end_time={"seconds": int(now.timestamp())},
            )
            point = _gcp_mon.Point(interval=interval, value={"double_value": value})
            series.points = [point]
            client.create_time_series(name=project_name, time_series=[series])
            return True
        except Exception:
            return False

    def send_log(self, log_group: str, message: str, level: str = "INFO") -> bool:
        # GCP logging uses a separate client (google.cloud.logging)
        try:
            from google.cloud import logging as _gcp_logging
            client = _gcp_logging.Client(project=self._project_id)
            gcp_logger = client.logger(log_group)
            gcp_logger.log_text(f"[{level}] {message}", severity=level)
            return True
        except Exception:
            return False

    def query_metrics(self, namespace: str, metric_name: str,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None) -> List[Dict]:
        client = self._get_client()
        if not client or not self._project_id:
            return []
        try:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            start = datetime.fromisoformat(start_time) if start_time else now - timedelta(hours=1)
            end_t = datetime.fromisoformat(end_time) if end_time else now
            project_name = f"projects/{self._project_id}"
            interval = _gcp_mon.TimeInterval(
                start_time={"seconds": int(start.timestamp())},
                end_time={"seconds": int(end_t.timestamp())},
            )
            results = client.list_time_series(
                request={
                    "name": project_name,
                    "filter": f'metric.type = "custom.googleapis.com/{namespace}/{metric_name}"',
                    "interval": interval,
                    "view": _gcp_mon.ListTimeSeriesRequest.TimeSeriesView.FULL,
                }
            )
            points = []
            for ts in results:
                for p in ts.points:
                    points.append({
                        "timestamp": str(p.interval.end_time),
                        "value": p.value.double_value,
                    })
            return points
        except Exception:
            return []

    def create_alarm(self, name: str, namespace: str, metric_name: str,
                     threshold: float, comparison: str = "GreaterThanThreshold") -> bool:
        # GCP Alert Policies require the AlertPolicyServiceClient — simplified stub
        return False

    def check_availability(self) -> bool:
        return _HAS_GCP_MON and bool(self._project_id)


# ============================================================
# OCI Monitoring
# ============================================================
try:
    import oci as _oci_mon
    _HAS_OCI_MON = True
except ImportError:
    _HAS_OCI_MON = False


class OCIMonitoringProvider(MonitoringProvider):
    """Oracle Cloud Infrastructure Monitoring implementation."""

    def __init__(self, compartment_id: str = ""):
        self._compartment_id = compartment_id or os.environ.get("OCI_COMPARTMENT_OCID", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "oci_monitoring"

    def _get_client(self):
        if self._client is None and _HAS_OCI_MON:
            try:
                config = _oci_mon.config.from_file()
                self._client = _oci_mon.monitoring.MonitoringClient(config)
            except Exception:
                pass
        return self._client

    def send_metric(self, namespace: str, metric_name: str, value: float,
                    dimensions: Optional[Dict] = None) -> bool:
        client = self._get_client()
        if not client or not self._compartment_id:
            return False
        try:
            now = datetime.now(timezone.utc)
            data_point = _oci_mon.monitoring.models.Datapoint(
                timestamp=now, value=value,
            )
            metric_data = _oci_mon.monitoring.models.MetricDataDetails(
                namespace=namespace, name=metric_name,
                compartment_id=self._compartment_id,
                dimensions=dimensions or {},
                datapoints=[data_point],
            )
            client.post_metric_data(
                post_metric_data_details=_oci_mon.monitoring.models.PostMetricDataDetails(
                    metric_data=[metric_data],
                )
            )
            return True
        except Exception:
            return False

    def send_log(self, log_group: str, message: str, level: str = "INFO") -> bool:
        # OCI Logging requires a separate LoggingManagementClient — simplified stub
        return False

    def query_metrics(self, namespace: str, metric_name: str,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None) -> List[Dict]:
        # OCI metric queries require SummarizeMetricsDataDetails — simplified stub
        return []

    def create_alarm(self, name: str, namespace: str, metric_name: str,
                     threshold: float, comparison: str = "GreaterThanThreshold") -> bool:
        return False

    def check_availability(self) -> bool:
        return _HAS_OCI_MON and bool(self._compartment_id)


# ============================================================
# IBM Cloud Monitoring — Sysdig + Log Analysis (D237)
# ============================================================
class IBMMonitoringProvider(MonitoringProvider):
    """IBM Cloud Monitoring (Sysdig) + Log Analysis implementation (D237).

    Uses urllib.request (stdlib) for REST API — no additional SDK required.
    """

    def __init__(self, api_key: str = "", sysdig_api_key: str = "",
                 region: str = "us-south"):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._sysdig_key = sysdig_api_key or os.environ.get("IBM_SYSDIG_API_KEY", "")
        self._region = region

    @property
    def provider_name(self) -> str:
        return "ibm_cloud_monitoring"

    def send_metric(self, namespace: str, metric_name: str, value: float,
                    dimensions: Optional[Dict] = None) -> bool:
        if not self._sysdig_key:
            return False
        try:
            import json as _json
            import urllib.request
            url = f"https://{self._region}.monitoring.cloud.ibm.com/api/data"
            data = _json.dumps({"metric": metric_name, "value": value,
                                "dimensions": dimensions or {},
                                "timestamp": ""}).encode()
            req = urllib.request.Request(url, data=data, method="POST",
                                        headers={"Authorization": f"Bearer {self._sysdig_key}",
                                                 "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception:
            return False

    def send_log(self, log_group: str, message: str, level: str = "INFO") -> bool:
        if not self._api_key:
            return False
        try:
            import json as _json
            import urllib.request
            url = f"https://api.{self._region}.logging.cloud.ibm.com/logs/ingest"
            payload = {"lines": [{"line": message, "level": level,
                                  "app": log_group, "meta": {}}]}
            data = _json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, method="POST",
                                        headers={"apikey": self._api_key,
                                                 "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception:
            return False

    def query_metrics(self, namespace: str, metric_name: str,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None) -> List[Dict]:
        return []  # Sysdig query API requires complex PromQL — simplified stub

    def create_alarm(self, name: str, namespace: str, metric_name: str,
                     threshold: float, comparison: str = "GreaterThanThreshold") -> bool:
        return False  # Sysdig alerts require complex API — simplified stub

    def check_availability(self) -> bool:
        return bool(self._api_key) or bool(self._sysdig_key)


# ============================================================
# Local Monitoring — file + SQLite (stdlib only, air-gap safe, D224)
# ============================================================
class LocalMonitoringProvider(MonitoringProvider):
    """Local file-based logging + SQLite metrics (stdlib only, air-gap safe)."""

    def __init__(self, data_dir: Optional[str] = None):
        root = Path(__file__).resolve().parent.parent.parent
        self._data_dir = Path(data_dir) if data_dir else root / "data"
        self._log_dir = self._data_dir / "logs" / "monitoring"
        self._db_path = self._data_dir / "monitoring_metrics.db"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create metrics table if not exists."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    dimensions TEXT,
                    recorded_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_local_metrics_ns_name
                ON local_metrics(namespace, metric_name)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_alarms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    namespace TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    threshold REAL NOT NULL,
                    comparison TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Failed to init local metrics DB: %s", e)

    @property
    def provider_name(self) -> str:
        return "local"

    def send_metric(self, namespace: str, metric_name: str, value: float,
                    dimensions: Optional[Dict] = None) -> bool:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT INTO local_metrics (namespace, metric_name, value, dimensions, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (namespace, metric_name, value,
                 json.dumps(dimensions) if dimensions else None,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def send_log(self, log_group: str, message: str, level: str = "INFO") -> bool:
        try:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = self._log_dir / f"{log_group}_{date_str}.log"
            timestamp = datetime.now(timezone.utc).isoformat()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{level}] {message}\n")
            return True
        except Exception:
            return False

    def query_metrics(self, namespace: str, metric_name: str,
                      start_time: Optional[str] = None,
                      end_time: Optional[str] = None) -> List[Dict]:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM local_metrics WHERE namespace = ? AND metric_name = ?"
            params: list = [namespace, metric_name]
            if start_time:
                query += " AND recorded_at >= ?"
                params.append(start_time)
            if end_time:
                query += " AND recorded_at <= ?"
                params.append(end_time)
            query += " ORDER BY recorded_at DESC LIMIT 1000"
            rows = conn.execute(query, params).fetchall()
            conn.close()
            return [{"timestamp": r["recorded_at"], "value": r["value"],
                      "dimensions": r["dimensions"]} for r in rows]
        except Exception:
            return []

    def create_alarm(self, name: str, namespace: str, metric_name: str,
                     threshold: float, comparison: str = "GreaterThanThreshold") -> bool:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT OR REPLACE INTO local_alarms "
                "(name, namespace, metric_name, threshold, comparison, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, namespace, metric_name, threshold, comparison,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        return True  # Local always available
