# CUI // SP-CTI
"""Meta-test (shx-safe-04): every Genesis reflex module is registered or explicitly exempt.

The Genesis daemon dispatches ONLY the reflexes named in
``tools/genesis/daemon.py`` REFLEX_NAMES (see DaemonBase.run_due_reflexes:
a reflex with no schedule entry is silently skipped, and only names in
REFLEX_NAMES are ever scheduled).  A reflex module dropped into
``tools/genesis/reflexes/`` but never added to REFLEX_NAMES is DEAD CODE —
it never runs and nothing warns you.  That is exactly how the security
reflexes ``sdc_control_expiry`` and ``cato_monitor`` sat dormant.

This test enumerates every importable top-level reflex module and asserts it is
either dispatched (in REFLEX_NAMES) or listed in EXEMPT below with a reason.
Purpose: any FUTURE reflex added without registration fails CI — forcing an
explicit register-or-exempt decision.

Scope note: EXEMPT documents the state at the time shx-safe-04 landed.  Many
entries are canvas/domain reflexes present in ``reflex_registry.REGISTRY`` but
intentionally outside the daemon's smaller REFLEX_NAMES dispatch subset, or are
invoked on-demand by other subsystems.  Entries marked "unverified — inherited
exemption" are grandfathered: they appear unwired and were out of scope for
shx-safe-04, but the guard is now in place so they cannot silently multiply.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.genesis.daemon import REFLEX_NAMES  # noqa: E402

# ---------------------------------------------------------------------------
# The two security reflexes registered by shx-safe-04 — asserted live below.
# ---------------------------------------------------------------------------
_SHX_SAFE_04_REGISTERED = ("sdc_control_expiry", "cato_monitor")

# ---------------------------------------------------------------------------
# The two BDC reflexes registered by bdr-ops-1 — asserted live below.
# Previously EXEMPT (registered-but-undispatched); now wired into REFLEX_NAMES.
# ---------------------------------------------------------------------------
_BDR_OPS_1_REGISTERED = ("bdc_isa_expiry", "cato_twin")

# ---------------------------------------------------------------------------
# Modules deliberately NOT in the daemon's REFLEX_NAMES dispatch subset.
# Each MUST carry a one-line reason.  A new reflex is expected to be REGISTERED,
# not added here — add to EXEMPT only for genuinely non-daemon-scheduled modules.
# ---------------------------------------------------------------------------
EXEMPT: dict[str, str] = {
    # --- In reflex_registry.REGISTRY but outside the daemon dispatch subset ---
    # (canvas/domain/strategos reflexes; scheduled via their canvas or tier-gated)
    "dat_refresh": "reflex_registry STRATEGOS (tier-gated) + invoked by tools/dat/dti_update_runner.py; not daemon-scheduled",
    "govcon_scan": "reflex_registry DOMAIN — GovCon/SAM.gov scan; outside daemon REFLEX_NAMES subset",
    "migration_intel": "reflex_registry DOMAIN — migration intelligence harvester; outside daemon subset",
    "socmint": "reflex_registry DOMAIN OSINT (STRATEGOS-class); seed-referenced; outside daemon subset",
    "mcip_dti_scorer": "reflex_registry DOMAIN — MCIP DTI scorer; outside daemon subset",
    "quality": "reflex_registry SUPPORT; invoked on-demand by qdc_canvas blueprint + oracle quality lens via run_reflex(), not daemon-scheduled",
    "gepa_optimizer": "reflex_registry SUPPORT; also standalone MCP tool/skill (tools/skills/gepa_optimizer.py) invoked on-demand",
    "failure_triage": "reflex_registry SUPPORT; outside daemon dispatch subset",
    "fathomdesk_news_patterns": "reflex_registry SUPPORT; FathomDesk domain reflex invoked by scheduler script, not daemon-scheduled",
    "fathomdesk_correlation_monitor": "reflex_registry SUPPORT; FathomDesk domain reflex; outside daemon subset",
    "fathomdesk_trap_sweep": "reflex_registry DOMAIN; FathomDesk domain reflex; outside daemon subset",
    "fathomdesk_openbb_refresh": "reflex_registry DOMAIN; FathomDesk domain reflex; outside daemon subset",
    "fathomdesk_fundamentals_sweep": "reflex_registry DOMAIN; FathomDesk domain reflex; outside daemon subset",
    "fathomdesk_pc_ratio": "FathomDesk put/call-ratio domain reflex; not in reflex_registry or daemon subset; unverified — inherited exemption",
    "wf_feedback_aggregation": "reflex_registry SUPPORT; workflow-canvas HITL feedback aggregation; outside daemon subset",
    "wf_ext_poller": "reflex_registry SUPPORT; workflow external-step poller; outside daemon subset",
    "circuit_capacity_monitor": "reflex_registry DOMAIN; CCC network canvas reflex; outside daemon subset",
    "nocc_alarm_triage": "reflex_registry DOMAIN; NOCC network canvas reflex; outside daemon subset",
    "nocc_sla_watcher": "reflex_registry DOMAIN; NOCC network canvas reflex; outside daemon subset",
    "bgp_route_monitor": "reflex_registry DOMAIN; NOCC/BGP network canvas reflex; outside daemon subset",
    "peering_health_monitor": "reflex_registry DOMAIN; PMC peering network canvas reflex; outside daemon subset",
    "bgp_alerter_ingest": "reflex_registry DOMAIN; NOCC BGPalerter ingest; outside daemon subset",
    "peering_agreement_renewal": "reflex_registry DOMAIN; network peering renewal; outside daemon subset",
    "xc_order_poller": "reflex_registry DOMAIN; CCC cross-connect order poller; outside daemon subset",
    # --- Invoked on-demand by other subsystems (not daemon-scheduled) ---
    "oracle_triage": "on-demand — invoked by tools/foundry/oracle_verifiers.py (Foundry oracle pipeline), not daemon-scheduled",
    # --- Referenced only by kanban/db seeds (task placeholders); not wired to any dispatcher ---
    "aadc_reflex": "seed-referenced only (tools/kanban/seed_aadc_enhancement.py); AADC canvas reflex not wired into daemon; unverified — inherited exemption",
    "aimc_orphan_refs": "seed-referenced only (tools/kanban/seed_aadc_aimc_appmigration.py); not wired into daemon; unverified — inherited exemption",
    "cyber_feed_refresh": "seed-referenced only (tools/db/seeds/seed_sg_cyber_ext.py); not wired into daemon; unverified — inherited exemption",
    "sim_training_export": "referenced only by a DB migration seed (130_genesis_audit_log); not wired into daemon; unverified — inherited exemption",
    # --- No dispatcher/import references found; self-labeled canvas reflexes that appear unwired ---
    # (out of scope for shx-safe-04 — the guard now prevents further silent additions)
    # NOTE: forge_academy_oracle was deleted in penta-aca-06 (doubly dead: never in
    # REFLEX_NAMES and queried the wrong table). Its replacement, academy_oracle_reflex,
    # IS registered in REFLEX_NAMES, so it needs no exemption here.
    "gameday_orchestrator": "no references found; AI GameDay orchestrator appears unwired; unverified — inherited exemption",
    "govchain_anchor": "no references found; GovChain Merkle-anchor reflex appears unwired; unverified — inherited exemption",
    "idc_cloud_drift": "no references found; IDC canvas reflex appears unwired; unverified — inherited exemption",
    "mdc_cutover_countdown": "no references found; MDC canvas reflex appears unwired; unverified — inherited exemption",
    "qdc_gate_breach": "no references found; QDC canvas reflex appears unwired; unverified — inherited exemption",
}

_REFLEX_DIR = Path(__file__).resolve().parents[1] / "tools" / "genesis" / "reflexes"


def _top_level_reflex_modules() -> list[str]:
    """Enumerate importable top-level reflex module names.

    Skips ``__init__``, private modules (leading underscore), and subpackages
    (e.g. ``strategos/`` — its members are dispatched under dotted names via
    reflex_registry, not as top-level daemon reflexes).
    """
    names = []
    for py in sorted(_REFLEX_DIR.glob("*.py")):
        stem = py.stem
        if stem == "__init__" or stem.startswith("_"):
            continue
        names.append(stem)
    return names


def test_reflex_dir_exists():
    assert _REFLEX_DIR.is_dir(), f"reflex dir not found: {_REFLEX_DIR}"
    assert _top_level_reflex_modules(), "no reflex modules enumerated — glob/path bug"


def test_every_reflex_registered_or_exempt():
    """Core guard: no reflex module is silently unregistered."""
    unaccounted = []
    for name in _top_level_reflex_modules():
        if name in REFLEX_NAMES:
            continue
        if name in EXEMPT:
            continue
        unaccounted.append(name)
    assert not unaccounted, (
        "Reflex module(s) neither in tools/genesis/daemon.py REFLEX_NAMES nor in "
        f"EXEMPT: {unaccounted}. A reflex not in REFLEX_NAMES is NEVER dispatched. "
        "Register it (REFLEX_NAMES + args/genesis_config.yaml schedule) or add it to "
        "EXEMPT with a one-line reason explaining why it is not daemon-scheduled."
    )


def test_shx_safe_04_reflexes_registered():
    """The two security reflexes this task revived must be dispatched, not exempt."""
    for name in _SHX_SAFE_04_REGISTERED:
        assert name in REFLEX_NAMES, f"'{name}' missing from REFLEX_NAMES (shx-safe-04 regression)"
        assert name not in EXEMPT, f"'{name}' must be dispatched, not exempt"
        assert (_REFLEX_DIR / f"{name}.py").is_file(), f"module file for '{name}' missing"


def test_exempt_and_registered_are_disjoint():
    """A module cannot be both dispatched and exempt."""
    overlap = sorted(set(EXEMPT) & set(REFLEX_NAMES))
    assert not overlap, f"names in BOTH EXEMPT and REFLEX_NAMES: {overlap}"


def test_no_stale_exemptions():
    """Every EXEMPT entry must correspond to a real module file (no rot)."""
    existing = set(_top_level_reflex_modules())
    stale = sorted(n for n in EXEMPT if n not in existing)
    assert not stale, (
        f"EXEMPT references module(s) that no longer exist: {stale}. "
        "Remove the stale exemption(s)."
    )


def test_every_exemption_has_reason():
    """Guard against blank exemption reasons."""
    blank = sorted(n for n, r in EXEMPT.items() if not (r or "").strip())
    assert not blank, f"EXEMPT entries missing a reason: {blank}"


def test_registered_reflexes_are_importable_with_run():
    """The two revived reflexes must import and expose a callable run()."""
    import importlib

    for name in _SHX_SAFE_04_REGISTERED:
        mod = importlib.import_module(f"tools.genesis.reflexes.{name}")
        assert callable(getattr(mod, "run", None)), f"'{name}' has no callable run()"


# ---------------------------------------------------------------------------
# bdr-ops-1: the three BDC reflexes must be dispatched, not dormant.
# ---------------------------------------------------------------------------
_BDR_OPS_1_ALL = _BDR_OPS_1_REGISTERED + ("cato_monitor",)


def test_bdr_ops_1_reflexes_registered():
    """bdc_isa_expiry, cato_twin, and cato_monitor must all be dispatched."""
    reflex_dir = _REFLEX_DIR
    for name in _BDR_OPS_1_ALL:
        assert name in REFLEX_NAMES, f"'{name}' missing from REFLEX_NAMES (bdr-ops-1 regression)"
        assert name not in EXEMPT, f"'{name}' must be dispatched, not exempt"
        assert (reflex_dir / f"{name}.py").is_file(), f"module file for '{name}' missing"


def test_bdr_ops_1_reflexes_importable_with_run():
    """The three BDC reflexes must import and expose a callable run()."""
    import importlib

    for name in _BDR_OPS_1_ALL:
        mod = importlib.import_module(f"tools.genesis.reflexes.{name}")
        assert callable(getattr(mod, "run", None)), f"'{name}' has no callable run()"


def test_bdr_ops_1_config_blocks_have_required_keys():
    """genesis_config.yaml must carry schedule-bearing blocks for the BDC reflexes.

    The daemon schedules a reflex ONLY when its config block has a parseable
    ``schedule`` (tools/daemon/base.py). A registered name with no config block
    is silently never scheduled — so assert the blocks parse with the keys the
    daemon and this task require.
    """
    import yaml

    cfg_path = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    reflexes = cfg.get("reflexes", {})

    required = {"enabled", "risk_tier", "schedule", "description", "success_metric"}
    expected_cadence = {
        "bdc_isa_expiry": ("every 24h", True),
        "cato_twin": ("every 6h", True),  # bdr-vv-2: enabled after operational smoke
        "cato_monitor": ("every 6h", True),
    }
    for name, (schedule, enabled) in expected_cadence.items():
        block = reflexes.get(name)
        assert isinstance(block, dict), f"'{name}' has no config block in genesis_config.yaml"
        missing = required - set(block)
        assert not missing, f"'{name}' config block missing keys: {sorted(missing)}"
        assert block["schedule"] == schedule, (
            f"'{name}' schedule is {block['schedule']!r}, expected {schedule!r}"
        )
        assert block["enabled"] is enabled, (
            f"'{name}' enabled is {block['enabled']!r}, expected {enabled!r}"
        )
