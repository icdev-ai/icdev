#!/usr/bin/env python3
# CUI // SP-CTI
"""Capability consumption counter — the measurement half of "declared but never used".

ICDEV's signature defect is not a capability that *breaks*. It is a capability
that is registered, importable, catalogued, enabled — and never consumed, while
nothing goes red. Known instances:

  * the migration-149 audit hash chain: schema, verifier, bundle format and
    dashboard all shipped; the writer never populated ``hash``/``previous_hash``;
  * ``MCPToolAuthorizer``: zero call sites, yet ``fedramp_ksi_generator`` counted
    the *file's existence* as satisfied evidence;
  * ``prompt_registry``: 0 rows in ``prompt_versions``, 0 importers;
  * GEPA: dispatched every 24h with 7 successful runs on the board and zero
    recorded consumption, because the only outcome it could write was
    ``status='applied'`` — an artifact it declined stayed ``'pending'`` forever,
    so the queue could only grow and a correct decline was indistinguishable
    from never having run (rem-cap-01);
  * three separate reflex incidents (xbm-wake-01, xbm-wake-02, hgx-obs-02) where
    ``enabled: true`` + a working module + a catalogue entry produced zero
    executions.

This tool counts, per class of declared capability, how many declared units were
actually *consumed* over a configurable recent window.

Three design rules, each of which exists because breaking it is how the bug
above gets shipped:

1. **No new telemetry.** Every count comes from a table that already records the
   event: ``genesis_reflex_state`` (per-reflex runs), ``studio_mcp_dispatch_audit``
   (per-tool dispatch decisions), ``agent_approval_log`` (gate decisions),
   ``audit_platform`` (MCP authz verdicts), ``audit_trail`` (chain hashes),
   ``prompt_versions`` and ``agent_improvement_artifacts`` (registry rows). If a
   capability's use is not already recorded, that absence is the finding.

2. **"Unmeasurable" is never reported as zero.** A missing telemetry table, an
   unreadable declaration source, or a failed query sets
   ``telemetry_available: false`` with an ``unmeasured_reason`` — because a
   silent zero from a broken probe is indistinguishable from a real zero, and
   that ambiguity is precisely what let the five cases above survive.

3. **A nonzero count is not a failure.** ``--gate`` fails on *unmeasurable*
   classes, not on inert ones. Inertness is a finding for a human; an
   unmeasurable capability class is a defect in the measurement itself.

The same measurement runs one layer down, on SUBSTRATES — the tables, columns
and config blocks a capability is designed *against*. That half is probed on
demand against a named plan or diff, so a design built on an empty substrate is
reported before the code exists rather than after it ships answering "unknown"
forever. See the "Substrates" section below for why it is on-demand and not a
sweep.

CLI:
    python tools/awareness/capability_consumption.py --json
    python tools/awareness/capability_consumption.py --json --window-days 7
    python tools/awareness/capability_consumption.py --json --class reflex
    python tools/awareness/capability_consumption.py --known-inert --json
    python tools/awareness/capability_consumption.py --gate

    # Substrate half — run these BEFORE designing against a substrate
    python tools/awareness/capability_consumption.py --probe-plan plan.md --substrate-gate
    python tools/awareness/capability_consumption.py --probe-substrate kg_ontology
    python tools/awareness/capability_consumption.py --probe-diff origin/main --json
    python tools/awareness/capability_consumption.py --substrates
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

LOG = get_logger("capability_consumption")


def _repo_file(rel: str) -> Path:
    """Resolve a repo-root-relative path from either tree.

    This module is mirrored into ``icdev/tools/awareness/``, where ``BASE_DIR``
    resolves to ``<repo>/icdev`` — and ``icdev/args`` carries only 23 of the 327
    files in ``args/``, with no ``icdev/hardprompts`` at all. Falling back one
    level lets the mirrored copy read the same declaration sources instead of
    reporting half its classes unmeasurable, and avoids duplicating config files
    that would then drift (the failure mode ``args/llm_config.yaml`` already has
    three copies of). In a wheel install neither path exists, the caller sees no
    file, and the affected class reports unmeasurable — which is the truth.
    """
    local = BASE_DIR / rel
    return local if local.exists() else BASE_DIR.parent / rel


CONFIG_PATH = _repo_file("args/capability_consumption.yaml")
DEFAULT_WINDOW_DAYS = 30
DEFAULT_INERT_THRESHOLD = 0
DEFAULT_MAX_LISTED_UNITS = 40

# Guard for the identifiers interpolated into _count_by_key's SELECT.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class ClassResult:
    """One capability class: what is declared, and how much of it got used.

    ``declared``/``consumed``/``inert`` count capability *units*; ``events``
    counts consumption *occurrences*. A class with declared=24, consumed=7,
    events=1200 has 17 units nobody has touched in the window, however busy the
    other seven were — which is the number that matters here.
    """

    capability_class: str
    declaration_source: str = ""
    telemetry_table: str = ""
    telemetry_available: bool = False
    unmeasured_reason: Optional[str] = None
    declared: int = 0
    consumed: int = 0
    inert: int = 0
    events: int = 0
    inert_units: List[str] = field(default_factory=list)
    inert_units_truncated: bool = False
    top_consumed: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        pct = round(100.0 * self.consumed / self.declared, 1) if self.declared else 0.0
        return {
            "capability_class": self.capability_class,
            "description": self.description,
            "declaration_source": self.declaration_source,
            "telemetry_table": self.telemetry_table,
            "telemetry_available": self.telemetry_available,
            "unmeasured_reason": self.unmeasured_reason,
            "declared": self.declared,
            "consumed": self.consumed,
            "inert": self.inert,
            "consumed_pct": pct,
            "events": self.events,
            "inert_units": self.inert_units,
            "inert_units_truncated": self.inert_units_truncated,
            "top_consumed": self.top_consumed,
            "extra": self.extra,
        }


def _unmeasured(cls: str, source: str, table: str, reason: str) -> ClassResult:
    return ClassResult(
        capability_class=cls,
        declaration_source=source,
        telemetry_table=table,
        telemetry_available=False,
        unmeasured_reason=reason,
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load args/capability_consumption.yaml, falling back to built-in defaults."""
    path = path or CONFIG_PATH
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            return data
    except Exception as exc:  # noqa: BLE001 — config is advisory, defaults are safe
        LOG.warning("capability_consumption config unreadable (%s); using defaults", exc)
    return {}


# ---------------------------------------------------------------------------
# Window binding
#
# Timestamp columns are not uniform across this schema: audit_trail.created_at
# is a real PostgreSQL ``timestamp`` while every other source stores an ISO
# string in a TEXT column, and the TEXT ones are not even internally consistent
# ("2026-08-12T03:42:26+00:00" from Python's isoformat, "2026-08-12 03:42:26+00"
# from a PG-side write). A single hardcoded bound format silently drops rows on
# whichever half it does not match, so the bound is chosen from the column's
# declared type and the TEXT form uses a SPACE separator: ' ' (0x20) sorts below
# both 'T' and any digit, so a same-instant row is never excluded by separator
# choice. The worst case is sub-second over-inclusion at the boundary, which is
# the safe direction for a "was this used recently" count.
# ---------------------------------------------------------------------------


def _column_type(conn, table: str, column: str) -> Optional[str]:
    """Return the declared type of ``table.column``, or None if absent."""
    from tools.db.storage import is_pg

    try:
        if is_pg(conn):
            row = conn.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
            return dict(row)["data_type"] if row else None
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
            info = dict(row)
            if str(info.get("name")) == column:
                return str(info.get("type") or "")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("column type lookup failed for %s.%s: %s", table, column, exc)
        _rollback(conn)
    return None


def _window_bound(conn, table: str, column: str, since: datetime) -> Any:
    """Bind value for ``column >= since`` that matches the column's storage type."""
    declared = (_column_type(conn, table, column) or "").lower()
    if "timestamp" in declared or "date" in declared:
        return since
    return since.strftime("%Y-%m-%d %H:%M:%S.%f%z")


def _rollback(conn) -> None:
    """Clear an aborted PostgreSQL transaction.

    Without this, one failed probe poisons the connection and every *later*
    probe reports "relation does not exist" for tables that are present — a
    measurement tool reporting phantom zeroes, which is the exact failure it
    was written to detect.
    """
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass


def _count_by_key(
    conn,
    table: str,
    key_column: str,
    ts_column: str,
    since: datetime,
    extra_sql: str = "",
    extra_params: Tuple[Any, ...] = (),
) -> Dict[str, int]:
    """Return {key: event_count} for rows in ``table`` newer than ``since``.

    Table and column names are interpolated because SQL has no placeholder for
    an identifier. Every caller passes a module-level literal, and the check
    below keeps it that way — the values are asserted to be plain identifiers so
    a future caller cannot turn this into an injection point by threading user
    input through. Only the window bound and ``extra_params`` are data, and
    those are parameterized.
    """
    for ident in (table, key_column, ts_column):
        if not _IDENTIFIER_RE.fullmatch(ident):
            raise ValueError(f"not a plain SQL identifier: {ident!r}")
    bound = _window_bound(conn, table, ts_column, since)
    sql = (
        f"SELECT {key_column} AS k, COUNT(*) AS n FROM {table} "
        f"WHERE {ts_column} IS NOT NULL AND {ts_column} >= %s {extra_sql} "
        f"GROUP BY {key_column}"
    )
    rows = conn.execute(sql, (bound,) + tuple(extra_params)).fetchall()
    counts: Dict[str, int] = {}
    for row in rows:
        rec = dict(row)
        counts[str(rec.get("k") or "")] = int(rec.get("n") or 0)
    return counts


def _finish(
    result: ClassResult,
    declared_units: List[str],
    counts: Dict[str, int],
    threshold: int,
    max_listed: int,
) -> ClassResult:
    """Fold declared units against observed event counts into a ClassResult."""
    declared_units = sorted(set(declared_units))
    result.declared = len(declared_units)
    inert = [u for u in declared_units if counts.get(u, 0) <= threshold]
    result.inert = len(inert)
    result.consumed = result.declared - result.inert
    result.events = sum(counts.get(u, 0) for u in declared_units)
    result.inert_units = inert[:max_listed]
    result.inert_units_truncated = len(inert) > max_listed
    result.top_consumed = [
        {"unit": u, "events": counts[u]}
        for u in sorted(
            (u for u in declared_units if counts.get(u, 0) > threshold),
            key=lambda x: (-counts.get(x, 0), x),
        )[:10]
    ]
    # Events recorded against units nobody declared. Not an error — it means the
    # declaration source and the telemetry disagree about what exists, which is
    # worth surfacing rather than silently dropping.
    undeclared = sorted(set(counts) - set(declared_units))
    if undeclared:
        result.extra["undeclared_units_observed"] = undeclared[:max_listed]
        result.extra["undeclared_units_count"] = len(undeclared)
    result.telemetry_available = True
    return result


