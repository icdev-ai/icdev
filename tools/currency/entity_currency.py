# CUI // SP-CTI
"""Domain-agnostic entity-currency store — writer, resolver, backfill (cef-fnd-04).

WHAT IT IS

One row per (source, entity, version) ASSERTION in ``entity_currency``: what an
entity is, whether a given source says it is current, what supersedes it, as of
when, with a declared confidence and a pointer back to the row it came from.

WHAT IS NOT IN THIS FILE

No table name, no column name, no vendor, no product, no protocol, no industry.
All of those live in ``args/entity_currency.yaml`` as data, which is what makes
a fourth provider a config entry rather than a patch. The only closed vocabulary
here is :data:`VERDICTS`, because callers branch on it.

DISAGREEMENT IS PRESERVED

Two sources that disagree keep two rows. :func:`resolve` picks a winner at READ
time under the declared policy and hands back the losers next to it, so a caller
sees the conflict instead of inheriting it silently. Curated sources are
``authoritative`` and win outright — ahead of confidence, ahead of recency —
because a tie-break that can be overturned by bumping a prior is not authority.

CONFIDENCE IS A DECLARED PRIOR

Said plainly so nothing downstream mistakes it for a measurement: it is the
per-source constant from the YAML, lowered to ``unknown_confidence`` when the
source asserts no currency signal. It ranks sources against each other. Nothing
in this repo has calibrated it.

CLI
    python -m tools.currency.entity_currency --backfill --json
    python -m tools.currency.entity_currency --stats --json
    python -m tools.currency.entity_currency --resolve <entity> [--entity-type T]
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = _REPO_ROOT / "args" / "entity_currency.yaml"

TABLE = "entity_currency"

#: The one CLOSED vocabulary. Validated here and deliberately NOT by a CHECK
#: constraint — a CHECK is a second copy that drifts the first time a verdict is
#: added (migrations 20260803002224, 20260809203855, 20260816122036 made the
#: same call).
VERDICTS = frozenset({
    "current",                 # the source asserts it is the thing to be on
    "scheduled_end_of_life",   # supported now, with an announced end date
    "deprecated",              # discouraged, not yet dead
    "end_of_support",          # support ended; not formally end-of-life
    "end_of_life",             # the source asserts it is done
    "unknown",                 # the source has heard of it and asserts nothing
})

#: Recognised ``verdict.strategy`` values in the YAML.
STRATEGIES = frozenset({"dates", "value_map"})

#: Platform-wide columns carried through from a source row when it has them.
#: Not domain vocabulary — these two are the RLS columns every ICDEV table uses.
_PASSTHROUGH_COLUMNS = ("tenant_id", "classification")

#: A table/column name interpolated into SQL must look like an identifier. The
#: names come from a repo-owned config file, not from a request, but the check
#: costs nothing and keeps the interpolation obviously safe.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_cache: dict = {"config": None, "mtime": None}


class ConfigError(ValueError):
    """A source declaration in args/entity_currency.yaml is unusable."""


# ── config ────────────────────────────────────────────────────────────────────

def load_config(force: bool = False) -> dict:
    """Source declarations, mtime hot-reloaded (same shape as pack_loader)."""
    import yaml

    if not CONFIG_PATH.exists():
        return {}
    mtime = CONFIG_PATH.stat().st_mtime
    if not force and _cache["config"] is not None and _cache["mtime"] == mtime:
        return _cache["config"]
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    _cache["config"], _cache["mtime"] = cfg, mtime
    return cfg


def declared_sources(enabled_only: bool = True) -> list[dict]:
    """Source specs from the YAML, in declaration order."""
    out = []
    for spec in load_config().get("sources") or []:
        if not isinstance(spec, dict) or not spec.get("id"):
            continue
        if enabled_only and not spec.get("enabled", True):
            continue
        out.append(spec)
    return out


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def normalize_key(value: Any) -> str:
    """Casefold + collapse whitespace — the join key.

    Nothing domain-specific: no stripping of vendor prefixes, no model-number
    parsing. Two sources spelling the same entity differently stay different
    keys, which is honest — silently merging them would invent an agreement.
    """
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _iso(value: Any) -> str:
    """Best-effort ISO8601 rendering of whatever the source stored."""
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value).strip()


def _date10(value: Any) -> Optional[str]:
    """First 10 chars of an ISO date, or None. Comparison is lexical, which is
    exactly right for zero-padded ISO dates and wrong for anything else — so a
    value that does not look like one is dropped rather than mis-compared."""
    s = _iso(value)[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def derive_verdict_from_dates(
    eol_date: Any, eos_date: Any, today: Optional[str] = None
) -> str:
    """Generic date logic — no domain, no vendor, no product.

    Past EOL dominates past EOS (a thing past both is end-of-life, not merely
    unsupported). A future EOL is `scheduled_end_of_life` rather than `current`
    because "supported, with a known horizon" is the actionable answer. No date
    at all is `unknown` and never `current`: absence of an announcement is not
    evidence of support.
    """
    today = today or _today()
    eol, eos = _date10(eol_date), _date10(eos_date)
    if eol and eol <= today:
        return "end_of_life"
    if eos and eos <= today:
        return "end_of_support"
    if eol:
        return "scheduled_end_of_life"
    if eos:
        return "current"
    return "unknown"


def _derive_verdict(spec: dict, row: dict) -> str:
    rule = spec.get("verdict") or {}
    strategy = str(rule.get("strategy") or "dates")
    if strategy not in STRATEGIES:
        raise ConfigError(
            f"source '{spec.get('id')}': unknown verdict strategy '{strategy}' "
            f"(known: {sorted(STRATEGIES)})"
        )
    if strategy == "value_map":
        column = rule.get("column")
        if not column:
            raise ConfigError(f"source '{spec.get('id')}': value_map needs a `column`")
        raw = str(row.get(column) or "").strip().lower()
        verdict = str((rule.get("map") or {}).get(raw) or rule.get("default") or "unknown")
    else:
        cols = spec.get("columns") or {}
        verdict = derive_verdict_from_dates(
            row.get(cols.get("eol_date")), row.get(cols.get("eos_date"))
        )
    if verdict not in VERDICTS:
        raise ConfigError(
            f"source '{spec.get('id')}': verdict '{verdict}' is not in VERDICTS"
        )
    return verdict


# ── the assertion ─────────────────────────────────────────────────────────────

@dataclass
class CurrencyAssertion:
    """One source's answer about one entity at one version."""

    entity_type: str
    entity_key: str
    verdict: str
    source: str
    as_of: str
    namespace: str = ""
    entity_label: str = ""
    entity_version: str = ""
    superseded_by: Optional[str] = None
    source_kind: str = "derived"
    confidence: float = 0.0
    eol_date: Optional[str] = None
    eos_date: Optional[str] = None
    provenance_table: Optional[str] = None
    provenance_id: Optional[str] = None
    provenance: dict = field(default_factory=dict)
    tenant_id: str = "default"
    classification: str = "CUI"

    def __post_init__(self) -> None:
        self.entity_key = normalize_key(self.entity_key)
        self.namespace = normalize_key(self.namespace)
        self.entity_type = normalize_key(self.entity_type)
        self.entity_version = str(self.entity_version or "").strip()
        self.entity_label = str(self.entity_label or "").strip()
        if not self.entity_key:
            raise ValueError("CurrencyAssertion needs a non-empty entity_key")
        if not self.entity_type:
            raise ValueError("CurrencyAssertion needs a non-empty entity_type")
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown verdict '{self.verdict}' (known: {sorted(VERDICTS)})")
        self.as_of = _iso(self.as_of) or _now()
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))

    @property
    def record_id(self) -> str:
        """Deterministic id over the identity tuple.

        Deterministic rather than a uuid4 so a re-run of the same source over the
        same entity addresses the same row even on a backend that resolves the
        conflict target differently — a uuid id would make every re-run look like
        new evidence to anything joining on it.
        """
        ident = "|".join((
            self.source, self.entity_type, self.namespace,
            self.entity_key, self.entity_version,
        ))
        return "ec-" + hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]


