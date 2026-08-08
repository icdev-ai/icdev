#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-02 — the SBOM 2026 **Component Producer** element.

The 2026 Minimum Elements replaced *Supplier Name* with **Component Producer**:
the entity that creates, defines and identifies the component, exactly one
organization per component. ICDEV emitted neither, and the field that looks like
a producer — CycloneDX ``group`` — is a Maven/npm namespace.

Each ecosystem gets its own resolution test over a fixture whose producer
metadata is known by construction, plus the two negatives that matter more than
any positive: a component whose producer cannot be identified is **explicitly
marked of unknown provenance**, and a producer is **never** populated from
``group``.
"""

import json
import textwrap
from pathlib import Path

import pytest

from tools.compliance import component_producer as cp

REPO_ROOT = Path(__file__).resolve().parent.parent

ACME = "Acme Widgets, Inc."


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


def _registry():
    return cp.load_producer_registry()


# ---------------------------------------------------------------------------
# python — PyPI Author / Maintainer distribution metadata
# ---------------------------------------------------------------------------


def _dist_info(project, name, version, body):
    site_packages = project / ".venv" / "Lib" / "site-packages"
    _write(
        site_packages / f"{name}-{version}.dist-info" / "METADATA",
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{body}\n",
    )
    return site_packages


def test_python_producer_comes_from_the_distribution_author(tmp_path):
    _dist_info(tmp_path, "alpha", "1.0.0", f"Author: {ACME}\nAuthor-email: dev@acme.example")

    result = cp.resolve_producer(
        {"ecosystem": "python", "name": "alpha", "version": "1.0.0", "purl": "pkg:pypi/alpha@1.0.0"},
        project_dir=tmp_path,
    )

    assert result["producer"] == ACME
    assert result["provenance"] == cp.KNOWN
    assert result["source"] == cp.SOURCE_PYTHON_DIST


def test_python_falls_back_to_maintainer_then_to_the_author_email_domain(tmp_path):
    _dist_info(tmp_path, "beta", "2.0.0", "Author:\nMaintainer: Beta Foundation")
    _dist_info(tmp_path, "gamma", "3.0.0", "Author: UNKNOWN\nAuthor-email: security@apache.org")

    beta = cp.resolve_producer({"ecosystem": "python", "name": "beta", "version": "2.0.0"}, project_dir=tmp_path)
    assert beta["producer"] == "Beta Foundation"

    # `Author: UNKNOWN` is setuptools' placeholder, not an organization called
    # UNKNOWN. The registered e-mail domain is the next real piece of evidence.
    gamma = cp.resolve_producer({"ecosystem": "python", "name": "gamma", "version": "3.0.0"}, project_dir=tmp_path)
    assert gamma["producer"] == "The Apache Software Foundation"
    assert gamma["source"] == cp.SOURCE_EMAIL_DOMAIN_REGISTRY


def test_python_distribution_naming_is_normalized_before_lookup(tmp_path):
    """`ruamel.yaml`, `ruamel-yaml` and `Ruamel_YAML` are one distribution."""
    _dist_info(tmp_path, "Ruamel_YAML", "0.18.6", f"Author: {ACME}")

    result = cp.resolve_producer(
        {"ecosystem": "python", "name": "ruamel.yaml", "version": "0.18.6"}, project_dir=tmp_path
    )
    assert result["producer"] == ACME


def test_python_with_no_installed_environment_is_unknown_provenance(tmp_path):
    result = cp.resolve_producer(
        {"ecosystem": "python", "name": "alpha", "version": "1.0.0"}, project_dir=tmp_path
    )
    assert cp.is_unknown(result)
    assert result["reason"] == cp.REASON_METADATA_NOT_FOUND


# ---------------------------------------------------------------------------
# npm — the package's own `author` field
# ---------------------------------------------------------------------------


def test_npm_producer_comes_from_the_installed_package_author(tmp_path):
    _write(
        tmp_path / "node_modules" / "alpha" / "package.json",
        json.dumps({"name": "alpha", "version": "1.0.0", "author": {"name": ACME, "email": "dev@acme.example"}}),
    )

    result = cp.resolve_producer(
        {"ecosystem": "npm", "name": "alpha", "version": "1.0.0", "purl": "pkg:npm/alpha@1.0.0"},
        project_dir=tmp_path,
    )
    assert result["producer"] == ACME
    assert result["source"] == cp.SOURCE_NPM_PACKAGE_JSON


def test_npm_reads_the_nested_instance_not_whichever_copy_is_on_top(tmp_path):
    """Two installed copies of one package can have different producers.

    ``dependency_resolver`` keys an npm instance by its install path precisely so
    nested duplicates stay distinct; the producer has to follow the same path or
    the nested instance inherits a fact about a different copy.
    """
    _write(
        tmp_path / "node_modules" / "gamma" / "package.json",
        json.dumps({"name": "gamma", "version": "3.0.0", "author": "Top Level Org"}),
    )
    _write(
        tmp_path / "node_modules" / "beta" / "node_modules" / "gamma" / "package.json",
        json.dumps({"name": "gamma", "version": "1.5.0", "author": "Nested Org"}),
    )

    nested = cp.resolve_producer(
        {
            "ecosystem": "npm",
            "name": "gamma",
            "version": "1.5.0",
            "key": "npm|node_modules/beta/node_modules/gamma",
        },
        project_dir=tmp_path,
    )
    assert nested["producer"] == "Nested Org"


def test_npm_scoped_package_resolves_under_its_scope_directory(tmp_path):
    _write(
        tmp_path / "node_modules" / "@acme" / "core" / "package.json",
        json.dumps({"name": "@acme/core", "maintainers": [{"name": ACME}]}),
    )

    result = cp.resolve_producer(
        {"ecosystem": "npm", "group": "@acme", "name": "core", "version": "1.0.0"},
        project_dir=tmp_path,
    )
    assert result["producer"] == ACME


def test_npm_package_that_names_nobody_is_unknown_provenance(tmp_path):
    _write(
        tmp_path / "node_modules" / "silent" / "package.json",
        json.dumps({"name": "silent", "version": "1.0.0"}),
    )

    result = cp.resolve_producer(
        {"ecosystem": "npm", "name": "silent", "version": "1.0.0"}, project_dir=tmp_path
    )
    assert cp.is_unknown(result)
    assert result["reason"] == cp.REASON_METADATA_SILENT


# ---------------------------------------------------------------------------
# maven / gradle — the POM's <organization>, else groupId as reverse-DNS
# ---------------------------------------------------------------------------


def _install_pom(project, group_id, artifact_id, version, body):
    path = (
        project
        / ".m2"
        / "repository"
        / Path(*group_id.split("."))
        / artifact_id
        / version
        / f"{artifact_id}-{version}.pom"
    )
    return _write(path, f"<project>\n{body}\n</project>\n")


def test_maven_producer_comes_from_the_pom_organization(tmp_path):
    _install_pom(
        tmp_path,
        "com.acme",
        "widget",
        "1.0",
        f"  <organization><name>{ACME}</name><url>https://acme.example</url></organization>",
    )

    result = cp.resolve_producer(
        {
            "ecosystem": "maven",
            "group": "com.acme",
            "name": "widget",
            "version": "1.0",
            "purl": "pkg:maven/com.acme/widget@1.0",
        },
        project_dir=tmp_path,
    )
    assert result["producer"] == ACME
    assert result["source"] == cp.SOURCE_MAVEN_POM_ORGANIZATION


def test_maven_falls_back_to_a_developers_organization(tmp_path):
    _install_pom(
        tmp_path,
        "com.acme",
        "gadget",
        "2.0",
        "  <developers><developer><name>A Person</name>"
        f"<organization>{ACME}</organization></developer></developers>",
    )

    result = cp.resolve_producer(
        {"ecosystem": "maven", "name": "gadget", "version": "2.0", "purl": "pkg:maven/com.acme/gadget@2.0"},
        project_dir=tmp_path,
    )
    assert result["producer"] == ACME
    assert result["source"] == cp.SOURCE_MAVEN_POM_DEVELOPER


def test_maven_group_id_is_mapped_through_the_registry_not_copied(tmp_path):
    """`org.apache.commons` is reverse-DNS for a domain, not an organization's name."""
    result = cp.resolve_producer(
        {
            "ecosystem": "maven",
            "group": "org.apache.commons",
            "name": "commons-lang3",
            "version": "3.14.0",
            "purl": "pkg:maven/org.apache.commons/commons-lang3@3.14.0",
        },
        project_dir=tmp_path,
    )
    assert result["producer"] == "The Apache Software Foundation"
    assert result["source"] == cp.SOURCE_NAMESPACE_REGISTRY
    assert result["producer"] != "org.apache.commons"