# ---------------------------------------------------------------------------
# Probes — one per capability class
# ---------------------------------------------------------------------------


def probe_reflex(conn, since: datetime, threshold: int, max_listed: int) -> ClassResult:
    """Genesis reflexes: declared in REFLEX_NAMES *and* enabled in config.

    Both halves are required because neither alone dispatches anything —
    ``run_due_reflexes`` iterates ``REFLEX_NAMES``, and skips any entry without
    an enabled config block carrying a parseable schedule. xbm-wake-02 was
    exactly the gap between the two.
    """
    res = ClassResult(
        capability_class="reflex",
        declaration_source="tools.genesis.daemon.REFLEX_NAMES x args/genesis_config.yaml",
        telemetry_table="genesis_reflex_state",
    )
    try:
        from tools.genesis.daemon import REFLEX_NAMES

        import yaml

        cfg = yaml.safe_load(
            _repo_file("args/genesis_config.yaml").read_text(encoding="utf-8")
        ) or {}
        reflex_cfg = cfg.get("reflexes") or {}
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "reflex", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )

    declared = [
        name for name in REFLEX_NAMES
        if (reflex_cfg.get(name) or {}).get("enabled") is True
    ]
    res.extra["reflex_names_total"] = len(REFLEX_NAMES)
    res.extra["config_enabled_total"] = sum(
        1 for v in reflex_cfg.values() if isinstance(v, dict) and v.get("enabled") is True
    )
    # Enabled in config but absent from the dispatcher's list: shipped, wired,
    # scheduled, and structurally incapable of running. The xbm-wake-02 shape.
    res.extra["enabled_but_not_dispatched"] = sorted(
        name for name, v in reflex_cfg.items()
        if isinstance(v, dict) and v.get("enabled") is True and name not in set(REFLEX_NAMES)
    )[:max_listed]

    from tools.db.storage import table_exists

    if not table_exists(conn, "genesis_reflex_state"):
        return _unmeasured(
            "reflex", res.declaration_source, res.telemetry_table,
            "genesis_reflex_state does not exist",
        )
    try:
        # One row per reflex holding cumulative counters, not one row per run —
        # so a reflex counts as consumed when its last_run_at falls in the
        # window, and `events` sums total_runs for those. total_runs is
        # lifetime, so it is reported separately rather than as window events.
        counts = _count_by_key(conn, "genesis_reflex_state", "reflex_name", "last_run_at", since)
        lifetime = {}
        for row in conn.execute(
            "SELECT reflex_name, total_runs FROM genesis_reflex_state"
        ).fetchall():
            rec = dict(row)
            lifetime[str(rec.get("reflex_name") or "")] = int(rec.get("total_runs") or 0)
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "reflex", res.declaration_source, res.telemetry_table, f"query failed: {exc}",
        )

    res = _finish(res, declared, counts, threshold, max_listed)
    # genesis_reflex_state holds one row per reflex with cumulative counters and
    # a last_run_at — there is no per-run history to count. So `events` here is
    # one per reflex that ran inside the window, not a run tally, and equals
    # `consumed` by construction. Flagged so a consumer cannot misread it as
    # throughput.
    res.extra["events_semantics"] = "one per capability that ran in window (no per-run history)"
    res.extra["never_run_lifetime"] = sorted(
        name for name in declared if lifetime.get(name, 0) <= 0
    )[:max_listed]
    res.extra["never_run_lifetime_count"] = sum(
        1 for name in declared if lifetime.get(name, 0) <= 0
    )
    return res


def probe_mcp_dispatch_tool(conn, since: datetime, threshold: int, max_listed: int) -> ClassResult:
    """MCP tools in the registry vs. dispatch attempts the tool gate recorded."""
    res = ClassResult(
        capability_class="mcp_dispatch_tool",
        declaration_source="tools.mcp.tool_registry.TOOL_REGISTRY",
        telemetry_table="studio_mcp_dispatch_audit",
    )
    try:
        from tools.mcp.tool_registry import TOOL_REGISTRY

        declared = sorted(TOOL_REGISTRY.keys())
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "mcp_dispatch_tool", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )

    from tools.db.storage import table_exists

    if not table_exists(conn, "studio_mcp_dispatch_audit"):
        return _unmeasured(
            "mcp_dispatch_tool", res.declaration_source, res.telemetry_table,
            "studio_mcp_dispatch_audit does not exist",
        )
    try:
        counts = _count_by_key(conn, "studio_mcp_dispatch_audit", "tool", "recorded_at", since)
        allowed = _count_by_key(
            conn, "studio_mcp_dispatch_audit", "tool", "recorded_at", since,
            extra_sql="AND decision = %s", extra_params=("allowed",),
        )
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "mcp_dispatch_tool", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )
    res = _finish(res, declared, counts, threshold, max_listed)
    # Scoped to declared tools so this stays consistent with `events`, which
    # also ignores dispatches against tools the registry does not list. Summing
    # raw `allowed` here would let an undeclared tool drive denied_events
    # negative.
    allowed_declared = sum(allowed.get(name, 0) for name in set(declared))
    res.extra["allowed_events"] = allowed_declared
    res.extra["denied_events"] = res.events - allowed_declared
    return res


def probe_agent_approval_rule(conn, since: datetime, threshold: int, max_listed: int) -> ClassResult:
    """Tools the approval policy REQUIRES APPROVAL FOR vs. gate decisions recorded.

    Two narrowings, both rem-cap-05, both because the naive reading of this class
    could never reach zero even with a perfectly wired gate.

    1. ``declared`` is scoped to the tiers in ``require_approval_tiers``. The hook
       ``build_approval_hook`` returns early for a call that does not require
       approval (approval_gate.py, "allowed without ceremony and without a row"),
       which is the right design for an audit trail of DECISIONS — but it means
       the 37 tools in the ``reversible``/``recoverable`` tiers could be
       classified ten thousand times a day and ``agent_approval_log`` would still
       hold nothing for any of them. Counting them as declared-and-inert made a
       working gate indistinguishable from an absent one, in the direction that
       pins the budget at >= 37 forever. They are reported in
       ``extra.not_measurable_by_design``, ENUMERATED rather than merely counted,
       so a tier change or a policy edit is visible — a class that quietly shrank
       its own denominator would be the same dishonesty pointed the other way.
       The tier list is read from the policy, never hardcoded: an operator who
       adds ``recoverable`` to ``require_approval_tiers`` moves those 13 tools
       into the measurable set on the next run.

    2. Events are filtered on ``tier``. ``tools/quality/hitl_delta.py``
       legitimately reuses ``record_decision()`` and this table for a different
       kind of decision (tiers ``review`` / ``trust_delta``, rules
       ``claim_guard`` / ``hitl_delta``) — tiers ``classify()`` can never emit.
       The probe previously reported zero only because those rows' ``tool_name``
       values happened not to collide with a policy-enumerated tool; a collision
       would have reported FALSE consumption for a gate that has never run.
    """
    res = ClassResult(
        capability_class="agent_approval_rule",
        declaration_source="args/agent_approval_policy.yaml (tools.* in require_approval_tiers)",
        telemetry_table="agent_approval_log (tier IN approval_gate.TIERS)",
    )
    try:
        from tools.agent_runtime.approval_gate import TIERS, load_policy

        policy = load_policy()
        tools_by_tier = policy.get("tools") or {}
        require = {
            str(t).strip().lower() for t in (policy.get("require_approval_tiers") or [])
        }
        measurable = tuple(t for t in TIERS if t in require)
        declared = [
            str(name).lower()
            for tier in measurable
            for name in (tools_by_tier.get(tier) or [])
        ]
        excluded_by_tier = {
            tier: sorted({str(n).lower() for n in (tools_by_tier.get(tier) or [])})
            for tier in TIERS
            if tier not in require and (tools_by_tier.get(tier) or [])
        }
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )

    if not measurable:
        # No tier requires approval, so the hook records nothing for any tool.
        # Reporting a clean 0 declared / 0 inert here would launder a gate that
        # cannot write a row into a gate with nothing left to prove.
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            "require_approval_tiers is empty — no call can produce a decision row",
        )

    from tools.db.storage import table_exists

    if not table_exists(conn, "agent_approval_log"):
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            "agent_approval_log does not exist",
        )
    if _column_type(conn, "agent_approval_log", "tier") is None:
        # Without the discriminator the count cannot exclude hitl_delta's rows,
        # and an over-count here reads as a live gate. Say so instead.
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            "agent_approval_log has no tier column — consumption is indistinguishable "
            "from other record_decision() writers",
        )
    try:
        counts = _count_by_key(
            conn, "agent_approval_log", "tool_name", "decided_at", since,
            extra_sql="AND LOWER(tier) IN (" + ", ".join(["%s"] * len(TIERS)) + ")",
            extra_params=tuple(TIERS),
        )
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )
    counts = {str(k).lower(): v for k, v in counts.items()}
    res = _finish(res, declared, counts, threshold, max_listed)
    excluded_flat = sorted({n for names in excluded_by_tier.values() for n in names})
    res.extra["require_approval_tiers"] = sorted(require)
    res.extra["not_measurable_by_design"] = {
        "count": len(excluded_flat),
        "tiers": sorted(excluded_by_tier),
        "tools_by_tier": {
            tier: names[:max_listed] for tier, names in sorted(excluded_by_tier.items())
        },
        "truncated": any(len(names) > max_listed for names in excluded_by_tier.values()),
        "why": (
            "build_approval_hook auto-allows a tier outside require_approval_tiers "
            "without writing a row; these tools can never appear in agent_approval_log"
        ),
    }
    return res


