# CUI // SP-CTI
"""omx-adapt-06: ONE notion of "this LLM provider is exhausted", shared by the
router and the kanban dispatcher.

OPERATOR DECISION 2026-10-11: "If Claude exhausts its tokens, we have Ollama
Cloud and vice versa." Claude is reached only through the claude.ai
SUBSCRIPTION (the ``claude`` CLI / CLI bridge -- omx-guard-07 forbids metered
keys) and draws a monthly programmatic credit that fails CLOSED when spent
(omx-spike-02, Usage Credits OFF). Ollama Cloud is the operator's other
subscription. When one is exhausted, work moves to the other until the stated
resume time; when both are, work is HELD, never failed.

Three jobs, one module, so the two seams cannot disagree:

* ``classify(text, provider=...)`` -- reads the BODY of a provider error, never
  an exit code. Pi exits 0 on a provider 400/404 (measured twice: omx-spike-01
  and omx-spike-02), and the 400 that ends Claude for a third-party harness
  says so only in its body ("Third-party apps now draw from your extra usage").
  Returns ``Exhaustion(provider, kind, resume_at)`` or None.
* ``record()`` / ``exhausted_until()`` -- the state lives in the router's
  EXISTING D-AUTO-DEGRADE file (``data/llm_degraded_tier2.json``), as
  ``provider:<family>`` entries of its ``degraded`` set with the resume time in
  ``probed_at`` -- exactly how a degraded tier2 model is stored. No parallel
  store: every process (dashboard, scheduler, daemon) reads the same file.
* ``plan_harness_dispatch()`` -- for a coding-agent dispatch, decide run /
  failover / hold from that state and the ``provider_failover.harnesses``
  declaration.

LLM-agnostic: no model id appears here. Provider families, the router's
failover models and every harness model live in ``args/llm_config.yaml``
under ``provider_failover``.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from icdev.core.paths import repo_root

KIND_EXHAUSTED = "exhausted"
KIND_RATE_LIMITED = "rate_limited"

#: The state file the router's D-AUTO-DEGRADE circuit-breaker already owns.
STATE_PATH: Path = repo_root(__file__) / "data" / "llm_degraded_tier2.json"

#: Prefix that keeps provider entries apart from degraded MODEL names in the
#: shared ``degraded`` set -- ``LLMRouter._is_tier2_degraded(model)`` can never
#: mistake one for the other.
_KEY_PREFIX = "provider:"

_DEFAULT_HOLD_SECONDS = {KIND_EXHAUSTED: 3600, KIND_RATE_LIMITED: 300}

# How far into a log the classifier reads. Provider errors land at the end of a
# run; reading the whole log would let a task that merely DISCUSSES limits
# (this module's own tests, say) read as exhausted.
TAIL_CHARS = 6000


@dataclass
class Exhaustion:
    """One classified provider-exhaustion observation."""

    provider: Optional[str]
    kind: str
    resume_at: Optional[float] = None  # unix seconds, only when the body STATED one
    evidence: str = ""


@dataclass
class HarnessPlan:
    """What a coding-agent dispatch should do, given current exhaustion state."""

    action: str  # run | failover | hold
    adapter: str
    model: Optional[str] = None
    family: Optional[str] = None  # the provider family that will actually serve
    from_family: Optional[str] = None
    hold_until: Optional[float] = None
    reason: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ── body patterns ───────────────────────────────────────────────────────────
# Provider-SPECIFIC phrasing names its provider by itself. Each was taken from a
# measured or published error body, not invented:
#   * claude CLI: "Claude AI usage limit reached|<epoch>", "You've hit your
#     limit · resets 3pm (America/New_York)", "5-hour limit reached",
#     "Credit balance is too low".
#   * Anthropic 400 to a third-party subscription client (omx-spike-02, Pi):
#     "Third-party apps now draw from your extra usage, not your plan limits."
_CLAUDE_EXHAUSTED = [
    r"claude(?:\s+ai)?\s+usage\s+limit\s+reached",
    r"(?:you'?ve|you\s+have)\s+(?:hit|reached)\s+your\s+(?:[\w-]+\s+)?limit\b",
    r"\b(?:5-hour|five-hour|opus|sonnet)\s+limit\s+reached",
    r"(?:draw\s+from|out\s+of)\s+(?:your\s+)?extra\s+usage",
    r"credit\s+balance\s+is\s+too\s+low",
    r"programmatic\s+(?:usage\s+)?credit\S*\s+(?:is\s+|has\s+been\s+)?"
    r"(?:exhausted|used\s+up|depleted|run\s+out)",
]
# Ollama Cloud: a 429 whose body names a usage window or tells you to upgrade.
_OLLAMA_EXHAUSTED = [
    r"ollama[^\n]{0,200}(?:usage\s+limit|upgrade)",
    r"(?:usage\s+limit|upgrade)[^\n]{0,200}ollama\.com",
]
# Generic: the provider is whatever produced the body (the caller's hint).
_GENERIC_EXHAUSTED = [
    r"(?:reached|exceeded|hit)\s+(?:your|the)\s+(?:hourly|daily|weekly|monthly|"
    r"session)?\s*usage\s+limit",
    r"\b(?:hourly|daily|weekly|monthly|session)\s+usage\s+limit",
    r"usage\s+limit\s+(?:reached|exceeded)",
    r"quota\s+(?:exceeded|exhausted)",
    r"insufficient_quota",
    r"out\s+of\s+(?:credits|tokens|usage)",
    r"upgrade\s+(?:for|to\s+(?:get|continue))\s+(?:higher|more)\s+(?:limits|usage)",
]
_GENERIC_RATE_LIMITED = [
    # A bare 429 is anchored to an HTTP/status context so "line 429" of a
    # traceback is not a rate limit.
    r"(?:\b(?:status|http|code|error|statuscode)[\"'\s:=]*429\b)",
    r"\b429\s*[:\-]?\s*(?:too\s+many|\{|rate|usage|you)",
    r"too\s+many\s+(?:concurrent\s+)?requests",
    # "rate limit" alone is prose; an error says it was hit.
    r"\brate[\s_]limit(?:ed\b|_error|\s+(?:exceeded|reached|hit))",
]

_FLAGS = re.IGNORECASE


def _first(patterns: List[str], text: str) -> Optional[re.Match]:
    for pat in patterns:
        m = re.search(pat, text, _FLAGS)
        if m:
            return m
    return None


def classify(text: str, *, provider: Optional[str] = None,
             now: Optional[float] = None) -> Optional[Exhaustion]:
    """Classify a provider error BODY. Returns None when it is not exhaustion.

    ``provider`` is the family that produced the text when the caller knows it
    (the router knows the model's provider; the dispatcher knows the harness's
    model). Provider-specific phrasing overrides the hint -- a Claude limit
    message is Claude's whoever relayed it. Generic phrasing (a 429, "weekly
    usage limit") is attributed to the hint, and with no hint it is NOT
    attributed at all: guessing a provider would fail the wrong one over.
    """
    if not text:
        return None
    tail = str(text)[-TAIL_CHARS:]
    now = time.time() if now is None else now

    m = _first(_CLAUDE_EXHAUSTED, tail)
    if m:
        return Exhaustion("claude", KIND_EXHAUSTED, parse_resume_at(tail, now=now),
                          _evidence(tail, m))
    m = _first(_OLLAMA_EXHAUSTED, tail)
    if m:
        return Exhaustion("ollama_cloud", KIND_EXHAUSTED,
                          parse_resume_at(tail, now=now), _evidence(tail, m))
    if not provider:
        return None
    m = _first(_GENERIC_EXHAUSTED, tail)
    if m:
        return Exhaustion(provider, KIND_EXHAUSTED, parse_resume_at(tail, now=now),
                          _evidence(tail, m))
    m = _first(_GENERIC_RATE_LIMITED, tail)
    if m:
        return Exhaustion(provider, KIND_RATE_LIMITED, parse_resume_at(tail, now=now),
                          _evidence(tail, m))
    return None


def _evidence(text: str, m: re.Match) -> str:
    start = max(0, m.start() - 60)
    return " ".join(text[start:m.end() + 100].split())[:240]


# ── resume time ─────────────────────────────────────────────────────────────
_UNIT_SECONDS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60,
                 "h": 3600, "hr": 3600, "hour": 3600, "d": 86400, "day": 86400}


def parse_resume_at(text: str, *, now: Optional[float] = None) -> Optional[float]:
    """The resume time a body STATES, as unix seconds -- or None.

    Never invents one: a body with no time gets the declared default hold from
    ``record()``, and that default is visible as such in the state.
    """
    if not text:
        return None
    now = time.time() if now is None else now

    # claude CLI: "Claude AI usage limit reached|1760000000"
    m = re.search(r"limit\s+reached\|(\d{10})\b", text, _FLAGS)
    if m:
        return float(m.group(1))

    # ISO timestamp in a JSON body: "resets_at": "2026-10-11T19:00:00Z"
    m = re.search(r"(?:reset|resume|retry)\w*[\"']?\s*[:=]\s*[\"']"
                  r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
                  text, _FLAGS)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass

    # Relative: "try again in 20 minutes", "resets in 2 hours", "retry-after: 30"
    m = re.search(r"(?:try\s+again|retry|resets?|available)\s*(?:after|in)?\s*[:=]?\s*"
                  r"(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b",
                  text, _FLAGS)
    if m:
        unit = m.group(2).lower().rstrip("s") or "s"
        secs = _UNIT_SECONDS.get(unit) or _UNIT_SECONDS.get(unit[:1], 60)
        return now + int(m.group(1)) * secs
    m = re.search(r"retry-after\s*[:=]\s*(\d+)\b", text, _FLAGS)
    if m:
        return now + int(m.group(1))

    # Wall clock: "resets 3pm (America/New_York)", "resets at 7:30 am"
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
                  r"(?:\s*\(([A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?|UTC)\))?",
                  text, _FLAGS)
    if m and (m.group(2) or m.group(3)):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if 0 <= hour < 24 and 0 <= minute < 60:
            tz = _zone(m.group(4))
            base = datetime.fromtimestamp(now, tz=tz)
            target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= base:
                target += timedelta(days=1)
            return target.timestamp()
    return None


def _zone(name: Optional[str]):
    if name:
        if name.upper() == "UTC":
            return timezone.utc
        try:
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 -- unknown zone: fall back to local
            pass
    return datetime.now().astimezone().tzinfo


# ── policy (args/llm_config.yaml: provider_failover) ────────────────────────
_policy_cache: Dict[str, Any] = {"path": None, "mtime": None, "policy": None}


def _expand(value: Any) -> Any:
    """``${VAR:-default}`` expansion, same rule as the router's ``_expand_env``."""
    if isinstance(value, str):
        pattern = r"\$\{([^${}]+)\}"

        def repl(match):
            expr = match.group(1)
            if ":-" in expr:
                var, default = expr.split(":-", 1)
                return os.environ.get(var, default)
            return os.environ.get(expr, "")

        for _ in range(8):
            new = re.sub(pattern, repl, value)
            if new == value:
                break
            value = new
        return value
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_policy(config: Optional[dict] = None) -> dict:
    """The ``provider_failover`` block, env-expanded. ``{}`` when absent/disabled.

    The router passes its own loaded config; everyone else reads the resolved
    ``llm_config.yaml`` (mtime-cached -- this is called on every dispatch).
    """
    if config is None:
        try:
            from tools.llm.config_path import resolve_llm_config_path  # noqa: PLC0415
            import yaml  # noqa: PLC0415

            path = resolve_llm_config_path()
            mtime = path.stat().st_mtime
            if _policy_cache["path"] == str(path) and _policy_cache["mtime"] == mtime:
                block = _policy_cache["policy"]
            else:
                with open(path, "r", encoding="utf-8") as fh:
                    block = (yaml.safe_load(fh) or {}).get("provider_failover") or {}
                _policy_cache.update(path=str(path), mtime=mtime, policy=block)
        except Exception:  # noqa: BLE001 -- no policy readable: no failover, never a crash
            return {}
    else:
        block = config.get("provider_failover") or {}
    policy = _expand(block)
    if str(policy.get("enabled", True)).lower() in ("false", "0", "no", "off"):
        return {}
    return policy