def test_gradle_coordinates_resolve_through_the_maven_resolver(tmp_path):
    _install_pom(tmp_path, "com.acme", "plugin", "3.1", f"  <organization><name>{ACME}</name></organization>")

    result = cp.resolve_producer(
        {
            "ecosystem": "gradle",
            "group": "com.acme",
            "name": "plugin",
            "version": "3.1",
            "purl": "pkg:maven/com.acme/plugin@3.1",
        },
        project_dir=tmp_path,
    )
    assert result["producer"] == ACME


def test_maven_with_an_unregistered_group_is_unknown_provenance(tmp_path):
    result = cp.resolve_producer(
        {
            "ecosystem": "maven",
            "group": "com.unheard-of-vendor",
            "name": "thing",
            "version": "1.0",
            "purl": "pkg:maven/com.unheard-of-vendor/thing@1.0",
        },
        project_dir=tmp_path,
    )
    assert cp.is_unknown(result)
    assert result["reason"] == cp.REASON_NAMESPACE_UNREGISTERED


# ---------------------------------------------------------------------------
# golang — the module host path
# ---------------------------------------------------------------------------


def test_golang_module_host_path_identifies_the_producer():
    result = cp.resolve_producer(
        {
            "ecosystem": "golang",
            "name": "k8s.io/client-go",
            "version": "v0.29.0",
            "purl": "pkg:golang/k8s.io/client-go@v0.29.0",
        }
    )
    assert result["producer"] == "The Kubernetes Authors"
    assert result["source"] == cp.SOURCE_NAMESPACE_REGISTRY