def probe_mcp_tool_authorization(
    conn, since: datetime, threshold: int, max_listed: int
) -> ClassResult:
    """D261 RBAC roles vs. MCPToolAuthorizer verdicts the HTTP surface recorded.

    The declared roles come from ``tools/mcp/tool_registry.py`` since
    exa-policy-07 retired the hand-written matrix in
    ``args/owasp_agentic_config.yaml``. Reading the YAML would now report zero
    declared roles and therefore a falsely clean "nothing declared, nothing
    unconsumed" -- the exact laundering this module exists to prevent.

    The verdict payload is a JSON blob in ``audit_platform.details``. It is read
    raw and parsed in Python rather than picked apart with json_extract, which
    is SQLite-only dialect and does not survive on the PostgreSQL primary.
    """
    res = ClassResult(
        capability_class="mcp_tool_authorization",
        declaration_source="tools/mcp/tool_registry.py (ROLES)",
        telemetry_table="audit_platform (event_type='mcp.authz')",
    )
    try:
        from tools.mcp.tool_registry import ROLES

        declared = sorted(str(r).lower() for r in ROLES)
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "mcp_tool_authorization", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )

    from tools.db.storage import table_exists

    if not table_exists(conn, "audit_platform"):
        return _unmeasured(
            "mcp_tool_authorization", res.declaration_source, res.telemetry_table,
            "audit_platform does not exist",
        )
    try:
        bound = _window_bound(conn, "audit_platform", "recorded_at", since)
        rows = conn.execute(
            "SELECT details FROM audit_platform "
            "WHERE event_type = %s AND recorded_at IS NOT NULL AND recorded_at >= %s",
            ("mcp.authz", bound),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "mcp_tool_authorization", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )

    counts: Dict[str, int] = {}
    enforced = 0
    for row in rows:
        detail = dict(row).get("details")
        payload: Dict[str, Any] = {}
        if isinstance(detail, dict):
            payload = detail
        elif detail:
            try:
                parsed = json.loads(detail)
                payload = parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                payload = {}
        role = str(payload.get("rbac_role") or payload.get("role") or "").lower()
        counts[role] = counts.get(role, 0) + 1
        if payload.get("enforced") is True:
            enforced += 1

    res = _finish(res, declared, counts, threshold, max_listed)
    res.extra["verdicts_total"] = len(rows)
    res.extra["verdicts_enforced"] = enforced
    # A verdict that binds nothing is a measurement of intent, not of control.
    res.extra["verdicts_monitor_only"] = len(rows) - enforced
    return res


def probe_prompt_template(conn, since: datetime, threshold: int, max_listed: int) -> ClassResult:
    """Hard prompts on disk vs. rows the prompt registry actually holds.

    Consumption here is registration, not invocation: the registry has no usage
    table, and inventing one would violate the no-new-telemetry rule. A template
    with no non-draft ``prompt_versions`` row has never entered the registry at
    all, which is the state prompt_registry has been in since it shipped.
    """
    res = ClassResult(
        capability_class="prompt_template",
        declaration_source="hardprompts/*.md",
        telemetry_table="prompt_versions",
    )
    prompt_dir = _repo_file("hardprompts")
    if not prompt_dir.is_dir():
        return _unmeasured(
            "prompt_template", res.declaration_source, res.telemetry_table,
            "hardprompts/ directory not found",
        )
    declared = sorted(p.stem for p in prompt_dir.glob("*.md"))

    from tools.db.storage import table_exists

    if not table_exists(conn, "prompt_versions"):
        return _unmeasured(
            "prompt_template", res.declaration_source, res.telemetry_table,
            "prompt_versions does not exist",
        )
    try:
        counts = _count_by_key(
            conn, "prompt_versions", "prompt_name", "updated_at", since,
            extra_sql="AND status <> %s", extra_params=("draft",),
        )
        total_rows = int(
            dict(conn.execute("SELECT COUNT(*) AS n FROM prompt_versions").fetchone()).get("n") or 0
        )
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "prompt_template", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )
    res = _finish(res, declared, counts, threshold, max_listed)
    res.extra["prompt_versions_rows_total"] = total_rows
    return res


def probe_audit_chain(conn, since: datetime, threshold: int, max_listed: int) -> ClassResult:
    """The migration-149 hash chain: are audit rows actually being chained?

    One declared unit — the chain itself. ``events`` is the number of audit rows
    written in the window that carry a hash, and ``coverage_pct`` puts that over
    the rows written in the same window. The chain shipped with a verifier, a
    bundle format and a dashboard, and 3 hashed rows in 78,000.
    """
    res = ClassResult(
        capability_class="audit_chain",
        declaration_source="migration 149 (audit_trail.hash/previous_hash/signature)",
        telemetry_table="audit_trail",
    )
    from tools.db.storage import table_exists

    if not table_exists(conn, "audit_trail"):
        return _unmeasured(
            "audit_chain", res.declaration_source, res.telemetry_table,
            "audit_trail does not exist",
        )
    if not _column_type(conn, "audit_trail", "hash"):
        return _unmeasured(
            "audit_chain", res.declaration_source, res.telemetry_table,
            "audit_trail.hash column absent — migration 149 has not run",
        )
    try:
        bound = _window_bound(conn, "audit_trail", "created_at", since)
        eligible = int(dict(conn.execute(
            "SELECT COUNT(*) AS n FROM audit_trail WHERE created_at >= %s", (bound,)
        ).fetchone()).get("n") or 0)
        hashed = int(dict(conn.execute(
            "SELECT COUNT(*) AS n FROM audit_trail WHERE created_at >= %s AND hash IS NOT NULL",
            (bound,),
        ).fetchone()).get("n") or 0)
        chained = int(dict(conn.execute(
            "SELECT COUNT(*) AS n FROM audit_trail WHERE created_at >= %s "
            "AND hash IS NOT NULL AND previous_hash IS NOT NULL",
            (bound,),
        ).fetchone()).get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "audit_chain", res.declaration_source, res.telemetry_table, f"query failed: {exc}",
        )

    res = _finish(res, ["audit_trail_hash_chain"], {"audit_trail_hash_chain": hashed},
                  threshold, max_listed)
    res.extra["audit_rows_in_window"] = eligible
    res.extra["hashed_rows_in_window"] = hashed
    res.extra["chain_linked_rows_in_window"] = chained
    res.extra["coverage_pct"] = round(100.0 * hashed / eligible, 4) if eligible else 0.0
    return res


def probe_skill_optimizer(conn, since: datetime, threshold: int, max_listed: int) -> ClassResult:
    """GEPA: skills queued for optimization vs. artifacts GEPA actually decided.

    Consumption is a RECORDED GEPA DECISION — applied or declined — not an apply
    alone (rem-cap-01). Counting applies only made "GEPA ran and correctly
    declined everything" indistinguishable from "GEPA never ran", which is the
    declared-but-unconsumed shape this module exists to catch, in the module
    that is supposed to catch it. It also matches how the two neighbouring
    classes already count: mcp_tool_authorization counts a VERDICT rather than a
    denial, and audit_chain counts a row written with a chain hash rather than a
    tamper finding. `applied` and `declined` are reported separately in `extra`
    so a busy decline loop can never read as a busy patch loop.

    Also reports how many queued artifacts even *satisfy* GEPA's own selection
    predicate. That number being zero while the queue is full is the difference
    between "nothing to do" and "structurally cannot ever act", and only the
    second one is a defect. `pending_undecided` is the honest denominator for
    that comparison: an artifact GEPA has terminally declined is settled, and
    counting it as backlog is what kept the alarm on forever.
    """
    res = ClassResult(
        capability_class="skill_optimizer",
        declaration_source=(
            "agent_improvement_artifacts (distinct skill_used, "
            "excluding upstream evidence rejections)"
        ),
        telemetry_table="agent_improvement_artifacts",
    )
    from tools.db.storage import column_exists, table_exists

    if not table_exists(conn, "agent_improvement_artifacts"):
        return _unmeasured(
            "skill_optimizer", res.declaration_source, res.telemetry_table,
            "agent_improvement_artifacts does not exist",
        )
    # A database that predates the gepa_decision_columns migration cannot be
    # asked this question. Reporting zero there would be the misleading zero
    # this module refuses to produce everywhere else.
    if not column_exists(conn, "agent_improvement_artifacts", "gepa_decided_at"):
        return _unmeasured(
            "skill_optimizer", res.declaration_source, res.telemetry_table,
            "agent_improvement_artifacts.gepa_decided_at does not exist "
            "(migration 20260816125047_gepa_decision_columns has not run)",
        )
    try:
        # Declared units are the skills GEPA was asked to improve, across every
        # status — NOT just the pending queue. An artifact GEPA applies leaves
        # that queue, so scoping the declaration to 'pending' would make a skill
        # structurally incapable of ever being counted as consumed: the exact
        # never-goes-green shape this tool exists to catch.
        all_rows = conn.execute(
            "SELECT skill_used, composite_score, baseline_score, status, gepa_decision "
            "FROM agent_improvement_artifacts"
        ).fetchall()
        counts = _count_by_key(
            conn, "agent_improvement_artifacts", "skill_used", "gepa_decided_at", since
        )
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "skill_optimizer", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )

    from tools.skills.gepa_optimizer import DECISION_APPLIED, TERMINAL_DECISIONS

    declared: List[str] = []
    pending = 0
    pending_undecided = 0
    selectable = 0
    decided_lifetime = 0
    applied_lifetime = 0
    upstream_rejected = 0
    for row in all_rows:
        rec = dict(row)
        status = str(rec.get("status") or "")
        # An artifact the Reflexion agent's evidence gate refused
        # (`rejected_no_evidence`, args/refinement_evidence.yaml
        # `rejected_status`) never enters GEPA's queue — GEPA selects on
        # status='pending' and is structurally never shown it. Counting it as a
        # skill GEPA was asked to improve blames GEPA for an upstream decision
        # and makes the class permanently un-greenable no matter how well GEPA
        # works. 'applied' still counts, which is the point of not scoping this
        # to 'pending' alone: an artifact GEPA acted on has left that queue.
        if status.startswith("rejected"):
            upstream_rejected += 1
            continue
        declared.append(str(rec.get("skill_used") or "(unattributed)"))
        decision = str(rec.get("gepa_decision") or "")
        if decision:
            decided_lifetime += 1
            if decision == DECISION_APPLIED:
                applied_lifetime += 1
        if status != "pending":
            continue
        pending += 1
        if decision in TERMINAL_DECISIONS:
            continue
        pending_undecided += 1
        composite = rec.get("composite_score")
        baseline = rec.get("baseline_score")
        if composite is None or baseline is None:
            continue
        # Mirrors gepa_optimizer._score_verdict: composite >= 0.60 and a delta
        # of at least 0.05 over baseline.
        if float(composite) >= 0.60 and float(composite) - float(baseline) >= 0.05:
            selectable += 1

    counts = {(k or "(unattributed)"): v for k, v in counts.items()}
    res = _finish(res, declared, counts, threshold, max_listed)
    res.extra["artifacts_total"] = len(all_rows)
    # Reported, never silently dropped: these are artifacts the evidence gate
    # refused upstream, so they are not GEPA's to answer for and not GEPA's to
    # be credited with either.
    res.extra["upstream_rejected_artifacts"] = upstream_rejected
    res.extra["pending_artifacts"] = pending
    res.extra["pending_undecided_artifacts"] = pending_undecided
    res.extra["artifacts_matching_selection_predicate"] = selectable
    # Lifetime, alongside the window counts — the same split probe_reflex makes
    # with never_run_lifetime. A capability that decided something once and has
    # been idle since is idle, not inert, and the two must not blur.
    res.extra["decisions_lifetime"] = decided_lifetime
    res.extra["applied_lifetime"] = applied_lifetime
    res.extra["declined_lifetime"] = decided_lifetime - applied_lifetime
    return res


