# CUI // SP-CTI
"""The emulator seam's configuration is DOCUMENTED, and the docs match it (flx-seam-02).

RED AT THE MERGE BASE. Measured there: `.env.example` carries NO `FLOCI_*` and no
`LOCALSTACK_*` key at all -- its only AWS line is `AWS_DEFAULT_REGION=us-gov-west-1`
-- so nothing told an operator the emulator was configurable, which of the two
name families to use, or that one of them is deprecated. Every assertion below
about the documented key set fails against that file.

WHY EVERY EXPECTATION IS DERIVED AND NOT SPELLED OUT
----------------------------------------------------
The env names come from walking `tools/cloud/emulator.py`'s AST, the defaults
from its `DEFAULT_*` constants, and the deprecation map from its
`DEPRECATED_ALIASES` dict. A test carrying its OWN copy of the key list agrees
with a stale document forever -- it proves the list was typed twice, which was
never in question. These fail the day the seam grows a key nobody wrote down,
or a documented default drifts from the one the code applies.

THE ONE ASSERTION THAT IS NOT ABOUT TEXT
----------------------------------------
`test_a_copied_env_example_does_not_downgrade_real_aws_to_dry_run` parses
`.env.example` the way the runtime parses `.env` and puts the result through the
SHIPPED `detect_mode`. An endpoint declared while the switch is off is a
CONTRADICTION the seam deliberately answers `dry_run` to, so shipping an
uncommented `FLOCI_ENDPOINT` in the example file would silently downgrade every
real `terraform apply` on every deployment that copied it to plan-only. That
consequence is measured through the consumer, never re-spelled as a rule about
which lines carry a `#`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from dotenv import dotenv_values

from tools.cloud import emulator

_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _ROOT / ".env.example"
_SEAM = _ROOT / "tools" / "cloud" / "emulator.py"
_COMMANDS_DOC = _ROOT / "docs" / "reference" / "commands.md"
_MANIFEST_SHARD = Path("tools") / "manifest" / "cloud-agnostic-architecture.md"

#: An env NAME. `FLOCI_ACCOUNT_ID:invalid` is a warn-dedupe key, not a variable,
#: and the colon keeps it out by construction rather than by an exclusion list.
_ENV_NAME_RE = re.compile(r"(?:FLOCI|LOCALSTACK)_[A-Z0-9_]+")

#: A `KEY=value` line, live or commented out.
_ASSIGNMENT_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _seam_env_names() -> set[str]:
    """Every emulator env name the seam's source mentions as a whole string."""
    tree = ast.parse(_SEAM.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _ENV_NAME_RE.fullmatch(node.value)
    }


def _env_example_lines() -> list[str]:
    return _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def _documented_assignments() -> dict[str, str]:
    """`KEY -> value` for every assignment in .env.example, commented or not.

    A commented `# FLOCI_ENDPOINT=http://localhost:4566` IS documentation -- it
    shows the operator the name, the shape and the default in one line -- so the
    leading `#` is stripped before matching. Whether a key is ACTIVE is a
    different question, asked separately below.
    """
    found: dict[str, str] = {}
    for line in _env_example_lines():
        stripped = line.lstrip("#").strip()
        match = _ASSIGNMENT_RE.match(stripped)
        if match:
            found.setdefault(match.group(1), match.group(2).strip())
    return found


def _active_settings() -> dict[str, str]:
    """.env.example parsed exactly the way the runtime parses .env.

    `tools/studio/executors/_base.py::load_dotenv` uses `dotenv_values`; so does
    this, so a comment convention this file gets wrong cannot pass here and fail
    on a real deployment.
    """
    return {k: v for k, v in dotenv_values(_ENV_EXAMPLE).items() if v is not None}


def _floci_names() -> set[str]:
    return {n for n in _seam_env_names() if n.startswith("FLOCI_")}


# -- The key set -----------------------------------------------------------


def test_the_seam_actually_reads_the_keys_this_file_asserts_about():
    """Guard the derivation itself.

    Every test below is only as good as the AST walk that feeds it. If a refactor
    moved these names behind a computed string, `_seam_env_names()` would return
    an empty set and the documentation tests would all pass vacuously -- the
    two-empty-sides-is-not-agreement failure this repo has a rule about.
    """
    names = _seam_env_names()
    assert len(names) >= 5, f"derivation found too few env names to be trusted: {names}"
    assert "FLOCI_ENABLED" in names
    assert set(emulator.DEPRECATED_ALIASES.values()) <= names