def test_golang_forge_host_alone_is_not_a_producer():
    """github.com hosts; it does not create, define or identify anything."""
    result = cp.resolve_producer(
        {"ecosystem": "golang", "name": "github.com/some-person/some-lib", "version": "v1.0.0"}
    )
    assert cp.is_unknown(result)
    assert result["reason"] == cp.REASON_FORGE_HOST


def test_golang_registered_forge_account_does_identify_a_producer():
    result = cp.resolve_producer(
        {"ecosystem": "golang", "name": "github.com/prometheus/client_golang", "version": "v1.19.0"}
    )
    assert result["producer"] == "The Prometheus Authors"


def test_golang_resolves_from_the_purl_when_the_ecosystem_key_is_absent():
    result = cp.resolve_producer({"name": "go.uber.org/zap", "purl": "pkg:golang/go.uber.org/zap@v1.27.0"})
    assert result["producer"] == "Uber Technologies, Inc."


# ---------------------------------------------------------------------------
# cargo — the crate's published authors (what crates.io renders as the owner)
# ---------------------------------------------------------------------------


def test_cargo_producer_comes_from_the_vendored_crate_manifest(tmp_path):
    _write(
        tmp_path / "vendor" / "alpha" / "Cargo.toml",
        f"""
        [package]
        name = "alpha"
        version = "1.0.0"
        authors = ["{ACME} <dev@acme.example>", "Someone Else"]
        """,
    )

    result = cp.resolve_producer(
        {"ecosystem": "cargo", "name": "alpha", "version": "1.0.0", "purl": "pkg:cargo/alpha@1.0.0"},
        project_dir=tmp_path,
    )
    assert result["producer"] == ACME
    assert result["source"] == cp.SOURCE_CARGO_MANIFEST


def test_cargo_reads_the_registry_source_cache_when_cargo_home_is_set(tmp_path):
    cargo_home = tmp_path / "cargo-home"
    _write(
        cargo_home / "registry" / "src" / "index.crates.io-abc123" / "beta-2.0.0" / "Cargo.toml",
        """
        [package]
        name = "beta"
        version = "2.0.0"
        authors = ["Beta Systems GmbH"]
        """,
    )

    result = cp.resolve_producer(
        {"ecosystem": "cargo", "name": "beta", "version": "2.0.0"},
        project_dir=tmp_path,
        env={"CARGO_HOME": str(cargo_home)},
    )
    assert result["producer"] == "Beta Systems GmbH"


