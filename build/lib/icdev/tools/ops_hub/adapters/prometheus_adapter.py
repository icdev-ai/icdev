# CUI // SP-CTI
"""OHC — Prometheus Adapter.

Scrapes metrics via Prometheus HTTP API (query/query_range).
Falls back to file-based pushgateway format (.prom files) for air-gap mode.

Local mode: prometheus on localhost:9090 (standard default port)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class PrometheusAdapter(OpsAdapter):
    ADAPTER_NAME = "prometheus"
    ADAPTER_TYPE = "oss"
    DOMAIN = "aiops"

    def __init__(self):
        self._base_url = os.environ.get(
            "PROMETHEUS_URL",
            os.environ.get("OHC_PROMETHEUS_URL", "http://localhost:9090"),
        ).rstrip("/")
        self._prom_files_dir = Path(os.environ.get(
            "OHC_PROM_FILES_DIR",
            str(Path(__file__).resolve().parents[4] / "data" / "prom_metrics"),
        ))

    def available(self) -> bool:
        # Available if Prometheus is reachable OR prom files dir exists
        if self._ping():
            return True
        return self._prom_files_dir.exists() and any(self._prom_files_dir.glob("*.prom"))

    def _ping(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._base_url}/-/healthy", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:  # nosec B310
                return resp.status == 200
        except Exception:
            return False

    def health_check(self) -> AdapterHealth:
        prom_up = self._ping()
        file_mode = self._prom_files_dir.exists()

        if not prom_up and not file_mode:
            return self._stub_health(
                f"Prometheus not reachable at {self._base_url} and no .prom files found"
            )

        _, elapsed_ms = self._timed_probe(self._ping)
        mode = "http" if prom_up else "file"
        return AdapterHealth(
            available=True,
            adapter_name=self.ADAPTER_NAME,
            adapter_type=self.ADAPTER_TYPE,
            domain=self.DOMAIN,
            version="",
            latency_ms=elapsed_ms,
            details={"mode": mode, "url": self._base_url if prom_up else str(self._prom_files_dir)},
        )

    def _http_query(self, query: str) -> list[dict]:
        """Run an instant query against Prometheus HTTP API."""
        url = f"{self._base_url}/api/v1/query?" + urllib.parse.urlencode({"query": query})
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read())
                if data.get("status") == "success":
                    return data.get("data", {}).get("result", [])
        except Exception:
            pass
        return []

    def _http_query_range(self, query: str, start: str, end: str, step: str = "5m") -> list[dict]:
        """Run a range query against Prometheus HTTP API."""
        url = f"{self._base_url}/api/v1/query_range?" + urllib.parse.urlencode({
            "query": query, "start": start, "end": end, "step": step,
        })
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read())
                if data.get("status") == "success":
                    return data.get("data", {}).get("result", [])
        except Exception:
            pass
        return []

    def _file_query(self, metric_name: str) -> list[dict]:
        """Parse .prom files for a metric name (air-gap fallback)."""
        results = []
        for prom_file in self._prom_files_dir.glob("*.prom"):
            try:
                for line in prom_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    if metric_name in line:
                        results.append({"line": line, "source": prom_file.name})
            except Exception:
                pass
        return results

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []
        if self._ping():
            try:
                url = f"{self._base_url}/api/v1/label/__name__/values"
                with urllib.request.urlopen(url, timeout=10) as resp:  # nosec B310
                    data = json.loads(resp.read())
                    metric_names = data.get("data", [])
                    return [AdapterResource(
                        id=name, name=name,
                        resource_type="metric",
                        status="active",
                    ) for name in metric_names[:200]]
            except Exception:
                pass
        # File mode
        names = set()
        for prom_file in self._prom_files_dir.glob("*.prom"):
            try:
                for line in prom_file.read_text(encoding="utf-8").splitlines():
                    if not line.startswith("#") and "{" in line:
                        names.add(line.split("{")[0].strip())
            except Exception:
                pass
        return [AdapterResource(id=n, name=n, resource_type="metric", status="file")
                for n in sorted(names)]

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        if not self.available():
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="Prometheus unavailable")
        if self._ping():
            step = kwargs.get("step", "5m")
            start = kwargs.get("start", "")
            end = kwargs.get("end", "")
            if start and end:
                raw = self._http_query_range(metric_name, start, end, step)
            else:
                raw = self._http_query(metric_name)
            values = []
            for series in raw:
                labels = series.get("metric", {})
                if "values" in series:
                    for ts, val in series["values"]:
                        values.append({"timestamp": ts, "value": val, "labels": labels})
                elif "value" in series:
                    ts, val = series["value"]
                    values.append({"timestamp": ts, "value": val, "labels": labels})
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=values)
        # File fallback
        raw = self._file_query(metric_name)
        values = [{"line": r["line"], "source": r["source"]} for r in raw]
        return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                               values=values, unit="file")

    def push_event(self, event_type: str, payload: dict) -> dict:
        """Push metrics to pushgateway."""
        if not self._ping():
            return {"status": "unavailable"}
        try:
            job = payload.get("job", "ohc")
            metric_name = payload.get("metric_name", "ohc_event_total")
            value = payload.get("value", 1)
            prom_data = f"# TYPE {metric_name} gauge\n{metric_name} {value}\n"
            data = prom_data.encode()
            url = f"{self._base_url.replace(':9090', ':9091')}/metrics/job/{job}"
            req = urllib.request.Request(url, data=data, method="POST",
                                          headers={"Content-Type": "text/plain"})
            with urllib.request.urlopen(req, timeout=5):  # nosec B310
                return {"status": "ok"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
