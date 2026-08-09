#!/usr/bin/env python3
# CUI // SP-CTI
"""Component Dependency Relationship (SBOM 2026, sbx-cov-02).

Before this task the ICDEV SBOM was a flat component list with no CycloneDX
``dependencies`` array at all. These cases pin the element in both directions:
the generator emits a rooted, cycle-checked graph whose every ref resolves, and
the validator rejects each way that can fail — including the pre-sbx-cov-02
flat-list shape itself.
"""

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.compliance import dependency_graph as dg  # noqa: E402
from tools.compliance.sbom_generator import _build_cyclonedx_sbom  # noqa: E402

MIGRATION_DIR = (
    REPO_ROOT / "tools" / "db" / "migrations" / "20260809232803_sbom_relationship_type_vocabulary"
)


# =====================================================================================
# Fixtures
# =====================================================================================


def component(name, version, key, dependencies=(), scope="required", resolution="resolved"):
    """One resolver-shape instance, in the shape `dependency_resolver._component` emits."""
    return {
        "type": "library",
        "name": name,
        "declared_name": name,
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
        "declared_license": "MIT",
        "declared_hashes": [],
        "artifact_path": "",
        "artifact_subject": "",
    }


def generate(components, project_id="proj"):
    document, _count = _build_cyclonedx_sbom({"id": project_id, "name": "Proj"}, components)
    return document


def refs_of(document):
    return {c["bom-ref"] for c in document["components"]}


def entry_for(document, ref):
    return next((e for e in document["dependencies"] if e["ref"] == ref), None)


# A chain: root -> a -> b -> c
CHAIN = [
    component("a", "1.0.0", "ka", ["kb"]),
    component("b", "1.0.0", "kb", ["kc"]),
    component("c", "1.0.0", "kc"),
]


# =====================================================================================
# The array exists and is rooted
# =====================================================================================


def test_a_generated_sbom_carries_a_dependencies_array():
    """The element was entirely absent before sbx-cov-02, not partially met."""
    document = generate(CHAIN)
    assert document["dependencies"], "no `dependencies` array — the element is still absent"


def test_the_graph_is_rooted_at_the_target_component():
    document = generate(CHAIN)
    target = document["metadata"]["component"]["bom-ref"]
    assert entry_for(document, target) is not None
    # Every component is reachable from the target, or it is not in the tree.
    adjacency = {e["ref"]: e["dependsOn"] for e in document["dependencies"]}
    assert dg._reachable(adjacency, target) >= refs_of(document)


def test_every_bom_ref_resolves():
    document = generate(CHAIN)
    known = refs_of(document) | {document["metadata"]["component"]["bom-ref"]}
    for entry in document["dependencies"]:
        assert entry["ref"] in known
        for child in entry["dependsOn"]:
            assert child in known, f"dependsOn {child!r} resolves to nothing"


def test_the_chain_is_reconstructed_not_flattened():
    """root -> a -> b -> c, not root -> {a, b, c}."""
    document = generate(CHAIN)
    target = document["metadata"]["component"]["bom-ref"]
    by_name = {c["name"]: c["bom-ref"] for c in document["components"]}

    assert entry_for(document, target)["dependsOn"] == [by_name["a"]]
    assert entry_for(document, by_name["a"])["dependsOn"] == [by_name["b"]]
    assert entry_for(document, by_name["b"])["dependsOn"] == [by_name["c"]]
    assert entry_for(document, by_name["c"])["dependsOn"] == []


def test_a_transitive_dependency_is_not_claimed_as_direct():
    """The narrow definition: an edge only where one component is necessary for
    the operation of the other. `b` is reached through `a`, so the target does
    not depend on it directly."""
    document = generate(CHAIN)
    target = document["metadata"]["component"]["bom-ref"]
    by_name = {c["name"]: c["bom-ref"] for c in document["components"]}
    assert by_name["b"] not in entry_for(document, target)["dependsOn"]
    assert by_name["c"] not in entry_for(document, target)["dependsOn"]


def test_no_edge_is_invented_between_unrelated_components():
    document = generate([component("x", "1", "kx"), component("y", "1", "ky")])
    by_name = {c["name"]: c["bom-ref"] for c in document["components"]}
    assert entry_for(document, by_name["x"])["dependsOn"] == []
    assert entry_for(document, by_name["y"])["dependsOn"] == []