# ── db ────────────────────────────────────────────────────────────────────────

def _connect():
    from tools.db.storage import get_connection
    return get_connection()


_INSERT = (
    f"INSERT INTO {TABLE} "  # nosec B608 - module constant; every value is bound
    "(record_id, entity_type, namespace, entity_key, entity_label, entity_version, "
    " verdict, superseded_by, source, source_kind, as_of, observed_at, confidence, "
    " eol_date, eos_date, provenance_table, provenance_id, provenance_json, "
    " tenant_id, classification) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (source, entity_type, namespace, entity_key, entity_version) "
    "DO UPDATE SET "
    "    entity_label = excluded.entity_label, "
    "    verdict = excluded.verdict, "
    "    superseded_by = excluded.superseded_by, "
    "    source_kind = excluded.source_kind, "
    "    as_of = excluded.as_of, "
    "    observed_at = excluded.observed_at, "
    "    confidence = excluded.confidence, "
    "    eol_date = excluded.eol_date, "
    "    eos_date = excluded.eos_date, "
    "    provenance_table = excluded.provenance_table, "
    "    provenance_id = excluded.provenance_id, "
    "    provenance_json = excluded.provenance_json"
)


def upsert(assertions: Iterable[CurrencyAssertion], conn=None) -> int:
    """Write assertions, idempotently. Returns the number written."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        observed = _now()
        written = 0
        for a in assertions:
            conn.execute(_INSERT, (
                a.record_id, a.entity_type, a.namespace, a.entity_key,
                a.entity_label, a.entity_version, a.verdict, a.superseded_by,
                a.source, a.source_kind, a.as_of, observed, a.confidence,
                _date10(a.eol_date), _date10(a.eos_date), a.provenance_table,
                a.provenance_id, json.dumps(a.provenance, default=str, sort_keys=True),
                a.tenant_id, a.classification,
            ))
            written += 1
        conn.commit()
        return written
    finally:
        if own:
            conn.close()


# ── backfill ──────────────────────────────────────────────────────────────────

def _select_columns(spec: dict) -> list[str]:
    cols = {c for c in (spec.get("columns") or {}).values() if c}
    cols.update(c for c in (spec.get("extra_columns") or []) if c)
    if spec.get("entity_type_column"):
        cols.add(spec["entity_type_column"])
    rule = spec.get("verdict") or {}
    if rule.get("column"):
        cols.add(rule["column"])
    cols.update(_PASSTHROUGH_COLUMNS)
    bad = [c for c in cols if not _IDENT_RE.match(str(c))]
    if bad:
        raise ConfigError(f"source '{spec.get('id')}': bad column name(s) {bad}")
    return sorted(cols)


def _read_source(spec: dict, conn) -> list[dict]:
    """Read a declared source table, tolerating columns it does not have.

    The passthrough and extra columns are optional by design, so a missing one
    is retried away rather than failing the source: `SELECT *` on the second
    attempt, and Python picks what it needs. That keeps a source usable on a
    database whose migration history predates one of its columns.
    """
    table = spec.get("table")
    if not table or not _IDENT_RE.match(str(table)):
        raise ConfigError(f"source '{spec.get('id')}': bad or missing `table`")
    columns = _select_columns(spec)
    try:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table}"  # nosec B608 - identifiers validated above
        ).fetchall()
    except Exception as exc:
        logger.debug("entity_currency: narrow select on %s failed (%s); widening", table, exc)
        try:
            conn.rollback()  # PG: a failed statement poisons the transaction
        except Exception:
            pass
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # nosec B608 - validated
    return [dict(r) for r in rows]


def _assertion_from_row(
    spec: dict, row: dict, cfg: dict, prov_columns: list[str]
) -> Optional[CurrencyAssertion]:
    cols = spec.get("columns") or {}

    def col(field_name: str) -> Any:
        name = cols.get(field_name)
        return row.get(name) if name else None

    key = col("entity_key")
    if not str(key or "").strip():
        return None  # a row that names no entity asserts nothing

    if spec.get("entity_type_column"):
        entity_type = row.get(spec["entity_type_column"])
    else:
        entity_type = spec.get("entity_type")
    if not str(entity_type or "").strip():
        return None

    verdict = _derive_verdict(spec, row)
    confidence = float(spec.get("confidence") or 0.0)
    if verdict == "unknown":
        confidence = min(confidence, float(cfg.get("unknown_confidence") or 0.0))

    version = str(col("entity_version") or "").strip()
    superseded = str(col("superseded_by") or "").strip() or None
    if superseded and superseded == version:
        superseded = None  # "move to what you are already on" is not a supersession

    # The raw source fields, for the case where the origin row has since been
    # overwritten — both external feeds are MUTABLE upsert caches, so that is
    # routine. The two RLS columns are excluded: they have columns of their own
    # here, and a second copy could only drift from the first.
    provenance = {
        c: row.get(c) for c in prov_columns
        if c in row and c not in _PASSTHROUGH_COLUMNS
    }

    return CurrencyAssertion(
        entity_type=entity_type,
        entity_key=key,
        entity_label=str(col("entity_label") or key),
        entity_version=version,
        namespace=col("namespace") or "",
        verdict=verdict,
        superseded_by=superseded,
        source=str(spec["id"]),
        source_kind=str(spec.get("kind") or "derived"),
        as_of=_iso(col("as_of")) or _now(),
        confidence=confidence,
        eol_date=col("eol_date"),
        eos_date=col("eos_date"),
        provenance_table=str(spec.get("table")),
        provenance_id=str(col("provenance_id") or "") or None,
        provenance=provenance,
        tenant_id=str(row.get("tenant_id") or "default"),
        classification=str(row.get("classification") or "CUI"),
    )


def backfill(conn=None, sources: Optional[Iterable[str]] = None) -> dict:
    """Re-derive the store from every declared source. Idempotent.

    Per-source isolation is deliberate: one unreadable table must not cost the
    other providers their rows, and a source that failed reports WHY rather than
    reporting zero — the two are different findings.
    """
    own = conn is None
    if own:
        conn = _connect()
    wanted = {str(s) for s in sources} if sources else None
    cfg = load_config()
    out: dict = {"sources": {}, "written": 0, "errors": {}}
    try:
        for spec in declared_sources():
            sid = str(spec["id"])
            if wanted and sid not in wanted:
                continue
            try:
                rows = _read_source(spec, conn)
                prov_columns = _select_columns(spec)
                assertions = []
                skipped = 0
                for r in rows:
                    a = _assertion_from_row(spec, r, cfg, prov_columns)
                    if a is None:
                        skipped += 1
                    else:
                        assertions.append(a)
                written = upsert(assertions, conn=conn)
                out["sources"][sid] = {
                    "read": len(rows), "written": written, "skipped": skipped,
                    "kind": spec.get("kind"), "table": spec.get("table"),
                }
                out["written"] += written
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning("entity_currency backfill: source %s failed: %s", sid, exc)
                out["errors"][sid] = str(exc)
                out["sources"][sid] = {"read": 0, "written": 0, "error": str(exc)}
        return out
    finally:
        if own:
            conn.close()


# ── read ──────────────────────────────────────────────────────────────────────

def _is_authoritative(spec_by_id: dict, row: dict) -> bool:
    return bool((spec_by_id.get(str(row.get("source")), {}) or {}).get("authoritative"))


def _sort_by_policy(rows: list[dict], spec_by_id: dict, order: list[str]) -> list[dict]:
    """Apply the declared resolution keys, least significant first.

    A stable sort run once per key, in reverse policy order, IS a lexicographic
    multi-key sort — and it keeps each key's direction obvious instead of hiding
    a descending sort inside a negated composite key.
    """
    ranked = list(rows)
    for key in reversed(list(order)):
        if key == "authoritative":
            ranked.sort(key=lambda r: 0 if _is_authoritative(spec_by_id, r) else 1)
        elif key == "confidence":
            ranked.sort(key=lambda r: float(r.get("confidence") or 0.0), reverse=True)
        elif key == "as_of":
            ranked.sort(key=lambda r: _iso(r.get("as_of")), reverse=True)
        else:
            logger.debug("entity_currency: ignoring unknown resolution key '%s'", key)
    return ranked


def query(
    entity_key: str,
    entity_type: Optional[str] = None,
    namespace: Optional[str] = None,
    entity_version: Optional[str] = None,
    conn=None,
) -> list[dict]:
    """Every assertion any source holds about one entity."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        sql = f"SELECT * FROM {TABLE} WHERE entity_key = %s"  # nosec B608 - constant identifier
        params: list = [normalize_key(entity_key)]
        if entity_type:
            sql += " AND entity_type = %s"
            params.append(normalize_key(entity_type))
        if namespace is not None:
            sql += " AND namespace = %s"
            params.append(normalize_key(namespace))
        if entity_version is not None:
            sql += " AND entity_version = %s"
            params.append(str(entity_version).strip())
        try:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        except Exception as exc:
            logger.warning("entity_currency: query failed (%s)", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return []
    finally:
        if own:
            conn.close()


def resolve(
    entity_key: str,
    entity_type: Optional[str] = None,
    namespace: Optional[str] = None,
    entity_version: Optional[str] = None,
    conn=None,
) -> Optional[dict]:
    """The store's answer, with the disagreement attached.

    Returns None when NO source has heard of the entity — which is a different
    answer from every source reporting 'unknown', and the caller must be able to
    tell them apart. Never guesses: an entity nobody knows gets no verdict, not
    a 'current' one.
    """
    rows = query(entity_key, entity_type, namespace, entity_version, conn=conn)
    if not rows:
        return None
    spec_by_id, order = _policy()
    return _resolution_view(rows, spec_by_id, order)


def _policy() -> tuple:
    """``(spec_by_id, resolution order)`` as declared in the YAML.

    Read once per resolution rather than per row: both :func:`resolve` and
    :func:`search` need the same two values, and a search over many entities
    must not re-parse the config for each one.
    """
    spec_by_id = {str(s["id"]): s for s in declared_sources(enabled_only=False)}
    order = (load_config().get("resolution") or {}).get("order") or [
        "authoritative", "confidence", "as_of"
    ]
    return spec_by_id, order


def _resolution_view(rows: list[dict], spec_by_id: dict, order: list[str]) -> dict:
    """One entity's assertions ranked under the policy, with the losers attached.

    Callers get the winner's fields flattened and every other source's row under
    ``others`` — the disagreement travels WITH the answer rather than being
    dropped by whoever reads the verdict.
    """
    ranked = _sort_by_policy(rows, spec_by_id, order)
    winner = ranked[0]
    others = ranked[1:]
    verdicts = {str(r.get("verdict")) for r in rows}
    return {
        "entity_key": winner.get("entity_key"),
        "entity_type": winner.get("entity_type"),
        "namespace": winner.get("namespace"),
        "entity_version": winner.get("entity_version"),
        "entity_label": winner.get("entity_label"),
        "classification": winner.get("classification"),
        "verdict": winner.get("verdict"),
        "superseded_by": winner.get("superseded_by"),
        "source": winner.get("source"),
        "authoritative": _is_authoritative(spec_by_id, winner),
        "as_of": winner.get("as_of"),
        "confidence": winner.get("confidence"),
        "eol_date": winner.get("eol_date"),
        "eos_date": winner.get("eos_date"),
        "provenance": {
            "table": winner.get("provenance_table"),
            "id": winner.get("provenance_id"),
            "record_id": winner.get("record_id"),
        },
        # True when the sources do not agree. Reported, never resolved away.
        "conflict": len(verdicts) > 1,
        "sources_consulted": sorted({str(r.get("source")) for r in rows}),
        "others": others,
    }


#: Words a free-text currency question carries that say nothing about WHICH
#: entity is being asked about. English question scaffolding and the currency
#: verbs themselves — not domain vocabulary, and deliberately short: a long
#: stoplist starts deciding which entities are askable.
_SEARCH_STOPWORDS = frozenset({
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "by", "can",
    "do", "does", "for", "from", "has", "have", "in", "is", "it", "longer",
    "no", "of", "on", "or", "our", "out", "should", "that", "the", "their",
    "there", "this", "to", "up", "use", "used", "using", "version", "was",
    "we", "what", "when", "which", "with", "you",
    # The currency verbs themselves. They say WHAT is being asked, never about
    # WHICH entity, and the router has already read them — leaving them in
    # would score every entity down by the same fraction for carrying none of
    # them. Hyphenated forms are listed because the tokenizer keeps `-`.
    "current", "currently", "deprecated", "eol", "eos", "eosl", "end-of-life",
    "end-of-support", "end-of-sale", "life", "obsolete", "retired", "still",
    "sunset", "superseded", "supported",
})

#: A search token: starts alphanumeric, may carry the punctuation real product
#: and version strings contain (``tls 1.1``, ``c9300-48``, ``rhel_8``).
_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")

#: Rows scanned per search before the read stops. The cap exists so a two-token
#: query cannot pull the whole table into memory; when it is hit, every returned
#: view carries ``scan_truncated: True`` — a bounded read that does not SAY it
#: was bounded reads as full coverage, which is the defect this platform keeps
#: finding.
SEARCH_ROW_CAP = 400


def search_terms(text: Any) -> list[str]:
    """Entity-bearing tokens of a free-text question, lowercased, in order."""
    seen: list[str] = []
    for token in _SEARCH_TOKEN_RE.findall(str(text or "")):
        low = token.casefold()
        if low in _SEARCH_STOPWORDS or len(low) < 2:
            continue
        if low not in seen:
            seen.append(low)
    return seen


def match_score(terms: list[str], haystack: str) -> float:
    """Fraction of the query's entity terms present in one entity's own text."""
    if not terms:
        return 0.0
    return sum(1 for t in terms if t in haystack) / len(terms)


def search(
    text: str,
    limit: int = 10,
    entity_type: Optional[str] = None,
    conn=None,
) -> list[dict]:
    """Free-text lookup: every entity whose text matches, each one RESOLVED.

    The shape is :func:`resolve`'s, once per matched entity, plus ``match`` (the
    fraction of query terms the entity's own text carries) and
    ``scan_truncated``. Ordered by the same authority the store resolves with —
    authoritative sources first — so a caller that renders the list in order is
    already showing curated evidence above a feed's.

    UNLIKE :func:`query`, THIS RAISES. ``query`` logs and returns ``[]`` on a DB
    failure, which makes a dead table indistinguishable from an entity nobody
    has heard of. The Cortex ``currency`` backend needs that distinction to
    annotate ``BackendResults.errors``, so the exception is the return value
    here and swallowing it would silently defeat the whole reporting contract.
    """
    terms = search_terms(text)
    if not terms:
        return []
    own = conn is None
    if own:
        conn = _connect()
    try:
        clauses = []
        params: list = []
        for term in terms:
            like = f"%{term}%"
            clauses.append(
                "(LOWER(entity_key) LIKE %s OR LOWER(entity_label) LIKE %s "
                "OR LOWER(namespace) LIKE %s)"
            )
            params.extend([like, like, like])
        sql = (
            f"SELECT * FROM {TABLE} WHERE ({' OR '.join(clauses)})"  # nosec B608 - constant identifier; every value bound
        )
        if entity_type:
            sql += " AND entity_type = %s"
            params.append(normalize_key(entity_type))
        # Deterministic order so the cap always truncates the same tail rather
        # than a different one per query plan.
        sql += " ORDER BY entity_type, namespace, entity_key, entity_version, source"
        sql += " LIMIT %s"
        params.append(SEARCH_ROW_CAP + 1)
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        if own:
            conn.close()

    truncated = len(rows) > SEARCH_ROW_CAP
    if truncated:
        rows = rows[:SEARCH_ROW_CAP]
        logger.warning(
            "entity_currency.search: scan capped at %d rows for %r — the result "
            "set is a prefix, not the whole match",
            SEARCH_ROW_CAP, text,
        )

    groups: dict = {}
    for row in rows:
        key = (
            str(row.get("entity_type") or ""), str(row.get("namespace") or ""),
            str(row.get("entity_key") or ""), str(row.get("entity_version") or ""),
        )
        groups.setdefault(key, []).append(row)

    spec_by_id, order = _policy()
    views = []
    for key, group in groups.items():
        view = _resolution_view(group, spec_by_id, order)
        haystack = " ".join(str(v or "").casefold() for v in (
            key[0], key[1], key[2], key[3], view.get("entity_label"),
        ))
        view["match"] = match_score(terms, haystack)
        view["scan_truncated"] = truncated
        views.append(view)

    # Least significant key first (stable sorts compose into a lexicographic
    # multi-key sort), so as_of stays a plain descending string sort instead of
    # being folded into a negated composite — same rule as _sort_by_policy.
    views.sort(key=lambda v: _iso(v.get("as_of")), reverse=True)
    views.sort(key=lambda v: (
        0 if v.get("authoritative") else 1,
        -float(v.get("match") or 0.0),
        -float(v.get("confidence") or 0.0),
    ))
    return views[:max(1, int(limit or 1))]


def stats(conn=None) -> dict:
    """Per-source counts, verdict mix and freshness — the substrate view.

    A source declared in the YAML that has written NOTHING is reported with
    ``rows: 0`` rather than omitted: a provider that never wrote is the exact
    finding this platform's substrate probe exists to surface, and dropping it
    from the report would hide it.
    """
    own = conn is None
    if own:
        conn = _connect()
    try:
        try:
            rows = [dict(r) for r in conn.execute(
                f"SELECT source, verdict, observed_at FROM {TABLE}"  # nosec B608 - constant
            ).fetchall()]
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return {"table_available": False, "reason": str(exc), "total": 0}
        by_source: dict = {}
        by_verdict: dict = {}
        for r in rows:
            s = by_source.setdefault(str(r.get("source")), {"rows": 0, "last_observed": ""})
            s["rows"] += 1
            obs = _iso(r.get("observed_at"))
            if obs > s["last_observed"]:
                s["last_observed"] = obs
            v = str(r.get("verdict"))
            by_verdict[v] = by_verdict.get(v, 0) + 1
        for spec in declared_sources(enabled_only=False):
            by_source.setdefault(str(spec["id"]), {"rows": 0, "last_observed": ""})
        return {
            "table_available": True,
            "total": len(rows),
            "by_source": by_source,
            "by_verdict": by_verdict,
            "declared_sources": [str(s["id"]) for s in declared_sources(enabled_only=False)],
        }
    finally:
        if own:
            conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Domain-agnostic entity-currency store")
    parser.add_argument("--backfill", action="store_true",
                        help="Re-derive the store from every declared source")
    parser.add_argument("--source", action="append", dest="sources",
                        help="Limit --backfill to this source id (repeatable)")
    parser.add_argument("--stats", action="store_true", help="Per-source rows and verdict mix")
    parser.add_argument("--resolve", metavar="ENTITY", help="Resolve one entity")
    parser.add_argument("--entity-type", help="Narrow --resolve to one entity_type")
    parser.add_argument("--namespace", help="Narrow --resolve to one namespace")
    parser.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.backfill:
        out: Any = backfill(sources=args.sources)
    elif args.resolve:
        out = resolve(args.resolve, entity_type=args.entity_type, namespace=args.namespace)
        if out is None:
            out = {"entity_key": normalize_key(args.resolve), "known": False}
    else:
        out = stats()

    if args.json_out or args.resolve:
        print(json.dumps(out, indent=2, default=str))
    elif args.backfill:
        print(f"written: {out['written']}")
        for sid, s in sorted(out["sources"].items()):
            detail = s.get("error") or f"read {s.get('read')} -> wrote {s.get('written')}"
            print(f"  {sid:<26} {detail}")
    else:
        if not out.get("table_available"):
            print(f"entity_currency unavailable: {out.get('reason')}")
            return 0
        print(f"total: {out['total']}")
        for sid, s in sorted(out.get("by_source", {}).items()):
            print(f"  {sid:<26} {s['rows']:>6} rows   last {s['last_observed'] or 'never'}")
        for verdict, n in sorted(out.get("by_verdict", {}).items()):
            print(f"  verdict {verdict:<24} {n:>6}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
