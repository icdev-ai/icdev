# CUI // SP-CTI
"""A spec's own subprocess reaches the database the run asked for (qa-fail-679a43311f34d5c9).

THE DEFECT THIS GUARDS
----------------------
``qa-fail-6a87916931be3793`` made the documented E2E isolation recipe actually
redirect the dashboard, and made ``globalSetup`` MEASURE that it had::

    ICDEV_PG_DATABASE=icdev_e2e npx playwright test
    ->  ✓ E2E database confirmed: server is on 'icdev_e2e'

``webServerDatabaseEnv()`` is merged into ``webServer.env`` and nowhere else, so
it redirects the dashboard PLAYWRIGHT STARTS. It does not reach a subprocess a
SPEC spawns, and four specs spawn one: the DIC workspace seed fixture (two
specs), the second dashboard ``dwo_restart_durability`` starts, and the gateway
``dwo_trigger_linkage`` starts. Each inherited the operator's ambient
``ICDEV_DATABASE_URL``, and every connection site in ``tools/db/storage.py``
reads that DSN BEFORE the discrete ``ICDEV_PG_DATABASE``. Re-derived in this
worktree 2026-09-09, same interpreter, nothing else changed::

    ICDEV_PG_DATABASE=icdev_e2e                         ->  'icdev'
    ICDEV_PG_DATABASE=icdev_e2e ICDEV_DATABASE_URL=''   ->  'icdev_e2e'

TWO CONSEQUENCES AND THE SECOND IS THE SERIOUS ONE. The specs fail, because the
fixture seeds one database and the rail reads another. AND the fixture rows land
in the CANONICAL BOARD while the run prints that tick -- which was a TRUE
statement about a process that was not the one doing the writing. Proven by
running the fixture under the documented recipe: 1 ``dic_documents`` + 1
``dic_versions`` + 3 ``dic_sections`` + 3 ``dic_suggestions`` rows appeared in
``icdev``.

WHY A PER-SITE FIX IS NOT THE FIX
---------------------------------
Three of the four sites were repaired one at a time, each carrying its own copy
of the reasoning, and the fourth was left. That is the shape CLAUDE.md's
autonomy-lrn-01 rule names: a fixture-based repair passes at every site it was
applied to and says nothing about the next one, and the next one is a spec nobody
has written yet. So the repair is structural in two parts, and this module pins
both:

* ONE FUNCTION builds the environment for every ICDEV subprocess in the suite
  (``tests/e2e/fixtures/subprocess_env.ts``), and the CENSUS below asserts over
  the SOURCE that every spawn of the interpreter goes through it. That assertion
  is impossible to write against a repeated pattern.
* THE ASSERTION MEASURES THE SUBPROCESS PATH, not only the server. An isolation
  check that covers one writer and reports ``confirmed`` is the shape of the very
  defect it exists to close, so ``globalSetup`` now spawns a probe with the same
  ``icdevSubprocessEnv()`` a spec uses and ``confirmed`` requires EVERY writer.

THE CENSUS HAS POSITIVE CONTROLS, because a scanner that stopped scanning also
reports clean. ``test_the_census_catches_a_bare_inherit`` and its siblings feed
the scanner the defect as it was actually written and require it to fire.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

E2E_DIR = REPO_ROOT / "tests" / "e2e"
HELPER = E2E_DIR / "fixtures" / "subprocess_env.ts"
PROBE = E2E_DIR / "fixtures" / "database_probe.py"
GLOBAL_SETUP = REPO_ROOT / "globalSetup.ts"
CONFIG = REPO_ROOT / "playwright.config.ts"

# The one function a spawn's environment must be built from.
BUILDER = "icdevSubprocessEnv"


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------

def strip_ts_comments_and_strings(src: str) -> str:
    """Blank out comments, string literals and template literals.

    Needed in BOTH directions. A comment explaining the defect mentions
    ``spawn(PYTHON`` -- and every one of the four fixed sites now carries such a
    comment, so a naive scan would report the explanations as findings. And a
    string literal could contain ``spawn(`` too. Characters are replaced with
    spaces rather than deleted so every reported offset still matches the file.
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                if src[i] != "\n":
                    out[i] = " "
                i += 1
            for _ in range(2):
                if i < n:
                    out[i] = " "
                    i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out[i] = " "
            i += 1
            while i < n:
                if src[i] == "\\":
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                    continue
                if src[i] == quote:
                    out[i] = " "
                    i += 1
                    break
                if src[i] != "\n":
                    out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def _call_text(src: str, open_paren: int) -> str:
    """The full text of a call, from its opening paren to the balanced close."""
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] in "([{":
            depth += 1
        elif src[i] in ")]}":
            depth -= 1
            if depth == 0:
                return src[open_paren : i + 1]
    return src[open_paren:]


def _first_argument(call_text: str) -> str:
    """The command expression: up to the first TOP-LEVEL comma."""
    depth = 0
    for i, ch in enumerate(call_text[1:], start=1):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            return call_text[1:i].strip()
    return call_text[1:-1].strip()


#: An expression that names a python interpreter. ``PYTHON`` is the shared
#: binding; a literal is matched too so a site that spells it out is in scope.
_INTERPRETER_RE = re.compile(r"^(PYTHON|[A-Za-z_][\w.]*\bPYTHON\b|.*python.*)$", re.I)

#: A ``.py`` target in the argv is a SECOND, independent signal -- it catches a
#: spawn whose command came through a name this scanner cannot recognise.
_PY_TARGET_RE = re.compile(r"\.py\b")


def spawn_sites(path: Path) -> list[dict]:
    """Every ``spawn``/``spawnSync`` call in one file, classified.

    ``in_scope`` is True when the call runs an ICDEV python process, by EITHER
    signal: the command expression names an interpreter, or the argv names a
    ``.py`` file. ``builds_env`` is whether the call's own text reaches
    :data:`BUILDER`.
    """
    try:
        label = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # A positive control feeds the scanner a file outside the repo.
        label = path.as_posix()
    raw = path.read_text(encoding="utf-8")
    code = strip_ts_comments_and_strings(raw)
    # `.py` and `'python'` live in the literals the stripper blanked, so the
    # SIGNALS are read from the raw call text and the call's EXTENT from the
    # stripped one -- otherwise a commented-out spawn would be a site.
    sites = []
    for m in re.finditer(r"\bspawn(?:Sync)?\s*\(", code):
        open_paren = code.index("(", m.start())
        stripped_call = _call_text(code, open_paren)
        raw_call = raw[open_paren : open_paren + len(stripped_call)]
        command = _first_argument(raw_call)
        in_scope = bool(_INTERPRETER_RE.match(command)) or bool(_PY_TARGET_RE.search(raw_call))
        sites.append(
            {
                "file": label,
                "line": raw[:open_paren].count("\n") + 1,
                "command": command,
                "in_scope": in_scope,
                # Read from the STRIPPED call: a comment promising to use the
                # builder is not using it.
                "builds_env": BUILDER in stripped_call,
            }
        )
    return sites


def all_spawn_sites() -> list[dict]:
    sites: list[dict] = []
    for path in sorted(E2E_DIR.rglob("*.ts")):
        sites.extend(spawn_sites(path))
    return sites


# ---------------------------------------------------------------------------
# The census: every in-scope spawn builds its env from the one function
# ---------------------------------------------------------------------------

def test_the_suite_still_spawns_python_subprocesses():
    """The census must be measuring something.

    A scanner that finds no sites reports clean, which is indistinguishable from
    a suite with no defect -- so the denominator is asserted before the rate.
    """
    in_scope = [s for s in all_spawn_sites() if s["in_scope"]]
    assert in_scope, (
        "no in-scope spawn site found -- either the suite stopped spawning python "
        "subprocesses or this scanner stopped recognising them"
    )


def test_every_spawned_python_subprocess_gets_the_requested_database():
    """The census. A new spawn site with a bare inherit fails HERE.

    This is the whole class fix: the four known sites are repaired, and the
    fifth -- in a spec nobody has written yet -- cannot reintroduce the defect
    without failing this test.
    """
    offenders = [s for s in all_spawn_sites() if s["in_scope"] and not s["builds_env"]]
    assert not offenders, (
        "an ICDEV python subprocess inherits the ambient ICDEV_DATABASE_URL and so "
        "lands on the CANONICAL board under the documented isolation recipe "
        "(qa-fail-679a43311f34d5c9). Build its env from "
        f"tests/e2e/fixtures/subprocess_env.ts::{BUILDER}:\n"
        + "\n".join(f"  {s['file']}:{s['line']}  spawn({s['command']})" for s in offenders)
    )


def test_the_four_known_sites_are_all_covered():
    """Each spec the incident named is present AND covered, by name.

    A count is not enough: the rate stays clean if a site is DELETED, and one of
    these four was left out of the per-site repair precisely because nothing
    enumerated them.
    """
    expected = {
        "tests/e2e/dic_workspace_decisions.spec.ts",
        "tests/e2e/dic_workspace_two_reviewers.spec.ts",
        "tests/e2e/dwo_restart_durability.spec.ts",
        "tests/e2e/dwo_trigger_linkage.spec.ts",
    }
    covered = {s["file"] for s in all_spawn_sites() if s["in_scope"] and s["builds_env"]}
    missing = expected - covered
    assert not missing, f"known spawn sites are no longer covered: {sorted(missing)}"


def test_a_non_python_spawn_is_not_a_finding():
    """``spawnSync('taskkill', ...)`` must stay out of scope.

    A census that refused routine work would be stood down within a week, which
    is the rule CLAUDE.md already records about the PreToolUse hook.
    """
    sites = all_spawn_sites()
    taskkill = [s for s in sites if "taskkill" in s["command"].lower()]
    assert taskkill, "expected the process-tree kills to still be present"
    assert not any(s["in_scope"] for s in taskkill), (
        "a non-python spawn was classified as needing a database redirect"
    )


# ---------------------------------------------------------------------------
# Positive controls: the scanner fires on the defect as it was actually written
# ---------------------------------------------------------------------------

DEFECT = """
import { spawnSync } from 'child_process';
const PYTHON = process.env.ICDEV_PYTHON || 'python';
function runFixture(args: string[]): string {
  const res = spawnSync(PYTHON, [FIXTURE, ...args], {
    cwd: ROOT,
    encoding: 'utf-8',
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });
  return res.stdout;
}
"""

FIXED = DEFECT.replace(
    "env: { ...process.env, PYTHONIOENCODING: 'utf-8' },",
    "env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),",
)


def test_the_census_catches_a_bare_inherit(tmp_path):
    """The incident's own code, fed back in, must be reported."""
    spec = tmp_path / "probe.spec.ts"
    spec.write_text(DEFECT, encoding="utf-8")
    sites = spawn_sites(spec)
    assert len(sites) == 1, sites
    assert sites[0]["in_scope"], "a spawn of PYTHON was not recognised as in scope"
    assert not sites[0]["builds_env"], "a bare process.env inherit was read as covered"


def test_the_census_accepts_the_repair(tmp_path):
    """And the control: the repaired form must NOT be reported.

    Without this, a scanner that flags everything would pass the test above.
    """
    spec = tmp_path / "probe.spec.ts"
    spec.write_text(FIXED, encoding="utf-8")
    sites = spawn_sites(spec)
    assert len(sites) == 1, sites
    assert sites[0]["in_scope"] and sites[0]["builds_env"]


def test_a_comment_describing_the_defect_is_not_a_finding(tmp_path):
    """Every repaired site now carries a comment naming the defect.

    The first version of a scanner like this greps, and so reports the previous
    fix's own explanation of itself -- the trap CLAUDE.md records for
    ``perfect_score_census``.
    """
    spec = tmp_path / "probe.spec.ts"
    spec.write_text(
        "// spawnSync(PYTHON, [F], { env: { ...process.env } }) used to be wrong\n"
        "/* spawn(PYTHON, [F], { env: { ...process.env } }) too */\n",
        encoding="utf-8",
    )
    assert spawn_sites(spec) == []


def test_a_py_target_is_in_scope_even_under_an_unfamiliar_command(tmp_path):
    """The second signal: a spawn this scanner cannot name is still caught."""
    spec = tmp_path / "probe.spec.ts"
    spec.write_text(
        "const res = spawnSync(interpreterWeDoNotRecognise, "
        "['tools/dashboard/app.py'], { env: { ...process.env } });\n",
        encoding="utf-8",
    )
    sites = spawn_sites(spec)
    assert len(sites) == 1 and sites[0]["in_scope"] and not sites[0]["builds_env"]


# ---------------------------------------------------------------------------
# One spelling of the precedence
# ---------------------------------------------------------------------------

def test_the_shared_helper_exists_and_delegates_to_the_one_resolver():
    assert HELPER.is_file(), f"missing shared subprocess env helper: {HELPER}"
    body = HELPER.read_text(encoding="utf-8")
    assert "webServerDatabaseEnv" in body, (
        "the helper must reuse the ONE database resolver, not respell the precedence"
    )
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("*")
    )
    for env_var in ("ICDEV_PG_DATABASE", "ICDEV_PG_DB"):
        assert f"'{env_var}'" not in code, (
            f"the helper re-derives the database precedence ({env_var}) -- a second "
            "spelling is how the server and a subprocess came to disagree"
        )


