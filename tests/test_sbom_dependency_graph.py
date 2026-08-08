#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-cov-02 — the SBOM 2026 **Component Dependency Relationship** element.

Before this task the ICDEV SBOM was a flat component list: no CycloneDX
``dependencies`` array at all, so no graph could be reconstructed from ICDEV
output. The element was absent, not partial.

What is pinned here:

* the emitted document carries a ``dependencies`` array rooted at the target
  component, and every ``ref`` / ``dependsOn`` in it resolves to a real bom-ref;
* the graph is the tree sbx-cov-01 resolved — the npm fixture's nested ``gamma``
  hangs off ``alpha`` and the hoisted one off ``beta``, which is the whole point
  of keeping nested instances;
* two instances that share metadata but resolve DIFFERENT dependencies are two
  components with two sets of relationships, and two that agree on both are one;
* cycles are found and declared rather than hung on;
* a declared-only component gets no entry (its edges are unknown) while a
  resolved leaf gets an empty ``dependsOn`` (it is known to have none);
* the relationship vocabulary the graph emits is the same vocabulary the
  ``sbom_dependencies.relationship_type`` CHECK enforces, proven against a
  database the migration was actually applied to;
* the same edges render as SPDX ``RELATIONSHIP`` entries, so sbx-fmt-01 inherits
  a graph rather than re-deriving one.

