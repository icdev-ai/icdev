# CUI // SP-CTI
"""Migration 127 rollback — drop dd_freshness_runs."""


def down(conn):
    conn.execute("DROP TABLE IF EXISTS dd_freshness_runs")
    conn.commit()
