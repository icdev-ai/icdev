#!/usr/bin/env python3
# CUI // SP-CTI
"""Scope controls for the agent browser (oss-browse-02).

An LLM that can click inside a platform managing ATO artifacts needs its
guardrails landed *with* the capability, not as a follow-up. This module is
that guardrail layer, and it ships before/with ``agent_browser.py`` so the
capability cannot be written without a seam to enforce.

Four controls, all default-deny:

1. **Domain allowlist** — ``check_navigation()`` refuses every URL that is not
   loopback. A routable host requires three independent switches: it must be
   listed in ``allowed_domains``, ``allow_non_local`` must be true, and
   ``egress_guard`` must clear it (https-only, DNS resolve-then-check against
   private ranges). Non-http(s) schemes (``javascript:``, ``data:``, ``file:``)
   are refused outright, which is what stops ``navigate()`` from doubling as a
   script-injection or local-file-read primitive.
2. **Sensitive-data placeholders** — ``SensitiveDataResolver`` swaps
   ``<secret>name</secret>`` for a real value *at the driver*. The prompt, the
   transcript, and the audit row only ever see the placeholder. Authorization
   comes from ``tools/security/credential_broker.py``; the value itself comes
   from the env var named in config. No new secret path.
3. **Budget** — ``ActionBudget`` caps actions per run, failures per run, and
   wall-clock per step.
4. **Audit** — every action, allowed or denied, writes to ``audit_trail``
   following the ``audit=True`` pattern in ``tools/agent_toolkit/_shell.py``
   (best-effort; an audit failure never fails the call, but a *denial* always
   raises).

``GuardedDriver`` binds all four to a live WebDriver. Actions run through
``run_action()``, which consumes budget, times the step, re-checks the URL
afterwards (so a redirect cannot walk the session out of scope), and audits.

The placeholder-substitution, per-step-cap and failure-cap concepts are adapted
from browser-use (MIT) — ``sensitive_data``, ``max_actions_per_step`` and
``max_failures``. Concept only: this is an independent Selenium implementation
with no runtime dependency on that project.

Usage::

    from tools.browser import get_driver
    from tools.browser.scope import GuardedDriver, load_scope_config

    driver = get_driver(headless=True)
    try:
        session = GuardedDriver(driver, run_id="vv-001")
        session.navigate("http://localhost:5050/")          # allowed
        session.navigate("https://example.com/")            # NavigationDenied
    finally:
        driver.quit()

CLI::

    python tools/browser/scope.py --show --json
    python tools/browser/scope.py --check-url http://localhost:5050/ --json
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.scope")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_ENV_VAR = "ICDEV_BROWSER_SCOPE_CONFIG"
CONFIG_FILENAME = "browser_scope.yaml"

# Loopback only. Everything else is opt-in, per host, per config.
DEFAULT_ALLOWED_DOMAINS: Tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
DEFAULT_ALLOWED_SCHEMES: Tuple[str, ...] = ("http", "https")

# Driver attributes that would let a caller step around the guard. Reaching
# them raises; the audited equivalents are navigate() / run_script() / .driver.
_BYPASS_ATTRS = frozenset(
    {
        "get",
        "execute_script",
        "execute_async_script",
        "execute_cdp_cmd",
        "set_page_load_timeout",
        "set_script_timeout",
    }
)

_SECRET_PATTERN = re.compile(r"<secret>([A-Za-z0-9_.\-]{1,64})</secret>")


# ── Exceptions ────────────────────────────────────────────────────────────────


class ScopeViolation(Exception):
    """Base class for every refusal this module issues."""


class NavigationDenied(ScopeViolation):
    """A URL was refused by the domain/scheme allowlist or by egress_guard."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"navigation denied: {url} ({reason})")
        self.url = url
        self.reason = reason


class ActionBudgetExceeded(ScopeViolation):
    """The per-run action cap or the failure cap was hit."""


class StepTimeout(ScopeViolation):
    """A single action exceeded ``step_timeout_seconds``."""


class SecretResolutionError(ScopeViolation):
    """A ``<secret>…</secret>`` placeholder could not be resolved safely."""


# ── Config ────────────────────────────────────────────────────────────────────