The validator is exercised in both directions: it scores a real generated SBOM
as met, and it rejects each defect this task exists to prevent — including the
pre-sbx-cov-02 document shape.
"""

import importlib.util
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance import dependency_graph as dg  # noqa: E402

MIGRATION_VERSION = "20260808045015"

ROOT_COPY = BASE_DIR / "tools" / "compliance" / "dependency_graph.py"
MIRROR_COPY = BASE_DIR / "icdev" / "tools" / "compliance" / "dependency_graph.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _instance(key, name, version, dependencies=(), resolution="resolved", scope="required"):
    """A resolver-shape component instance."""
    return {
        "type": "library",
        "name": name,
        "version": version,
        "purl": f"pkg:npm/{name}@{version}",
        "group": "",
        "scope": scope,
        "source": "package-lock.json",
        "ecosystem": "npm",
        "key": key,
        "dependencies": list(dependencies),
        "resolution": resolution,
        "direct": True,
    }


def _entries(graph):
    """``{ref: sorted dependsOn}`` for the rendered CycloneDX array."""
    return {
        entry["ref"]: entry["dependsOn"] for entry in dg.to_cyclonedx_dependencies(graph)
    }


def _ref_of(graph, name, version):
    matches = [
        node
        for node in graph["nodes"]
        if node["instance"]["name"] == name and node["instance"]["version"] == version
    ]
    assert len(matches) == 1, f"expected one {name}@{version} node, got {len(matches)}"
    return matches[0]["ref"]


# ---------------------------------------------------------------------------
# graph construction
# ---------------------------------------------------------------------------


def test_graph_reconstructs_the_resolved_npm_tree():
    """alpha -> (beta, nested gamma@1.5); beta -> hoisted gamma@3.0.

    This is the sbx-cov-01 fixture's real resolution order. A graph that pointed
    alpha at gamma@3.0 would be describing software that was never installed.
    """
    components = [
        _instance(
            "npm|node_modules/alpha",
            "alpha",
            "1.0.0",
            ["npm|node_modules/beta", "npm|node_modules/alpha/node_modules/gamma"],
        ),
        _instance("npm|node_modules/beta", "beta", "2.0.0", ["npm|node_modules/gamma"]),
        _instance("npm|node_modules/alpha/node_modules/gamma", "gamma", "1.5.0"),
        _instance("npm|node_modules/gamma", "gamma", "3.0.0"),
    ]

    graph = dg.build_dependency_graph(components, "icdev-fixture")
    entries = _entries(graph)

    alpha = _ref_of(graph, "alpha", "1.0.0")
    beta = _ref_of(graph, "beta", "2.0.0")
    gamma_nested = _ref_of(graph, "gamma", "1.5.0")
    gamma_hoisted = _ref_of(graph, "gamma", "3.0.0")

    assert entries["icdev-fixture"] == [alpha], "only alpha has no dependent inside the graph"
    assert sorted(entries[alpha]) == sorted([beta, gamma_nested])
    assert entries[beta] == [gamma_hoisted]
    assert entries[gamma_nested] == []
    assert entries[gamma_hoisted] == []


def test_duplicate_instances_with_different_relationships_stay_separate():
    """Same name, same version, different resolved dependencies -> two nodes.

    Collapsing these would have to either invent an edge to x@2.0.0 from the
    instance that resolves x@1.0.0, or drop one. Both are wrong.
    """
    components = [
        _instance("npm|node_modules/a/node_modules/lodash", "lodash", "4.0.0", ["npm|x1"]),
        _instance("npm|node_modules/b/node_modules/lodash", "lodash", "4.0.0", ["npm|x2"]),
        _instance("npm|x1", "x", "1.0.0"),
        _instance("npm|x2", "x", "2.0.0"),
    ]

    graph = dg.build_dependency_graph(components, "root")
    lodash_nodes = [node for node in graph["nodes"] if node["instance"]["name"] == "lodash"]
    assert len(lodash_nodes) == 2, "two instances with differing relationships collapsed into one"

    refs = {node["ref"] for node in lodash_nodes}
    assert len(refs) == 2, "the two instances share a bom-ref"

    entries = _entries(graph)
    depends = sorted(tuple(entries[ref]) for ref in refs)
    assert depends == sorted(
        [(_ref_of(graph, "x", "1.0.0"),), (_ref_of(graph, "x", "2.0.0"),)]
    ), "each instance must carry its own relationships"


def test_instances_that_agree_on_metadata_and_edges_collapse():
    """Indistinguishable instances are one component — the graph is not a log."""
    components = [
        _instance("npm|node_modules/a/node_modules/lodash", "lodash", "4.0.0", ["npm|x1"]),
        _instance("npm|node_modules/b/node_modules/lodash", "lodash", "4.0.0", ["npm|x1"]),
        _instance("npm|x1", "x", "1.0.0"),
    ]

    graph = dg.build_dependency_graph(components, "root")
    lodash_nodes = [node for node in graph["nodes"] if node["instance"]["name"] == "lodash"]
    assert len(lodash_nodes) == 1
    assert sorted(lodash_nodes[0]["instance_keys"]) == [
        "npm|node_modules/a/node_modules/lodash",
        "npm|node_modules/b/node_modules/lodash",
    ]


def test_a_cycle_is_detected_declared_and_still_reachable():
    """npm permits cycles. The graph must terminate, say so, and stay rooted."""
    components = [
        _instance("npm|a", "a", "1.0.0", ["npm|b"]),
        _instance("npm|b", "b", "1.0.0", ["npm|a"]),
    ]

    graph = dg.build_dependency_graph(components, "root")

    assert len(graph["cycles"]) == 1
    properties = {p["name"]: p["value"] for p in dg.graph_properties(graph)}
    assert properties["icdev:sbom:dependency:cycles"] == "1"
    assert "icdev:sbom:dependency:cycles:detail" in properties

    # Nothing in a closed cycle has in-degree zero, so without the rescue the
    # whole cycle would be unreachable from the target.
    entries = _entries(graph)
    assert len(entries["root"]) == 1, "a cycle needs exactly one entry point, not one per member"
    assert len(graph["unrooted"]) == 1
    reachable = dg._reachable({ref: deps for ref, deps in entries.items()}, "root")
    assert {node["ref"] for node in graph["nodes"]} <= reachable


def test_a_self_edge_is_reported_as_a_cycle():
    graph = dg.build_dependency_graph([_instance("npm|a", "a", "1.0.0", ["npm|a"])], "root")
    assert len(graph["cycles"]) == 1
    # A self-loop does not make the node unreachable — it still has in-degree
    # zero from anything else, so it hangs off the root normally.
    assert graph["unrooted"] == []
    assert _entries(graph)["root"] == [graph["nodes"][0]["ref"]]


def test_declared_only_components_state_unknown_not_empty():
    """Absent entry means unknown; empty dependsOn means known to have none.

    Conflating them would let a declared-manifest fallback — where the
    component's own dependencies were never read — assert that it has none.
    """
    components = [
        _instance("maven|0", "alpha", "1.0.0", resolution="declared"),
        _instance("npm|leaf", "leaf", "2.0.0", resolution="resolved"),
    ]

    graph = dg.build_dependency_graph(components, "root")
    entries = _entries(graph)

    declared_ref = _ref_of(graph, "alpha", "1.0.0")
    resolved_ref = _ref_of(graph, "leaf", "2.0.0")

    assert declared_ref not in entries, "a declared-only component claimed to have no dependencies"
    assert entries[resolved_ref] == [], "a resolved leaf must say it depends on nothing"
    assert graph["unknown_refs"] == [declared_ref]
    # It is still a dependency OF the target — only its own edges are unknown.
    assert declared_ref in entries["root"]


def test_an_edge_to_a_component_that_was_never_emitted_is_dropped():
    """A dangling dependsOn is the exact defect the element's validator fails on."""
    components = [_instance("npm|a", "a", "1.0.0", ["npm|ghost"])]

    graph = dg.build_dependency_graph(components, "root")

    assert graph["dangling_edges"] == 1
    assert _entries(graph)[_ref_of(graph, "a", "1.0.0")] == []


