# CUI // SP-CTI
"""FOCUS cost bundles from FinOps, for ICDEV[OS]'s Cloud CoP (osx-cop-05, ``icos-focus-cost/v1``).

A provider's FOCUS Cost and Usage export (FOCUS 1.4) becomes ONE bundle that ICDEV[OS] imports
(``icdev_os/cloud/focus.py`` there is the authority on what it takes):

**Reading an export** (:func:`read_export`). CSV, a JSON array or JSON lines. In CSV a decimal column is parsed as a
number, a JSON column (``Tags`` and the other object columns) as JSON, and an empty cell is null; JSON is taken as
written (NaN and Infinity refused). Every row is validated against the pinned schema's ``$defs/FocusRow`` with
jsonschema, and every date-time column is checked as RFC 3339 here (jsonschema's ``date-time`` format check needs a
package this platform does not ship, so it is never relied on). One row that fails refuses the WHOLE export, with
each failing row named: an export is never half read. One export is one billing period in one currency (the
config's); anything else is refused.

**The bundle** (:func:`build`), exactly as ICDEV[OS] checks it: ``schema``, ``producer`` (ICDEV[IT], this tool, its
version), ``produced_at`` (RFC 3339 UTC), ``classification`` (a claim; ICDEV[OS] marks it by its own config),
``focus`` (version and dataset), ``pin`` (the schema digest of the pinned contract), ``billing_period``,
``currency``, ``service_tag``, the ``rows`` as read, ``workloads`` (EVERY declared workload: rows, effective and
billed cost; one with no rows reports 0 and "0.000000"), ``unattributed`` (rows whose Tags name no declared
workload), ``publication`` (null until a person publishes it) and ``content_digest``. Totals are
``Decimal(str(v))`` sums of ``EffectiveCost`` and ``BilledCost`` (null as 0), quantized to six places half-even,
written as strings. ``content_digest`` is SHA-256 over the canonical JSON (keys sorted, separators ``,`` ``:``) of the
bundle without ``content_digest``, ``publication`` and ``produced_at``. Deterministic given the export, the config
and the time.

**The pin.** ``context/icos/focus-cost.schema.json`` is ICDEV[OS]'s published schema, vendored verbatim and pinned
twice: by the file's SHA-256 (:data:`SCHEMA_SHA256`; a changed file is refused, never used) and by its schema digest
(:data:`SCHEMA_DIGEST`: the file without its top-level ``x-icos-contract`` version stamp, canonical JSON), which is
what a bundle carries as ``pin.sha256`` and what ICDEV[OS] compares with its own.

**Publishing is a person's decision** (``decl-wft-finops-focus-publish``). Anyone, or an agent, PREPARES a bundle from
an export, which records it at its digest in the append-only, hash-chained ``finops_focus_events`` (one chain per
billing month) and decides nothing. A person who did not prepare it PUBLISHES it or refuses, with a reason, bound to
that digest. Only then does the bundle carry its ``publication`` (who, the step, when, the digest, who prepared it);
ICDEV[OS] refuses a bundle with none, one at another digest, or one its preparer signed.

CLI (deterministic; never publishes)::

    python tools/finops/focus_bundle.py --export <focus.csv|json> [--at 2026-11-18T00:00:00Z] [--out <file>]
    python tools/finops/focus_bundle.py --published <event id|latest> --out <file>
    python tools/finops/focus_bundle.py --verify <file>
    python tools/finops/focus_bundle.py --list
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
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
CONFIG_RELPATH = "args/finops_focus.yaml"
SCHEMA = "icos-focus-cost/v1"
SCHEMA_RELPATH = "context/icos/focus-cost.schema.json"
#: ICDEV[OS]'s published schema (contracts/schemas/focus-cost.schema.json, contract SCHEMA_CONTRACT), vendored
#: verbatim. DRAFT: ICDEV[OS] has not merged contract 1.195.0 yet; re-pin when it does.
SCHEMA_SHA256 = "f79e7a232253856fbf6c504685dd19f3cfda4811ce80c376a68e2278c7b05af9"
#: What a bundle pins (pin.sha256): sha256(canonical(schema without "x-icos-contract")).
SCHEMA_DIGEST = "09a2ea740fcbcd9107b79a0665a48728cc61370911fb5bb2647683421ae6ec58"
SCHEMA_CONTRACT = "1.195.0"
PRODUCER_SYSTEM = "ICDEV[IT]"
PRODUCER_TOOL = "tools/finops/focus_bundle.py"
DATASET = "CostAndUsage"
#: The cost columns a workload's total is computed from (as ICDEV[OS] computes it).
TOTALS = ("EffectiveCost", "BilledCost")
Q = Decimal("0.000001")
TABLE = "finops_focus_events"
GENESIS = "0" * 64

TEMPLATE = "decl-wft-finops-focus-publish"
SURFACE = "idp.idp_api_finops_focus_publish"
PAGE = "/idp/finops/focus"

EVENTS = ("prepared", "published", "refused")
ACTOR_KINDS = ("person", "agent", "bootstrap")

_COLS = ("event_id", "period", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
         "content_digest", "bundle_json", "detail_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in _COLS if c != "row_hash")

_WORKLOAD = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_EXPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class FocusRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _at(value: Optional[str] = None) -> str:
    """``produced_at``: RFC 3339 UTC to the second (now, or the time given)."""
    if not value:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t = _when(value)
    if t is None:
        raise FocusRefused(400, "at", f"--at must be an RFC 3339 time with a zone, not {value!r}")
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(value: Any) -> Optional[datetime]:
    s = str(value or "").strip()
    if not _RFC3339.match(s):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


# ── configuration ────────────────────────────────────────────────────────────


def config_errors(cfg: Any) -> list[str]:
    """What is wrong with a config (empty: it may be used). Never repaired."""
    if not isinstance(cfg, dict):
        return ["the config is not a mapping"]
    errs = []
    if str(cfg.get("focus_version")) != "1.4":
        errs.append(f"focus_version is {cfg.get('focus_version')!r}: the pinned schema's rows are FOCUS 1.4")
    tag = cfg.get("tag_key")
    if not isinstance(tag, str) or not 2 <= len(tag) <= 64:
        errs.append("tag_key must be a Tags key of 2 to 64 characters")
    if not isinstance(cfg.get("currency"), str) or not _CURRENCY.match(cfg["currency"]):
        errs.append("currency must be ISO 4217 (three capital letters)")
    ws = cfg.get("workloads")
    if not isinstance(ws, list) or not ws:
        errs.append("workloads must declare at least one workload")
        ws = []
    seen: set[str] = set()
    for w in ws:
        key = w.get("key") if isinstance(w, dict) else None
        if not isinstance(key, str) or not _WORKLOAD.match(key):
            errs.append(f"workload key {key!r} does not match {_WORKLOAD.pattern}")
        elif key in seen:
            errs.append(f"workload {key} is declared twice")
        else:
            seen.add(key)
        if not isinstance(w, dict) or not _text(w.get("name")):
            errs.append(f"workload {key!r} has no name")
    ex = cfg.get("exports")
    if not isinstance(ex, dict) or not isinstance(ex.get("dir"), str) or not ex["dir"].strip():
        errs.append("exports.dir must name the directory the FOCUS exports are read from")
    else:
        p = Path(ex["dir"])
        if p.is_absolute() or ".." in p.parts:
            errs.append("exports.dir must be a directory inside the repo (relative, no '..')")
        if not ex.get("suffixes") or not all(isinstance(s, str) and s.startswith(".") for s in ex["suffixes"]):
            errs.append("exports.suffixes must list file suffixes such as .csv")
        for k in ("max_bytes", "max_rows"):
            if not isinstance(ex.get(k), int) or ex[k] <= 0:
                errs.append(f"exports.{k} must be a positive integer")
    return errs


def load_config(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else BASE_DIR / CONFIG_RELPATH
    raw = p.read_bytes()
    cfg = yaml.safe_load(raw.decode("utf-8")) or {}
    errs = config_errors(cfg)
    if errs:
        raise FocusRefused(500, "config", f"{CONFIG_RELPATH} is refused: " + "; ".join(errs))
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    cfg["focus_version"] = str(cfg["focus_version"])
    return cfg


def workload_keys(cfg: dict) -> list[str]:
    return [w["key"] for w in cfg["workloads"]]


def exports_dir(cfg: dict) -> Path:
    """Where FOCUS exports are read from: ``exports.dir`` (repo-relative), or ``ICDEV_FINOPS_FOCUS_DIR``."""
    env = _text(os.environ.get("ICDEV_FINOPS_FOCUS_DIR"))
    p = Path(env or cfg["exports"]["dir"]).expanduser()
    return p if p.is_absolute() else BASE_DIR / p


# ── the pinned schema ────────────────────────────────────────────────────────


def schema() -> dict:
    raw = (BASE_DIR / SCHEMA_RELPATH).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != SCHEMA_SHA256:
        raise FocusRefused(500, "schema", f"{SCHEMA_RELPATH} is not the pinned ICDEV[OS] contract (sha256 {got}, "
                                          f"pinned {SCHEMA_SHA256})")
    return json.loads(raw)


def schema_digest(s: Optional[dict] = None) -> str:
    """The schema digest a bundle pins: the schema without its version stamp, canonical JSON, SHA-256."""
    s = s if s is not None else schema()
    return digest({k: v for k, v in s.items() if k != "x-icos-contract"})


def _pinned() -> dict:
    s = schema()
    got = schema_digest(s)
    if got != SCHEMA_DIGEST:
        raise FocusRefused(500, "schema", f"{SCHEMA_RELPATH}'s schema digest is {got}, pinned {SCHEMA_DIGEST}")
    return s


def row_schema(s: Optional[dict] = None) -> dict:
    s = s or _pinned()
    return {"$schema": s.get("$schema"), "$defs": s["$defs"], "$ref": "#/$defs/FocusRow"}


def columns(s: Optional[dict] = None) -> dict[str, set[str]]:
    """The FocusRow columns by how CSV text is read: ``number``, ``json``, ``datetime`` (the rest are strings)."""
    props = (s or _pinned())["$defs"]["FocusRow"]["properties"]
    out: dict[str, set[str]] = {"number": set(), "json": set(), "datetime": set()}
    for name, p in props.items():
        types = p.get("type")
        types = {types} if isinstance(types, str) else set(types or ())
        if "number" in types:
            out["number"].add(name)
        elif "object" in types or "array" in types:
            out["json"].add(name)
        if p.get("format") == "date-time":
            out["datetime"].add(name)
    return out


# ── reading an export ────────────────────────────────────────────────────────


def _number(text: str) -> Any:
    try:
        d = Decimal(text)
    except InvalidOperation:
        return text                     # left as text: the schema names it
    if not d.is_finite():
        return text
    return int(text) if re.fullmatch(r"-?\d+", text) else float(text)


def _no_constant(name: str):
    raise ValueError(f"{name} is not a JSON number")


def parse_csv(text: str, s: Optional[dict] = None) -> list[dict]:
    cols = columns(s)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise FocusRefused(400, "export", "the CSV export has no header row")
    rows = []
    for raw in reader:
        if None in raw:
            raise FocusRefused(400, "export", f"CSV line {reader.line_num} has more cells than the header")
        row: dict[str, Any] = {}
        for k, v in raw.items():
            k = (k or "").strip()
            v = "" if v is None else v
            if v == "":
                row[k] = None
            elif k in cols["number"]:
                row[k] = _number(v.strip())
            elif k in cols["json"]:
                try:
                    row[k] = json.loads(v, parse_constant=_no_constant)
                except ValueError:
                    row[k] = v          # left as text: the schema names it
            else:
                row[k] = v
        rows.append(row)
    return rows


def parse_json(text: str) -> list[dict]:
    body = text.strip()
    try:
        if body.startswith("["):
            rows = json.loads(body, parse_constant=_no_constant)
        else:
            rows = [json.loads(line, parse_constant=_no_constant) for line in body.splitlines() if line.strip()]
    except ValueError as exc:
        raise FocusRefused(400, "export", f"the JSON export is not JSON: {exc}"[:300]) from exc
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise FocusRefused(400, "export", "a JSON export is an array of rows, or one row (an object) per line")
    return rows


def row_errors(rows: list[dict], s: Optional[dict] = None) -> list[str]:
    """Each row's errors against ``$defs/FocusRow`` and RFC 3339, named by row number (1 is the first row)."""
    import jsonschema

    s = s or _pinned()
    v = jsonschema.Draft202012Validator(row_schema(s), format_checker=jsonschema.FormatChecker())
    dts = columns(s)["datetime"]
    errs = []
    for i, r in enumerate(rows, start=1):
        for e in sorted(v.iter_errors(r), key=lambda e: (list(e.absolute_path), e.message)):
            where = "/".join(str(p) for p in e.absolute_path) or "(row)"
            errs.append(f"row {i}: {where}: {e.message}"[:300])
        for c in sorted(dts):
            if isinstance(r, dict) and isinstance(r.get(c), str) and _when(r[c]) is None:
                errs.append(f"row {i}: {c}: {r[c]!r} is not an RFC 3339 date-time with a zone")
    return errs