@dataclass
class BrowserScopeConfig:
    """Resolved scope configuration. Defaults are the deny-by-default posture."""

    allowed_domains: Tuple[str, ...] = DEFAULT_ALLOWED_DOMAINS
    denied_domains: Tuple[str, ...] = ()
    allowed_schemes: Tuple[str, ...] = DEFAULT_ALLOWED_SCHEMES
    allow_non_local: bool = False
    require_egress_guard: bool = True

    max_actions_per_run: int = 50
    max_failures: int = 3
    step_timeout_seconds: float = 15.0
    page_load_timeout_seconds: float = 30.0
    script_timeout_seconds: float = 15.0

    audit_enabled: bool = True
    audit_actor: str = "browser_agent"
    audit_project_id: str = ""

    broker_agent_id: str = "browser_agent"
    broker_function: str = "browser_credential"
    require_broker_authorization: bool = True
    # placeholder name -> environment variable name (never a value)
    sensitive_placeholders: Dict[str, str] = field(default_factory=dict)

    source_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_domains": list(self.allowed_domains),
            "denied_domains": list(self.denied_domains),
            "allowed_schemes": list(self.allowed_schemes),
            "allow_non_local": self.allow_non_local,
            "require_egress_guard": self.require_egress_guard,
            "max_actions_per_run": self.max_actions_per_run,
            "max_failures": self.max_failures,
            "step_timeout_seconds": self.step_timeout_seconds,
            "page_load_timeout_seconds": self.page_load_timeout_seconds,
            "script_timeout_seconds": self.script_timeout_seconds,
            "audit_enabled": self.audit_enabled,
            "audit_actor": self.audit_actor,
            "audit_project_id": self.audit_project_id,
            "broker_agent_id": self.broker_agent_id,
            "broker_function": self.broker_function,
            "require_broker_authorization": self.require_broker_authorization,
            # names only — never the env var values
            "sensitive_placeholders": sorted(self.sensitive_placeholders),
            "source_path": self.source_path,
        }


def _config_candidates() -> List[Path]:
    """Candidate paths for browser_scope.yaml, most specific first."""
    override = os.environ.get(CONFIG_ENV_VAR)
    candidates: List[Path] = []
    if override:
        candidates.append(Path(override))
    candidates.append(BASE_DIR / "args" / CONFIG_FILENAME)
    # The icdev/ package mirror only carries a subset of args/, so fall back to
    # the repo-root copy when this module is imported as icdev.tools.browser.*
    if BASE_DIR.name == "icdev":
        candidates.append(BASE_DIR.parent / "args" / CONFIG_FILENAME)
    return candidates


def _as_tuple(value: Any, default: Sequence[str]) -> Tuple[str, ...]:
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        value = [value]
    return tuple(str(v).strip().lower() for v in value if str(v).strip())