def families(policy: Optional[dict] = None) -> List[str]:
    policy = load_policy() if policy is None else policy
    return list((policy.get("providers") or {}).keys())


def failover_target(family: Optional[str], policy: Optional[dict] = None) -> Optional[str]:
    policy = load_policy() if policy is None else policy
    entry = (policy.get("providers") or {}).get(family or "") or {}
    return entry.get("failover_to") or None


def family_for_router_provider(provider_name: str,
                               policy: Optional[dict] = None) -> Optional[str]:
    """The family a router provider (``claude-cli``, ``ollama_cloud``...) belongs to."""
    policy = load_policy() if policy is None else policy
    for fam, entry in (policy.get("providers") or {}).items():
        if provider_name in (entry.get("router_providers") or []):
            return fam
    return None


def family_for_harness_model(model: Optional[str],
                             policy: Optional[dict] = None) -> Optional[str]:
    """The family a harness ``provider/model`` string belongs to, by prefix."""
    if not model:
        return None
    policy = load_policy() if policy is None else policy
    for fam, entry in (policy.get("providers") or {}).items():
        for prefix in entry.get("harness_model_prefixes") or []:
            if prefix and str(model).startswith(str(prefix)):
                return fam
    return None


def harness_family(adapter: str, model: Optional[str],
                   policy: Optional[dict] = None) -> Optional[str]:
    """The family a harness run will draw on: fixed (claude_cli) or by its model."""
    policy = load_policy() if policy is None else policy
    entry = (policy.get("harnesses") or {}).get(adapter) or {}
    if entry.get("family"):
        return entry["family"]
    return family_for_harness_model(model, policy)