def test_an_empty_project_still_emits_a_rooted_dependencies_array():
    graph = dg.build_dependency_graph([], "root")
    assert dg.to_cyclonedx_dependencies(graph) == [{"ref": "root", "dependsOn": []}]


def test_optional_scope_becomes_an_optional_relationship():
    components = [
        _instance("npm|a", "a", "1.0.0", ["npm|d"]),
        _instance("npm|d", "d", "1.0.0", scope="optional"),
    ]

    graph = dg.build_dependency_graph(components, "root")
    types = {edge["type"] for edge in graph["edges"] if edge["to"] == _ref_of(graph, "d", "1.0.0")}
    assert types == {dg.RELATIONSHIP_OPTIONAL_DEPENDS_ON}


# ---------------------------------------------------------------------------
# SPDX
# ---------------------------------------------------------------------------


def test_spdx_relationships_express_the_same_edges():
    """sbx-fmt-01 must be able to write the identical graph as SPDX."""
    components = [
        _instance("npm|a", "a", "1.0.0", ["npm|b", "npm|d"]),
        _instance("npm|b", "b", "1.0.0"),
        _instance("npm|d", "d", "1.0.0", scope="optional"),
    ]
    graph = dg.build_dependency_graph(components, "root")
    a, b, d = (_ref_of(graph, n, "1.0.0") for n in ("a", "b", "d"))

    relationships = dg.to_spdx_relationships(graph)
    triples = {
        (r["spdxElementId"], r["relationshipType"], r["relatedSpdxElement"])
        for r in relationships
    }

    assert ("SPDXRef-DOCUMENT", "DESCRIBES", "SPDXRef-root") in triples
    assert (f"SPDXRef-{a}", "DEPENDS_ON", f"SPDXRef-{b}") in triples
    # OPTIONAL_DEPENDENCY_OF reads "A is an optional dependency of B", the
    # inverse of our edge direction, so the operands swap.
    assert (f"SPDXRef-{d}", "OPTIONAL_DEPENDENCY_OF", f"SPDXRef-{a}") in triples

    # One relationship per edge, plus the DESCRIBES.
    assert len(relationships) == len(graph["edges"]) + 1


def test_every_relationship_type_has_an_spdx_mapping():
    assert set(dg.SPDX_RELATIONSHIP) == set(dg.RELATIONSHIP_TYPES)


# ---------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------


def _document(components, dependencies, cycles="0", target="icdev-app"):
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "metadata": {
            "component": {"type": "application", "bom-ref": target, "name": "app"},
            "properties": [{"name": "icdev:sbom:dependency:cycles", "value": cycles}],
        },
        "components": components,
        "dependencies": dependencies,
    }


def test_validator_rejects_the_flat_component_list_icdev_used_to_emit():
    document = _document([{"bom-ref": "c1", "name": "alpha"}], [])
    document.pop("dependencies")

    report = dg.validate_dependency_graph(document)

    assert report["status"] == "not_met"
    assert [f["code"] for f in report["findings"]] == ["dependencies_absent"]


def test_validator_rejects_a_dangling_depends_on():
    document = _document(
        [{"bom-ref": "c1", "name": "alpha"}],
        [{"ref": "icdev-app", "dependsOn": ["c1"]}, {"ref": "c1", "dependsOn": ["ghost"]}],
    )

    report = dg.validate_dependency_graph(document)

    assert report["status"] == "not_met"
    assert "unresolved_depends_on" in {f["code"] for f in report["findings"]}