#: Where the hook-point enum lives. Read with ``ast`` rather than imported: the
#: module creates its singleton at import time and the singleton auto-loads the
#: nine chat builtins, which pull in RAG, Bayesian learning and the genesis
#: status reader. ``check_capability_liveness`` runs this twice per commit, so a
#: measurement tool must not execute eleven extension modules to count ten
#: names — and a builtin that failed to import would turn a count into an
#: unmeasurable, which is a reading about the importer, not about the seam.
_EXTENSION_MANAGER_SRC = "tools/extensions/extension_manager.py"


def _extension_points_from_source() -> List[str]:
    """The ``ExtensionPoint`` enum's values, parsed without importing it."""
    tree = ast.parse(
        _repo_file(_EXTENSION_MANAGER_SRC).read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "ExtensionPoint"):
            continue
        values = []
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                values.append(stmt.value.value)
        return values
    raise ValueError("ExtensionPoint enum not found")


def probe_extension_hook_point(
    conn, since: datetime, threshold: int, max_listed: int
) -> ClassResult:
    """Extension hook points declared vs. dispatches actually recorded.

    The seam ``args/extension_config.yaml`` declares ten points for. Until
    hcx-live-02 nothing counted a dispatch anywhere, so "this point is consumed"
    and "this point has never been called in the platform's history" were the
    same reading — and eight of the ten have no dispatch call site at all.

    Declared units are points present in BOTH the enum and an enabled config
    block, for the same reason ``probe_reflex`` requires both halves: a point
    the config disables is stood down on purpose and is not the defect, and a
    config key with no matching enum member can never be dispatched at all.
    Both exclusions are reported in ``extra`` rather than dropped silently.
    """
    res = ClassResult(
        capability_class="extension_hook_point",
        declaration_source="ExtensionPoint x args/extension_config.yaml (hook_points)",
        telemetry_table="runtime_invocations (surface='extension')",
    )
    try:
        import yaml

        enum_points = _extension_points_from_source()
        cfg = yaml.safe_load(
            _repo_file("args/extension_config.yaml").read_text(encoding="utf-8")
        ) or {}
        points_cfg = ((cfg.get("extensions") or {}).get("hook_points") or {})
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "extension_hook_point", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )

    def _point_enabled(name: str) -> bool:
        block = points_cfg.get(name)
        # An absent block is the permissive default dispatch() itself applies.
        return not isinstance(block, dict) or block.get("enabled") is not False

    declared = [p for p in enum_points if _point_enabled(p)]
    res.extra["enum_points_total"] = len(enum_points)
    res.extra["disabled_in_config"] = sorted(
        p for p in enum_points if not _point_enabled(p)
    )[:max_listed]
    # A hook_points key naming no enum member configures a point that cannot be
    # dispatched — declared-but-unconsumable, one notch worse than inert.
    res.extra["config_points_not_in_enum"] = sorted(
        set(points_cfg) - set(enum_points)
    )[:max_listed]

    from tools.db.storage import table_exists

    if not table_exists(conn, "runtime_invocations"):
        return _unmeasured(
            "extension_hook_point", res.declaration_source, res.telemetry_table,
            "runtime_invocations does not exist",
        )
    try:
        counts = _count_by_key(
            conn, "runtime_invocations", "name", "started_at", since,
            extra_sql="AND surface = %s", extra_params=("extension",),
        )
        failed = _count_by_key(
            conn, "runtime_invocations", "name", "started_at", since,
            extra_sql="AND surface = %s AND status = %s",
            extra_params=("extension", "error"),
        )
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "extension_hook_point", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )

    res = _finish(res, declared, counts, threshold, max_listed)
    # A dispatch whose handler raised is still a dispatch — it counts as
    # consumption. It is reported separately because a point that only ever
    # fails is consumed and broken, and the two readings must not cancel out.
    res.extra["failed_dispatch_events"] = sum(failed.get(p, 0) for p in set(declared))
    res.extra["points_with_failures"] = sorted(
        p for p in declared if failed.get(p, 0) > 0
    )[:max_listed]
    return res


# ---------------------------------------------------------------------------
# Cortex federation layer (cef-ci-01)
#
# The federation layer added three rungs — ``currency`` (cef-bck-01),
# ``external`` (cef-bck-02) and ``sme`` (cef-bck-03) — behind one governed
# facade, ``cortex.resolve`` (cef-rsv-01). That is the textbook shape of this
# repository's signature defect: registered in CORTEX_BACKENDS, importable,
# weighted in args/cortex_config.yaml, documented — and reachable only if
# something actually asks for them.
#
# Two probes rather than one, because "was this rung reached" and "was this verb
# called" are different questions with different repairs. A facade nothing calls
# is a missing CALLER; a rung nothing returns from is a missing ROUTE (or a dead
# backend). Folding them into one class would let a busy ``cortex.resolve`` hide
# three rungs that never answered — the exact miss ``inert_units`` exists to
# prevent.
# ---------------------------------------------------------------------------
_CORTEX_SCHEMAS_SRC = "tools/cortex/schemas.py"
_CORTEX_API_SRC = "tools/cortex/api.py"
_CORTEX_CONFIG_SRC = "args/cortex_config.yaml"
_CORTEX_AUDIT_TABLE = "cortex_audit"

#: Newest-first cap on cortex_audit rows whose gates_json is parsed. The blob
#: has no column of its own to filter on, so the rung tally is a Python scan;
#: the cap bounds it and ``rows_truncated`` REPORTS when it bit, because a
#: silently sampled "never consumed" is the one reading this module may not
#: produce.
_CORTEX_AUDIT_SCAN_LIMIT = 50000


def _str_tuple_from_source(path: str, name: str) -> List[str]:
    """The string members of a module-level tuple/list literal, without importing.

    ``tools.cortex`` pulls in the retrieval stack, the LLM router and a domain
    pack registry on import. A measurement tool must not need a working Cortex
    to report on Cortex, so the declaration is read the way
    ``_extension_points_from_source`` reads the ExtensionPoint enum.
    """
    tree = ast.parse(_repo_file(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets or not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        return [
            elt.value for elt in node.value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
    raise ValueError(f"{name} not found in {path}")


def _cortex_routed_backends() -> Dict[str, List[str]]:
    """Which rungs a deployment's config puts on an AUTOMATIC route.

    ``resolve.backends``, ``search.fan_out.backends`` and every
    ``search.domains.*.backends`` are the routes a caller gets without naming
    one. A rung on none of them is reachable only by an explicit
    ``strategy=``/``backends=`` argument — which is a real, deliberate posture
    here (``external`` opens a socket outside the boundary; ``sme`` returns an
    LLM's opinion and must never outrank evidence) and NOT a reason to exempt
    it. It is reported, so a zero against it reads as "no caller has ever asked"
    rather than "the config is broken".
    """
    import yaml

    cfg = yaml.safe_load(
        _repo_file(_CORTEX_CONFIG_SRC).read_text(encoding="utf-8")
    ) or {}
    search = cfg.get("search") or {}
    routes: Dict[str, List[str]] = {
        "resolve.backends": list(((cfg.get("resolve") or {}).get("backends")) or []),
        "search.fan_out.backends": list(
            ((search.get("fan_out") or {}).get("backends")) or []
        ),
    }
    for domain, block in (search.get("domains") or {}).items():
        if isinstance(block, dict) and block.get("backends"):
            routes[f"search.domains.{domain}.backends"] = list(block["backends"])
    return routes


def _cortex_backend_events(
    conn, since: datetime, limit: int = _CORTEX_AUDIT_SCAN_LIMIT
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int], int, bool]:
    """Tally ``gates_json['backends']`` over cortex_audit rows newer than ``since``.

    Parsed in PYTHON, never with SQLite-dialect JSON SQL in the query — the
    repository rule, and here also a correctness one: ``gates_json`` is ``jsonb``
    on PostgreSQL and TEXT on SQLite, so one dialect's extraction operator does
    not exist on the other backend at all.

    Returns ``(used, consulted, failed, rows_scanned, truncated)``.
    """
    bound = _window_bound(conn, _CORTEX_AUDIT_TABLE, "created_at", since)
    rows = conn.execute(
        "SELECT gates_json FROM " + _CORTEX_AUDIT_TABLE + " "
        "WHERE created_at IS NOT NULL AND created_at >= %s "
        "ORDER BY created_at DESC LIMIT %s",
        (bound, int(limit) + 1),
    ).fetchall()
    truncated = len(rows) > int(limit)
    rows = rows[: int(limit)]
    used: Dict[str, int] = {}
    consulted: Dict[str, int] = {}
    failed: Dict[str, int] = {}
    buckets = {"used": used, "consulted": consulted, "failed": failed}
    for row in rows:
        blob = dict(row).get("gates_json")
        if isinstance(blob, (str, bytes)):
            try:
                blob = json.loads(blob)
            except Exception:  # noqa: BLE001 — one unreadable blob is not a verdict
                continue
        if not isinstance(blob, dict):
            continue
        backends = blob.get("backends")
        if not isinstance(backends, dict):
            continue
        for key, sink in buckets.items():
            for name in backends.get(key) or ():
                if name:
                    sink[str(name)] = sink.get(str(name), 0) + 1
    return used, consulted, failed, len(rows), truncated


def probe_cortex_backend(
    conn, since: datetime, threshold: int, max_listed: int
) -> ClassResult:
    """Cortex retrieval rungs declared vs. rungs that actually ANSWERED.

    Consumption is a governed call whose audit row records the rung under
    ``gates_json.backends.used`` — a rung that RETURNED A HIT. Deliberately not
    ``consulted``: that list is a read of ``resolve.backends`` in
    args/cortex_config.yaml, so counting it would report every declared rung
    live on a deployment where not one of them ever answered, which is this
    gate's failure mode rather than its finding.

    ``sme`` counts on the same terms as the rest. Its hits are excluded from a
    resolution's citations and can never move a verdict (base_pack TRUST rule
    1), and that is a statement about CITABILITY, not about whether the rung was
    reached. Conflating the two would make the one advisory backend permanently
    unmeasurable.
    """
    res = ClassResult(
        capability_class="cortex_backend",
        declaration_source="CORTEX_BACKENDS (tools/cortex/schemas.py)",
        telemetry_table="cortex_audit (gates_json.backends.used)",
    )
    try:
        declared = _str_tuple_from_source(_CORTEX_SCHEMAS_SRC, "CORTEX_BACKENDS")
        advisory = _str_tuple_from_source(_CORTEX_SCHEMAS_SRC, "ADVISORY_BACKENDS")
        routes = _cortex_routed_backends()
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "cortex_backend", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )
    if not declared:
        return _unmeasured(
            "cortex_backend", res.declaration_source, res.telemetry_table,
            "CORTEX_BACKENDS is empty",
        )

    automatic = {b for names in routes.values() for b in names}
    res.extra["advisory_backends"] = sorted(set(advisory) & set(declared))
    res.extra["routes"] = {k: sorted(v) for k, v in sorted(routes.items())}
    # Reachable ONLY when a caller names it. Reported, never exempted: an
    # unreached rung here needs a CALLER, which is a different repair from a
    # rung that is routed to and still never answers.
    res.extra["opt_in_only"] = sorted(set(declared) - automatic)

    from tools.db.storage import table_exists

    if not table_exists(conn, _CORTEX_AUDIT_TABLE):
        return _unmeasured(
            "cortex_backend", res.declaration_source, res.telemetry_table,
            f"{_CORTEX_AUDIT_TABLE} does not exist",
        )
    try:
        used, consulted, failed, scanned, truncated = _cortex_backend_events(conn, since)
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "cortex_backend", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )

    res = _finish(res, declared, used, threshold, max_listed)
    res.extra["audit_rows_scanned"] = scanned
    res.extra["rows_truncated"] = truncated
    # A rung the router ASKED and that never answered. Not consumption, and a
    # different finding from a rung nothing ever routed to: this one is wired
    # and silent, which usually means an empty corpus or a dead adapter.
    res.extra["consulted_never_used"] = sorted(
        b for b in declared if consulted.get(b, 0) > 0 and used.get(b, 0) <= threshold
    )[:max_listed]
    res.extra["consulted_events"] = {
        b: consulted[b] for b in sorted(declared) if consulted.get(b, 0)
    }
    # A rung that DIED. Recorded so "reached and broken" never reads as inert.
    res.extra["failed_events"] = {
        b: failed[b] for b in sorted(declared) if failed.get(b, 0)
    }
    return res