def test_every_canonical_key_the_seam_reads_is_documented():
    """The card's subject: an operator can discover every setting that exists."""
    documented = set(_documented_assignments())
    missing = sorted(_floci_names() - documented)
    assert not missing, (
        f"the seam reads {missing} and .env.example documents neither a live nor a "
        "commented assignment for them"
    )


def test_no_documented_floci_key_is_one_the_seam_never_reads():
    """The reverse direction, and it is the same defect pointing the other way.

    A documented `FLOCI_*` key nothing reads is a setting an operator will set
    and watch do nothing -- configuration residue manufactured by the document
    that was supposed to explain the configuration.
    """
    documented = {k for k in _documented_assignments() if k.startswith("FLOCI_")}
    invented = sorted(documented - _floci_names())
    assert not invented, f".env.example documents {invented}, which the seam never reads"


def test_there_is_no_documented_credential_setting():
    """`credentials()` ALWAYS returns the dummy pair and reads no env at all.

    Documenting a `FLOCI_ACCESS_KEY` would invite an operator to put a real
    GovCloud key where the seam refuses to look -- and the reason it refuses is
    that these values are passed to `docker run -e` and into a Terraform provider
    block aimed at localhost.
    """
    assert emulator.credentials({"AWS_ACCESS_KEY_ID": "AKIAREAL"}) == ("test", "test")
    documented = {k for k in _documented_assignments() if k.startswith("FLOCI_")}
    creds = sorted(k for k in documented if "KEY" in k or "SECRET" in k or "CRED" in k)
    assert not creds, f".env.example invents credential settings the seam never reads: {creds}"


# -- The deprecated aliases ------------------------------------------------


@pytest.mark.parametrize(
    ("canonical", "alias"), sorted(emulator.DEPRECATED_ALIASES.items())
)
def test_every_deprecated_alias_is_documented_beside_the_name_that_replaces_it(
    canonical: str, alias: str
):
    """An alias listed without its replacement tells an operator they are wrong
    and not what to do instead."""
    lines = [ln for ln in _env_example_lines() if alias in ln]
    assert lines, f"{alias} is honoured by the seam and documented nowhere in .env.example"
    assert any(canonical in ln for ln in lines), (
        f"{alias} is documented but never beside {canonical}, so the file names the "
        "deprecated spelling without naming its replacement"
    )


def test_the_deprecation_carries_the_date_the_seam_warns_with():
    """The seam's own runtime warning cites a date; the document must cite the same
    one, or an operator reading the log and the file gets two answers."""
    seam_dates = set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", _SEAM.read_text(encoding="utf-8")))
    warned = {d for d in seam_dates if f"deprecated {d}" in _SEAM.read_text(encoding="utf-8")}
    assert warned, "the seam no longer states a deprecation date; update this test with it"

    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    block = text[text.index("FLOCI_ENABLED") :]
    assert any(d in block for d in warned), (
        f"the FLOCI block does not carry the deprecation date the seam warns with ({warned})"
    )


# -- The documented defaults match the code's ------------------------------


@pytest.mark.parametrize(
    ("key", "constant"),
    [
        ("FLOCI_ENDPOINT", emulator.DEFAULT_ENDPOINT),
        ("FLOCI_REGION", emulator.DEFAULT_REGION),
        ("FLOCI_ACCOUNT_ID", emulator.DEFAULT_ACCOUNT_ID),
    ],
)
def test_documented_default_matches_the_seams_own_constant(key: str, constant: str):
    """A documented default that drifts from the applied default is worse than an
    undocumented one: it is a specific, checkable, wrong claim."""
    documented = _documented_assignments()
    assert key in documented, f"{key} is not documented in .env.example"
    assert documented[key] == constant, (
        f".env.example documents {key}={documented[key]!r} while the seam applies "
        f"{constant!r}"
    )


def test_the_default_posture_is_stated_and_it_is_off():
    """`FLOCI_ENABLED=false` ships LIVE, because the switch is the one setting whose
    value an operator must see rather than infer from a comment."""
    assert _active_settings().get("FLOCI_ENABLED") == "false"
    assert emulator.enabled(_active_settings()) is False