def test_the_database_redirect_is_applied_last_so_a_caller_cannot_outrank_it():
    """A caller's ``extra`` must not be able to set its own ``ICDEV_DATABASE_URL``.

    There is no legitimate reason for a spec to, and one that did would reinstate
    exactly this defect with the fix in place.
    """
    body = HELPER.read_text(encoding="utf-8")
    fn = body.split("export function icdevSubprocessEnv", 1)[1]
    spread = re.search(r"return \{(.+?)\};", fn, re.S)
    assert spread, "icdevSubprocessEnv does not end in a single merged object"
    order = [
        token
        for token in re.findall(r"\.\.\.(\w+)", spread.group(1))
    ]
    assert order and order[-1] == "webServerDatabaseEnv", (
        f"the database redirect must be merged LAST, got {order}"
    )


def test_only_the_helper_reads_the_interpreter_variable():
    """``ICDEV_PYTHON`` has ONE binding.

    A spec that re-derives it keeps its own interpreter reference, and the census
    above recognises a command expression -- so a local rebinding is the way a
    future site slips past both halves of this guard at once.
    """
    offenders = []
    for path in sorted(E2E_DIR.rglob("*.ts")):
        if path == HELPER:
            continue
        code = strip_ts_comments_and_strings(path.read_text(encoding="utf-8"))
        if "ICDEV_PYTHON" in code:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, (
        "the interpreter must come from tests/e2e/fixtures/subprocess_env.ts, "
        f"not be re-derived: {offenders}"
    )