def probe_cortex_facade(
    conn, since: datetime, threshold: int, max_listed: int
) -> ClassResult:
    """Governed Cortex verbs declared vs. verbs a governed call ever ran.

    ``CORTEX_FACADES`` is the closed list of public verbs, and every one of them
    is wrapped by ``@_governed_facade``, which writes exactly one append-only
    ``cortex_audit`` row per call with ``function = 'cortex.<verb>'``. So the
    declaration and the telemetry are the same seam observed from both ends, and
    a verb with no rows has genuinely never been invoked through the governed
    door.

    Units are the FULL operation name rather than the bare verb, so the count
    and the audit row's ``function`` are the same string — and so an operation
    recorded under a name no facade declares surfaces as
    ``undeclared_units_observed`` instead of being quietly dropped.
    """
    res = ClassResult(
        capability_class="cortex_facade",
        declaration_source="CORTEX_FACADES (tools/cortex/api.py)",
        telemetry_table="cortex_audit (function)",
    )
    try:
        facades = _str_tuple_from_source(_CORTEX_API_SRC, "CORTEX_FACADES")
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "cortex_facade", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )
    if not facades:
        return _unmeasured(
            "cortex_facade", res.declaration_source, res.telemetry_table,
            "CORTEX_FACADES is empty",
        )
    declared = [f"cortex.{name}" for name in facades]

    from tools.db.storage import table_exists

    if not table_exists(conn, _CORTEX_AUDIT_TABLE):
        return _unmeasured(
            "cortex_facade", res.declaration_source, res.telemetry_table,
            f"{_CORTEX_AUDIT_TABLE} does not exist",
        )
    try:
        counts = _count_by_key(
            conn, _CORTEX_AUDIT_TABLE, "function", "created_at", since,
        )
        blocked = _count_by_key(
            conn, _CORTEX_AUDIT_TABLE, "function", "created_at", since,
            extra_sql="AND blocked = %s", extra_params=(True,),
        )
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "cortex_facade", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )

    res = _finish(res, declared, counts, threshold, max_listed)
    # A BLOCKED call is still a call: the verb was invoked and the TRUST chain
    # refused it. It counts as consumption and is reported separately, on the
    # same terms as extension_hook_point's failed dispatches — a verb that is
    # only ever blocked is consumed and broken, and the two readings must not
    # cancel out.
    res.extra["blocked_events"] = {
        f: blocked[f] for f in sorted(declared) if blocked.get(f, 0)
    }
    return res


PROBES: Dict[str, Callable[..., ClassResult]] = {
    "reflex": probe_reflex,
    "mcp_dispatch_tool": probe_mcp_dispatch_tool,
    "agent_approval_rule": probe_agent_approval_rule,
    "mcp_tool_authorization": probe_mcp_tool_authorization,
    "prompt_template": probe_prompt_template,
    "audit_chain": probe_audit_chain,
    "skill_optimizer": probe_skill_optimizer,
    "extension_hook_point": probe_extension_hook_point,
    "cortex_backend": probe_cortex_backend,
    "cortex_facade": probe_cortex_facade,
}


# ---------------------------------------------------------------------------
# Substrates — the same measurement, one layer down, taken BEFORE the design
#
# A capability is something the platform says it can DO. A substrate is what a
# capability is designed AGAINST: a table, a column, a config block. The defect
# is the same shape and the measurement is the same shape — declared versus
# actually there — but a substrate can be checked before a line of code exists,
# which is the only time checking it is cheap.
#
# trust-disc-04: an approved implementation plan described ``kg_ontology`` as a
# working SHACL-lite supplying declared (subject_type, predicate, object_type)
# legality. Measured on the live board the same afternoon:
#
#     kg_nodes                   8,869 rows   populated
#     kg_edges                  16,493 rows   populated
#     kg_ontology                    0 rows   inert
#     ontology_subclass_closure      0 rows   inert (the source that feeds it)
#     kg_nodes.ontology_id           0 populated
#
# The declared-ontology chain was inert end to end while the graph beneath it
# was rich, so a validator built on the empty half would have answered
# "unknown" forever while looking like it worked. One SELECT COUNT(*) would
# have caught it and nothing asked for one. This asks for one.
#
# Three rules, two carried verbatim from the capability half because breaking
# them is how a measurement tool becomes worse than no measurement:
#
#   1. A zero from an empty DATABASE is not a finding. A fresh worktree, an
#      ephemeral CI database or an unreachable backend makes every substrate on
#      the platform look inert — on the live PostgreSQL board 1,320 of 1,775
#      tables are empty, so a probe that cannot tell "nobody has run this
#      platform" from "nobody wired this writer" fabricates findings by the
#      thousand. ``operating_history`` draws that line and the whole report
#      degrades to unmeasurable rather than guessing.
#   2. ABSENT and EMPTY are different answers and are never merged. A table
#      that does not exist means a migration has not run in *this* database; a
#      table that exists with zero rows means a writer has not run anywhere.
#      Only the second is the defect this was built for.
#   3. A substrate is probed on demand, against a named plan or diff. It is not
#      a background sweep: given 1,320 empty tables, "list the empty tables" is
#      noise, and "does the thing I am about to build on have rows" is an
#      answer.
# ---------------------------------------------------------------------------

#: Statuses that mean "code was designed against something that is not there".
#: ``absent`` is deliberately NOT one of them for a table — see rule 2 above.
SUBSTRATE_FINDING_STATUSES = frozenset({"empty", "column_unpopulated", "config_absent"})

#: Witness tables proving the database being probed has actually been run.
#: ANY satisfied witness is enough. The thresholds are high enough that a
#: conftest schema or a CI run that happened to write a few audit rows does not
#: clear them — a fabricated finding is worse here than a missed one, because
#: the whole value of this probe is that its zeroes can be trusted.
DEFAULT_HISTORY_WITNESSES: Tuple[Dict[str, Any], ...] = (
    {"table": "audit_trail", "min_rows": 1000},
    {"table": "kg_nodes", "min_rows": 100},
    {"table": "kanban_tasks", "min_rows": 100},
)

#: A bare word is only read as a table name when it carries an underscore.
#: Without that, ``tasks``, ``users``, ``documents``, ``groups``, ``agents`` and
#: ``order`` — all real tables in this schema — make every English sentence in
#: a plan a substrate reference. A name inside SQL context (FROM/JOIN/INTO/…)
#: needs no such guard, because the context is the evidence.
_BARE_REF_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?:\.([A-Za-z_][A-Za-z0-9_]*))?\b")
#: The keyword is captured, not discarded, because reading a substrate and
#: writing one are opposite findings. Code that adds ``INSERT INTO x`` is
#: populating x — that is the fix, not the defect — so a write-only reference
#: is recorded and then not counted. The longer forms lead the alternation so
#: ``DELETE FROM`` is never mistaken for a read of the same table.
_SQL_CONTEXT_RE = re.compile(
    r"\b(DELETE\s+FROM|INSERT\s+INTO|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
    r"FROM|JOIN|INTO|UPDATE|TABLE)\s+[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
#: Match kinds that mean "this design consumes the substrate".
_READ_MATCH_KINDS = frozenset({"read_sql", "bare_name", "declared", "explicit"})
#: ``kg_nodes.py`` is a module, not a column. So is ``.md``, ``.sql``, ``.json``.
_NON_COLUMN_ATTRS = frozenset({
    "py", "md", "sql", "json", "jsonl", "yaml", "yml", "html", "txt", "csv",
    "js", "ts", "tsx", "sh", "ps1", "ini", "cfg", "toml", "db", "png", "log",
})
_CONFIG_REF_SEP = "::"


@dataclass
class SubstrateResult:
    """One substrate reference and what is actually in it.

    ``status`` is the whole point and its vocabulary is deliberately narrow:

        populated           rows exist (column: non-null values exist)
        empty               the table exists and holds no rows — THE finding
        column_unpopulated  the table holds rows, the column is 100% NULL
        column_absent       the table exists, the column does not
        absent              no such table in this database
        config_populated    config key resolves to a non-empty value
        config_empty        config key resolves to null/[]/{}/''
        config_absent       config file or key path does not exist
        unmeasurable        the probe could not answer, and says so
    """

    ref: str
    kind: str = "table"  # table | column | config
    table: str = ""
    column: Optional[str] = None
    config_path: str = ""
    config_key: str = ""
    status: str = "unmeasurable"
    rows: Optional[int] = None
    populated: Optional[int] = None
    measurable: bool = False
    unmeasured_reason: Optional[str] = None
    references: List[str] = field(default_factory=list)
    #: The subset of ``references`` that are actual reads. A module that names a
    #: substrate in its docstring and queries it 190 lines later must report the
    #: query, or the reader looks at prose and concludes the check misfired.
    read_references: List[str] = field(default_factory=list)
    match_kinds: List[str] = field(default_factory=list)
    note: str = ""
    superseded_by: Optional[str] = None

    @property
    def is_finding(self) -> bool:
        """A finding is an empty substrate this change actually READS.

        Two exclusions, both measured rather than guessed. Over the last 40
        commits on main, whole-file scanning would have fired on 68% of them
        and diff-scoped scanning on 22%; charging a commit for a table it only
        writes to — the change that FIXES an empty substrate — is a third of
        what remained, and a column finding under an already-reported empty
        table is another third. A check that fires on two commits in three
        gets switched off, and then it measures nothing at all.
        """
        if self.status not in SUBSTRATE_FINDING_STATUSES:
            return False
        if self.superseded_by:
            return False
        if self.match_kinds and not (set(self.match_kinds) & _READ_MATCH_KINDS):
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "table": self.table,
            "column": self.column,
            "config_path": self.config_path,
            "config_key": self.config_key,
            "status": self.status,
            "rows": self.rows,
            "populated": self.populated,
            "measurable": self.measurable,
            "unmeasured_reason": self.unmeasured_reason,
            "references": self.references,
            "read_references": self.read_references,
            "match_kinds": self.match_kinds,
            "note": self.note,
            "superseded_by": self.superseded_by,
            "is_finding": self.is_finding,
        }