def test_each_documented_key_is_explained_by_a_comment_above_it():
    """The card asks for a one-line explanation per key. An uncommented block of
    names is a list, not documentation."""
    lines = _env_example_lines()
    unexplained = []
    for idx, line in enumerate(lines):
        match = _ASSIGNMENT_RE.match(line.lstrip("#").strip())
        if not match or not match.group(1).startswith("FLOCI_"):
            continue
        # Walk up through the contiguous comment block above this assignment.
        prose = []
        cursor = idx - 1
        while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
            body = lines[cursor].lstrip("#").strip()
            if not _ASSIGNMENT_RE.match(body):
                prose.append(body)
            cursor -= 1
        if not any(len(p.split()) >= 4 for p in prose):
            unexplained.append(match.group(1))
    assert not unexplained, f"documented with no explanatory comment above: {unexplained}"


# -- The consequence, measured through the consumer ------------------------


def test_a_copied_env_example_does_not_downgrade_real_aws_to_dry_run():
    """THE SAFETY CASE, and the reason FLOCI_ENDPOINT ships commented out.

    `detect_mode` answers `dry_run` for an endpoint declared while the switch is
    off -- deliberately, because reading that contradiction as "no emulator, so
    use real AWS" is how a `terraform apply` written for localhost reaches a real
    account. The consequence for THIS file is the mirror image: ship an
    uncommented `FLOCI_ENDPOINT` in the example and every operator who copies it
    with the switch off silently loses `aws` mode -- every real apply becomes
    plan-only, and nothing anywhere goes red.

    Asked through the shipped `detect_mode` rather than by asserting a `#`.
    """
    from tools.studio.executors._base import detect_mode

    with_real_credentials = {
        **_active_settings(),
        "AWS_ACCESS_KEY_ID": "AKIAREAL",
        "AWS_SECRET_ACCESS_KEY": "real",
    }
    assert detect_mode(with_real_credentials) == "aws", (
        "a .env copied from .env.example downgrades real AWS to dry_run -- an "
        "emulator endpoint is declared while FLOCI_ENABLED is false"
    )
    assert emulator.endpoint_declared(_active_settings()) is False


def test_the_example_file_alone_reaches_no_emulator():
    """The air-gap-safe posture, asserted on the file an operator actually copies."""
    assert emulator.status(_active_settings(), probe=False) == emulator.STATUS_DISABLED


# -- Registration ----------------------------------------------------------


def test_the_seam_has_no_cli_so_the_docs_must_document_the_import():
    """Re-derives the premise instead of assuming it.

    `check_doc_command_paths` resolves a documented `python tools/...py` to a
    file; the file existing is not the same as it being runnable. This asserts
    the seam genuinely has no argparse and no `__main__` entry, which is what
    makes documenting an import correct and documenting a CLI a fabrication.
    """
    source = _SEAM.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "argparse" not in {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert '__name__ == "__main__"' not in source and "__name__ == '__main__'" not in source

    doc = _COMMANDS_DOC.read_text(encoding="utf-8")
    assert "python tools/cloud/emulator.py" not in doc, (
        "commands.md documents a CLI the seam does not have"
    )
    assert "from tools.cloud import emulator" in doc, (
        "commands.md does not document the import for the emulator seam"
    )


def test_commands_md_names_the_seam_and_its_switch():
    doc = _COMMANDS_DOC.read_text(encoding="utf-8")
    assert "tools/cloud/emulator.py" in doc
    assert "FLOCI_ENABLED" in doc


@pytest.mark.parametrize("root", ["", "icdev"])
def test_both_manifest_copies_register_the_seam(root: str):
    """The shard is duplicated under `icdev/`, which is what ships in the wheel.

    Registering in one copy leaves the packaged manifest silently missing the
    tool -- the same half-mirrored shape `args/mirror_parity_gate.yaml` exists
    for, on a file type that gate deliberately does not cover.
    """
    shard = _ROOT / root / _MANIFEST_SHARD if root else _ROOT / _MANIFEST_SHARD
    rows = [ln for ln in shard.read_text(encoding="utf-8").splitlines() if "cloud/emulator.py" in ln]
    assert len(rows) == 1, f"{shard.relative_to(_ROOT)} registers the seam {len(rows)} times"