def read_export(path: Path, cfg: Optional[dict] = None, *, name: Optional[str] = None) -> dict:
    """Read and validate one FOCUS export. Returns ``{name, sha256, rows, billing_period, currency}``.

    Refuses the whole export (:class:`FocusRefused` ``export``) if any row fails, naming each (the first 10)."""
    cfg = cfg or load_config()
    lim = cfg["exports"]
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise FocusRefused(404, "no_export", f"the export {name or p.name} cannot be read: {exc}") from exc
    if len(raw) > lim["max_bytes"]:
        raise FocusRefused(413, "too_large", f"an export is at most {lim['max_bytes']} bytes")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FocusRefused(400, "export", "an export is UTF-8 text") from exc
    s = _pinned()
    rows = parse_csv(text, s) if p.suffix.lower() == ".csv" else parse_json(text)
    if not rows:
        raise FocusRefused(400, "export", "the export has no rows: there is nothing to report")
    if len(rows) > lim["max_rows"]:
        raise FocusRefused(413, "too_large", f"an export is at most {lim['max_rows']} rows ({len(rows)} given)")
    errs = row_errors(rows, s)
    if errs:
        more = f" (and {len(errs) - 10} more)" if len(errs) > 10 else ""
        raise FocusRefused(422, "export", f"{len(errs)} row error(s) against {SCHEMA} $defs/FocusRow, so the "
                                          f"export is refused, not half read: " + "; ".join(errs[:10]) + more)
    periods = sorted({(r["BillingPeriodStart"], r["BillingPeriodEnd"]) for r in rows})
    if len(periods) != 1:
        raise FocusRefused(422, "export", f"an export is one billing period; this one spans {len(periods)}: "
                                          + ", ".join(f"{a} to {b}" for a, b in periods[:5]))
    wrong = sorted({r["BillingCurrency"] for r in rows} - {cfg["currency"]})
    if wrong:
        raise FocusRefused(422, "export", f"rows are billed in {', '.join(wrong)}, not {cfg['currency']} "
                                          f"({CONFIG_RELPATH} currency)")
    return {"name": name or p.name, "sha256": hashlib.sha256(raw).hexdigest(), "rows": rows,
            "billing_period": {"start": periods[0][0], "end": periods[0][1]}, "currency": cfg["currency"]}


