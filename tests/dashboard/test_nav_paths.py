# CUI // SP-CTI
"""nav_paths: the two shared nav surfaces are DERIVED, not hand-appended (mfx-sib-02).

The ``- Pages:`` line in ``.claude/commands/start.md`` and the Compliance
dropdown's ``request.path in [...]`` active-path list in ``base.html`` were
lists nothing generated. Every route-migration card appended one token to each,
so N cards of one epic collided N-1 times on two lines neither of them was
really editing.

These tests pin the properties that make the generator trustworthy:

  * it DERIVES — the menu's own links plus app.py's 301 aliases produce the
    list, so a hand edit is drift the ``--check`` can see;
  * it PRESERVES BEHAVIOUR — every path the hand-written list highlighted is
    still highlighted, either by the derived list or by a retained prefix
    guard. Asserted against the pre-change list captured from git history,
    never against the list this change wrote;
  * it is IDEMPOTENT and MIRRORED — ``--write`` twice is a no-op, and both
    base.html copies carry byte-identical generated blocks.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.dashboard import nav_paths  # noqa: E402

#: The Compliance active-path list EXACTLY as it stood before this card, taken
#: from base.html at commit ae89b9147. The behaviour-preservation test compares
#: against THIS, not against whatever the generator currently emits -- a test
#: that read the generated list would prove only that the generator agrees
#: with itself.
LEGACY_COMPLIANCE_PATHS = [
    "/compliance", "/boundary/compliance-hub", "/oscal", "/boundary/oscal",
    "/prod-audit", "/security/prod-audit", "/ai-transparency",
    "/security/ai-transparency", "/security/stig-manager", "/security/sbd",
    "/ai-accountability", "/security/ai-accountability", "/sbd", "/cato",
    "/boundary/cato-health", "/boundary/poam", "/boundary/fedramp-20x",
    "/compliance-debt", "/boundary/compliance-debt", "/ato-package",
    "/boundary/ato-package", "/ato-compliance", "/boundary/ato-compliance",
    "/fedramp-20x", "/control-inheritance", "/boundary/control-inheritance",
    "/mosa", "/boundary/mosa", "/analytics", "/poam", "/security-scan",
    "/pr-intel", "/stig-manager", "/sre", "/iac", "/migration",
    "/migration-cost", "/provenance", "/traces", "/xai",
]

#: The ``startswith(...)`` clauses that stay HAND-WRITTEN in the ``{% if %}``,
#: outside the generated block. A prefix is a policy ("everything under the
#: Security canvas is Compliance"); the generator only ever produces the
#: enumerated half.
RETAINED_PREFIXES = ("/security", "/zta", "/integrity")

SANDBOX_FILES = (
    "args/nav_paths.yaml",
    "tools/dashboard/templates/base.html",
    "icdev/tools/dashboard/templates/base.html",
    ".claude/commands/start.md",
    "tools/dashboard/app.py",
)


def _sandbox(tmp_path: Path, extra: tuple[str, ...] = ()) -> Path:
    """A minimal copy of the tree the generator reads and writes."""
    root = tmp_path / "tree"
    for rel in SANDBOX_FILES + extra:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE_DIR / rel, dst)
    return root


# -- derivation ---------------------------------------------------------------

def test_every_menu_link_is_in_the_derived_list():
    """The intrinsic meaning of `active`: you are on a page THIS menu links to."""
    derived = set(nav_paths.derive_nav_paths("compliance"))
    menu = set(nav_paths.menu_hrefs("compliance"))
    assert menu, "no menu links parsed -- the dropdown anchor no longer matches"
    assert menu <= derived, sorted(menu - derived)


def test_a_301_alias_is_derived_rather_than_hand_listed():
    """`/control-inheritance` is listed because app.py 301s it to a menu page.

    That is the exact token rmf-ui-08 hand-appended. Nothing in the config
    names it; it falls out of (menu link) + (redirect literal in app.py).
    """
    aliases = nav_paths.legacy_aliases()
    assert aliases.get("/boundary/control-inheritance") == ["/control-inheritance"]
    derived = nav_paths.derive_nav_paths("compliance")
    assert "/control-inheritance" in derived
    assert "/boundary/control-inheritance" in derived


def test_an_alias_to_a_page_outside_this_menu_is_not_pulled_in():
    """A 301 is an alias for THIS dropdown only if its target is in THIS menu."""
    derived = set(nav_paths.derive_nav_paths("compliance"))
    aliases = nav_paths.legacy_aliases()
    menu = set(nav_paths.menu_hrefs("compliance"))
    extras = set(nav_paths.declared_extras("compliance"))
    for target, olds in aliases.items():
        if target in menu:
            continue
        for old in olds:
            if old in extras:
                continue
            assert old not in derived, f"{old} -> {target} is not in the Compliance menu"


def test_derived_list_is_sorted_and_deduplicated():
    derived = nav_paths.derive_nav_paths("compliance")
    assert derived == sorted(set(derived))


# -- behaviour preservation ---------------------------------------------------

def test_no_previously_highlighted_path_stops_highlighting():
    """Every legacy token still resolves -- via the list, or a retained prefix."""
    derived = set(nav_paths.derive_nav_paths("compliance"))
    lost = [
        p for p in LEGACY_COMPLIANCE_PATHS
        if p not in derived and not p.startswith(RETAINED_PREFIXES)
    ]
    assert lost == [], f"paths that would stop highlighting Compliance: {lost}"


def test_paths_the_derivation_cannot_see_are_declared_with_a_reason():
    """A legacy token that is neither a menu link nor an alias is DECLARED.

    `/iac`, `/migration` and friends are not in the Compliance menu and nothing
    redirects to them. Rather than lose them silently in a template line they
    sit in args/nav_paths.yaml carrying a written reason -- visible, and
    deletable by whoever decides they do not belong there.
    """
    extras = nav_paths.declared_extras("compliance")
    reasons = nav_paths.declared_extra_reasons("compliance")
    assert extras, "the residue is empty -- behaviour preservation is unproven"
    for path in extras:
        assert reasons.get(path, "").strip(), f"{path} is declared with no reason"


# -- the committed tree agrees with the derivation ----------------------------

def test_committed_nav_block_matches_the_derivation():
    """The drift guard. A hand edit to the generated block fails here and in CI."""
    report = nav_paths.check_nav()
    assert report["ok"], report["diffs"]


def test_both_base_html_copies_carry_identical_generated_blocks():
    blocks = [nav_paths.read_block(p, nav_paths.NAV_MARKER) for p in nav_paths.nav_targets()]
    assert len(blocks) == 2, "expected tools/ and the icdev/ mirror"
    assert blocks[0] is not None
    assert blocks[0] == blocks[1]


def test_start_md_pages_block_is_present_and_marked():
    block = nav_paths.read_block(nav_paths.pages_target(), nav_paths.PAGES_MARKER)
    assert block is not None, "start.md has no generated Pages block"
    assert block.lstrip().startswith("- Pages:")


def test_write_is_idempotent(tmp_path):
    """`--write` twice changes nothing the second time."""
    sandbox = _sandbox(tmp_path)
    first = nav_paths.write_nav(root=sandbox)
    before = (sandbox / "tools/dashboard/templates/base.html").read_bytes()
    second = nav_paths.write_nav(root=sandbox)
    after = (sandbox / "tools/dashboard/templates/base.html").read_bytes()
    assert before == after
    assert first["paths"] == second["paths"]
    assert second["changed"] == []


# -- the pages half, without paying for an app import -------------------------

def test_pages_filter_keeps_pages_and_drops_api_rules():
    rules = [
        {"rule": "/boundary/oscal", "methods": ["GET", "HEAD"]},
        {"rule": "/api/kanban/tasks", "methods": ["GET"]},
        {"rule": "/proposals/<id>/api/export", "methods": ["GET"]},
        {"rule": "/rfp/upload", "methods": ["POST"]},
        {"rule": "/", "methods": ["GET"]},
    ]
    assert nav_paths.pages_from_rules(rules) == ["/", "/boundary/oscal"]


def test_pages_filter_is_sorted_and_deduplicated():
    rules = [
        {"rule": "/b", "methods": ["GET"]},
        {"rule": "/a", "methods": ["GET"]},
        {"rule": "/b", "methods": ["GET", "POST"]},
    ]
    assert nav_paths.pages_from_rules(rules) == ["/a", "/b"]


def test_probe_env_is_scrubbed_and_declared():
    """The url_map depends on env toggles, so the probe env cannot be inherited.

    Measured 2026-09-04 on this tree: the SAME checkout yields 4568 rules under
    this session's ambient environment and 2370 under a bare one. A `--check`
    reading the ambient env passes or fails according to whose shell ran it.
    """
    env = nav_paths.probe_env(root=BASE_DIR)
    assert "ICDEV_PG_DATABASE" not in env
    assert env["ICDEV_STORAGE_BACKEND"] == "sqlite"
    assert env["ICDEV_DB_PATH"].endswith(".db")
    assert env["PYTHONPATH"] == str(BASE_DIR)

    # Every registry component is forced ON, so the documented page list is the
    # SUPERSET of pages the platform can serve rather than one machine's toggle
    # state. Asserted against the registry rather than against a hand-picked
    # flag name: a canvas added tomorrow has to be covered too.
    from tools.config.component_registry import ComponentRegistry

    declared = {
        flag
        for component in ComponentRegistry().list_all()
        for flag in ([getattr(component, "env_flag", None)]
                     + list(getattr(component, "extra_env_flags", None) or []))
        if flag
    }
    assert declared, "the component registry declared no env flags"
    off = [flag for flag in sorted(declared) if env.get(flag, "").lower() not in ("true", "1")]
    assert off == [], f"registry toggles the probe would leave off: {off}"


# The live url_map derivation is DELIBERATELY NOT a pytest test. It creates the
# whole Flask app (~16s measured) and it is gated as its own `test-gates` step,
# `python tools/dashboard/nav_paths.py --check --pages-only`. Writing it here
# behind a skipif would have registered a skip in args/ci_skip_census.txt --
# a gated test that skips is UNMEASURED, not passing, and the debt would sit
# next to a check that already runs unconditionally in CI. The filter it
# applies to the rules is pure and IS tested, above.


# -- the CLI contract the hook and CI depend on -------------------------------

def test_check_cli_exits_zero_on_the_committed_tree():
    proc = subprocess.run(
        [sys.executable, "tools/dashboard/nav_paths.py", "--check", "--nav-only"],
        cwd=str(BASE_DIR), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pre_commit_gate_is_scoped_to_the_files_that_can_change_the_answer():
    """The hook must cost NOTHING on a commit that cannot have moved the list.

    Four files can change the derivation: either base.html copy (the menu),
    app.py (the 301s) and the generator's own config. A commit touching none of
    them runs no subprocess at all -- which is why this gate can be
    unconditional in the hook rather than opt-in.
    """
    # importlib rather than `from tools.testing import ...`: the tools/ and
    # icdev/tools/ spellings resolve to ONE module object in a source checkout
    # (xit-decl-02), and naming the module by its dotted path is the idiom the
    # rest of the suite uses for that reason.
    pre_commit_check = importlib.import_module("tools.testing.pre_commit_check")

    assert not pre_commit_check._nav_paths_in_scope([])
    assert not pre_commit_check._nav_paths_in_scope(
        ["tools/kanban/cli.py", "tests/dashboard/test_nav_paths.py", "README.md"]
    )
    for path in (
        "tools/dashboard/templates/base.html",
        "icdev/tools/dashboard/templates/base.html",
        "tools/dashboard/app.py",
        "args/nav_paths.yaml",
    ):
        assert pre_commit_check._nav_paths_in_scope([path]), path

    # Every file the generator reads to derive the nav list is in scope. Derived
    # from the config, so a fifth redirect source added tomorrow fails here
    # rather than silently escaping the hook.
    declared = set(nav_paths.load_config().get("nav", {}).get("redirect_sources") or [])
    declared |= {
        str(p.relative_to(BASE_DIR)).replace("\\", "/") for p in nav_paths.nav_targets()
    }
    declared.add(nav_paths.CONFIG_RELPATH)
    uncovered = sorted(d for d in declared if not pre_commit_check._nav_paths_in_scope([d]))
    assert uncovered == [], f"the hook would not fire for: {uncovered}"


def test_pre_commit_gate_passes_on_the_committed_tree():
    pre_commit_check = importlib.import_module("tools.testing.pre_commit_check")

    assert pre_commit_check._run_nav_paths_check(["tools/dashboard/templates/base.html"])


def test_a_hand_edit_to_the_generated_block_is_reported_as_drift(tmp_path):
    sandbox = _sandbox(tmp_path)
    target = sandbox / "tools/dashboard/templates/base.html"
    text = target.read_text(encoding="utf-8")
    edited = text.replace(
        "compliance_active_paths = [",
        "compliance_active_paths = ['/hand-edited', ",
        1,
    )
    assert edited != text, "the generated {% set %} line was not found to edit"
    target.write_text(edited, encoding="utf-8")

    report = nav_paths.check_nav(root=sandbox)
    assert not report["ok"]
    assert report["diffs"]
