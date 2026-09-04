"""Rollback — drop the first-verdict columns from ungated_test_baseline.

SQLite before 3.35 cannot DROP COLUMN; there the columns are left in place and
the rollback is a no-op, which is safe because nothing reads them once
`born_red_survey` is gone.
"""
# CUI // SP-CTI


def down(conn):
    for col in ("first_status", "ever_passed"):
        try:
            conn.execute(f"ALTER TABLE ungated_test_baseline DROP COLUMN {col}")
        except Exception:  # noqa: BLE001 — older SQLite cannot drop a column
            pass
    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass
