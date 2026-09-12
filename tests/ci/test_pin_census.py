# CUI // SP-CTI
"""Pin census — the four site kinds, the four predicates, and the ratchet.

xrv-route-03. Every test here builds a THROWAWAY tree rather than asserting
against the live one: a test pinned to today's 69 sites would fail the day
somebody pins a reference, which is the day the gate worked.

The exceptions are the two tests that deliberately DO read this checkout — the
acceptance criterion ("--check exits 0 on the tree as committed") and the
coherence registration. Those are claims about this repository and nowhere else.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import pin_census as pc

REPO = Path(__file__).resolve().parents[2]


# ── fixtures ───────────────────────────────────────────────────────────────
GATE_TEMPLATE = """\
pin_census:
  census_file: args/pin_census.txt
  pin_max: {pin_max}
  min_reason_chars: 12
  command_files:
    - .github/workflows/*.yml
    - .github/workflows/*.yaml
    - .gitlab-ci.yml
    - Dockerfile*
    - docker/Dockerfile*
  action_files:
    - .github/workflows/*.yml
    - .github/workflows/*.yaml
  compose_files:
    - docker-compose*.yml
  exclude: {exclude}
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal checkout `_find_repo_root` will recognise."""
    (tmp_path / "args").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "vendor" / "images").mkdir(parents=True)
    (tmp_path / "requirements.txt").write_text("pyyaml>=6.0\n", encoding="utf-8")
    (tmp_path / "args" / "pin_gate.yaml").write_text(
        GATE_TEMPLATE.format(pin_max=0, exclude="[]"), encoding="utf-8"
    )
    return tmp_path


def _set_gate(tree: Path, *, pin_max: int = 0, exclude: str = "[]") -> None:
    (tree / "args" / "pin_gate.yaml").write_text(
        GATE_TEMPLATE.format(pin_max=pin_max, exclude=exclude), encoding="utf-8"
    )


def _report(tree: Path, only=None) -> dict:
    return pc.build_report(tree, only, pc.load_config(tree))


def _keys(report: dict) -> set[str]:
    return {site["key"] for site in report["unregistered"]}


def _kinds(report: dict) -> list[str]:
    return [site["kind"] for site in report["unregistered"]]


# ── kind 1: an unpinned install ────────────────────────────────────────────
def test_an_unpinned_pip_install_is_a_site(tree: Path) -> None:
    (tree / ".gitlab-ci.yml").write_text(
        "job:\n  script:\n    - pip install --quiet llm-sandbox docker pyyaml\n",
        encoding="utf-8",
    )
    report = _report(tree)
    assert _keys(report) == {
        ".gitlab-ci.yml::unpinned_install::llm-sandbox",
        ".gitlab-ci.yml::unpinned_install::docker",
        ".gitlab-ci.yml::unpinned_install::pyyaml",
    }
    assert set(_kinds(report)) == {pc.KIND_INSTALL}


def test_a_version_pinned_install_is_not_a_site(tree: Path) -> None:
    (tree / ".gitlab-ci.yml").write_text(
        "job:\n  script:\n    - pip install llm-sandbox==0.3.1 docker===7.1.0\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


def test_a_version_RANGE_is_not_a_pin(tree: Path) -> None:
    """A range names a SET of releases, so the bytes a job runs still move."""
    (tree / ".gitlab-ci.yml").write_text(
        'job:\n  script:\n    - pip install "boto3>=1.34"\n', encoding="utf-8"
    )
    report = _report(tree)
    assert _keys(report) == {".gitlab-ci.yml::unpinned_install::boto3"}
    # The reference is reported WHOLE. Truncating it at the `>` was the first
    # version's bug and produced a finding about a package called "boto".
    assert report["unregistered"][0]["reference"] == "boto3>=1.34"


def test_a_requirements_file_and_an_editable_install_name_no_package(tree: Path) -> None:
    """The pin lives in the declared file, so there is nothing here to pin."""
    (tree / ".gitlab-ci.yml").write_text(
        "job:\n  script:\n"
        "    - pip install --quiet -r requirements.txt\n"
        "    - pip install -e . 2>/dev/null || true\n"
        "    - pip install dist/*.whl\n"
        "    - python3 -m pip install -c constraints.txt -r requirements.txt\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


def test_a_redirection_is_never_read_as_a_package(tree: Path) -> None:
    """`pip install -e . 2>/dev/null` reported a package called "2" once."""
    assert pc.pip_requirements(" -e . 2>/dev/null || true") == []


def test_a_pep508_direct_reference_pinned_to_a_git_TAG_is_a_site(tree: Path) -> None:
    """A git tag is mutable — it is not a pin, and it is not a path either."""
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        'jobs:\n  a:\n    steps:\n      - run: pip install "pkg @ git+https://h/o/r@v0.2.0"\n',
        encoding="utf-8",
    )
    report = _report(tree)
    assert _keys(report) == {".github/workflows/ci.yml::unpinned_install::pkg"}


def test_a_pep508_direct_reference_pinned_to_a_sha_is_not_a_site(tree: Path) -> None:
    sha = "a" * 40
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        f'jobs:\n  a:\n    steps:\n      - run: pip install "pkg @ git+https://h/o/r@{sha}"\n',
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


def test_a_backslash_continued_install_is_read_whole(tree: Path) -> None:
    """docker/Dockerfile.iac spells its install across four physical lines."""
    (tree / "docker").mkdir()
    (tree / "docker" / "Dockerfile.iac").write_text(
        "RUN apk add --no-cache git \\\n"
        "    && pip install --no-cache-dir \\\n"
        "        ansible \\\n"
        "        boto3 \\\n"
        "        botocore\n",
        encoding="utf-8",
    )
    report = _report(tree)
    assert _keys(report) == {
        "docker/Dockerfile.iac::unpinned_install::ansible",
        "docker/Dockerfile.iac::unpinned_install::boto3",
        "docker/Dockerfile.iac::unpinned_install::botocore",
    }


def test_npm_ci_and_a_bare_npm_install_resolve_through_the_lockfile(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - run: npm ci\n"
        "      - run: npm install 2>/dev/null || true\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


def test_an_unpinned_global_npm_install_is_a_site(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - run: npm install -g newman @scope/cli@2.1.0\n",
        encoding="utf-8",
    )
    report = _report(tree)
    # `@scope/cli@2.1.0` carries a version, so only `newman` is a site.
    assert _keys(report) == {".github/workflows/ci.yml::unpinned_install::newman"}


# ── kind 2: a remote install script ────────────────────────────────────────
def test_curl_piped_into_a_shell_is_a_site(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n"
        "      - run: curl -fsSL https://ollama.com/install.sh | sh\n",
        encoding="utf-8",
    )
    report = _report(tree)
    assert _keys(report) == {
        ".github/workflows/ci.yml::unpinned_script::https://ollama.com/install.sh"
    }
    assert _kinds(report) == [pc.KIND_SCRIPT]


def test_a_curl_that_is_not_piped_into_a_shell_is_not_a_site(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n"
        "      - run: curl -fsSL https://example.com/health | jq .status\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


# ── kind 3: a tag-pinned action ────────────────────────────────────────────
def test_a_tag_pinned_action_is_a_site(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: pypa/gh-action-pypi-publish@release/v1\n",
        encoding="utf-8",
    )
    report = _report(tree)
    assert _keys(report) == {
        ".github/workflows/ci.yml::tag_pinned_action::actions/checkout",
        ".github/workflows/ci.yml::tag_pinned_action::pypa/gh-action-pypi-publish",
    }
    assert set(_kinds(report)) == {pc.KIND_ACTION}


def test_a_SHA_pinned_action_is_NOT_a_site(tree: Path) -> None:
    sha = "08c6903cd8c0fde910a37f88322edcfb5dd907a8"
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{sha}  # v5.0.0\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


def test_a_local_workflow_reference_is_not_a_third_party_action(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n"
        "      - uses: ./.github/workflows/interface_validation_steps.yaml\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


def test_the_key_carries_no_ref_so_a_routine_tag_bump_is_the_same_site(
    tree: Path,
) -> None:
    """`@v4 -> @v5` is the SAME unpinned decision; keying on the ref would fail
    --check on every bump and demand a census edit that says nothing new."""
    workflow = tree / ".github" / "workflows" / "ci.yml"
    workflow.write_text(
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n", encoding="utf-8"
    )
    before = _keys(_report(tree))
    workflow.write_text(
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v5\n", encoding="utf-8"
    )
    assert _keys(_report(tree)) == before


def test_two_uses_of_one_action_in_one_file_are_one_site(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        "  b:\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 1


# ── kind 4: an undigested compose image ────────────────────────────────────
def test_a_compose_image_with_no_vendored_digest_is_a_site(tree: Path) -> None:
    (tree / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: pgvector/pgvector:pg16\n", encoding="utf-8"
    )
    report = _report(tree)
    assert _keys(report) == {"docker-compose.yml::undigested_image::pgvector/pgvector"}
    assert report["unregistered"][0]["service"] == "db"


def test_a_digest_pinned_compose_image_is_not_a_site(tree: Path) -> None:
    (tree / "docker-compose.yml").write_text(
        f"services:\n  db:\n    image: postgres@sha256:{'b' * 64}\n", encoding="utf-8"
    )
    assert _report(tree)["sites_seen"] == 0


def test_an_image_whose_repo_IS_vendored_by_digest_is_not_a_site(tree: Path) -> None:
    """This is the seam: vendor/images/*.txt is where a MEASURED digest lives."""
    (tree / "vendor" / "images" / "images-floci.txt").write_text(
        f"# a comment\nfloci/floci@sha256:{'c' * 64}\n", encoding="utf-8"
    )
    (tree / "docker-compose.yml").write_text(
        "services:\n"
        "  floci:\n    image: floci/floci:2.0.1\n"
        "  floci_az:\n    image: floci/floci-az:0.12.0\n",
        encoding="utf-8",
    )
    report = _report(tree)
    assert _keys(report) == {"docker-compose.yml::undigested_image::floci/floci-az"}


def test_a_BUILT_service_is_never_an_image_site(tree: Path) -> None:
    """A `build:` key means the image is produced here, never pulled — so a
    registry digest cannot describe it. Measured at adoption: this one predicate
    is the difference between 8 real sites and 29 mostly-noise ones."""
    (tree / "docker-compose.yml").write_text(
        "services:\n"
        "  agent:\n"
        "    build:\n      context: .\n      dockerfile: docker/Dockerfile.agent-base\n"
        "    image: icdev/agent-base:latest\n",
        encoding="utf-8",
    )
    assert _report(tree)["sites_seen"] == 0


def test_a_registry_host_with_a_port_is_not_read_as_a_tag(tree: Path) -> None:
    assert pc.image_repo("registry.example.com:5000/team/app:1.2") == (
        "registry.example.com:5000/team/app"
    )
    assert pc.image_repo("ghcr.io/berriai/litellm-non_root:main-stable") == (
        "ghcr.io/berriai/litellm-non_root"
    )


# ── the ratchet ────────────────────────────────────────────────────────────
def _seed_census(tree: Path) -> int:
    result = pc.seed(tree, pc.load_config(tree))
    (tree / "args" / "pin_census.txt").write_text(
        "# header\n" + "\n".join(result["lines"]) + "\n", encoding="utf-8"
    )
    _set_gate(tree, pin_max=result["sites"])
    return result["sites"]


def test_a_registered_site_passes_and_a_NEW_one_fails(tree: Path) -> None:
    workflow = tree / ".github" / "workflows" / "ci.yml"
    workflow.write_text(
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        "      - run: pip install ruff\n",
        encoding="utf-8",
    )
    assert _seed_census(tree) == 2
    assert _report(tree)["ok"] is True

    workflow.write_text(
        workflow.read_text(encoding="utf-8") + "      - run: pip install bandit\n",
        encoding="utf-8",
    )
    report = _report(tree)
    assert report["ok"] is False
    assert _keys(report) == {".github/workflows/ci.yml::unpinned_install::bandit"}


def test_the_census_is_ENUMERATED_not_counted(tree: Path) -> None:
    """A swap — one site out, one in, count unchanged — must still fail.

    A bare count can be held constant while the set churns, which is exactly how
    the ungated-test gap regrew behind a green gate.
    """
    workflow = tree / ".github" / "workflows" / "ci.yml"
    workflow.write_text(
        "jobs:\n  a:\n    steps:\n      - run: pip install ruff\n", encoding="utf-8"
    )
    assert _seed_census(tree) == 1

    workflow.write_text(
        "jobs:\n  a:\n    steps:\n      - run: pip install bandit\n", encoding="utf-8"
    )
    report = _report(tree)
    assert report["census_size"] == report["sites_seen"] == 1
    assert report["over_ceiling"] is False, "the COUNT is unchanged, by construction"
    assert report["ok"] is False, "and identity is what catches the swap"
    assert _keys(report) == {".github/workflows/ci.yml::unpinned_install::bandit"}
    assert report["stale_entries"] == [
        ".github/workflows/ci.yml::unpinned_install::ruff"
    ]


def test_a_census_entry_with_no_written_reason_is_refused(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - run: pip install ruff\n", encoding="utf-8"
    )
    (tree / "args" / "pin_census.txt").write_text(
        ".github/workflows/ci.yml::unpinned_install::ruff  # TBD\n", encoding="utf-8"
    )
    _set_gate(tree, pin_max=1)
    report = _report(tree)
    assert report["thin_reasons"] == [
        ".github/workflows/ci.yml::unpinned_install::ruff"
    ]
    assert report["ok"] is False


def test_the_ceiling_is_breached_by_a_registered_site_over_it(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - run: pip install ruff\n", encoding="utf-8"
    )
    _seed_census(tree)
    _set_gate(tree, pin_max=0)
    report = _report(tree)
    assert report["over_ceiling"] is True
    assert report["ok"] is False


def test_prune_only_ever_shrinks(tree: Path) -> None:
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - run: pip install ruff\n", encoding="utf-8"
    )
    census = tree / "args" / "pin_census.txt"
    census.write_text(
        "# header\n"
        ".github/workflows/ci.yml::unpinned_install::ruff  # a written reason here\n"
        ".github/workflows/gone.yml::unpinned_install::x  # a written reason here\n",
        encoding="utf-8",
    )
    _set_gate(tree, pin_max=2)
    result = pc.prune(tree, pc.load_config(tree))
    assert result["dropped"] == 1
    text = census.read_text(encoding="utf-8")
    assert "gone.yml" not in text
    assert "ci.yml::unpinned_install::ruff" in text
    assert text.startswith("# header")


def test_an_exclusion_takes_a_file_out_of_scope(tree: Path) -> None:
    (tree / ".gitlab-ci.yml").write_text(
        "job:\n  script:\n    - pip install ruff\n", encoding="utf-8"
    )
    _set_gate(
        tree,
        exclude='[{"path": ".gitlab-ci.yml", "reason": "a written reason long enough"}]',
    )
    assert _report(tree)["sites_seen"] == 0


# ── scope ──────────────────────────────────────────────────────────────────
def test_a_diff_touching_no_CI_file_is_out_of_scope(tree: Path) -> None:
    assert pc.filter_scope(["tools/foo.py", "README.md"], pc.load_config(tree)) == []


def test_a_diff_touching_a_CI_file_is_in_scope(tree: Path) -> None:
    cfg = pc.load_config(tree)
    assert pc.filter_scope(
        ["./.gitlab-ci.yml", "docker-compose.yml", "tools/foo.py"], cfg
    ) == [".gitlab-ci.yml", "docker-compose.yml"]


def test_a_partial_scan_suppresses_the_ceiling_and_stale_halves(tree: Path) -> None:
    """A subset cannot tell a deleted site from an unscanned one, and "69
    entries are stale" on a one-file commit is how a check earns a `|| true`."""
    (tree / ".gitlab-ci.yml").write_text(
        "job:\n  script:\n    - pip install ruff\n", encoding="utf-8"
    )
    (tree / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n", encoding="utf-8"
    )
    _seed_census(tree)
    _set_gate(tree, pin_max=0)  # a breach the full scan WOULD report

    report = _report(tree, only=[".gitlab-ci.yml"])
    assert report["partial"] is True
    assert report["over_ceiling"] is False
    assert report["stale_entries"] == []
    assert report["ok"] is True


# ── unmeasurable is its own verdict ────────────────────────────────────────
def test_an_unparseable_compose_file_is_UNMEASURABLE_and_not_clean(tree: Path) -> None:
    (tree / "docker-compose.yml").write_text(
        "services:\n  db:\n   image: [unclosed\n", encoding="utf-8"
    )
    report = _report(tree)
    assert report["unmeasurable"], "a file that will not parse must say so"
    assert report["ok"] is False
    assert report["sites_seen"] == 0, "and it must not invent sites either"


def test_an_absent_vendor_images_dir_is_UNMEASURABLE_not_every_image_unpinned(
    tmp_path: Path,
) -> None:
    """Reading an absent vendor tree as "no image is pinned" would invent a
    finding for every image on any deployment without one."""
    (tmp_path / "args").mkdir()
    (tmp_path / "requirements.txt").write_text("pyyaml>=6.0\n", encoding="utf-8")
    (tmp_path / "args" / "pin_gate.yaml").write_text(
        GATE_TEMPLATE.format(pin_max=0, exclude="[]"), encoding="utf-8"
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:16\n", encoding="utf-8"
    )
    report = pc.build_report(tmp_path, None, pc.load_config(tmp_path))
    assert any("vendor digests unreadable" in entry for entry in report["unmeasurable"])
    assert report["ok"] is False


def test_a_missing_gate_config_raises_rather_than_defaulting(tmp_path: Path) -> None:
    (tmp_path / "args").mkdir()
    with pytest.raises(pc.PinCensusError):
        pc.load_config(tmp_path)


def test_prune_refuses_against_an_unmeasurable_scan(tree: Path) -> None:
    """Pruning against a partial read DELETES a live entry — the one direction
    that loses information."""
    (tree / "docker-compose.yml").write_text(
        "services:\n  db:\n   image: [unclosed\n", encoding="utf-8"
    )
    (tree / "args" / "pin_census.txt").write_text(
        "x::unpinned_install::y  # a written reason here\n", encoding="utf-8"
    )
    with pytest.raises(pc.PinCensusError):
        pc.prune(tree, pc.load_config(tree))


# ── rates never fabricate ──────────────────────────────────────────────────
def test_a_rate_over_an_empty_denominator_is_None_and_never_a_number() -> None:
    assert pc._rate(0, 0) is None
    assert pc._rate(5, 0) is None


def test_an_imperfect_share_floors_to_999_rather_than_rounding_to_perfect() -> None:
    assert pc._rate(9999, 10000) == 99.9
    assert pc._rate(10, 10) == 100.0, "a rate that IS 100 still reads 100.0"
    assert pc._rate(1, 2) == 50.0


def test_the_requirements_survey_is_unmeasurable_with_no_requirements_file(
    tmp_path: Path,
) -> None:
    survey = pc.survey_requirements(tmp_path)
    assert survey["measured"] is False
    assert survey["declared"] is None
    assert survey["unpinned_pct"] is None, "never 0.0 and never 100.0"


def test_the_requirements_survey_keeps_ranges_and_direct_refs_apart(
    tmp_path: Path,
) -> None:
    """A range is resolved by the index against a published release; a mutable
    git tag by whoever owns the repository. Different facts, different fixes."""
    (tmp_path / "requirements.txt").write_text(
        "# a comment\n"
        "pyyaml>=6.0\n"
        "flask==3.0.1\n"
        "icdev-core @ git+https://github.com/o/r@v0.2.0\n"
        "-r other.txt\n",
        encoding="utf-8",
    )
    survey = pc.survey_requirements(tmp_path)
    assert survey["declared"] == 3
    assert survey["pinned"] == 1
    assert survey["ranged"] == 1
    assert survey["direct_reference"] == 1
    assert survey["unpinned_pct"] == 66.7


def test_the_requirements_survey_is_reported_and_NEVER_gated(tree: Path) -> None:
    """45 of 46 declarations use `>=`; gating them would refuse routine work."""
    (tree / "requirements.txt").write_text(
        "\n".join(f"pkg{i}>=1.0" for i in range(40)) + "\n", encoding="utf-8"
    )
    report = _report(tree)
    survey = report["surveyed_not_gated"]["requirements_txt"]
    assert survey["ranged"] == 40
    assert report["sites_seen"] == 0
    assert report["ok"] is True, "a range in requirements.txt is surveyed, not a site"


# ── this checkout ──────────────────────────────────────────────────────────
def test_the_tree_as_committed_passes_the_gate() -> None:
    """The acceptance criterion: every current site is named in the census.

    Run as a SUBPROCESS from the repo root so the assertion is about the exit
    code an operator and CI actually see, not about a function return.
    """
    result = subprocess.run(
        [sys.executable, "tools/ci/pin_census.py", "--check"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        "args/pin_census.txt does not name every site in this tree:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_json_report_is_machine_readable_on_this_tree() -> None:
    result = subprocess.run(
        [sys.executable, "tools/ci/pin_census.py", "--json"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["scope"] == "tree"
    assert set(report["by_kind"]) == set(pc.KINDS)
    assert report["unmeasurable"] == [], report["unmeasurable"]


def test_coherence_checker_registers_pin_census() -> None:
    from tools.workflow import coherence_checker as cc

    assert "pin_census" in cc.CHECK_REGISTRY
    assert cc._FIX_REGISTRY.get("pin_census") == "skip", (
        "a pin is a decision; auto-registering a site would be the gate widening "
        "its own allowlist"
    )
    # A claim about the CI surface, not about the diff — so the full tier always
    # runs it and the fast tier re-adds it only when the diff can move its verdict.
    assert "pin_census" in cc.HEAVY_CHECKS
    assert "pin_census" in cc.select_checks("full")
    assert "pin_census" not in cc.select_checks("fast", [Path("tools/foo.py")])
    assert "pin_census" in cc.select_checks("fast", [Path(".gitlab-ci.yml")])


def test_the_coherence_check_warns_rather_than_failing_on_a_new_site(
    monkeypatch: pytest.MonkeyPatch, tree: Path
) -> None:
    """WARN on day one, over a 69-site surface nobody has drained. The standalone
    tool's own `--check` is where the refusal lives."""
    from tools.workflow import coherence_checker as cc

    (tree / ".gitlab-ci.yml").write_text(
        "job:\n  script:\n    - pip install unpinned-thing\n", encoding="utf-8"
    )

    # Capture the real functions BEFORE patching: a lambda that calls back
    # through the patched name recurses without bound, and the checker turns
    # that into "could not be read" -- a warn for the wrong reason, which would
    # have passed a looser assertion on `status` alone.
    real_build_report = pc.build_report
    real_load_config = pc.load_config

    def _build_report(only=None, cfg=None, **_kwargs):
        return real_build_report(tree, only, real_load_config(tree))

    monkeypatch.setattr(pc, "build_report", _build_report)
    monkeypatch.setattr(pc, "load_config", lambda *a, **k: real_load_config(tree))

    result = cc.check_pin_census()
    assert result.status == "warn"
    assert result.missing, "the new site is named, not merely counted"
