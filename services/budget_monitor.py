# CUI // SP-CTI
"""Budget monitor service — reads budget config, tracks resource usage, calculates variance."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class BudgetLimit:
    resource: str
    limit: float
    unit: str = "units"


@dataclass
class UsageSample:
    resource: str
    used: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class BudgetVariance:
    resource: str
    limit: float
    used: float
    remaining: float
    pct_used: float
    over_budget: bool


class BudgetMonitor:
    """Monitor real-time resource usage against configured budget limits."""

    DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "args" / "budget_config.yaml"

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = Path(config_path or self.DEFAULT_CONFIG_PATH)
        self._limits: list[BudgetLimit] = []
        self._initialized = False

    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Load budget configuration from YAML."""
        if self._config_path.exists():
            with open(self._config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        else:
            cfg = {}

        self._limits = [
            BudgetLimit(
                resource=item["resource"],
                limit=float(item["limit"]),
                unit=item.get("unit", "units"),
            )
            for item in cfg.get("limits", [])
        ]
        self._initialized = True

    # ------------------------------------------------------------------
    def fetch_current_usage(self) -> list[UsageSample]:
        """Collect current usage from environment / metrics API."""
        samples: list[UsageSample] = []
        for bl in self._limits:
            env_key = f"ICDEV_USAGE_{bl.resource.upper()}"
            raw = os.environ.get(env_key)
            if raw is not None:
                try:
                    samples.append(UsageSample(resource=bl.resource, used=float(raw)))
                except ValueError:
                    pass
            else:
                # Zero usage when no metric is available — safe default.
                samples.append(UsageSample(resource=bl.resource, used=0.0))
        return samples

    # ------------------------------------------------------------------
    def calculate_variance(self, samples: Optional[list[UsageSample]] = None) -> list[BudgetVariance]:
        """Return variance of each resource against its configured limit."""
        if not self._initialized:
            self.initialize()
        if samples is None:
            samples = self.fetch_current_usage()

        usage_map = {s.resource: s.used for s in samples}
        variances: list[BudgetVariance] = []
        for bl in self._limits:
            used = usage_map.get(bl.resource, 0.0)
            remaining = bl.limit - used
            pct = (used / bl.limit * 100) if bl.limit > 0 else 0.0
            variances.append(
                BudgetVariance(
                    resource=bl.resource,
                    limit=bl.limit,
                    used=used,
                    remaining=remaining,
                    pct_used=round(pct, 2),
                    over_budget=remaining < 0,
                )
            )
        return variances