def harness_current_model(adapter: str, policy: Optional[dict] = None,
                          environ: Optional[dict] = None) -> Optional[str]:
    """The model a harness would run with today: its declared ``model_env``."""
    policy = load_policy() if policy is None else policy
    env = os.environ if environ is None else environ
    entry = (policy.get("harnesses") or {}).get(adapter) or {}
    var = entry.get("model_env")
    return (env.get(var) or None) if var else None


def harness_model_for(adapter: str, family: str, policy: Optional[dict] = None,
                      environ: Optional[dict] = None) -> Optional[str]:
    """The model ``adapter`` should use to draw on ``family``, or None.

    A declared ``harnesses.<adapter>.models.<family>`` wins; otherwise the
    harness's current model if it already belongs to ``family``. A harness
    with a fixed family needs no model (None) and serves only that family.
    """
    policy = load_policy() if policy is None else policy
    entry = (policy.get("harnesses") or {}).get(adapter) or {}
    declared = ((entry.get("models") or {}).get(family) or "").strip()
    if declared:
        return declared
    current = harness_current_model(adapter, policy, environ)
    if current and family_for_harness_model(current, policy) == family:
        return current
    return None


# ── shared state (the D-AUTO-DEGRADE file) ──────────────────────────────────
def state_path() -> Path:
    return STATE_PATH