def load_scope_config(
    path: Optional[Path] = None,
    overrides: Optional[Mapping[str, Any]] = None,
) -> BrowserScopeConfig:
    """Load scope config from YAML, falling back to the deny-by-default values.

    A missing, empty, or unparseable file yields the safe defaults — loopback
    only — rather than an error, so a deployment that forgets the file is
    restricted rather than broken. ``overrides`` is applied last and is how
    tests and callers narrow (never widen, by convention) the policy.
    """
    data: Dict[str, Any] = {}
    source: Optional[str] = None

    candidates = [path] if path is not None else _config_candidates()
    for candidate in candidates:
        if candidate is None or not Path(candidate).exists():
            continue
        try:
            import yaml  # local import: keeps the module importable without PyYAML

            raw = yaml.safe_load(Path(candidate).read_text(encoding="utf-8")) or {}
            data = raw.get("browser_scope", raw) or {}
            source = str(candidate)
            break
        except Exception as exc:
            logger.warning("browser scope config unreadable at %s: %s", candidate, exc)
            continue

    limits = data.get("limits") or {}
    audit = data.get("audit") or {}
    sensitive = data.get("sensitive_data") or {}
    placeholders = sensitive.get("placeholders") or {}

    cfg = BrowserScopeConfig(
        allowed_domains=_as_tuple(data.get("allowed_domains"), DEFAULT_ALLOWED_DOMAINS),
        denied_domains=_as_tuple(data.get("denied_domains"), ()),
        allowed_schemes=_as_tuple(data.get("allowed_schemes"), DEFAULT_ALLOWED_SCHEMES),
        allow_non_local=bool(data.get("allow_non_local", False)),
        require_egress_guard=bool(data.get("require_egress_guard", True)),
        max_actions_per_run=int(limits.get("max_actions_per_run", 50)),
        max_failures=int(limits.get("max_failures", 3)),
        step_timeout_seconds=float(limits.get("step_timeout_seconds", 15.0)),
        page_load_timeout_seconds=float(limits.get("page_load_timeout_seconds", 30.0)),
        script_timeout_seconds=float(limits.get("script_timeout_seconds", 15.0)),
        audit_enabled=bool(audit.get("enabled", True)),
        audit_actor=str(audit.get("actor", "browser_agent")),
        audit_project_id=str(audit.get("project_id", "") or ""),
        broker_agent_id=str(sensitive.get("broker_agent_id", "browser_agent")),
        broker_function=str(sensitive.get("broker_function", "browser_credential")),
        require_broker_authorization=bool(
            sensitive.get("require_broker_authorization", True)
        ),
        sensitive_placeholders={str(k): str(v) for k, v in dict(placeholders).items()},
        source_path=source,
    )

    for key, value in dict(overrides or {}).items():
        if not hasattr(cfg, key):
            raise ValueError(f"unknown scope config override: {key}")
        if key in ("allowed_domains", "denied_domains", "allowed_schemes"):
            value = _as_tuple(value, ())
        setattr(cfg, key, value)

    return cfg


# ── Domain allowlist ──────────────────────────────────────────────────────────


@dataclass
class ScopeDecision:
    """Outcome of a navigation check."""

    allowed: bool
    reason: str
    url: str
    host: Optional[str] = None
    scheme: Optional[str] = None
    ips: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "url": self.url,
            "host": self.host,
            "scheme": self.scheme,
            "ips": list(self.ips),
        }


def _host_matches(host: str, patterns: Sequence[str]) -> bool:
    """Exact host match, or parent-domain suffix match."""
    host = host.lower().strip("[]")
    for raw in patterns:
        pattern = str(raw).lower().strip().strip("[]")
        if not pattern:
            continue
        if host == pattern or host.endswith("." + pattern):
            return True
    return False


def is_local_host(host: Optional[str]) -> bool:
    """True for loopback hosts — the only thing allowed without opt-in."""
    if not host:
        return False
    h = host.lower().strip("[]")
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def check_navigation(
    url: str,
    config: Optional[BrowserScopeConfig] = None,
    resolver: Optional[Callable[..., Any]] = None,
) -> ScopeDecision:
    """Decide whether the agent may navigate to *url*.

    Order matters: scheme, then denylist, then allowlist, then locality, then
    egress. ``resolver`` is forwarded to ``egress_guard`` and is injectable so
    tests never touch DNS.
    """
    cfg = config or load_scope_config()

    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return ScopeDecision(False, "malformed", url)

    scheme = (parts.scheme or "").lower()
    if scheme not in cfg.allowed_schemes:
        return ScopeDecision(False, "scheme_not_allowed", url, scheme=scheme)

    try:
        host = parts.hostname
    except ValueError:
        # urlsplit accepts some malformed authorities but raises on .hostname
        return ScopeDecision(False, "malformed", url, scheme=scheme)
    if not host:
        return ScopeDecision(False, "no_host", url, scheme=scheme)
    host = host.lower()

    # Denylist beats allowlist, always.
    if _host_matches(host, cfg.denied_domains):
        return ScopeDecision(False, "denylisted", url, host=host, scheme=scheme)

    if not _host_matches(host, cfg.allowed_domains):
        return ScopeDecision(False, "not_allowlisted", url, host=host, scheme=scheme)

    if is_local_host(host):
        # egress_guard is deliberately skipped for loopback: it is an *egress*
        # control and refuses non-https and loopback IPs by design, so running
        # it here would deny the only default-allowed target. Nothing leaves
        # the host on this path.
        return ScopeDecision(True, "ok_local", url, host=host, scheme=scheme)

    if not cfg.allow_non_local:
        return ScopeDecision(
            False, "non_local_disabled", url, host=host, scheme=scheme
        )

    if cfg.require_egress_guard:
        try:
            from tools.http.egress_guard import egress_guard
        except Exception as exc:  # fail closed — no guard, no egress
            logger.warning("egress_guard unavailable: %s", exc)
            return ScopeDecision(
                False, "egress_guard_unavailable", url, host=host, scheme=scheme
            )
        guard_cfg = {
            "allowlist": list(cfg.allowed_domains),
            "denylist": list(cfg.denied_domains),
        }
        allowed, reason, ips = egress_guard(url, guard_cfg, resolver=resolver)
        if not allowed:
            return ScopeDecision(
                False, f"egress_guard:{reason}", url, host=host,
                scheme=scheme, ips=tuple(ips or ()),
            )
        return ScopeDecision(
            True, "ok_remote", url, host=host, scheme=scheme, ips=tuple(ips or ())
        )

    return ScopeDecision(True, "ok_remote_unguarded", url, host=host, scheme=scheme)