def test_a_project_with_no_components_still_states_a_root():
    """"This software has no components" is a statement worth making."""
    document = generate([])
    target = document["metadata"]["component"]["bom-ref"]
    assert entry_for(document, target) == {"ref": target, "dependsOn": []}


# =====================================================================================
# Duplicate instances appear separately with their own relationships
# =====================================================================================


def test_instances_differing_only_in_resolved_dependencies_stay_separate():
    """The refinement that motivates this task.

    Two `foo@1.0.0` instances that resolve different versions of `bar` are two
    components. Collapsing them would either invent an edge or lose one.
    """
    document = generate(
        [
            component("foo", "1.0.0", "a/foo", ["a/foo/bar"]),
            component("foo", "1.0.0", "b/foo", ["b/foo/bar"]),
            component("bar", "1.0.0", "a/foo/bar"),
            component("bar", "2.0.0", "b/foo/bar"),
        ]
    )
    foos = [c for c in document["components"] if c["name"] == "foo"]
    assert len(foos) == 2, "two instances with different dependencies collapsed into one"

    bar_of = {c["version"]: c["bom-ref"] for c in document["components"] if c["name"] == "bar"}
    edges = {entry_for(document, f["bom-ref"])["dependsOn"][0] for f in foos}
    assert edges == {bar_of["1.0.0"], bar_of["2.0.0"]}, "each instance keeps its own edge"


def test_indistinguishable_instances_are_one_component():
    """The refinement is strict, not indiscriminate: two instances agreeing on
    metadata *and* on relationships are one entry, not two identical ones."""
    document = generate(
        [
            component("dup", "1.0.0", "a/dup"),
            component("dup", "1.0.0", "b/dup"),
        ]
    )
    assert len([c for c in document["components"] if c["name"] == "dup"]) == 1


def test_separately_listed_components_never_share_a_bom_ref():
    """`component_id` hashes coordinates alone while a component is listed
    separately when any of six fields differ, so two entries could collide on
    one ref. An edge naming a shared ref identifies neither component."""
    document = generate(
        [
            component("lodash", "4.17.21", "k1", scope="required"),
            component("lodash", "4.17.21", "k2", scope="optional"),
        ]
    )
    refs = [c["bom-ref"] for c in document["components"]]
    assert len(refs) == 2
    assert len(set(refs)) == 2, f"two components share one bom-ref: {refs}"
    assert dg.validate_dependency_graph(document)["status"] == "met"


def test_the_generator_dedup_rule_is_the_graph_rule():
    """Two copies of "what counts as one component" is how a dependsOn ends up
    naming a component the document does not list."""
    from tools.compliance import sbom_generator

    instance = component("z", "1", "kz")
    assert sbom_generator._component_identity(instance) == dg.component_identity(instance)


# =====================================================================================
# Cycles
# =====================================================================================


def test_a_cycle_is_detected_and_declared():
    document = generate([component("a", "1", "ka", ["kb"]), component("b", "1", "kb", ["ka"])])
    properties = {p["name"]: p["value"] for p in document["metadata"]["properties"]}
    assert properties[dg.PROPERTY_CYCLES] == "1"
    assert dg.PROPERTY_CYCLES_DETAIL in properties


def test_an_acyclic_graph_declares_zero_cycles():
    properties = {p["name"]: p["value"] for p in generate(CHAIN)["metadata"]["properties"]}
    assert properties[dg.PROPERTY_CYCLES] == "0"


def test_a_cycle_does_not_make_components_unreachable():
    """A cycle with no external entry point still has to be reachable from the
    target, or its members are not in the tree at all."""
    document = generate([component("a", "1", "ka", ["kb"]), component("b", "1", "kb", ["ka"])])
    adjacency = {e["ref"]: e["dependsOn"] for e in document["dependencies"]}
    target = document["metadata"]["component"]["bom-ref"]
    assert dg._reachable(adjacency, target) >= refs_of(document)


def test_cycle_detection_terminates_on_a_deep_chain():
    """Iterative, so a deep npm tree cannot blow the stack."""
    adjacency = {f"n{i}": [f"n{i + 1}"] for i in range(3000)}
    adjacency["n3000"] = []
    assert dg.detect_cycles(adjacency) == []


def test_a_cycle_is_reported_once_regardless_of_entry_point():
    assert len(dg.detect_cycles({"a": ["b"], "b": ["a"]})) == 1