def _read_state() -> dict:
    try:
        path = state_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001 -- unreadable state = nothing exhausted
        pass
    return {}


def _write_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


def record(exh: Exhaustion, *, now: Optional[float] = None,
           policy: Optional[dict] = None) -> Optional[float]:
    """Persist ``exh`` so every process agrees. Returns the resume time used.

    A body that stated no time gets the declared default hold for its kind
    (``hold_seconds``); ``resume_source`` records which it was. A provider with
    no family (no hint, generic body) is not recorded.
    """
    if exh is None or not exh.provider:
        return None
    now = time.time() if now is None else now
    policy = load_policy() if policy is None else policy
    holds = {**_DEFAULT_HOLD_SECONDS, **(policy.get("hold_seconds") or {})}
    stated = exh.resume_at is not None and exh.resume_at > now
    resume_at = exh.resume_at if stated else now + float(holds.get(exh.kind, 3600))

    key = _KEY_PREFIX + exh.provider
    state = _read_state()
    degraded = set(state.get("degraded") or [])
    probed_at = dict(state.get("probed_at") or {})
    detail = dict(state.get("provider_detail") or {})
    # A later observation never SHORTENS a known exhaustion (a rate-limit seen
    # during a weekly-quota exhaustion must not end it early).
    if key in degraded and float(probed_at.get(key) or 0) > resume_at:
        resume_at = float(probed_at[key])
    degraded.add(key)
    probed_at[key] = resume_at
    detail[exh.provider] = {
        "kind": exh.kind,
        "resume_at": resume_at,
        "resume_source": "stated" if stated else "default_hold",
        "recorded_at": now,
        "evidence": exh.evidence,
    }
    state.update(degraded=sorted(degraded), probed_at=probed_at, provider_detail=detail)
    try:
        _write_state(state)
    except Exception:  # noqa: BLE001 -- best effort, like the router's own persist
        pass
    return resume_at


