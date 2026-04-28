# CUI // SP-CTI
"""Migration 056 — Create historical_cases table for Intent Assessment Lens.

Stores PMESII-PT vectorized historical conflict cases used by the
intent_assessment lens to compute cosine-similarity precedent matching.

Columns
-------
id              TEXT PRIMARY KEY
case_name       TEXT — human-readable conflict name (e.g. "Gulf War 1991")
pmesii_vector   TEXT — JSON array of 7 floats [P, M, E, S, I, I2, PT] 0-1 scale
outcome         TEXT — brief historical outcome summary
escalation_level INTEGER — 1 (low) to 5 (existential/WMD)
region          TEXT — geographic region
year_start      INTEGER — conflict start year
year_end        INTEGER — conflict end year (NULL if ongoing)
outcome_severity_weight REAL — derived from escalation_level; 0.2-1.0
"""
from tools.db.storage import get_connection

MIGRATION_ID = "056"
MIGRATION_NAME = "historical_cases"
DESCRIPTION = "Create historical_cases table for PMESII-PT intent precedent matching"

_DDL = """
CREATE TABLE IF NOT EXISTS historical_cases (
    id                      TEXT PRIMARY KEY,
    case_name               TEXT NOT NULL UNIQUE,
    pmesii_vector           TEXT NOT NULL,
    outcome                 TEXT NOT NULL,
    escalation_level        INTEGER NOT NULL DEFAULT 3,
    region                  TEXT,
    year_start              INTEGER,
    year_end                INTEGER,
    outcome_severity_weight REAL NOT NULL DEFAULT 0.6
);
"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_hc_escalation ON historical_cases(escalation_level)",
    "CREATE INDEX IF NOT EXISTS idx_hc_region      ON historical_cases(region)",
    "CREATE INDEX IF NOT EXISTS idx_hc_year        ON historical_cases(year_start)",
]


def up(conn=None) -> None:
    conn = get_connection()
    conn.execute(_DDL)
    for idx in _INDICES:
        try:
            conn.execute(idx)
        except Exception:
            pass
    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 056 applied.")