def test_validator_rejects_a_graph_that_is_not_rooted_at_the_target():
    document = _document(
        [{"bom-ref": "c1", "name": "alpha"}],
        [{"ref": "c1", "dependsOn": []}],
    )

    report = dg.validate_dependency_graph(document)

    codes = {f["code"] for f in report["findings"]}
    assert "unrooted_graph" in codes
    assert "unreachable_components" in codes


def test_validator_rejects_an_undeclared_cycle():
    document = _document(
        [{"bom-ref": "c1", "name": "a"}, {"bom-ref": "c2", "name": "b"}],
        [
            {"ref": "icdev-app", "dependsOn": ["c1"]},
            {"ref": "c1", "dependsOn": ["c2"]},
            {"ref": "c2", "dependsOn": ["c1"]},
        ],
        cycles="0",
    )

    report = dg.validate_dependency_graph(document)

    assert report["status"] == "not_met"
    assert "cycle_count_mismatch" in {f["code"] for f in report["findings"]}


def test_validator_rejects_a_document_that_was_never_cycle_checked():
    document = _document(
        [{"bom-ref": "c1", "name": "alpha"}],
        [{"ref": "icdev-app", "dependsOn": ["c1"]}, {"ref": "c1", "dependsOn": []}],
    )
    document["metadata"]["properties"] = []

    report = dg.validate_dependency_graph(document)

    assert "cycles_not_checked" in {f["code"] for f in report["findings"]}


def test_validator_rejects_a_duplicated_ref():
    document = _document(
        [{"bom-ref": "c1", "name": "alpha"}],
        [
            {"ref": "icdev-app", "dependsOn": ["c1"]},
            {"ref": "c1", "dependsOn": []},
            {"ref": "c1", "dependsOn": []},
        ],
    )

    report = dg.validate_dependency_graph(document)

    assert "duplicate_ref" in {f["code"] for f in report["findings"]}


# ---------------------------------------------------------------------------
# end to end through the generator
# ---------------------------------------------------------------------------


def _package_lock_v3():
    """The sbx-cov-01 npm fixture: two dependents needing incompatible ranges."""
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "fixture", "version": "1.0.0"},
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {"beta": "^2.0.0", "gamma": "^1.0.0"},
            },
            "node_modules/alpha/node_modules/gamma": {"version": "1.5.0"},
            "node_modules/beta": {"version": "2.0.0", "dependencies": {"gamma": "^3.0.0"}},
            "node_modules/gamma": {"version": "3.0.0"},
        },
    }


def _seed_project(db_path, project_id, directory):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        (project_id, "Dependency Graph Fixture", "api", str(directory)),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def generated_sbom(icdev_db, tmp_path, monkeypatch):
    # icdev_db is a temp SQLite file, but get_connection only honours a .db path
    # when the process backend is sqlite — under the PG tier this fixture would
    # otherwise generate against the live PostgreSQL database.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    from tools.compliance import sbom_generator

    project = tmp_path / "graph-project"
    project.mkdir()
    (project / "package-lock.json").write_text(
        json.dumps(_package_lock_v3()), encoding="utf-8"
    )
    (project / "package.json").write_text(
        json.dumps({"dependencies": {"alpha": "^1.0.0"}}), encoding="utf-8"
    )
    _seed_project(icdev_db, "dep-graph", project)

    out_file = tmp_path / "graph.cdx.json"
    sbom_generator.generate_sbom(
        project_id="dep-graph", output_path=str(out_file), db_path=icdev_db
    )
    return json.loads(out_file.read_text(encoding="utf-8"))


def test_generated_sbom_carries_a_dependencies_array(generated_sbom):
    assert generated_sbom["dependencies"], "the SBOM is still a flat component list"


def test_every_bom_ref_in_the_generated_graph_resolves(generated_sbom):
    known = {c["bom-ref"] for c in generated_sbom["components"]}
    known.add(generated_sbom["metadata"]["component"]["bom-ref"])

    for entry in generated_sbom["dependencies"]:
        assert entry["ref"] in known, f"dependencies[].ref {entry['ref']} resolves to nothing"
        for target in entry["dependsOn"]:
            assert target in known, f"dependsOn {target} resolves to nothing"