def test_self_dependency_is_a_cycle():
    assert dg.detect_cycles({"a": ["a"]}) == [["a", "a"]]


# =====================================================================================
# Unknown vs known-empty
# =====================================================================================


def test_a_declared_only_component_gets_no_entry():
    """CycloneDX distinguishes unknown from known-empty by absence. An entry
    with an empty dependsOn asserts the component depends on nothing; a
    manifest-declared component whose own dependencies were never read has not
    made that claim."""
    document = generate([component("flask", "2.0", "k1", resolution="declared")])
    by_name = {c["name"]: c["bom-ref"] for c in document["components"]}
    assert entry_for(document, by_name["flask"]) is None

    properties = {p["name"]: p["value"] for p in document["metadata"]["properties"]}
    assert properties[dg.PROPERTY_UNKNOWN] == "1"


def test_a_resolved_leaf_states_a_known_empty_dependency_set():
    document = generate([component("leaf", "1", "k1")])
    by_name = {c["name"]: c["bom-ref"] for c in document["components"]}
    assert entry_for(document, by_name["leaf"]) == {"ref": by_name["leaf"], "dependsOn": []}


def test_a_declared_component_is_still_reachable_from_the_target():
    document = generate([component("flask", "2.0", "k1", resolution="declared")])
    target = document["metadata"]["component"]["bom-ref"]
    assert refs_of(document) <= set(entry_for(document, target)["dependsOn"])


# =====================================================================================
# Embedding, not linking
# =====================================================================================


def test_the_document_states_that_it_embeds_rather_than_links():
    """The standard permits linking, but a link satisfies Coverage only if the
    recipient is guaranteed access to every linked document."""
    properties = {p["name"]: p["value"] for p in generate(CHAIN)["metadata"]["properties"]}
    assert properties[dg.PROPERTY_GRAPH] == "rooted"
    assert "embedded" in properties[dg.PROPERTY_EMBEDDING]
    assert properties[dg.PROPERTY_EDGES] == "3"


def test_no_dependency_is_expressed_as_a_link_to_another_sbom():
    document = generate(CHAIN)
    for component_entry in document["components"]:
        kinds = {r.get("type") for r in component_entry.get("externalReferences") or []}
        assert "bom" not in kinds


# =====================================================================================
# The validator
# =====================================================================================


def test_the_validator_scores_a_generated_sbom_met():
    report = dg.validate_dependency_graph(generate(CHAIN))
    assert report["status"] == "met", report["findings"]
    assert report["element"] == dg.ELEMENT_NAME
    assert report["stats"]["edges"] == 3


def test_the_validator_rejects_the_pre_sbx_cov_02_flat_list():
    document = generate(CHAIN)
    del document["dependencies"]
    report = dg.validate_dependency_graph(document)
    assert report["status"] == "not_met"
    assert [f["code"] for f in report["findings"]] == ["dependencies_absent"]


def test_the_validator_rejects_an_empty_dependencies_array():
    document = generate(CHAIN)
    document["dependencies"] = []
    assert dg.validate_dependency_graph(document)["status"] == "not_met"


def test_the_validator_rejects_a_dangling_depends_on():
    document = generate(CHAIN)
    document["dependencies"][1]["dependsOn"] = ["no-such-component"]
    report = dg.validate_dependency_graph(document)
    assert report["status"] == "not_met"
    assert "unresolved_depends_on" in {f["code"] for f in report["findings"]}


def test_the_validator_rejects_an_entry_for_an_unknown_ref():
    document = generate(CHAIN)
    document["dependencies"].append({"ref": "ghost", "dependsOn": []})
    report = dg.validate_dependency_graph(document)
    assert "unresolved_ref" in {f["code"] for f in report["findings"]}


def test_the_validator_rejects_a_duplicated_entry():
    document = generate(CHAIN)
    document["dependencies"].append(dict(document["dependencies"][1]))
    report = dg.validate_dependency_graph(document)
    assert "duplicate_ref" in {f["code"] for f in report["findings"]}


def test_the_validator_rejects_an_unrooted_graph():
    document = generate(CHAIN)
    target = document["metadata"]["component"]["bom-ref"]
    document["dependencies"] = [e for e in document["dependencies"] if e["ref"] != target]
    report = dg.validate_dependency_graph(document)
    assert "unrooted_graph" in {f["code"] for f in report["findings"]}


