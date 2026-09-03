# CUI // SP-CTI
"""Asset visibility that cannot fabricate a percentage (rmf-vis-01).

THE DEFECT THIS REFUSES. "Asset visibility: 100%" over an estate nobody has
sized. The discovery stack can always count what it HAS seen; nothing in the
tree could say what fraction of the estate that is, because no denominator was
ever registered -- and the shape a coverage widget reaches for when the
denominator is missing is ``pct = seen / total * 100 if total else 100.0``,
which draws a full green bar for a fabric nothing has ever scanned. Three of
the four defects fixed on 2026-08-20 were that literal
(``args/perfect_score_gate.yaml``, ratcheted to 0). A missing number sends
somebody to measure; a perfect one closes the question.

TWO NUMBERS, AND ONLY ONE OF THEM NEEDS A DENOMINATOR.

  CORROBORATION DEPTH is always reportable. It is distinct ``(asset, source)``
  PAIRS over distinct assets -- how many independent sources agree that each
  asset exists. It needs nothing declared, so it is a real measurement on
  every deployment including one with no CMDB at all, and it is the number
  this module leads with.

  VISIBILITY PCT needs an authoritative denominator registered in
  ``args/asset_denominators.yaml`` for that fabric. Without one it is
  ``None`` -- never 0.0, never 100.0 -- and every renderer prints the words
  "not assessed".

PAIRS, NEVER ROWS. ``odc_gap_scores`` holds 91 rows spanning a month carrying
ONE distinct value for ONE subject: a single stuck writer that any row-counting
confidence model rates as extremely well corroborated. The same trap is live
here -- the ZIG scanner re-registers a device on every sweep, and the NetBox
adapter re-reports the whole inventory every run. Counting rows would let one
chatty source manufacture arbitrary corroboration for an estate a single
source has seen. REPETITION IS NOT CORROBORATION.

FOUR RANKED DENOMINATOR KINDS, and the losers are REPORTED, never averaged in:
``approved_cmdb`` > ``ip_allocation_plan`` > ``dhcp_scope`` >
``derived_if_mib``. Two declarations that disagree about the size of an estate
is a finding for a human; splitting the difference deletes it. The ranking,
the confidence priors, the units and the bias directions are all DATA in the
YAML -- this module names no vendor, product, protocol or site.

``denominator_source`` AND ``denominator_confidence`` TRAVEL WITH THE NUMBER,
persisted and rendered, because "43% against an approved CMDB" and "43% of a
switch's own port count" are different claims and a reader who cannot tell
them apart has been misled by an arithmetically correct number. So does
``denominator_unit``: ``derived_if_mib`` counts PORTS, and a port is not an
asset.

A NUMERATOR OVER ITS DENOMINATOR IS NOT CLAMPED TO 100. It means the
denominator is wrong or stale, which is the one fact worth acting on, and
clamping hides it. ``numerator_exceeds_denominator`` says so and the
percentage is reported as computed.

A SYNTHETIC ROW IS NOT AN OBSERVATION. The live board's 24 ``ni_devices`` rows
are all ``source='synthetic'`` and their own ``notes`` column says "Synthetic
demo device -- fabricated, not an observed asset." Counting them would put a
demo fixture in the numerator of a compliance claim. They are excluded BY
NAME, counted under ``excluded``, and never silently dropped -- an excluded
asset that vanishes from the report is indistinguishable from one that was
never discovered.

FABRIC ATTRIBUTION IS DERIVED, AND ITS ABSENCE IS A FINDING. ``asset_identity``
(rmf-ident-01) carries no fabric column and its ``ingest()`` does not read
``ni_devices.source``, so the fabric and the evidence class are recovered by
joining back on ``ni_device_id`` and reading
``properties_json.discovery.fabric`` (rmf-disc-01's sink writes it there
precisely so a row's origin stays recoverable). An asset no fabric claims is
``unattributed`` -- its own bucket, never folded into a fabric and never
dropped.

THE JOIN IS IN PYTHON, NOT SQL. On PostgreSQL the identity table and the
network canvas share the ``icdev`` database and a SQL JOIN would work; on
SQLite the canvas has its own file, so the same JOIN would silently return
nothing. Same rule CLAUDE.md already states for JSON.

Public API
----------
load_config(path=None)          -> the denominator declaration
kind_spec(kind, config)         -> rank / confidence / unit / bias for a kind
corroboration(assets)           -> pairs, depth and the tier histogram
resolve_denominator(...)        -> the ranked winner + the losers, or None
visibility_pct(observed, denom) -> the ONE place a percentage is computed
measure(fabric=None, ...)       -> the whole report, per fabric
record_snapshot(report, ...)    -> append to asset_visibility_snapshots
list_snapshots(...)             -> read the series back
render(report)                  -> the human table; prints "not assessed"

CLI
---
python -m tools.assets.visibility --measure [--fabric enterprise] [--json]
python -m tools.assets.visibility --measure --record
python -m tools.assets.visibility --denominators [--json]
python -m tools.assets.visibility --history [--fabric enterprise] [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# The sys.path BOOTSTRAP, and nothing else. Every use of this name is a
# sys.path expression, so it answers "where do I import from" -- a question a
# __file__ climb answers correctly before AND after a move, which is why
# xit-decl-03 exempts the idiom.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))


def _root() -> Path:
    """xit-decl-03: the ONE root resolver for a REPO-RELATIVE path.

    Deliberately NOT ``_BOOTSTRAP_ROOT``. That name is a hard-coded claim
    about where this file sits, true today and silently wrong the moment the
    module moves into the kernel package -- at which point it would resolve
    ``args/asset_denominators.yaml`` against the wrong tree and
    :func:`load_config` would report "declaration file not present", i.e. a
    fabricated "nothing is declared" rather than an error.
    """
    from icdev.core.paths import repo_root

    return repo_root(__file__)

TABLE = "asset_visibility_snapshots"
IDENTITY_TABLE = "asset_identity"
CONFIG_RELPATH = "args/asset_denominators.yaml"
ADAPTERS_RELPATH = "args/discovery_adapters.yaml"

#: Ranked BEST FIRST. The ranking lives in the YAML; this tuple is the
#: vocabulary a declaration may use, so a typo is refused rather than
#: silently producing a denominator that never resolves.
KIND_APPROVED_CMDB = "approved_cmdb"
KIND_IP_ALLOCATION_PLAN = "ip_allocation_plan"
KIND_DHCP_SCOPE = "dhcp_scope"
KIND_DERIVED_IF_MIB = "derived_if_mib"
DENOMINATOR_KINDS: tuple[str, ...] = (
    KIND_APPROVED_CMDB,
    KIND_IP_ALLOCATION_PLAN,
    KIND_DHCP_SCOPE,
    KIND_DERIVED_IF_MIB,
)

#: The only kind whose value is COMPUTED rather than declared.
DERIVED_KINDS: tuple[str, ...] = (KIND_DERIVED_IF_MIB,)

#: Three states, and the middle one is the whole card.
STATE_UNMEASURABLE = "unmeasurable"   # neither number is a measurement
STATE_NOT_ASSESSED = "not_assessed"   # assets counted, NO denominator -> pct None
STATE_ASSESSED = "assessed"           # both sides present -> pct is real
VISIBILITY_STATES: tuple[str, ...] = (
    STATE_UNMEASURABLE,
    STATE_NOT_ASSESSED,
    STATE_ASSESSED,
)

#: What a renderer prints where a percentage would go when there is none. The
#: acceptance criterion names this string.
NOT_ASSESSED_LABEL = "not assessed"

#: ``ni_devices.source`` values that are NOT an observation of a real estate.
#: `synthetic` rows carry "fabricated, not an observed asset" in their own
#: notes column. Excluded BY NAME and counted, never dropped.
NON_OBSERVATION_SOURCES: tuple[str, ...] = ("synthetic",)

#: A row whose evidence class is NULL/empty. Not an observation either -- we
#: cannot say where it came from, and "unknown provenance" cannot support a
#: coverage claim -- but a DIFFERENT fact from a known fabrication, so it gets
#: its own bucket rather than being merged into the one above.
EXCLUDED_UNATTRIBUTED_SOURCE = "unattributed_source"
EXCLUDED_SYNTHETIC = "synthetic"

#: Assets no fabric claims. Reported as a pseudo-fabric so they are visible;
#: never merged into a declared fabric's numerator.
FABRIC_UNATTRIBUTED = "(unattributed)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------

def default_config_path() -> Path:
    return _root() / CONFIG_RELPATH


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Read the denominator declaration.

    An unreadable or absent file is NOT an error and NOT an empty estate: it
    means nothing is declared, which is exactly the ``not_assessed`` case. The
    reason is carried on the result so a reader can tell "no file" apart from
    "a file declaring nothing".
    """
    import yaml

    p = Path(path) if path else default_config_path()
    if not p.exists():
        return {"kinds": [], "fabrics": {}, "derived_if_mib": {},
                "config_path": str(p), "readable": False,
                "reason": "declaration file not present"}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - a malformed declaration
        return {"kinds": [], "fabrics": {}, "derived_if_mib": {},
                "config_path": str(p), "readable": False,
                "reason": f"declaration unreadable: {exc}"}
    if not isinstance(raw, dict):
        return {"kinds": [], "fabrics": {}, "derived_if_mib": {},
                "config_path": str(p), "readable": False,
                "reason": "declaration is not a mapping"}
    raw.setdefault("kinds", [])
    raw.setdefault("fabrics", {})
    raw.setdefault("derived_if_mib", {})
    raw["config_path"] = str(p)
    raw["readable"] = True
    return raw


