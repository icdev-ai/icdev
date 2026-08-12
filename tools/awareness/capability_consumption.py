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
  * GEPA: dispatched every 24h, but its selection predicate requires
    ``composite_score - baseline_score >= 0.05`` and every queued artifact has
    the two equal, so it can never select one;
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

CLI:
    python tools/awareness/capability_consumption.py --json
    python tools/awareness/capability_consumption.py --json --window-days 7
    python tools/awareness/capability_consumption.py --json --class reflex
    python tools/awareness/capability_consumption.py --known-inert --json
    python tools/awareness/capability_consumption.py --gate
"""
from __future__ import annotations

import argparse
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
    """Tools the approval policy enumerates by tier vs. gate decisions recorded."""
    res = ClassResult(
        capability_class="agent_approval_rule",
        declaration_source="args/agent_approval_policy.yaml (tools.*)",
        telemetry_table="agent_approval_log",
    )
    try:
        from tools.agent_runtime.approval_gate import TIERS, load_policy

        policy = load_policy()
        tools_by_tier = policy.get("tools") or {}
        declared = [
            str(name).lower()
            for tier in TIERS
            for name in (tools_by_tier.get(tier) or [])
        ]
    except Exception as exc:  # noqa: BLE001
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            f"declaration source unreadable: {exc}",
        )

    from tools.db.storage import table_exists

    if not table_exists(conn, "agent_approval_log"):
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            "agent_approval_log does not exist",
        )
    try:
        counts = _count_by_key(conn, "agent_approval_log", "tool_name", "decided_at", since)
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "agent_approval_rule", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )
    counts = {str(k).lower(): v for k, v in counts.items()}
    return _finish(res, declared, counts, threshold, max_listed)


def probe_mcp_tool_authorization(
    conn, since: datetime, threshold: int, max_listed: int
) -> ClassResult:
    """D261 RBAC roles vs. MCPToolAuthorizer verdicts the HTTP surface recorded.

    The verdict payload is a JSON blob in ``audit_platform.details``. It is read
    raw and parsed in Python rather than picked apart with json_extract, which
    is SQLite-only dialect and does not survive on the PostgreSQL primary.
    """
    res = ClassResult(
        capability_class="mcp_tool_authorization",
        declaration_source="args/owasp_agentic_config.yaml (mcp_authorization.role_tool_matrix)",
        telemetry_table="audit_platform (event_type='mcp.authz')",
    )
    try:
        import yaml

        cfg = yaml.safe_load(
            _repo_file("args/owasp_agentic_config.yaml").read_text(encoding="utf-8")
        ) or {}
        matrix = (cfg.get("mcp_authorization") or {}).get("role_tool_matrix") or {}
        declared = sorted(str(r).lower() for r in matrix)
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
    """GEPA: skills queued for optimization vs. artifacts it actually applied.

    Also reports how many queued artifacts even *satisfy* GEPA's own selection
    predicate. That number being zero while the queue is full is the difference
    between "nothing to do" and "structurally cannot ever act", and only the
    second one is a defect.
    """
    res = ClassResult(
        capability_class="skill_optimizer",
        declaration_source="agent_improvement_artifacts (distinct skill_used)",
        telemetry_table="agent_improvement_artifacts",
    )
    from tools.db.storage import table_exists

    if not table_exists(conn, "agent_improvement_artifacts"):
        return _unmeasured(
            "skill_optimizer", res.declaration_source, res.telemetry_table,
            "agent_improvement_artifacts does not exist",
        )
    try:
        # Declared units are the skills GEPA was asked to improve, across every
        # status — NOT just the pending queue. An artifact GEPA applies leaves
        # that queue, so scoping the declaration to 'pending' would make a skill
        # structurally incapable of ever being counted as consumed: the exact
        # never-goes-green shape this tool exists to catch.
        all_rows = conn.execute(
            "SELECT skill_used, composite_score, baseline_score, status "
            "FROM agent_improvement_artifacts"
        ).fetchall()
        counts = _count_by_key(
            conn, "agent_improvement_artifacts", "skill_used", "applied_at", since
        )
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        return _unmeasured(
            "skill_optimizer", res.declaration_source, res.telemetry_table,
            f"query failed: {exc}",
        )

    declared: List[str] = []
    pending = 0
    selectable = 0
    for row in all_rows:
        rec = dict(row)
        declared.append(str(rec.get("skill_used") or "(unattributed)"))
        if str(rec.get("status") or "") != "pending":
            continue
        pending += 1
        composite = rec.get("composite_score")
        baseline = rec.get("baseline_score")
        if composite is None or baseline is None:
            continue
        # Mirrors gepa_optimizer._get_pending_artifacts: composite >= 0.60 and
        # a delta of at least 0.05 over baseline.
        if float(composite) >= 0.60 and float(composite) - float(baseline) >= 0.05:
            selectable += 1

    counts = {(k or "(unattributed)"): v for k, v in counts.items()}
    res = _finish(res, declared, counts, threshold, max_listed)
    res.extra["artifacts_total"] = len(all_rows)
    res.extra["pending_artifacts"] = pending
    res.extra["artifacts_matching_selection_predicate"] = selectable
    return res


PROBES: Dict[str, Callable[..., ClassResult]] = {
    "reflex": probe_reflex,
    "mcp_dispatch_tool": probe_mcp_dispatch_tool,
    "agent_approval_rule": probe_agent_approval_rule,
    "mcp_tool_authorization": probe_mcp_tool_authorization,
    "prompt_template": probe_prompt_template,
    "audit_chain": probe_audit_chain,
    "skill_optimizer": probe_skill_optimizer,
}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def collect(
    window_days: Optional[int] = None,
    only: Optional[List[str]] = None,
    conn=None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Measure consumption for every enabled capability class.

    Args:
        window_days: Lookback in days. Defaults to the config value (30).
        only: Restrict to these capability class names.
        conn: Optional open connection (tests pass a seeded one).
        config: Optional pre-loaded config dict.

    Returns:
        A JSON-serializable report. ``totals.unmeasurable_classes`` is the
        number that matters for --gate: a class nobody can count is worse than
        a class counted at zero.
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

    return {
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
    args = parser.parse_args(argv)

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
