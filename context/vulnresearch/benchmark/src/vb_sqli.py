# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Looks a user up by name. PLANTED: SQL injection (CWE-89)."""
import sqlite3


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (name TEXT)")
    conn.executemany("INSERT INTO users VALUES (?)", [("alice",), ("bob",)])
    return conn


def find_user(name):
    conn = _db()
    return conn.execute(f"SELECT name FROM users WHERE name = '{name}'").fetchall()
