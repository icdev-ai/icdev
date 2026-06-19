# [TEMPLATE: CUI // SP-CTI]
"""Provider Health Tracker — adaptive circuit breaker for LLM providers.

Automatically degrades unhealthy providers (rate-limit, token exhaustion,
network failure) and auto-recovers them via periodic probes.  Load-shares
across healthy providers by reordering the fallback chain.

Usage (inside LLMRouter):
    from tools.llm.provider_health import ProviderHealthTracker
    tracker = ProviderHealthTracker()
    tracker.record_failure("ollama_cloud", exc)   # may auto-degrade
    tracker.record_success("featherless")          # contributes to recovery
    chain = tracker.reorder_chain(original_chain, router)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from tools.llm.router import LLMRouter

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.provider_health")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
HEALTH_STATE_PATH = BASE_DIR / "data" / "llm_provider_health.json"


class ProviderState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECOVERING = "recovering"


@dataclass
class ProviderHealthRecord:
    """Rolling-window health metrics for a single provider."""

    provider: str
    state: ProviderState = ProviderState.HEALTHY
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_probe_at: float = 0.0
    # Window tracking: list of (timestamp, success_bool) tuples, pruned on read
    _window: List[tuple] = field(default_factory=list, repr=False)
    # Parsed from rate-limit error — probe no sooner than this
    resume_at: float = 0.0

    def failure_rate(self, window_seconds: float) -> float:
        """Failure rate over the last *window_seconds*."""
        cutoff = time.time() - window_seconds
        recent = [e for e in self._window if e[0] >= cutoff]
        self._window = recent  # prune
        if not recent:
            return 0.0
        failures = sum(1 for _, ok in recent if not ok)
        return failures / len(recent)

    def record(self, success: bool) -> None:
        now = time.time()
        self._window.append((now, success))
        if success:
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            self.last_success_at = now
        else:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            self.last_failure_at = now

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "consecutive_successes": self.consecutive_successes,
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_probe_at": self.last_probe_at,
            "resume_at": self.resume_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProviderHealthRecord":
        rec = cls(provider=data.get("provider", "unknown"))
        rec.state = ProviderState(data.get("state", "healthy"))
        rec.consecutive_successes = data.get("consecutive_successes", 0)
        rec.consecutive_failures = data.get("consecutive_failures", 0)
        rec.last_success_at = data.get("last_success_at", 0.0)
        rec.last_failure_at = data.get("last_failure_at", 0.0)
        rec.last_probe_at = data.get("last_probe_at", 0.0)
        rec.resume_at = data.get("resume_at", 0.0)
        return rec


class ProviderHealthTracker:
    """Thread-safe provider health monitor with circuit-breaker semantics.

    Singleton-like shared state across all LLMRouter instances in the
    process.  Persists to JSON for cross-process awareness.
    """

    _instance: Optional["ProviderHealthTracker"] = None
    _lock: Lock = Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "ProviderHealthTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._records: Dict[str, ProviderHealthRecord] = {}
        self._config: Dict[str, Any] = {}
        self._state_lock = Lock()
        self._load_state()
        self._load_config()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        """Read provider_health section from llm_config.yaml."""
        try:
            import yaml

            cfg_path = BASE_DIR / "args" / "llm_config.yaml"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                self._config = cfg.get("provider_health", {})
        except Exception as exc:
            logger.debug("ProviderHealth config load failed: %s", exc)
            self._config = {}

    def _cfg(self, key: str, default: Any) -> Any:
        return self._config.get(key, default)

    @property
    def enabled(self) -> bool:
        return bool(self._cfg("enabled", True))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        try:
            if HEALTH_STATE_PATH.exists():
                with open(HEALTH_STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, rec_data in data.get("providers", {}).items():
                    self._records[name] = ProviderHealthRecord.from_dict(rec_data)
                logger.info(
                    "Loaded provider health state: %d providers",
                    len(self._records),
                )
        except Exception:
            pass  # best-effort

    def _persist_state(self) -> None:
        try:
            HEALTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "providers": {
                    name: rec.to_dict()
                    for name, rec in self._records.items()
                },
                "persisted_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(HEALTH_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass  # best-effort

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def _get_record(self, provider: str) -> ProviderHealthRecord:
        if provider not in self._records:
            self._records[provider] = ProviderHealthRecord(provider=provider)
        return self._records[provider]

    def record_success(self, provider: str) -> None:
        """Call after a successful provider.invoke()."""
        if not self.enabled:
            return
        with self._state_lock:
            rec = self._get_record(provider)
            rec.record(success=True)
            old_state = rec.state

            if rec.state == ProviderState.RECOVERING:
                threshold = self._cfg("recovery_after_consecutive_successes", 3)
                if rec.consecutive_successes >= threshold:
                    rec.state = ProviderState.HEALTHY
                    logger.info(
                        "Provider %s RECOVERED (%d consecutive successes)",
                        provider,
                        rec.consecutive_successes,
                    )
                    self._persist_state()
            elif rec.state == ProviderState.DEGRADED:
                # A success while degraded means we can try probing
                rec.state = ProviderState.RECOVERING
                logger.info(
                    "Provider %s entering RECOVERY after success", provider
                )
                self._persist_state()
            # HEALTHY → no state change needed

            if old_state != rec.state:
                logger.debug(
                    "Provider %s state: %s → %s",
                    provider,
                    old_state.value,
                    rec.state.value,
                )

    def record_failure(
        self,
        provider: str,
        exc: Optional[Exception] = None,
        is_rate_limit: bool = False,
        resume_at: float = 0.0,
    ) -> None:
        """Call after a failed provider.invoke(). May auto-degrade."""
        if not self.enabled:
            return
        with self._state_lock:
            rec = self._get_record(provider)
            rec.record(success=False)
            old_state = rec.state

            if resume_at:
                rec.resume_at = resume_at

            # Check degrade triggers
            should_degrade = False
            if is_rate_limit:
                should_degrade = True
                logger.warning(
                    "Provider %s DEGRADED (rate-limit detected)", provider
                )
            else:
                consec_threshold = self._cfg(
                    "degrade_after_consecutive_failures", 3
                )
                if rec.consecutive_failures >= consec_threshold:
                    should_degrade = True
                    logger.warning(
                        "Provider %s DEGRADED (%d consecutive failures)",
                        provider,
                        rec.consecutive_failures,
                    )
                else:
                    window = self._cfg("degrade_window_seconds", 300)
                    rate_threshold = self._cfg("degrade_after_failure_rate", 0.5)
                    if rec.failure_rate(window) >= rate_threshold:
                        should_degrade = True
                        logger.warning(
                            "Provider %s DEGRADED (failure rate %.0f%% in %.0fs)",
                            provider,
                            rec.failure_rate(window) * 100,
                            window,
                        )

            if should_degrade and rec.state != ProviderState.DEGRADED:
                rec.state = ProviderState.DEGRADED
                rec.consecutive_successes = 0
                self._persist_state()
                logger.info(
                    "Provider %s state: %s → degraded",
                    provider,
                    old_state.value,
                )

    def get_state(self, provider: str) -> ProviderState:
        """Return current state of a provider."""
        if not self.enabled:
            return ProviderState.HEALTHY
        rec = self._records.get(provider)
        return rec.state if rec else ProviderState.HEALTHY

    def is_healthy(self, provider: str) -> bool:
        return self.get_state(provider) == ProviderState.HEALTHY

    def is_degraded(self, provider: str) -> bool:
        return self.get_state(provider) == ProviderState.DEGRADED

    def should_probe(self, provider: str) -> bool:
        """True if enough time has passed to probe a degraded provider."""
        rec = self._records.get(provider)
        if not rec or rec.state != ProviderState.DEGRADED:
            return False
        now = time.time()
        if rec.resume_at and now < rec.resume_at:
            return False
        interval = self._cfg("probe_interval_seconds", 300)
        return (now - rec.last_probe_at) >= interval

    def mark_probed(self, provider: str, success: bool) -> None:
        """Record the result of a recovery probe."""
        with self._state_lock:
            rec = self._get_record(provider)
            rec.last_probe_at = time.time()
            if success:
                rec.record(success=True)
                threshold = self._cfg("recovery_after_consecutive_successes", 3)
                if rec.consecutive_successes >= threshold:
                    rec.state = ProviderState.HEALTHY
                    logger.info("Provider %s RECOVERED after probe", provider)
                    self._persist_state()
                elif rec.state != ProviderState.RECOVERING:
                    rec.state = ProviderState.RECOVERING
                    self._persist_state()
            else:
                rec.record(success=False)
                self._persist_state()

    # ------------------------------------------------------------------
    # Chain reordering
    # ------------------------------------------------------------------
    def reorder_chain(
        self,
        chain: List[str],
        router: "LLMRouter",
    ) -> List[str]:
        """Return chain with models from degraded providers moved to end.

        HEALTHY providers first (original order preserved),
        then RECOVERING providers,
        then DEGRADED providers.
        """
        if not self.enabled:
            return chain

        healthy: List[str] = []
        recovering: List[str] = []
        degraded: List[str] = []

        for model_name in chain:
            model_cfg = router._get_model_config(model_name)
            if not model_cfg:
                healthy.append(model_name)
                continue
            provider = model_cfg.get("provider", "")
            state = self.get_state(provider)
            if state == ProviderState.HEALTHY:
                healthy.append(model_name)
            elif state == ProviderState.RECOVERING:
                recovering.append(model_name)
            else:
                degraded.append(model_name)

        result = healthy + recovering + degraded
        if result != chain:
            logger.debug(
                "Chain reordered: %s → %s", chain, result
            )
        return result

    # ------------------------------------------------------------------
    # Load sharing (round-robin across healthy providers)
    # ------------------------------------------------------------------
    def apply_load_sharing(
        self,
        chain: List[str],
        router: "LLMRouter",
    ) -> List[str]:
        """Optionally shuffle healthy models round-robin per provider."""
        if not self.enabled:
            return chain
        ls_cfg = self._config.get("load_sharing", {})
        if not ls_cfg.get("enabled", False):
            return chain

        mode = ls_cfg.get("mode", "round_robin")
        if mode != "round_robin":
            return chain  # weighted / latency are future work

        # Group healthy models by provider, keep degraded as-is at end
        healthy: List[str] = []
        degraded: List[str] = []
        by_provider: Dict[str, List[str]] = {}

        for model_name in chain:
            model_cfg = router._get_model_config(model_name)
            provider = model_cfg.get("provider", "") if model_cfg else ""
            state = self.get_state(provider)
            if state == ProviderState.HEALTHY:
                by_provider.setdefault(provider, []).append(model_name)
                healthy.append(model_name)
            else:
                degraded.append(model_name)

        if len(by_provider) <= 1:
            return chain  # nothing to share

        # Interleave providers round-robin
        providers = list(by_provider.keys())
        interleaved: List[str] = []
        indices = {p: 0 for p in providers}
        total_healthy = sum(len(v) for v in by_provider.values())
        while len(interleaved) < total_healthy:
            for p in providers:
                if indices[p] < len(by_provider[p]):
                    interleaved.append(by_provider[p][indices[p]])
                    indices[p] += 1

        return interleaved + degraded

    # ------------------------------------------------------------------
    # Dashboard / debug API
    # ------------------------------------------------------------------
    def get_all_health(self) -> Dict[str, dict]:
        """Return serializable health state for all tracked providers."""
        return {
            name: rec.to_dict()
            for name, rec in self._records.items()
        }
