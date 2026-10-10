# CUI // SP-CTI
"""omx-guard-02: the runner never AUTONOMOUSLY spawns an adapter whose guard is unverified.

THE DEFECT. The kanban runner dispatches unattended sessions, and the only ICDEV
control that sees a tool call inside one is the PreToolUse guard (CLAUDE.md:
``claude_cli`` runs with ``--dangerously-skip-permissions``). With the OSS
harnesses selectable through ``ICDEV_AGENT_ADAPTER``, nothing asked whether the
CHOSEN adapter's guard is wired before spawning it -- an adapter with no guard
would run autonomously with no ICDEV control at all.

ONE QUESTION, MEASURED, NOT DECLARED:

* ``claude_cli`` -- its guard lives in ``.claude/settings.json``, outside the
  adapter seam (the capability matrix reports it ``unconfirmed`` for exactly
  that reason). It counts as guarded when a ``PreToolUse`` entry matching every
  tool runs ``pre_tool_use.py`` and is NOT behind a shell neutraliser such as
  ``|| true`` (D394: a wrapped hook printed BLOCKED and blocked nothing), and
  ``ICDEV_PRETOOLUSE_ENFORCE`` has not stood the hook down.
* every other adapter -- the capability matrix's ``guard_wired`` cell, which
  only ``verify_guard()`` reporting a live refusal can make ``present``.
  ``unconfirmed`` is NOT guarded: fail-closed, like ``pick_default(require=)``.

Scope: AUTONOMOUS dispatch only. Interactive use never consults this module.

Modes (``ICDEV_GUARD_PARITY_GATE``): ``enforce`` (default) refuses an unguarded
adapter and the runner tries the next guarded one in ``fallback_order``;
``report`` logs the would-be refusal and dispatches anyway. The default was
flipped to ``enforce`` because ``--survey`` showed the live default adapter
``claude_cli``) guarded on 2026-10-10 -- re-survey before changing it.

Usage:
    python -m tools.agents.guard_parity --survey
    python -m tools.agents.guard_parity --survey --json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

_BASE = pathlib.Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

MODE_ENV = "ICDEV_GUARD_PARITY_GATE"
REPORT = "report"
ENFORCE = "enforce"
DEFAULT_MODE = ENFORCE
_MODES = (REPORT, ENFORCE)

SETTINGS_PATH = _BASE / ".claude" / "settings.json"
_HOOK_SCRIPT = "pre_tool_use.py"
# A shell operator that makes the hook's exit 2 unreachable (D394).
_NEUTRALISER = re.compile(r"(\|\|\s*(true\b|:|exit\s+0\b))|(;\s*(true|exit\s+0)\s*$)")

# verify_guard() spawns a process; the scheduler asks once per dispatch.
_CACHE_TTL_SECONDS = 600.0
_cache: Dict[str, "GuardVerdict"] = {}
_cache_at: Dict[str, float] = {}


@dataclass
class GuardVerdict:
    adapter: str
    guarded: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GateDecision:
    """What the runner should spawn. ``adapter`` is None when nothing guarded is left."""
    mode: str
    chosen: str
    adapter: Optional[str]
    refused: List[GuardVerdict] = field(default_factory=list)

    @property
    def note(self) -> str:
        """The reason recorded on the task -- empty when nothing was refused."""
        if not self.refused:
            return ""
        parts = [f"{v.adapter}: {v.reason}" for v in self.refused]
        verb = "refused" if self.mode == ENFORCE else "would refuse (mode=report)"
        tail = (f"; dispatching {self.adapter}" if self.adapter
                else "; no guarded adapter available")
        return f"guard parity {verb} " + " | ".join(parts) + tail


def mode() -> str:
    value = os.environ.get(MODE_ENV, "").strip().lower()
    if not value:
        return DEFAULT_MODE
    if value not in _MODES:
        logger.warning("%s=%r is not one of %s -- using %s",
                       MODE_ENV, value, _MODES, DEFAULT_MODE)
        return DEFAULT_MODE
    return value


def claude_settings_verdict(settings_path: Optional[pathlib.Path] = None) -> GuardVerdict:
    """claude_cli is guarded iff settings.json wires an un-neutralised PreToolUse hook."""
    name = "claude_cli"
    if os.environ.get("ICDEV_PRETOOLUSE_ENFORCE", "").strip() == "0":
        return GuardVerdict(name, False, "ICDEV_PRETOOLUSE_ENFORCE=0 stands the hook down")
    path = settings_path or SETTINGS_PATH
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 -- unreadable is unverified
        return GuardVerdict(name, False, f"{path.name} unreadable: {type(exc).__name__}")
    entries = ((settings.get("hooks") or {}).get("PreToolUse")) or []
    neutralised = []
    for entry in entries:
        if not isinstance(entry, dict) or (entry.get("matcher") or "") not in ("", "*"):
            continue
        for hook in entry.get("hooks") or []:
            cmd = str((hook or {}).get("command") or "")
            if _HOOK_SCRIPT not in cmd:
                continue
            if _NEUTRALISER.search(cmd):
                neutralised.append(cmd)
                continue
            return GuardVerdict(
                name, True,
                f"{path.name} PreToolUse runs {_HOOK_SCRIPT} for every tool, unwrapped",
            )
    if neutralised:
        return GuardVerdict(name, False,
                            f"PreToolUse hook is behind a shell neutraliser: {neutralised[0]!r}")
    return GuardVerdict(name, False,
                        f"no all-tools PreToolUse entry runs {_HOOK_SCRIPT} in {path.name}")


def _matrix_verdict(adapter_name: str) -> GuardVerdict:
    from tools.agents import capability_matrix  # noqa: PLC0415 -- imports registry

    try:
        entry = capability_matrix.probe_adapter(adapter_name, only=["guard_wired"])
        cell = (entry.get("capabilities") or {}).get("guard_wired") or {}
    except Exception as exc:  # noqa: BLE001 -- a failed probe never promotes
        return GuardVerdict(adapter_name, False,
                            f"guard_wired probe failed: {type(exc).__name__}: {exc}")
    actual = cell.get("actual", capability_matrix.UNCONFIRMED)
    evidence = str(cell.get("evidence") or "")
    return GuardVerdict(adapter_name, actual == capability_matrix.PRESENT,
                        f"guard_wired={actual}" + (f" ({evidence})" if evidence else ""))


def verdict_for(adapter_name: str, *, use_cache: bool = True) -> GuardVerdict:
    """Is ICDEV's guard verified for this adapter's tool calls?"""
    now = time.monotonic()
    if use_cache and adapter_name in _cache and now - _cache_at[adapter_name] < _CACHE_TTL_SECONDS:
        return _cache[adapter_name]
    if adapter_name == "claude_cli":
        verdict = claude_settings_verdict()
    else:
        verdict = _matrix_verdict(adapter_name)
    _cache[adapter_name] = verdict
    _cache_at[adapter_name] = now
    return verdict


def reset_cache() -> None:
    _cache.clear()
    _cache_at.clear()


def _available(name: str) -> bool:
    from tools.agents import registry  # noqa: PLC0415

    try:
        return bool(registry.get_adapter(name).available())
    except Exception:  # noqa: BLE001
        return False


def gate(
    chosen: str,
    fallback_order: Sequence[str],
    *,
    verdict: Optional[Callable[[str], GuardVerdict]] = None,
    available: Optional[Callable[[str], bool]] = None,
    gate_mode: Optional[str] = None,
) -> GateDecision:
    """Screen the adapter the runner picked; fall back to a guarded one in enforce.

    ``report`` never changes the answer -- it only fills ``refused`` so the
    caller can log and record what enforce would have done.
    """
    verdict = verdict or verdict_for
    available = available or _available
    gm = gate_mode or mode()
    first = verdict(chosen)
    if first.guarded:
        return GateDecision(gm, chosen, chosen)
    decision = GateDecision(gm, chosen, chosen if gm == REPORT else None, [first])
    if gm == REPORT:
        return decision
    for name in fallback_order:
        if name == chosen or not available(name):
            continue
        v = verdict(name)
        if v.guarded:
            decision.adapter = name
            return decision
        decision.refused.append(v)
    return decision


def survey(adapters: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Every registered adapter's verdict, and which enforce would refuse."""
    from tools.agents import registry  # noqa: PLC0415

    names = list(adapters) if adapters is not None else registry.list_adapters()
    rows = []
    for name in names:
        v = verdict_for(name, use_cache=False)
        row = v.to_dict()
        row["available"] = _available(name)
        row["would_refuse_in_enforce"] = not v.guarded
        rows.append(row)
    forced = os.environ.get("ICDEV_AGENT_ADAPTER", "").strip()
    return {
        "mode": mode(),
        "live_default_adapter": forced or "claude_cli",
        "adapters": rows,
        "refused": sorted(r["adapter"] for r in rows if r["would_refuse_in_enforce"]),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.agents.guard_parity",
        description="Which agent adapters the runner may spawn autonomously.",
    )
    parser.add_argument("--survey", action="store_true", help="verdict for every adapter")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(argv)
    if not opts.survey:
        parser.print_help()
        return 0
    result = survey()
    if opts.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"guard parity gate: mode={result['mode']} "
          f"live default={result['live_default_adapter']}")
    for row in result["adapters"]:
        tag = "GUARDED " if row["guarded"] else "REFUSED "
        avail = "available" if row["available"] else "not installed"
        print(f"  {tag} {row['adapter']:<18} [{avail}] {row['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
