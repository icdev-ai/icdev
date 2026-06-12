#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 023 rollback: drop ad_news_patterns table and its indexes."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP INDEX IF EXISTS idx_ad_news_patterns_sev_date")
        conn.execute("DROP INDEX IF EXISTS idx_ad_news_patterns_cat_date")
        conn.execute("DROP TABLE IF EXISTS ad_news_patterns")
        conn.commit()
        print("[023_ad_news_patterns] down: ad_news_patterns dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
