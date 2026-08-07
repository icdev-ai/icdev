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

xbm-wake-02 note — "outside the daemon subset" was never a reason
=================================================================
Nine of these exemptions read like a design decision ("reflex_registry DOMAIN;
outside daemon subset") when they were really an unaudited default.  xbm-wake-02
took the eight flagged by xbm-wake-01 plus the one unaccounted module and
checked each against evidence rather than prose:

  * does anything actually call it (grep for a non-test, non-seed invoker)?
  * has it ever run (``genesis_reflex_state.total_runs``)?
  * would registering it even work — does ``run()`` exist with the
    ``(config, trust) -> {"success": ...}`` contract the daemon requires, and
    does its ``args/genesis_config.yaml`` block carry a parseable ``schedule``?

Measured on 2026-08-07: **none of the nine had a ``genesis_reflex_state`` row at
all** — zero runs, zero audit events, against 28,662 rows of ``genesis_audit``
for their registered peers.  Two were register-eligible and are now dispatched
(``govcon_scan``, ``idp_score_recorder``).  The other seven each hit a concrete,
named blocker recorded inline below.  ``_UNVERIFIED_BASELINE`` freezes the
remaining grandfathered set so the phrase cannot spread — see
``test_no_new_unverified_exemptions``.
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
# xbm-wake-02: the nine reflexes audited for register-vs-exempt on evidence.
# Two are now dispatched; the seven below them keep an exemption that names a
# concrete blocker instead of "outside daemon subset".
# ---------------------------------------------------------------------------
_XBM_WAKE_02_REGISTERED = ("idp_score_recorder",)

_XBM_WAKE_02_EXEMPTED = (
    "govcon_scan",
    "socmint",
    "failure_triage",
    "fathomdesk_trap_sweep",
    "fathomdesk_pc_ratio",
    "nocc_sla_watcher",
    "peering_agreement_renewal",
    "quality",
)

# The phrase xbm-wake-02 exists to eliminate. It may not appear on any reflex
# this task audited, and the set that still carries it may not grow.
_UNVERIFIED_MARKER = "unverified — inherited exemption"

# Frozen 2026-08-07: canvas/seed reflexes NOT audited by xbm-wake-02 (its scope
# was the eight xbm-wake-01 flagged plus idp_score_recorder). Each still needs
# the same evidence pass. Shrink this list as they are verified; never grow it.
_UNVERIFIED_BASELINE = frozenset({
    "aadc_reflex",
    "aimc_orphan_refs",
    "cyber_feed_refresh",
    "sim_training_export",
    "gameday_orchestrator",
    "govchain_anchor",
    "idc_cloud_drift",
    "mdc_cutover_countdown",
    "qdc_gate_breach",
})

# ---------------------------------------------------------------------------
# Modules deliberately NOT in the daemon's REFLEX_NAMES dispatch subset.
# Each MUST carry a one-line reason.  A new reflex is expected to be REGISTERED,
# not added here — add to EXEMPT only for genuinely non-daemon-scheduled modules.
# ---------------------------------------------------------------------------
EXEMPT: dict[str, str] = {
    # --- In reflex_registry.REGISTRY but outside the daemon dispatch subset ---
    # (canvas/domain/strategos reflexes; scheduled via their canvas or tier-gated)
    "dat_refresh": "reflex_registry STRATEGOS (tier-gated) + invoked by tools/dat/dti_update_runner.py; not daemon-scheduled",
    "migration_intel": "reflex_registry DOMAIN — migration intelligence harvester; outside daemon subset",
    "mcip_dti_scorer": "reflex_registry DOMAIN — MCIP DTI scorer; outside daemon subset",
    "gepa_optimizer": "reflex_registry SUPPORT; also standalone MCP tool/skill (tools/skills/gepa_optimizer.py) invoked on-demand",
    "fathomdesk_news_patterns": "reflex_registry SUPPORT; FathomDesk domain reflex invoked by scheduler script, not daemon-scheduled",
    "fathomdesk_correlation_monitor": "reflex_registry SUPPORT; FathomDesk domain reflex; outside daemon subset",
    "fathomdesk_openbb_refresh": "reflex_registry DOMAIN; FathomDesk domain reflex; outside daemon subset",
    "fathomdesk_fundamentals_sweep": "reflex_registry DOMAIN; FathomDesk domain reflex; outside daemon subset",
    "wf_feedback_aggregation": "reflex_registry SUPPORT; workflow-canvas HITL feedback aggregation; outside daemon subset",
    "wf_ext_poller": "reflex_registry SUPPORT; workflow external-step poller; outside daemon subset",
    "circuit_capacity_monitor": "reflex_registry DOMAIN; CCC network canvas reflex; outside daemon subset",
    "nocc_alarm_triage": "reflex_registry DOMAIN; NOCC network canvas reflex; outside daemon subset",
    "bgp_route_monitor": "reflex_registry DOMAIN; NOCC/BGP network canvas reflex; outside daemon subset",
    "peering_health_monitor": "reflex_registry DOMAIN; PMC peering network canvas reflex; outside daemon subset",
    "bgp_alerter_ingest": "reflex_registry DOMAIN; NOCC BGPalerter ingest; outside daemon subset",
    "xc_order_poller": "reflex_registry DOMAIN; CCC cross-connect order poller; outside daemon subset",
    # --- xbm-wake-02: audited on evidence; each blocker is measured, not assumed ---
    "govcon_scan": (
        "no invoker — the ACE roles carrying `genesis_reflex: govcon_scan` only surface it "
        "as a display string, and apps/forge_academy/configurator.py's handler is an "
        "explicitly simulated lab that never calls the reflex. Blocked on runtime: "
        "scan_sam_gov() loops 8 NAICS x 4 notice types and fetches a description per "
        "opportunity, and a measured full scan ran past 30 minutes without returning — "
        "far over the 300 s defaults.reflex_timeout_seconds watchdog. Registering it would "
        "produce watchdog_timeout failures and trip its breaker in three days, and raising "
        "the cap to fit would stall a SEQUENTIAL daemon loop (heal every 5 min) for half an "
        "hour daily. Needs an owner decision on scan scope or a cap, not a guess. Its "
        "demand-signal return-shape bug — which made success=False unconditional — was "
        "fixed by xbm-wake-02 so the runtime question is the only one left."
    ),
    "quality": (
        "on-demand — POST /api/genesis/quality → tools/qdc_canvas/blueprint.py::"
        "api_genesis_quality calls quality.run_reflex(); also offered as a CLI by "
        "tools/oracle/lenses/lens_quality.py:492. NOT daemon-dispatchable: the module "
        "defines run_reflex(), not run(), so adding it to REFLEX_NAMES would fail "
        "check_reflex_registry and the daemon would fall through to stub mode."
    ),
    "nocc_sla_watcher": (
        "on-demand CLI — `python tools/genesis/reflexes/nocc_sla_watcher.py` (module has "
        "__main__), documented in docs/reference/commands.md and "
        ".agents/skills/icdev-noc/REFERENCE.md. Blocked for daemon dispatch: run() returns "
        "{'status': 'ok', ...} with no 'success' key, so DaemonBase would score every cycle "
        "a failure and trip its breaker in three; noc_sla_records is also empty."
    ),
    "peering_agreement_renewal": (
        "no invoker found (config block only). Blocked for daemon dispatch: run() returns "
        "no 'success' key, so every cycle would score a failure; nc_peering_agreements is "
        "empty, so it has nothing to check even if dispatched."
    ),
    "socmint": (
        "no invoker found. Blocked: its backing module tools.strategos.socmint_harvester "
        "does not exist anywhere in the tree, so run() returns success=False on every call. "
        "Its genesis_config.yaml block also has no 'schedule:' key, so DaemonBase.__init__ "
        "builds no schedule entry and run_due_reflexes would skip it even if registered."
    ),
    "failure_triage": (
        "no invoker — README claimed a live 30-min cadence; genesis_reflex_state has no row "
        "for it, so it has never run. Blocked twice: run() returns no 'success' key (every "
        "cycle would score a failure), and registration needs an owner decision on the "
        "cadence — risk_tier yellow, and ICDEV_AUTOFIX_ENABLED=true in the live .env means "
        "it would generate LLM patches and commit to autofix/* branches every 30 minutes."
    ),
    "fathomdesk_trap_sweep": (
        "no invoker outside tests. Blocked on PostgreSQL: ad_reflex_cooldowns does not exist "
        "and _mark_cooldown uses SQLite-only `INSERT OR REPLACE`, so _check_cooldown's "
        "except-branch returns True forever and the duplicate-suppression guard never "
        "engages; ad_signals is also empty, so a dispatched sweep would be inert."
    ),
    "fathomdesk_pc_ratio": (
        "no invoker; catalogued in reflex_registry by xbm-wake-02 (it was module + config "
        "block only). Blocked on PostgreSQL: its _DDL is SQLite-only (INTEGER PRIMARY KEY "
        "AUTOINCREMENT / datetime('now')) and ad_pc_ratio_history does not exist, so "
        "_persist_snapshot fails and run() returns success=False whenever the CBOE fetch "
        "actually succeeds."
    ),
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


# ---------------------------------------------------------------------------
# xbm-wake-02: the register-or-exempt decision must stay evidence-backed.
# ---------------------------------------------------------------------------


def test_xbm_wake_02_registered_reflexes_dispatch():
    """govcon_scan and idp_score_recorder must be dispatched, not exempt."""
    for name in _XBM_WAKE_02_REGISTERED:
        assert name in REFLEX_NAMES, f"'{name}' missing from REFLEX_NAMES (xbm-wake-02 regression)"
        assert name not in EXEMPT, f"'{name}' must be dispatched, not exempt"
        assert (_REFLEX_DIR / f"{name}.py").is_file(), f"module file for '{name}' missing"


def test_xbm_wake_02_registered_reflexes_honour_the_daemon_contract():
    """Registering is not enough — the daemon calls run(config, trust) and reads
    ``result["success"]``. A module without that key scores every cycle a failure
    and trips its own breaker in three, which is how several of the exempted
    reflexes in this file are blocked."""
    import importlib
    import inspect

    for name in _XBM_WAKE_02_REGISTERED:
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


def test_xbm_wake_02_registered_reflexes_have_a_parseable_schedule():
    """A REFLEX_NAMES entry with no parseable ``schedule:`` is never scheduled —
    DaemonBase.__init__ simply builds no entry and run_due_reflexes skips it.
    That is the second half of the trap socmint is still stuck in."""
    import yaml

    from tools.daemon.base import parse_schedule

    cfg_path = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        reflexes = (yaml.safe_load(fh) or {}).get("reflexes", {})

    for name in _XBM_WAKE_02_REGISTERED:
        block = reflexes.get(name)
        assert isinstance(block, dict), f"'{name}' has no config block in genesis_config.yaml"
        assert block.get("enabled") is True, f"'{name}' is registered but enabled is not True"
        assert parse_schedule(block.get("schedule", "")), (
            f"'{name}' schedule {block.get('schedule')!r} does not parse — it would be "
            "registered and still never dispatched"
        )


def test_govcon_scan_demand_signal_shape_is_fixed():
    """The bug that made govcon_scan unable to ever report success.

    ``aggregate_demand_signals``/``get_high_demand_signals`` are annotated ``-> dict``.
    The reflex iterated the returned dict, which yields str keys, so
    ``s.get("article_generated")`` raised AttributeError on every run, incremented
    ``errors``, and forced ``success = (errors == 0)`` to False. Registration was
    impossible while that stood; keep it fixed so the only open question is runtime.
    """
    src = (_REFLEX_DIR / "govcon_scan.py").read_text(encoding="utf-8")
    assert 'high.get("signals"' in src, (
        "govcon_scan must read high['signals']; get_high_demand_signals returns a dict"
    )
    assert "for s in high]" not in src and "for s in high " not in src, (
        "govcon_scan is iterating the demand-signal dict again — that yields str keys"
    )


def test_audited_exemptions_name_a_real_invoker_or_blocker():
    """No reflex xbm-wake-02 audited may still say 'unverified — inherited exemption'."""
    still_unverified = sorted(
        n for n in _XBM_WAKE_02_EXEMPTED if _UNVERIFIED_MARKER in EXEMPT.get(n, "")
    )
    assert not still_unverified, (
        f"xbm-wake-02 audited these but their reason is still a placeholder: "
        f"{still_unverified}. State the invoker or the concrete blocker."
    )
    for name in _XBM_WAKE_02_EXEMPTED:
        assert name in EXEMPT, f"'{name}' was audited as exempt but is not in EXEMPT"
        assert name not in REFLEX_NAMES, f"'{name}' is both exempt and dispatched"
        # A verified reason is a sentence, not a category label.
        assert len(EXEMPT[name]) >= 80, (
            f"'{name}' reason is too thin to be evidence: {EXEMPT[name]!r}"
        )


def test_no_new_unverified_exemptions():
    """The grandfathered set may shrink, never grow.

    ``_UNVERIFIED_BASELINE`` is the frozen 2026-08-07 list of canvas/seed reflexes
    that were outside xbm-wake-02's scope and still need the same evidence pass.
    A NEW name carrying the placeholder means someone grandfathered a reflex
    instead of deciding about it.
    """
    carrying = {n for n, r in EXEMPT.items() if _UNVERIFIED_MARKER in (r or "")}
    new = sorted(carrying - _UNVERIFIED_BASELINE)
    assert not new, (
        f"new 'unverified — inherited exemption' entries: {new}. Determine whether "
        "anything invokes the reflex and whether it can be dispatched, then record "
        "that — do not inherit the placeholder."
    )


def test_reflex_registry_no_longer_claims_to_be_authoritative():
    """reflex_registry.py dispatches nothing, so it must not say it does.

    Its docstring called itself the 'authoritative list of all reflexes' while no
    dispatcher imported it — which is how eight enabled reflexes with working
    modules sat at zero runs and nothing went red.
    """
    import tools.genesis.reflex_registry as registry

    doc = registry.__doc__ or ""
    # The summary line is what a reader skims and what tooling extracts.
    summary = doc.strip().splitlines()[0].lower()
    assert "authoritative" not in summary, (
        f"reflex_registry.py's summary line still claims authority: {summary!r}. It "
        "schedules nothing — dispatch needs daemon.REFLEX_NAMES plus a parseable "
        "schedule in args/genesis_config.yaml."
    )
    assert "schedules nothing" in doc.lower(), (
        "reflex_registry.py must state plainly that it does not drive dispatch"
    )
    assert "REFLEX_NAMES" in (registry.__doc__ or ""), (
        "the docstring must point readers at what actually dispatches"
    )


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