def test_cargo_without_a_manifest_on_disk_is_unknown_provenance(tmp_path):
    result = cp.resolve_producer(
        {"ecosystem": "cargo", "name": "absent", "version": "1.0.0"},
        project_dir=tmp_path,
        env={},
    )
    assert cp.is_unknown(result)
    assert result["reason"] == cp.REASON_METADATA_NOT_FOUND


# ---------------------------------------------------------------------------
# nuget — the package's .nuspec <authors>
# ---------------------------------------------------------------------------


def test_nuget_producer_comes_from_the_nuspec_authors(tmp_path):
    _write(
        tmp_path / "packages" / "Acme.Core.1.2.3" / "Acme.Core.nuspec",
        f"""
        <package><metadata>
          <id>Acme.Core</id><version>1.2.3</version><authors>{ACME}</authors>
        </metadata></package>
        """,
    )

    result = cp.resolve_producer(
        {"ecosystem": "nuget", "name": "Acme.Core", "version": "1.2.3", "purl": "pkg:nuget/Acme.Core@1.2.3"},
        project_dir=tmp_path,
    )
    assert result["producer"] == ACME
    assert result["source"] == cp.SOURCE_NUGET_NUSPEC


def test_nuget_reads_the_global_packages_folder(tmp_path):
    packages = tmp_path / "global-packages"
    _write(
        packages / "acme.core" / "1.2.3" / "acme.core.nuspec",
        "<package><metadata><authors>Acme Widgets, Inc.</authors></metadata></package>",
    )

    result = cp.resolve_producer(
        {"ecosystem": "nuget", "name": "Acme.Core", "version": "1.2.3"},
        project_dir=tmp_path,
        env={"NUGET_PACKAGES": str(packages)},
    )
    assert result["producer"] == ACME


def test_nuget_without_a_nuspec_is_unknown_provenance(tmp_path):
    result = cp.resolve_producer(
        {"ecosystem": "nuget", "name": "Absent", "version": "1.0.0"}, project_dir=tmp_path, env={}
    )
    assert cp.is_unknown(result)


# ---------------------------------------------------------------------------
# the producer is never the namespace
# ---------------------------------------------------------------------------


def test_producer_is_never_populated_from_group(tmp_path):
    """The acceptance criterion, stated directly.

    A component with a populated `group`, no metadata on disk and no registry
    entry must come back unknown. `group` is a coordinate; using it would state
    that an organization named `com.unheard-of-vendor` produced the component.
    """
    component = {
        "ecosystem": "maven",
        "group": "com.unheard-of-vendor",
        "name": "thing",
        "version": "1.0",
        "purl": "pkg:maven/com.unheard-of-vendor/thing@1.0",
    }
    result = cp.resolve_producer(component, project_dir=tmp_path)
    assert result["producer"] == cp.UNKNOWN
    assert result["producer"] != component["group"]


def test_metadata_that_merely_echoes_the_namespace_is_rejected(tmp_path):
    """`authors = ["com.acme"]` names a coordinate, not an organization."""
    _write(
        tmp_path / "vendor" / "widget" / "Cargo.toml",
        """
        [package]
        name = "widget"
        authors = ["com.acme"]
        """,
    )

    result = cp.resolve_producer(
        {"ecosystem": "cargo", "group": "com.acme", "name": "widget", "version": "1.0.0"},
        project_dir=tmp_path,
    )
    assert cp.is_unknown(result)
    assert result["reason"] == cp.REASON_NAMESPACE_ECHO


def test_the_module_never_builds_a_producer_out_of_group():
    """Belt and braces, read out of the AST rather than out of prose.

    Every producer this module states is constructed by `_known_producer`, whose
    first argument is the name. That argument may never be derived from `group`.
    `group` is allowed to appear elsewhere — it locates a POM on disk, and
    `_reject_namespace_echo` compares against it — and it appears in the
    *evidence* string of a registry hit, which records what was mapped rather
    than what was emitted.
    """
    import ast

    source = (REPO_ROOT / "tools" / "compliance" / "component_producer.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "_known_producer" or not node.args:
            continue
        rendered = ast.dump(node.args[0])
        assert "group" not in rendered.lower(), f"producer name derived from group: {ast.unparse(node.args[0])}"


# ---------------------------------------------------------------------------
# name normalization — exactly one organization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (f"{ACME} <dev@acme.example> (https://acme.example)", ACME),
        ("  Acme   Widgets,   Inc.  ", "Acme Widgets, Inc."),
        ("Alice Smith, Bob Jones", "Alice Smith"),
        ("Alice Smith; Bob Jones", "Alice Smith"),
        ("Eclipse Foundation AISBL", "Eclipse Foundation AISBL"),
        ("UNKNOWN", None),
        ("n/a", None),
        ("", None),
        (None, None),
    ],
)
def test_normalization_yields_exactly_one_organization(raw, expected):
    assert cp.normalize_producer_name(raw, _registry()["placeholders"]) == expected


