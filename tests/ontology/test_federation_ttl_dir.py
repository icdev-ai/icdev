# CUI // SP-CTI
"""tools.ontology.federation: build_federation loads the OWNING PARENT's ontology directory, and only that when asked.

Red-first: at the merge base ``build_federation`` has no ``ttl_dir`` /
``include_builtin`` parameters and ``_ttl_dir`` does not exist.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.ontology import federation

TTL = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix core: <https://icdev.dev/ns/core#> .
@prefix fin: <https://icdev.dev/ns/fin#> .

fin:Instrument rdf:type owl:Class ;
    rdfs:label "Instrument" ;
    rdfs:subClassOf core:Entity .

fin:Equity rdf:type owl:Class ;
    rdfs:label "Equity" ;
    rdfs:subClassOf fin:Instrument .
"""


@pytest.fixture
def parent(tmp_path, monkeypatch):
    """A second parent: its own icdev_domain.yaml, args/ontology, and database."""
    root = tmp_path / "other_parent"
    (root / "args" / "ontology").mkdir(parents=True)
    (root / "icdev_domain.yaml").write_text("domain:\n  key: fin\n", encoding="utf-8")
    (root / "args" / "ontology" / "finance.ttl").write_text(TTL, encoding="utf-8")
    monkeypatch.setenv("ICDEV_PROJECT_ROOT", str(root))
    return root


def _classes(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT id, domain FROM ontology_classes").fetchall())
    finally:
        conn.close()


def test_ttl_dir_follows_the_project_root(parent):
    assert federation._ttl_dir() == parent / "args" / "ontology"


def test_ttl_dir_is_this_checkout_without_a_declared_root(monkeypatch):
    monkeypatch.delenv("ICDEV_PROJECT_ROOT", raising=False)
    here = Path(federation.__file__).resolve().parents[2] / "args" / "ontology"
    assert federation._ttl_dir() == here


def test_only_the_parents_ttl_classes_without_builtin(parent, tmp_path):
    db = tmp_path / "fin.db"
    res = federation.build_federation(db_path=str(db), include_builtin=False)
    assert res["status"] == "ok"
    assert res["builtin_included"] is False
    assert res["ttl_domains_loaded"] == ["finance"]
    assert Path(res["ttl_dir"]) == parent / "args" / "ontology"
    classes = _classes(db)
    assert set(classes) == {"fin:Instrument", "fin:Equity"}
    # the IT vocabulary hard-coded in the module did NOT come along
    assert not any(c.startswith(("network:", "compliance:", "security:")) for c in classes)
    conn = sqlite3.connect(db)
    try:
        closure = set(conn.execute("SELECT subclass, superclass FROM ontology_subclass_closure").fetchall())
        assert conn.execute("SELECT COUNT(*) FROM ontology_properties").fetchone()[0] == 0
    finally:
        conn.close()
    # transitive: Equity -> Instrument -> core:Entity
    assert ("fin:Equity", "fin:Instrument") in closure
    assert ("fin:Equity", "core:Entity") in closure


def test_builtin_stays_the_default(parent, tmp_path):
    db = tmp_path / "it.db"
    res = federation.build_federation(db_path=str(db))
    assert res["builtin_included"] is True
    classes = _classes(db)
    assert "fin:Instrument" in classes
    assert any(c.startswith("network:") for c in classes), "the IT domain ontologies are loaded by default"


def test_explicit_ttl_dir_beats_the_root(parent, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "tiny.ttl").write_text(TTL.replace("fin:Equity", "fin:Future").replace('"Equity"', '"Future"'), encoding="utf-8")
    db = tmp_path / "x.db"
    res = federation.build_federation(db_path=str(db), ttl_dir=other, include_builtin=False)
    assert res["ttl_domains_loaded"] == ["tiny"]
    assert set(_classes(db)) == {"fin:Instrument", "fin:Future"}


def test_the_trees_own_ttl_hierarchies_are_parsed():
    """74 rdfs:subClassOf statements live in args/ontology/*.ttl; the parser used to
    recover 4. Every file lays a statement out as a subject at column 0 followed
    by indented predicate lines, and the same-line regex never saw that form."""
    ttl_dir = Path(federation.__file__).resolve().parents[2] / "args" / "ontology"
    declared = sum(p.read_text(encoding="utf-8").count("rdfs:subClassOf") for p in ttl_dir.glob("*.ttl"))
    parsed = 0
    for p in sorted(ttl_dir.glob("*.ttl")):
        classes, _ = federation._parse_ttl_file(p)
        parsed += sum(len(c["superclasses"]) for c in classes)
    assert declared >= 70, declared
    # every declared edge whose subject is a declared class is recovered
    assert parsed >= declared - 4, (parsed, declared)
    # and a concrete one, label included
    classes, _ = federation._parse_ttl_file(ttl_dir / "strategy.ttl")
    by_id = {c["id"]: c for c in classes}
    assert by_id["strategy:WarCouncilBrief"]["superclasses"] == ["core:Concept"]
    assert by_id["strategy:WarCouncilBrief"]["label"] == "War Council Brief"