# ---------------------------------------------------------------------------
# The probe: it measures the SUBPROCESS path, and never echoes the environment
# ---------------------------------------------------------------------------

def test_the_probe_exists_and_measures_rather_than_echoes():
    assert PROBE.is_file(), f"missing subprocess database probe: {PROBE}"
    source = PROBE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # Strip the module docstring, which legitimately names the variables.
    code = code.split('"""', 2)[-1]
    assert "active_database" in code, "the probe must MEASURE the database"
    for env_var in ("ICDEV_PG_DATABASE", "ICDEV_DATABASE_URL"):
        assert env_var not in code, (
            f"the probe must never read {env_var} back -- that is a request, not a "
            "measurement, and echoing it would agree with the operator and be wrong"
        )


def test_the_probe_writes_nothing():
    """It audits a database; it must not modify one.

    Importing a canvas ``init_db`` APPLIES that canvas's ADD COLUMN migrations,
    so a probe that did would be its own finding on the board it is auditing.
    """
    source = PROBE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    code = code.split('"""', 2)[-1]
    for forbidden in ("INSERT", "UPDATE", "DELETE", "init_db", "commit("):
        assert forbidden not in code, f"the read-only probe references {forbidden}"


def test_the_probe_reports_unmeasurable_without_a_nonzero_exit():
    """``measured: false`` is a real answer, and must be readable as one.

    Collapsing it into a non-zero exit makes an unmeasurable probe
    indistinguishable from a crashed one, and the two send a reader to different
    fixes.
    """
    source = PROBE.read_text(encoding="utf-8")
    body = source.split("def main()", 1)[1].split("if __name__", 1)[0]
    assert body.count("return 0") >= 3, (
        "the import failure, the connection failure and the success path must all "
        "produce a readable verdict"
    )
    assert '"measured": False' in body


