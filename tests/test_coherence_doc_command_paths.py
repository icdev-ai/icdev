# CUI // SP-CTI
"""Tests for check_doc_command_paths coherence check (oss-fix-02).

Builds a synthetic repo under tmp_path containing a doc file and a tools/ tree,
points PROJECT_ROOT and the gate config at it, then asserts each outcome:
resolving references pass, a broken reference FAILS, a grandfathered broken
reference is downgraded to WARN, and stale allowlist entries are surfaced.

Also asserts the real repo has no NEW broken documented commands, so the gate
protects CLAUDE.md / docs/reference/commands.md going forward.
"""
from __future__ import annotations

import pathlib
import sys
import textwrap


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


def _build_repo(tmp_path: pathlib.Path, doc_body: str, tool_paths=(), config: str = "") -> pathlib.Path:
    """Write a synthetic repo: one doc file, zero or more real tool files, a gate config."""
    repo = tmp_path / "repo"
    doc = repo / "docs" / "reference" / "commands.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(textwrap.dedent(doc_body), encoding="utf-8")

    for rel in tool_paths:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# CUI // SP-CTI\n", encoding="utf-8")

    cfg = repo / "args" / "doc_command_gate.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(config or "docs:\n  - docs/reference/commands.md\n", encoding="utf-8")
    return repo


def _run(tmp_path, monkeypatch, doc_body, tool_paths=(), config=""):
    repo = _build_repo(tmp_path, doc_body, tool_paths, config)
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    monkeypatch.setattr(cc, "_DOC_COMMAND_CONFIG", repo / "args" / "doc_command_gate.yaml")
    monkeypatch.setattr(cc, "_DOC_COMMAND_DEFAULT_DOCS", ("docs/reference/commands.md",))
    return cc.check_doc_command_paths()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_resolving_references_pass(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        """
        ```bash
        python tools/db/storage.py --health --json
        python tools/memory/memory_read.py --format markdown
        ```
        """,
        tool_paths=("tools/db/storage.py", "tools/memory/memory_read.py"),
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.message}"
    assert result.check_id == "doc_command_paths"
    assert result.missing == []


def test_dotted_module_form_resolves_via_package_init(tmp_path, monkeypatch):
    """`python -m tools.airgap` resolves through tools/airgap/__init__.py."""
    result = _run(
        tmp_path,
        monkeypatch,
        """
        ```bash
        python -m tools.airgap --detect --json
        python -m tools.cortex.service_keys --list
        ```
        """,
        tool_paths=("tools/airgap/__init__.py", "tools/cortex/service_keys.py"),
    )
    assert result.status == "pass", result.message


# ---------------------------------------------------------------------------
# Failure path — the whole point of the gate
# ---------------------------------------------------------------------------

def test_broken_reference_fails_with_file_and_line(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        """
        ```bash
        python tools/db/storage.py --health
        python tools/showcase/validator.py --app foo --json
        ```
        """,
        tool_paths=("tools/db/storage.py",),
    )
    assert result.status == "fail", f"Expected fail, got {result.status}: {result.message}"
    assert "tools/showcase/validator.py" in result.missing
    # The operator needs the exact doc:line to fix it.
    assert any("docs/reference/commands.md:4" in a for a in result.actual), result.actual


def test_broken_dotted_module_fails(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        """
        ```bash
        python -m tools.ghost.module --run
        ```
        """,
    )
    assert result.status == "fail", result.message
    assert "tools.ghost.module" in result.missing


def test_directory_without_init_does_not_satisfy_dotted_reference(tmp_path, monkeypatch):
    """A bare directory is not an importable module — must not pass."""
    repo = _build_repo(tmp_path, "python -m tools.emptypkg --go\n")
    (repo / "tools" / "emptypkg").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    monkeypatch.setattr(cc, "_DOC_COMMAND_CONFIG", repo / "args" / "doc_command_gate.yaml")
    monkeypatch.setattr(cc, "_DOC_COMMAND_DEFAULT_DOCS", ("docs/reference/commands.md",))
    result = cc.check_doc_command_paths()
    assert result.status == "fail", result.message


