#!/usr/bin/env python3
# CUI // SP-CTI
"""End-to-end ICDEV(TM) release: version bump -> notes gate -> build -> publish.

This is the FRONT and BACK of the release. The middle — sync, validate, build,
wheel inspection, throwaway-venv smoke, air-gap verification — already exists in
``build_release.py`` and is delegated to, never reimplemented.

    release.py                      build_release.py
    ----------                      ----------------
    1. preflight                 -> 4. sync_package_tree --clean
    2. bump versions                5. validate_package_config --gate
    3. notes gate                   6. clean dist/ + python -m build
                                    7. inspect the wheel
                                    8. smoke test in a throwaway venv
                                    9. air-gap offline install
    10. publish (opt-in)

WHY THIS EXISTS

Three consecutive releases went out broken because the pipeline was driven by
hand:

  * **1.2.40** — `sync_package_tree.py` was never run, so the wheel shipped 29
    differing and 53 missing ``args/`` files, including the
    ``component_registry.yaml`` that 1.2.39 had just fixed for pip installs.
  * **1.2.41** — the sync then copied back-compat SHIMS over their real twins.
    ``icdev/tools/llm/agent_loop.py`` went from 1,825 lines to an 89-line stub
    that imported from itself; ``import icdev.tools.llm.agent_loop`` raised
    ImportError for anyone who installed it.
  * Both passed ``twine check``. **A wheel that cannot import passes twine
    check** — it validates metadata, not behaviour.
  * Worse, the guard added to ``sync_package_tree`` after 1.2.41 could not fire
    on the DOCUMENTED path: ``build_release.py`` runs ``--clean``, which deleted
    the mirror before the guard had a target to protect. Every release built the
    intended way would have shipped the same hollow module. Fixed by making
    ``--clean`` git-restore the tracked tree, and caught here regardless by the
    self-import check in ``step_verify_payload`` — a defect this cheap to detect
    should never again depend on one tool getting its ordering right.

Every one of those was preventable by running ``build_release.py``, whose step 8
installs the built wheel into a clean venv and imports it. The failure was not
knowing the tool existed; it was reaching for ``python -m build`` directly. So
this script makes the whole path one command, and refuses the shortcuts:

  * ``--publish`` cannot be combined with ``--skip-smoke``. The smoke test is
    the only step that would have caught 1.2.41.
  * Publishing is opt-in. The default is a full dry run that builds and verifies
    but uploads nothing.
  * A version with no release notes will not publish. 1.2.38 and 1.2.39 shipped
    with no CHANGELOG entry at all, so ``/updates`` advertised 1.2.37 as newest
    for two releases.

USAGE

    # Dry run — bump, build, verify. Uploads NOTHING. Start here.
    python tools/installer/release.py --version 1.2.43

    # Same, choosing the number for you
    python tools/installer/release.py --bump patch

    # Scaffold the notes sections, then stop so you can write them
    python tools/installer/release.py --version 1.2.43 --scaffold-notes

    # The real thing
    python tools/installer/release.py --version 1.2.43 --publish

    # Just fix the version declarations, nothing else
    python tools/installer/release.py --version 1.2.43 --bump-only

Credentials come from ``.env`` (``TWINE_USERNAME``/``TWINE_PASSWORD``, or
``PYPI_API_TOKEN``) and are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR = REPO_ROOT / "dist"
ENV_FILE = REPO_ROOT / ".env"

#: Every file that declares the version, and how to find it.
#:
#: `icdev/_version.py` is the single source of truth; the others must agree.
#: They had drifted to THREE different numbers (brand.yaml 1.2.30, CHANGELOG
#: 1.2.37, pyproject/_version 1.2.39) because each was updated by hand.
#:
#: `icdev/data/args/brand.yaml` is deliberately absent — it is the packaged
#: mirror of `args/brand.yaml` and is written by sync_package_tree.py. Bumping
#: it here would be overwritten by the sync anyway.
VERSION_FILES: tuple[tuple[str, str, str], ...] = (
    ("icdev/_version.py", r'^__version__ = "(?P<v>[^"]+)"', '__version__ = "{v}"'),
    ("pyproject.toml", r'^version = "(?P<v>[^"]+)"', 'version = "{v}"'),
    ("args/brand.yaml", r'^version: "(?P<v>[^"]+)"', 'version: "{v}"'),
    # The Helm chart's appVersion. A chart advertising a version the package
    # never had makes "which build is this cluster running?" unanswerable, and
    # it had drifted to 21.0.0 while the package was 1.2.42. Chart `version:`
    # is deliberately NOT bumped here — that is the CHART's own revision and
    # moves when the templates change, not when the app does.
    ("deploy/helm/Chart.yaml", r'^appVersion: "(?P<v>[^"]+)"', 'appVersion: "{v}"'),
)

SOURCE_OF_TRUTH = "icdev/_version.py"

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


# --------------------------------------------------------------------------- #
# Version helpers
# --------------------------------------------------------------------------- #


def is_source_checkout() -> bool:
    """True when running from a git checkout rather than an installed wheel.

    `tools/installer/` is not in PARENT_ONLY_DIRS — correctly, because it also
    holds installer.py, module_registry.py and platform_setup.py, which end
    users need. This module comes along as a neighbour, so `pip install icdev`
    delivers a release tool that cannot possibly work: REPO_ROOT resolves to
    `site-packages/icdev`, where pyproject.toml, args/brand.yaml and
    deploy/helm/Chart.yaml do not exist, and preflight shells out to git in a
    directory that is not a repository.

    Detected by the presence of the files this script EDITS, rather than by
    looking for `.git`: a source tarball or an exported tree is still a valid
    place to cut a release, and a checkout with the version files missing is
    not one regardless of its git status.
    """
    return (REPO_ROOT / "pyproject.toml").is_file()


def _refuse_outside_checkout() -> int:
    print(
        "icdev release: this is a MAINTAINER tool for building and publishing "
        "ICDEV itself.\n"
        f"It is running from an installed package ({REPO_ROOT}), where the files "
        "it edits\n"
        "(pyproject.toml, args/brand.yaml, deploy/helm/Chart.yaml) do not exist.\n\n"
        "Run it from a source checkout of the icdev repository instead.\n\n"
        "If you are looking to set up an INSTALLED ICDEV, you want:\n"
        "  icdev init      # scaffold the project payload\n"
        "  icdev setup     # configure LLM, database, RAG and Docker",
        file=sys.stderr,
    )
    return 2


def read_versions() -> dict:
    """Current version as declared by each file (None when the file/pattern is absent)."""
    out: dict[str, str | None] = {}
    for rel, pattern, _fmt in VERSION_FILES:
        p = REPO_ROOT / rel
        if not p.is_file():
            out[rel] = None
            continue
        m = re.search(pattern, p.read_text(encoding="utf-8"), re.M)
        out[rel] = m.group("v") if m else None
    return out


def current_version() -> str:
    v = read_versions().get(SOURCE_OF_TRUTH)
    if not v:
        raise SystemExit(f"cannot read version from {SOURCE_OF_TRUTH}")
    return v


def next_version(current: str, part: str) -> str:
    m = _SEMVER_RE.match(current)
    if not m:
        raise SystemExit(f"{current!r} is not MAJOR.MINOR.PATCH — pass --version explicitly")
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version(version: str, *, dry_run: bool = False) -> list:
    """Set ``version`` in every declaring file. Returns per-file results."""
    results = []
    for rel, pattern, fmt in VERSION_FILES:
        p = REPO_ROOT / rel
        if not p.is_file():
            results.append({"file": rel, "ok": False, "reason": "missing"})
            continue
        text = p.read_text(encoding="utf-8")
        new, n = re.subn(pattern, fmt.format(v=version), text, count=1, flags=re.M)
        if n != 1:
            results.append({"file": rel, "ok": False, "reason": "pattern not found"})
            continue
        if not dry_run:
            p.write_text(new, encoding="utf-8")
        results.append({"file": rel, "ok": True})
    return results


# --------------------------------------------------------------------------- #
# Release notes
# --------------------------------------------------------------------------- #


def notes_status(version: str) -> dict:
    """Whether README and CHANGELOG each carry a section for ``version``.

    A release with no notes is not a cosmetic problem: `/updates` renders
    CHANGELOG.md, so a missing entry means the dashboard keeps advertising an
    older release as the newest one.
    """
    readme = REPO_ROOT / "README.md"
    changelog = REPO_ROOT / "CHANGELOG.md"
    esc = re.escape(version)
    out = {
        "readme": bool(readme.is_file() and re.search(
            rf"^##\s+What's New in {esc}\b", readme.read_text(encoding="utf-8"), re.M)),
        "changelog": bool(changelog.is_file() and re.search(
            rf"^##\s+\[{esc}\]", changelog.read_text(encoding="utf-8"), re.M)),
    }
    out.update(updates_page_status(version))
    return out


def updates_page_status(version: str) -> dict:
    """Will /updates actually RENDER this release, and lead with it?

    The heading regex above proves a line exists. It does not prove the page
    shows anything useful — and /updates is the surface users check to answer
    "what changed?". It renders CHANGELOG.md through
    ``tools.dashboard.changelog.parse_changelog``.

    Three things can satisfy the regex and still be wrong on the page:

      * the entry does not PARSE as a release (malformed heading or date), so
        the page silently omits it;
      * it is not the NEWEST entry, so the page leads with an older version —
        exactly the state that had /updates advertising 1.2.37 while the
        package shipped 1.2.39;
      * it parses but is EMPTY, which is what an unedited ``--scaffold-notes``
        stub looks like. Notes that are still a TODO are worse than none: they
        read as though someone wrote them.

    Validated with the page's OWN parser rather than a second implementation,
    so this cannot drift from what /updates actually does.
    """
    result = {"updates_parses": False, "updates_is_newest": False,
              "updates_has_content": False}
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.dashboard.changelog import parse_changelog

        releases = parse_changelog(str(REPO_ROOT / "CHANGELOG.md")) or []
    except Exception:  # noqa: BLE001 - the gate reports; it does not crash
        return result

    if not releases:
        return result
    result["updates_is_newest"] = str(releases[0].get("version", "")) == version

    entry = next((r for r in releases if str(r.get("version", "")) == version), None)
    if entry is None:
        return result
    result["updates_parses"] = True

    sections = entry.get("sections") or {}
    items = [i for v in sections.values() for i in (v or [])]
    # A lone "TODO: ..." bullet is the scaffold, not release notes.
    result["updates_has_content"] = bool(
        [i for i in items if "TODO" not in str(i).upper()])
    return result


def scaffold_notes(version: str, *, dry_run: bool = False) -> list:
    """Insert empty, clearly-marked sections for ``version`` at the top of each file.

    Deliberately leaves TODO placeholders rather than generating prose. Release
    notes are a judgement call about what mattered and why; a script that
    invents them produces changelog entries nobody trusts.
    """
    today = date.today().isoformat()
    actions = []

    changelog = REPO_ROOT / "CHANGELOG.md"
    if changelog.is_file():
        t = changelog.read_text(encoding="utf-8")
        if not re.search(rf"^##\s+\[{re.escape(version)}\]", t, re.M):
            entry = (f"## [{version}] - {today}\n\n"
                     "### Fixed\n- TODO: what broke, and why it mattered.\n\n")
            m = re.search(r"^##\s+\[", t, re.M)
            idx = m.start() if m else len(t)
            if not dry_run:
                changelog.write_text(t[:idx] + entry + t[idx:], encoding="utf-8")
            actions.append({"file": "CHANGELOG.md", "action": "scaffolded"})
        else:
            actions.append({"file": "CHANGELOG.md", "action": "already present"})

    readme = REPO_ROOT / "README.md"
    if readme.is_file():
        t = readme.read_text(encoding="utf-8")
        if not re.search(rf"^##\s+What's New in {re.escape(version)}\b", t, re.M):
            sec = (f"## What's New in {version} — TODO: headline\n\n"
                   "- **TODO.** What changed, and why a reader should care.\n\n---\n\n")
            m = re.search(r"^##\s+What's New in ", t, re.M)
            idx = m.start() if m else len(t)
            if not dry_run:
                readme.write_text(t[:idx] + sec + t[idx:], encoding="utf-8")
            actions.append({"file": "README.md", "action": "scaffolded",
                            "note": "update the Table of Contents anchor too"})
        else:
            actions.append({"file": "README.md", "action": "already present"})

    return actions


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #


def twine_env() -> dict:
    """Build the upload environment from .env. Never logs the secret."""
    env = dict(os.environ)
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*(TWINE_USERNAME|TWINE_PASSWORD|PYPI_API_TOKEN)\s*=\s*(.*)$", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    if not env.get("TWINE_PASSWORD") and env.get("PYPI_API_TOKEN"):
        env["TWINE_USERNAME"] = "__token__"
        env["TWINE_PASSWORD"] = env["PYPI_API_TOKEN"]
    return env


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def _run(cmd: list, *, env: dict | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True,
                          text=True, timeout=timeout)


def step_preflight(version: str) -> dict:
    """Cheap checks that fail fast, before anything is written."""
    problems = []
    if not _SEMVER_RE.match(version):
        problems.append(f"version {version!r} is not MAJOR.MINOR.PATCH")

    # `version == cur` is NOT an error: it is what a resumed release looks like.
    # The bump lands before the build, so any later failure (build, verify,
    # upload) leaves the declarations already at the target. Treating that as
    # "nothing to release" would wedge the retry — you could never finish a
    # release that failed halfway. PyPI already rejects a duplicate upload, so
    # the real protection against re-releasing lives there, not here.
    cur = current_version()
    resuming = version == cur
    if not resuming and _SEMVER_RE.match(cur) and _SEMVER_RE.match(version):
        if tuple(int(x) for x in version.split(".")) < tuple(int(x) for x in cur.split(".")):
            problems.append(f"version {version} is LOWER than current {cur}")

    r = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=60)
    branch = (r.stdout or "").strip()
    if branch == "main":
        problems.append("on main — release from a branch (CLAUDE.md: worktree/branch-first)")

    return {"ok": not problems, "problems": problems, "branch": branch,
            "current": cur, "resuming": resuming}


def step_build(skip_smoke: bool) -> dict:
    """Delegate to build_release.py — sync, validate, build, inspect, smoke, air-gap."""
    cmd = [sys.executable, "tools/installer/build_release.py", "--json"]
    if skip_smoke:
        cmd.append("--skip-smoke")
    r = _run(cmd, timeout=5400)
    payload = {}
    if r.stdout:
        try:
            payload = json.loads(r.stdout[r.stdout.index("{"):])
        except (ValueError, json.JSONDecodeError):
            payload = {}
    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "report": payload,
        "tail": (r.stdout or r.stderr or "")[-1500:],
    }


def _wheel_path(version: str):
    hits = sorted(DIST_DIR.glob(f"icdev-{version}-*.whl")) if DIST_DIR.is_dir() else []
    return hits[0] if hits else None


_SELF_IMPORT_SKIP = ("__init__.py",)


def _self_importing_modules(names, read) -> list:
    """Modules in the wheel that import from THEMSELVES.

    `icdev/tools/llm/agent_loop.py` containing
    `from icdev.tools.llm.agent_loop import DONE` is a back-compat SHIM that has
    been copied over the real implementation. Python raises ImportError on a
    partially initialized module the first time anything imports it, so the
    capability is simply gone from the installed package.

    This happened twice. 1.2.41 shipped it outright. Then the guard added to
    `sync_package_tree` could not fire on the DOCUMENTED release path at all,
    because `build_release.py` runs `--clean`, which deletes the mirror before
    the guard has a target to protect — so every release built the intended way
    would have carried it.

    Presence checks cannot see this: the file is there, it is just hollow. This
    is a cheap, decisive, offline check for a defect that otherwise only shows up
    when a user imports the module.
    """
    import re as _re

    bad = []
    for name in names:
        if not name.startswith("icdev/") or not name.endswith(".py"):
            continue
        if name.rsplit("/", 1)[-1] in _SELF_IMPORT_SKIP:
            continue
        module = name[:-3].replace("/", ".")
        try:
            text = read(name).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - unreadable member is not this check's job
            continue
        if _re.search(rf"^\s*from\s+{_re.escape(module)}\s+import\b", text, _re.M):
            bad.append(name)
    return bad


def step_verify_payload(version: str) -> dict:
    """Assert the wheel carries everything a fresh clone would.

    THE FAILURE THIS EXISTS FOR

    `tools/agents/` — the 9-file agent adapter registry — never shipped in any
    release. `.gitignore` carried a bare ``agents/`` rule intended for agent
    OUTPUT directories, and it matched the source directory at any depth. The
    files were therefore untracked: present on the machine that wrote them,
    absent from every fresh clone, every CI checkout, and every wheel built from
    one. Nothing failed; the code was simply not there.

    Neither `twine check` nor the venv smoke test can see this. Both inspect what
    the wheel HAS; neither knows what it SHOULD have.

    The comparison is deliberately against ``git ls-files``, not the working
    directory. An untracked file on the release engineer's disk is exactly the
    thing that produces a wheel nobody else can reproduce — comparing against
    the working tree would have called the broken releases healthy.
    """
    import zipfile

    problems = []
    wheel = _wheel_path(version)
    if wheel is None:
        return {"ok": False, "problems": [f"no wheel for {version} in dist/"]}

    parent_only = _parent_only_dirs()
    tracked = subprocess.run(
        ["git", "ls-files", "tools/"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.split()

    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
        # Detected while the archive is open — reopening per member would mean
        # thousands of archive opens on a 3,400-module wheel.
        hollow = _self_importing_modules(names, z.read)

    missing = []
    for rel in tracked:
        if not rel.endswith(".py"):
            continue
        parts = rel.split("/")
        if len(parts) > 1 and parts[1] in parent_only:
            continue
        if f"icdev/{rel}" not in names:
            missing.append(rel)

    if missing:
        problems.append(
            f"{len(missing)} tracked source file(s) absent from the wheel — "
            f"first few: {missing[:5]}")

    # The FORGE layers a project cannot run without. `icdev init` copies these
    # out of the wheel; if they are not inside it, init silently produces a
    # project with no goals, no args and no orchestration layer.
    required_prefixes = {
        "icdev/data/args/": "FORGE args layer",
        "icdev/data/goals/": "FORGE goals layer",
        "icdev/data/hardprompts/": "FORGE hardprompts layer",
        "icdev/data/context/": "FORGE context layer",
    }
    for prefix, label in required_prefixes.items():
        if not any(n.startswith(prefix) for n in names):
            problems.append(f"wheel carries no {label} ({prefix})")

    # CLAUDE.md by EXACT path, not by prefix. A `claude_bootstrap/` prefix test
    # passes as long as anything at all lives under it — including the platform
    # instruction files — so a missing CLAUDE.md would slip straight through.
    if "icdev/data/claude_bootstrap/CLAUDE.md" not in names:
        problems.append("wheel carries no CLAUDE.md — `icdev init` has no master instructions")
    if not any(n.startswith("icdev/data/claude_bootstrap/claude/commands/") for n in names):
        problems.append("wheel carries no .claude/commands payload")

    # `icdev init` writes .env from this template; without it a fresh project
    # has no configuration at all.
    if not any(n.endswith(".env.template") for n in names):
        problems.append("wheel carries no .env.template — `icdev init` cannot write .env")

    # The component registry drives every canvas and menu entry. A wheel that
    # ships without it discovers zero components (the 1.2.38 pip-install bug).
    if "icdev/data/args/component_registry.yaml" not in names:
        problems.append("wheel carries no component_registry.yaml — no canvases or menu items")

    # The FORGE DATA layers, file by file — not just "the directory exists".
    #
    # A prefix check passes on a single file. 295 IQE seed queries were absent
    # from the wheel while `data/context/` looked present, because `.iqe` was
    # missing from the package-data patterns: the engine shipped, its queries
    # did not, and every canvas came up with an empty Ask-Any-Canvas widget.
    data_layers = {"goals": "data/goals", "args": "data/args",
                   "hardprompts": "data/hardprompts", "context": "data/context"}
    for top, pkg_rel in data_layers.items():
        want = [r for r in subprocess.run(
            ["git", "ls-files", f"{top}/"], cwd=REPO_ROOT,
            capture_output=True, text=True).stdout.split() if r]
        gone = [r for r in want
                if f"icdev/{pkg_rel}/{r.split('/', 1)[1]}" not in names
                and not _deliberately_unpackaged(r)] if want else []
        if gone:
            problems.append(
                f"{len(gone)}/{len(want)} tracked {top}/ file(s) absent from the wheel "
                f"— first few: {gone[:4]}")

    # Hollow modules: present in the wheel, but importing from themselves.
    if hollow:
        problems.append(
            f"{len(hollow)} module(s) in the wheel import from THEMSELVES — a "
            f"back-compat shim was copied over the real implementation, so "
            f"importing them raises ImportError: {hollow[:4]}")

    # `icdev setup` is the first thing a pip user runs after `icdev init`. It
    # ships as ordinary package code, so it can go missing exactly the way the
    # AI platform files and tools/agents did — present in the repo, absent from
    # the wheel, discovered by a user rather than by CI.
    for mod, label in (
        ("icdev/tools/cli/setup_wizard.py", "guided setup wizard"),
        ("icdev/tools/cli/setup.py", "component setup TUI"),
        ("icdev/tools/cli/provision_db.py", "database + vector-store provisioner"),
    ):
        if mod not in names:
            problems.append(f"wheel carries no {label} ({mod})")

    # ICDEV is LLM-agnostic. All ten non-Claude platform instruction files were
    # tracked in git and none of them shipped, so an installed project was
    # Claude-only. A release that quietly drops them makes the claim false at
    # the point it matters most — in the user's project.
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.dx.ai_platforms import AI_PLATFORM_FILES, bootstrap_name

        plat_missing = [
            rel for _p, rel in AI_PLATFORM_FILES
            if f"icdev/data/claude_bootstrap/{bootstrap_name(rel)}" not in names
        ]
        if plat_missing:
            problems.append(
                f"{len(plat_missing)} AI platform instruction file(s) missing from the "
                f"wheel — an installed project would be Claude-only: {plat_missing[:5]}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not verify AI platform coverage: {exc}")

    # Genesis reflexes are scheduled work; a partial set fails silently at
    # runtime because the daemon simply never finds the reflex.
    reflexes_tracked = {r for r in tracked
                        if r.startswith("tools/genesis/reflexes/") and r.endswith(".py")}
    reflexes_missing = [r for r in reflexes_tracked if f"icdev/{r}" not in names]
    if reflexes_missing:
        problems.append(
            f"{len(reflexes_missing)} genesis reflex(es) missing from the wheel: "
            f"{reflexes_missing[:5]}")

    return {
        "ok": not problems,
        "problems": problems,
        "wheel": wheel.name,
        "tracked_py_checked": sum(
            1 for r in tracked
            if r.endswith(".py") and not (len(r.split("/")) > 1 and r.split("/")[1] in parent_only)
        ),
        "genesis_reflexes": len(reflexes_tracked),
    }


#: Tracked files that are deliberately NOT packaged. Kept explicit so the gate
#: reports real omissions instead of crying wolf — a gate that always fails is a
#: gate people learn to skip.
_UNPACKAGED_SUFFIXES = (".bak", ".tmp", ".pem", ".key", ".crt")
_UNPACKAGED_NAMES = (".gitkeep", ".gitignore")


def _deliberately_unpackaged(rel: str) -> bool:
    """True for build scratch, VCS placeholders and credentials.

    `.pem`/`.key`/`.crt` are excluded on purpose: shipping certificates inside a
    public wheel would be worse than omitting them.
    """
    name = rel.rsplit("/", 1)[-1]
    return name in _UNPACKAGED_NAMES or rel.endswith(_UNPACKAGED_SUFFIXES)


def _parent_only_dirs() -> set:
    """Subsystems intentionally absent from the public wheel.

    Read from sync_package_tree.py so the two cannot disagree — a hardcoded copy
    here would start reporting deliberate exclusions as missing payload the
    first time that list changed.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.installer.sync_package_tree import PARENT_ONLY_DIRS

        return set(PARENT_ONLY_DIRS)
    except Exception:  # noqa: BLE001 - fall back to checking everything
        return set()