def clear(family: str) -> None:
    """Forget an exhaustion (operator action, or a test)."""
    key = _KEY_PREFIX + family
    state = _read_state()
    state["degraded"] = sorted(set(state.get("degraded") or []) - {key})
    (state.get("probed_at") or {}).pop(key, None)
    (state.get("provider_detail") or {}).pop(family, None)
    try:
        _write_state(state)
    except Exception:  # noqa: BLE001
        pass


def exhausted_until(family: Optional[str], *, now: Optional[float] = None) -> Optional[float]:
    """Resume time if ``family`` is exhausted NOW, else None (it has resumed)."""
    if not family:
        return None
    now = time.time() if now is None else now
    state = _read_state()
    key = _KEY_PREFIX + family
    if key not in (state.get("degraded") or []):
        return None
    try:
        until = float((state.get("probed_at") or {}).get(key) or 0)
    except (TypeError, ValueError):
        return None
    return until if until > now else None


def snapshot(*, now: Optional[float] = None, policy: Optional[dict] = None) -> Dict[str, Any]:
    """Every declared family's current state -- for the capability matrix / panel."""
    now = time.time() if now is None else now
    policy = load_policy() if policy is None else policy
    detail = _read_state().get("provider_detail") or {}
    out: Dict[str, Any] = {}
    for fam in families(policy):
        until = exhausted_until(fam, now=now)
        info = detail.get(fam) or {}
        out[fam] = {
            "exhausted": until is not None,
            "kind": info.get("kind") if until is not None else None,
            "resume_at": (datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
                          if until is not None else None),
            "resume_source": info.get("resume_source") if until is not None else None,
            "failover_to": failover_target(fam, policy),
        }
    return out


# ── router seam ─────────────────────────────────────────────────────────────
def apply_to_chain(chain: List[str], models: Dict[str, dict],
                   policy: Optional[dict] = None, *,
                   now: Optional[float] = None) -> List[str]:
    """Drop models of exhausted families; splice in the declared failover models.

    Failover models take the position of the first dropped model, so priority
    against the rest of the chain is unchanged. When EVERY entry would be
    dropped and nothing can replace them, the chain is returned unchanged: the
    router then fails exactly as it would have, rather than with an empty chain.
    """
    policy = load_policy() if policy is None else policy
    if not policy or not chain:
        return list(chain)
    providers = policy.get("providers") or {}
    exhausted = {f for f in providers if exhausted_until(f, now=now) is not None}
    if not exhausted:
        return list(chain)

    def fam_of(model_name: str) -> Optional[str]:
        return family_for_router_provider((models.get(model_name) or {}).get("provider", ""),
                                          policy)

    out: List[str] = []
    spliced = set()
    for name in chain:
        fam = fam_of(name)
        if fam not in exhausted:
            if name not in out:
                out.append(name)
            continue
        if fam in spliced:
            continue
        spliced.add(fam)
        target = failover_target(fam, policy)
        if not target or target in exhausted:
            continue
        for alt in (providers.get(fam) or {}).get("router_failover_models") or []:
            if alt in models and alt not in out and alt not in chain:
                out.append(alt)
    return out or list(chain)


