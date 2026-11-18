# CUI // SP-CTI
"""A source's data discovery bundle for ICDEV[OS], and the ledger a person publishes it through (osx-cap-02).

ICDEV[OS] has no profiler, PII scanner, anomaly rules or quality engine of its own for a provider's source; ICDEV[IT]'s
Data Design Canvas has them (``tools/data_canvas``). This module REUSES that machinery and hands ICDEV[OS] what it found,
as one ``icos-discovery/v1`` bundle per declared source (schema ``context/icos/discovery.schema.json``, owned by
ICDEV[OS] and vendored here verbatim, pinned by SHA-256). ICDEV[OS] imports the bundle, marks it by its own config and
joins it with its catalogue; it never imports these tools.

**Metadata only, never a datum** (:func:`build`). From a stored Explore profile (``dd_explore_profiles``):

* each table's row count and each column's physical and inferred type, null count, null percentage and distinct
  count. The profile's top values, minimum and maximum are DATA, so none leaves;
* the PII columns ``pii_scanner.scan_profile`` flags, with the pattern NAMES its values matched (``email``, ``ssn``),
  never a value; its reason text names columns and patterns only;
* the anomalies ``anomaly_detector``'s RULE-BASED checks find, each with a FIXED description per type (the detector's
  own outlier text embeds the range, which is data). A model's anomaly findings are never exported;
* the design's quality rules (``dd_quality_rules``) and each one's latest run (``dd_quality_runs``); a rule that never
  ran is ``passed: null``, never a pass;
* the catalogue entries that cover the source: the data-mesh products (``dm_data_products``) with an input port whose
  ``source_system`` is the source key.

``content_digest`` is SHA-256 over the canonical JSON (keys sorted, separators ``,`` and ``:``) of the bundle without
``content_digest``, ``publication`` and ``produced_at``: ICDEV[OS] recomputes it. ``schema_pin`` names the contract
version and the SHA-256 of the schema's body (the file without ``x-icos-contract``): ICDEV[OS] refuses another pin.
Deterministic given its inputs: the same profile, rules, runs, products and ``as_of`` give the same digest.

**Publishing is a person's decision** (``decl-wft-data-discovery-publish``). Anyone (or an agent) PREPARES a source's
bundle, which records it, at its digest, in the append-only, hash-chained ``data_discovery_export_events``; a person
who did not prepare it PUBLISHES it or refuses, with a reason, bound to that digest. Only then does the bundle carry its
``publication`` (who, the step, when, the digest). Nothing publishes itself.

CLI: ``tools/data_canvas/discovery_export.py``. Page: ``/data-discovery/``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

import yaml  # noqa: E402

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)
CONFIG_RELPATH = "args/data_discovery_export.yaml"
E2E_ENV = "ICDEV_DISCOVERY_SOURCES_E2E"
SCHEMA = "icos-discovery/v1"
SCHEMA_RELPATH = "context/icos/discovery.schema.json"
#: ICDEV[OS]'s published schema (contracts/schemas/discovery.schema.json, contract SCHEMA_CONTRACT), vendored
#: verbatim. The file digest is what this module refuses a changed file by; the BODY digest (the canonical JSON
#: without ``x-icos-contract``) is what a bundle pins, so a contract release that does not change it keeps pins valid.
SCHEMA_SHA256 = "dce0c1a844142310fc079c97799d5919519ff0ebe73cd066e348c995bf4e8aba"
SCHEMA_BODY_SHA256 = "cb86216d08d69500864070c06892a969b5fa65c9a22aa5c38e02a22ab9189857"
SCHEMA_CONTRACT = "1.194.0"
PRODUCER = "icdev-it/discovery_export"
CLASSIFICATION = "CUI // SP-CTI"
TABLE = "data_discovery_export_events"
GENESIS = "0" * 64

TEMPLATE = "decl-wft-data-discovery-publish"
SURFACE = "data_discovery.dd_api_publish"

EVENTS = ("prepared", "published", "refused")
ACTOR_KINDS = ("person", "agent", "bootstrap")
KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
KINDS = ("sqlite", "postgresql", "duckdb", "file", "other")
INFERRED = ("string", "numeric", "integer", "datetime", "boolean")
QUALITY_KINDS = ("completeness", "uniqueness", "range", "pattern", "freshness")
#: A fixed description per rule-based anomaly type and severity: the detector's own text can carry values.
ANOMALY_TEXT = {
    ("high_null_rate", "high"): "More than 40 per cent of the column's rows are null.",
    ("high_null_rate", "medium"): "More than 20 per cent of the column's rows are null.",
    ("zero_cardinality", "high"): "The column has no distinct value: every row is null or identical.",
    ("potential_pii", "medium"): "The column's name suggests personal data.",
    ("outlier_range", "medium"): "The column's range extends far beyond its most common values (possible outliers).",
}

_COLS = ("event_id", "source", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
         "content_digest", "bundle_json", "detail_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in _COLS if c != "row_hash")


class DiscoveryRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def canonical(value: Any) -> str:
    """ICDEV[OS]'s canonical JSON (``icdev_os/data/discoverymodel.canonical``): keys sorted, no whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def content_digest(bundle: dict) -> str:
    """SHA-256 of the bundle WITHOUT ``content_digest``, ``publication`` and ``produced_at``; ICDEV[OS] recomputes it."""
    return digest({k: v for k, v in bundle.items() if k not in ("content_digest", "publication", "produced_at")})


