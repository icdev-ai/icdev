"""Scratch probe: does the leak guard actually report the opening stack?"""
import sqlite3


def test_leaks_a_write_transaction(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "probe.db"))
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    assert conn.in_transaction