def test_generated_graph_is_rooted_at_the_target_component(generated_sbom):
    target = generated_sbom["metadata"]["component"]["bom-ref"]
    roots = [e for e in generated_sbom["dependencies"] if e["ref"] == target]
    assert len(roots) == 1

    by_ref = {c["bom-ref"]: c for c in generated_sbom["components"]}
    direct = sorted(by_ref[ref]["name"] for ref in roots[0]["dependsOn"])
    assert direct == ["alpha"], "only alpha is a direct dependency of the fixture"


def test_generated_graph_reconstructs_the_cov_01_tree(generated_sbom):
    by_ref = {c["bom-ref"]: c for c in generated_sbom["components"]}
    edges = {
        entry["ref"]: sorted(
            f"{by_ref[t]['name']}@{by_ref[t]['version']}" for t in entry["dependsOn"]
        )
        for entry in generated_sbom["dependencies"]
        if entry["ref"] in by_ref
    }
    named = {f"{by_ref[ref]['name']}@{by_ref[ref]['version']}": deps for ref, deps in edges.items()}

    assert named["alpha@1.0.0"] == ["beta@2.0.0", "gamma@1.5.0"]
    assert named["beta@2.0.0"] == ["gamma@3.0.0"]
    assert named["gamma@1.5.0"] == []
    assert named["gamma@3.0.0"] == []


def test_generated_sbom_declares_its_cycle_check_and_embedding_choice(generated_sbom):
    properties = {p["name"]: p["value"] for p in generated_sbom["metadata"]["properties"]}
    assert properties["icdev:sbom:dependency:cycles"] == "0"
    assert properties["icdev:sbom:dependency:graph"] == "rooted"
    assert properties["icdev:sbom:dependency:embedding"].startswith("embedded:")


def test_the_validator_scores_the_element_as_met(generated_sbom):
    report = dg.validate_dependency_graph(generated_sbom)
    assert report["status"] == "met", report["findings"]
    assert report["element"] == "Component Dependency Relationship"
    assert report["stats"]["edges"] == 4


# ---------------------------------------------------------------------------
# vocabulary <-> database
# ---------------------------------------------------------------------------


PRE_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sbom_components (
    id TEXT PRIMARY KEY,
    component_name TEXT
);
CREATE TABLE IF NOT EXISTS sbom_dependencies (
    id                  TEXT    PRIMARY KEY,
    sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
    parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
    child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
    relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
    scope               TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT,
    created_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
);
"""


@pytest.fixture
def migrated_sqlite_db(tmp_path, monkeypatch):
    """sbx-fnd-02's table shape with the real vocabulary migration applied.

    Nothing here reads the migration's Python as text: a test that asserted on
    the source would pass for a migration the runner never discovers.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    from tools.db.migration_runner import MigrationRunner

    db_path = tmp_path / "sbom_vocabulary.db"
    seed = sqlite3.connect(str(db_path))
    seed.executescript(PRE_MIGRATION_DDL)
    seed.execute("INSERT INTO sbom_records (id, project_id) VALUES (1, 'p')")
    seed.execute("INSERT INTO sbom_components (id) VALUES ('c1'), ('c2')")
    seed.executemany(
        "INSERT INTO sbom_components (id) VALUES (?)",
        [(f"p{index}",) for index in range(len(dg.RELATIONSHIP_TYPES))],
    )
    seed.execute(
        "INSERT INTO sbom_dependencies "
        "(id, sbom_record_id, parent_component_id, child_component_id, relationship_type) "
        "VALUES ('pre', 1, 'c1', 'c2', 'depends_on')"
    )
    seed.commit()
    seed.close()

    runner = MigrationRunner(db_path=db_path, engine="sqlite")
    runner.ensure_migrations_table()
    migration = next(
        (m for m in runner.discover_migrations() if m["version"] == MIGRATION_VERSION), None
    )
    assert migration is not None, (
        f"migration {MIGRATION_VERSION} is not discoverable — a directory with neither "
        "up.sql nor up.py is skipped silently by discover_migrations"
    )
    result = runner.apply_migration(migration)
    assert result["success"], f"migration failed on SQLite: {result.get('error')}"
    return db_path