def body_digest(schema_doc: dict) -> str:
    """The pin: SHA-256 of the schema's canonical JSON without ``x-icos-contract``."""
    return digest({k: v for k, v in schema_doc.items() if k != "x-icos-contract"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _iso(value: Any) -> Optional[str]:
    """An ISO 8601 UTC time (``...Z``) from a stored timestamp, or None when there is none."""
    text = _text(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_of(value: Any, default: Optional[str] = None) -> str:
    text = _text(value) or (default or datetime.now(timezone.utc).date().isoformat())
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    try:
        if m is None:
            raise ValueError(text)
        return date.fromisoformat(m.group(1)).isoformat()
    except ValueError:
        raise DiscoveryRefused(400, "as_of", f"as_of must be a date, YYYY-MM-DD, not {text!r}") from None


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("icdev")
    except Exception:  # noqa: BLE001 — an unpackaged checkout still says what it is
        return "unpackaged"


# ── configuration and schema ─────────────────────────────────────────────────


def config_path() -> Path:
    """The declared sources: ``args/data_discovery_export.yaml``, or (tests, e2e) a file under tests/ named by
    ``ICDEV_DISCOVERY_SOURCES_E2E``. Any other path is refused, never followed."""
    alt = _text(os.environ.get(E2E_ENV))
    if not alt:
        return BASE_DIR / CONFIG_RELPATH
    p = Path(alt)
    p = (p if p.is_absolute() else BASE_DIR / p).resolve()
    if not p.is_relative_to((BASE_DIR / "tests").resolve()) or p.suffix != ".yaml" or not p.is_file():
        raise DiscoveryRefused(500, "config", f"{E2E_ENV} names {alt!r}: a .yaml file under tests/, or nothing")
    return p


def load_config(path: Optional[Path] = None) -> dict:
    p = path or config_path()
    raw = p.read_bytes()
    doc = yaml.safe_load(raw) or {}
    sources: dict[str, dict] = {}
    for s in doc.get("sources") or []:
        key = _text((s or {}).get("key"))
        if not KEY_RE.match(key):
            raise DiscoveryRefused(500, "config", f"source key {key!r} is not ^[a-z][a-z0-9_-]{{0,63}}$")
        if key in sources:
            raise DiscoveryRefused(500, "config", f"source {key!r} is declared twice")
        if not (_text(s.get("design_id")) or _text(s.get("design"))):
            raise DiscoveryRefused(500, "config", f"source {key!r} names no design (design_id or design)")
        sources[key] = {"key": key, "name": _text(s.get("name")) or key,
                        "design_id": _text(s.get("design_id")) or None, "design": _text(s.get("design")) or None}
    return {"sources": sources, "publish": doc.get("publish") or {}, "_sha256": hashlib.sha256(raw).hexdigest(),
            "_path": p.relative_to(BASE_DIR).as_posix() if p.is_relative_to(BASE_DIR) else str(p)}


def source_config(source: str, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    s = cfg["sources"].get(_text(source))
    if s is None:
        raise DiscoveryRefused(404, "unknown_source", f"source {source!r} is not declared in {cfg['_path']}")
    return s


def schema() -> dict:
    """The vendored ICOS schema, refused unless it is the pinned file and its body the pinned body."""
    raw = (BASE_DIR / SCHEMA_RELPATH).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != SCHEMA_SHA256:
        raise DiscoveryRefused(500, "schema", f"{SCHEMA_RELPATH} is not the pinned ICOS contract (sha256 {got}, "
                                              f"pinned {SCHEMA_SHA256})")
    doc = json.loads(raw)
    if body_digest(doc) != SCHEMA_BODY_SHA256:
        raise DiscoveryRefused(500, "schema", f"{SCHEMA_RELPATH}'s body is not the pinned body {SCHEMA_BODY_SHA256}")
    return doc


def validate(bundle: dict) -> list[str]:
    """The bundle's errors against the ICOS contract's schema, then its digest and pin. Empty: ICDEV[OS] would take it."""
    import jsonschema

    v = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())
    errors = sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
                    for e in v.iter_errors(bundle))
    if errors:
        return errors
    if bundle.get("content_digest") != content_digest(bundle):
        errors.append("content_digest is not the digest of the bundle's content")
    if (bundle.get("schema_pin") or {}).get("body_sha256") != SCHEMA_BODY_SHA256:
        errors.append("schema_pin is not the pinned contract's body")
    pub = bundle.get("publication")
    if pub and pub.get("content_digest") != bundle["content_digest"]:
        errors.append("the publication was taken at another digest")
    return errors


# ── the canvas: profiles, rules, runs, products ──────────────────────────────


def _canvas():
    from tools.data_canvas.db.init_db import get_connection

    return get_connection()


def _dicts(cur) -> list[dict]:
    rows = cur.fetchall()
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append(dict(r))
        elif hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            names = [d[0] for d in cur.description]
            out.append(dict(zip(names, tuple(r))))
    return out


def _design_ids(conn, s: dict) -> list[str]:
    if s.get("design_id"):
        return [s["design_id"]]
    rows = _dicts(conn.execute("SELECT id FROM data_designs WHERE name = ? ORDER BY created_at DESC, id",
                               (s["design"],)))
    return [str(r["id"]) for r in rows]


def profiles(source: str, conn=None) -> list[dict]:
    """The source's stored Explore profiles, newest first (id, design, table count, when). No profile body."""
    s = source_config(source)
    own = conn is None
    conn = conn or _canvas()
    try:
        ids = _design_ids(conn, s)
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = _dicts(conn.execute(
            f"SELECT id, design_id, table_count, classification, created_at FROM dd_explore_profiles "  # nosec B608
            f"WHERE design_id IN ({marks}) ORDER BY created_at DESC, id DESC", tuple(ids)))
    finally:
        if own:
            conn.close()
    return [{"id": str(r["id"]), "design_id": str(r["design_id"]), "table_count": r["table_count"],
             "classification": r["classification"], "created_at": _iso(r["created_at"])} for r in rows]


def _profile(conn, s: dict, profile_id: Optional[str]) -> dict:
    ids = _design_ids(conn, s)
    if not ids:
        raise DiscoveryRefused(409, "no_design", f"no design {s.get('design_id') or s.get('design')!r} for {s['key']}")
    marks = ",".join("?" for _ in ids)
    sql = (f"SELECT id, design_id, db_conn_json, profile_json, classification, created_at FROM dd_explore_profiles "  # nosec B608
           f"WHERE design_id IN ({marks})")
    params: tuple = tuple(ids)
    if profile_id:
        sql += " AND id = ?"
        params += (profile_id,)
    rows = _dicts(conn.execute(sql + " ORDER BY created_at DESC, id DESC", params))
    if not rows:
        raise DiscoveryRefused(409, "no_profile", f"{s['key']} has no stored profile"
                                                  + (f" {profile_id!r}" if profile_id else "")
                                                  + ": profile the source on /data/explore first")
    return rows[0]


def _load_json(text: Any, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _top_values(col: dict) -> list:
    return [tv.get("value") if isinstance(tv, dict) else tv for tv in col.get("top_values") or []]


def _columns(table: dict) -> list[dict]:
    out = []
    for col in table.get("columns") or []:
        inferred = _text(col.get("inferred_type")).lower()
        c = {"name": _text(col.get("name") or col.get("column_name")),
             "physical_type": _text(col.get("type_str"))[:100],
             "inferred_type": inferred if inferred in INFERRED else "unknown",
             "null_count": max(0, int(col.get("null_count") or 0)),
             "null_pct": min(100.0, max(0.0, float(col.get("null_pct") or 0.0))),
             "distinct_count": max(0, int(col.get("distinct_count") or 0))}
        if col.get("error"):
            c["error"] = _text(col.get("error"))[:300]
        out.append(c)
    return out


def _pii(profile: dict, classification: str) -> list[dict]:
    from tools.data_canvas.pii_scanner import scan_profile

    found = scan_profile(profile, classification).get("findings") or []
    out = [{"table": _text(f.get("table")), "column": _text(f.get("column")), "severity": f["severity"],
            "pii_type": _text(f.get("pii_type"))[:40] or "PII", "reason": _text(f.get("description"))[:300],
            "patterns": sorted({_text(p) for p in f.get("pattern_matches") or [] if _text(p)}), "method": "rule"}
           for f in found if f.get("severity") in ("high", "medium", "low")]
    return sorted(out, key=lambda x: (x["table"], x["column"]))


def _anomalies(tables: list[dict]) -> list[dict]:
    from tools.data_canvas.anomaly_detector import _rule_based_findings

    out = []
    for t in tables:
        stats = {_text(c.get("name")): {"null_pct": c.get("null_pct", 0), "distinct_count": c.get("distinct_count"),
                                        "min": c.get("min"), "max": c.get("max"), "top_values": _top_values(c)}
                 for c in t.get("columns") or []}
        for f in _rule_based_findings(stats):     # the RULE-BASED checks only: never the model path
            ftype = _text(f.get("finding_type"))
            sev = _text(f.get("severity"))
            kind = ftype if ftype in ("high_null_rate", "zero_cardinality", "potential_pii", "outlier_range") else "other"
            if sev not in ("high", "medium", "low"):
                continue
            out.append({"table": _text(t.get("name")), "column": _text(f.get("column")), "severity": sev,
                        "type": kind, "description": ANOMALY_TEXT.get((kind, sev), "A rule-based check flagged the column."),
                        "method": "rule"})
    return sorted(out, key=lambda x: (x["table"], x["column"], x["type"]))


def _quality(conn, design_ids: list[str]) -> list[dict]:
    if not design_ids:
        return []
    marks = ",".join("?" for _ in design_ids)
    rules = _dicts(conn.execute(
        f"SELECT id, table_name, column_name, check_type, threshold FROM dd_quality_rules "  # nosec B608
        f"WHERE design_id IN ({marks}) AND enabled = 1 ORDER BY id", tuple(design_ids)))
    out = []
    for r in rules:
        runs = _dicts(conn.execute("SELECT passed, actual_value, created_at FROM dd_quality_runs WHERE rule_id = ? "
                                   "ORDER BY created_at DESC, id DESC", (r["id"],)))
        run = runs[0] if runs else None
        kind = _text(r["check_type"])
        out.append({"rule_id": _text(r["id"])[:64], "table": _text(r["table_name"]),
                    "column": _text(r.get("column_name"))[:128],
                    "kind": kind if kind in QUALITY_KINDS else "other",
                    "threshold": float(r["threshold"]) if r.get("threshold") is not None else None,
                    "passed": bool(run["passed"]) if run else None,
                    "actual": float(run["actual_value"]) if run and run.get("actual_value") is not None else None,
                    "ran_at": _iso(run["created_at"]) if run else None})
    return sorted(out, key=lambda x: (x["table"], x["column"], x["rule_id"]))


def _catalog(conn, key: str) -> list[dict]:
    """The data-mesh products with an input port whose ``source_system`` is the source key."""
    ports: list[dict] = []
    for sql in ("SELECT product_id, schema_json FROM dm_input_ports WHERE source_system = ?",
                "SELECT product_id, schema_json FROM dm_ports WHERE port_type = 'input' AND source_system = ?"):
        try:
            ports += _dicts(conn.execute(sql, (key,)))
        except Exception:  # noqa: BLE001 — a canvas without the ports table covers nothing
            continue
    tables: dict[str, set] = {}
    for p in ports:
        sch = _load_json(p.get("schema_json"), {})
        tables.setdefault(str(p["product_id"]), set()).update(
            _text(t) for t in (sch.get("tables") if isinstance(sch, dict) else None) or [] if _text(t))
    out = []
    for pid in sorted(tables):
        rows = _dicts(conn.execute(
            "SELECT p.id, p.name, p.status, d.name AS domain_name FROM dm_data_products p "
            "LEFT JOIN dm_domains d ON d.id = p.domain_id WHERE p.id = ?", (pid,)))
        if not rows:
            continue
        r = rows[0]
        out.append({"product_id": _text(r["id"])[:128], "name": _text(r["name"])[:200] or pid,
                    "domain_name": _text(r.get("domain_name")) or None, "status": _text(r.get("status")) or None,
                    "tables": sorted(tables[pid])})
    return out


def build(source: str, *, profile_id: Optional[str] = None, as_of: Optional[str] = None, at: Optional[str] = None,
          cfg: Optional[dict] = None, conn=None) -> dict:
    """The source's ``icos-discovery/v1`` bundle from its stored profile (the newest, or ``profile_id``). A draft:
    ``publication`` is null. Validated against the pinned schema before it is returned."""
    s = source_config(source, cfg)
    own = conn is None
    conn = conn or _canvas()
    try:
        row = _profile(conn, s, profile_id)
        prof = _load_json(row.get("profile_json"), {})
        dbc = _load_json(row.get("db_conn_json"), {})
        design_ids = _design_ids(conn, s)
        quality = _quality(conn, [str(row["design_id"])] if row.get("design_id") else design_ids)
        catalog = _catalog(conn, s["key"])
    finally:
        if own:
            conn.close()
    classification = _text(row.get("classification") or prof.get("classification")) or CLASSIFICATION
    raw_tables = sorted((t for t in prof.get("tables") or [] if isinstance(t, dict)), key=lambda t: _text(t.get("name")))
    tables = [{"name": _text(t.get("name")), "row_count": max(0, int(t.get("row_count") or 0)), "columns": _columns(t)}
              for t in raw_tables]
    pii = _pii({"tables": raw_tables}, classification)
    anomalies = _anomalies(raw_tables)
    profiled_at = _iso(row.get("created_at")) or _iso(prof.get("profiled_at")) or _iso(_now())
    kind = _text(dbc.get("db_type") or prof.get("db_type")).lower()
    if at and _iso(at) is None:
        raise DiscoveryRefused(400, "at", f"--at must be an ISO 8601 time, not {at!r}")
    b = {
        "schema": SCHEMA,
        "schema_pin": {"contract": SCHEMA_CONTRACT, "body_sha256": SCHEMA_BODY_SHA256},
        "producer": {"system": "icdev-it", "tool": PRODUCER, "version": _version()},
        "produced_at": _iso(at) if at else _iso(_now()),
        "classification": classification,
        "source": {"key": s["key"], "name": s["name"], "kind": kind if kind in KINDS else "other",
                   "design_id": _text(row.get("design_id")) or None, "profile_id": _text(row["id"]),
                   "profiled_at": profiled_at},
        "as_of": _as_of(as_of, (profiled_at or "")[:10] or None),
        "tables": tables,
        "pii": pii,
        "anomalies": anomalies,
        "quality": quality,
        "catalog": catalog,
        "summary": {"tables": len(tables), "columns": sum(len(t["columns"]) for t in tables), "pii": len(pii),
                    "anomalies": len(anomalies),
                    "quality_passed": sum(1 for q in quality if q["passed"] is True),
                    "quality_failed": sum(1 for q in quality if q["passed"] is False),
                    "quality_unrun": sum(1 for q in quality if q["passed"] is None)},
        "publication": None,
    }
    b["content_digest"] = content_digest(b)
    errs = validate(b)
    if errs:
        raise DiscoveryRefused(422, "invalid", f"the bundle does not meet {SCHEMA}: {'; '.join(errs[:3])}")
    return b


# ── the ledger: prepared, then a person publishes ────────────────────────────


def _connection():
    """The platform database without the row-security predicate (the ledger has no label column: it is the
    platform's record of what it released, as coe_conformance_events is)."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def read_events(conn=None, source: Optional[str] = None) -> list[dict]:
    own = conn is None
    conn = conn or _connection()
    try:
        sql = f"SELECT {', '.join(_COLS)} FROM {TABLE}"  # nosec B608 — fixed table and columns
        params: tuple = ()
        if source:
            sql += " WHERE source = %s"
            params = (source,)
        rows = conn.execute(sql + " ORDER BY created_at, event_id", params).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Each source's rows in hash-chain order, plus ``_problems`` (a row that does not verify or is off its chain)."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["source"]), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for src, items in sorted(by.items()):
        nxt = {str(r["prev_hash"]): r for r in items}
        ordered, prev = [], GENESIS
        while prev in nxt:
            r = nxt.pop(prev)
            ordered.append(r)
            prev = str(r["row_hash"])
        for r in items:
            if _row_hash(r) != r["row_hash"]:
                out["_problems"].append({"event_id": r["event_id"], "source": src,
                                         "problem": "row hash does not match its content"})
            elif r["event"] == "prepared":
                try:
                    if content_digest(json.loads(r["bundle_json"])) != r["content_digest"]:
                        raise ValueError
                except (ValueError, TypeError):
                    out["_problems"].append({"event_id": r["event_id"], "source": src,
                                             "problem": "the bundle does not match its digest"})
        for r in nxt.values():
            out["_problems"].append({"event_id": r["event_id"], "source": src,
                                     "problem": "not on the source's hash chain"})
        out[src] = ordered
    return out


def _detail(row: dict) -> dict:
    try:
        return json.loads(row.get("detail_json") or "{}")
    except ValueError:
        return {}


def replay(ordered: list[dict]) -> dict:
    """One source's ledger state: the latest prepared bundle, whether a person decided it, the latest published."""
    prepared = published = None
    history = []
    for r in ordered:
        det = _detail(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")} | {"detail": det}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None}
        elif prepared and det.get("prepared_event") == prepared["event_id"]:
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"]}
    return {"prepared": prepared, "published": published, "history": history}


def _state(source: str, conn) -> tuple[dict, list[dict], list[dict]]:
    rows = read_events(conn, source)
    ch = chain(rows)
    return replay(ch.get(source, [])), ch["_problems"], ch.get(source, [])


def _head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def _append(conn, *, source: str, event: str, actor: str, actor_kind: str, content: str, prev_hash: str,
            bundle: Optional[dict] = None, actor_role: str = "", step_id: str = "", reason: str = "",
            detail: Optional[dict] = None) -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    row = {"event_id": f"ddx-{uuid.uuid4().hex[:16]}", "source": source, "event": event, "actor": actor,
           "actor_kind": actor_kind, "actor_role": actor_role or "", "step_id": step_id or "",
           "reason": reason or "", "content_digest": content,
           "bundle_json": canonical(bundle) if bundle is not None else "",
           "detail_json": canonical(detail or {}), "prev_hash": prev_hash, "created_at": _now()}
    row["row_hash"] = _row_hash(row)
    try:
        conn.execute(f"INSERT INTO {TABLE} ({', '.join(_COLS)}) VALUES ({', '.join(['%s'] * len(_COLS))})",
                     tuple(row[c] for c in _COLS))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        except Exception:  # noqa: BLE001
            pass
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise DiscoveryRefused(409, "concurrent", f"{source}'s bundle changed while you were deciding: reload it") from exc
        raise DiscoveryRefused(503, "store_unavailable",
                               f"the discovery export ledger could not be written, so nothing changed ({exc})") from exc
    return row


def _actor_kind(actor: dict) -> str:
    if _text(actor.get("id")).startswith("bootstrap:"):
        return "bootstrap"
    if actor.get("role") == "service" or _text(actor.get("id")).startswith("agent:"):
        return "agent"
    return "person"


def prepare(source: str, actor: Optional[dict], *, profile_id: Optional[str] = None, as_of: Optional[str] = None,
            note: str = "", conn=None, bundle: Optional[dict] = None) -> dict:
    """Build the source's bundle and record it, at its digest, as PREPARED. Decides nothing.

    Anyone signed in, or an agent (recorded as one). A person who did not prepare it publishes it or refuses.
    """
    if not isinstance(actor, dict) or not actor.get("id"):
        raise DiscoveryRefused(401, "no_actor", "preparing a bundle needs a signed-in person or a named agent")
    s = source_config(source)
    b = bundle if bundle is not None else build(s["key"], profile_id=profile_id, as_of=as_of)
    own = conn is None
    conn = conn or _connection()
    try:
        _, problems, ordered = _state(s["key"], conn)
        if problems:
            raise DiscoveryRefused(409, "chain_broken", f"{s['key']}'s ledger does not verify: {problems[0]['problem']}")
        row = _append(conn, source=s["key"], event="prepared", actor=_text(actor["id"]),
                      actor_kind=_actor_kind(actor), actor_role=_text(actor.get("role")),
                      content=b["content_digest"], bundle=b, reason=note,
                      detail={"as_of": b["as_of"], "profile_id": b["source"]["profile_id"], "summary": b["summary"]},
                      prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    return {"source": s["key"], "event_id": row["event_id"], "content_digest": b["content_digest"],
            "prepared_by": row["actor"], "actor_kind": row["actor_kind"], "as_of": b["as_of"],
            "profile_id": b["source"]["profile_id"], "summary": b["summary"], "template": TEMPLATE,
            "next": "a person who did not prepare it publishes it or refuses, on /data-discovery/"}


def builders(state: dict) -> dict[str, str]:
    """Who may not publish the prepared bundle (``separate_from: builders``): whoever prepared it."""
    p = state.get("prepared") or {}
    who = _text(p.get("actor"))
    return {who: "prepared this bundle"} if who else {}


def publish(source: str, *, decision: str, reason: str, read_digest: str, actor: Optional[dict], conn=None) -> dict:
    """A person who did not prepare it publishes the source's prepared bundle to ICDEV[OS], or refuses it.

    The person's step of ``decl-wft-data-discovery-publish``, with a reason, bound to the digest of the prepared
    bundle they read. A published bundle carries its ``publication``; a refused one never leaves.
    """
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise DiscoveryRefused(401, "no_person", "publishing is a signed-in person's declared step on /data-discovery/")
    s = source_config(source)
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _state(s["key"], conn)
        if problems:
            raise DiscoveryRefused(409, "chain_broken", f"{s['key']}'s ledger does not verify: {problems[0]['problem']}")
        p = st["prepared"]
        if p is None:
            raise DiscoveryRefused(409, "nothing_prepared", f"no bundle of {s['key']} is prepared: prepare one first")
        if p.get("decided"):
            raise DiscoveryRefused(409, "already_decided",
                                   f"the prepared bundle {p['content_digest'][:12]} was already {p['decided']['event']}: prepare a new one")
        if _text(read_digest) != p["content_digest"]:
            raise DiscoveryRefused(409, "digest", f"the prepared bundle is {p['content_digest'][:12]}, not what you read (reload it)")
        dec = _text(decision).lower()
        try:
            taken = declared.take_step(
                TEMPLATE, surface=SURFACE, decision=dec, subject=f"discovery:{s['key']}",
                reason=_text(reason), actor=actor, separated_from=builders(st),
                payload={"source": s["key"], "content_digest": p["content_digest"], "prepared_event": p["event_id"],
                         "profile_id": (p.get("detail") or {}).get("profile_id")})
        except declared.StepRefused as exc:
            raise DiscoveryRefused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        row = _append(conn, source=s["key"], event=event, actor=taken["decided_by"], actor_kind="person",
                      actor_role=_text(actor.get("role")), step_id=taken["step_id"], reason=_text(reason),
                      content=p["content_digest"], detail={"prepared_event": p["event_id"], "decision": dec},
                      prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    out = {"source": s["key"], "event": event, "event_id": row["event_id"], "content_digest": p["content_digest"],
           "step": taken, "decided_by": taken["decided_by"], "prepared_by": p["actor"]}
    if event == "published":
        out["bundle"] = published_bundle(s["key"])
    return out


def published_bundle(source: str, conn=None) -> Optional[dict]:
    """The source's newest PUBLISHED bundle, as prepared, with its ``publication``; None when none is."""
    s = source_config(source)
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _state(s["key"], conn)
    finally:
        if own:
            conn.close()
    pub = st["published"]
    if pub is None or problems:
        return None
    prep = next((r for r in ordered if r["event_id"] == pub["prepared_event"]), None)
    if prep is None:
        return None
    b = json.loads(prep["bundle_json"])
    b["publication"] = {"signed_by": pub["actor"], "role": pub["actor_role"] or None, "step_id": pub["step_id"],
                        "decided_at": _iso(pub["created_at"]), "template": TEMPLATE,
                        "content_digest": pub["content_digest"], "prepared_by": pub["prepared_by"],
                        "event_id": pub["event_id"]}
    return b


# ── reading ──────────────────────────────────────────────────────────────────


def ledger_view(source: str, conn=None) -> dict:
    s = source_config(source)
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, _ = _state(s["key"], conn)
    finally:
        if own:
            conn.close()
    p = st["prepared"]
    return {"prepared": p, "published": st["published"], "history": st["history"],
            "builders": builders(st), "chain": {"intact": not problems, "problems": problems},
            "next": ["publish"] if p and not p.get("decided") else ["prepare"], "template": TEMPLATE}


def sources_view(conn=None) -> dict:
    """Every declared source and its ledger state. Reads the ledger only; builds no bundle."""
    cfg = load_config()
    rows = read_events(conn)
    ch = chain(rows)
    out = []
    for key, s in cfg["sources"].items():
        st = replay(ch.get(key, []))
        out.append({"key": key, "name": s["name"], "design": s.get("design"), "design_id": s.get("design_id"),
                    "prepared": st["prepared"], "published": st["published"]})
    return {"sources": out, "chain": {"intact": not ch["_problems"], "problems": ch["_problems"], "rows": len(rows)},
            "config": {"path": cfg["_path"], "sha256": cfg["_sha256"]}, "template": TEMPLATE, "schema": SCHEMA,
            "schema_path": SCHEMA_RELPATH, "contract": SCHEMA_CONTRACT, "schema_sha256": SCHEMA_SHA256}


def source_view(source: str, *, profile_id: Optional[str] = None, as_of: Optional[str] = None, conn=None) -> dict:
    """The page's view of one source: its profiles, the bundle as it would be prepared now (or why not), its ledger."""
    s = source_config(source)
    try:
        b, why = build(s["key"], profile_id=profile_id, as_of=as_of), None
    except DiscoveryRefused as exc:
        if exc.code not in ("no_design", "no_profile"):
            raise
        b, why = None, exc.to_dict()
    return {"source": {"key": s["key"], "name": s["name"]}, "profiles": profiles(s["key"]), "bundle": b,
            "unbuilt": why, "ledger": ledger_view(s["key"], conn)}


# ── twin ─────────────────────────────────────────────────────────────────────


def twin_evidence() -> dict:
    """What the governance twin reads: each source's ledger state and its newest published bundle's facts."""
    ev: dict[str, Any] = {"sources": None, "errors": {}}
    try:
        cfg = load_config()
        rows = read_events()
        ch = chain(rows)
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["discovery"] = str(exc)
        return ev
    items = []
    for key in sorted(cfg["sources"]):
        ordered = ch.get(key, [])
        st = replay(ordered)
        pub = st["published"]
        facts = None
        if pub is not None:
            prep = next((r for r in ordered if r["event_id"] == pub["prepared_event"]), None)
            b = _load_json(prep["bundle_json"], {}) if prep else {}
            facts = {"as_of": b.get("as_of"),
                     "failing": sorted(q["rule_id"] for q in b.get("quality") or [] if q.get("passed") is False),
                     "unrun": sorted(q["rule_id"] for q in b.get("quality") or [] if q.get("passed") is None),
                     "pii": len(b.get("pii") or [])}
        items.append({"source": key, "prepared": st["prepared"] and {
            k: st["prepared"][k] for k in ("event_id", "actor", "created_at", "content_digest")} | {
            "decided": bool(st["prepared"].get("decided"))},
            "published": pub and {k: pub[k] for k in ("event_id", "actor", "prepared_by", "created_at",
                                                      "content_digest")},
            "facts": facts})
    ev["sources"] = items
    ev["problems"] = ch["_problems"]
    ev["policy"] = cfg.get("publish") or {}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)`` for the discovery bundles. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    sources = ev.get("sources")
    checks = [check("data.discovery.ledger", "Data discovery export ledger (data_discovery_export_events)",
                    sources is not None, errors.get("discovery") or "ledger not read")]
    if sources is not None:
        # Nothing declared is nothing observed: never a pass.
        checks.append(check("data.discovery.declared", "A source declared for export",
                            bool(sources), "no source is declared in args/data_discovery_export.yaml"))
    findings: list[dict] = []
    pol = {**(ev.get("policy") or {}), **(policy or {})}
    stale = float(pol.get("stale_days") or 14)
    waiting = float(pol.get("waiting_warn_days") or 7)
    for p in ev.get("problems") or []:
        findings.append(finding("data.discovery.chain_broken", "critical", f"{p['source']}: {p['problem']}",
                                thread_id=f"data_discovery:{p['event_id']}",
                                recommendation="The export history cannot be trusted; the ledger is append-only, so "
                                               "find who wrote the row and treat it as an incident."))
    published = 0
    for r in sources or []:
        pub, prep, facts = r.get("published"), r.get("prepared"), r.get("facts") or {}
        # A source never published is unverified, never passed.
        checks.append(check(f"data.discovery.{r['source']}", f"A published discovery bundle for {r['source']}",
                            pub is not None, "no bundle of this source has been published: unverified"))
        if prep and not prep.get("decided"):
            age = age_hours(prep.get("created_at"), observed_at)
            if age is not None and age > waiting * 24:
                findings.append(finding("data.discovery.awaiting_publish", "low",
                                        f"{r['source']}: a prepared bundle has waited {age / 24:.0f} days for a person",
                                        thread_id=f"data_discovery:{prep['event_id']}",
                                        recommendation="A person who did not prepare it publishes it or refuses."))
        if pub is None:
            continue
        published += 1
        thread = f"data_discovery:{pub['event_id']}"
        if pub.get("actor") and pub.get("actor") == pub.get("prepared_by"):
            findings.append(finding("data.discovery.self_published", "critical",
                                    f"{r['source']}: the bundle was published by the person who prepared it",
                                    thread_id=thread, recommendation="Treat it as unpublished; a person who did "
                                                                     "not prepare it publishes a new one."))
        age = age_hours(pub.get("created_at"), observed_at)
        if age is not None and age > stale * 24:
            findings.append(finding("data.discovery.stale", "medium",
                                    f"{r['source']}: the newest published bundle is {age / 24:.0f} days old",
                                    thread_id=thread, recommendation="Profile the source again and publish a current bundle."))
        for rid in facts.get("failing") or []:
            findings.append(finding("data.discovery.quality_failing", "high",
                                    f"{r['source']}: quality rule {rid} fails in the published bundle (as of {facts.get('as_of')})",
                                    thread_id=thread, recommendation="Fix the data at its provider, or the rule, and publish again."))
        for rid in facts.get("unrun") or []:
            findings.append(finding("data.discovery.quality_unrun", "low",
                                    f"{r['source']}: quality rule {rid} never ran before the bundle was published",
                                    thread_id=thread, recommendation="Run the design's quality rules on /data/quality and publish again."))
    summary = {"discovery_sources": len(sources or []), "discovery_published": published}
    return checks, findings, summary
