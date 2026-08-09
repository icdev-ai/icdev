# CUI // SP-CTI
"""CDP policy/tier preflight (cdp-port-06).

Decides which browser-automation tier is usable on THIS host, deterministically
and without launching anything — so the rest of the plan knows up front whether
it even applies (spike cdp-00 §4.7.3, priority #2).

The load-bearing fact (spike §4.7.1): ``RemoteDebuggingAllowed`` is a Chromium
enterprise policy that blocks BOTH ``--remote-debugging-port`` and
``--remote-debugging-pipe``, machine-wide. And ``chromedriver``/``msedgedriver``
drive the browser over CDP *themselves* — so the classic ``DevToolsActivePort
file doesn't exist`` failure is exactly this policy firing. The consequence people
miss:

    Anywhere Selenium works, CDP works. Anywhere policy blocks CDP, Selenium is
    dead too.

CDP's requirements are a strict SUBSET of Selenium's. So a restrictive policy is
NOT an argument for keeping Selenium — it takes Selenium out as well. The tiers:

  * Tier 1 — CDP: remote debugging permitted + any Chromium-family browser.
             Zero downloads, zero binaries. The default everywhere.
  * Tier 2 — Selenium + version-matched WebDriver binary. A *compatibility*
             option (W3C-WebDriver-mandated audits, or a CDP-client escape hatch),
             never the air-gap answer. Selectable, never auto-selected.
  * Tier 3 — browser-free HTTP verification (route_smoke / api_contract_tester /
             fathomdesk_smoke). Always available; the honest degradation when
             debugging is forbidden or no browser is present. Rendered-DOM checks
             are lost — that loss is stated by cdp-port-07, not discovered.

**Unset policy means PERMITTED** — the documented Chromium default. Preflight
reads the key, picks the tier, and reports which and *why*; it never guesses by
launching a browser and waiting for a timeout.
"""
from __future__ import annotations

import glob
import json
import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.cdp.preflight")

# The Chromium policy value name, identical across Edge and Chrome.
_POLICY_VALUE = "RemoteDebuggingAllowed"

# Windows policy registry locations (hive, subkey). HKLM is machine-wide (the
# documented scope of this policy); HKCU is checked too for completeness.
_WIN_POLICY_KEYS = [
    (r"HKLM", r"SOFTWARE\Policies\Microsoft\Edge"),
    (r"HKLM", r"SOFTWARE\Policies\Google\Chrome"),
    (r"HKCU", r"SOFTWARE\Policies\Microsoft\Edge"),
    (r"HKCU", r"SOFTWARE\Policies\Google\Chrome"),
]

# Linux managed-policy JSON directories (Chromium reads every *.json here).
_LINUX_POLICY_GLOBS = [
    "/etc/opt/chrome/policies/managed/*.json",
    "/etc/opt/edge/policies/managed/*.json",
    "/etc/chromium/policies/managed/*.json",
]


@dataclass
class PolicyResult:
    """Result of reading ``RemoteDebuggingAllowed``.

    ``allowed`` is ``None`` when the policy is unset — which is PERMITTED per the
    Chromium default, distinct from an explicit ``True``.
    """

    allowed: Optional[bool]
    source: str  # "registry:HKLM\\...", "policy-file:/etc/...", "unset", "unreadable"
    sources_checked: List[str] = field(default_factory=list)

    @property
    def forbids_debugging(self) -> bool:
        return self.allowed is False


@dataclass
class TierDecision:
    tier: int                 # 1 | 2 | 3
    name: str                 # "cdp" | "selenium" | "http-only"
    reason: str
    policy_allowed: Optional[bool]
    browser_present: bool
    requested: str            # "auto" | "cdp" | "selenium"
    lost_at_this_tier: List[str] = field(default_factory=list)


# ── Policy read (deterministic, no browser launch) ────────────────────────────


def _read_windows_policy() -> PolicyResult:
    checked: List[str] = []
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return PolicyResult(allowed=None, source="unset", sources_checked=checked)

    hive_map = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    for hive_name, subkey in _WIN_POLICY_KEYS:
        where = f"registry:{hive_name}\\{subkey}"
        checked.append(where)
        try:
            with winreg.OpenKey(hive_map[hive_name], subkey) as k:
                val, _ = winreg.QueryValueEx(k, _POLICY_VALUE)
                # An explicit 0 forbids; any explicit non-zero permits.
                return PolicyResult(allowed=bool(val), source=where, sources_checked=checked)
        except OSError:
            continue
    return PolicyResult(allowed=None, source="unset", sources_checked=checked)


def _read_linux_policy() -> PolicyResult:
    checked: List[str] = []
    for pattern in _LINUX_POLICY_GLOBS:
        for path in sorted(glob.glob(pattern)):
            checked.append(f"policy-file:{path}")
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if _POLICY_VALUE in data:
                return PolicyResult(
                    allowed=bool(data[_POLICY_VALUE]),
                    source=f"policy-file:{path}",
                    sources_checked=checked,
                )
    return PolicyResult(allowed=None, source="unset", sources_checked=checked)