# ── Sensitive data ────────────────────────────────────────────────────────────


@dataclass
class ResolvedText:
    """Text with placeholders resolved. ``repr`` shows the redacted form only."""

    secret: str
    redacted: str
    placeholders: Tuple[str, ...] = ()

    @property
    def has_secrets(self) -> bool:
        return bool(self.placeholders)

    def __repr__(self) -> str:  # never leak the value through logging/tracebacks
        return f"ResolvedText({self.redacted!r}, placeholders={list(self.placeholders)})"

    __str__ = __repr__


class SensitiveDataResolver:
    """Resolve ``<secret>name</secret>`` placeholders at the driver.

    The agent never receives a credential: it writes a placeholder, this
    resolver swaps in the value immediately before the keystroke, and
    ``redact()`` puts the placeholder back for anything that gets persisted.

    Authorization is requested from ``tools/security/credential_broker.py``
    once per resolver, so a grant/denial row lands in the broker's append-only
    log; the value itself is read from the environment variable the config maps
    the placeholder to. That is the broker's own documented fallback path — no
    second secret store is introduced.
    """

    def __init__(
        self,
        config: Optional[BrowserScopeConfig] = None,
        broker: Optional[Any] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._cfg = config or load_scope_config()
        self._broker = broker
        self._env = env if env is not None else os.environ
        self._authorized: Optional[bool] = None
        self._auth_reason: str = ""

    # -- authorization ---------------------------------------------------

    def _authorize(self) -> None:
        """Ask the credential broker once. Fail closed on denial."""
        if self._authorized is not None:
            if not self._authorized:
                raise SecretResolutionError(
                    f"credential broker denied browser secrets: {self._auth_reason}"
                )
            return

        if not self._cfg.require_broker_authorization:
            self._authorized = True
            self._auth_reason = "broker_not_required"
            return

        broker = self._broker
        if broker is None:
            try:
                from tools.security.credential_broker import CredentialBroker

                broker = CredentialBroker()
            except Exception as exc:
                self._authorized = False
                self._auth_reason = f"broker_unavailable: {exc}"
                raise SecretResolutionError(
                    f"credential broker unavailable, refusing to resolve secrets: {exc}"
                ) from exc

        try:
            grant = broker.request_credential(
                agent_id=self._cfg.broker_agent_id,
                function=self._cfg.broker_function,
            )
        except Exception as exc:
            self._authorized = False
            self._auth_reason = f"broker_error: {exc}"
            raise SecretResolutionError(f"credential broker error: {exc}") from exc

        if isinstance(grant, dict) and grant.get("error"):
            self._authorized = False
            self._auth_reason = str(grant.get("error"))
            raise SecretResolutionError(
                f"credential broker denied browser secrets: {self._auth_reason}"
            )

        self._authorized = True
        self._auth_reason = "fallback" if (grant or {}).get("fallback") else "grant"

    # -- resolution ------------------------------------------------------

    def _lookup(self, name: str) -> str:
        env_var = self._cfg.sensitive_placeholders.get(name)
        if not env_var:
            raise SecretResolutionError(
                f"placeholder '{name}' is not declared in "
                f"browser_scope.sensitive_data.placeholders"
            )
        value = self._env.get(env_var)
        if not value:
            raise SecretResolutionError(
                f"placeholder '{name}' maps to unset environment variable '{env_var}'"
            )
        return value

    def resolve(self, text: str) -> ResolvedText:
        """Return the driver-ready text plus the transcript-safe redaction."""
        if not text:
            return ResolvedText(secret=text or "", redacted=text or "")

        names = [m.group(1) for m in _SECRET_PATTERN.finditer(text)]
        if not names:
            return ResolvedText(secret=text, redacted=text)

        self._authorize()

        resolved = _SECRET_PATTERN.sub(lambda m: self._lookup(m.group(1)), text)
        return ResolvedText(
            secret=resolved,
            redacted=text,
            placeholders=tuple(dict.fromkeys(names)),
        )

    def redact(self, text: Optional[str]) -> str:
        """Replace any known secret value found in *text* with its placeholder.

        Used on anything persisted — audit details, transcripts, page text — so
        a credential that made it onto the page cannot land in the record.
        """
        if not text:
            return text or ""
        out = text
        for name, env_var in self._cfg.sensitive_placeholders.items():
            value = self._env.get(env_var)
            if value and value in out:
                out = out.replace(value, f"<secret>{name}</secret>")
        return out


# ── Budget ────────────────────────────────────────────────────────────────────


class ActionBudget:
    """Per-run action cap, failure cap, and per-step wall-clock budget."""

    def __init__(
        self,
        max_actions: int = 50,
        max_failures: int = 3,
        step_timeout_seconds: float = 15.0,
    ) -> None:
        self.max_actions = int(max_actions)
        self.max_failures = int(max_failures)
        self.step_timeout_seconds = float(step_timeout_seconds)
        self.actions_used = 0
        self.failures = 0

    @classmethod
    def from_config(cls, config: BrowserScopeConfig) -> "ActionBudget":
        return cls(
            max_actions=config.max_actions_per_run,
            max_failures=config.max_failures,
            step_timeout_seconds=config.step_timeout_seconds,
        )

    @property
    def remaining_actions(self) -> int:
        return max(0, self.max_actions - self.actions_used)

    def consume(self, action: str = "action") -> int:
        """Charge one action against the run. Raises when the cap is hit."""
        if self.actions_used >= self.max_actions:
            raise ActionBudgetExceeded(
                f"action cap reached ({self.max_actions}) — refused '{action}'"
            )
        self.actions_used += 1
        return self.actions_used

    def record_failure(self, action: str = "action", error: str = "") -> None:
        """Charge one failure. Raises once the failure cap is reached."""
        self.failures += 1
        if self.failures >= self.max_failures:
            raise ActionBudgetExceeded(
                f"failure cap reached ({self.max_failures}) after "
                f"'{action}': {error}"
            )

    def check_step_duration(self, action: str, elapsed: float) -> None:
        if self.step_timeout_seconds > 0 and elapsed > self.step_timeout_seconds:
            raise StepTimeout(
                f"'{action}' took {elapsed:.1f}s, over the "
                f"{self.step_timeout_seconds:.1f}s step budget"
            )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "actions_used": self.actions_used,
            "max_actions": self.max_actions,
            "remaining_actions": self.remaining_actions,
            "failures": self.failures,
            "max_failures": self.max_failures,
            "step_timeout_seconds": self.step_timeout_seconds,
        }