def test_the_validator_rejects_a_component_unreachable_from_the_target():
    document = generate(CHAIN)
    target = document["metadata"]["component"]["bom-ref"]
    entry_for(document, target)["dependsOn"] = []
    report = dg.validate_dependency_graph(document)
    assert "unreachable_components" in {f["code"] for f in report["findings"]}


def test_the_validator_rejects_two_components_sharing_a_bom_ref():
    document = generate(CHAIN)
    document["components"][1]["bom-ref"] = document["components"][0]["bom-ref"]
    report = dg.validate_dependency_graph(document)
    assert "duplicate_component_ref" in {f["code"] for f in report["findings"]}


def test_the_validator_rejects_a_declared_cycle_count_that_disagrees():
    """The count is what makes "cycle-checked" verifiable rather than a claim
    the recipient has to take on trust."""
    document = generate(CHAIN)
    for prop in document["metadata"]["properties"]:
        if prop["name"] == dg.PROPERTY_CYCLES:
            prop["value"] = "7"
    report = dg.validate_dependency_graph(document)
    assert "cycle_count_mismatch" in {f["code"] for f in report["findings"]}


def test_a_third_party_sbom_without_icdev_properties_can_still_be_met():
    """The validator cycle-checks every document itself, so requiring an
    `icdev:`-namespaced property would score every valid third-party CycloneDX
    document a gap for the sole reason that ICDEV did not write it."""
    document = generate(CHAIN)
    document["metadata"]["properties"] = []
    report = dg.validate_dependency_graph(document)
    assert report["status"] == "met", report["findings"]
    assert report["stats"]["cycles"] == 0


def test_the_validator_reports_a_cycle_in_stats_even_when_undeclared():
    document = generate([component("a", "1", "ka", ["kb"]), component("b", "1", "kb", ["ka"])])
    document["metadata"]["properties"] = []
    assert dg.validate_dependency_graph(document)["stats"]["cycles"] == 1


def test_the_validator_survives_a_malformed_entry():
    document = generate(CHAIN)
    document["dependencies"].append("not-an-object")
    report = dg.validate_dependency_graph(document)
    assert "malformed_entry" in {f["code"] for f in report["findings"]}


# =====================================================================================
# The conformance gate scores the element
# =====================================================================================


def test_the_conformance_gate_scores_the_element_met():
    from tools.compliance import sbom_conformance_gate as gate

    scored = gate._score_structural(generate(CHAIN))
    assert scored["elements"]["component_dependency_relationship"] == gate.MET


def test_the_conformance_gate_scores_a_flat_list_as_a_gap():
    from tools.compliance import sbom_conformance_gate as gate

    document = generate(CHAIN)
    del document["dependencies"]
    scored = gate._score_structural(document)
    assert scored["elements"]["component_dependency_relationship"] == gate.GAP


def test_the_gate_is_not_satisfied_by_presence_alone():
    """Presence was the interim check. An array whose dependsOn names a ref no
    component carries builds no graph at all, and it is exactly the shape a
    partial implementation produces."""
    from tools.compliance import sbom_conformance_gate as gate

    document = generate(CHAIN)
    document["dependencies"][1]["dependsOn"] = ["no-such-component"]
    assert document["dependencies"]  # present...
    scored = gate._score_structural(document)
    assert scored["elements"]["component_dependency_relationship"] == gate.GAP  # ...but not met


# =====================================================================================
# SPDX expresses the same edges (sbx-fmt-01)
# =====================================================================================


def test_spdx_relationships_carry_the_same_edges():
    from tools.compliance.spdx_writer import to_spdx

    document = generate(CHAIN)
    spdx = to_spdx(document)

    cyclonedx_edges = sum(len(e["dependsOn"]) for e in document["dependencies"])
    spdx_edges = [r for r in spdx["relationships"] if r["relationshipType"] != "DESCRIBES"]
    assert len(spdx_edges) == cyclonedx_edges == 3
    assert sum(1 for r in spdx["relationships"] if r["relationshipType"] == "DESCRIBES") == 1


def test_the_shared_renderer_maps_an_optional_edge_to_its_spdx_inverse():
    """SPDX's OPTIONAL_DEPENDENCY_OF reads A-is-an-optional-dependency-of-B,
    the inverse of our direction — hence the swap rather than a second edge."""
    graph = dg.build_dependency_graph(
        [component("a", "1", "ka", ["kb"]), component("b", "1", "kb", scope="optional")],
        "root",
        mint_ref=lambda c: c["name"],
    )
    relationships = dg.to_spdx_relationships(graph)
    optional = [r for r in relationships if r["relationshipType"] == "OPTIONAL_DEPENDENCY_OF"]
    assert optional, relationships
    assert optional[0]["spdxElementId"] == "SPDXRef-b"
    assert optional[0]["relatedSpdxElement"] == "SPDXRef-a"