def _insert_edge(conn, edge_id, relationship_type, parent="c1", child="c2"):
    conn.execute(
        "INSERT INTO sbom_dependencies "
        "(id, sbom_record_id, parent_component_id, child_component_id, relationship_type) "
        "VALUES (?, 1, ?, ?, ?)",
        (edge_id, parent, child, relationship_type),
    )
    conn.commit()


def test_the_database_accepts_every_relationship_type_the_graph_emits(migrated_sqlite_db):
    conn = sqlite3.connect(str(migrated_sqlite_db))
    try:
        for index, relationship_type in enumerate(dg.RELATIONSHIP_TYPES):
            # A fresh parent per type: the UNIQUE key covers relationship_type,
            # but the seeded 'pre' row already occupies (c1, c2, depends_on).
            _insert_edge(conn, f"edge-{index}", relationship_type, parent=f"p{index}")
        stored = {
            row[0] for row in conn.execute("SELECT relationship_type FROM sbom_dependencies")
        }
        assert set(dg.RELATIONSHIP_TYPES) <= stored
    finally:
        conn.close()


def test_the_database_rejects_a_relationship_type_outside_the_vocabulary(migrated_sqlite_db):
    conn = sqlite3.connect(str(migrated_sqlite_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_edge(conn, "bogus", "necessary_for_the_operation_of")
    finally:
        conn.close()


def test_the_migration_preserves_rows_written_before_it(migrated_sqlite_db):
    """The SQLite branch rebuilds the table; a rebuild that dropped rows is a
    data-loss migration that would pass a columns-exist assertion."""
    conn = sqlite3.connect(str(migrated_sqlite_db))
    try:
        row = conn.execute(
            "SELECT relationship_type FROM sbom_dependencies WHERE id = 'pre'"
        ).fetchone()
        assert row is not None and row[0] == "depends_on"
    finally:
        conn.close()


def _pg_url():
    """The live PG DSN, if one is reachable.

    Deliberately keyed on ICDEV_DATABASE_URL rather than ICDEV_STORAGE_BACKEND:
    ``tests/conftest.py`` pins the backend to sqlite for the whole session, so a
    guard that read the backend would make this test unrunnable everywhere —
    which for the primary backend is worse than no test.
    """
    url = os.environ.get("ICDEV_DATABASE_URL", "")
    if not url.startswith("postgres"):
        return None
    try:
        from tools.db.storage import _get_pg_connection

        raw = _get_pg_connection(url)
        raw.close()
        return url
    except Exception:
        return None


@pytest.mark.skipif(not _pg_url(), reason="no reachable PostgreSQL (ICDEV_DATABASE_URL)")
def test_the_constraint_installs_and_enforces_on_postgresql():
    """The PG branch is the one that ships — PostgreSQL is the primary backend.

    Runs in a throwaway schema, never against live tables, so the assertions are
    about the migration rather than about whatever state the dev database is in.
    """
    from tools.db.storage import StorageConnection, _get_pg_connection

    up = _load_migration_module("up")
    conn = StorageConnection(_get_pg_connection(_pg_url()), "postgresql")
    schema = "sbx_cov02_" + uuid.uuid4().hex[:8]
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path TO {schema}")
    conn.execute(
        """
        CREATE TABLE sbom_dependencies (
            id                  TEXT    PRIMARY KEY,
            sbom_record_id      INTEGER NOT NULL,
            parent_component_id TEXT    NOT NULL,
            child_component_id  TEXT    NOT NULL,
            relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
            scope               TEXT,
            classification      TEXT    NOT NULL DEFAULT 'CUI',
            tenant_id           TEXT,
            created_at          TEXT,
            UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
        )
        """
    )
    conn.commit()
    try:
        assert up.up(conn)["actions"] == ["pg_constraint_added"]
        # PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS, so re-running has to be
        # guarded rather than merely tolerated.
        assert up.up(conn)["actions"] == ["pg_constraint_already_present"]

        for index, relationship_type in enumerate(dg.RELATIONSHIP_TYPES):
            conn.execute(
                "INSERT INTO sbom_dependencies (id, sbom_record_id, parent_component_id, "
                "child_component_id, relationship_type) VALUES (%s, 1, %s, 'c2', %s)",
                (f"edge-{index}", f"p{index}", relationship_type),
            )
        conn.commit()

        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO sbom_dependencies (id, sbom_record_id, parent_component_id, "
                "child_component_id, relationship_type) "
                "VALUES ('bogus', 1, 'c1', 'c2', 'necessary_for_the_operation_of')"
            )
            conn.commit()
        conn.rollback()

        down = _load_migration_module("down")
        assert down.down(conn)["actions"] == ["pg_constraint_dropped"]
    finally:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.commit()
        conn.close()


def _load_migration_module(direction):
    path = (
        BASE_DIR
        / "tools"
        / "db"
        / "migrations"
        / f"{MIGRATION_VERSION}_sbom_relationship_type_vocabulary"
        / f"{direction}.py"
    )
    spec = importlib.util.spec_from_file_location(f"sbx_cov02_{direction}", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SNAPSHOT_GAP_VERSION = "20260808000000"
SNAPSHOT_GAP_DIR = (
    BASE_DIR / "tools" / "db" / "migrations" / f"{SNAPSHOT_GAP_VERSION}_sbom_components_pg_snapshot_gap"
)
#: Everything migration 209 creates. A fresh PostgreSQL bootstrap has none of
#: it — pg_consolidated.sql omits all three while the snapshot marker claims
#: coverage through version 301, so bootstrap_pg records 209 applied without
#: running it.
MIGRATION_209_TABLES = (
    "sbom_components",
    "supply_chain_vulnerabilities",
    "supply_chain_risk_scores",
)


def test_the_snapshot_gap_repair_restores_every_table_209_creates():
    """Half a repair is worse than none: sbom_dependencies' foreign key needs
    sbom_components, and handler_service.py SELECTs the other two."""
    sql = (SNAPSHOT_GAP_DIR / "up.sql").read_text(encoding="utf-8")
    for table in MIGRATION_209_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, (
            f"{table} is created by migration 209 but not restored by the snapshot-gap repair"
        )


def test_the_snapshot_gap_repair_is_ordered_before_the_migration_that_needs_it():
    """20260808030213 ALTERs sbom_components and foreign-keys sbom_dependencies
    to it. If the repair does not sort first, both statements fail on a fresh
    PostgreSQL and are swallowed by executescript's skip handling — the
    migration records success having added nothing."""
    from tools.db.bootstrap_pg import _version_order_key

    assert _version_order_key(SNAPSHOT_GAP_VERSION) < _version_order_key("20260808030213")
    assert _version_order_key("20260808030213") < _version_order_key(MIGRATION_VERSION)


# The end-to-end proof that the three migrations produce the whole schema on a
# fresh PostgreSQL is the CI "Test (PostgreSQL)" job itself: it runs
# bootstrap_pg against an empty database, and _apply_post_snapshot_migrations
# raises SystemExit on any failure. That is what turned this defect up — the
# vocabulary migration refuses to record itself as applied when
# sbom_dependencies is absent, so the job went red instead of building another
# database that lies about its own schema. Reproducing it here would mean
# CREATE DATABASE plus a global connection-pool reset inside a pytest session
# that has already opened one, and the resulting test would be a worse gate
# than the bootstrap it imitates.


def test_conftest_schema_declares_the_same_vocabulary():
    """conftest is what most of the suite tests against; drift from the
    migration is how a test starts passing against a shape production lacks."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    for relationship_type in dg.RELATIONSHIP_TYPES:
        assert f"'{relationship_type}'" in MINIMAL_ICDEV_SCHEMA, (
            f"{relationship_type} is missing from the sbom_dependencies CHECK in "
            "tests/conftest.py"
        )
    assert dg.relationship_check_sql() in " ".join(MINIMAL_ICDEV_SCHEMA.split())


# ---------------------------------------------------------------------------
# packaging
# ---------------------------------------------------------------------------


def test_root_and_mirror_stay_in_sync():
    assert MIRROR_COPY.exists(), (
        "icdev/tools/compliance/dependency_graph.py is missing — the packaged copy is "
        "what a pip install ships"
    )
    assert ROOT_COPY.read_text(encoding="utf-8") == MIRROR_COPY.read_text(encoding="utf-8"), (
        "tools/compliance/dependency_graph.py and its icdev/ mirror have diverged"
    )