# ── Audit ─────────────────────────────────────────────────────────────────────


def audit_browser_action(
    action: str,
    outcome: str,
    details: Optional[Dict[str, Any]] = None,
    config: Optional[BrowserScopeConfig] = None,
    run_id: Optional[str] = None,
) -> None:
    """Best-effort audit-trail write for one browser action.

    Follows ``_shell.py``'s ``audit=True`` pattern: reuses the existing
    ``agent_task_completed`` / ``agent_task_failed`` event types (so no
    ``audit_trail`` CHECK-constraint change is needed) and carries the browser
    specifics in ``action`` and ``details``. Never raises.
    """
    cfg = config or load_scope_config()
    if not cfg.audit_enabled:
        return
    try:
        from tools.audit.audit_logger import log_event

        payload = dict(details or {})
        payload["outcome"] = outcome
        if run_id:
            payload["run_id"] = run_id
        log_event(
            event_type="agent_task_completed" if outcome == "allowed" else "agent_task_failed",
            actor=cfg.audit_actor,
            action=f"browser.{action}",
            details=json.loads(json.dumps(payload, default=str)),
            # NULL rather than a synthetic id: audit_trail.project_id is a FK to
            # projects(id), and a non-existent id makes the insert fail silently.
            project_id=cfg.audit_project_id or None,
        )
    except Exception as exc:  # pragma: no cover — audit must never fail the call
        logger.debug("browser audit write failed: %s", exc)