def list_exports(cfg: Optional[dict] = None) -> list[dict]:
    cfg = cfg or load_config()
    d = exports_dir(cfg)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in cfg["exports"]["suffixes"] and _EXPORT_NAME.match(p.name):
            out.append({"name": p.name, "bytes": p.stat().st_size,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    return out


def export_path(name: str, cfg: Optional[dict] = None) -> Path:
    """An export by NAME in the declared directory, never a path: anything else is refused."""
    cfg = cfg or load_config()
    n = _text(name)
    if not _EXPORT_NAME.match(n) or Path(n).suffix.lower() not in cfg["exports"]["suffixes"]:
        raise FocusRefused(400, "export_name", f"an export is named by its file name in the exports directory "
                                               f"({', '.join(cfg['exports']['suffixes'])}), not {name!r}")
    d = exports_dir(cfg)
    p = d / n
    if p.resolve().parent != d.resolve() or not p.is_file():
        raise FocusRefused(404, "no_export", f"no export {n!r} in the exports directory")
    return p


# ── the bundle ───────────────────────────────────────────────────────────────


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("icdev")
    except Exception:  # noqa: BLE001 — an unpackaged checkout still says what it is
        return "unpackaged"


def amount(v: Any) -> str:
    """A decimal as the bundle writes it: six places, half-even."""
    return str(Decimal(str(v)).quantize(Q, rounding=ROUND_HALF_EVEN))


def workload_of(row: dict, tag: str) -> Optional[str]:
    tags = row.get("Tags")
    v = tags.get(tag) if isinstance(tags, dict) else None
    return v if isinstance(v, str) and v else None


def totals(rows: list[dict], tag: str, keys: Iterable[str]) -> tuple[dict[str, dict], dict]:
    """Each listed workload's total over the rows that name it, and what no listed workload names."""
    acc = {k: {"rows": 0, **{c: Decimal(0) for c in TOTALS}} for k in keys}
    rest = {"rows": 0, **{c: Decimal(0) for c in TOTALS}}
    for r in rows:
        slot = acc.get(workload_of(r, tag) or "", rest)
        slot["rows"] += 1
        for c in TOTALS:
            slot[c] += Decimal(str(r.get(c) or 0))

    def out(a: dict) -> dict:
        return {"rows": a["rows"], "effective_cost": amount(a["EffectiveCost"]), "billed_cost": amount(a["BilledCost"])}
    return {k: out(a) for k, a in acc.items()}, out(rest)


def content_digest(bundle: dict) -> str:
    """SHA-256 over the canonical JSON of the bundle WITHOUT ``content_digest``, ``publication`` and
    ``produced_at``: what was reported, not when the file was written or who published it. ICDEV[OS] recomputes it."""
    return digest({k: v for k, v in bundle.items() if k not in ("content_digest", "publication", "produced_at")})


def build(export: dict, *, cfg: Optional[dict] = None, at: Optional[str] = None) -> dict:
    """The ``icos-focus-cost/v1`` bundle of one read export (:func:`read_export`), unpublished."""
    cfg = cfg or load_config()
    keys = workload_keys(cfg)
    per, rest = totals(export["rows"], cfg["tag_key"], keys)
    bundle = {
        "schema": SCHEMA,
        "producer": {"system": PRODUCER_SYSTEM, "tool": PRODUCER_TOOL, "version": _version()},
        "produced_at": _at(at),
        "classification": cfg.get("classification"),
        "focus": {"version": cfg["focus_version"], "dataset": DATASET},
        "pin": {"schema": SCHEMA, "sha256": SCHEMA_DIGEST},
        "billing_period": dict(export["billing_period"]),
        "currency": cfg["currency"],
        "service_tag": cfg["tag_key"],
        "rows": export["rows"],
        "workloads": [{"key": k, **per[k]} for k in keys],
        "unattributed": rest,
        "publication": None,
    }
    bundle["content_digest"] = content_digest(bundle)
    errs = verify(bundle, cfg=cfg, draft_ok=True)
    if errs:
        raise FocusRefused(500, "contract", f"the bundle does not meet {SCHEMA}: " + "; ".join(errs[:10]))
    return bundle


def preview(name: str, *, cfg: Optional[dict] = None, at: Optional[str] = None) -> dict:
    """The draft bundle an export (by name) would be prepared as now, and what it was read from."""
    cfg = cfg or load_config()
    ex = read_export(export_path(name, cfg), cfg, name=name)
    return {"export": {"name": ex["name"], "sha256": ex["sha256"], "rows": len(ex["rows"])},
            "bundle": build(ex, cfg=cfg, at=at)}


def verify(bundle: Any, *, cfg: Optional[dict] = None, draft_ok: bool = False) -> list[str]:
    """Every reason ICDEV[OS] would refuse this bundle (empty: it would import it). ``draft_ok`` allows no
    publication (a draft, still checked against everything else)."""
    import jsonschema

    if not isinstance(bundle, dict):
        return ["(root): a bundle is a JSON object"]
    cfg = cfg or load_config()
    s = _pinned()
    v = jsonschema.Draft202012Validator(s, format_checker=jsonschema.FormatChecker())
    errs = sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"[:300]
                  for e in v.iter_errors(bundle))
    if errs:
        return errs
    for where, val in (("produced_at", bundle["produced_at"]), ("billing_period/start", bundle["billing_period"]["start"]),
                       ("billing_period/end", bundle["billing_period"]["end"])):
        if _when(val) is None:
            errs.append(f"{where}: {val!r} is not an RFC 3339 date-time with a zone")
    errs += row_errors(bundle["rows"], s)
    if bundle["focus"]["version"] != cfg["focus_version"]:
        errs.append(f"version: its rows are FOCUS {bundle['focus']['version']}, not {cfg['focus_version']}")
    if bundle["pin"]["sha256"] != SCHEMA_DIGEST:
        errs.append(f"wrong_pin: built against schema {bundle['pin']['sha256'][:12]}, not {SCHEMA_DIGEST[:12]}")
    got = content_digest(bundle)
    if got != bundle["content_digest"]:
        errs.append(f"tampered: content_digest {bundle['content_digest'][:12]} is not its content's ({got[:12]})")
    keys = [w["key"] for w in bundle["workloads"]]
    declared = workload_keys(cfg)
    if len(set(keys)) != len(keys):
        errs.append(f"inconsistent: a workload is listed twice: {keys}")
    if sorted(set(keys)) != sorted(declared):
        errs.append(f"undeclared: it reports {sorted(set(keys))}, the declared workloads are {sorted(declared)}")
    if bundle["service_tag"] != cfg["tag_key"]:
        errs.append(f"inconsistent: rows attributed by Tags[{bundle['service_tag']!r}], not Tags[{cfg['tag_key']!r}]")
    wrong = sorted({r["BillingCurrency"] for r in bundle["rows"]} - {bundle["currency"]})
    if wrong:
        errs.append(f"inconsistent: rows billed in {', '.join(wrong)}, not the bundle's {bundle['currency']}")
    want, rest = totals(bundle["rows"], bundle["service_tag"], keys)
    for w in bundle["workloads"]:
        mine = want.get(w["key"]) or {}
        if {k: w.get(k) for k in mine} != mine:
            errs.append(f"tampered: {w['key']}'s total {w['effective_cost']} over {w['rows']} rows is not its rows' "
                        f"({mine.get('effective_cost')} over {mine.get('rows')})")
    if bundle["unattributed"] != rest:
        errs.append(f"tampered: the unattributed total is not its rows' ({rest['effective_cost']})")
    pub = bundle.get("publication")
    if not pub:
        if not draft_ok:
            errs.append("unpublished: nobody published it; a bundle with no publication is a draft")
    else:
        if pub["content_digest"] != got:
            errs.append(f"unpublished: published at {pub['content_digest'][:12]}, not at its content's {got[:12]}")
        if pub["signed_by"] == pub["prepared_by"]:
            errs.append(f"unpublished: {pub['signed_by']} prepared it and published it")
    return errs


def summary(bundle: dict) -> dict:
    return {"billing_period": bundle["billing_period"], "currency": bundle["currency"], "rows": len(bundle["rows"]),
            "workloads": bundle["workloads"], "unattributed": bundle["unattributed"]}


def period_of(bundle: dict) -> str:
    """The ledger chain a bundle belongs to: its billing month (YYYY-MM)."""
    return str(bundle["billing_period"]["start"])[:7]


# ── the ledger: prepared, then a person publishes ────────────────────────────


def _connection():
    """The platform database without the row-security predicate (the ledger has no label column: it is the
    platform's own FinOps record, as coe_conformance_events is)."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def read_events(conn=None, period: Optional[str] = None) -> list[dict]:
    own = conn is None
    conn = conn or _connection()
    try:
        sql = f"SELECT {', '.join(_COLS)} FROM {TABLE}"  # nosec B608 — fixed table and columns
        params: tuple = ()
        if period:
            sql += " WHERE period = %s"
            params = (period,)
        rows = conn.execute(sql + " ORDER BY created_at, event_id", params).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Each billing month's rows in hash-chain order, plus ``_problems`` (a row that does not verify, or is off its
    chain)."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["period"]), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for period, items in sorted(by.items()):
        nxt = {str(r["prev_hash"]): r for r in items}
        ordered, prev = [], GENESIS
        while prev in nxt:
            r = nxt.pop(prev)
            ordered.append(r)
            prev = str(r["row_hash"])
        for r in items:
            if _row_hash(r) != r["row_hash"]:
                out["_problems"].append({"event_id": r["event_id"], "period": period,
                                         "problem": "row hash does not match its content"})
            elif r["event"] == "prepared":
                try:
                    if content_digest(json.loads(r["bundle_json"])) != r["content_digest"]:
                        raise ValueError
                except (ValueError, TypeError):
                    out["_problems"].append({"event_id": r["event_id"], "period": period,
                                             "problem": "the bundle does not match its digest"})
        for r in nxt.values():
            out["_problems"].append({"event_id": r["event_id"], "period": period,
                                     "problem": "not on the billing month's hash chain"})
        out[period] = ordered
    return out


def _detail(row: dict) -> dict:
    try:
        return json.loads(row.get("detail_json") or "{}")
    except ValueError:
        return {}


def _public(r: dict) -> dict:
    return {k: r[k] for k in ("event_id", "period", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                              "content_digest", "created_at", "row_hash", "prev_hash")} | {"detail": _detail(r)}


def replay(ordered: list[dict]) -> dict:
    """One billing month's state: the latest prepared bundle (and whether a person decided it), every publication."""
    prepared = None
    published: list[dict] = []
    history = []
    by_id = {}
    for r in ordered:
        h = _public(r)
        history.append(h)
        by_id[r["event_id"]] = h
        if r["event"] == "prepared":
            prepared = {**h, "decided": None}
        else:
            pe = h["detail"].get("prepared_event")
            if prepared and pe == prepared["event_id"]:
                prepared["decided"] = h
            if r["event"] == "published":
                prep = by_id.get(pe) or {}
                published.append({**h, "prepared_event": pe, "prepared_by": prep.get("actor")})
    return {"prepared": prepared, "published": published, "history": history}


def _head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def _append(conn, *, period: str, event: str, actor: str, actor_kind: str, content: str, prev_hash: str,
            bundle: Optional[dict] = None, actor_role: str = "", step_id: str = "", reason: str = "",
            detail: Optional[dict] = None) -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    row = {"event_id": f"fofe-{uuid.uuid4().hex[:16]}", "period": period, "event": event, "actor": actor,
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
            raise FocusRefused(409, "concurrent", f"{period}'s ledger changed while you were deciding: reload it") from exc
        raise FocusRefused(503, "store_unavailable",
                           f"the FOCUS ledger could not be written, so nothing changed ({exc})") from exc
    return row


def _actor_kind(actor: dict) -> str:
    if _text(actor.get("id")).startswith("bootstrap:"):
        return "bootstrap"
    if actor.get("role") == "service" or _text(actor.get("id")).startswith("agent:"):
        return "agent"
    return "person"


def _period_state(period: str, conn) -> tuple[dict, list[dict], list[dict]]:
    rows = read_events(conn, period)
    ch = chain(rows)
    return replay(ch.get(period, [])), ch["_problems"], ch.get(period, [])


def prepare(export: str, actor: Optional[dict], *, note: str = "", conn=None, at: Optional[str] = None,
            bundle: Optional[dict] = None) -> dict:
    """Read an export (by name), build its bundle and record it, at its digest, as PREPARED. Decides nothing.

    Anyone signed in, or an agent (recorded as one). A person who did not prepare it publishes it or refuses."""
    if not isinstance(actor, dict) or not actor.get("id"):
        raise FocusRefused(401, "no_actor", "preparing a bundle needs a signed-in person or a named agent")
    cfg = load_config()
    if bundle is None:
        ex = read_export(export_path(export, cfg), cfg, name=export)
        b = build(ex, cfg=cfg, at=at)
        source = {"name": ex["name"], "sha256": ex["sha256"]}
    else:
        b = bundle
        source = {"name": _text(export), "sha256": None}
    period = period_of(b)
    own = conn is None
    conn = conn or _connection()
    try:
        _, problems, ordered = _period_state(period, conn)
        if problems:
            raise FocusRefused(409, "chain_broken", f"{period}'s ledger does not verify: {problems[0]['problem']}")
        row = _append(conn, period=period, event="prepared", actor=_text(actor["id"]),
                      actor_kind=_actor_kind(actor), actor_role=_text(actor.get("role")),
                      content=b["content_digest"], bundle=b, reason=note,
                      detail={"export": source, "summary": summary(b)}, prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    return {"event_id": row["event_id"], "period": period, "content_digest": b["content_digest"],
            "prepared_by": row["actor"], "actor_kind": row["actor_kind"], "export": source,
            "summary": summary(b), "template": TEMPLATE,
            "next": f"a person who did not prepare it publishes it or refuses, on {PAGE}"}


def builders(prepared: Optional[dict]) -> dict[str, str]:
    """Who may not publish the prepared bundle (``separate_from: builders``): whoever prepared it."""
    who = _text((prepared or {}).get("actor"))
    return {who: "prepared this bundle"} if who else {}


def _event(event_id: str, conn) -> dict:
    rows = conn.execute(f"SELECT {', '.join(_COLS)} FROM {TABLE} WHERE event_id = %s",  # nosec B608 — fixed
                        (_text(event_id),)).fetchall()
    if not rows:
        raise FocusRefused(404, "no_event", f"no FOCUS ledger event {event_id!r}")
    return dict(zip(_COLS, tuple(rows[0])))


def publish(event_id: str, *, decision: str, reason: str, read_digest: str, actor: Optional[dict],
            conn=None) -> dict:
    """A person who did not prepare it publishes a prepared bundle to ICDEV[OS], or refuses it.

    The person's step of ``decl-wft-finops-focus-publish``, with a reason, bound to the digest of the prepared bundle
    they read. Only the billing month's NEWEST prepared bundle, undecided, can be decided. A published bundle carries
    its ``publication``; a refused one never leaves."""
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise FocusRefused(401, "no_person", f"publishing is a signed-in person's declared step on {PAGE}")
    own = conn is None
    conn = conn or _connection()
    try:
        ev = _event(event_id, conn)
        if ev["event"] != "prepared":
            raise FocusRefused(409, "not_prepared", f"{event_id} is a {ev['event']} event, not a prepared bundle")
        st, problems, ordered = _period_state(ev["period"], conn)
        if problems:
            raise FocusRefused(409, "chain_broken", f"{ev['period']}'s ledger does not verify: {problems[0]['problem']}")
        p = st["prepared"]
        if p is None or p["event_id"] != ev["event_id"]:
            raise FocusRefused(409, "superseded", f"a newer bundle of {ev['period']} was prepared after {event_id}: "
                                                  "decide that one")
        if p.get("decided"):
            raise FocusRefused(409, "already_decided", f"the prepared bundle {p['content_digest'][:12]} was already "
                                                       f"{p['decided']['event']}: prepare a new one")
        if _text(read_digest) != p["content_digest"]:
            raise FocusRefused(409, "digest", f"the prepared bundle is {p['content_digest'][:12]}, not what you read "
                                              "(reload it)")
        dec = _text(decision).lower()
        if dec == "publish":
            errs = verify(json.loads(ev["bundle_json"]), draft_ok=True)
            if errs:
                raise FocusRefused(409, "off_contract", f"the prepared bundle would not meet {SCHEMA}: {errs[0]}")
        export = (p.get("detail") or {}).get("export") or {}
        try:
            taken = declared.take_step(
                TEMPLATE, surface=SURFACE, decision=dec, subject=f"finops_focus:{ev['period']}",
                reason=_text(reason), actor=actor, separated_from=builders(p),
                payload={"prepared_event": p["event_id"], "content_digest": p["content_digest"],
                         "export": export.get("name"), "period": ev["period"]})
        except declared.StepRefused as exc:
            raise FocusRefused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        row = _append(conn, period=ev["period"], event=event, actor=taken["decided_by"], actor_kind="person",
                      actor_role=_text(actor.get("role")), step_id=taken["step_id"], reason=_text(reason),
                      content=p["content_digest"], detail={"prepared_event": p["event_id"], "decision": dec},
                      prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    out = {"event": event, "event_id": row["event_id"], "prepared_event": p["event_id"], "period": ev["period"],
           "content_digest": p["content_digest"], "step": taken, "decided_by": taken["decided_by"],
           "prepared_by": p["actor"]}
    if event == "published":
        out["bundle"] = published_bundle(row["event_id"])
    return out


def _with_publication(prep: dict, pub: dict) -> dict:
    b = json.loads(prep["bundle_json"])
    b["publication"] = {"signed_by": pub["actor"], "role": pub["actor_role"] or None, "step_id": pub["step_id"],
                        "decided_at": pub["created_at"], "template": TEMPLATE,
                        "content_digest": pub["content_digest"], "prepared_by": prep["actor"],
                        "event_id": pub["event_id"]}
    return b


def published_bundle(event_id: Optional[str] = None, conn=None) -> Optional[dict]:
    """A PUBLISHED bundle, as prepared, with its ``publication``: the one that publication event (or prepared event)
    names, or the newest published (``None`` / ``"latest"``). None when there is none or its month does not verify."""
    own = conn is None
    conn = conn or _connection()
    try:
        rows = read_events(conn)
    finally:
        if own:
            conn.close()
    ch = chain(rows)
    bad = {p["period"] for p in ch["_problems"]}
    pubs = []
    for period, ordered in ch.items():
        if period == "_problems" or period in bad:
            continue
        by_id = {r["event_id"]: r for r in ordered}
        for r in ordered:
            if r["event"] == "published":
                prep = by_id.get(_detail(r).get("prepared_event"))
                if prep is not None:
                    pubs.append((r, prep))
    want = _text(event_id)
    if want and want != "latest":
        hit = next(((r, p) for r, p in pubs if want in (r["event_id"], p["event_id"])), None)
        return _with_publication(hit[1], hit[0]) if hit else None
    if not pubs:
        return None
    r, p = max(pubs, key=lambda x: (x[0]["created_at"], x[0]["event_id"]))
    return _with_publication(p, r)


# ── reading ──────────────────────────────────────────────────────────────────


def ledger_view(conn=None) -> dict:
    """The page's view: every billing month's state, the exports to prepare from, the config and the pin."""
    cfg = load_config()
    rows = read_events(conn)
    ch = chain(rows)
    months = []
    for period in sorted((k for k in ch if k != "_problems"), reverse=True):
        st = replay(ch[period])
        p = st["prepared"]
        months.append({"period": period, "prepared": p, "published": list(reversed(st["published"])),
                       "history": list(reversed(st["history"])), "builders": builders(p),
                       "next": ["publish"] if p and not p.get("decided") else ["prepare"]})
    return {"months": months, "chain": {"intact": not ch["_problems"], "problems": ch["_problems"], "rows": len(rows)},
            "exports": list_exports(cfg),
            "config": {"path": CONFIG_RELPATH, "sha256": cfg["_sha256"], "focus_version": cfg["focus_version"],
                       "tag_key": cfg["tag_key"], "currency": cfg["currency"],
                       "workloads": [{"key": w["key"], "name": w["name"]} for w in cfg["workloads"]]},
            "schema": {"id": SCHEMA, "path": SCHEMA_RELPATH, "sha256": SCHEMA_SHA256, "digest": SCHEMA_DIGEST,
                       "contract": SCHEMA_CONTRACT},
            "template": TEMPLATE}


# ── twin ─────────────────────────────────────────────────────────────────────


def twin_evidence() -> dict:
    """What the governance twin reads: the ledger's state, and the newest published bundle re-verified. Never raises."""
    ev: dict[str, Any] = {"months": None, "errors": {}}
    try:
        cfg = load_config()
        rows = read_events()
        ch = chain(rows)
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["finops_focus"] = str(exc)
        return ev
    months, newest = [], None
    for period in sorted(k for k in ch if k != "_problems"):
        st = replay(ch[period])
        p = st["prepared"]
        months.append({"period": period,
                       "prepared": p and {k: p[k] for k in ("event_id", "actor", "created_at", "content_digest")}
                       | {"decided": bool(p.get("decided"))},
                       "published": len(st["published"])})
        for pub in st["published"]:
            key = (pub["created_at"], pub["event_id"])
            if newest is None or key > newest[0]:
                newest = (key, pub)
    facts = None
    if newest is not None:
        pub = newest[1]
        b = published_bundle(pub["event_id"]) or {}
        try:
            errs = verify(b, cfg=cfg) if b else ["the published bundle cannot be rebuilt from the ledger"]
        except Exception as exc:  # noqa: BLE001
            errs = [f"it cannot be verified: {exc}"]
        facts = {"event_id": pub["event_id"], "actor": pub["actor"], "prepared_by": pub.get("prepared_by"),
                 "created_at": pub["created_at"], "content_digest": pub["content_digest"],
                 "billing_period": b.get("billing_period"),
                 "workloads": [{k: w.get(k) for k in ("key", "rows", "effective_cost")} for w in b.get("workloads") or []],
                 "unattributed": b.get("unattributed"), "errors": errs}
    ev["months"] = months
    ev["newest_published"] = facts
    ev["problems"] = ch["_problems"]
    ev["workloads"] = workload_keys(cfg)
    ev["policy"] = cfg.get("publish") or {}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)`` for the FOCUS cost bundles. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    months = ev.get("months")
    checks = [check("finops.focus.ledger", "FOCUS cost bundle ledger (finops_focus_events)",
                    months is not None, errors.get("finops_focus") or "ledger not read")]
    findings: list[dict] = []
    pol = {**(ev.get("policy") or {}), **(policy or {})}
    stale = float(pol.get("stale_days") or 45)
    waiting = float(pol.get("waiting_warn_days") or 7)
    for p in ev.get("problems") or []:
        findings.append(finding("finops.focus.chain_broken", "critical", f"{p['period']}: {p['problem']}",
                                thread_id=f"finops_focus:{p['event_id']}",
                                recommendation="The bundle history cannot be trusted; the ledger is append-only, so "
                                               "find who wrote the row and treat it as an incident."))
    for m in months or []:
        prep = m.get("prepared")
        if prep and not prep.get("decided"):
            age = age_hours(prep.get("created_at"), observed_at)
            if age is not None and age > waiting * 24:
                findings.append(finding("finops.focus.awaiting_publish", "low",
                                        f"{m['period']}: a prepared bundle has waited {age / 24:.0f} days for a person",
                                        thread_id=f"finops_focus:{prep['event_id']}",
                                        recommendation="A person who did not prepare it publishes it or refuses."))
    pub = ev.get("newest_published")
    usable, why = False, "no FOCUS bundle has been published: unverified"
    if pub is not None:
        thread = f"finops_focus:{pub['event_id']}"
        usable = True
        if pub.get("actor") and pub.get("actor") == pub.get("prepared_by"):
            usable, why = False, "the newest published bundle was published by its preparer: it counts for nothing"
            findings.append(finding("finops.focus.self_published", "critical",
                                    f"{pub['event_id']}: the bundle was published by the person who prepared it",
                                    thread_id=thread, recommendation="Treat it as unpublished; a person who did not "
                                                                     "prepare it publishes a new one."))
        if pub.get("errors"):
            usable, why = False, "the newest published bundle is off its contract: it counts for nothing"
            findings.append(finding("finops.focus.off_contract", "high",
                                    f"{pub['event_id']}: the published bundle would be refused by ICDEV[OS]",
                                    thread_id=thread, detail="; ".join(pub["errors"][:3]),
                                    recommendation="Find how it changed; prepare a new bundle from the export and "
                                                   "have a person publish it."))
        end = (pub.get("billing_period") or {}).get("end")
        age = age_hours(end, observed_at) if end else None
        if age is not None and age > stale * 24:
            findings.append(finding("finops.focus.stale", "medium",
                                    f"the newest published bundle's billing period ended {age / 24:.0f} days ago",
                                    thread_id=thread, recommendation="Prepare the next month's bundle from its FOCUS "
                                                                     "export, and have a person publish it."))
        un = pub.get("unattributed") or {}
        if usable and int(un.get("rows") or 0) > 0:
            findings.append(finding("finops.focus.unattributed", "low",
                                    f"{un['rows']} row(s) ({un.get('effective_cost')}) name no declared workload",
                                    thread_id=thread, recommendation="Tag the resources with the workload key, or "
                                                                     "declare the workload."))
    checks.append(check("finops.focus.published", "A published FOCUS cost bundle ICDEV[OS] would import",
                        usable, why))
    reported = {w["key"] for w in (pub or {}).get("workloads") or []} if usable else set()
    for key in ev.get("workloads") or []:
        checks.append(check(f"finops.focus.{key}", f"{key}'s cost, reported by the newest published bundle",
                            key in reported, why if not usable else f"the newest published bundle does not list {key}"))
    summary = {"finops_focus_months": len(months or []),
               "finops_focus_published": sum(int(m.get("published") or 0) for m in months or []),
               "finops_focus_newest": (pub or {}).get("event_id")}
    return checks, findings, summary


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="FOCUS cost bundles (icos-focus-cost/v1) for ICDEV[OS]")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", type=Path, help="preview the DRAFT bundle of a FOCUS export (CSV, JSON, JSON lines)")
    g.add_argument("--published", help="write a PUBLISHED bundle: its publication (or prepared) event id, or latest")
    g.add_argument("--verify", type=Path, help="say whether ICDEV[OS] would import this bundle file, and why not")
    g.add_argument("--list", action="store_true", help="the exports in the declared directory, and the ledger")
    ap.add_argument("--at", help="produced_at (RFC 3339; default now), so a draft can be re-made byte for byte")
    ap.add_argument("--out", type=Path, help="write the bundle here (default: stdout)")
    a = ap.parse_args(argv)
    try:
        if a.list:
            v = ledger_view()
            print(json.dumps({"status": "ok", "exports": v["exports"], "chain": v["chain"],
                              "months": [{"period": m["period"], "next": m["next"],
                                          "published": [p["event_id"] for p in m["published"]]}
                                         for m in v["months"]]}, indent=2))
            return 0
        if a.verify:
            b = json.loads(a.verify.read_text(encoding="utf-8"))
            errs = verify(b)
            print(json.dumps({"status": "ok" if not errs else "refused", "errors": errs,
                              "content_digest": b.get("content_digest") if isinstance(b, dict) else None,
                              "published": bool(isinstance(b, dict) and b.get("publication"))}, indent=2))
            return 0 if not errs else 1
        if a.published:
            b = published_bundle(a.published)
            if b is None:
                print(json.dumps({"status": "error", "error": f"no published bundle {a.published!r}"}))
                return 1
        else:
            cfg = load_config()
            b = build(read_export(a.export, cfg), cfg=cfg, at=a.at)
    except FocusRefused as exc:
        print(json.dumps({"status": "error", "code": exc.code, "error": exc.message}))
        return 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    text = json.dumps(b, indent=2, sort_keys=True) + "\n"
    if not a.out:
        sys.stdout.write(text)
        return 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text, encoding="utf-8")
    print(json.dumps({"status": "ok", "out": str(a.out), "schema": b["schema"], "content_digest": b["content_digest"],
                      "published": b["publication"] is not None, "billing_period": b["billing_period"],
                      "workloads": b["workloads"], "unattributed": b["unattributed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