def parse_substrate_ref(ref: str) -> SubstrateResult:
    """Split ``table`` / ``table.column`` / ``file.yaml::key.path`` into a result shell."""
    ref = str(ref).strip()
    if _CONFIG_REF_SEP in ref:
        path, _, key = ref.partition(_CONFIG_REF_SEP)
        return SubstrateResult(
            ref=ref, kind="config", config_path=path.strip(), config_key=key.strip()
        )
    table, _, column = ref.partition(".")
    if column:
        return SubstrateResult(ref=ref, kind="column", table=table, column=column)
    return SubstrateResult(ref=ref, kind="table", table=table)


def _known_tables(conn) -> Optional[set]:
    """Every table name in the database, or None if the catalogue is unreadable.

    None and ``set()`` are different answers: an empty catalogue would mean
    "this database has no tables", which is a claim, whereas None means the
    probe could not look — and an unreadable catalogue must not read as a clean
    board.
    """
    from tools.db.storage import is_pg

    try:
        if is_pg(conn):
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            ).fetchall()
            return {str(dict(r)["table_name"]) for r in rows}
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        return {str(dict(r)["name"]) for r in rows}
    except Exception as exc:  # noqa: BLE001
        LOG.warning("table catalogue unreadable: %s", exc)
        _rollback(conn)
        return None


def _quoted(identifier: str) -> str:
    """Double-quote a validated identifier.

    Quoting is not cosmetic: ``order`` is a real table in this schema and a
    reserved word in both backends, so the unquoted form is a syntax error that
    would surface as ``unmeasurable`` on a table that is perfectly measurable.
    The caller has already asserted the identifier is a plain word, so there is
    nothing inside the quotes to escape.
    """
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"not a plain SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def _row_count(conn, table: str) -> int:
    return int(dict(conn.execute(f"SELECT COUNT(*) AS n FROM {_quoted(table)}").fetchone())["n"] or 0)


def _nonnull_count(conn, table: str, column: str) -> int:
    sql = (
        f"SELECT COUNT({_quoted(column)}) AS n FROM {_quoted(table)}"
    )
    return int(dict(conn.execute(sql).fetchone())["n"] or 0)


