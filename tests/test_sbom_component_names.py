#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-06 — Component Name alternates and Component Version unknown-marking.

Two field fixes from the 2026 Minimum Elements, and both are about a name or a
version *not being lost*:

* **Component Name** gained one sentence — a data format implementing it "must
  allow multiple entries to capture alternate names". ICDEV emitted exactly one
  name per component, and it was not even the producer's: a Python component was
  lower-cased and its separators rewritten, an npm scope was split off into
  ``group``, and a Maven artifact carried a bare artifactId.
* **Component Version** requires an explicit unknown marker where the producer
  provides no version. ICDEV wrote the literal ``"unspecified"``, or
  ``"managed"`` for a Maven dependency whose version lives in a parent POM.
  Neither is machine-interpretable, and both conflate the two states that
  Explicitly Identifying Unknown Information exists to separate. sbx-prc-01
  removed the literals from the parsers; this closes what was left.

The version half is asserted **per parser**, because "no placeholder anywhere" is
a claim about six independent regex parsers and a whole-file grep cannot tell a
fixed parser from a deleted one.
"""

import json
from pathlib import Path

from tools.compliance import component_names as cnames
from tools.compliance import unknown_information as ui

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Component Version — one test per parser that used to emit a placeholder
# ---------------------------------------------------------------------------


def test_requirements_txt_marks_an_unpinned_dependency_unknown(tmp_path):
    from tools.compliance import sbom_generator

    path = _write(tmp_path / "requirements.txt", "flask==3.0.0\nrequests\n")
    components = {c["name"]: c for c in sbom_generator._parse_requirements_txt(path)}

    assert components["flask"]["version"] == "3.0.0"
    assert components["requests"]["version"] == ui.UNKNOWN
    assert components["requests"]["version_unknown_reason"] == ui.REASON_DECLARED_WITHOUT_VERSION
    # An unpinned dependency must not carry a purl that claims a version.
    assert components["requests"]["purl"] == "pkg:pypi/requests"


def test_pyproject_marks_an_unpinned_dependency_unknown(tmp_path):
    from tools.compliance import sbom_generator

    path = _write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["flask>=3.0.0", "requests"]\n',
    )
    components = {c["name"]: c for c in sbom_generator._parse_pyproject_toml(path)}

    assert components["flask"]["version"] == "3.0.0"
    assert components["requests"]["version"] == ui.UNKNOWN
    assert components["requests"]["version_unknown_reason"] == ui.REASON_DECLARED_WITHOUT_VERSION
    assert components["requests"]["purl"] == "pkg:pypi/requests"


def test_package_json_marks_a_wildcard_range_unknown(tmp_path):
    from tools.compliance import sbom_generator

    path = _write(
        tmp_path / "package.json",
        json.dumps({"dependencies": {"react": "^18.2.0", "left-pad": "*"}}),
    )
    components = {c["name"]: c for c in sbom_generator._parse_package_json(path)}

    assert components["react"]["version"] == "18.2.0"
    # `*` is not a version. It used to become the literal "unspecified", which
    # said neither "unknown to us" nor "we are not telling you".
    assert components["left-pad"]["version"] == ui.UNKNOWN
    assert components["left-pad"]["version_unknown_reason"] == ui.REASON_DECLARED_WITHOUT_VERSION
    assert components["left-pad"]["purl"] == "pkg:npm/left-pad"


def test_cargo_toml_marks_both_declaration_forms_unknown(tmp_path):
    from tools.compliance import sbom_generator

    path = _write(
        tmp_path / "Cargo.toml",
        "[dependencies]\n"
        'serde = "1.0.195"\n'
        'bare = ""\n'
        'tokio = { version = "1.35", features = ["full"] }\n'
        'pathdep = { path = "../local" }\n',
    )
    components = {c["name"]: c for c in sbom_generator._parse_cargo_toml(path)}

    assert components["serde"]["version"] == "1.0.195"
    assert components["tokio"]["version"] == "1.35"
    # Both the simple form and the inline-table form had their own placeholder.
    for name in ("bare", "pathdep"):
        assert components[name]["version"] == ui.UNKNOWN, name
        assert components[name]["version_unknown_reason"] == ui.REASON_DECLARED_WITHOUT_VERSION
        assert components[name]["purl"] == f"pkg:cargo/{name}"


def test_pom_xml_distinguishes_a_parent_managed_version(tmp_path):
    from tools.compliance import sbom_generator

    path = _write(
        tmp_path / "pom.xml",
        "<project><dependencies>"
        "<dependency><groupId>com.example</groupId><artifactId>alpha</artifactId>"
        "<version>1.2.3</version></dependency>"
        "<dependency><groupId>com.example</groupId><artifactId>beta</artifactId></dependency>"
        "</dependencies></project>",
    )
    components = {c["name"]: c for c in sbom_generator._parse_pom_xml(path)}

    assert components["alpha"]["version"] == "1.2.3"
    assert components["beta"]["version"] == ui.UNKNOWN
    # "managed" was never an unknown: it meant the version is resolvable from the
    # parent POM's dependencyManagement, which this declared-only parser cannot
    # read. That is its own reason, distinct from "nobody declared one".
    assert components["beta"]["version_unknown_reason"] == ui.REASON_VERSION_MANAGED_BY_PARENT
    assert components["beta"]["purl"] == "pkg:maven/com.example/beta"


def test_csproj_lists_a_versionless_package_reference_rather_than_dropping_it(tmp_path):
    from tools.compliance import sbom_generator

    path = _write(
        tmp_path / "App.csproj",
        "<Project>\n"
        "  <ItemGroup>\n"
        '    <PackageReference Include="Serilog" Version="3.1.1" />\n'
        '    <PackageReference Include="Newtonsoft.Json" />\n'
        '    <PackageReference Include="Dapper">\n'
        "      <Version>2.1.24</Version>\n"
        "    </PackageReference>\n"
        "  </ItemGroup>\n"
        "</Project>\n",
    )
    components = {c["name"]: c for c in sbom_generator._parse_csproj(path)}

    assert components["Serilog"]["version"] == "3.1.1"
    assert components["Dapper"]["version"] == "2.1.24"
    # Central Package Management moves the version to Directory.Packages.props.
    # Every pattern in this parser requires a Version, so the reference used to
    # be dropped outright — the component disappeared from the SBOM rather than
    # appearing with its version stated as unknown.
    assert "Newtonsoft.Json" in components, "a versionless PackageReference was dropped"
    assert components["Newtonsoft.Json"]["version"] == ui.UNKNOWN
    assert (
        components["Newtonsoft.Json"]["version_unknown_reason"]
        == ui.REASON_DECLARED_WITHOUT_VERSION
    )
    assert components["Newtonsoft.Json"]["purl"] == "pkg:nuget/Newtonsoft.Json"


def test_go_mod_states_a_real_version_for_every_module(tmp_path):
    """A parenthesized ``require`` block must not yield a module named ``(``.

    go.mod always carries a version, so this parser has no unknown to mark — but
    it did once emit a component whose *name* was ``(`` and whose *version* was a
    module path, which is both elements wrong at once.
    """
    from tools.compliance import sbom_generator

    path = _write(
        tmp_path / "go.mod",
        "module example.com/app\n\ngo 1.22\n\n"
        "require (\n"
        "\tgithub.com/foo/bar/v2 v2.1.0\n"
        "\tgithub.com/baz/qux v1.0.0 // indirect\n"
        ")\n\n"
        "require golang.org/x/text v0.14.0\n",
    )
    components = sbom_generator._parse_go_mod(path)
    by_name = {c["name"]: c for c in components}

    assert "(" not in by_name
    assert set(by_name) == {
        "github.com/foo/bar/v2",
        "github.com/baz/qux",
        "golang.org/x/text",
    }
    assert by_name["golang.org/x/text"]["version"] == "v0.14.0"
    for component in components:
        assert component["version"].startswith("v"), component
        assert not ui.is_legacy_sentinel(component["version"]), component


def test_no_parser_emits_a_pre_2026_placeholder(tmp_path):
    """Every declared parser at once, over a tree that pins nothing.

    The per-parser tests above prove each one marks the absence correctly. This
    one proves none of them still writes a legacy sentinel into a version field
    by any path — including one a future parser might add.
    """
    from tools.compliance import sbom_generator

    _write(tmp_path / "requirements.txt", "requests\n")
    _write(tmp_path / "pyproject.toml", '[project]\ndependencies = ["urllib3"]\n')
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"left-pad": "*"}}))
    _write(tmp_path / "Cargo.toml", '[dependencies]\nbare = ""\n')
    _write(
        tmp_path / "pom.xml",
        "<project><dependencies><dependency><groupId>g</groupId>"
        "<artifactId>a</artifactId></dependency></dependencies></project>",
    )
    _write(tmp_path / "App.csproj", '<Project><PackageReference Include="Nuget.Pkg" /></Project>')

    components = []
    for parser, filename in (
        (sbom_generator._parse_requirements_txt, "requirements.txt"),
        (sbom_generator._parse_pyproject_toml, "pyproject.toml"),
        (sbom_generator._parse_package_json, "package.json"),
        (sbom_generator._parse_cargo_toml, "Cargo.toml"),
        (sbom_generator._parse_pom_xml, "pom.xml"),
        (sbom_generator._parse_csproj, "App.csproj"),
    ):
        parsed = parser(tmp_path / filename)
        assert parsed, f"{filename} yielded no components"
        components.extend(parsed)

    for component in components:
        assert not ui.is_legacy_sentinel(component["version"]), component
        assert component["version"] == ui.UNKNOWN
        assert component["version_unknown_reason"] in ui.UNKNOWN_REASONS


def test_the_ai_bom_generator_marks_an_unpinned_requirement_unknown(tmp_path):
    """The AI-BOM is generated output too, and it wrote the same literal."""
    from tools.security.ai_bom_generator import AIBOMGenerator

    _write(tmp_path / "requirements.txt", "torch==2.1.0\ntransformers\n")
    components = {
        c["component_name"]: c
        for c in AIBOMGenerator()._scan_requirements(tmp_path)
    }

    assert components["torch"]["version"] == "2.1.0"
    assert components["transformers"]["version"] == ui.UNKNOWN
    assert not ui.is_legacy_sentinel(components["transformers"]["version"])


def test_the_ai_bom_risk_rule_still_reads_a_pre_2026_row():
    """A BOM already in the database says "unspecified"; the rule must still fire."""
    from tools.security.ai_bom_generator import AIBOMGenerator

    generator = AIBOMGenerator()
    for stored in ("unspecified", ui.UNKNOWN):
        assert (
            generator._assess_risk(
                {"component_type": "library", "component_name": "lib", "version": stored}
            )
            == "high"
        ), stored


# ---------------------------------------------------------------------------
# Component Name — alternates, per ecosystem
# ---------------------------------------------------------------------------


def test_the_producers_own_spelling_survives_normalization():
    # PyPI's index is case- and separator-insensitive, so ICDEV normalizes for
    # the purl and the bom-ref. The element is defined as the name the *producer*
    # assigned, so the original spelling has to reach the document as well.
    names = cnames.derive_names(
        {"name": "flask", "declared_name": "Flask", "purl": "pkg:pypi/flask@3.0.0"}
    )
    assert names["primary"] == "flask"
    assert names["alternates"] == [{"name": "Flask", "kind": cnames.NAME_DECLARED}]


def test_a_declared_spelling_also_yields_its_pep_503_form():
    # `Flask_Login` normalizes to `flask-login` for the purl; `flask_login` is a
    # third real spelling, and an advisory may use any of the three.
    names = cnames.derive_names(
        {
            "name": "flask-login",
            "declared_name": "Flask_Login",
            "purl": "pkg:pypi/flask-login@0.6.3",
        }
    )
    assert cnames.all_names(names) == ["flask-login", "Flask_Login"]
    # The normalized form of the declared spelling IS the primary, so it is not
    # repeated as an alternate of itself.
    assert cnames.normalize_pypi("Flask_Login") == "flask-login"


def test_an_npm_scoped_package_states_its_published_name():
    # CycloneDX splits `@babel/core` into group + name, so the name the package
    # was actually published under appears in no single field.
    names = cnames.derive_names(
        {"name": "core", "group": "@babel", "purl": "pkg:npm/%40babel%2Fcore@7.23.9"}
    )
    assert names["alternates"] == [{"name": "@babel/core", "kind": cnames.NAME_QUALIFIED}]


def test_an_unscoped_npm_package_gains_nothing():
    assert cnames.derive_names({"name": "express", "purl": "pkg:npm/express@4.18.2"})[
        "alternates"
    ] == []


def test_a_maven_artifact_states_its_full_coordinate():
    # `commons-lang3` alone is ambiguous across groups; nobody cites it that way.
    names = cnames.derive_names(
        {
            "name": "commons-lang3",
            "group": "org.apache.commons",
            "purl": "pkg:maven/org.apache.commons/commons-lang3@3.14.0",
        }
    )
    assert names["alternates"] == [
        {"name": "org.apache.commons:commons-lang3", "kind": cnames.NAME_QUALIFIED}
    ]


def test_gradle_coordinates_are_maven_coordinates():
    names = cnames.derive_names(
        {"name": "guava", "group": "com.google.guava", "ecosystem": "gradle"}
    )
    assert names["alternates"][0]["name"] == "com.google.guava:guava"
    assert names["alternates"][0]["kind"] == cnames.NAME_QUALIFIED


def test_a_go_module_states_its_short_name():
    names = cnames.derive_names(
        {"name": "github.com/spf13/cobra", "purl": "pkg:golang/github.com/spf13/cobra@v1.8.0"}
    )
    assert names["alternates"] == [{"name": "cobra", "kind": cnames.NAME_SHORT}]


def test_a_single_name_component_states_no_alternates():
    # Not a defect — most components genuinely have one name, and inventing a
    # second would be exactly the guessing the element is meant to prevent.
    assert cnames.derive_names({"name": "serde", "purl": "pkg:cargo/serde@1.0.195"})[
        "alternates"
    ] == []
    assert cnames.derive_names({"name": "Serilog", "purl": "pkg:nuget/Serilog@3.1.1"})[
        "alternates"
    ] == []


def test_an_alternate_never_repeats_the_primary_name():
    # `flask` declared as `flask` is one name, not two.
    names = cnames.derive_names(
        {"name": "flask", "declared_name": "flask", "purl": "pkg:pypi/flask@3.0.0"}
    )
    assert names["alternates"] == []


def test_the_ecosystem_is_recovered_from_the_purl_when_not_stated():
    # Declared-manifest parsers set no `ecosystem` field; the resolver does.
    # Both must derive the same alternates or the two paths disagree.
    assert cnames.purl_ecosystem("pkg:pypi/flask@3.0.0") == "python"
    assert cnames.purl_ecosystem("pkg:maven/g/a@1") == "maven"
    assert cnames.purl_ecosystem("pkg:golang/github.com/x/y@v1") == "golang"
    assert cnames.purl_ecosystem("not-a-purl") == ""


def test_the_purl_name_segment_is_decoded_not_split_on_the_first_at():
    # `%40babel` decodes to `@babel`; cutting the version off at the FIRST `@`
    # would eat the namespace and leave a name of `babel%2Fcore`.
    assert cnames.purl_name("pkg:npm/%40babel/core@7.23.9") == "core"
    assert cnames.purl_name("pkg:golang/github.com/spf13/cobra@v1.8.0") == "cobra"
    assert cnames.purl_name("pkg:pypi/flask@3.0.0?arch=any#sub/dir") == "flask"


def test_derivation_order_is_fixed_so_regeneration_is_byte_stable():
    component = {
        "name": "core",
        "group": "@babel",
        "declared_name": "@babel/core",
        "purl": "pkg:npm/%40babel%2Fcore@7.23.9",
    }
    first = cnames.derive_names(component)
    assert first == cnames.derive_names(component)
    # `declared` and `qualified` both produce `@babel/core`; the earlier kind
    # keeps it rather than the name being listed twice.
    assert [e["kind"] for e in first["alternates"]] == [cnames.NAME_DECLARED]


def test_every_name_lists_the_primary_first():
    names = cnames.derive_names(
        {"name": "flask", "declared_name": "Flask", "purl": "pkg:pypi/flask@3.0.0"}
    )
    assert cnames.all_names(names) == ["flask", "Flask"]


# ---------------------------------------------------------------------------
# CycloneDX round-trip
# ---------------------------------------------------------------------------


def test_alternates_round_trip_through_cyclonedx():
    component = {
        "name": "core",
        "group": "@babel",
        "declared_name": "Babel-Core",
        "purl": "pkg:npm/%40babel%2Fcore@7.23.9",
    }
    names = cnames.derive_names(component)
    assert len(names["alternates"]) == 2

    cdx = {"type": "library", "name": "core", "version": "7.23.9"}
    cnames.apply_names_to_cyclonedx(cdx, names)

    assert cnames.parse_names_from_cyclonedx(cdx) == names
    assert cnames.validate_names(cnames.parse_names_from_cyclonedx(cdx)) == []


def test_the_multiple_entries_are_multiple_properties():
    # The element's requirement is literally that the format allow more than one
    # entry. A single joined string would satisfy a schema and not the standard.
    cdx = {"name": "core"}
    cnames.apply_names_to_cyclonedx(
        cdx,
        {
            "primary": "core",
            "alternates": [
                {"name": "@babel/core", "kind": cnames.NAME_QUALIFIED},
                {"name": "Babel-Core", "kind": cnames.NAME_DECLARED},
            ],
        },
    )
    payload = [
        p["value"]
        for p in cdx["properties"]
        if p["name"].startswith(cnames.PROPERTY_ALTERNATE_PREFIX)
    ]
    assert payload == ["@babel/core", "Babel-Core"]


def test_a_component_with_no_alternates_gains_no_properties():
    cdx = {"name": "serde"}
    cnames.apply_names_to_cyclonedx(cdx, {"primary": "serde", "alternates": []})
    assert "properties" not in cdx
    assert cnames.parse_names_from_cyclonedx(cdx)["alternates"] == []


def test_names_round_trip_through_json():
    names = cnames.derive_names(
        {"name": "flask", "declared_name": "Flask", "purl": "pkg:pypi/flask@3.0.0"}
    )
    restored = cnames.names_from_json(cnames.names_to_json(names), primary="flask")
    assert restored == names
    # Unreadable JSON degrades to "no alternates", never to an exception.
    assert cnames.names_from_json("{not json", primary="flask")["alternates"] == []


# ---------------------------------------------------------------------------
# validation — the negatives
# ---------------------------------------------------------------------------


def test_an_alternate_with_an_invented_kind_is_a_defect():
    cdx = {
        "name": "flask",
        "properties": [{"name": f"{cnames.PROPERTY_ALTERNATE_PREFIX}vibes", "value": "Flask"}],
    }
    errors = cnames.validate_names(cnames.parse_names_from_cyclonedx(cdx))
    assert any("unrecognised alternate-name kind" in e for e in errors)


def test_an_alternate_repeating_the_primary_name_is_a_defect():
    cdx = {
        "name": "flask",
        "properties": [
            {"name": f"{cnames.PROPERTY_ALTERNATE_PREFIX}{cnames.NAME_DECLARED}", "value": "flask"}
        ],
    }
    errors = cnames.validate_names(cnames.parse_names_from_cyclonedx(cdx))
    assert any("repeats the primary name" in e for e in errors)


def test_a_repeated_alternate_is_a_defect():
    cdx = {
        "name": "core",
        "properties": [
            {
                "name": f"{cnames.PROPERTY_ALTERNATE_PREFIX}{cnames.NAME_QUALIFIED}",
                "value": "@babel/core",
            },
            {
                "name": f"{cnames.PROPERTY_ALTERNATE_PREFIX}{cnames.NAME_DECLARED}",
                "value": "@babel/core",
            },
        ],
    }
    errors = cnames.validate_names(cnames.parse_names_from_cyclonedx(cdx))
    assert any("listed twice" in e for e in errors)


# ---------------------------------------------------------------------------
# the generator emits it
# ---------------------------------------------------------------------------


def test_the_generated_document_carries_alternate_names():
    from tools.compliance import sbom_generator

    project = {"id": "fld-06", "name": "Fixture", "directory_path": None}
    components = [
        {
            "type": "library",
            "name": "flask",
            "declared_name": "Flask",
            "version": "3.0.0",
            "purl": "pkg:pypi/flask@3.0.0",
            "ecosystem": "python",
        },
        {
            "type": "library",
            "name": "core",
            "group": "@babel",
            "version": "7.23.9",
            "purl": "pkg:npm/%40babel%2Fcore@7.23.9",
            "ecosystem": "npm",
        },
        {
            "type": "library",
            "name": "serde",
            "version": "1.0.195",
            "purl": "pkg:cargo/serde@1.0.195",
            "ecosystem": "cargo",
        },
    ]

    sbom, _ = sbom_generator._build_cyclonedx_sbom(project, components, spec_version="1.6")
    by_name = {c["name"]: c for c in sbom["components"]}

    def names_of(component):
        return cnames.all_names(cnames.parse_names_from_cyclonedx(component))

    assert names_of(by_name["flask"]) == ["flask", "Flask"]
    assert names_of(by_name["core"]) == ["core", "@babel/core"]
    assert names_of(by_name["serde"]) == ["serde"]
    assert cnames.validate_sbom_names(sbom)[0] == []


def test_deduplication_does_not_lose_a_producers_spelling():
    """Two manifests, two spellings, one component.

    ``_component_identity`` keys on the normalized name, so declaring ``Flask``
    in one manifest and ``FLASK`` in another collapses to a single component —
    and without the collapsed-spelling merge, whichever spelling lost the race
    would simply vanish. The element exists so that a name is not lost.
    """
    from tools.compliance import sbom_generator

    project = {"id": "fld-06", "name": "Fixture", "directory_path": None}
    base = {
        "type": "library",
        "name": "flask",
        "version": "3.0.0",
        "purl": "pkg:pypi/flask@3.0.0",
        "ecosystem": "python",
    }
    components = [
        dict(base, declared_name="Flask", source="requirements.txt"),
        dict(base, declared_name="FLASK", source="pyproject.toml"),
    ]

    sbom, count = sbom_generator._build_cyclonedx_sbom(project, components, spec_version="1.6")

    assert count == 1
    assert cnames.all_names(cnames.parse_names_from_cyclonedx(sbom["components"][0])) == [
        "flask",
        "Flask",
        "FLASK",
    ]
    assert cnames.validate_sbom_names(sbom)[0] == []


def test_a_withheld_name_suppresses_the_alternates_in_the_document(tmp_path):
    from tools.compliance import sbom_generator

    project = {"id": "fld-06", "name": "Fixture", "directory_path": None}
    components = [
        {
            "type": "library",
            "name": "core",
            "group": "@internal",
            "version": "1.0.0",
            "purl": "pkg:npm/%40internal%2Fcore@1.0.0",
            "ecosystem": "npm",
        }
    ]
    policy = ui.load_disclosure_policy(path=tmp_path / "absent.yaml", env={})
    policy["withhold"]["components"] = [
        {"field": ui.FIELD_NAME, "reason": ui.REASON_CLASSIFICATION_RESTRICTED}
    ]

    sbom, _ = sbom_generator._build_cyclonedx_sbom(
        project, components, spec_version="1.6", disclosure_policy=policy
    )

    component = sbom["components"][0]
    assert component["name"] == "withheld"
    # Publishing `@internal/core` beside a redacted name would undo the redaction.
    assert cnames.parse_names_from_cyclonedx(component)["alternates"] == []


def test_the_declared_adoption_keeps_the_reason_the_parser_established():
    """The reshape between the parsers and the generator must not flatten a reason.

    ``_adopt_declared`` rebuilds every declared component into resolver shape,
    and it is the only path a declared manifest takes to a real SBOM. Dropping
    ``version_unknown_reason`` there collapsed the Maven parent-POM case back to
    "nobody pinned one" — the per-parser tests still passed, because they call
    the parser directly and never cross this seam.
    """
    from tools.compliance.dependency_resolver import _adopt_declared

    parsed = [
        {
            "type": "library",
            "name": "beta",
            "version": ui.UNKNOWN,
            "version_unknown_reason": ui.REASON_VERSION_MANAGED_BY_PARENT,
            "purl": "pkg:maven/com.example/beta",
            "group": "com.example",
            "scope": "required",
        },
        {
            "type": "library",
            "name": "gamma",
            "version": "1.0.0",
            "purl": "pkg:maven/com.example/gamma@1.0.0",
            "group": "com.example",
            "scope": "required",
        },
    ]
    adopted = {c["name"]: c for c in _adopt_declared("maven", parsed, "pom.xml")}

    assert adopted["beta"]["version_unknown_reason"] == ui.REASON_VERSION_MANAGED_BY_PARENT
    # A component with a real version states no reason at all.
    assert "version_unknown_reason" not in adopted["gamma"]


def test_the_declared_adoption_carries_the_producers_spelling():
    from tools.compliance.dependency_resolver import _adopt_declared

    adopted = _adopt_declared(
        "python",
        [
            {
                "type": "library",
                "name": "flask",
                "declared_name": "Flask",
                "version": "3.0.0",
                "purl": "pkg:pypi/flask@3.0.0",
            }
        ],
        "requirements.txt",
    )
    assert adopted[0]["declared_name"] == "Flask"


def test_a_maven_parent_managed_version_keeps_its_reason_in_the_document():
    from tools.compliance import sbom_generator
    from tools.compliance.dependency_resolver import _adopt_declared

    project = {"id": "fld-06", "name": "Fixture", "directory_path": None}
    components = _adopt_declared(
        "maven",
        [
            {
                "type": "library",
                "name": "beta",
                "version": ui.UNKNOWN,
                "version_unknown_reason": ui.REASON_VERSION_MANAGED_BY_PARENT,
                "purl": "pkg:maven/com.example/beta",
                "group": "com.example",
                "scope": "required",
            }
        ],
        "pom.xml",
    )

    sbom, _ = sbom_generator._build_cyclonedx_sbom(project, components, spec_version="1.6")
    beta = sbom["components"][0]

    assert beta["version"] == ui.UNKNOWN
    assert (
        ui.Disclosure.from_cyclonedx(beta).unknown_fields[ui.FIELD_VERSION]
        == ui.REASON_VERSION_MANAGED_BY_PARENT
    )
    # And the coordinate is an alternate name, since `beta` alone is ambiguous.
    assert cnames.all_names(cnames.parse_names_from_cyclonedx(beta)) == [
        "beta",
        "com.example:beta",
    ]


def test_a_generated_sbom_states_both_elements_end_to_end(icdev_db, tmp_path):
    """Real database, real manifests on disk, real file written.

    Proves the two elements reach the artifact when the generator runs the way
    its call sites run it — not just when ``_build_cyclonedx_sbom`` is handed a
    component list directly.
    """
    import sqlite3

    from tools.compliance import sbom_generator

    project = tmp_path / "fld-06-project"
    _write(project / "requirements.txt", "Flask==3.0.0\nrequests\n")

    conn = sqlite3.connect(str(icdev_db))
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        ("fld06-e2e", "Component Name Fixture", "api", str(project)),
    )
    conn.commit()
    conn.close()

    out_file = tmp_path / "names.cdx.json"
    sbom_generator.generate_sbom(
        project_id="fld06-e2e", output_path=str(out_file), db_path=icdev_db
    )
    sbom = json.loads(out_file.read_text(encoding="utf-8"))

    by_name = {c["name"]: c for c in sbom["components"]}

    # Component Name: the producer's spelling survived the normalization that
    # the purl and the bom-ref need.
    assert cnames.all_names(cnames.parse_names_from_cyclonedx(by_name["flask"])) == [
        "flask",
        "Flask",
    ]

    # Component Version: the unpinned dependency says unknown, and says why.
    assert by_name["requests"]["version"] == ui.UNKNOWN
    assert (
        ui.Disclosure.from_cyclonedx(by_name["requests"]).unknown_fields[ui.FIELD_VERSION]
        == ui.REASON_DECLARED_WITHOUT_VERSION
    )

    # And no placeholder anywhere in the serialized artifact.
    raw = out_file.read_text(encoding="utf-8")
    assert '"unspecified"' not in raw
    assert '"managed"' not in raw

    assert cnames.validate_sbom_names(sbom)[0] == []
    assert ui.validate_sbom_disclosure(sbom)[0] == []


# ---------------------------------------------------------------------------
# mirror parity
# ---------------------------------------------------------------------------


def test_root_and_mirror_stay_in_sync():
    for relative in (
        "tools/compliance/component_names.py",
        "tools/compliance/sbom_generator.py",
        "tools/compliance/dependency_resolver.py",
        "tools/security/ai_bom_generator.py",
    ):
        root = REPO_ROOT / relative
        mirror = REPO_ROOT / "icdev" / relative
        assert mirror.exists(), f"icdev/{relative} is missing"
        assert root.read_bytes() == mirror.read_bytes(), relative
