# CUI // SP-CTI
"""Persist discovered devices into ``ni_devices`` (rmf-disc-01).

THE COLUMN SET IS READ FROM THE LIVE SCHEMA, NEVER FROM THE DDL.

``ni_devices`` is created by ``CREATE TABLE IF NOT EXISTS`` in
``tools/network/db/init_db.py`` and then ALTERed by migrations, so the two have
diverged: measured 2026-09-02 the live PostgreSQL table carries ``source``,
``rack``, ``criticality``, ``maintenance_contract``, ``contract_expiry`` and
``classification``, none of which are in that DDL — while the SQLite fallback,
built from the DDL alone, has none of them. An INSERT naming a column the live
table lacks raises, is swallowed by a surrounding handler, and the feature
reports success while persisting nothing. That is exactly how
``module_budget_usage`` held 0 rows. So this writes the INTERSECTION of what it
has and what the table has, and reports which canonical fields it could not
place.

PROVENANCE RIDES IN ``properties_json`` AS WELL AS IN ``source``.

``properties_json`` exists in BOTH shapes; ``source`` exists only on the
migrated one. A row whose origin is unrecoverable cannot be told apart from the
24 ``source='synthetic'`` rows already on the live board — and the de-facto
standard learner reads this table as ``inventory``, i.e. as an OBSERVED estate.
Synthetic rows and discovered rows must never be one population.

UPSERT, NOT INSERT. The primary key is ``DiscoveredDevice.stable_id()``, a hash
of (fabric, adapter, node_id), so a second sweep updates the row the first one
wrote. A discovery loop that inserted would report a larger estate every hour,
which from the table is indistinguishable from an estate that is growing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from tools.assets.discovery_adapters.base import DiscoveredDevice, utcnow
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.assets.discovery_adapters.sink")

TABLE = "ni_devices"

#: canonical DiscoveredDevice attribute -> ni_devices column, in preference
#: order. The first column of a group that EXISTS in the live table wins; a
#: canonical field with no surviving column is reported, never dropped silently.
COLUMN_MAP: dict[str, tuple[str, ...]] = {
    "label": ("label",),
    "device_type": ("device_type",),
    "vendor": ("vendor",),
    "model": ("model",),
    "firmware_version": ("firmware_version",),
    "site": ("site",),
    "rack": ("rack_location", "rack"),
}


@dataclass
class SinkReport:
    """What a persist actually did. Every number here is a count, never a rate."""

    inserted: int = 0
    updated: int = 0
    #: Canonical fields that had no column in the live table.
    unplaced_fields: list[str] = field(default_factory=list)
    #: Columns written, in the order they appear in the statement.
    columns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def written(self) -> int:
        return self.inserted + self.updated

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "written": self.written,
            "unplaced_fields": list(self.unplaced_fields),
            "columns": list(self.columns),
            "errors": list(self.errors),
        }


def table_columns(conn, table: str = TABLE) -> set[str]:
    """Columns of ``table`` AS THE DATABASE HAS THEM RIGHT NOW.

    Tries ``information_schema`` (PostgreSQL) then ``PRAGMA table_info``
    (SQLite). Returns an empty set when the table does not exist — the caller
    treats that as "cannot write", never as "no columns to write".

    Placeholders are PostgreSQL-native ``%s`` throughout this module, per
    ``coherence_checker --check runtime_placeholder_style``: PostgreSQL is the
    primary backend, and ``translate_sql``'s ``?`` rewrite is an init-path
    fallback that runtime SQL may not lean on. ``StorageConnection`` rewrites
    the other way on the SQLite path, which is what the tests exercise.
    """
    try:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s",
            (table,),
        )
        cols = {str(r["column_name"]) for r in cur.fetchall()}
        if cols:
            return cols
    except Exception:  # noqa: BLE001 — not PostgreSQL, or no such view
        pass
    try:
        cur = conn.execute("PRAGMA table_info(%s)" % table)
        return {str(r["name"]) for r in cur.fetchall()}
    except Exception:  # noqa: BLE001
        return set()


def _existing_ids(conn, ids: Sequence[str]) -> set[str]:
    if not ids:
        return set()
    found: set[str] = set()
    # Chunked so a large sweep does not build a statement with 10k markers.
    for start in range(0, len(ids), 200):
        chunk = list(ids[start : start + 200])
        markers = ", ".join(["%s"] * len(chunk))
        cur = conn.execute(
            "SELECT id FROM %s WHERE id IN (%s)" % (TABLE, markers), tuple(chunk)
        )
        found.update(str(r["id"]) for r in cur.fetchall())
    return found


def persist(
    devices: Iterable[DiscoveredDevice],
    conn=None,
    topology_id: str | None = None,
) -> SinkReport:
    """Upsert ``devices`` into ni_devices. Returns what it did.

    ``conn`` defaults to ``tools.network.db.init_db.get_connection()`` — the
    canvas connection, which on PostgreSQL is the shared ``icdev`` database
    with RLS stood down (canvas tables carry no ``tenant_id``/``classification``
    predicate columns). That is the same connection
    ``doc_modernization/defacto_learner`` reads through, so a row written here
    is a row that feed can see.
    """
    device_list = list(devices)
    report = SinkReport()
    if not device_list:
        return report

    owns_conn = conn is None
    if conn is None:
        from tools.network.db.init_db import get_connection

        conn = get_connection()

    try:
        available = table_columns(conn)
        if not available:
            report.errors.append(
                "%s does not exist (or its columns could not be read) — "
                "nothing written" % TABLE
            )
            return report

        # id / node_id are the two the table declares NOT NULL. Without them
        # there is no row to write, and pretending otherwise would be the
        # swallowed-INSERT failure this module documents.
        required = {"id", "node_id"}
        if not required.issubset(available):
            report.errors.append(
                "%s is missing required column(s): %s"
                % (TABLE, ", ".join(sorted(required - available)))
            )
            return report

        placements: list[tuple[str, str]] = []  # (attribute, column)
        for attr, candidates in COLUMN_MAP.items():
            column = next((c for c in candidates if c in available), "")
            if column:
                placements.append((attr, column))
            else:
                report.unplaced_fields.append(attr)

        has_properties = "properties_json" in available
        has_source = "source" in available
        has_topology = "topology_id" in available
        has_updated = "updated_at" in available
        has_created = "created_at" in available

        columns = ["id", "node_id"] + [c for _, c in placements]
        if has_properties:
            columns.append("properties_json")
        if has_source:
            columns.append("source")
        if has_topology:
            columns.append("topology_id")
        if has_created:
            columns.append("created_at")
        if has_updated:
            columns.append("updated_at")
        report.columns = list(columns)

        ids = [d.stable_id() for d in device_list]
        already = _existing_ids(conn, ids)

        update_cols = [c for c in columns if c not in ("id", "created_at")]
        insert_sql = "INSERT INTO %s (%s) VALUES (%s)" % (
            TABLE,
            ", ".join(columns),
            ", ".join(["%s"] * len(columns)),
        )
        update_sql = "UPDATE %s SET %s WHERE id = %%s" % (
            TABLE,
            ", ".join("%s = %%s" % c for c in update_cols),
        )

        for device in device_list:
            row: dict[str, Any] = {
                "id": device.stable_id(),
                "node_id": str(device.node_id),
            }
            for attr, column in placements:
                row[column] = getattr(device, attr, "") or ""
            if has_properties:
                row["properties_json"] = device.properties_json()
            if has_source:
                # The EVIDENCE CLASS, in rmf-disc-02's vocabulary — `csv`,
                # `netbox`, `discovery`, `topology_ingest` — because
                # `defacto_learner`'s `exclude_when` is keyed on these exact
                # values and a label of our own invention would route a lab
                # import into the platform's strongest claim about fielded
                # hardware. WHICH adapter and WHICH fabric is in
                # properties_json; the column carries the class, not the source
                # instance.
                #
                # NULL when unknown, never a guess: an unlabelled row reads
                # exactly as it did before, which is better than a wrong label.
                row["source"] = device.source_label or None
            if has_topology:
                row["topology_id"] = topology_id
            now = utcnow()
            if has_created:
                row["created_at"] = now
            if has_updated:
                row["updated_at"] = now

            try:
                if row["id"] in already:
                    conn.execute(
                        update_sql,
                        tuple(row[c] for c in update_cols) + (row["id"],),
                    )
                    report.updated += 1
                else:
                    conn.execute(insert_sql, tuple(row[c] for c in columns))
                    report.inserted += 1
                    already.add(row["id"])
            except Exception as exc:  # noqa: BLE001
                # Recorded, never swallowed: a write that failed must not be
                # reportable as a device that was persisted.
                report.errors.append(
                    "%s (%s): %s" % (device.node_id, device.adapter, exc)
                )
                logger.warning(
                    "ni_devices write failed for %s: %s", device.node_id, exc
                )
        conn.commit()
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return report