def operating_history(conn, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Has this database been run enough for a zero to mean anything?

    Returns ``has_history`` plus the per-witness evidence, so a caller that
    degrades to "unmeasurable" can say exactly which witness it looked at
    rather than asserting an unexplained warn.
    """
    cfg = (config or {}).get("substrate_probe") or {}
    witnesses = cfg.get("history_witnesses") or DEFAULT_HISTORY_WITNESSES
    catalogue = _known_tables(conn)
    if catalogue is None:
        return {
            "has_history": False,
            "witnesses": [],
            "reason": "table catalogue unreadable — cannot tell an empty platform from an unreachable one",
        }

    evidence: List[Dict[str, Any]] = []
    satisfied = False
    for entry in witnesses:
        if not isinstance(entry, dict):
            continue
        table = str(entry.get("table") or "")
        try:
            minimum = max(1, int(entry.get("min_rows") or 1))
        except (TypeError, ValueError):
            minimum = 1
        if not table or not _IDENTIFIER_RE.fullmatch(table):
            continue
        rows: Optional[int] = None
        if table in catalogue:
            try:
                rows = _row_count(conn, table)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("witness %s unreadable: %s", table, exc)
                _rollback(conn)
        met = rows is not None and rows >= minimum
        satisfied = satisfied or met
        evidence.append({"table": table, "rows": rows, "min_rows": minimum, "satisfied": met})

    reason = None
    if not satisfied:
        reason = (
            "database has no operating history — no witness table reached its "
            "minimum ("
            + ", ".join(
                f"{e['table']}={'absent' if e['rows'] is None else e['rows']}/{e['min_rows']}"
                for e in evidence
            )
            + "). Every substrate would read as inert here because nothing has "
            "been recorded, not because nothing is wired."
        )
    return {"has_history": satisfied, "witnesses": evidence, "reason": reason}


def _probe_config_substrate(res: SubstrateResult) -> SubstrateResult:
    """Resolve ``file.yaml::dotted.key`` against the repo tree.

    A missing config KEY is a finding while a missing TABLE is not, and the
    asymmetry is deliberate: the table lives in whichever database is in front
    of us and may simply be one migration behind, whereas the config file is
    checked into the tree that is being reviewed. Its absence is a fact about
    the change, not about the environment.
    """
    path = _repo_file(res.config_path)
    if not path.exists():
        res.status = "config_absent"
        res.measurable = True
        res.note = f"{res.config_path} does not exist"
        return res
    try:
        if path.suffix in (".yaml", ".yml"):
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        elif path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            res.unmeasured_reason = f"unsupported config format: {path.suffix or '(none)'}"
            return res
    except Exception as exc:  # noqa: BLE001
        res.unmeasured_reason = f"config unparseable: {exc}"
        return res

    node: Any = data
    for part in [p for p in res.config_key.split(".") if p]:
        if isinstance(node, dict) and part in node:
            node = node[part]
            continue
        res.status = "config_absent"
        res.measurable = True
        res.note = f"key path stops at {part!r}"
        return res

    res.measurable = True
    if isinstance(node, (list, dict, str)):
        res.rows = len(node)
        res.status = "config_populated" if len(node) else "config_empty"
    elif node is None:
        res.rows = 0
        res.status = "config_empty"
    else:
        res.rows = 1
        res.status = "config_populated"
    return res


def probe_substrate(conn, ref: str, catalogue: Optional[set] = None) -> SubstrateResult:
    """Answer "is there anything in it?" for one substrate reference."""
    res = parse_substrate_ref(ref)
    if res.kind == "config":
        return _probe_config_substrate(res)

    if not _IDENTIFIER_RE.fullmatch(res.table) or (
        res.column and not _IDENTIFIER_RE.fullmatch(res.column)
    ):
        res.unmeasured_reason = f"not a plain identifier: {ref!r}"
        return res

    if catalogue is None:
        catalogue = _known_tables(conn)
    if catalogue is None:
        res.unmeasured_reason = "table catalogue unreadable"
        return res
    if res.table not in catalogue:
        res.status = "absent"
        res.measurable = True
        res.note = "no such table in this database — a migration has not run here"
        return res

    try:
        res.rows = _row_count(conn, res.table)
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        res.unmeasured_reason = f"row count failed: {exc}"
        return res

    res.measurable = True
    if res.kind == "table":
        res.status = "populated" if res.rows else "empty"
        if not res.rows:
            res.note = "table exists and holds no rows — a writer has not run"
        return res

    try:
        res.populated = _nonnull_count(conn, res.table, res.column or "")
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        # The row count succeeded, so the table is fine; the column is not
        # there. Reported as its own status rather than folded into "empty",
        # which would send a reader looking for a missing writer instead of a
        # missing migration.
        res.status = "column_absent"
        res.note = f"column not readable on {res.table}: {str(exc).splitlines()[0][:120]}"
        return res

    if not res.rows:
        res.status = "empty"
        res.note = "table exists and holds no rows — a writer has not run"
    elif res.populated:
        res.status = "populated"
    else:
        res.status = "column_unpopulated"
        res.note = f"{res.rows} rows, every one NULL in {res.column} — nothing populates it"
    return res


def extract_substrate_refs(
    text: str,
    catalogue: set,
    source: str = "",
    ignore: Optional[set] = None,
) -> Dict[str, Dict[str, Any]]:
    """Pull substrate references out of arbitrary text against a real catalogue.

    Matching against the database's own table list — rather than parsing the
    text for things that look like table names — is what keeps this usable on
    prose. A plan is English; the only tokens worth reporting are the ones that
    name something that actually exists.

    Returns ``{ref: {"match_kinds": [...], "references": ["source:line", ...]}}``.
    """
    ignore = ignore or set()
    found: Dict[str, Dict[str, Any]] = {}

    def _record(ref: str, kind: str, lineno: int) -> None:
        entry = found.setdefault(ref, {"match_kinds": [], "references": [], "read_references": []})
        if kind not in entry["match_kinds"]:
            entry["match_kinds"].append(kind)
        where = f"{source}:{lineno}" if source else str(lineno)
        if where not in entry["references"]:
            entry["references"].append(where)
        if kind == "read_sql" and where not in entry["read_references"]:
            entry["read_references"].append(where)

    for lineno, line in enumerate(text.splitlines(), start=1):
        # SQL context first: it is the stronger evidence, and a name it has
        # already classified must not be re-recorded as a bare mention. Without
        # that, `INSERT INTO kg_ontology` picks up a bare_name alongside its
        # write_sql and reads as a design against the table — charging the one
        # change that POPULATES an empty substrate with the defect.
        sql_kinds: Dict[str, str] = {}
        for match in _SQL_CONTEXT_RE.finditer(line):
            keyword = " ".join(match.group(1).lower().split())
            name = match.group(2)
            if name in catalogue and name not in ignore:
                kind = "read_sql" if keyword in ("from", "join") else "write_sql"
                # A read anywhere on the line wins: `INSERT INTO x SELECT ... FROM x`
                # both writes and reads x, and the read is what can be empty.
                if sql_kinds.get(name) != "read_sql":
                    sql_kinds[name] = kind
                _record(name, kind, lineno)
        for match in _BARE_REF_RE.finditer(line):
            name, attr = match.group(1), match.group(2)
            if name not in catalogue or name in ignore:
                continue
            kind = sql_kinds.get(name, "bare_name")
            _record(name, kind, lineno)
            if attr and attr.lower() not in _NON_COLUMN_ATTRS:
                _record(f"{name}.{attr}", kind, lineno)
    return found


def merge_ref_entry(
    target: Dict[str, Any], entry: Dict[str, Any], limit: int = 12
) -> Dict[str, Any]:
    """Fold one extraction result into an accumulating ``{ref: entry}`` map.

    Shared by every caller that gathers references from more than one source
    (several plans, several files in a diff) so the three list keys cannot
    drift apart — a ``read_references`` entry silently dropped by one caller
    and kept by another is how a finding ends up pointing at a docstring.
    """
    for key in ("match_kinds", "references", "read_references"):
        merged = list(dict.fromkeys((target.get(key) or []) + (entry.get(key) or [])))
        target[key] = sorted(merged) if key == "match_kinds" else merged[:limit]
    return target


def _added_lines_by_file(diff_text: str) -> Dict[str, List[Tuple[int, str]]]:
    """Parse a unified diff into ``{path: [(new_lineno, added_line), ...]}``.

    Only ADDED lines are read. A diff that deletes the last reference to an
    empty table is the opposite of the defect, and reporting it would train the
    reader to ignore the output.
    """
    by_file: Dict[str, List[Tuple[int, str]]] = {}
    path = ""
    lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = target[2:] if target.startswith("b/") else target
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            lineno = int(match.group(1)) if match else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            if path and path != "/dev/null":
                by_file.setdefault(path, []).append((lineno, raw[1:]))
            lineno += 1
        elif not raw.startswith("-"):
            lineno += 1
    return by_file


def diff_refs(base_ref: str, catalogue: set, ignore: Optional[set] = None) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    """Substrate references this branch introduces, committed or not.

    Two diffs, unioned: ``<base_ref>...HEAD`` for what the branch has committed
    and ``HEAD`` for what is still in the working tree. The point of this probe
    is to answer the question BEFORE the code lands, and a session that has not
    committed yet is the most likely caller — reading only the committed half
    would hand that caller a clean report on work it has not saved.
    """
    import subprocess  # noqa: PLC0415 — only needed on this path

    diffs: List[str] = []
    for args in ([f"{base_ref}...HEAD"], ["HEAD"]):
        try:
            proc = subprocess.run(
                ["git", "diff", "--unified=0"] + args,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except Exception as exc:  # noqa: BLE001
            return {}, f"git diff failed: {exc}"
        if proc.returncode != 0:
            return {}, f"git diff exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
        diffs.append(proc.stdout)

    merged: Dict[str, Dict[str, Any]] = {}
    combined: Dict[str, List[Tuple[int, str]]] = {}
    for text in diffs:
        for path, lines in _added_lines_by_file(text).items():
            combined.setdefault(path, []).extend(lines)
    for path, lines in combined.items():
        # The added lines are extracted as one blob, so the extractor's line
        # numbers are positions within that blob. They are mapped back to real
        # file line numbers here, so a finding points at the line that
        # introduced it rather than at the file.
        blob = "\n".join(text for _, text in lines)
        numbers = [num for num, _ in lines]

        def _restamp(refs: List[str]) -> List[str]:
            out = []
            for where in refs:
                _, _, local = where.rpartition(":")
                try:
                    real: Any = numbers[int(local) - 1]
                except (ValueError, IndexError):
                    real = local
                out.append(f"{path}:{real}")
            return out

        for ref, entry in extract_substrate_refs(blob, catalogue, source=path, ignore=ignore).items():
            entry = dict(entry)
            entry["references"] = _restamp(entry.get("references") or [])
            entry["read_references"] = _restamp(entry.get("read_references") or [])
            merge_ref_entry(merged.setdefault(ref, {}), entry)
    return merged, None


def declared_substrates(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The curated substrate list from args/capability_consumption.yaml."""
    cfg = config if config is not None else load_config()
    entries = cfg.get("substrates") or []
    return [e for e in entries if isinstance(e, dict) and e.get("ref")]


def _attach_refs(res: SubstrateResult, meta: Dict[str, Any]) -> SubstrateResult:
    """Copy the extraction metadata onto a probed result."""
    res.references = list(meta.get("references") or [])
    res.read_references = list(meta.get("read_references") or [])
    res.match_kinds = list(meta.get("match_kinds") or [])
    return res


def probe_substrates(
    refs: Optional[Dict[str, Dict[str, Any]]] = None,
    conn=None,
    config: Optional[Dict[str, Any]] = None,
    include_declared: bool = False,
) -> Dict[str, Any]:
    """Probe a set of substrate references and report what is actually in them.

    Args:
        refs: ``{ref: {"match_kinds": [...], "references": [...]}}`` — normally
            the output of :func:`extract_substrate_refs` or :func:`diff_refs`.
        conn: optional open connection (tests pass a seeded one).
        config: optional pre-loaded config dict.
        include_declared: also probe the curated ``substrates:`` list.

    Returns a JSON-serializable report. ``measurable`` false means the answer
    is "this database cannot tell you", never "everything is fine".
    """
    cfg = config if config is not None else load_config()
    refs = dict(refs or {})

    declared_notes: Dict[str, str] = {}
    if include_declared:
        for entry in declared_substrates(cfg):
            ref = str(entry["ref"])
            refs.setdefault(ref, {"match_kinds": ["declared"], "references": []})
            if "declared" not in refs[ref]["match_kinds"]:
                refs[ref]["match_kinds"].append("declared")
            declared_notes[ref] = str(entry.get("note") or "").strip()

    now = datetime.now(timezone.utc)
    owns_conn = conn is None
    backend = "unknown"
    results: List[SubstrateResult] = []
    history: Dict[str, Any] = {"has_history": False, "witnesses": [], "reason": "not probed"}

    try:
        if owns_conn:
            from tools.db.storage import get_connection

            conn = get_connection()
        from tools.db.storage import get_backend

        backend = get_backend()
    except Exception as exc:  # noqa: BLE001
        # No database at all. Every table ref is unmeasurable; config refs still
        # resolve, because they are answered from the tree.
        for ref, meta in sorted(refs.items()):
            res = parse_substrate_ref(ref)
            if res.kind == "config":
                res = _probe_config_substrate(res)
            else:
                res.unmeasured_reason = f"database unreachable: {exc}"
            _attach_refs(res, meta)
            results.append(res)
        history = {"has_history": False, "witnesses": [], "reason": f"database unreachable: {exc}"}
        return _substrate_report(now, backend, history, results, declared_notes)

    try:
        history = operating_history(conn, cfg)
        catalogue = _known_tables(conn)
        for ref, meta in sorted(refs.items()):
            if history["has_history"] or parse_substrate_ref(ref).kind == "config":
                res = probe_substrate(conn, ref, catalogue)
            else:
                # Rule 1. Not "populated", not "empty" — unmeasurable, with the
                # witness evidence attached so the reader can see why.
                res = parse_substrate_ref(ref)
                res.unmeasured_reason = history["reason"]
            _attach_refs(res, meta)
            results.append(res)
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    return _substrate_report(now, backend, history, results, declared_notes)


def _substrate_report(
    now: datetime,
    backend: str,
    history: Dict[str, Any],
    results: List[SubstrateResult],
    declared_notes: Dict[str, str],
) -> Dict[str, Any]:
    for res in results:
        note = declared_notes.get(res.ref)
        if note and not res.note:
            res.note = note
    # A column of an empty table is empty by arithmetic, not by its own defect.
    # Reporting both doubles every finding and points the reader at a column
    # when the answer is "the whole table has no writer".
    empty_tables = {r.table for r in results if r.kind == "table" and r.status == "empty"}
    for res in results:
        if res.kind == "column" and res.table in empty_tables:
            res.superseded_by = res.table
    findings = [r for r in results if r.is_finding]
    measured = [r for r in results if r.measurable]
    by_status: Dict[str, int] = {}
    for res in results:
        key = res.status if res.measurable else "unmeasurable"
        by_status[key] = by_status.get(key, 0) + 1
    return {
        "generated_at": now.isoformat(),
        "backend": backend,
        "operating_history": history,
        "measurable": bool(history.get("has_history")),
        "substrates": [r.to_dict() for r in results],
        "findings": [r.to_dict() for r in findings],
        "totals": {
            "probed": len(results),
            "measured": len(measured),
            "unmeasurable": len(results) - len(measured),
            "findings": len(findings),
            "by_status": by_status,
        },
    }


def format_substrate_text(report: Dict[str, Any]) -> str:
    lines = [
        "CUI // SP-CTI",
        f"Substrate probe — backend={report['backend']}, "
        f"{report['totals']['probed']} reference(s)",
        "",
    ]
    if not report["operating_history"]["has_history"]:
        lines += [
            f"WARN: {report['operating_history']['reason']}",
            "      Table substrates are reported UNMEASURABLE rather than empty.",
            "",
        ]
    lines += [
        f"{'substrate':<44} {'status':<19} {'rows':>10}  where",
        "-" * 100,
    ]
    for s in report["substrates"]:
        status = s["status"] if s["measurable"] else "UNMEASURABLE"
        rows = "?" if s["rows"] is None else str(s["rows"])
        if s["kind"] == "column" and s["populated"] is not None:
            rows = f"{s['populated']}/{s['rows']}"
        where = ", ".join(s["references"][:2]) or ("(declared)" if "declared" in s["match_kinds"] else "")
        lines.append(f"{s['ref']:<44} {status:<19} {rows:>10}  {where}")
    lines += [
        "-" * 100,
        f"Findings: {report['totals']['findings']}",
    ]
    for s in report["findings"]:
        lines.append(f"  {s['ref']}: {s['status']} — {s['note']}")
        for where in (s["read_references"] or s["references"])[:5]:
            lines.append(f"      designed against at {where}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def collect(
    window_days: Optional[int] = None,
    only: Optional[List[str]] = None,
    conn=None,
    config: Optional[Dict[str, Any]] = None,
    include_substrates: bool = False,
) -> Dict[str, Any]:
    """Measure consumption for every enabled capability class.

    Args:
        window_days: Lookback in days. Defaults to the config value (30).
        only: Restrict to these capability class names.
        conn: Optional open connection (tests pass a seeded one).
        config: Optional pre-loaded config dict.
        include_substrates: Also probe the curated ``substrates:`` list and
            attach it under ``substrates``. OFF by default, and deliberately:
            ``check_capability_liveness`` calls this twice per commit and its
            fast-tier budget is ~0.75s, so a COUNT(*) fan-out it does not read
            would be a per-commit cost for nothing. The substrate CLI flags
            (``--substrates``, ``--probe-*``) probe them directly instead of
            through here.

    Returns:
        A JSON-serializable report. ``totals.unmeasurable_classes`` is the
        number that matters for --gate: a class nobody can count is worse than
        a class counted at zero. The substrate half never feeds that number —
        a zero-row substrate is a finding for a human, and folding it into the
        liveness budget would couple that ratchet to the size of the schema.
    """
    cfg = config if config is not None else load_config()
    days = int(window_days or cfg.get("window_days") or DEFAULT_WINDOW_DAYS)
    threshold = int(cfg.get("inert_threshold", DEFAULT_INERT_THRESHOLD))
    max_listed = int(cfg.get("max_listed_units", DEFAULT_MAX_LISTED_UNITS))
    class_cfg = cfg.get("classes") or {}

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    owns_conn = conn is None
    if owns_conn:
        from tools.db.storage import get_connection

        conn = get_connection()

    results: List[ClassResult] = []
    try:
        for name, probe in PROBES.items():
            entry = class_cfg.get(name)
            if isinstance(entry, dict) and entry.get("enabled") is False:
                continue
            if only and name not in only:
                continue
            try:
                res = probe(conn, since, threshold, max_listed)
            except Exception as exc:  # noqa: BLE001 — one bad probe must not
                # take the whole report down, but it must not look like a zero.
                LOG.warning("probe %s failed: %s", name, exc)
                _rollback(conn)
                res = _unmeasured(name, "", "", f"probe raised: {exc}")
            if isinstance(entry, dict):
                res.description = str(entry.get("description") or "").strip()
            results.append(res)
        substrate_report: Optional[Dict[str, Any]] = None
        if include_substrates:
            try:
                substrate_report = probe_substrates(conn=conn, config=cfg, include_declared=True)
            except Exception as exc:  # noqa: BLE001 — same rule as a probe: a
                # failure here is reported as unmeasurable, never as clean.
                LOG.warning("substrate probe failed: %s", exc)
                _rollback(conn)
                substrate_report = {
                    "measurable": False,
                    "operating_history": {"has_history": False, "witnesses": [], "reason": str(exc)},
                    "substrates": [],
                    "findings": [],
                    "totals": {"probed": 0, "measured": 0, "unmeasurable": 0, "findings": 0, "by_status": {}},
                }
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    measured = [r for r in results if r.telemetry_available]
    by_class = {r.capability_class: r for r in results}

    known_inert = []
    for case in cfg.get("known_inert_cases") or []:
        if not isinstance(case, dict):
            continue
        cls = str(case.get("capability_class") or "")
        res = by_class.get(cls)
        # Two shapes of case. Most are "this one capability was never used", so
        # the class-wide event count answers it. `inert_units` is for a case
        # about *which* units in a busy class went untouched — a reflex class
        # with 74 runs still has the xbm-wake defect if any declared reflex sat
        # at zero, and a class-wide event count would hide exactly that.
        metric = str(case.get("metric") or "events")
        if metric not in ("events", "inert_units"):
            metric = "events"
        case_measured = bool(res and res.telemetry_available)
        value = None
        still_inert = None
        if case_measured:
            value = res.events if metric == "events" else res.inert
            still_inert = value <= threshold if metric == "events" else value > 0
        known_inert.append({
            "id": case.get("id"),
            "capability_class": cls,
            "note": str(case.get("note") or "").strip(),
            "metric": metric,
            "measured": case_measured,
            "value": value,
            "still_inert": still_inert,
            "unmeasured_reason": res.unmeasured_reason if res else "class not evaluated",
        })

    from tools.db.storage import get_backend

    report: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "window_days": days,
        "window_start": since.isoformat(),
        "inert_threshold": threshold,
        "backend": get_backend(),
        "classes": [r.to_dict() for r in results],
        "known_inert_cases": known_inert,
        "totals": {
            "classes_evaluated": len(results),
            "classes_measured": len(measured),
            "unmeasurable_classes": len(results) - len(measured),
            "declared_units": sum(r.declared for r in measured),
            "consumed_units": sum(r.consumed for r in measured),
            "inert_units": sum(r.inert for r in measured),
            "consumption_events": sum(r.events for r in measured),
            "fully_inert_classes": sorted(
                r.capability_class for r in measured if r.events <= threshold
            ),
        },
    }
    if substrate_report is not None:
        report["substrates"] = substrate_report
        report["totals"]["substrate_findings"] = substrate_report["totals"]["findings"]
    return report


