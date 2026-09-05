# CUI // SP-CTI
"""AIMC topology builder — ML training pipeline nodes (data prep, training, eval, serving).

Artifact parsing order:
  1. MLproject / mlproject          — MLflow entry points and parameters
  2. requirements.txt               — detect ML framework (pytorch, tensorflow, sklearn, xgboost)
  3. config.yaml / train_config.yaml — model hyperparameters and pipeline stages
  4. Default — 7-node ML pipeline with MLflow registry + LocalStack data lake
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)

_FRAMEWORK_MAP = {
    "torch":        "pytorch",
    "pytorch":      "pytorch",
    "tensorflow":   "tensorflow",
    "keras":        "tensorflow",
    "sklearn":      "sklearn",
    "scikit-learn": "sklearn",
    "xgboost":      "xgboost",
    "lightgbm":     "lightgbm",
    "catboost":     "catboost",
    "transformers": "huggingface",
    "diffusers":    "huggingface",
    "sentence_transformers": "huggingface",
    "ollama":       "ollama",
    "llama":        "ollama",
}


def _parse_mlproject(path: Path) -> Optional[dict]:
    """Parse MLproject YAML for entry points and parameters."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        entry_points = list(data.get("entry_points", {}).keys())
        conda_env = data.get("conda_env", "")
        return {"entry_points": entry_points, "conda_env": conda_env}
    except Exception:
        return None


def _detect_framework(requirements_path: Path) -> str:
    """Scan requirements.txt for ML framework."""
    try:
        text = requirements_path.read_text(encoding="utf-8", errors="ignore").lower()
        for pkg, framework in _FRAMEWORK_MAP.items():
            if pkg in text:
                return framework
    except Exception:
        pass
    return "generic"


def _parse_train_config(path: Path) -> Optional[dict]:
    """Parse config.yaml or train_config.yaml for pipeline metadata."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return {
            "model_name": data.get("model_name") or data.get("model") or "",
            "epochs": data.get("epochs") or data.get("max_epochs") or 0,
            "batch_size": data.get("batch_size") or 0,
            "stages": data.get("stages") or [],
        }
    except Exception:
        return None


class AIMCTopologyBuilder(BaseTopologyBuilder):
    canvas = "aimc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        mlproject: Optional[dict] = None
        train_cfg: Optional[dict] = None
        framework = "generic"

        for fname in ("MLproject", "mlproject"):
            p = artifacts_dir / fname
            if p.exists():
                mlproject = _parse_mlproject(p)
                break

        req = artifacts_dir / "requirements.txt"
        if req.exists():
            framework = _detect_framework(req)

        for fname in ("config.yaml", "train_config.yaml", "train_config.yml"):
            p = artifacts_dir / fname
            if p.exists():
                train_cfg = _parse_train_config(p)
                if train_cfg:
                    break

        model_name = (train_cfg or {}).get("model_name", "icdev-model")
        entry_points = (mlproject or {}).get("entry_points", ["train", "eval"])
        stages = (train_cfg or {}).get("stages", ["ingest", "feature", "train", "eval", "register", "serve", "monitor"])

        nodes = [
            NodeSpec(name="data-lake",      node_type="cloud",           meta={"role": "raw-data",     "csp": "aws/s3"}),
            NodeSpec(name="feature-store",  node_type="vpcs",            meta={"role": "feature-store", "tool": "feast"}),
            NodeSpec(name="training-node",  node_type="vpcs",            meta={"role": "training",      "framework": framework, "model": model_name}),
            NodeSpec(name="eval-harness",   node_type="vpcs",            meta={"role": "evaluation",    "entry_points": entry_points}),
            NodeSpec(name="model-registry", node_type="vpcs",            meta={"role": "registry",      "tool": "mlflow"}),
            NodeSpec(name="inference-srv",  node_type="vpcs",            meta={"role": "inference",     "tool": "triton/ollama"}),
            NodeSpec(name="drift-monitor",  node_type="vpcs",            meta={"role": "drift-detection"}),
            NodeSpec(name="ml-fabric",      node_type="ethernet_switch", meta={"role": "ml-pipeline-fabric"}),
        ]

        links = [
            LinkSpec("data-lake",      "ml-fabric"),
            LinkSpec("feature-store",  "ml-fabric"),
            LinkSpec("training-node",  "ml-fabric"),
            LinkSpec("eval-harness",   "ml-fabric"),
            LinkSpec("model-registry", "ml-fabric"),
            LinkSpec("inference-srv",  "ml-fabric"),
            LinkSpec("drift-monitor",  "ml-fabric"),
        ]

        source = "artifact" if (mlproject or train_cfg or framework != "generic") else "default"

        return TopologySpec(
            project_name="icdev-aimc-sim",
            canvas="aimc",
            nodes=nodes,
            links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count",  type="link_count", expected_value=len(links)),
                ProbeSpec(name="mlflow_up",   type="http_get",
                          target_node="model-registry", port=5001, path="/health"),
                ProbeSpec(name="emulator_apply", type="emulator_apply"),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-mlflow-aimc",
                    image="ghcr.io/mlflow/mlflow:latest",
                    ports={5001: 5000},
                    healthcheck_url="http://localhost:5001/health",
                ),
                DockerServiceSpec(
                    name="icdev-floci-aimc",
                    image=EMULATOR_IMAGE,
                    ports={CANVAS_EMULATOR_HOST_PORTS["aimc"]: EMULATOR_CONTAINER_PORT},
                    env={"DEFAULT_REGION": "us-east-1"},
                    healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["aimc"]),
                ),
            ],
            metadata={
                "pipeline_stages": stages,
                "framework": framework,
                "model_name": model_name,
                "source": source,
                "entry_points": entry_points,
            },
        )