# ---------------------------------------------------------------------------
# Grandfathering
# ---------------------------------------------------------------------------

GF_CONFIG = textwrap.dedent(
    """
    docs:
      - docs/reference/commands.md
    grandfathered:
      tools/dochub/doc_generator.py: "DocHub never built"
    """
)


def test_grandfathered_reference_downgrades_to_warn(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        """
        ```bash
        python tools/dochub/doc_generator.py --json
        ```
        """,
        config=GF_CONFIG,
    )
    assert result.status == "warn", f"Expected warn, got {result.status}: {result.message}"
    assert result.missing == ["tools/dochub/doc_generator.py"]
    assert "NEW" in result.message


def test_grandfathering_does_not_excuse_a_different_broken_reference(tmp_path, monkeypatch):
    """The allowlist is per-path — it must not blanket-excuse the doc file."""
    result = _run(
        tmp_path,
        monkeypatch,
        """
        ```bash
        python tools/dochub/doc_generator.py --json
        python tools/dochub/brand_new_gap.py --json
        ```
        """,
        config=GF_CONFIG,
    )
    assert result.status == "fail", result.message
    assert result.missing == ["tools/dochub/brand_new_gap.py"]


def test_stale_allowlist_entry_is_surfaced(tmp_path, monkeypatch):
    """An entry whose target now exists should be reported so the list shrinks."""
    result = _run(
        tmp_path,
        monkeypatch,
        """
        ```bash
        python tools/dochub/doc_generator.py --json
        ```
        """,
        tool_paths=("tools/dochub/doc_generator.py",),
        config=GF_CONFIG,
    )
    assert result.status == "warn", result.message
    assert "tools/dochub/doc_generator.py" in result.extra
    assert "stale" in result.message


def test_uncited_allowlist_entry_is_stale(tmp_path, monkeypatch):
    """An entry no longer referenced by any doc is dead weight."""
    result = _run(
        tmp_path,
        monkeypatch,
        "python tools/db/storage.py --health\n",
        tool_paths=("tools/db/storage.py",),
        config=GF_CONFIG,
    )
    assert result.status == "warn", result.message
    assert "tools/dochub/doc_generator.py" in result.extra


def test_missing_config_fails_closed(tmp_path, monkeypatch):
    """No allowlist file => nothing is excused; the gate gets stricter, not looser."""
    repo = _build_repo(tmp_path, "python tools/dochub/doc_generator.py --json\n")
    (repo / "args" / "doc_command_gate.yaml").unlink()
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    monkeypatch.setattr(cc, "_DOC_COMMAND_CONFIG", repo / "args" / "doc_command_gate.yaml")
    monkeypatch.setattr(cc, "_DOC_COMMAND_DEFAULT_DOCS", ("docs/reference/commands.md",))
    result = cc.check_doc_command_paths()
    assert result.status == "fail", result.message


def test_malformed_config_fails_closed(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        "python tools/dochub/doc_generator.py --json\n",
        config="docs:\n  - docs/reference/commands.md\ngrandfathered: [[[not-a-map\n",
    )
    assert result.status == "fail", result.message


# ---------------------------------------------------------------------------
# Registration + live-repo contract
# ---------------------------------------------------------------------------

def test_check_is_registered():
    assert "doc_command_paths" in cc.CHECK_REGISTRY
    assert cc._FIX_REGISTRY.get("doc_command_paths") == "skip"


def test_live_repo_has_no_new_broken_documented_commands():
    """Regression guard: CLAUDE.md / commands.md must not gain broken commands.

    Pre-existing breakage is grandfathered in args/doc_command_gate.yaml, so a
    'fail' here means a NEW documented command points at a nonexistent file.
    """
    result = cc.check_doc_command_paths()
    assert result.status != "fail", result.message


def test_showcase_validator_is_no_longer_documented():
    """oss-fix-02: the phantom showcase tools were removed from the docs."""
    for doc in ("CLAUDE.md", "docs/reference/commands.md", "tools/manifest/showcase.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        for phantom in ("showcase/validator.py", "showcase/generate_app.py", "showcase/osint_engine.py"):
            assert phantom not in text, f"{phantom} still referenced in {doc}"