def test_the_shared_renderer_always_describes_the_root():
    graph = dg.build_dependency_graph(CHAIN, "root", mint_ref=lambda c: c["name"])
    first = dg.to_spdx_relationships(graph)[0]
    assert first == {
        "spdxElementId": dg.SPDX_DOCUMENT_REF,
        "relationshipType": "DESCRIBES",
        "relatedSpdxElement": "SPDXRef-root",
    }


def test_every_relationship_type_has_an_spdx_mapping():
    """A type with no mapping would raise in `to_spdx_relationships` at emit
    time, on a document that had already been generated."""
    for relationship_type in dg.RELATIONSHIP_TYPES:
        assert relationship_type in dg.SPDX_RELATIONSHIP


# =====================================================================================
# Graph builder units
# =====================================================================================


def test_a_dangling_resolver_edge_is_dropped_and_counted():
    graph = dg.build_dependency_graph(
        [component("a", "1", "ka", ["missing"])], "root", mint_ref=lambda c: c["name"]
    )
    assert graph["dangling_edges"] == 1
    assert all(e["to"] != "missing" for e in graph["edges"])


def test_an_orphan_is_attached_to_the_root_and_reported():
    graph = dg.build_dependency_graph(
        [component("a", "1", "ka", ["kb"]), component("b", "1", "kb", ["ka"])],
        "root",
        mint_ref=lambda c: c["name"],
    )
    assert graph["unrooted"], "a cycle with no external entry was left unreachable"
    assert "root" in graph["adjacency"]


def test_duplicate_resolver_keys_do_not_drop_a_component():
    graph = dg.build_dependency_graph(
        [component("a", "1", "same"), component("b", "1", "same")],
        "root",
        mint_ref=lambda c: c["name"],
    )
    assert len(graph["nodes"]) == 2


def test_the_builder_does_not_mutate_the_caller_components():
    """The generator enriches each instance as it renders it and its caller
    reads that back off the list it passed in."""
    components = [component("a", "1", "ka")]
    snapshot = json.loads(json.dumps(components))
    dg.build_dependency_graph(components, "root", mint_ref=lambda c: c["name"])
    assert components == snapshot


def test_the_builder_returns_the_callers_own_dicts():
    components = [component("a", "1", "ka")]
    graph = dg.build_dependency_graph(components, "root", mint_ref=lambda c: c["name"])
    assert graph["nodes"][0]["instance"] is components[0]


def test_dependency_rows_exclude_the_root_edge():
    """Both operands of `sbom_dependencies` are component rows; the target
    software is metadata.component, not a components entry."""
    graph = dg.build_dependency_graph(CHAIN, "root", mint_ref=lambda c: c["name"])
    rows = dg.dependency_rows(graph)
    assert all(row["parent_component_id"] != "root" for row in rows)
    assert {row["relationship_type"] for row in rows} <= set(dg.RELATIONSHIP_TYPES)


def test_an_optional_target_produces_an_optional_edge():
    graph = dg.build_dependency_graph(
        [component("a", "1", "ka", ["kb"]), component("b", "1", "kb", scope="optional")],
        "root",
        mint_ref=lambda c: c["name"],
    )
    edge = next(e for e in graph["edges"] if e["from"] == "a")
    assert edge["type"] == dg.RELATIONSHIP_OPTIONAL_DEPENDS_ON


# =====================================================================================
# The relationship_type CHECK vocabulary (migration 20260809232803)
# =====================================================================================


