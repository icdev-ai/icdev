# CUI // SP-CTI
"""Migration 080 rollback — drop wf report ingestion tables."""
MIGRATION_ID = "080"
MIGRATION_NAME = "wf_report_ingestion"


def down(conn) -> None:
    for tbl in (
        "wf_report_section_chunks",
        "wf_generated_reports",
        "wf_report_section_defs",
        "wf_ingested_files",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    try:
        conn.commit()
    except Exception:
        pass