def read_remote_debugging_policy() -> PolicyResult:
    """Read ``RemoteDebuggingAllowed`` from the platform's policy store.

    Returns ``allowed=None`` (unset → permitted) when no policy is configured —
    the state on an unmanaged workstation and the documented Chromium default.
    """
    if platform.system() == "Windows":
        return _read_windows_policy()
    return _read_linux_policy()


# ── Browser presence (reuses the existing detectors; no new logic) ────────────


def detect_browser_present() -> bool:
    """True if any Chromium-family browser is detected. Delegates to
    driver_manager's already-shipped version detectors — no duplicate discovery."""
    try:
        from tools.browser.driver_manager import _detect_chrome_version, _detect_edge_version
    except Exception:  # noqa: BLE001 - detectors absent → treat as no browser
        return False
    return bool(_detect_edge_version() or _detect_chrome_version())


# ── Tier selection ────────────────────────────────────────────────────────────

# What a browser-free (Tier 3) run cannot do — stated, not discovered.
_LOST_AT_TIER3 = [
    "agent browser (rendered-DOM element indexing)",
    "a11y_sweep.py (accessibility tree)",
    "visual regression / screenshot validation",
    "any e2e assertion that needs a rendered DOM",
]


def select_tier(
    policy: PolicyResult,
    browser_present: bool,
    requested: str = "auto",
) -> TierDecision:
    """Pick the tier from policy + browser presence.

    ``requested``:
      * ``auto``     — CDP → (Tier 3 if CDP impossible). Never silently picks
                       Selenium; Tier 2 is opt-in only.
      * ``cdp``      — demand Tier 1; raise via reason if policy/browser forbid it.
      * ``selenium`` — demand Tier 2 (compatibility). Still impossible if policy
                       forbids debugging (Selenium drives over CDP too).
    """
    allowed = policy.allowed
    forbids = policy.forbids_debugging

    # Policy forbids remote debugging → BOTH transports are dead. Only Tier 3.
    if forbids:
        return TierDecision(
            tier=3, name="http-only",
            reason=(
                f"{_POLICY_VALUE}=0 ({policy.source}) forbids remote debugging machine-wide; "
                "this blocks CDP AND Selenium (chromedriver drives over CDP too). "
                "Falling back to browser-free HTTP verification."
            ),
            policy_allowed=allowed, browser_present=browser_present, requested=requested,
            lost_at_this_tier=list(_LOST_AT_TIER3),
        )

    # Debugging permitted (explicit True or unset default) but no browser present.
    if not browser_present:
        return TierDecision(
            tier=3, name="http-only",
            reason=(
                "no Chromium-family browser detected; neither CDP nor Selenium can "
                "launch one. Falling back to browser-free HTTP verification."
            ),
            policy_allowed=allowed, browser_present=browser_present, requested=requested,
            lost_at_this_tier=list(_LOST_AT_TIER3),
        )

    permitted_note = "permitted (unset default)" if allowed is None else "explicitly permitted"

    if requested == "selenium":
        return TierDecision(
            tier=2, name="selenium",
            reason=(
                f"Tier 2 requested; remote debugging {permitted_note}. Selenium is a "
                "compatibility option (W3C-WebDriver audits / CDP-client escape hatch), "
                "and additionally needs a version-matched driver binary."
            ),
            policy_allowed=allowed, browser_present=browser_present, requested=requested,
        )

    # auto or explicit cdp → Tier 1.
    return TierDecision(
        tier=1, name="cdp",
        reason=(
            f"remote debugging {permitted_note} and a Chromium-family browser is present; "
            "CDP selected — no driver binary, no download."
        ),
        policy_allowed=allowed, browser_present=browser_present, requested=requested,
    )


def preflight(requested: str = "auto") -> Dict[str, Any]:
    """One-shot: read policy, detect a browser, choose a tier, return a report."""
    policy = read_remote_debugging_policy()
    browser_present = detect_browser_present()
    decision = select_tier(policy, browser_present, requested=requested)
    logger.info(
        "[cdp preflight] tier=%d (%s) requested=%s policy_allowed=%s browser=%s",
        decision.tier, decision.name, requested, policy.allowed, browser_present,
    )
    return {
        "platform": platform.system(),
        "policy": asdict(policy),
        "browser_present": browser_present,
        "decision": asdict(decision),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="CDP policy/tier preflight (cdp-port-06)")
    parser.add_argument(
        "--requested", choices=["auto", "cdp", "selenium"], default="auto",
        help="Requested backend; 'auto' (default) resolves CDP -> Tier 3.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    parser.add_argument(
        "--gate", action="store_true",
        help="Exit non-zero when the selected tier is 3 (no browser transport available).",
    )
    ns = parser.parse_args()

    report = preflight(requested=ns.requested)
    print(json.dumps(report, indent=2))
    if ns.gate and report["decision"]["tier"] == 3:
        sys.exit(1)