# ── Guarded driver ────────────────────────────────────────────────────────────


class GuardedDriver:
    """A WebDriver wrapper that enforces every control in this module.

    ``agent_browser.py`` (oss-browse-01) builds its element indexing and
    click/type surface on top of this, so the page representation cannot be
    written in a way that skips the guard.

    Not a sandbox. ``.driver`` is the deliberate, documented escape hatch for
    code that needs the raw session; the attributes in ``_BYPASS_ATTRS`` are
    blocked on the wrapper so the escape is explicit rather than accidental.
    """

    def __init__(
        self,
        driver: Any,
        config: Optional[BrowserScopeConfig] = None,
        run_id: Optional[str] = None,
        budget: Optional[ActionBudget] = None,
        secrets: Optional[SensitiveDataResolver] = None,
    ) -> None:
        self.driver = driver
        self.config = config or load_scope_config()
        self.run_id = run_id
        self.budget = budget or ActionBudget.from_config(self.config)
        self.secrets = secrets or SensitiveDataResolver(self.config)

        # The only *enforcing* timeouts Selenium gives us. The wall-clock check
        # in run_action() is the backstop for actions the driver does not time.
        for setter, seconds in (
            ("set_page_load_timeout", self.config.page_load_timeout_seconds),
            ("set_script_timeout", self.config.script_timeout_seconds),
        ):
            try:
                if seconds and seconds > 0:
                    getattr(driver, setter)(seconds)
            except Exception as exc:  # not every driver/stub supports these
                logger.debug("%s unsupported on this driver: %s", setter, exc)

    # -- attribute policy -------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name in _BYPASS_ATTRS:
            raise ScopeViolation(
                f"'{name}' bypasses the browser scope guard — use "
                f"navigate()/run_script()/run_action(), or '.driver' explicitly"
            )
        driver = self.__dict__.get("driver")
        if driver is None:
            # Reached only before __init__ bound the driver (e.g. copy/pickle).
            raise AttributeError(name)
        return getattr(driver, name)

    # -- scope --------------------------------------------------------------

    def check_url(self, url: str) -> ScopeDecision:
        return check_navigation(url, self.config)

    def assert_in_scope(self, action: str = "assert_in_scope") -> str:
        """Verify the session has not been walked out of scope by a redirect.

        Called after every action. On a violation the session is parked at
        ``about:blank`` before raising, so the out-of-scope page is not left
        live for the next action.
        """
        try:
            current = self.driver.current_url
        except Exception as exc:
            logger.debug("current_url unavailable during scope check: %s", exc)
            return ""
        if not current or current.startswith("about:"):
            return current or ""
        decision = self.check_url(current)
        if not decision.allowed:
            try:
                # Direct driver call by design: containment, not navigation.
                self.driver.get("about:blank")
            except Exception as exc:
                logger.debug("containment navigation failed: %s", exc)
            audit_browser_action(
                action,
                "denied",
                {"url": current, "reason": decision.reason, "post_action": True},
                self.config,
                self.run_id,
            )
            raise NavigationDenied(current, f"post_action:{decision.reason}")
        return current

    # -- action envelope ----------------------------------------------------

    def run_action(
        self,
        name: str,
        fn: Callable[[], Any],
        target: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Run one browser action under budget, timing, scope re-check, and audit.

        Every mutation the agent makes should go through here. Denials raise;
        the audit row is written either way.
        """
        try:
            self.budget.consume(name)
        except ActionBudgetExceeded as exc:
            audit_browser_action(
                name, "denied",
                {"target": self._redact(target), "reason": str(exc), **(detail or {})},
                self.config, self.run_id,
            )
            raise

        started = time.time()
        try:
            result = fn()
        except Exception as exc:
            elapsed = time.time() - started
            audit_browser_action(
                name, "failed",
                {
                    "target": self._redact(target),
                    "error": self._redact(f"{type(exc).__name__}: {exc}"),
                    "duration_ms": int(elapsed * 1000),
                    **(detail or {}),
                },
                self.config, self.run_id,
            )
            # Charges the failure; raises ActionBudgetExceeded at the cap.
            self.budget.record_failure(name, str(exc))
            raise

        elapsed = time.time() - started
        try:
            self.budget.check_step_duration(name, elapsed)
        except StepTimeout as exc:
            audit_browser_action(
                name, "failed",
                {
                    "target": self._redact(target),
                    "error": str(exc),
                    "duration_ms": int(elapsed * 1000),
                    **(detail or {}),
                },
                self.config, self.run_id,
            )
            self.budget.record_failure(name, str(exc))
            raise

        self.assert_in_scope(name)
        audit_browser_action(
            name, "allowed",
            {
                "target": self._redact(target),
                "duration_ms": int(elapsed * 1000),
                "actions_used": self.budget.actions_used,
                **(detail or {}),
            },
            self.config, self.run_id,
        )
        return result

    def _redact(self, text: Optional[str]) -> str:
        try:
            return self.secrets.redact(text)
        except Exception:  # redaction must never break the audit path
            return text or ""

    # -- guarded operations --------------------------------------------------

    def navigate(self, url: str) -> str:
        """Navigate, if and only if the allowlist permits it."""
        decision = self.check_url(url)
        if not decision.allowed:
            audit_browser_action(
                "navigate", "denied",
                {"url": url, "reason": decision.reason, "host": decision.host},
                self.config, self.run_id,
            )
            raise NavigationDenied(url, decision.reason)

        self.run_action(
            "navigate",
            lambda: self.driver.get(url),
            target=url,
            detail={"host": decision.host, "reason": decision.reason},
        )
        return url

    def type_text(self, element: Any, text: str, clear: bool = False) -> Tuple[str, ...]:
        """Type into *element*, resolving ``<secret>…</secret>`` at the driver.

        Returns the placeholder names that were substituted. The value is never
        returned, logged, or audited.
        """
        resolved = self.secrets.resolve(text)

        def _do() -> None:
            if clear:
                element.clear()
            element.send_keys(resolved.secret)

        self.run_action(
            "type",
            _do,
            target=resolved.redacted[:200],
            detail={"placeholders": list(resolved.placeholders)},
        )
        return resolved.placeholders

    def click(self, element: Any, label: Optional[str] = None) -> None:
        self.run_action("click", element.click, target=label or "")

    def run_script(self, script: str, *args: Any) -> Any:
        """Audited, budgeted ``execute_script``."""
        return self.run_action(
            "script",
            lambda: self.driver.execute_script(script, *args),
            target=script[:200],
        )

    def screenshot(self, path: Optional[str] = None) -> Any:
        def _do() -> Any:
            if path:
                self.driver.save_screenshot(str(path))
                return path
            return self.driver.get_screenshot_as_png()

        return self.run_action("screenshot", _do, target=str(path or "<png>"))

    def status(self) -> Dict[str, Any]:
        """Run status — budget plus the active policy. Safe to log."""
        return {
            "run_id": self.run_id,
            "budget": self.budget.snapshot(),
            "policy": self.config.to_dict(),
        }


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="ICDEV™ browser agent scope controls (oss-browse-02)"
    )
    parser.add_argument("--show", action="store_true", help="Print the active policy")
    parser.add_argument("--check-url", help="Test one URL against the allowlist")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    cfg = load_scope_config()

    if args.check_url:
        decision = check_navigation(args.check_url, cfg)
        if args.json:
            print(json.dumps(decision.to_dict(), indent=2))
        else:
            verdict = "ALLOW" if decision.allowed else "DENY "
            print(f"{verdict} {decision.url}  ({decision.reason})")
        return 0 if decision.allowed else 1

    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
