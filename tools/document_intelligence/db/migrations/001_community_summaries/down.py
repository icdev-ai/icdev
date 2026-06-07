#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 001 rollback: drop dic_community_summaries table and indexes.

Does not affect existing KG nodes/edges.
"""

import sqlite3


def down(conn) -> dict:
    conn.execute("DROP INDEX IF EXISTS idx_dic_community_summaries_community")
    conn.execute("DROP INDEX IF EXISTS idx_dic_community_summaries_tenant")
    conn.execute("DROP TABLE IF EXISTS dic_community_summaries")
    conn.commit()
    return {"status": "rolled_back", "dropped": ["dic_community_summaries"]}