def format_text(report: Dict[str, Any]) -> str:
    lines = [
        "CUI // SP-CTI",
        f"Capability consumption — last {report['window_days']}d "
        f"(since {report['window_start'][:19]}, backend={report['backend']})",
        "",
        f"{'class':<24} {'declared':>9} {'consumed':>9} {'inert':>7} {'events':>9}  telemetry",
        "-" * 88,
    ]
    for c in report["classes"]:
        if not c["telemetry_available"]:
            lines.append(
                f"{c['capability_class']:<24} {'?':>9} {'?':>9} {'?':>7} {'?':>9}  "
                f"UNMEASURABLE — {c['unmeasured_reason']}"
            )
            continue
        lines.append(
            f"{c['capability_class']:<24} {c['declared']:>9} {c['consumed']:>9} "
            f"{c['inert']:>7} {c['events']:>9}  {c['telemetry_table']}"
        )
    t = report["totals"]
    lines += [
        "-" * 88,
        f"{'TOTAL':<24} {t['declared_units']:>9} {t['consumed_units']:>9} "
        f"{t['inert_units']:>7} {t['consumption_events']:>9}",
        "",
        f"Fully inert classes : {', '.join(t['fully_inert_classes']) or '(none)'}",
        f"Unmeasurable classes: {t['unmeasurable_classes']}",
        "",
        "Known inert cases:",
    ]
    for case in report["known_inert_cases"]:
        if not case["measured"]:
            state = f"UNMEASURABLE ({case['unmeasured_reason']})"
        else:
            verdict = "STILL INERT" if case["still_inert"] else "consumed"
            state = f"{case['metric']}={case['value']} — {verdict}"
        lines.append(f"  {str(case['id']):<24} [{case['capability_class']}] {state}")
    return "\n".join(lines)


def _run_substrate_mode(args: argparse.Namespace) -> int:
    """CLI glue for the substrate half: gather references, probe, report, gate.

    The catalogue is read once and every source is matched against it, so a
    reference is only ever reported when it names something that genuinely
    exists in the database in front of us.
    """
    cfg = load_config()
    ignore = {str(n) for n in ((cfg.get("substrate_probe") or {}).get("ignore_names") or [])}
    refs: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    for ref in args.probe_substrate or []:
        refs.setdefault(str(ref), {"match_kinds": ["explicit"], "references": []})

    catalogue: Optional[set] = None
    if args.probe_plan or args.probe_diff:
        conn = None
        try:
            from tools.db.storage import get_connection

            conn = get_connection()
            catalogue = _known_tables(conn)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"catalogue unreadable: {exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    if catalogue is None and (args.probe_plan or args.probe_diff):
        # Without the catalogue every token in a plan is a candidate, so the
        # honest move is to report that we could not look rather than to guess
        # at table names out of prose.
        errors.append(
            "cannot extract references from a plan or diff without the database "
            "table catalogue — reporting nothing found rather than guessing"
        )
        catalogue = set()

    for path_str in args.probe_plan or []:
        path = Path(path_str)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            errors.append(f"plan not found: {path_str}")
            continue
        found = extract_substrate_refs(
            path.read_text(encoding="utf-8", errors="replace"),
            catalogue or set(),
            source=path_str,
            ignore=ignore,
        )
        for ref, entry in found.items():
            merge_ref_entry(refs.setdefault(ref, {}), entry)

    if args.probe_diff:
        found, err = diff_refs(args.probe_diff, catalogue or set(), ignore=ignore)
        if err:
            errors.append(err)
        for ref, entry in found.items():
            merge_ref_entry(refs.setdefault(ref, {}), entry)

    report = probe_substrates(refs, config=cfg, include_declared=bool(args.substrates))
    report["errors"] = errors

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_substrate_text(report))
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)

    if not args.substrate_gate:
        return 0
    if not report["measurable"]:
        # Rule 1, at the exit code. A database with no operating history cannot
        # fail this gate, because every substrate on it reads as inert and a
        # gate that fails on a fresh worktree gets switched off within a week.
        print(
            f"GATE WARN: {report['operating_history']['reason']} — substrate gate not enforced",
            file=sys.stderr,
        )
        return 0
    if report["totals"]["findings"]:
        print(
            "GATE FAIL: designed against "
            f"{report['totals']['findings']} zero-row substrate(s): "
            + ", ".join(f["ref"] for f in report["findings"]),
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count consumption per declared capability class over a recent window."
    )
    parser.add_argument("--json", action="store_true", help="Emit the full JSON report")
    parser.add_argument("--window-days", type=int, default=None, help="Lookback window in days")
    parser.add_argument(
        "--class", dest="classes", action="append", default=None,
        choices=sorted(PROBES),
        help="Restrict to one capability class (repeatable)",
    )
    parser.add_argument(
        "--known-inert", action="store_true",
        help="Report only the known-inert case registry",
    )
    parser.add_argument(
        "--gate", action="store_true",
        help="Exit 1 if any capability class could not be measured at all",
    )
    parser.add_argument(
        "--probe-substrate", dest="probe_substrate", action="append", default=None,
        metavar="REF",
        help="Probe one substrate: TABLE, TABLE.COLUMN or file.yaml::key.path (repeatable)",
    )
    parser.add_argument(
        "--probe-plan", dest="probe_plan", action="append", default=None, metavar="PATH",
        help="Probe every substrate a plan/spec/source file references (repeatable)",
    )
    parser.add_argument(
        "--probe-diff", dest="probe_diff", nargs="?", const="origin/main", default=None,
        metavar="REF",
        help="Probe substrates introduced by `git diff REF...HEAD` (default origin/main)",
    )
    parser.add_argument(
        "--substrates", action="store_true",
        help="Probe the curated substrate list in args/capability_consumption.yaml",
    )
    parser.add_argument(
        "--substrate-gate", action="store_true",
        help="Exit 1 if a probed substrate is empty (never on an unmeasurable database)",
    )
    args = parser.parse_args(argv)

    if args.probe_substrate or args.probe_plan or args.probe_diff or args.substrates:
        return _run_substrate_mode(args)

    report = collect(window_days=args.window_days, only=args.classes)

    if args.known_inert:
        payload: Any = report["known_inert_cases"]
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            for case in payload:
                print(
                    f"{case['id']}: {case['metric']}={case['value']} "
                    f"still_inert={case['still_inert']}"
                )
    elif args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_text(report))

    if args.gate and report["totals"]["unmeasurable_classes"] > 0:
        unmeasurable = [
            c["capability_class"] for c in report["classes"] if not c["telemetry_available"]
        ]
        print(
            f"GATE FAIL: {len(unmeasurable)} capability class(es) unmeasurable: "
            f"{', '.join(unmeasurable)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
