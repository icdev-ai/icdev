# CUI // SP-CTI
"""Canonical asset identity across the three ZT / asset stacks (rmf-ident-01).

THE DEFECT. Three stacks describe the same machines and none of them can be
joined to another:

    DoD 7-pillar ZTA   zta_maturity_scores / zta_posture_evidence  -> project_id
    NSA ZIG            zig_device_registry                        -> sha256(hostname)[:16]
    NDC / PVM          ni_devices.id, nc_attack_surface.device_name

So there was no path from a DISCOVERED device to a ZT decision to an
attack-surface row to an enclave -- the four things an RMF/cATO package has to
put in one sentence. Worse, the ZIG device pillar seeded itself from a
six-entry ``DEFAULT_FLEET`` fixture, so its maturity score described a fleet
that does not exist.

WHAT THIS IS. A PROJECTION, not a fourth key. Every stack keeps writing what
it always wrote; this table records which of their rows are the same asset,
and each resolver column is NULLABLE because "this stack has never seen this
asset" is a finding in its own right, not a resolution failure.

THREE DATABASES, SO THE JOIN IS IN PYTHON. On PostgreSQL all three stacks
share the ``icdev`` database, but on SQLite each canvas has its own file
(``data/icdev.db``, the security canvas db, the network canvas db). A SQL JOIN
would therefore work on PG and silently return nothing on SQLite. Every
cross-stack read here goes through that stack's OWN ``get_connection()`` and
is joined in Python -- the rule CLAUDE.md already states for JSON.

CORROBORATION COUNTS DISTINCT SOURCES, NEVER ROWS. A device the ZIG scanner
re-registered forty times is observed by ONE source. Repetition is not
corroboration.

Public API
----------
fabric_key(...)            -> the natural key two sources must agree on
zig_device_id(hostname)    -> the ONE definition of the ZIG fingerprint rule
upsert_asset(...)          -> insert-or-update one asset, returns the row
get_asset / find_asset     -> read one
list_assets(...)           -> read many
ingest(...)                -> populate from the three stacks
asset_posture(asset_id)    -> device -> ZT decision -> attack surface -> enclave
managed_fleet()            -> what the ZIG device pillar deploys against

CLI
---
python -m tools.assets.identity --ingest [--json]
python -m tools.assets.identity --list [--limit 50] [--json]
python -m tools.assets.identity --posture <asset-id-or-hostname> [--json]
python -m tools.assets.identity --fleet [--json]
python -m tools.assets.identity --stats [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# xit-decl-03: the ONE root resolver, never Path(__file__).parents[N].
try:
    from icdev.core.paths import repo_root as _repo_root

    _ROOT = _repo_root(__file__)
except Exception:  # pragma: no cover - bootstrap only
    _ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TABLE = "asset_identity"

#: How an asset's classification LABEL was arrived at. NULL is a fourth state
#: -- nothing has classified it -- and must never be read as 'rule'.
CLASSIFICATION_METHODS = ("rule", "oui", "model", "human_confirmed")

#: Derived from the count of DISTINCT discovery sources, never from rows.
TIER_UNCONFIRMED = "unconfirmed"
TIER_SINGLE = "single_source"
TIER_CORROBORATED = "corroborated"
TIER_AUTHORITATIVE = "authoritative"
CORROBORATION_TIERS = (
    TIER_UNCONFIRMED,
    TIER_SINGLE,
    TIER_CORROBORATED,
    TIER_AUTHORITATIVE,
)

#: The stacks that can report an asset. A name outside this set is still
#: recorded -- an unknown source is data, not an error -- but these are the
#: ones ``ingest()`` writes.
SOURCE_NI = "ni_devices"
SOURCE_ZIG = "zig_device_registry"
SOURCE_VULN = "nc_vuln_hosts"
SOURCE_MANUAL = "manual"

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_SEP_RE = re.compile(r"[^0-9a-f]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Identity derivation
# ---------------------------------------------------------------------------

def normalise_mac(mac: Optional[str]) -> Optional[str]:
    """``00-1B-44-11-3A-B7`` / ``001b.4411.3ab7`` -> ``00:1b:44:11:3a:b7``.

    Returns None for anything that is not twelve hex digits, so a partial or
    placeholder MAC can never become a fabric key that two unrelated assets
    both normalise onto.
    """
    if not mac:
        return None
    digits = _SEP_RE.sub("", str(mac).strip().lower())
    if len(digits) != 12:
        return None
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


def normalise_hostname(hostname: Optional[str]) -> Optional[str]:
    if not hostname:
        return None
    name = str(hostname).strip().lower().rstrip(".")
    return name or None


def fabric_key(
    hostname: Optional[str] = None,
    mac_address: Optional[str] = None,
    mgmt_ip: Optional[str] = None,
) -> Optional[str]:
    """The natural key two sources must agree on to be the SAME asset.

    Preference order is by how hard the identifier is to reassign:
    ``mac:`` (burned in) beats ``host:`` (renameable) beats ``ip:`` (a lease).
    The prefix is part of the key, so an IP-keyed asset and a host-keyed asset
    can never collide on a bare string.

    Returns None when nothing usable was supplied -- the caller must then NOT
    write a row, because an asset with no natural key cannot be re-identified
    on the next discovery and would accumulate one row per sweep.
    """
    mac = normalise_mac(mac_address)
    if mac:
        return f"mac:{mac}"
    host = normalise_hostname(hostname)
    if host:
        return f"host:{host}"
    ip = (mgmt_ip or "").strip()
    if ip:
        return f"ip:{ip.lower()}"
    return None


def asset_id_for(key: str) -> str:
    """Derive the canonical id from the fabric key.

    Deterministic, so the same asset re-discovered by a different source in a
    different process lands on the same id without a round trip.
    """
    return "ai-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def zig_device_id(hostname: str) -> str:
    """The ZIG device fingerprint -- ``sha256(hostname)[:16]``.

    THE ONE DEFINITION. The rule was written out by hand at five sites in
    tools/security_canvas (compliance scanner, attestation engine, EDR
    controller, MDM manager, device pillar orchestrator); every one of them now
    calls this. A resolver that re-implemented the rule a sixth time could
    drift from the key it claims to resolve onto, which is the entire failure
    this module exists to end.
    """
    return hashlib.sha256(hostname.encode()).hexdigest()[:16]


def corroboration_tier(
    sources: Iterable[str], *, human_confirmed: bool = False
) -> str:
    """DISTINCT sources, never rows.

    ``odc_gap_scores`` holds 91 rows carrying one distinct value for one
    subject -- a single stuck writer that any row-counting confidence model
    rates as extremely well corroborated. Same trap here: the ZIG scanner
    re-registers a device on every sweep.
    """
    if human_confirmed:
        return TIER_AUTHORITATIVE
    distinct = {s for s in (sources or ()) if s}
    if not distinct:
        return TIER_UNCONFIRMED
    if len(distinct) == 1:
        return TIER_SINGLE
    return TIER_CORROBORATED


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _table_exists(conn) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {TABLE} LIMIT 1").fetchone()  # nosec B608
        return True
    except Exception:  # noqa: BLE001 - unmigrated database, not an error here
        return False


_COLUMNS = (
    "asset_id", "tenant_id", "classification", "classification_method",
    "fabric_key", "hostname", "mgmt_ip", "mac_address", "os_platform",
    "device_type", "vendor", "model",
    "zig_device_id", "ni_device_id", "ni_node_id", "zta_project_id",
    "surface_device_name", "enclave_id",
    "discovery_sources", "corroboration_tier",
    "first_seen", "last_seen", "created_at", "updated_at",
)

#: Fields a later, weaker observation must NOT blank out. A discovery source
#: that does not know the vendor reports nothing, not "no vendor" -- so an
#: incoming None leaves the stored value alone.
_MERGEABLE = (
    "hostname", "mgmt_ip", "mac_address", "os_platform", "device_type",
    "vendor", "model", "zig_device_id", "ni_device_id", "ni_node_id",
    "zta_project_id", "surface_device_name", "enclave_id",
    "classification_method",
)


def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["discovery_sources"] = json.loads(d.get("discovery_sources") or "[]")
    except (TypeError, ValueError):
        d["discovery_sources"] = []
    return d


def upsert_asset(
    *,
    hostname: Optional[str] = None,
    mac_address: Optional[str] = None,
    mgmt_ip: Optional[str] = None,
    source: str = SOURCE_MANUAL,
    classification: Optional[str] = None,
    classification_method: Optional[str] = None,
    human_confirmed: bool = False,
    tenant_id: str = "default",
    conn=None,
    **fields: Any,
) -> Optional[dict]:
    """Insert or update ONE asset. Returns the stored row, or None.

    None means the observation carried no usable natural key -- see
    ``fabric_key`` -- and is deliberately not an exception: a discovery sweep
    over a thousand rows must skip the two that are unidentifiable and keep
    going, having COUNTED them.
    """
    key = fabric_key(hostname=hostname, mac_address=mac_address, mgmt_ip=mgmt_ip)
    if not key:
        return None
    if classification_method and classification_method not in CLASSIFICATION_METHODS:
        raise ValueError(
            f"classification_method must be one of {CLASSIFICATION_METHODS}, "
            f"got {classification_method!r}"
        )

    own_conn = conn is None
    conn = conn or _conn()
    try:
        asset_id = asset_id_for(key)
        now = _now()
        existing = conn.execute(
            f"SELECT * FROM {TABLE} WHERE asset_id = %s",  # nosec B608
            (asset_id,),
        ).fetchone()

        incoming = {
            "hostname": normalise_hostname(hostname),
            "mgmt_ip": (mgmt_ip or None),
            "mac_address": normalise_mac(mac_address),
            "classification_method": classification_method,
        }
        for name in _MERGEABLE:
            if name in fields:
                incoming[name] = fields[name]

        if existing:
            prev = _row_to_dict(existing)
            merged = {k: prev.get(k) for k in _MERGEABLE}
            for k, v in incoming.items():
                if v is not None and k in merged:
                    merged[k] = v
            sources = sorted(set(prev["discovery_sources"]) | ({source} if source else set()))
            tier = corroboration_tier(
                sources,
                human_confirmed=human_confirmed
                or prev.get("corroboration_tier") == TIER_AUTHORITATIVE,
            )
            conn.execute(
                f"UPDATE {TABLE} SET "  # nosec B608
                "hostname = %s, mgmt_ip = %s, mac_address = %s, os_platform = %s, "
                "device_type = %s, vendor = %s, model = %s, "
                "zig_device_id = %s, ni_device_id = %s, ni_node_id = %s, "
                "zta_project_id = %s, surface_device_name = %s, enclave_id = %s, "
                "classification = %s, classification_method = %s, "
                "discovery_sources = %s, corroboration_tier = %s, "
                "last_seen = %s, updated_at = %s "
                "WHERE asset_id = %s",
                (
                    merged["hostname"], merged["mgmt_ip"], merged["mac_address"],
                    merged["os_platform"], merged["device_type"], merged["vendor"],
                    merged["model"], merged["zig_device_id"], merged["ni_device_id"],
                    merged["ni_node_id"], merged["zta_project_id"],
                    merged["surface_device_name"], merged["enclave_id"],
                    classification or prev.get("classification") or "cui",
                    merged["classification_method"],
                    _dumps(sources), tier, now, now, asset_id,
                ),
            )
        else:
            sources = sorted({source}) if source else []
            tier = corroboration_tier(sources, human_confirmed=human_confirmed)
            conn.execute(
                f"INSERT INTO {TABLE} ("  # nosec B608
                "asset_id, tenant_id, classification, classification_method, "
                "fabric_key, hostname, mgmt_ip, mac_address, os_platform, "
                "device_type, vendor, model, zig_device_id, ni_device_id, "
                "ni_node_id, zta_project_id, surface_device_name, enclave_id, "
                "discovery_sources, corroboration_tier, first_seen, last_seen, "
                "created_at, updated_at) VALUES ("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    asset_id, tenant_id, classification or "cui",
                    incoming["classification_method"], key,
                    incoming["hostname"], incoming["mgmt_ip"], incoming["mac_address"],
                    incoming.get("os_platform"), incoming.get("device_type"),
                    incoming.get("vendor"), incoming.get("model"),
                    incoming.get("zig_device_id"), incoming.get("ni_device_id"),
                    incoming.get("ni_node_id"), incoming.get("zta_project_id"),
                    incoming.get("surface_device_name"), incoming.get("enclave_id"),
                    _dumps(sources), tier, now, now, now, now,
                ),
            )
        conn.commit()
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE asset_id = %s",  # nosec B608
            (asset_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        if own_conn:
            conn.close()


def get_asset(asset_id: str, conn=None) -> Optional[dict]:
    own_conn = conn is None
    conn = conn or _conn()
    try:
        if not _table_exists(conn):
            return None
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE asset_id = %s",  # nosec B608
            (asset_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        if own_conn:
            conn.close()


def find_asset(
    *,
    hostname: Optional[str] = None,
    mac_address: Optional[str] = None,
    mgmt_ip: Optional[str] = None,
    conn=None,
) -> Optional[dict]:
    """Resolve an observation to a stored asset by its fabric key."""
    key = fabric_key(hostname=hostname, mac_address=mac_address, mgmt_ip=mgmt_ip)
    if not key:
        return None
    return get_asset(asset_id_for(key), conn=conn)


def list_assets(limit: int = 200, conn=None) -> list[dict]:
    own_conn = conn is None
    conn = conn or _conn()
    try:
        if not _table_exists(conn):
            return []
        rows = conn.execute(
            f"SELECT * FROM {TABLE} ORDER BY last_seen DESC, asset_id LIMIT {int(limit)}"  # nosec B608
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Ingest -- populate from the three stacks
# ---------------------------------------------------------------------------

def _read_ni_devices() -> tuple[Optional[list[dict]], str]:
    """(rows, note). None rows means UNREADABLE, which is not zero rows."""
    try:
        from tools.network.db.init_db import get_connection as nc_conn
    except Exception as exc:  # noqa: BLE001
        return None, f"network canvas not importable: {exc}"
    conn = None
    try:
        conn = nc_conn()
        rows = conn.execute(
            "SELECT id, node_id, label, device_type, vendor, model FROM ni_devices"
        ).fetchall()
        return [dict(r) for r in rows], ""
    except Exception as exc:  # noqa: BLE001
        return None, f"ni_devices unreadable: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _read_zig_registry() -> tuple[Optional[list[dict]], str]:
    try:
        from tools.security_canvas.db.init_db import get_connection as sc_conn
    except Exception as exc:  # noqa: BLE001
        return None, f"security canvas not importable: {exc}"
    conn = None
    try:
        conn = sc_conn()
        rows = conn.execute(
            "SELECT device_id, hostname, os_platform, last_seen_at "
            "FROM zig_device_registry"
        ).fetchall()
        return [dict(r) for r in rows], ""
    except Exception as exc:  # noqa: BLE001
        return None, f"zig_device_registry unreadable: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _read_vuln_hosts() -> tuple[Optional[list[dict]], str]:
    try:
        from tools.network.db.init_db import get_connection as nc_conn
    except Exception as exc:  # noqa: BLE001
        return None, f"network canvas not importable: {exc}"
    conn = None
    try:
        conn = nc_conn()
        rows = conn.execute(
            "SELECT ip, fqdn, netbios, os, node_id FROM nc_vuln_hosts"
        ).fetchall()
        return [dict(r) for r in rows], ""
    except Exception as exc:  # noqa: BLE001
        return None, f"nc_vuln_hosts unreadable: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def ingest(conn=None) -> dict[str, Any]:
    """Populate asset_identity from the three stacks.

    Every source reports its OWN outcome. A source that could not be READ is
    ``readable: false`` with a reason, NEVER ``ingested: 0`` -- an unmigrated
    network canvas and a network canvas with no devices are different facts
    and send you to different fixes.
    """
    own_conn = conn is None
    conn = conn or _conn()
    result: dict[str, Any] = {"sources": {}, "assets_written": 0, "skipped_no_key": 0}
    try:
        if not _table_exists(conn):
            result["error"] = (
                "asset_identity does not exist here -- migration "
                "20260902205902_asset_identity has not run"
            )
            result["measurable"] = False
            return result
        result["measurable"] = True

        written = 0
        skipped = 0

        # --- NDC: ni_devices ------------------------------------------------
        rows, note = _read_ni_devices()
        if rows is None:
            result["sources"][SOURCE_NI] = {"readable": False, "reason": note, "rows": None}
        else:
            n = 0
            for r in rows:
                # ni_devices carries no hostname column; `label` is the
                # operator-facing name and `node_id` the topology handle.
                host = r.get("label") or r.get("node_id")
                got = upsert_asset(
                    hostname=host,
                    source=SOURCE_NI,
                    conn=conn,
                    ni_device_id=r.get("id"),
                    ni_node_id=r.get("node_id"),
                    device_type=r.get("device_type"),
                    vendor=r.get("vendor"),
                    model=r.get("model"),
                    # A vendor/model match is how NDC decided what this is.
                    classification_method="model" if r.get("model") else None,
                    surface_device_name=host,
                )
                if got is None:
                    skipped += 1
                else:
                    n += 1
            result["sources"][SOURCE_NI] = {"readable": True, "rows": len(rows), "ingested": n}
            written += n

        # --- ZIG: zig_device_registry ---------------------------------------
        rows, note = _read_zig_registry()
        if rows is None:
            result["sources"][SOURCE_ZIG] = {"readable": False, "reason": note, "rows": None}
        else:
            n = 0
            for r in rows:
                host = r.get("hostname")
                if not host:
                    skipped += 1
                    continue
                got = upsert_asset(
                    hostname=host,
                    source=SOURCE_ZIG,
                    conn=conn,
                    os_platform=r.get("os_platform"),
                    # Recorded as REPORTED, then asserted against the rule
                    # below -- a registry row whose id does not match
                    # sha256(hostname) is a real finding, not something to
                    # paper over by recomputing it.
                    zig_device_id=r.get("device_id") or zig_device_id(host),
                )
                if got is None:
                    skipped += 1
                else:
                    n += 1
            result["sources"][SOURCE_ZIG] = {"readable": True, "rows": len(rows), "ingested": n}
            written += n

        # --- PVM: nc_vuln_hosts ---------------------------------------------
        rows, note = _read_vuln_hosts()
        if rows is None:
            result["sources"][SOURCE_VULN] = {"readable": False, "reason": note, "rows": None}
        else:
            n = 0
            for r in rows:
                host = r.get("fqdn") or r.get("netbios") or None
                got = upsert_asset(
                    hostname=host,
                    mgmt_ip=r.get("ip"),
                    source=SOURCE_VULN,
                    conn=conn,
                    ni_node_id=r.get("node_id"),
                    os_platform=r.get("os"),
                    surface_device_name=host,
                )
                if got is None:
                    skipped += 1
                else:
                    n += 1
            result["sources"][SOURCE_VULN] = {"readable": True, "rows": len(rows), "ingested": n}
            written += n

        result["assets_written"] = written
        result["skipped_no_key"] = skipped
        result["total_assets"] = _count(conn)
        return result
    finally:
        if own_conn:
            conn.close()


def _count(conn) -> Optional[int]:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE}").fetchone()  # nosec B608
        return int(dict(row)["n"])
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# The join the card names: device -> ZT decision -> attack surface -> enclave
# ---------------------------------------------------------------------------

def _zt_decision(asset: dict) -> dict[str, Any]:
    """The newest ZERO-TRUST decision recorded about this asset.

    Reads the ZIG stack's own tables through the ZIG connection. ``measured``
    is False when the stack could not be read at all -- an absent security
    canvas must not render as "no decision has ever been made", which is what
    a bare empty list would say.
    """
    out: dict[str, Any] = {
        "measured": False,
        "reason": "",
        "device_id": asset.get("zig_device_id"),
        "nac": None,
        "attestation": None,
        "registry": None,
    }
    device_id = asset.get("zig_device_id")
    host = asset.get("hostname")
    if not device_id and host:
        device_id = zig_device_id(host)
        out["device_id"] = device_id
    if not device_id:
        out["reason"] = "asset has no ZIG resolver and no hostname to derive one from"
        return out

    try:
        from tools.security_canvas.db.init_db import get_connection as sc_conn
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"security canvas not importable: {exc}"
        return out

    conn = None
    try:
        conn = sc_conn()
        out["measured"] = True
        try:
            row = conn.execute(
                "SELECT device_id, hostname, decision, reason, network_segment, "
                "created_at FROM zig_nac_events WHERE device_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            out["nac"] = dict(row) if row else None
        except Exception as exc:  # noqa: BLE001
            out["measured"] = False
            out["reason"] = f"zig_nac_events unreadable: {exc}"
        try:
            row = conn.execute(
                "SELECT device_id, verdict, trust_score, expires_at, created_at "
                "FROM zig_device_attestations WHERE device_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            out["attestation"] = dict(row) if row else None
        except Exception as exc:  # noqa: BLE001
            out["measured"] = False
            out["reason"] = out["reason"] or f"zig_device_attestations unreadable: {exc}"
        try:
            row = conn.execute(
                "SELECT device_id, hostname, mdm_enrolled, edr_installed, "
                "nac_authorized, health_score, compliance_score, last_seen_at "
                "FROM zig_device_registry WHERE device_id = %s",
                (device_id,),
            ).fetchone()
            out["registry"] = dict(row) if row else None
        except Exception as exc:  # noqa: BLE001
            out["measured"] = False
            out["reason"] = out["reason"] or f"zig_device_registry unreadable: {exc}"
        return out
    except Exception as exc:  # noqa: BLE001
        out["measured"] = False
        out["reason"] = f"security canvas unreachable: {exc}"
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _attack_surface(asset: dict, limit: int = 25) -> dict[str, Any]:
    """The attack-surface rows for this asset, plus the enclave it sits in.

    ``nc_attack_surface`` keys on ``device_name``, so the resolver column is
    what is matched -- not the hostname, which the PVM mapper does not
    necessarily use.
    """
    out: dict[str, Any] = {
        "measured": False,
        "reason": "",
        "device_name": asset.get("surface_device_name") or asset.get("hostname"),
        "rows": [],
        "enclave": None,
    }
    name = out["device_name"]
    if not name:
        out["reason"] = "asset has no attack-surface resolver"
        return out
    try:
        from tools.network.db.init_db import get_connection as nc_conn
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"network canvas not importable: {exc}"
        return out

    conn = None
    try:
        conn = nc_conn()
        rows = conn.execute(
            "SELECT id, device_name, ip, cve_id, exposure_type, reachable, "
            "bgp_exposed, criticality, surface_score, assessed_at "
            f"FROM nc_attack_surface WHERE device_name = %s "  # nosec B608
            f"ORDER BY surface_score DESC LIMIT {int(limit)}",
            (name,),
        ).fetchall()
        out["measured"] = True
        out["rows"] = [dict(r) for r in rows]

        # ---- the enclave -------------------------------------------------
        # nc_boundaries.node_ids is a JSON array. CLAUDE.md: never
        # json_each/json_extract at a runtime call site -- read the raw column
        # and match in Python, which works identically on both backends.
        node_id = asset.get("ni_node_id")
        if node_id:
            try:
                for b in conn.execute(
                    "SELECT id, label, classification, node_ids FROM nc_boundaries"
                ).fetchall():
                    b = dict(b)
                    try:
                        members = json.loads(b.get("node_ids") or "[]")
                    except (TypeError, ValueError):
                        continue
                    if node_id in members:
                        out["enclave"] = {
                            "id": b.get("id"),
                            "label": b.get("label"),
                            "classification": b.get("classification"),
                        }
                        break
            except Exception as exc:  # noqa: BLE001
                out["reason"] = f"nc_boundaries unreadable: {exc}"
        return out
    except Exception as exc:  # noqa: BLE001
        out["measured"] = False
        out["reason"] = f"nc_attack_surface unreadable: {exc}"
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _zta_posture(asset: dict) -> dict[str, Any]:
    """The 7-pillar ZTA posture for the PROJECT this asset is bound to.

    The DoD pillar stack keys on ``project_id`` and has no per-device row at
    all, so an asset with no ``zta_project_id`` is reported ``bound: false``
    rather than given a project by inference. That unbound state IS the
    finding this table was created to make visible.
    """
    out: dict[str, Any] = {
        "bound": bool(asset.get("zta_project_id")),
        "project_id": asset.get("zta_project_id"),
        "measured": False,
        "reason": "",
        "device_pillar": None,
        "overall": None,
    }
    if not out["bound"]:
        out["reason"] = "asset is not bound to a ZTA project (zta_project_id is NULL)"
        return out
    conn = None
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT pillar, score, maturity_level, created_at "
            "FROM zta_maturity_scores WHERE project_id = %s "
            "ORDER BY created_at DESC",
            (asset["zta_project_id"],),
        ).fetchall()
        out["measured"] = True
        seen: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            seen.setdefault(str(d.get("pillar")), d)
        out["device_pillar"] = seen.get("device")
        out["overall"] = seen.get("overall")
        return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"zta_maturity_scores unreadable: {exc}"
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def asset_posture(selector: str) -> dict[str, Any]:
    """ONE asset, joined across all three stacks.

    ``selector`` is an asset_id or a hostname. Returns
    ``{asset, zt_decision, zta_posture, attack_surface, enclave, joined}``.

    ``joined`` names which stacks actually answered -- a caller must be able
    to tell "this device has no attack-surface row" from "the PVM stack was
    not readable here", and one empty list cannot say both.
    """
    asset = get_asset(selector) or find_asset(hostname=selector)
    if not asset:
        return {
            "found": False,
            "selector": selector,
            "reason": "no asset with that id or hostname -- run --ingest first",
        }

    zt = _zt_decision(asset)
    surface = _attack_surface(asset)
    zta = _zta_posture(asset)
    return {
        "found": True,
        "asset": asset,
        "zt_decision": zt,
        "zta_posture": zta,
        "attack_surface": surface,
        "enclave": surface.get("enclave"),
        "joined": {
            "zig": zt["measured"],
            "pvm": surface["measured"],
            "zta": zta["measured"],
        },
    }


# ---------------------------------------------------------------------------
# The ZIG device pillar's fleet
# ---------------------------------------------------------------------------

def managed_fleet(conn=None) -> list[dict[str, str]]:
    """The fleet the ZIG device pillar deploys against.

    Returns ``[{hostname, os_platform}, ...]`` from asset_identity, or an
    EMPTY list when the table is absent or holds no asset with a hostname.
    The caller (device_pillar_orchestrator) falls back to its fixture on an
    empty list and REPORTS which it used -- a maturity score computed over
    six invented hostnames must never be indistinguishable from one computed
    over the real estate.
    """
    own_conn = conn is None
    conn = conn or _conn()
    try:
        if not _table_exists(conn):
            return []
        rows = conn.execute(
            f"SELECT hostname, os_platform FROM {TABLE} "  # nosec B608
            "WHERE hostname IS NOT NULL AND hostname <> '' "
            "ORDER BY hostname"
        ).fetchall()
        fleet = []
        for r in rows:
            d = dict(r)
            fleet.append(
                {
                    "hostname": d["hostname"],
                    # ZIG's scanners default to linux; recording the default
                    # HERE keeps it out of the orchestrator, which must not
                    # invent a platform for an asset nothing profiled.
                    "os_platform": d.get("os_platform") or "linux",
                }
            )
        return fleet
    finally:
        if own_conn:
            conn.close()


def stats(conn=None) -> dict[str, Any]:
    """Coverage, and the holes named.

    Every resolver gets its own count, because "how many assets does ZIG know
    about" and "how many does PVM know about" are different questions and a
    single 'linked' number answers neither.
    """
    own_conn = conn is None
    conn = conn or _conn()
    try:
        if not _table_exists(conn):
            return {
                "measurable": False,
                "reason": "asset_identity does not exist here -- migration not run",
                "total": None,
            }
        total = _count(conn) or 0
        if total == 0:
            return {
                "measurable": True,
                "total": 0,
                "note": "table exists and is empty -- nothing has been ingested",
                "resolvers": {},
                "corroboration": {},
            }
        resolvers = {}
        for col in ("zig_device_id", "ni_device_id", "ni_node_id",
                    "zta_project_id", "surface_device_name", "enclave_id"):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {TABLE} "  # nosec B608
                f"WHERE {col} IS NOT NULL AND {col} <> ''"
            ).fetchone()
            resolvers[col] = int(dict(row)["n"])
        corro = {}
        for row in conn.execute(
            f"SELECT corroboration_tier AS t, COUNT(*) AS n FROM {TABLE} "  # nosec B608
            "GROUP BY corroboration_tier"
        ).fetchall():
            d = dict(row)
            corro[str(d["t"])] = int(d["n"])
        methods = {}
        for row in conn.execute(
            f"SELECT classification_method AS m, COUNT(*) AS n FROM {TABLE} "  # nosec B608
            "GROUP BY classification_method"
        ).fetchall():
            d = dict(row)
            # NULL is its own bucket: nothing classified these.
            methods[str(d["m"]) if d["m"] is not None else "unclassified"] = int(d["n"])
        return {
            "measurable": True,
            "total": total,
            "resolvers": resolvers,
            "corroboration": corro,
            "classification_method": methods,
        }
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Canonical asset identity (rmf-ident-01)")
    ap.add_argument("--ingest", action="store_true", help="populate from the three stacks")
    ap.add_argument("--list", action="store_true", help="list stored assets")
    ap.add_argument("--posture", metavar="ASSET", help="join one asset across all stacks")
    ap.add_argument("--fleet", action="store_true", help="the ZIG device-pillar fleet")
    ap.add_argument("--stats", action="store_true", help="coverage per resolver")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.ingest:
        out: Any = ingest()
    elif args.posture:
        out = asset_posture(args.posture)
    elif args.fleet:
        fleet = managed_fleet()
        out = {
            "source": "asset_identity" if fleet else "empty",
            "count": len(fleet),
            "fleet": fleet,
        }
    elif args.stats:
        out = stats()
    elif args.list:
        out = {"assets": list_assets(limit=args.limit)}
    else:
        ap.print_help()
        return 2

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
