# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Looks a user up by name with a bound parameter. SAFE look-alike: the table name is formatted in."""
import sqlite3

TABLE = "users"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE {TABLE} (name TEXT)")
    conn.executemany(f"INSERT INTO {TABLE} VALUES (?)", [("alice",), ("bob",)])
    return conn


def find_user(name):
    conn = _db()
    return conn.execute(f"SELECT name FROM {TABLE} WHERE name = ?", (name,)).fetchall()
