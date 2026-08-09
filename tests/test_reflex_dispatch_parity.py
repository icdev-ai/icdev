# CUI // SP-CTI
"""Meta-test (hgx-obs-02): daemon.REFLEX_NAMES and genesis_config.yaml must agree.

A Genesis reflex needs BOTH halves to run, and neither half warns you when the
other is missing:

* ``tools/genesis/daemon.py`` ``REFLEX_NAMES`` — ``DaemonBase.__init__`` only
  builds schedule/state entries for names in this list.
* the ``reflexes:`` block of ``args/genesis_config.yaml`` — ``__init__`` reads
  ``config["reflexes"][name]["schedule"]``, and when there is no block there is
  no schedule.  ``run_due_reflexes`` then does ``if not schedule: continue`` —
  a bare skip, every cycle, forever, with nothing logged.

So a name in REFLEX_NAMES with no config block is **as dead as an unregistered
module**, and a config block with no REFLEX_NAMES entry is a block nobody reads.
``tests/test_reflex_registration.py`` guards module-vs-REFLEX_NAMES; this file
guards REFLEX_NAMES-vs-config, the other half of the same trap.

Measured on 2026-08-09, before this task: 88 names, 85 config blocks, and the
two sets differed in **31** places.  ``gepa_optimizer``'s own docstring said
"Runs every 24 hours via the genesis daemon" while it appeared in neither.

Baselines below are frozen.  They may SHRINK, never grow — the same discipline
``_UNVERIFIED_BASELINE`` applies in ``test_reflex_registration.py``.  Fixing one
means giving the reflex a real config block (or removing the dead entry), then
deleting its line here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.daemon.base import parse_schedule  # noqa: E402
from tools.genesis.daemon import REFLEX_NAMES  # noqa: E402
from tests.test_reflex_registration import EXEMPT  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _REPO_ROOT / "args" / "genesis_config.yaml"
_REFLEX_DIR = _REPO_ROOT / "tools" / "genesis" / "reflexes"

# ---------------------------------------------------------------------------
# hgx-obs-02: the self-improvement flywheel.  These three were registered (or,
# for gepa_optimizer, only catalogued in reflex_registry) with no config block,
# so reflexion → evolution → GEPA never ran a single cycle.  They must stay in
# BOTH halves, enabled, on a parseable schedule.
# ---------------------------------------------------------------------------
_HGX_OBS_02_REGISTERED = ("gepa_optimizer", "reflexion_loop", "evolution")

# ---------------------------------------------------------------------------
# Frozen 2026-08-09.  In REFLEX_NAMES, no config block → never scheduled.
# Each needs a schedule/risk_tier/success_metric block written by someone who
# knows its cadence; hgx-obs-02 fixed the three above rather than guessing at
# seventeen.  Shrink this list; never add to it.
# ---------------------------------------------------------------------------
_MISSING_CONFIG_BASELINE = frozenset({
    "ace_skill_promoter",
    "ace_team_monitor",
    "aidp_monitor",
    "commitment_watch_reflex",
    "daily_briefing_reflex",
    "dic_digest",
    "freshness_guardian",
    "inspect_adapt",
    "meeting_prep_reflex",
    "memory_maintenance_reflex",
    "nightly_prep_reflex",
    "objective_tracker_reflex",
    "pma_credential_monitor",
    "pma_int_gap_monitor",
    "redaction_scan_reflex",
    "thought_leadership_reflex",
    "weekly_retro_reflex",
})

# ---------------------------------------------------------------------------
# Frozen 2026-08-09.  Config blocks for names that were removed from
# REFLEX_NAMES because no module was ever written for them (see the `rri:`
# comment in daemon.py).  The block is inert: nothing reads it.  Config-only
# names that DO have a module are covered by test_reflex_registration.EXEMPT
# instead, which already records a measured blocker for each — this list is
# only for the ones with no module at all.
# ---------------------------------------------------------------------------
_CONFIG_ONLY_ORPHANS = frozenset({
    "aadc_compliance",
    "cost_optimizer",
    "goal_learner",
    "oracle",
    "remediation_lens",
})


def _config_reflexes() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("reflexes", {}) or {}


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------
def test_config_is_readable():
    reflexes = _config_reflexes()
    assert reflexes, f"no reflexes: block parsed from {_CONFIG_PATH}"
    assert REFLEX_NAMES, "REFLEX_NAMES is empty"


# ---------------------------------------------------------------------------
# Direction A — registered but unschedulable
# ---------------------------------------------------------------------------
def test_every_registered_reflex_has_a_config_block():
    """A REFLEX_NAMES entry with no config block is never scheduled."""
    reflexes = _config_reflexes()
    missing = sorted(n for n in REFLEX_NAMES if n not in reflexes)
    unexpected = sorted(set(missing) - _MISSING_CONFIG_BASELINE)
    assert not unexpected, (
        f"reflex(es) in REFLEX_NAMES with no args/genesis_config.yaml block: {unexpected}. "
        "DaemonBase.__init__ builds no schedule entry for them and run_due_reflexes "
        "skips them silently on every cycle — registering the name alone does nothing. "
        "Add a block with enabled/risk_tier/schedule/description/success_metric."
    )


def test_missing_config_baseline_has_not_rotted():
    """The grandfathered set may only shrink — a fixed reflex must leave the list."""
    reflexes = _config_reflexes()
    fixed = sorted(n for n in _MISSING_CONFIG_BASELINE if n in reflexes)
    assert not fixed, (
        f"these now HAVE a config block: {fixed}. Remove them from "
        "_MISSING_CONFIG_BASELINE so the guard keeps tightening."
    )
    gone = sorted(n for n in _MISSING_CONFIG_BASELINE if n not in REFLEX_NAMES)
    assert not gone, (
        f"_MISSING_CONFIG_BASELINE names no longer in REFLEX_NAMES: {gone}. "
        "Remove the stale entries."
    )


# ---------------------------------------------------------------------------
# Direction B — configured but never dispatched
# ---------------------------------------------------------------------------
def test_every_config_block_is_dispatched_or_accounted_for():
    """A config block for a name outside REFLEX_NAMES is read by nobody."""
    reflexes = _config_reflexes()
    orphaned = sorted(set(reflexes) - set(REFLEX_NAMES))
    unexplained = sorted(set(orphaned) - set(EXEMPT) - _CONFIG_ONLY_ORPHANS)
    assert not unexplained, (
        f"config block(s) for reflex(es) absent from REFLEX_NAMES: {unexplained}. "
        "The daemon only ever looks up config for names in REFLEX_NAMES, so these "
        "blocks are inert. Either register the name, or record why it is not "
        "daemon-scheduled in tests/test_reflex_registration.py EXEMPT."
    )


def test_config_only_orphans_really_have_no_module():
    """_CONFIG_ONLY_ORPHANS is for blocks whose reflex module was never written.

    A config-only name that DOES have a module belongs in EXEMPT with a measured
    blocker, not here — otherwise a real, writable reflex hides behind a label.
    """
    with_module = sorted(n for n in _CONFIG_ONLY_ORPHANS if (_REFLEX_DIR / f"{n}.py").is_file())
    assert not with_module, (
        f"these have a reflex module and so are not orphan blocks: {with_module}. "
        "Move them to tests/test_reflex_registration.py EXEMPT with a concrete "
        "blocker, or register them."
    )


def test_config_only_orphans_are_still_orphans():
    """Shrink-only: an orphan that got registered must leave the list."""
    reflexes = _config_reflexes()
    now_registered = sorted(n for n in _CONFIG_ONLY_ORPHANS if n in REFLEX_NAMES)
    assert not now_registered, (
        f"these are now dispatched: {now_registered}. Remove them from _CONFIG_ONLY_ORPHANS."
    )
    gone = sorted(n for n in _CONFIG_ONLY_ORPHANS if n not in reflexes)
    assert not gone, (
        f"_CONFIG_ONLY_ORPHANS names with no config block left: {gone}. Remove the stale entries."
    )


def test_baselines_are_disjoint():
    """A name cannot be both 'registered without config' and 'config without registration'."""
    overlap = sorted(_MISSING_CONFIG_BASELINE & _CONFIG_ONLY_ORPHANS)
    assert not overlap, f"names in BOTH parity baselines: {overlap}"


# ---------------------------------------------------------------------------
# hgx-obs-02 — the three reflexes this task revived
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", _HGX_OBS_02_REGISTERED)
def test_hgx_obs_02_reflex_is_dispatched_on_its_cadence(name):
    """Registered, configured, enabled, and on a schedule the daemon can parse."""
    assert name in REFLEX_NAMES, f"'{name}' missing from REFLEX_NAMES (hgx-obs-02 regression)"
    assert name not in EXEMPT, f"'{name}' must be dispatched, not exempt"
    assert (_REFLEX_DIR / f"{name}.py").is_file(), f"module file for '{name}' missing"

    block = _config_reflexes().get(name)
    assert isinstance(block, dict), f"'{name}' has no config block in genesis_config.yaml"
    missing = {"enabled", "risk_tier", "schedule", "description", "success_metric"} - set(block)
    assert not missing, f"'{name}' config block missing keys: {sorted(missing)}"
    assert block["enabled"] is True, f"'{name}' is registered but enabled is {block['enabled']!r}"
    assert parse_schedule(block["schedule"]), (
        f"'{name}' schedule {block['schedule']!r} does not parse — it would be registered, "
        "configured, and still never dispatched"
    )


@pytest.mark.parametrize("name", _HGX_OBS_02_REGISTERED)
def test_hgx_obs_02_reflex_honours_the_daemon_envelope(name):
    """The daemon reads ``result["success"]``; a module without it scores every
    cycle a failure and trips its breaker in three.  gepa_optimizer returned
    ``{"status": "ok", ...}`` before this task — registering it without fixing
    the envelope would have swapped one silent failure for another."""
    import importlib
    import inspect

    mod = importlib.import_module(f"tools.genesis.reflexes.{name}")
    run = getattr(mod, "run", None)
    assert callable(run), f"'{name}' has no callable run()"
    assert len(inspect.signature(run).parameters) >= 2, (
        f"'{name}'.run must accept (config, trust) — the daemon passes both positionally"
    )
    assert '"success"' in inspect.getsource(mod), (
        f"'{name}'.run never sets a 'success' key; DaemonBase reads "
        "result.get('success', False), so every run would be recorded as a failure"
    )