def test_the_probe_is_runnable_and_answers_honestly(tmp_path):
    """Run it for real against a throwaway SQLite file.

    A structural assertion cannot tell whether the probe actually works; the
    broken recipe was structurally fine too.
    """
    import json
    import subprocess
    import sys

    db = tmp_path / "probe.db"
    env = {
        k: v
        for k, v in __import__("os").environ.items()
        if not k.startswith(("ICDEV_PG_", "ICDEV_DATABASE_URL"))
    }
    env.update(
        {
            "ICDEV_STORAGE_BACKEND": "sqlite",
            "ICDEV_DB_PATH": str(db),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, f"probe produced no verdict: {proc.stderr[-800:]}"
    line = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1]
    body = json.loads(line)
    assert set(body) == {"measured", "backend", "database", "error"}
    if body["measured"]:
        assert body["database"], "a measured verdict must name a database"
    else:
        assert body["database"] is None, "an unmeasured verdict must not guess a name"
        assert body["error"], "an unmeasured verdict must say why"


# ---------------------------------------------------------------------------
# The assertion: `confirmed` means EVERY writer
# ---------------------------------------------------------------------------

def test_global_setup_probes_the_subprocess_writer_with_the_shared_builder():
    """It must measure the path a SPEC takes, not one it built itself.

    A hand-built environment would describe a path no spec uses -- the same error
    as re-reading our own variable back, one layer out.
    """
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    assert "probeSubprocessDatabase" in body
    fn = body.split("export function probeSubprocessDatabase", 1)[1]
    fn = fn.split("\n}", 1)[0]
    assert BUILDER in fn, (
        "the subprocess probe must build its env from the function the specs use"
    )
    assert "database_probe.py" in fn


def test_the_assertion_measures_both_writers():
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    fn = body.split("export async function assertDatabaseIsolated", 1)[1]
    fn = fn.split("\nexport ", 1)[0]
    assert "probeServerDatabase" in fn, "the server writer must still be measured"
    assert "probeSubprocessDatabase" in fn, (
        "the subprocess writer must be measured -- an isolation check that covers "
        "one writer and reports 'confirmed' is the defect this card is about"
    )
    assert "combineIsolation" in fn, "the run verdict must be folded from the writers"


def test_confirmed_requires_every_writer_and_an_empty_list_is_unmeasured():
    """The fold. ``mismatch`` > ``unmeasured`` > ``confirmed``, and ``[]`` is not a pass.

    Returning ``confirmed`` for an empty writer list is the two-empty-sides defect
    ``claim_verifier`` exists for, reproduced in four lines.
    """
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    fn = body.split("export function combineIsolation", 1)[1].split("\n}", 1)[0]
    assert "writers.length" in fn, "an empty writer list must be handled explicitly"
    assert "'unmeasured'" in fn, "an empty writer list must not read as confirmed"
    severity = body.split("VERDICT_SEVERITY", 1)[1].split("};", 1)[0]
    ranks = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"(\w+):\s*(\d+)", severity)
    }
    assert ranks["mismatch"] > ranks["unmeasured"] > ranks["confirmed"], ranks