def _load_migration(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATION_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SqliteShim:
    """The `%s`-parameter, `_backend`-carrying connection a migration expects."""

    _backend = "sqlite"

    def __init__(self, connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._connection.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._connection.commit()


@pytest.fixture()
def dependencies_table():
    """`sbom_dependencies` in its sbx-fnd-02 shape — no CHECK yet."""
    with tempfile.TemporaryDirectory() as directory:
        connection = sqlite3.connect(Path(directory) / "t.db")
        connection.executescript(
            """
            CREATE TABLE sbom_records (id INTEGER PRIMARY KEY);
            CREATE TABLE sbom_components (id TEXT PRIMARY KEY);
            CREATE TABLE sbom_dependencies (
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
            INSERT INTO sbom_records (id) VALUES (1);
            INSERT INTO sbom_components (id) VALUES ('c1'), ('c2');
            """
        )
        connection.commit()
        yield connection
        connection.close()


def _insert(connection, row_id, relationship_type):
    connection.execute(
        "INSERT INTO sbom_dependencies (id, sbom_record_id, parent_component_id, "
        "child_component_id, relationship_type) VALUES (?, 1, 'c1', 'c2', ?)",
        (row_id, relationship_type),
    )


def test_the_migration_is_discoverable_by_the_runner():
    """A directory with neither up.sql nor up.py is skipped silently."""
    from tools.db.migration_runner import MigrationRunner

    found = [
        m
        for m in MigrationRunner().discover_migrations()
        if m["name"] == "sbom_relationship_type_vocabulary"
    ]
    assert len(found) == 1, found
    assert found[0]["has_up_py"]
    assert found[0]["version"] == "20260809232803"


def test_the_check_is_installed_and_rejects_a_value_outside_the_vocabulary(dependencies_table):
    result = _load_migration("up").up(_SqliteShim(dependencies_table))
    assert result["status"] == "applied"
    assert result["vocabulary"] == list(dg.RELATIONSHIP_TYPES)

    with pytest.raises(sqlite3.IntegrityError):
        _insert(dependencies_table, "bad", "not_a_relationship")


def test_every_vocabulary_value_is_accepted(dependencies_table):
    _load_migration("up").up(_SqliteShim(dependencies_table))
    for index, relationship_type in enumerate(dg.RELATIONSHIP_TYPES):
        _insert(dependencies_table, f"ok{index}", relationship_type)


def test_the_rebuild_preserves_existing_rows(dependencies_table):
    _insert(dependencies_table, "keep", "depends_on")
    dependencies_table.commit()

    _load_migration("up").up(_SqliteShim(dependencies_table))

    rows = dependencies_table.execute("SELECT id FROM sbom_dependencies").fetchall()
    assert [row[0] for row in rows] == ["keep"]


def test_the_migration_is_idempotent(dependencies_table):
    module = _load_migration("up")
    module.up(_SqliteShim(dependencies_table))
    second = module.up(_SqliteShim(dependencies_table))
    assert second["actions"] == ["sqlite_check_already_present"]


def test_the_constraint_and_the_python_constant_cannot_drift():
    """The house rule: derive a CHECK vocabulary from the Python constant."""
    clause = dg.relationship_check_sql()
    for relationship_type in dg.RELATIONSHIP_TYPES:
        assert f"'{relationship_type}'" in clause
    assert clause.startswith("CHECK (relationship_type IN (")


def test_the_migration_refuses_when_the_table_is_absent():
    """Recording a no-op as applied would make it unrunnable later."""
    with tempfile.TemporaryDirectory() as directory:
        connection = sqlite3.connect(Path(directory) / "empty.db")
        with pytest.raises(RuntimeError, match="sbom_dependencies does not exist"):
            _load_migration("up").up(_SqliteShim(connection))
        connection.close()


# =====================================================================================
# CLI
# =====================================================================================


def test_the_cli_validates_a_generated_sbom(tmp_path):
    path = tmp_path / "sbom.json"
    path.write_text(json.dumps(generate(CHAIN)), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "compliance" / "dependency_graph.py"),
         "--validate", str(path), "--json"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "met"


def test_the_cli_exits_non_zero_on_a_flat_list(tmp_path):
    document = generate(CHAIN)
    del document["dependencies"]
    path = tmp_path / "flat.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "compliance" / "dependency_graph.py"),
         "--validate", str(path), "--json"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["status"] == "not_met"


# =====================================================================================
# Mirror parity
# =====================================================================================


def test_root_and_mirror_stay_in_sync():
    for relative in (
        "tools/compliance/dependency_graph.py",
        "tools/compliance/sbom_generator.py",
        "tools/compliance/sbom_conformance_gate.py",
    ):
        root = REPO_ROOT / relative
        mirror = REPO_ROOT / "icdev" / relative
        assert mirror.exists(), f"icdev/{relative} is missing"
        assert root.read_bytes() == mirror.read_bytes(), relative