def step_verify_artifacts(version: str) -> dict:
    """Assert the built artifacts are the ones we think, and are importable.

    `build_release.py` already smoke-tests the wheel; this adds the two checks
    specific to a VERSIONED release: the filenames carry the version we bumped
    to, and the packaged brand.yaml agrees. A wheel built before the bump looks
    identical to one built after until you read its name.
    """
    problems = []
    if not DIST_DIR.is_dir():
        return {"ok": False, "problems": ["dist/ missing — build did not run"]}

    wheels = sorted(DIST_DIR.glob(f"icdev-{version}-*.whl"))
    sdists = sorted(DIST_DIR.glob(f"icdev-{version}.tar.gz"))
    if not wheels:
        problems.append(f"no wheel named icdev-{version}-*.whl in dist/")
    if not sdists:
        problems.append(f"no sdist named icdev-{version}.tar.gz in dist/")

    if wheels:
        import zipfile
        with zipfile.ZipFile(wheels[0]) as z:
            names = set(z.namelist())
            brand = "icdev/data/args/brand.yaml"
            if brand in names:
                txt = z.read(brand).decode("utf-8", "replace")
                m = re.search(r'^version:\s*"([^"]+)"', txt, re.M)
                if m and m.group(1) != version:
                    problems.append(
                        f"packaged brand.yaml says {m.group(1)}, expected {version} "
                        "— the sync ran before the bump")

    r = _run([sys.executable, "-m", "twine", "check", *[str(p) for p in wheels + sdists]],
             timeout=600)
    if r.returncode != 0:
        problems.append(f"twine check failed: {(r.stdout or r.stderr)[-300:]}")

    return {"ok": not problems, "problems": problems,
            "artifacts": [p.name for p in wheels + sdists]}