def test_a_legal_suffix_after_a_comma_is_not_a_second_author():
    assert cp.normalize_producer_name("Acme Widgets, LLC", _registry()["placeholders"]) == "Acme Widgets, LLC"


# ---------------------------------------------------------------------------
# CycloneDX rendering
# ---------------------------------------------------------------------------


def test_cyclonedx_below_1_6_carries_the_producer_as_supplier():
    result = cp._known_producer(ACME, cp.SOURCE_NPM_PACKAGE_JSON)
    component = cp.apply_producer_to_cyclonedx({}, result, "1.4")
    assert component["supplier"] == {"name": ACME}
    assert "manufacturer" not in component


def test_cyclonedx_1_6_and_above_carries_it_as_manufacturer():
    """1.6 added the field whose definition IS Component Producer."""
    result = cp._known_producer(ACME, cp.SOURCE_NPM_PACKAGE_JSON)
    for spec in ("1.6", "1.7"):
        component = cp.apply_producer_to_cyclonedx({}, result, spec)
        assert component["manufacturer"] == {"name": ACME}
        assert "supplier" not in component


def test_unknown_provenance_writes_no_organizational_entity():
    """An entity named "unknown" would read as an organization called that."""
    result = cp.unknown_producer(cp.REASON_FORGE_HOST, "github.com")
    component = cp.apply_producer_to_cyclonedx({}, result, "1.6")
    assert component == {}


def test_properties_always_state_the_element_either_way():
    known = {p["name"]: p["value"] for p in cp.producer_properties(cp._known_producer(ACME, "src"))}
    assert known[cp.PROPERTY_PRODUCER] == ACME
    assert known[cp.PROPERTY_PROVENANCE] == cp.KNOWN

    unknown = {
        p["name"]: p["value"] for p in cp.producer_properties(cp.unknown_producer(cp.REASON_FORGE_HOST, "github.com"))
    }
    assert unknown[cp.PROPERTY_PRODUCER] == cp.UNKNOWN
    assert unknown[cp.PROPERTY_PROVENANCE] == cp.UNKNOWN
    assert unknown[cp.PROPERTY_PRODUCER_UNKNOWN_REASON] == cp.REASON_FORGE_HOST


def test_properties_round_trip():
    original = cp._known_producer(ACME, cp.SOURCE_MAVEN_POM_ORGANIZATION, "some/pom.xml")
    component = {"properties": cp.producer_properties(original)}
    assert cp.read_producer_from_cyclonedx(component)["producer"] == ACME

    marker = cp.unknown_producer(cp.REASON_FORGE_HOST)
    component = {"properties": cp.producer_properties(marker)}
    assert cp.is_unknown(cp.read_producer_from_cyclonedx(component))


# ---------------------------------------------------------------------------
# the validator
# ---------------------------------------------------------------------------


def test_validator_accepts_a_document_where_every_component_states_the_element():
    sbom = {
        "metadata": {"component": {"name": "target", "properties": cp.producer_properties(cp._known_producer(ACME, "s"))}},
        "components": [
            {"name": "a", "properties": cp.producer_properties(cp._known_producer("Org A", "s"))},
            {"name": "b", "properties": cp.producer_properties(cp.unknown_producer(cp.REASON_FORGE_HOST))},
        ],
    }
    errors, summary = cp.validate_sbom_producers(sbom)
    assert errors == []
    assert summary == {"component_count": 3, "producers_known": 2, "producers_unknown": 1}


def test_validator_rejects_a_silent_component():
    errors, _ = cp.validate_sbom_producers({"components": [{"name": "a"}]})
    assert any("no icdev:component-producer property" in e for e in errors)