def kind_spec(kind: str, config: dict[str, Any] | None = None) -> Optional[dict[str, Any]]:
    """rank / confidence / unit / bias for one kind, or None if undeclared.

    Returns None rather than a default so an unrecognised ``kind:`` in a
    fabric block cannot quietly acquire rank 0 and outrank a real CMDB.
    """
    config = config if config is not None else load_config()
    for entry in config.get("kinds") or []:
        if isinstance(entry, dict) and str(entry.get("kind")) == kind:
            return {
                "kind": kind,
                "rank": int(entry.get("rank", len(DENOMINATOR_KINDS))),
                "confidence": entry.get("confidence"),
                "unit": entry.get("unit"),
                "bias": entry.get("bias"),
                "description": entry.get("description"),
            }
    return None


def declared_fabrics(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Fabric id -> its declaration, read from the DISCOVERY config.

    The fabric registry is ``args/discovery_adapters.yaml`` (rmf-disc-01);
    this module does not declare a second one. A fabric that appears in the
    denominator file but nowhere in the discovery declaration is still
    reported -- an undeclared fabric is a finding, not a reason to hide it.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        import yaml

        p = _root() / ADAPTERS_RELPATH
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for fab in raw.get("fabrics") or []:
            if isinstance(fab, dict) and fab.get("id"):
                out[str(fab["id"])] = {
                    "name": fab.get("name"),
                    # The LABEL (UNCLASSIFIED / CUI / SECRET), never a banner.
                    "classification": fab.get("classification"),
                    "declared": True,
                }
    except Exception:  # noqa: BLE001 - an absent registry is not an error here
        return out
    return out


# ---------------------------------------------------------------------------
# Corroboration -- distinct (asset, source) PAIRS, never rows
# ---------------------------------------------------------------------------

def corroboration(assets: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Depth and the tier histogram over a set of assets.

    ``pairs`` is the size of the set of ``(asset_id, source)`` tuples. It is
    built as an actual set so that a source reporting the same asset forty
    times contributes ONE pair -- the property a test can prove, and the one
    a row count silently violates.

    ``depth`` is pairs / assets. It is None -- never 0.0 -- over an empty
    asset set, because "we looked and every asset has zero sources" and "we
    have no assets to look at" justify opposite actions.
    """
    pairs: set[tuple[str, str]] = set()
    tiers: dict[str, int] = {}
    ids: set[str] = set()
    for a in assets or ():
        aid = str(a.get("asset_id") or "")
        if not aid:
            continue
        ids.add(aid)
        for src in a.get("discovery_sources") or ():
            if src:
                pairs.add((aid, str(src)))
        tier = str(a.get("corroboration_tier") or "unconfirmed")
        tiers[tier] = tiers.get(tier, 0) + 1
    n = len(ids)
    # Share of assets that MORE THAN ONE distinct source reported. Its
    # denominator is the OBSERVED SET, which is measured rather than declared
    # -- a different denominator from visibility_pct's, and labelled as such.
    multi = 0
    per_asset: dict[str, int] = {}
    for aid, _src in pairs:
        per_asset[aid] = per_asset.get(aid, 0) + 1
    multi = sum(1 for v in per_asset.values() if v >= 2)
    return {
        "assets": n,
        "pairs": len(pairs),
        "depth": (len(pairs) / n) if n else None,
        "corroborated_assets": multi if n else None,
        "corroborated_share_pct": _rate(multi, n),
        "corroborated_share_denominator": "observed_assets (measured)",
        "tiers": tiers,
    }


def _rate(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    """The ONE place a percentage is computed in this module.

    Returns None over a missing, zero or negative denominator. Writing
    ``pct if total else 100.0`` here -- or ``else 0.0`` -- is the defect
    ``args/perfect_score_gate.yaml`` is ratcheted to 0 to prevent, and a
    single choke point is what makes that checkable by reading one function.

    NOT clamped to 100: a numerator over its denominator means the
    denominator is wrong, and hiding that is worse than printing 118.2%.
    """
    if numerator is None or denominator is None:
        return None
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)


def visibility_pct(observed: Optional[int], denominator: Optional[int]) -> Optional[float]:
    """Coverage, or None. Never 0.0-by-fallback, never 100.0-by-fallback."""
    return _rate(observed, denominator)


# ---------------------------------------------------------------------------
# Denominators
# ---------------------------------------------------------------------------

def derive_if_mib(devices: Iterable[dict[str, Any]],
                  config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Port count from the IF-MIB data discovery already collected.

    ``tools/network/discovery.py`` walks ``1.3.6.1.2.1.2.2.1.2`` and the
    rmf-disc-01 sink persists the result verbatim to
    ``ni_devices.properties_json['interfaces']``, so a switch reports its own
    port count and nothing new has to touch live gear.

    Returns ``{"value": int|None, "measurable": bool, "reason": str, ...}``.
    ``value`` is None -- never 0 -- when no device on the fabric carries
    interface data, because "this fabric has no ports" is a claim and
    "nothing has walked a switch here" is the truth.
    """
    config = config if config is not None else load_config()
    knobs = config.get("derived_if_mib") or {}
    excluded_prefixes = tuple(
        str(p).strip().lower() for p in (knobs.get("exclude_name_prefixes") or ()) if str(p).strip()
    )
    require_status = tuple(
        str(s).strip().lower() for s in (knobs.get("require_oper_status") or ()) if str(s).strip()
    )

    ports = 0
    contributing = 0
    excluded = 0
    devices_seen = 0
    for dev in devices or ():
        devices_seen += 1
        interfaces = (dev.get("_properties") or {}).get("interfaces")
        if not isinstance(interfaces, list) or not interfaces:
            continue
        counted_here = 0
        for iface in interfaces:
            if not isinstance(iface, dict):
                continue
            name = str(iface.get("name") or "").strip().lower()
            if excluded_prefixes and name.startswith(excluded_prefixes):
                excluded += 1
                continue
            if require_status:
                status = str(iface.get("oper_status") or "").strip().lower()
                if status not in require_status:
                    excluded += 1
                    continue
            counted_here += 1
        if counted_here:
            contributing += 1
            ports += counted_here

    if not contributing:
        return {
            "value": None,
            "measurable": False,
            "reason": (
                "no device on this fabric carries IF-MIB interface data -- "
                "SNMP discovery has not run here"
                if devices_seen
                else "no devices attributed to this fabric"
            ),
            "devices_seen": devices_seen,
            "devices_contributing": 0,
            "interfaces_excluded": excluded,
        }
    return {
        "value": ports,
        "measurable": True,
        "reason": "",
        "devices_seen": devices_seen,
        "devices_contributing": contributing,
        "interfaces_excluded": excluded,
    }


def resolve_denominator(
    fabric_id: str,
    *,
    config: dict[str, Any] | None = None,
    devices: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ranked winner for one fabric, with the losers reported beside it.

    Returns ``{"resolved": dict|None, "alternates": [...], "refused": [...],
    "reason": str}``. ``resolved`` is None when nothing is declared, when the
    only declarations name an unknown kind, or when a derived kind has no
    data -- and a None here is what makes ``visibility_pct`` None downstream.

    The winner is the LOWEST rank. Losers are carried verbatim under
    ``alternates`` and are never averaged into it: two sources disagreeing
    about the size of an estate is a finding for a human.
    """
    config = config if config is not None else load_config()
    declarations = (config.get("fabrics") or {}).get(fabric_id) or []
    if isinstance(declarations, dict):  # a single block rather than a list
        declarations = [declarations]

    candidates: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []

    for decl in declarations:
        if not isinstance(decl, dict):
            refused.append({"declaration": repr(decl), "reason": "not a mapping"})
            continue
        kind = str(decl.get("kind") or "")
        spec = kind_spec(kind, config)
        if spec is None:
            # An unrecognised kind is REFUSED, not defaulted. A typo that
            # acquired rank 0 would outrank a real CMDB.
            refused.append({"kind": kind, "reason": "kind is not declared under `kinds:`"})
            continue
        entry: dict[str, Any] = {
            "kind": kind,
            "rank": spec["rank"],
            "confidence": spec["confidence"],
            "unit": spec["unit"],
            "bias": spec["bias"],
            "as_of": decl.get("as_of"),
            "declared_by": decl.get("declared_by"),
            "note": decl.get("note"),
            "derived": kind in DERIVED_KINDS,
        }
        if kind in DERIVED_KINDS:
            derived = derive_if_mib(devices or (), config)
            entry["derivation"] = derived
            if not derived.get("measurable"):
                refused.append({
                    "kind": kind,
                    "reason": derived.get("reason") or "derivation unmeasurable",
                })
                continue
            entry["value"] = int(derived["value"])
        else:
            value = decl.get("value")
            if value is None:
                refused.append({"kind": kind, "reason": "no `value` declared"})
                continue
            try:
                entry["value"] = int(value)
            except (TypeError, ValueError):
                refused.append({"kind": kind, "reason": f"`value` is not an integer: {value!r}"})
                continue
            if entry["value"] <= 0:
                # A declared zero estate would make every percentage None
                # anyway; refusing it here says WHY rather than looking like
                # an absent declaration.
                refused.append({"kind": kind, "reason": f"`value` must be > 0, got {entry['value']}"})
                continue
        candidates.append(entry)

    if not candidates:
        reason = "no denominator declared for this fabric"
        if refused:
            reason = "every declaration for this fabric was refused or unmeasurable"
        return {"resolved": None, "alternates": [], "refused": refused, "reason": reason}

    candidates.sort(key=lambda c: (c["rank"], c["kind"]))
    winner = candidates[0]
    alternates = candidates[1:]
    return {
        "resolved": winner,
        "alternates": alternates,
        "refused": refused,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Reading the stacks
# ---------------------------------------------------------------------------

def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _table_exists(conn, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()  # nosec B608
        return True
    except Exception:  # noqa: BLE001 - unmigrated database, not an error here
        return False


def _read_identity(conn) -> tuple[Optional[list[dict]], str]:
    """(assets, note). None means UNREADABLE, which is not zero assets."""
    if not _table_exists(conn, IDENTITY_TABLE):
        return None, (
            f"{IDENTITY_TABLE} does not exist here -- migration "
            "20260902205902_asset_identity has not run"
        )
    try:
        rows = conn.execute(
            f"SELECT asset_id, hostname, ni_device_id, ni_node_id, "  # nosec B608
            f"discovery_sources, corroboration_tier, enclave_id "
            f"FROM {IDENTITY_TABLE}"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return None, f"{IDENTITY_TABLE} unreadable: {exc}"
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["discovery_sources"] = json.loads(d.get("discovery_sources") or "[]")
        except (TypeError, ValueError):
            d["discovery_sources"] = []
        out.append(d)
    return out, ""


def _read_ni_devices() -> tuple[Optional[list[dict]], str]:
    """(rows, note) from the NETWORK CANVAS connection.

    Its own ``get_connection`` deliberately, not this module's: on SQLite the
    canvas has its own database file, and reading it through the platform
    connection returns nothing while raising nothing.
    """
    try:
        from tools.network.db.init_db import get_connection as nc_conn
    except Exception as exc:  # noqa: BLE001
        return None, f"network canvas not importable: {exc}"
    conn = None
    try:
        conn = nc_conn()
        try:
            rows = conn.execute(
                "SELECT id, node_id, label, topology_id, source, properties_json "
                "FROM ni_devices"
            ).fetchall()
        except Exception:  # noqa: BLE001
            # The `CREATE TABLE IF NOT EXISTS` DDL and the migrated table have
            # diverged (rmf-disc-01 measured it): `source` exists on the live
            # PostgreSQL table and not in every shape. Fall back to the
            # columns every shape has -- provenance then comes from
            # properties_json alone, which is exactly why the sink writes it
            # in both places.
            rows = conn.execute(
                "SELECT id, node_id, label, topology_id, properties_json "
                "FROM ni_devices"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return None, f"ni_devices unreadable: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    out = []
    for r in rows:
        d = dict(r)
        # Parsed in PYTHON, never with json_extract in SQL: this runs on both
        # PostgreSQL and SQLite and the dialects disagree.
        try:
            d["_properties"] = json.loads(d.get("properties_json") or "{}")
        except (TypeError, ValueError):
            d["_properties"] = {}
        disc = d["_properties"].get("discovery") or {}
        d["_fabric"] = str(disc.get("fabric") or "") or None
        # The column when the live table has it, else the copy the sink wrote
        # into properties_json. NEVER a guess: absent in both is None, which
        # is `unattributed_source` downstream and not an observation.
        d["_source_label"] = d.get("source") or disc.get("source_label") or None
        out.append(d)
    return out, ""


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------

def measure(
    fabric: Optional[str] = None,
    *,
    conn=None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-fabric visibility. Never blends fabrics, never invents a rate."""
    own_conn = conn is None
    conn = conn or _conn()
    config = config if config is not None else load_config()
    measured_at = _now()
    report: dict[str, Any] = {
        "measured_at": measured_at,
        "config_path": config.get("config_path"),
        "config_readable": bool(config.get("readable")),
        "fabrics": [],
    }
    if not config.get("readable"):
        report["config_reason"] = config.get("reason")

    try:
        assets, note = _read_identity(conn)
        if assets is None:
            report["measurable"] = False
            report["reason"] = note
            report["identity"] = {"readable": False, "total": None, "reason": note}
            return report
        report["identity"] = {"readable": True, "total": len(assets)}
        if not assets:
            # The table exists and nothing has been ingested. That is NOT an
            # estate of size zero and it is NOT 0% visibility.
            report["measurable"] = False
            report["reason"] = (
                f"{IDENTITY_TABLE} is empty -- nothing has been ingested. Run "
                "`python -m tools.assets.identity --ingest` first."
            )
            return report

        devices, dev_note = _read_ni_devices()
        report["ni_devices"] = (
            {"readable": True, "rows": len(devices)}
            if devices is not None
            else {"readable": False, "rows": None, "reason": dev_note}
        )

        # ---- fabric + evidence-class attribution, joined in Python --------
        by_device_id: dict[str, dict[str, Any]] = {}
        for d in devices or ():
            if d.get("id"):
                by_device_id[str(d["id"])] = d

        fabrics: dict[str, dict[str, Any]] = {}
        registry = declared_fabrics()
        for fid in registry:
            fabrics.setdefault(fid, {"assets": [], "devices": []})

        excluded_global: dict[str, int] = {}
        for a in assets:
            dev = by_device_id.get(str(a.get("ni_device_id") or ""))
            fid = (dev or {}).get("_fabric") or FABRIC_UNATTRIBUTED
            src = (dev or {}).get("_source_label")
            bucket = fabrics.setdefault(fid, {"assets": [], "devices": []})
            if src and str(src).strip().lower() in NON_OBSERVATION_SOURCES:
                bucket.setdefault("excluded", {})
                bucket["excluded"][EXCLUDED_SYNTHETIC] = (
                    bucket.get("excluded", {}).get(EXCLUDED_SYNTHETIC, 0) + 1
                )
                excluded_global[EXCLUDED_SYNTHETIC] = excluded_global.get(EXCLUDED_SYNTHETIC, 0) + 1
                continue
            if dev is not None and not src:
                bucket.setdefault("excluded", {})
                bucket["excluded"][EXCLUDED_UNATTRIBUTED_SOURCE] = (
                    bucket.get("excluded", {}).get(EXCLUDED_UNATTRIBUTED_SOURCE, 0) + 1
                )
                excluded_global[EXCLUDED_UNATTRIBUTED_SOURCE] = (
                    excluded_global.get(EXCLUDED_UNATTRIBUTED_SOURCE, 0) + 1
                )
                continue
            bucket["assets"].append(a)
            if dev is not None:
                bucket["devices"].append(dev)

        # A device on a fabric whose asset never made it into asset_identity
        # still carries interfaces, and derive_if_mib must see it -- otherwise
        # the denominator would shrink exactly when the numerator does.
        for fid_bucket in fabrics.values():
            fid_bucket["_device_ids"] = {
                str(x.get("id")) for x in fid_bucket["devices"]
            }
        for d in devices or ():
            fid = d.get("_fabric") or FABRIC_UNATTRIBUTED
            bucket = fabrics.setdefault(
                fid, {"assets": [], "devices": [], "_device_ids": set()}
            )
            bucket.setdefault("_device_ids", set())
            if str(d.get("id")) not in bucket["_device_ids"]:
                bucket["_device_ids"].add(str(d.get("id")))
                bucket["devices"].append(d)

        report["excluded"] = excluded_global
        report["measurable"] = True

        wanted = [fabric] if fabric else sorted(fabrics)
        for fid in wanted:
            bucket = fabrics.get(fid)
            if bucket is None:
                report["fabrics"].append({
                    "fabric_id": fid,
                    "state": STATE_UNMEASURABLE,
                    "reason": "no such fabric -- not declared and no asset attributed to it",
                    "visibility_pct": None,
                })
                continue
            report["fabrics"].append(
                _measure_fabric(fid, bucket, registry.get(fid), config)
            )
        return report
    finally:
        if own_conn:
            conn.close()


def _measure_fabric(
    fabric_id: str,
    bucket: dict[str, Any],
    declaration: Optional[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    corro = corroboration(bucket["assets"])
    observed = corro["assets"]
    excluded = bucket.get("excluded") or {}

    den = resolve_denominator(fabric_id, config=config, devices=bucket["devices"])
    winner = den["resolved"]

    out: dict[str, Any] = {
        "fabric_id": fabric_id,
        "fabric_declared": bool(declaration),
        "fabric_name": (declaration or {}).get("name"),
        # The LABEL as declared. None when no registry claims this fabric --
        # an asset attributed to an unknown fabric is a finding, not a fabric
        # that happens to carry no label.
        "fabric_classification": (declaration or {}).get("classification"),
        "observed_assets": observed,
        "corroboration_pairs": corro["pairs"],
        "corroboration_depth": corro["depth"],
        "corroborated_assets": corro["corroborated_assets"],
        "corroborated_share_pct": corro["corroborated_share_pct"],
        "corroborated_share_denominator": corro["corroborated_share_denominator"],
        "tiers": corro["tiers"],
        "excluded": excluded,
        "denominator": None,
        "denominator_source": None,
        "denominator_confidence": None,
        "denominator_unit": None,
        "denominator_as_of": None,
        "denominator_rank": None,
        "denominator_declared_by": None,
        "denominator_bias": None,
        "denominator_note": None,
        "denominator_derivation": None,
        "alternates": den["alternates"],
        "denominator_refused": den["refused"],
        "visibility_pct": None,
        "visibility_label": NOT_ASSESSED_LABEL,
        "numerator_exceeds_denominator": False,
        "notes": [],
    }

    # The denominator is recorded whether or not a numerator exists. A fabric
    # declared to hold 40 assets where nothing observable was found is a
    # FINDING, and dropping the winner here would have rendered the LOSING
    # alternate under a blank primary -- which is how this branch first
    # shipped, and it read as though the loser had won.
    if winner is not None:
        out["denominator"] = winner["value"]
        out["denominator_source"] = winner["kind"]
        out["denominator_confidence"] = winner["confidence"]
        out["denominator_unit"] = winner["unit"]
        out["denominator_as_of"] = winner.get("as_of")
        out["denominator_rank"] = winner["rank"]
        out["denominator_declared_by"] = winner.get("declared_by")
        out["denominator_bias"] = winner.get("bias")
        out["denominator_note"] = winner.get("note")
        out["denominator_derivation"] = winner.get("derivation")

    if observed == 0:
        # Every asset attributed here was excluded, or none was attributed at
        # all. Not "0% visible" -- there is no numerator to divide, and a
        # declared denominator must not conjure one.
        out["state"] = STATE_UNMEASURABLE
        out["observed_assets"] = None
        out["corroboration_pairs"] = None
        out["reason"] = (
            "no observed asset is attributed to this fabric"
            + (f" ({sum(excluded.values())} excluded: "
               + ", ".join(f"{k}={v}" for k, v in sorted(excluded.items())) + ")"
               if excluded else "")
        )
        if winner is not None:
            out["notes"].append(
                f"A denominator IS declared for this fabric ({winner['kind']}="
                f"{winner['value']} {winner['unit']}), and nothing observable "
                "was found to divide by. That is a discovery gap, not 0% "
                "coverage."
            )
        return out

    if winner is None:
        out["state"] = STATE_NOT_ASSESSED
        out["reason"] = den["reason"]
        out["notes"].append(
            "Corroboration depth is measured; coverage is not. Register a "
            f"denominator for `{fabric_id}` in {CONFIG_RELPATH} to assess it."
        )
        return out

    pct = visibility_pct(observed, winner["value"])
    if pct is None:
        # STRUCTURAL, not incidental: `assessed` must mean a real percentage
        # exists. `resolve_denominator` refuses a value <= 0 and `observed`
        # is > 0 here, so this is unreachable today -- and a future kind whose
        # derivation returned something `_rate` declines must degrade to
        # `not_assessed` rather than label a null as an assessment.
        out["state"] = STATE_NOT_ASSESSED
        out["reason"] = (
            f"a denominator resolved ({winner['kind']}={winner['value']}) but "
            "no percentage could be computed from it"
        )
        return out

    out["state"] = STATE_ASSESSED
    out["visibility_pct"] = pct
    out["visibility_label"] = f"{pct}%"
    if observed > winner["value"]:
        out["numerator_exceeds_denominator"] = True
        out["notes"].append(
            f"{observed} observed assets against a declared denominator of "
            f"{winner['value']} -- the denominator is wrong or stale. The "
            "percentage is reported as computed and is NOT clamped to 100."
        )
    if winner["unit"] and winner["unit"] != "assets":
        out["notes"].append(
            f"The denominator counts {winner['unit']}, not assets. This "
            "percentage is assets-over-" + str(winner["unit"]) + "."
        )
    return out


# ---------------------------------------------------------------------------
# Persistence -- append-only
# ---------------------------------------------------------------------------

def record_snapshot(report: dict[str, Any], conn=None) -> dict[str, Any]:
    """Append one row per fabric. Never updates -- a snapshot is evidence."""
    own_conn = conn is None
    conn = conn or _conn()
    written: list[str] = []
    try:
        if not _table_exists(conn, TABLE):
            return {
                "recorded": False,
                "reason": (
                    f"{TABLE} does not exist here -- migration "
                    "20260902223458_asset_visibility_snapshots has not run"
                ),
                "written": 0,
            }
        if not report.get("measurable"):
            # An unmeasurable report has no fabric rows, so a bare
            # `written: 0` would read as "recorded successfully, nothing to
            # say" -- the same conflation of "measured nothing" with "could
            # not measure" this whole module exists to refuse.
            return {
                "recorded": False,
                "reason": (
                    "the measurement was UNMEASURABLE, so there is nothing to "
                    "record: " + str(report.get("reason") or "")
                ),
                "written": 0,
            }
        now = _now()
        for fab in report.get("fabrics") or []:
            sid = "avs-" + uuid.uuid4().hex[:16]
            conn.execute(
                f"INSERT INTO {TABLE} ("  # nosec B608
                "snapshot_id, tenant_id, classification, fabric_id, "
                "fabric_classification, measured_at, state, observed_assets, "
                "corroboration_pairs, corroboration_depth, tiers_json, "
                "denominator, denominator_source, denominator_confidence, "
                "denominator_unit, denominator_as_of, denominator_rank, "
                "denominator_declared_by, alternates_json, visibility_pct, "
                "numerator_exceeds, excluded_json, notes, created_at"
                ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    sid,
                    "default",
                    # The RLS LABEL, never a banner.
                    "cui",
                    fab.get("fabric_id"),
                    fab.get("fabric_classification"),
                    report.get("measured_at") or now,
                    fab.get("state") or STATE_UNMEASURABLE,
                    fab.get("observed_assets"),
                    fab.get("corroboration_pairs"),
                    fab.get("corroboration_depth"),
                    json.dumps(fab.get("tiers") or {}, sort_keys=True),
                    fab.get("denominator"),
                    fab.get("denominator_source"),
                    fab.get("denominator_confidence"),
                    fab.get("denominator_unit"),
                    fab.get("denominator_as_of"),
                    fab.get("denominator_rank"),
                    fab.get("denominator_declared_by"),
                    json.dumps(fab.get("alternates") or [], sort_keys=True, default=str),
                    fab.get("visibility_pct"),
                    1 if fab.get("numerator_exceeds_denominator") else 0,
                    json.dumps(fab.get("excluded") or {}, sort_keys=True),
                    "; ".join(str(n) for n in (fab.get("notes") or [])) or fab.get("reason"),
                    now,
                ),
            )
            written.append(sid)
        conn.commit()
        return {"recorded": True, "written": len(written), "snapshot_ids": written}
    finally:
        if own_conn:
            conn.close()


def list_snapshots(
    fabric: Optional[str] = None, limit: int = 20, conn=None
) -> dict[str, Any]:
    own_conn = conn is None
    conn = conn or _conn()
    try:
        if not _table_exists(conn, TABLE):
            return {"readable": False, "reason": f"{TABLE} does not exist here",
                    "snapshots": []}
        if fabric:
            rows = conn.execute(
                f"SELECT * FROM {TABLE} WHERE fabric_id = %s "  # nosec B608
                "ORDER BY measured_at DESC LIMIT %s",
                (fabric, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {TABLE} ORDER BY measured_at DESC LIMIT %s",  # nosec B608
                (int(limit),),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for col in ("tiers_json", "alternates_json", "excluded_json"):
                try:
                    d[col] = json.loads(d.get(col) or ("[]" if "alternates" in col else "{}"))
                except (TypeError, ValueError):
                    pass
            out.append(d)
        return {"readable": True, "snapshots": out}
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _fmt_pct(value: Optional[float]) -> str:
    """The ONE place a percentage becomes text.

    None renders as the words, never as a dash and never as 0. A dash reads
    as a formatting artefact; the words say a human has not been given the
    information.
    """
    return NOT_ASSESSED_LABEL if value is None else f"{value:.1f}%"


def _fmt_depth(value: Optional[float]) -> str:
    return "unmeasured" if value is None else f"{value:.2f}x"


def render(report: dict[str, Any]) -> str:
    lines: list[str] = ["CUI // SP-CTI", ""]
    lines.append(f"Asset visibility — measured {report.get('measured_at')}")
    ident = report.get("identity") or {}
    if not report.get("measurable"):
        lines.append("")
        lines.append("  UNMEASURABLE — no coverage number and no depth number.")
        lines.append(f"  {report.get('reason', '')}")
        lines.append("")
        lines.append(
            "  This is NOT 0% visibility and it is NOT 100%. Nothing has been"
        )
        lines.append("  measured, which is a different fact from either.")
        return "\n".join(lines)

    lines.append(
        f"  asset_identity rows: {ident.get('total')}   "
        f"denominators: {report.get('config_path')}"
    )
    excluded = report.get("excluded") or {}
    if excluded:
        lines.append(
            "  excluded from every numerator: "
            + ", ".join(f"{k}={v}" for k, v in sorted(excluded.items()))
        )
    lines.append("")
    header = (
        f"{'fabric':<22} {'state':<13} {'seen':>6} {'coverage':>14} "
        f"{'depth':>9} {'denominator':<22} {'conf':<9}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for fab in report.get("fabrics") or []:
        seen = fab.get("observed_assets")
        lines.append(
            f"{str(fab.get('fabric_id'))[:22]:<22} "
            f"{str(fab.get('state'))[:13]:<13} "
            f"{('-' if seen is None else seen):>6} "
            f"{_fmt_pct(fab.get('visibility_pct')):>14} "
            f"{_fmt_depth(fab.get('corroboration_depth')):>9} "
            f"{str(fab.get('denominator_source') or '—')[:22]:<22} "
            f"{str(fab.get('denominator_confidence') or '—')[:9]:<9}"
        )
        unit = fab.get("denominator_unit")
        if fab.get("denominator") is not None:
            lines.append(
                f"{'':<22}   denominator {fab['denominator']} {unit or ''} "
                f"(rank {fab.get('denominator_rank')}, as of "
                f"{fab.get('denominator_as_of') or 'undated'}, declared by "
                f"{fab.get('denominator_declared_by') or 'unstated'})"
            )
        tiers = fab.get("tiers") or {}
        if tiers:
            lines.append(
                f"{'':<22}   corroboration: "
                + ", ".join(f"{k}={v}" for k, v in sorted(tiers.items()))
                + f"  ({fab.get('corroboration_pairs')} distinct (asset, source) pairs)"
            )
        if fab.get("reason"):
            lines.append(f"{'':<22}   {fab['reason']}")
        for note in fab.get("notes") or []:
            lines.append(f"{'':<22}   ! {note}")
        for alt in fab.get("alternates") or []:
            lines.append(
                f"{'':<22}   alternate (not merged): {alt.get('kind')}="
                f"{alt.get('value')} {alt.get('unit')} rank {alt.get('rank')}"
            )
        for ref in fab.get("denominator_refused") or []:
            lines.append(
                f"{'':<22}   refused: {ref.get('kind')} — {ref.get('reason')}"
            )
    lines.append("")
    lines.append(
        f"  '{NOT_ASSESSED_LABEL}' means no authoritative denominator is "
        "registered for that fabric."
    )
    lines.append(
        "  It is not 0% and it is not 100%. Depth is measured either way: it "
        "needs no denominator."
    )
    return "\n".join(lines)


def render_denominators(config: dict[str, Any] | None = None) -> str:
    config = config if config is not None else load_config()
    lines = ["CUI // SP-CTI", "", f"Denominator declaration — {config.get('config_path')}"]
    if not config.get("readable"):
        lines.append(f"  UNREADABLE: {config.get('reason')}")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"{'kind':<22} {'rank':>4} {'confidence':<11} {'unit':<11} {'bias'}")
    lines.append("-" * 74)
    for kind in DENOMINATOR_KINDS:
        spec = kind_spec(kind, config) or {}
        lines.append(
            f"{kind:<22} {str(spec.get('rank', '—')):>4} "
            f"{str(spec.get('confidence') or '—'):<11} "
            f"{str(spec.get('unit') or '—'):<11} {spec.get('bias') or '—'}"
        )
    lines.append("")
    fabrics = config.get("fabrics") or {}
    if not fabrics:
        lines.append("  NO FABRIC HAS A DENOMINATOR DECLARED.")
        lines.append(
            "  Every fabric therefore reports 'not assessed' — never 0%, never 100%."
        )
        return "\n".join(lines)
    for fid, decls in sorted(fabrics.items()):
        res = resolve_denominator(fid, config=config)
        win = res["resolved"]
        lines.append(
            f"  {fid}: "
            + (f"{win['kind']}={win['value']} {win['unit']} (rank {win['rank']})"
               if win else f"unresolved — {res['reason']}")
        )
        for alt in res["alternates"]:
            lines.append(f"      alternate (not merged): {alt['kind']}={alt['value']}")
        for ref in res["refused"]:
            lines.append(f"      refused: {ref.get('kind')} — {ref.get('reason')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Asset visibility that cannot fabricate a percentage (rmf-vis-01)"
    )
    ap.add_argument("--measure", action="store_true", help="measure visibility per fabric")
    ap.add_argument("--fabric", metavar="ID", help="restrict to one fabric")
    ap.add_argument("--record", action="store_true",
                    help="append the measurement to asset_visibility_snapshots")
    ap.add_argument("--denominators", action="store_true",
                    help="show the ranked denominator declaration")
    ap.add_argument("--history", action="store_true", help="read the snapshot series back")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--config", metavar="PATH", help="override the declaration path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    config = load_config(args.config) if args.config else load_config()

    if args.denominators:
        if args.json:
            print(json.dumps({
                "config_path": config.get("config_path"),
                "readable": config.get("readable"),
                "kinds": [kind_spec(k, config) for k in DENOMINATOR_KINDS],
                "fabrics": {
                    fid: resolve_denominator(fid, config=config)
                    for fid in (config.get("fabrics") or {})
                },
            }, indent=2, default=str))
        else:
            print(render_denominators(config))
        return 0

    if args.history:
        out = list_snapshots(fabric=args.fabric, limit=args.limit)
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.measure:
        report = measure(fabric=args.fabric, config=config)
        if args.record:
            report["record"] = record_snapshot(report)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(render(report))
            if report.get("record"):
                print()
                print(f"  recorded: {report['record']}")
        # Exit 2 = the measurement could not be produced. Never the same as a
        # measurement that found nothing.
        return 0 if report.get("measurable") else 2

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
