# CUI // SP-CTI
"""sbx-fld-04 — the Component License upsert, on the AMBIENT PostgreSQL backend.

`tests/test_sbom_component_license.py` covers the element itself, but its
generator fixture forces SQLite (raw `sqlite3` schema, `monkeypatch.setenv`), so
nothing there exercises the dialect that actually ships — the upsert in
`_persist_components`, whose conflict arm assigns from `EXCLUDED`.

PostgreSQL is the primary backend, and `_persist_components` is a brand-new write
path on a table the generator has never touched — a PG-only failure there would be
swallowed into a stderr warning and reported as zero rows written while the SQLite
suite stayed green. That is exactly the class of bug this tier exists to catch, so
the upsert gets its own file with no sqlite-forcing fixture in it.

It has already earned its keep once: it is what found that `sbom_components` does
not exist at all on a freshly bootstrapped PostgreSQL, which
`20260808043009_restore_migration_209_tables_on_postgresql` repairs.

Skips outside the tier so it never fires in the normal SQLite suite.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)

_PROJECT = {"id": "sbx-fld-04-pg", "name": "PG probe", "directory_path": ""}


def _cyclonedx(component):
    """Run one parsed component through the real document builder.

    `_persist_components` writes rows FROM the finished document, so handing it a
    parsed dict would test a shape that never occurs in production.
    """
    from tools.compliance.sbom_generator import _build_cyclonedx_sbom

    sbom, _ = _build_cyclonedx_sbom(_PROJECT, [component])
    return sbom["components"]


@pytest.fixture()
def probe():
    """A component that exists nowhere else, removed again however the test ends."""
    from tools.db.storage import get_connection

    component = {
        "type": "library",
        "name": f"sbx-fld-04-probe-{uuid.uuid4().hex[:12]}",
        "version": "0.0.0",
        "group": "",
        "purl": "pkg:pypi/sbx-fld-04-probe@0.0.0",
        "declared_license": "MIT",
    }
    row_id = _cyclonedx(component)[0]["bom-ref"]
    try:
        yield component, row_id
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM sbom_components WHERE id = %s", (row_id,))
        conn.commit()
        conn.close()


def test_the_component_upsert_inserts_then_updates_on_postgresql(probe):
    """Insert, upsert the same component again, read the changed license back."""
    from tools.compliance.sbom_generator import _persist_components
    from tools.db.storage import get_connection

    component, row_id = probe
    conn = get_connection()
    try:
        assert _persist_components(conn, _cyclonedx(component)) == 1
        row = conn.execute(
            "SELECT license, component_name FROM sbom_components WHERE id = %s", (row_id,)
        ).fetchone()
        assert row is not None, (
            "_persist_components reported a write but no row landed — on PG a failure "
            "there is a stderr warning, not an exception"
        )
        assert row["license"] == "MIT"

        # Second run over the same component must take the ON CONFLICT arm: one row,
        # updated license, no duplicate-key error. The identity fields are unchanged,
        # so the bom-ref — and therefore the row id — is the same.
        component["declared_license"] = "(MIT OR Apache-2.0)"
        assert _persist_components(conn, _cyclonedx(component)) == 1

        rows = conn.execute(
            "SELECT license FROM sbom_components WHERE id = %s", (row_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["license"] == "MIT OR Apache-2.0"
    finally:
        conn.close()


def test_an_unknown_license_persists_the_explicit_marker_on_postgresql(probe):
    """The column may never be left NULL — that is the acceptance criterion."""
    from tools.compliance.component_licenser import UNKNOWN
    from tools.compliance.sbom_generator import _persist_components
    from tools.db.storage import get_connection

    component, row_id = probe
    component.pop("declared_license")

    conn = get_connection()
    try:
        assert _persist_components(conn, _cyclonedx(component)) == 1
        row = conn.execute(
            "SELECT license, unknown_fields_json FROM sbom_components WHERE id = %s", (row_id,)
        ).fetchone()
        assert row["license"] == UNKNOWN
        # The reason travels with it — `license = 'unknown'` alone says nothing
        # about why, and these are the columns sbx-fnd-02 created for that.
        assert "license" in row["unknown_fields_json"]
    finally:
        conn.close()
