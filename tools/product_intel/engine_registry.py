"""Engine registry for Product Intelligence — loads config, invokes sub-engines."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "args" / "product_intel_config.yaml"


@dataclass
class EngineConfig:
    name: str
    module: str
    cli_flags: list[str]
    enabled: bool
    timeout_seconds: int
    order: int


@dataclass
class EngineResult:
    name: str
    status: str  # ok | failed | skipped
    duration_ms: float
    signals_count: int
    output: dict[str, Any] = field(default_factory=dict)


class EngineRegistry:
    def __init__(self, config_path: Path = _CONFIG_PATH) -> None:
        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        self._engines: list[EngineConfig] = [
            EngineConfig(
                name=e["name"],
                module=e["module"],
                cli_flags=e.get("cli_flags", []),
                enabled=e.get("enabled", True),
                timeout_seconds=e.get("timeout_seconds", 120),
                order=e.get("order", 99),
            )
            for e in raw.get("engines", [])
        ]
        self._engines.sort(key=lambda e: e.order)

    def list_engines(self) -> list[EngineConfig]:
        return list(self._engines)

    def get_engine(self, name: str) -> EngineConfig:
        for eng in self._engines:
            if eng.name == name:
                return eng
        raise KeyError(f"Engine '{name}' not found in registry")

    def invoke(self, engine_config: EngineConfig, extra_args: list[str] | None = None) -> EngineResult:
        if not engine_config.enabled:
            return EngineResult(name=engine_config.name, status="skipped", duration_ms=0.0, signals_count=0)

        cmd = [sys.executable, "-m", engine_config.module] + engine_config.cli_flags + (extra_args or [])
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=engine_config.timeout_seconds,
            )
            duration_ms = (time.monotonic() - t0) * 1000

            if proc.returncode != 0:
                return EngineResult(
                    name=engine_config.name,
                    status="failed",
                    duration_ms=duration_ms,
                    signals_count=0,
                    output={"stderr": proc.stderr, "stdout": proc.stdout},
                )

            try:
                parsed: dict[str, Any] = json.loads(proc.stdout)
            except json.JSONDecodeError:
                return EngineResult(
                    name=engine_config.name,
                    status="failed",
                    duration_ms=duration_ms,
                    signals_count=0,
                    output={"parse_error": "non-JSON stdout", "stdout": proc.stdout},
                )

            signals_count = len(parsed.get("signals", parsed.get("results", [])))
            return EngineResult(
                name=engine_config.name,
                status="ok",
                duration_ms=duration_ms,
                signals_count=signals_count,
                output=parsed,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.monotonic() - t0) * 1000
            return EngineResult(
                name=engine_config.name,
                status="failed",
                duration_ms=duration_ms,
                signals_count=0,
                output={"exception": str(exc)},
            )