def test_validator_rejects_a_producer_copied_from_group():
    sbom = {
        "components": [
            {
                "name": "commons-lang3",
                "group": "org.apache.commons",
                "properties": cp.producer_properties(cp._known_producer("org.apache.commons", "s")),
            }
        ]
    }
    errors, _ = cp.validate_sbom_producers(sbom)
    assert any("populated from the component's group" in e for e in errors)


def test_validator_rejects_an_unknown_with_no_reason():
    sbom = {
        "components": [
            {
                "name": "a",
                "properties": [
                    {"name": cp.PROPERTY_PRODUCER, "value": cp.UNKNOWN},
                    {"name": cp.PROPERTY_PROVENANCE, "value": cp.UNKNOWN},
                ],
            }
        ]
    }
    errors, _ = cp.validate_sbom_producers(sbom)
    assert any("no icdev:component-producer-unknown-reason" in e for e in errors)


# ---------------------------------------------------------------------------
# the target component
# ---------------------------------------------------------------------------


def test_target_producer_prefers_the_operator_declaration(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"name": "app", "author": "Whoever Wrote The Manifest"}))

    result = cp.resolve_project_producer(
        {"name": "App"}, project_dir=tmp_path, env={cp.ENV_PROJECT_PRODUCER: ACME}
    )
    assert result["producer"] == ACME
    assert result["source"] == cp.SOURCE_OPERATOR


def test_target_producer_falls_back_to_the_project_manifest(tmp_path):
    _write(
        tmp_path / "pyproject.toml",
        f"""
        [project]
        name = "app"
        authors = [{{name = "{ACME}", email = "dev@acme.example"}}]
        """,
    )

    result = cp.resolve_project_producer({"name": "App"}, project_dir=tmp_path, env={})
    assert result["producer"] == ACME
    assert result["source"] == cp.SOURCE_PROJECT_MANIFEST


def test_target_producer_with_no_evidence_says_how_to_state_it(tmp_path):
    result = cp.resolve_project_producer({"name": "App"}, project_dir=tmp_path, env={})
    assert cp.is_unknown(result)
    assert cp.ENV_PROJECT_PRODUCER in result["evidence"]


# ---------------------------------------------------------------------------
# resolution is offline and never executes what it reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        Path("tools") / "compliance" / "component_producer.py",
        Path("icdev") / "tools" / "compliance" / "component_producer.py",
    ],
    ids=["root", "mirror"],
)
def test_producer_resolution_never_executes_what_it_parses(path):
    """Pins the `bypass-documented` decision in docs/security/sandbox-coverage.md.

    Package metadata is third-party content by definition. It may only be
    *parsed* — no `exec`, no `eval`, no `subprocess`, no `pickle`, no network,
    and no `yaml.load` (only `safe_load`, and only for ICDEV's own registry).
    """
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    banned = ["subprocess", "os.system", "pickle", "eval(", "exec(", "__import__(", "urllib", "requests."]
    found = [token for token in banned if token in source]
    assert not found, f"{path.as_posix()} gained an execution or network path: {found}"

    assert "yaml.load(" not in source.replace("yaml.safe_load(", "")


@pytest.mark.parametrize(
    "path",
    [
        Path("args") / "sbom_producer_registry.yaml",
        Path("icdev") / "args" / "sbom_producer_registry.yaml",
    ],
    ids=["root", "mirror"],
)
def test_the_namespace_registry_ships_in_both_trees(path):
    """A pip-installed ICDEV resolves the registry relative to `icdev/`."""
    registry = cp.load_producer_registry(REPO_ROOT / path)
    assert registry["domains"], f"{path.as_posix()} loaded no domain mappings"
    assert "github.com" in registry["forges"]


def test_a_missing_registry_degrades_to_unknown_rather_than_raising(tmp_path):
    empty = cp.load_producer_registry(tmp_path / "does-not-exist.yaml")
    assert empty == {"domains": {}, "forges": set(), "namespaces": {}, "placeholders": set()}

    result = cp.resolve_producer(
        {"ecosystem": "golang", "name": "k8s.io/client-go", "version": "v0.29.0"}, registry=empty
    )
    assert cp.is_unknown(result)