def test_a_skipped_subprocess_probe_is_never_silent():
    """Standing the second probe down must be SAID on the confirmed line.

    Otherwise the kill switch restores the original defect exactly: a tick that
    covers one writer and reads as covering the run.
    """
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    fn = body.split("export async function assertDatabaseIsolated", 1)[1]
    fn = fn.split("\nexport ", 1)[0]
    confirmed = fn.split("verdict === 'confirmed'", 1)[1]
    assert "ICDEV_E2E_DB_CHECK_SUBPROCESS" in confirmed, (
        "a run whose subprocess writer was not measured must say so"
    )


def test_the_subprocess_probe_has_its_own_auditable_kill_switch():
    """Narrower than the whole check, on purpose.

    An operator on a host where no interpreter can run must not have to disarm the
    server check too -- that one is the only thing that catches
    ``reuseExistingServer``.
    """
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    assert "ICDEV_E2E_DB_CHECK_SUBPROCESS" in body
    assert "ICDEV_E2E_DB_CHECK" in body


@pytest.mark.parametrize("path", [GLOBAL_SETUP, CONFIG, HELPER])
def test_the_incident_is_recorded_where_the_next_reader_will_be(path):
    """The card id, in the files somebody edits next.

    The previous card's id is in all three; this one's absence is how the same
    defect gets rediscovered at a fifth site.
    """
    body = path.read_text(encoding="utf-8")
    assert "qa-fail-679a43311f34d5c9" in body, (
        f"{path.name} does not record why a subprocess must not inherit the DSN"
    )
# CUI // SP-CTI
