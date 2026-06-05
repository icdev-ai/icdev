# CUI // SP-CTI
"""Tests for the SIPA claim parser (sipa-intent-01).

Covers the acceptance criteria for ``tools/integrity/claim_parser.py``:

  * ``parse_claim(path, declared_purpose=None)`` -> ``{claimed_capabilities: set,
    sources: [...]}`` derived from README.* files, ``.py`` module/package
    docstrings (``ast`` only — never imported/executed), and a caller-supplied
    ``declared_purpose`` string.
  * Natural-language claims map to ``constants.CAPABILITY_TYPES`` via a
    deterministic keyword/rule lexicon.
  * **The task's two anchor cases:** a README that says "formats JSON" yields an
    EMPTY network/process claim set; one that says "uploads results to the API"
    yields ``network_egress``.
  * Absence of a claim is the Mode B signal — exercised-minus-claimed surfaces an
    undisclosed capability.

Pure stdlib + constants; no DB, no network, no subprocess.
"""
from pathlib import Path

from tools.integrity import claim_parser as cp
from tools.integrity import constants


def _write(tmp_path: Path, name: str, text: str) -> Path:
    fp = tmp_path / name
    fp.write_text(text, encoding="utf-8")
    return fp


# --------------------------------------------------------------------------- #
# Anchor case 1 — "formats JSON" claims no network / process capability
# --------------------------------------------------------------------------- #
def test_readme_formats_json_claims_no_network_or_process(tmp_path):
    _write(tmp_path, "README.md", "# jsonfmt\n\nThis tool formats JSON files.\n")
    result = cp.parse_claim(str(tmp_path))

    claimed = result["claimed_capabilities"]
    assert "network_egress" not in claimed, f"unexpected network claim: {claimed}"
    assert "process_exec" not in claimed, f"unexpected process claim: {claimed}"


# --------------------------------------------------------------------------- #
# Anchor case 2 — "uploads results to the API" claims network_egress
# --------------------------------------------------------------------------- #
def test_readme_uploads_to_api_claims_network_egress(tmp_path):
    _write(
        tmp_path,
        "README.rst",
        "Results processor\n=================\n\nUploads results to the API "
        "when a run completes.\n",
    )
    result = cp.parse_claim(str(tmp_path))

    assert "network_egress" in result["claimed_capabilities"]
    # Evidence is retained for the HITL reviewer.
    net_sources = [
        s for s in result["sources"] if "network_egress" in s["capabilities"]
    ]
    assert net_sources, "expected a source crediting the network claim"
    phrases = net_sources[0]["matched"]["network_egress"]
    assert any(p in ("upload", "api") for p in phrases), phrases


# --------------------------------------------------------------------------- #
# Return shape + taxonomy validity
# --------------------------------------------------------------------------- #
def test_return_shape_and_known_capabilities(tmp_path):
    _write(tmp_path, "README.txt", "Encrypts and uploads your backups.")
    result = cp.parse_claim(str(tmp_path))

    assert set(result) == {"claimed_capabilities", "sources"}
    assert isinstance(result["claimed_capabilities"], set)
    # Every claimed capability is a member of the canonical taxonomy.
    for cap in result["claimed_capabilities"]:
        assert cap in constants.CAPABILITY_TYPES
    for src in result["sources"]:
        assert set(src) >= {"kind", "ref", "capabilities", "matched"}


def test_lexicon_keys_are_all_valid_capabilities():
    # Guard against a typo silently dropping a whole capability from Mode B.
    assert set(cp._LEXICON) <= set(constants.CAPABILITY_TYPES)


# --------------------------------------------------------------------------- #
# Source: module docstring (ast only — never executed)
# --------------------------------------------------------------------------- #
def test_module_docstring_is_parsed_for_claims(tmp_path):
    src = '''\
"""A helper that signs payloads and writes them to a cache file."""
import os  # never executed by the parser


def go():
    raise SystemExit("this code is never run during parsing")
'''
    _write(tmp_path, "mod.py", src)
    result = cp.parse_claim(str(tmp_path))

    claimed = result["claimed_capabilities"]
    assert "crypto" in claimed       # "signs"
    assert "filesystem" in claimed   # "writes ... cache file"
    docstring_sources = [s for s in result["sources"] if s["kind"] == "docstring"]
    assert docstring_sources, "expected a docstring source"


def test_single_python_file_uses_its_docstring(tmp_path):
    fp = _write(
        tmp_path,
        "single.py",
        '"""Downloads updates over HTTPS."""\n\nX = 1\n',
    )
    result = cp.parse_claim(str(fp))
    assert "network_egress" in result["claimed_capabilities"]


# --------------------------------------------------------------------------- #
# Source: declared_purpose string
# --------------------------------------------------------------------------- #
def test_declared_purpose_folds_into_claimed_set(tmp_path):
    _write(tmp_path, "README.md", "A quiet little library.")
    result = cp.parse_claim(
        str(tmp_path),
        declared_purpose="Spawns a subprocess to run the build command.",
    )
    assert "process_exec" in result["claimed_capabilities"]
    dp = [s for s in result["sources"] if s["kind"] == "declared_purpose"]
    assert dp and dp[0]["ref"] == "<declared_purpose>"


def test_declared_purpose_none_is_safe(tmp_path):
    _write(tmp_path, "README.md", "Formats JSON.")
    result = cp.parse_claim(str(tmp_path), declared_purpose=None)
    assert isinstance(result["claimed_capabilities"], set)


# --------------------------------------------------------------------------- #
# Mode B signal — absence of a claim
# --------------------------------------------------------------------------- #
def test_undisclosed_capabilities_surfaces_unclaimed_exercised(tmp_path):
    # README discloses only JSON formatting; the code (per the extractor) actually
    # opened a socket + shelled out -> both are undisclosed.
    _write(tmp_path, "README.md", "Formats JSON nicely.")
    claim = cp.parse_claim(str(tmp_path))

    exercised = {"network_egress", "process_exec", "filesystem"}
    undisclosed = cp.undisclosed_capabilities(claim, exercised)
    assert undisclosed == {"network_egress", "process_exec", "filesystem"}


def test_undisclosed_empty_when_claim_covers_behavior(tmp_path):
    _write(tmp_path, "README.md", "Uploads results to the API over HTTPS.")
    claim = cp.parse_claim(str(tmp_path))

    exercised = {"network_egress"}
    assert cp.undisclosed_capabilities(claim, exercised) == set()


# --------------------------------------------------------------------------- #
# Map helper directly + multi-capability prose
# --------------------------------------------------------------------------- #
def test_map_text_multi_capability():
    text = (
        "Reads an API key from an environment variable, encrypts the payload, "
        "uploads it to the endpoint, and caches a copy to disk."
    )
    matched = cp.map_text(text)
    assert "env_secret" in matched
    assert "crypto" in matched
    assert "network_egress" in matched
    assert "filesystem" in matched


def test_map_text_empty_for_benign_prose():
    assert cp.map_text("Adds two numbers and prints the sum.") == {}
    assert cp.map_text("") == {}


def test_word_boundary_avoids_false_positives():
    # "rapid" must not match "api"; "therapist" must not match either.
    assert "network_egress" not in cp.map_text("A rapid, therapist-approved UI.")


# --------------------------------------------------------------------------- #
# Nonexistent path is non-fatal
# --------------------------------------------------------------------------- #
def test_missing_path_returns_empty_claim(tmp_path):
    result = cp.parse_claim(str(tmp_path / "does-not-exist"))
    assert result["claimed_capabilities"] == set()
    assert result["sources"] == []