def test_the_context_reads_each_package_once(tmp_path):
    """A resolved npm tree repeats packages; the metadata read must not."""
    _write(
        tmp_path / "node_modules" / "alpha" / "package.json",
        json.dumps({"name": "alpha", "author": ACME}),
    )
    context = cp.ProducerContext(project_dir=tmp_path)

    reads = []
    original = cp._read_json
    cp._read_json = lambda path: (reads.append(path), original(path))[1]
    try:
        for _ in range(25):
            assert context.resolve({"ecosystem": "npm", "name": "alpha", "version": "1.0.0"})["producer"] == ACME
    finally:
        cp._read_json = original

    assert len(reads) == 1, f"package.json was read {len(reads)} times, not once"


# ---------------------------------------------------------------------------
# end to end through the generator
# ---------------------------------------------------------------------------


def _seed_project(db_path, project_id, directory):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        (project_id, "Producer Fixture", "api", str(directory)),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def mixed_fixture_project(tmp_path):
    """A project whose components split across known and unknown provenance."""
    project = tmp_path / "fixture-project"
    _write(
        project / "package.json",
        json.dumps({"name": "app", "dependencies": {"alpha": "^1.0.0"}}),
    )
    _write(
        project / "package-lock.json",
        json.dumps(
            {
                "name": "app",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "app", "dependencies": {"alpha": "^1.0.0"}},
                    "node_modules/alpha": {"version": "1.0.0"},
                    "node_modules/silent": {"version": "9.9.9"},
                },
            }
        ),
    )
    _write(project / "node_modules" / "alpha" / "package.json", json.dumps({"name": "alpha", "author": ACME}))
    _write(project / "node_modules" / "silent" / "package.json", json.dumps({"name": "silent"}))
    _write(
        project / "go.mod",
        """
        module example.test/app

        go 1.21

        require (
            k8s.io/client-go v0.29.0
            github.com/some-person/some-lib v1.0.0 // indirect
        )
        """,
    )
    return project


def test_every_component_in_a_generated_sbom_states_its_producer(icdev_db, mixed_fixture_project, tmp_path):
    """The acceptance criterion, end to end.

    Every component — including the document's own target component — carries
    either a Component Producer or an explicit unknown-provenance marker.
    """
    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "fld02-mixed", mixed_fixture_project)
    out_file = tmp_path / "producers.cdx.json"

    sbom_generator.generate_sbom(project_id="fld02-mixed", output_path=str(out_file), db_path=icdev_db)
    sbom = json.loads(out_file.read_text(encoding="utf-8"))

    errors, summary = cp.validate_sbom_producers(sbom)
    assert errors == [], errors
    assert summary["producers_known"] >= 2, summary
    assert summary["producers_unknown"] >= 2, summary

    by_name = {c["name"]: c for c in sbom["components"]}
    alpha = {p["name"]: p["value"] for p in by_name["alpha"]["properties"]}
    assert alpha[cp.PROPERTY_PRODUCER] == ACME
    assert by_name["alpha"]["supplier"] == {"name": ACME}  # default spec version is 1.4

    client_go = {p["name"]: p["value"] for p in by_name["k8s.io/client-go"]["properties"]}
    assert client_go[cp.PROPERTY_PRODUCER] == "The Kubernetes Authors"

    unresolvable = {p["name"]: p["value"] for p in by_name["github.com/some-person/some-lib"]["properties"]}
    assert unresolvable[cp.PROPERTY_PRODUCER] == cp.UNKNOWN
    assert unresolvable[cp.PROPERTY_PROVENANCE] == cp.UNKNOWN
    assert unresolvable[cp.PROPERTY_PRODUCER_UNKNOWN_REASON] == cp.REASON_FORGE_HOST


def test_no_generated_component_takes_its_producer_from_group(icdev_db, mixed_fixture_project, tmp_path):
    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "fld02-nogroup", mixed_fixture_project)
    out_file = tmp_path / "nogroup.cdx.json"

    sbom_generator.generate_sbom(project_id="fld02-nogroup", output_path=str(out_file), db_path=icdev_db)
    sbom = json.loads(out_file.read_text(encoding="utf-8"))

    for component in sbom["components"]:
        group = component.get("group")
        if not group:
            continue
        values = {p["name"]: p["value"] for p in component.get("properties", [])}
        assert values[cp.PROPERTY_PRODUCER].lower() != str(group).lower()
