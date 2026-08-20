"""Migration — record the FIRST verdict, not only the latest, per ungated file.

`ungated_test_baseline` holds one row per file, updated in place, because the
`ungated_test_drift` reflex only ever asked "what was it last time". That is
enough to spot a `pass -> fail` transition and structurally blind to a file
whose FIRST observation was already a failure: the reflex takes its
`was is None` branch, seeds a 'fail' baseline, and never mentions it again.

Two columns close that, and they answer different questions:

  first_status  the verdict at the first observation. 'fail' here plus 'fail'
                now is the born-red shape.
  ever_passed   a LATCH. Once a file has been observed passing it can never be
                born-red again, whatever it does afterwards. Kept apart from
                `first_status` because a file can be seeded 'fail', recover,
                and break again — that is a regression, and the drift reflex
                already reports it.

BACKFILL, AND WHY IT IS EXACT RATHER THAN ASSUMED
--------------------------------------------------
Existing rows have no recorded history, so inventing one would manufacture
findings. There is exactly one population whose history IS derivable: a row
whose `first_seen` equals its `last_checked` has been observed precisely ONCE,
so its current status IS its first status. Measured on the live board
2026-08-20, that is all 360 rows — the reflex samples 40 never-checked files a
run and has not yet come back round to any of them.

Every other row is left NULL, which `born_red_survey` reports as
`history_unknown` and never as either born-red or regressed. A NULL that reads
as "we cannot say" is the point; a NULL defaulted to 0/'fail' would file cards
for files nobody has evidence about.
"""
# CUI // SP-CTI


def _columns(conn, table: str) -> set:
    """Live column names for *table*, on either backend.

    Reads the catalogue rather than trusting the DDL: `CREATE TABLE IF NOT
    EXISTS` never alters an existing table, so the shipped DDL and the deployed
    shape drift apart (CLAUDE.md, INSERT/schema parity).
    """
    backend = getattr(conn, "_backend", None) or ""
    if "postgres" in str(backend).lower():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        return {dict(r)["column_name"] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    out = set()
    for r in rows:
        d = dict(r)
        out.add(d.get("name") or list(d.values())[1])
    return out


def up(conn):
    existing = _columns(conn, "ungated_test_baseline")
    if not existing:
        # The reflex's own migration has not run here. Nothing to widen, and
        # creating the table from this migration would fork the DDL.
        return

    if "first_status" not in existing:
        conn.execute("ALTER TABLE ungated_test_baseline ADD COLUMN first_status TEXT")
    if "ever_passed" not in existing:
        conn.execute(
            "ALTER TABLE ungated_test_baseline ADD COLUMN ever_passed INTEGER"
        )

    # Only the provably-single-observation rows. `first_seen = last_checked`
    # means the row has been written once and never revisited, so its current
    # verdict is also its first one.
    conn.execute(
        "UPDATE ungated_test_baseline SET first_status = status "
        "WHERE first_status IS NULL "
        "AND first_seen IS NOT NULL AND last_checked IS NOT NULL "
        "AND first_seen = last_checked"
    )
    conn.execute(
        "UPDATE ungated_test_baseline SET ever_passed = 1 "
        "WHERE ever_passed IS NULL AND status = 'pass'"
    )
    conn.execute(
        "UPDATE ungated_test_baseline SET ever_passed = 0 "
        "WHERE ever_passed IS NULL AND first_status = 'fail' AND status = 'fail'"
    )
    conn.commit()