def observe_router_error(provider_name: str, error: Any,
                         policy: Optional[dict] = None) -> Optional[Exhaustion]:
    """Classify a router provider failure and record it. Never raises."""
    try:
        policy = load_policy() if policy is None else policy
        fam = family_for_router_provider(provider_name, policy)
        if not fam:
            return None
        exh = classify(str(error), provider=fam)
        if exh is None or exh.provider not in (policy.get("providers") or {}):
            return None
        record(exh, policy=policy)
        return exh
    except Exception:  # noqa: BLE001
        return None


# ── coding-agent (kanban) seam ──────────────────────────────────────────────
def plan_harness_dispatch(adapter: str, model: Optional[str] = None, *,
                          available: Optional[Callable[[str], bool]] = None,
                          policy: Optional[dict] = None,
                          now: Optional[float] = None,
                          environ: Optional[dict] = None) -> HarnessPlan:
    """run / failover / hold for one dispatch of ``adapter`` (running ``model``).

    * run      -- its family is not exhausted (or it has no declared family).
    * failover -- its family is exhausted and the failover family is not: the
                  SAME harness on the other family's model when one is declared,
                  else the declared ``failover_adapter`` for that family.
    * hold     -- both exhausted (until the EARLIER resume), or no harness route
                  to the other family (until this family resumes).
    """
    policy = load_policy() if policy is None else policy
    now = time.time() if now is None else now
    available = available or (lambda _name: True)
    fam = harness_family(adapter, model, policy)
    if not policy or not fam:
        return HarnessPlan("run", adapter, model, fam)
    until = exhausted_until(fam, now=now)
    if until is None:
        return HarnessPlan("run", adapter, model, fam)

    target = failover_target(fam, policy)
    target_until = exhausted_until(target, now=now) if target else None
    if target and target_until is not None:
        hold = min(until, target_until)
        return HarnessPlan("hold", adapter, model, None, fam, hold,
                           f"{fam} and {target} both exhausted")
    if not target:
        return HarnessPlan("hold", adapter, model, None, fam, until,
                           f"{fam} exhausted and no failover provider is declared")

    same = harness_model_for(adapter, target, policy, environ)
    if same and harness_family(adapter, same, policy) == target:
        return HarnessPlan("failover", adapter, same, target, fam,
                           reason=f"{fam} exhausted -> {target} on {adapter}")
    entry = (policy.get("harnesses") or {}).get(adapter) or {}
    alt = (entry.get("failover_adapter") or {}).get(target)
    if alt and alt != adapter and available(alt):
        alt_model = harness_model_for(alt, target, policy, environ)
        if harness_family(alt, alt_model, policy) == target:
            return HarnessPlan("failover", alt, alt_model, target, fam,
                               reason=f"{fam} exhausted -> {target} via {alt}")
    return HarnessPlan("hold", adapter, model, None, fam, until,
                       f"{fam} exhausted and no harness route to {target} is "
                       f"declared/available for {adapter}")


__all__ = [
    "Exhaustion", "HarnessPlan", "KIND_EXHAUSTED", "KIND_RATE_LIMITED",
    "classify", "parse_resume_at", "record", "clear", "exhausted_until",
    "snapshot", "load_policy", "families", "failover_target",
    "family_for_router_provider", "family_for_harness_model", "harness_family",
    "harness_current_model", "harness_model_for", "apply_to_chain",
    "observe_router_error", "plan_harness_dispatch", "state_path",
]
