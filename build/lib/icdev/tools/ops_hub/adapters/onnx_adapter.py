# CUI // SP-CTI
"""OHC — ONNX Runtime Adapter.

Load, validate, and inspect ONNX models. Run inference with test tensors.
Air-gap safe — pure Python, pip install onnxruntime.

Graceful fallback: returns available=False if onnxruntime is not installed.
"""

from __future__ import annotations

import os
from pathlib import Path

from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class ONNXAdapter(OpsAdapter):
    ADAPTER_NAME = "onnx"
    ADAPTER_TYPE = "oss"
    DOMAIN = "mlops"

    def __init__(self):
        self._model_dir = Path(os.environ.get(
            "OHC_ONNX_MODEL_DIR",
            str(Path(__file__).resolve().parents[4] / "data" / "onnx_models"),
        ))

    def available(self) -> bool:
        try:
            import onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False

    def health_check(self) -> AdapterHealth:
        if not self.available():
            return self._stub_health("onnxruntime not installed — pip install onnxruntime")
        try:
            import onnxruntime as ort
            providers, elapsed_ms = self._timed_probe(lambda: ort.get_available_providers())
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                version=ort.__version__,
                latency_ms=elapsed_ms,
                details={
                    "providers": providers,
                    "model_dir": str(self._model_dir),
                    "model_count": len(list(self._model_dir.glob("*.onnx"))) if self._model_dir.exists() else 0,
                },
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        """List ONNX model files from the model directory."""
        if not self.available():
            return []
        if not self._model_dir.exists():
            return []
        results = []
        for model_path in sorted(self._model_dir.glob("*.onnx")):
            info = self._inspect_model(str(model_path))
            results.append(AdapterResource(
                id=model_path.stem,
                name=model_path.name,
                resource_type="onnx_model",
                status="valid" if not info.get("error") else "invalid",
                metadata=info,
            ))
        return results

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        """Run inference and return latency metrics for a model."""
        if not self.available():
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="onnxruntime not installed")
        model_path = kwargs.get("model_path", "")
        if not model_path:
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="model_path required")
        try:
            import onnxruntime as ort
            import time
            sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            # Generate random input tensors matching model input shapes
            import numpy as np
            feeds = {}
            for inp in sess.get_inputs():
                shape = [d if isinstance(d, int) and d > 0 else 1 for d in inp.shape]
                feeds[inp.name] = np.random.randn(*shape).astype("float32")
            times = []
            for _ in range(5):
                t0 = time.monotonic()
                sess.run(None, feeds)
                times.append((time.monotonic() - t0) * 1000)
            avg_ms = sum(times) / len(times)
            return AdapterMetrics(
                adapter_name=self.ADAPTER_NAME, metric_name="inference_latency_ms",
                values=[{"avg_ms": round(avg_ms, 2), "runs": 5}], unit="ms",
            )
        except Exception as exc:
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error=str(exc))

    def _inspect_model(self, model_path: str) -> dict:
        """Return model metadata: inputs, outputs, node count, opset."""
        try:
            import onnxruntime as ort
            sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            return {
                "inputs": [
                    {"name": i.name, "shape": i.shape, "type": i.type}
                    for i in sess.get_inputs()
                ],
                "outputs": [
                    {"name": o.name, "shape": o.shape, "type": o.type}
                    for o in sess.get_outputs()
                ],
                "providers": sess.get_providers(),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def push_event(self, event_type: str, payload: dict) -> dict:
        return {"status": "not_applicable", "note": "ONNX Runtime is a local inference engine"}