def step_publish(version: str) -> dict:
    """Upload to PyPI. Irreversible — a version number can never be reused."""
    files = sorted(DIST_DIR.glob(f"icdev-{version}-*.whl")) + \
        sorted(DIST_DIR.glob(f"icdev-{version}.tar.gz"))
    if not files:
        return {"ok": False, "error": f"no artifacts for {version}"}
    env = twine_env()
    if not env.get("TWINE_PASSWORD"):
        return {"ok": False,
                "error": "no credentials — set TWINE_PASSWORD or PYPI_API_TOKEN in .env"}
    r = _run([sys.executable, "-m", "twine", "upload", "--non-interactive",
              *[str(p) for p in files]], env=env, timeout=3600)
    return {
        "ok": r.returncode == 0,
        "files": [p.name for p in files],
        "url": f"https://pypi.org/project/icdev/{version}/" if r.returncode == 0 else "",
        "tail": (r.stdout or r.stderr or "")[-600:],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="End-to-end ICDEV release: bump -> notes -> build -> verify -> publish.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--version", help="Explicit target version, e.g. 1.2.43.")
    g.add_argument("--bump", choices=("major", "minor", "patch"),
                   help="Derive the next version from icdev/_version.py.")
    ap.add_argument("--publish", action="store_true",
                    help="Actually upload to PyPI. Irreversible. Off by default.")
    ap.add_argument("--bump-only", action="store_true",
                    help="Write the version declarations and stop.")
    ap.add_argument("--scaffold-notes", action="store_true",
                    help="Insert empty README/CHANGELOG sections and stop.")
    ap.add_argument("--skip-smoke", action="store_true",
                    help="LOCAL ONLY. Skip the venv smoke test. Refused with --publish.")
    ap.add_argument("--allow-missing-notes", action="store_true",
                    help="Build without release notes. Refused with --publish.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    # Refuse before touching anything. Every later step assumes a checkout.
    if not is_source_checkout():
        return _refuse_outside_checkout()

    # The two combinations that produced broken releases.
    if args.publish and args.skip_smoke:
        print("--publish with --skip-smoke is refused: the venv smoke test is the only "
              "step that catches a wheel which builds but cannot import.", file=sys.stderr)
        return 2
    if args.publish and args.allow_missing_notes:
        print("--publish with --allow-missing-notes is refused: /updates renders "
              "CHANGELOG.md, so a release with no entry is invisible there.", file=sys.stderr)
        return 2

    version = args.version or (next_version(current_version(), args.bump) if args.bump else None)
    if not version:
        print(f"current version: {current_version()}\n"
              "pass --version X.Y.Z or --bump {major,minor,patch}", file=sys.stderr)
        return 2

    report: dict = {"target_version": version, "published": False, "steps": {}}

    if args.scaffold_notes:
        report["steps"]["scaffold"] = scaffold_notes(version)
        report["ok"] = True
        _emit(report, args.json)
        return 0

    pre = step_preflight(version)
    report["steps"]["preflight"] = pre
    if not pre["ok"]:
        report["ok"] = False
        report["failed_at"] = "preflight"
        _emit(report, args.json)
        return 1

    # Notes are gated BEFORE the bump on purpose. The bump mutates three tracked
    # files; failing after that leaves the tree half-released, and the author has
    # to work out what to revert. Checking first means a missing-notes run is a
    # no-op you can simply re-run.
    notes = notes_status(version)
    report["steps"]["notes"] = notes
    # Headings present AND the page can actually render them. Checking only the
    # headings let /updates advertise a stale release while the package moved on.
    notes_ok = (notes["readme"] and notes["changelog"]
                and notes["updates_parses"] and notes["updates_is_newest"]
                and notes["updates_has_content"])
    if not notes_ok and not args.allow_missing_notes:
        report["ok"] = False
        report["failed_at"] = "notes"
        if not (notes["readme"] and notes["changelog"]):
            report["hint"] = (f"no release notes for {version}. Write them, or run "
                              f"--version {version} --scaffold-notes to stub them out.")
        elif not notes["updates_parses"]:
            report["hint"] = (f"the CHANGELOG entry for {version} does not parse — "
                              "/updates would silently omit this release. Check the "
                              "`## [X.Y.Z] - YYYY-MM-DD` heading format.")
        elif not notes["updates_is_newest"]:
            report["hint"] = (f"{version} is not the newest CHANGELOG entry, so "
                              "/updates would lead with an older release. Move it "
                              "to the top of the file.")
        else:
            report["hint"] = (f"the CHANGELOG entry for {version} is still the "
                              "scaffold (TODO placeholders). Notes that read as "
                              "written but say nothing are worse than none.")
        _emit(report, args.json)
        return 1

    report["steps"]["bump"] = write_version(version)
    if any(not r["ok"] for r in report["steps"]["bump"]):
        report["ok"] = False
        report["failed_at"] = "bump"
        _emit(report, args.json)
        return 1

    if args.bump_only:
        report["ok"] = True
        _emit(report, args.json)
        return 0

    build = step_build(args.skip_smoke)
    report["steps"]["build"] = build
    if not build["ok"]:
        report["ok"] = False
        report["failed_at"] = "build"
        _emit(report, args.json)
        return 1

    verify = step_verify_artifacts(version)
    report["steps"]["verify"] = verify
    if not verify["ok"]:
        report["ok"] = False
        report["failed_at"] = "verify"
        _emit(report, args.json)
        return 1

    # Completeness. `verify` proves the wheel is well-formed; this proves it is
    # WHOLE. tools/agents/ shipped as nothing at all for months because no step
    # compared the wheel against what the repo actually tracks.
    payload = step_verify_payload(version)
    report["steps"]["payload"] = payload
    if not payload["ok"]:
        report["ok"] = False
        report["failed_at"] = "payload"
        _emit(report, args.json)
        return 1

    if not args.publish:
        report["ok"] = True
        report["dry_run"] = True
        _emit(report, args.json)
        return 0

    pub = step_publish(version)
    report["steps"]["publish"] = pub
    report["published"] = pub["ok"]
    report["ok"] = pub["ok"]
    if not pub["ok"]:
        report["failed_at"] = "publish"
    _emit(report, args.json)
    return 0 if pub["ok"] else 1


def _emit(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    v = report["target_version"]
    print("=" * 68)
    if not report.get("ok"):
        print(f"  RELEASE {v} BLOCKED AT: {report.get('failed_at')}")
        print("=" * 68)
        step = report["steps"].get(report.get("failed_at"), {})
        for p in step.get("problems", []) or []:
            print(f"  - {p}")
        if report.get("hint"):
            print(f"  {report['hint']}")
        if report.get("failed_at") == "notes":
            n = report["steps"]["notes"]
            print(f"  README section: {'OK' if n['readme'] else 'MISSING'}")
            print(f"  CHANGELOG entry: {'OK' if n['changelog'] else 'MISSING'}")
        if step.get("tail"):
            print(step["tail"])
        return
    if report.get("published"):
        print(f"  PUBLISHED {v}")
        print("=" * 68)
        print(f"  {report['steps']['publish']['url']}")
    elif report.get("dry_run"):
        print(f"  RELEASE {v} VERIFIED — nothing uploaded")
        print("=" * 68)
        for a in report["steps"].get("verify", {}).get("artifacts", []):
            print(f"  {a}")
        print(f"\n  Re-run with --publish to upload:\n"
              f"    python tools/installer/release.py --version {v} --publish")
    else:
        print(f"  VERSION SET TO {v}")
        print("=" * 68)


if __name__ == "__main__":
    sys.exit(main())
