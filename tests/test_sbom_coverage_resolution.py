#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-cov-01 — the SBOM 2026 **Coverage** element.

The 2026 Minimum Elements replaced *Depth* with **Coverage**: every component
that makes up the target software, **including transitive dependencies**, with
**no minimum depth**. ICDEV previously parsed declared manifests, so it captured
direct dependencies only for every ecosystem except npm.

Each fixture below is a project whose transitive tree is known by construction —
a three-level ``alpha -> beta -> gamma`` chain where a declared-manifest parser
would see only ``alpha``. The tests assert that all three land in the resolved
set, that the *incomplete* case says so out loud, and that instances differing in
metadata survive as separate components.
"""

import json
import textwrap
import time
from pathlib import Path

import pytest

from tools.compliance import dependency_resolver as dr

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Agreed budget for the Coverage work (sbx-cov-01). Resolution is lockfile
#: parsing only — no subprocess, no network — so a 1000-package lockfile is the
#: realistic worst case for a single ecosystem and must stay well inside this.
RESOLUTION_BUDGET_SECONDS = 5.0

TRANSITIVE_CHAIN = {"alpha", "beta", "gamma"}


def _names(result):
    return {c["name"] for c in result["components"]}


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


def _edges_of(result, name):
    """The dependency keys of the (single) component called ``name``."""
    matches = [c for c in result["components"] if c["name"] == name]
    assert len(matches) == 1, f"expected exactly one {name}, got {len(matches)}"
    return matches[0]["dependencies"]


# ---------------------------------------------------------------------------
# python
# ---------------------------------------------------------------------------


def test_python_poetry_lock_yields_the_transitive_chain(tmp_path):
    _write(
        tmp_path / "poetry.lock",
        """
        [[package]]
        name = "alpha"
        version = "1.0.0"

        [package.dependencies]
        beta = ">=2.0"

        [[package]]
        name = "beta"
        version = "2.0.0"

        [package.dependencies]
        gamma = ">=3.0"

        [[package]]
        name = "gamma"
        version = "3.0.0"
        """,
    )
    # requirements.txt names only alpha -- the declared view a recipient must not
    # be handed as if it were complete.
    _write(tmp_path / "requirements.txt", "alpha==1.0.0\n")

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN
    assert _edges_of(result, "alpha") == ["python|beta"]
    assert _edges_of(result, "beta") == ["python|gamma"]


def test_python_uv_lock_dependency_table_array_form(tmp_path):
    _write(
        tmp_path / "uv.lock",
        """
        version = 1

        [[package]]
        name = "alpha"
        version = "1.0.0"

        [[package.dependencies]]
        name = "beta"

        [[package]]
        name = "beta"
        version = "2.0.0"

        [[package.dependencies]]
        name = "gamma"

        [[package]]
        name = "gamma"
        version = "3.0.0"
        """,
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN
    assert _edges_of(result, "beta") == ["python|gamma"]


def test_python_pipfile_lock_is_complete_but_edgeless(tmp_path):
    _write(
        tmp_path / "Pipfile.lock",
        json.dumps(
            {
                "default": {
                    "alpha": {"version": "==1.0.0"},
                    "beta": {"version": "==2.0.0"},
                    "gamma": {"version": "==3.0.0"},
                },
                "develop": {"delta": {"version": "==4.0.0"}},
            }
        ),
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN | {"delta"}
    # develop-section packages keep an optional scope.
    scopes = {c["name"]: c["scope"] for c in result["components"]}
    assert scopes["delta"] == "optional"
    assert scopes["alpha"] == "required"
    # Pipfile.lock records no edges, and the reason has to say so rather than
    # leaving a reader to infer that nothing depends on anything.
    ecosystem = result["ecosystems"][0]
    assert "no inter-package edges" in ecosystem["reason"]


def test_python_installed_environment_is_read_via_importlib_metadata(tmp_path):
    """`importlib.metadata` over the target environment, not requirements.txt."""
    site_packages = tmp_path / ".venv" / "Lib" / "site-packages"
    for name, version, requires in (
        ("alpha", "1.0.0", ["beta (>=2.0)"]),
        ("beta", "2.0.0", ["gamma (>=3.0)"]),
        ("gamma", "3.0.0", []),
    ):
        metadata = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
        metadata += [f"Requires-Dist: {r}" for r in requires]
        _write(site_packages / f"{name}-{version}.dist-info" / "METADATA", "\n".join(metadata) + "\n")

    _write(tmp_path / "requirements.txt", "alpha\n")

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert result["ecosystems"][0]["method"].startswith("importlib.metadata")
    assert _names(result) == TRANSITIVE_CHAIN
    assert _edges_of(result, "alpha") == ["python|beta"]


def test_python_without_a_lockfile_degrades_and_says_so(tmp_path):
    _write(tmp_path / "requirements.txt", "alpha==1.0.0\n")

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert result["ecosystems"][0]["complete"] is False
    assert "Transitive dependencies are absent" in result["ecosystems"][0]["reason"]
    assert "INCOMPLETE COVERAGE" in result["coverage"]["statement"]


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------


def _package_lock_v3():
    """alpha -> (beta, gamma@1.5 nested); beta -> gamma@3.0 hoisted.

    The nested ``gamma`` is npm's answer to two dependents needing incompatible
    ranges. The old parser discarded every nested entry, which dropped a real
    installed component out of the SBOM.
    """
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


def test_npm_package_lock_keeps_nested_instances(tmp_path):
    _write(tmp_path / "package-lock.json", json.dumps(_package_lock_v3()))
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"alpha": "^1.0.0"}}))

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert len(result["components"]) == 4, "the nested gamma instance was dropped"

    versions = sorted(c["version"] for c in result["components"] if c["name"] == "gamma")
    assert versions == ["1.5.0", "3.0.0"]

    # npm resolution order: alpha's gamma is the nested one, beta's is hoisted.
    assert "npm|node_modules/alpha/node_modules/gamma" in _edges_of(result, "alpha")
    assert _edges_of(result, "beta") == ["npm|node_modules/gamma"]


def test_npm_package_lock_v1_recurses_into_the_tree(tmp_path):
    _write(
        tmp_path / "package-lock.json",
        json.dumps(
            {
                "lockfileVersion": 1,
                "dependencies": {
                    "alpha": {
                        "version": "1.0.0",
                        "requires": {"beta": "^2.0.0"},
                        "dependencies": {
                            "beta": {
                                "version": "2.0.0",
                                "requires": {"gamma": "^3.0.0"},
                                "dependencies": {"gamma": {"version": "3.0.0"}},
                            }
                        },
                    }
                },
            }
        ),
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN
    assert _edges_of(result, "beta") == ["npm|node_modules/alpha/node_modules/beta/node_modules/gamma"]


def test_npm_yarn_lock_v1(tmp_path):
    _write(
        tmp_path / "yarn.lock",
        """
        # THIS IS AN AUTOGENERATED FILE.

        alpha@^1.0.0:
          version "1.0.0"
          resolved "https://registry.yarnpkg.com/alpha/-/alpha-1.0.0.tgz"
          dependencies:
            beta "^2.0.0"

        beta@^2.0.0:
          version "2.0.0"
          resolved "https://registry.yarnpkg.com/beta/-/beta-2.0.0.tgz"
          dependencies:
            gamma "^3.0.0"

        gamma@^3.0.0:
          version "3.0.0"
          resolved "https://registry.yarnpkg.com/gamma/-/gamma-3.0.0.tgz"
        """,
    )
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"alpha": "^1.0.0"}}))

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN
    assert _edges_of(result, "alpha") == ["npm|yarn:1"]


def test_npm_yarn_berry_lock(tmp_path):
    _write(
        tmp_path / "yarn.lock",
        """
        __metadata:
          version: 6

        "alpha@npm:^1.0.0":
          version: 1.0.0
          dependencies:
            beta: "npm:^2.0.0"

        "beta@npm:^2.0.0":
          version: 2.0.0
          dependencies:
            gamma: "npm:^3.0.0"

        "gamma@npm:^3.0.0":
          version: 3.0.0
        """,
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN


def test_npm_without_a_lockfile_degrades_and_says_so(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"alpha": "^1.0.0"}}))

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "no npm lockfile" in result["ecosystems"][0]["reason"]


# ---------------------------------------------------------------------------
# golang
# ---------------------------------------------------------------------------


def test_golang_go_mod_117_pruned_graph_includes_indirect_modules(tmp_path):
    _write(
        tmp_path / "go.mod",
        """
        module example.com/fixture

        go 1.21

        require example.com/alpha v1.0.0

        require (
            example.com/beta v2.0.0 // indirect
            example.com/gamma v3.0.0 // indirect
        )
        """,
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == {"example.com/alpha", "example.com/beta", "example.com/gamma"}
    directness = {c["name"]: c["direct"] for c in result["components"]}
    assert directness["example.com/alpha"] is True
    assert directness["example.com/beta"] is False


def test_golang_falls_back_to_go_sum_on_an_unpruned_module(tmp_path):
    _write(
        tmp_path / "go.mod",
        """
        module example.com/fixture

        go 1.15

        require example.com/alpha v1.0.0
        """,
    )
    _write(
        tmp_path / "go.sum",
        """
        example.com/alpha v1.0.0 h1:aaa=
        example.com/alpha v1.0.0/go.mod h1:aaa=
        example.com/beta v2.0.0 h1:bbb=
        example.com/beta v2.0.0/go.mod h1:bbb=
        example.com/gamma v3.0.0 h1:ccc=
        example.com/gamma v3.0.0/go.mod h1:ccc=
        """,
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert result["ecosystems"][0]["method"] == "go.sum"
    assert len(result["components"]) == 3, "the /go.mod hash lines were counted as components"
    assert "superset" in result["ecosystems"][0]["reason"]


def test_golang_without_go_sum_on_an_unpruned_module_degrades(tmp_path):
    _write(
        tmp_path / "go.mod",
        """
        module example.com/fixture

        go 1.15

        require example.com/alpha v1.0.0
        """,
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "Transitive modules are absent" in result["ecosystems"][0]["reason"]


# ---------------------------------------------------------------------------
# cargo
# ---------------------------------------------------------------------------


def test_cargo_lock_yields_the_transitive_chain(tmp_path):
    _write(
        tmp_path / "Cargo.lock",
        """
        version = 3

        [[package]]
        name = "alpha"
        version = "1.0.0"
        dependencies = ["beta"]

        [[package]]
        name = "beta"
        version = "2.0.0"
        dependencies = ["gamma 3.0.0"]

        [[package]]
        name = "gamma"
        version = "3.0.0"
        """,
    )
    _write(tmp_path / "Cargo.toml", '[dependencies]\nalpha = "1.0"\n')

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN
    # "gamma 3.0.0" carries a version; only the leading token is the crate name.
    assert _edges_of(result, "beta") == ["cargo|gamma"]


def test_cargo_without_a_lockfile_degrades_and_says_so(tmp_path):
    _write(tmp_path / "Cargo.toml", '[dependencies]\nalpha = "1.0"\n')

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "no Cargo.lock" in result["ecosystems"][0]["reason"]


# ---------------------------------------------------------------------------
# maven
# ---------------------------------------------------------------------------


def test_maven_dependency_list_output_yields_the_transitive_chain(tmp_path):
    _write(tmp_path / "pom.xml", "<project><dependencies></dependencies></project>")
    _write(
        tmp_path / "target" / "dependency-list.txt",
        """
        The following files have been resolved:
           com.example:alpha:jar:1.0.0:compile
           com.example:beta:jar:2.0.0:compile
           com.example:gamma:jar:3.0.0:runtime
           com.example:delta:jar:4.0.0:test
        """,
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN | {"delta"}
    scopes = {c["name"]: c["scope"] for c in result["components"]}
    assert scopes["delta"] == "optional"
    assert scopes["gamma"] == "required"


def test_maven_without_resolution_output_degrades_and_says_so(tmp_path):
    _write(tmp_path / "pom.xml", "<project><dependencies></dependencies></project>")

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "mvn dependency:list" in result["ecosystems"][0]["reason"]
    assert "transitive artifacts are absent" in result["ecosystems"][0]["reason"]


# ---------------------------------------------------------------------------
# gradle
# ---------------------------------------------------------------------------


def test_gradle_lockfile_yields_the_transitive_chain(tmp_path):
    _write(tmp_path / "build.gradle", "dependencies { implementation 'com.example:alpha:1.0.0' }")
    _write(
        tmp_path / "gradle.lockfile",
        """
        # This is a Gradle generated file for dependency locking.
        com.example:alpha:1.0.0=compileClasspath,runtimeClasspath
        com.example:beta:2.0.0=runtimeClasspath
        com.example:gamma:3.0.0=runtimeClasspath
        empty=annotationProcessor
        """,
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN


def test_gradle_without_a_lockfile_degrades_and_says_so(tmp_path):
    _write(tmp_path / "build.gradle", "dependencies { implementation 'com.example:alpha:1.0.0' }")

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "gradle.lockfile" in result["ecosystems"][0]["reason"]


# ---------------------------------------------------------------------------
# nuget
# ---------------------------------------------------------------------------


def test_nuget_project_assets_yields_the_transitive_chain(tmp_path):
    _write(
        tmp_path / "obj" / "project.assets.json",
        json.dumps(
            {
                "targets": {
                    "net8.0": {
                        "alpha/1.0.0": {"type": "package", "dependencies": {"beta": "2.0.0"}},
                        "beta/2.0.0": {"type": "package", "dependencies": {"gamma": "3.0.0"}},
                        "gamma/3.0.0": {"type": "package"},
                    }
                }
            }
        ),
    )
    _write(tmp_path / "Fixture.csproj", '<Project><ItemGroup></ItemGroup></Project>')

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN
    assert _edges_of(result, "alpha") == ["nuget|beta"]


def test_nuget_packages_lock_yields_the_transitive_chain(tmp_path):
    _write(
        tmp_path / "packages.lock.json",
        json.dumps(
            {
                "dependencies": {
                    "net8.0": {
                        "alpha": {"type": "Direct", "resolved": "1.0.0", "dependencies": {"beta": "2.0.0"}},
                        "beta": {"type": "Transitive", "resolved": "2.0.0"},
                        "gamma": {"type": "Transitive", "resolved": "3.0.0"},
                    }
                }
            }
        ),
    )

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_COMPLETE
    assert _names(result) == TRANSITIVE_CHAIN
    directness = {c["name"]: c["direct"] for c in result["components"]}
    assert directness["alpha"] is True
    assert directness["beta"] is False


def test_nuget_without_restore_output_degrades_and_says_so(tmp_path):
    _write(tmp_path / "Fixture.csproj", '<Project><ItemGroup></ItemGroup></Project>')

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "Transitive packages are absent" in result["ecosystems"][0]["reason"]


# ---------------------------------------------------------------------------
# aggregation and honesty
# ---------------------------------------------------------------------------


def test_one_unresolved_ecosystem_makes_the_whole_sbom_incomplete(tmp_path):
    """npm resolves, maven cannot — the document must not claim completeness."""
    _write(tmp_path / "package-lock.json", json.dumps(_package_lock_v3()))
    _write(tmp_path / "pom.xml", "<project><dependencies></dependencies></project>")

    result = dr.resolve_project(tmp_path)
    coverage = result["coverage"]

    assert coverage["status"] == dr.COVERAGE_INCOMPLETE
    assert [e["ecosystem"] for e in coverage["resolved"]] == ["npm"]
    assert [e["ecosystem"] for e in coverage["unresolved"]] == ["maven"]
    assert "does NOT establish" in coverage["statement"]


def test_a_project_with_no_manifests_reports_unknown_not_complete(tmp_path):
    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_UNKNOWN
    assert result["components"] == []
    assert "UNKNOWN COVERAGE" in result["coverage"]["statement"]


def test_a_missing_project_directory_reports_unknown(tmp_path):
    result = dr.resolve_project(tmp_path / "does-not-exist")

    assert result["coverage"]["status"] == dr.COVERAGE_UNKNOWN


def test_a_corrupt_lockfile_degrades_rather_than_aborting_the_sbom(tmp_path):
    _write(tmp_path / "Cargo.lock", "this is not valid toml {{{")
    _write(tmp_path / "Cargo.toml", '[dependencies]\nalpha = "1.0"\n')

    result = dr.resolve_project(tmp_path)

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "could not be parsed" in result["ecosystems"][0]["reason"]


def test_declared_parsers_fill_in_the_unresolved_ecosystem(tmp_path):
    """The declared fallback must still list what it *can* see."""
    _write(tmp_path / "requirements.txt", "alpha==1.0.0\nbeta==2.0.0\n")

    calls = []

    def fake_python_parser(project_dir):
        calls.append(project_dir)
        return [
            {"type": "library", "name": "alpha", "version": "1.0.0", "purl": "pkg:pypi/alpha@1.0.0"},
            {"type": "library", "name": "beta", "version": "2.0.0", "purl": "pkg:pypi/beta@2.0.0"},
        ]

    result = dr.resolve_project(tmp_path, declared_parsers={"python": fake_python_parser})

    assert calls == [tmp_path]
    assert _names(result) == {"alpha", "beta"}
    assert all(c["resolution"] == dr.RESOLUTION_DECLARED for c in result["components"])
    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE


def test_a_raising_declared_parser_does_not_abort_resolution(tmp_path):
    _write(tmp_path / "requirements.txt", "alpha==1.0.0\n")

    def exploding_parser(project_dir):
        raise OSError("boom")

    result = dr.resolve_project(tmp_path, declared_parsers={"python": exploding_parser})

    assert result["coverage"]["status"] == dr.COVERAGE_INCOMPLETE
    assert "declared fallback also failed" in result["ecosystems"][0]["reason"]


# ---------------------------------------------------------------------------
# performance
# ---------------------------------------------------------------------------


def test_resolution_stays_inside_the_agreed_time_budget(tmp_path):
    packages = {"": {"name": "big", "version": "1.0.0"}}
    for index in range(1000):
        packages[f"node_modules/pkg{index}"] = {
            "version": f"1.0.{index}",
            "dependencies": {f"pkg{(index + 1) % 1000}": "^1.0.0"},
        }
    _write(tmp_path / "package-lock.json", json.dumps({"lockfileVersion": 3, "packages": packages}))

    started = time.perf_counter()
    result = dr.resolve_project(tmp_path)
    elapsed = time.perf_counter() - started

    assert len(result["components"]) == 1000
    assert elapsed < RESOLUTION_BUDGET_SECONDS, (
        f"resolving a 1000-package lockfile took {elapsed:.2f}s, over the "
        f"{RESOLUTION_BUDGET_SECONDS}s budget agreed for sbx-cov-01"
    )


# ---------------------------------------------------------------------------
# module hygiene
# ---------------------------------------------------------------------------


def test_root_and_mirror_stay_in_sync():
    root = REPO_ROOT / "tools" / "compliance" / "dependency_resolver.py"
    mirror = REPO_ROOT / "icdev" / "tools" / "compliance" / "dependency_resolver.py"
    assert root.read_text(encoding="utf-8") == mirror.read_text(encoding="utf-8"), (
        "tools/compliance/dependency_resolver.py and its icdev/ mirror have diverged -- "
        "author changes in both."
    )


@pytest.mark.parametrize(
    "path",
    [
        Path("tools") / "compliance" / "dependency_resolver.py",
        Path("icdev") / "tools" / "compliance" / "dependency_resolver.py",
    ],
    ids=["root", "mirror"],
)
def test_resolver_never_executes_what_it_parses(path):
    """Pins the `bypass-documented` decision in docs/security/sandbox-coverage.md.

    Lockfiles are attacker-influenced input. The resolver may only *parse* them --
    no `exec`, no `eval`, no `subprocess`, no `pickle`, and no `yaml.load` (which
    instantiates arbitrary Python objects; only `safe_load` is permitted).
    """
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    banned = ["subprocess", "os.system", "pickle", "eval(", "exec(", "__import__("]
    found = [token for token in banned if token in source]
    assert not found, f"{path.as_posix()} gained an execution path: {found}"

    assert "yaml.load(" not in source.replace("yaml.safe_load(", "")
    assert "_yaml.load(" not in source.replace("_yaml.safe_load(", "")


def test_the_superseded_package_lock_parser_is_gone():
    """It skipped nested node_modules, which silently dropped installed components."""
    source = (REPO_ROOT / "tools" / "compliance" / "sbom_generator.py").read_text(encoding="utf-8")
    assert "def _parse_package_lock_json" not in source


# ---------------------------------------------------------------------------
# end to end through the generator
# ---------------------------------------------------------------------------


def _seed_project(db_path, project_id, directory):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        (project_id, "Coverage Fixture", "api", str(directory)),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def npm_fixture_project(tmp_path):
    project = tmp_path / "fixture-project"
    project.mkdir()
    _write(project / "package-lock.json", json.dumps(_package_lock_v3()))
    _write(project / "package.json", json.dumps({"dependencies": {"alpha": "^1.0.0"}}))
    return project


def test_generated_sbom_carries_every_transitive_component(icdev_db, npm_fixture_project, tmp_path):
    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "cov-complete", npm_fixture_project)
    out_file = tmp_path / "complete.cdx.json"

    sbom_generator.generate_sbom(
        project_id="cov-complete", output_path=str(out_file), db_path=icdev_db
    )
    sbom = json.loads(out_file.read_text(encoding="utf-8"))

    names = sorted(f"{c['name']}@{c['version']}" for c in sbom["components"])
    assert names == ["alpha@1.0.0", "beta@2.0.0", "gamma@1.5.0", "gamma@3.0.0"]

    # Both gamma instances are listed separately with distinct bom-refs, so a
    # dependency relationship can point at each one individually.
    refs = [c["bom-ref"] for c in sbom["components"]]
    assert len(refs) == len(set(refs)), "two component instances share a bom-ref"

    coverage = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    assert coverage["icdev:sbom:coverage"] == "complete"
    assert coverage["icdev:sbom:coverage:npm"].startswith("resolved: package-lock.json")
    assert sbom["compositions"] == [{"aggregate": "complete", "assemblies": sorted(refs)}]

    # The private resolution marker must never reach the artifact.
    assert all("_declared" not in c for c in sbom["components"])


def test_generated_sbom_states_incomplete_coverage_when_resolution_is_impossible(
    icdev_db, tmp_path
):
    from tools.compliance import sbom_generator

    project = tmp_path / "maven-project"
    project.mkdir()
    _write(
        project / "pom.xml",
        """
        <project><dependencies>
          <dependency>
            <groupId>com.example</groupId><artifactId>alpha</artifactId><version>1.0.0</version>
          </dependency>
        </dependencies></project>
        """,
    )
    _seed_project(icdev_db, "cov-incomplete", project)
    out_file = tmp_path / "incomplete.cdx.json"

    sbom_generator.generate_sbom(
        project_id="cov-incomplete", output_path=str(out_file), db_path=icdev_db
    )
    sbom = json.loads(out_file.read_text(encoding="utf-8"))

    # The declared dependency is still listed -- degrading must not mean dropping.
    assert [c["name"] for c in sbom["components"]] == ["alpha"]

    properties = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    assert properties["icdev:sbom:coverage"] == "incomplete"
    assert "INCOMPLETE COVERAGE" in properties["icdev:sbom:coverage:statement"]
    assert "does NOT establish" in properties["icdev:sbom:coverage:statement"]
    assert "mvn dependency:list" in properties["icdev:sbom:coverage:maven:reason"]

    assert sbom["compositions"] == [
        {"aggregate": "incomplete", "assemblies": [sbom["components"][0]["bom-ref"]]}
    ]


def test_generated_sbom_for_an_empty_project_is_unknown_not_complete(icdev_db, tmp_path):
    from tools.compliance import sbom_generator

    project = tmp_path / "empty-project"
    project.mkdir()
    _seed_project(icdev_db, "cov-unknown", project)
    out_file = tmp_path / "unknown.cdx.json"

    sbom_generator.generate_sbom(
        project_id="cov-unknown", output_path=str(out_file), db_path=icdev_db
    )
    sbom = json.loads(out_file.read_text(encoding="utf-8"))

    properties = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    assert properties["icdev:sbom:coverage"] == "unknown"
    assert sbom["compositions"][0]["aggregate"] == "unknown"
    assert sbom["components"] == []


def test_generation_time_stays_inside_the_agreed_budget(icdev_db, npm_fixture_project, tmp_path):
    """Resolution added lockfile parsing, not toolchain invocation or network I/O."""
    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "cov-budget", npm_fixture_project)

    started = time.perf_counter()
    sbom_generator.generate_sbom(
        project_id="cov-budget", output_path=str(tmp_path / "budget.cdx.json"), db_path=icdev_db
    )
    elapsed = time.perf_counter() - started

    assert elapsed < RESOLUTION_BUDGET_SECONDS, (
        f"SBOM generation took {elapsed:.2f}s, over the {RESOLUTION_BUDGET_SECONDS}s "
        "budget agreed for sbx-cov-01"
    )
