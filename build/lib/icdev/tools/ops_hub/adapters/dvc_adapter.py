# CUI // SP-CTI
"""OHC — DVC Adapter.

Dataset versioning, pipeline tracking, data lineage via DVC CLI.
Wrapper around subprocess calls to the `dvc` CLI.

Graceful fallback: returns available=False if DVC is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class DVCAdapter(OpsAdapter):
    ADAPTER_NAME = "dvc"
    ADAPTER_TYPE = "oss"
    DOMAIN = "mlops"

    def __init__(self):
        self._repo_path = Path(os.environ.get(
            "OHC_DVC_REPO",
            str(Path(__file__).resolve().parents[4]),
        ))

    def _dvc_bin(self) -> str | None:
        return shutil.which("dvc")

    def available(self) -> bool:
        return self._dvc_bin() is not None

    def health_check(self) -> AdapterHealth:
        dvc = self._dvc_bin()
        if not dvc:
            return self._stub_health("dvc not installed — pip install dvc")
        try:
            result, elapsed_ms = self._timed_probe(
                lambda: subprocess.run(
                    [dvc, "version"],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(self._repo_path),
                )
            )
            if result.returncode != 0:
                return self._stub_health(f"dvc version failed: {result.stderr[:200]}")
            version_line = result.stdout.strip().split("\n")[0]
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                version=version_line,
                latency_ms=elapsed_ms,
                details={"repo": str(self._repo_path)},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def _run(self, args: list[str], timeout: int = 30) -> dict:
        """Run a dvc command and return stdout/stderr as dict."""
        dvc = self._dvc_bin()
        if not dvc:
            return {"error": "dvc not installed", "stdout": "", "stderr": ""}
        try:
            result = subprocess.run(
                [dvc] + args,
                capture_output=True, text=True, timeout=timeout,
                cwd=str(self._repo_path),
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"error": "timeout", "stdout": "", "stderr": ""}
        except Exception as exc:
            return {"error": str(exc), "stdout": "", "stderr": ""}

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []

        if resource_type in ("stages", ""):
            result = self._run(["stage", "list", "--json"])
            if result.get("returncode") == 0:
                try:
                    stages = json.loads(result["stdout"])
                    return [AdapterResource(
                        id=s.get("name", ""), name=s.get("name", ""),
                        resource_type="dvc_stage",
                        status="ok",
                        metadata=s,
                    ) for s in (stages if isinstance(stages, list) else [])]
                except Exception:
                    pass

        if resource_type == "data":
            result = self._run(["data", "status", "--json"])
            if result.get("returncode") == 0:
                try:
                    data = json.loads(result["stdout"])
                    resources = []
                    for item in (data if isinstance(data, list) else []):
                        resources.append(AdapterResource(
                            id=item.get("path", ""), name=item.get("path", ""),
                            resource_type="dvc_dataset",
                            status=item.get("status", "unknown"),
                            metadata=item,
                        ))
                    return resources
                except Exception:
                    pass

        return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        if not self.available():
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="dvc not installed")
        result = self._run(["metrics", "show", "--json"])
        if result.get("returncode") == 0:
            try:
                metrics = json.loads(result["stdout"])
                values = [{"file": k, "metrics": v} for k, v in metrics.items()]
                return AdapterMetrics(adapter_name=self.ADAPTER_NAME,
                                       metric_name="dvc_metrics", values=values)
            except Exception:
                pass
        return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                               values=[], error=result.get("stderr", ""))

    def get_diff(self) -> dict:
        """Show DVC data diff between current and last commit."""
        if not self.available():
            return {"error": "dvc not installed"}
        result = self._run(["diff", "--json"])
        if result.get("returncode") == 0:
            try:
                return json.loads(result["stdout"])
            except Exception:
                pass
        return {"error": result.get("stderr", "no diff output")}

    def push_event(self, event_type: str, payload: dict) -> dict:
        return {"status": "not_applicable", "note": "DVC operates via CLI only"}
