# CUI // SP-CTI
"""A running process records WHICH CODE it is running (autonomy-id-01).

THE DEFECT. Nothing recorded the code version a live process held. On
2026-08-20 three fixes (#1859, #1861, #1863) merged and the running scheduler
and daemon went on executing pre-merge code: the board was correct, CI was
green, and the one thing no surface could state was whether the code doing the
work was the code that had been merged. A human noticed by eye.

``pr_watcher.run_daemon``'s own docstring records the same failure from the
other side — "twice the board looked broken when the only fault was this
process serving hours-old code".

THE TWO THINGS THESE TESTS PIN, because both fail GREEN if wrong:

  1. The reading is FROZEN at boot. ``code_reload.pull_if_safe`` fast-forwards
     the working copy underneath a running daemon, so a process that re-read
     HEAD would report the tree it COULD be running — reporting "current" at
     exactly the moment it went stale.
  2. UNKNOWN never becomes CURRENT. A process with no reading, or one predating
     the migration, must not read as up to date.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.coordination import code_identity as ci  # noqa: E402


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _fresh():
    """Every test boots its own identity — the module freezes by design."""
    ci.reset_for_test()
    yield
    ci.reset_for_test()


def _runner(sha="abc123def456", dirty="", rc=0):
    def _run(args, _root):
        if args[0] == "rev-parse":
            return _Result(sha, rc)
        if args[0] == "status":
            return _Result(dirty, rc)
        return _Result("", 1)
    return _run


# --------------------------------------------------------------------------- #
# 1. The reading is frozen — the whole design
# --------------------------------------------------------------------------- #
def test_the_boot_reading_is_never_recomputed(tmp_path):
    """`pull_if_safe` moves HEAD under a live daemon. A process that re-read it
    would report the tree it COULD be running, and would say `current` at the
    exact moment it went stale — worse than recording nothing."""
    first = ci.boot_identity(root=tmp_path, runner=_runner(sha="1111111"))
    moved = ci.boot_identity(root=tmp_path, runner=_runner(sha="2222222"))

    assert first["code_version"] == "1111111"
    assert moved["code_version"] == "1111111", (
        "HEAD moved and the process reported the NEW sha — it is still running "
        "the code it imported at boot"
    )


def test_the_returned_record_cannot_be_corrupted_by_a_caller(tmp_path):
    got = ci.boot_identity(root=tmp_path, runner=_runner())
    got["code_version"] = "tampered"
    assert ci.boot_identity(root=tmp_path)["code_version"] != "tampered"


# --------------------------------------------------------------------------- #
# 2. Unknown is a real answer and never reads as current
# --------------------------------------------------------------------------- #
def test_no_git_reports_unavailable_not_a_version(tmp_path, monkeypatch):
    monkeypatch.delenv("ICDEV_BUILD_ID", raising=False)

    def _no_git(_args, _root):
        raise OSError("git not found")

    ident = ci.boot_identity(root=tmp_path, runner=_no_git)
    assert ident["code_version"] is None
    assert ident["code_version_source"] == "unavailable"


def test_a_failed_git_call_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("ICDEV_BUILD_ID", raising=False)
    ident = ci.boot_identity(root=tmp_path, runner=_runner(sha="", rc=128))
    assert ident["code_version"] is None


def test_an_airgapped_build_id_is_used_only_when_git_cannot_answer(tmp_path, monkeypatch):
    """Deliberately AFTER git, unlike sbom_revision which prefers the
    declaration: this describes the TREE ON DISK, where a direct observation
    beats a declaration that may be a stale leftover."""
    monkeypatch.setenv("ICDEV_BUILD_ID", "build-7")

    def _no_git(_args, _root):
        raise OSError("no git")

    assert ci.boot_identity(root=tmp_path, runner=_no_git)["code_version"] == "build-7"
    ci.reset_for_test()
    assert ci.boot_identity(root=tmp_path, runner=_runner(sha="realsha"))[
        "code_version"] == "realsha", "a declared build id overrode an observable tree"


# --------------------------------------------------------------------------- #
# 3. `dirty` is its own axis — a sha alone OVERSTATES what is known
# --------------------------------------------------------------------------- #
def test_a_modified_tree_is_recorded_as_dirty(tmp_path):
    ident = ci.boot_identity(root=tmp_path, check_dirty=True,
                             runner=_runner(dirty=" M tools/x.py"))
    assert ident["code_dirty"] == 1


def test_a_clean_tree_is_recorded_as_clean(tmp_path):
    ident = ci.boot_identity(root=tmp_path, check_dirty=True, runner=_runner(dirty=""))
    assert ident["code_dirty"] == 0


def test_declining_the_dirty_check_is_unknown_never_clean(tmp_path):
    """`check_dirty=False` costs nothing and answers nothing. Reporting 0 there
    would be a clean bill of health nobody measured."""
    ident = ci.boot_identity(root=tmp_path, check_dirty=False, runner=_runner())
    assert ident["code_dirty"] is None


def test_an_unreadable_status_is_unknown_not_clean(tmp_path):
    def _run(args, _root):
        if args[0] == "rev-parse":
            return _Result("goodsha", 0)
        raise subprocess.SubprocessError("status blew up")

    ident = ci.boot_identity(root=tmp_path, check_dirty=True, runner=_run)
    assert ident["code_version"] == "goodsha"
    assert ident["code_dirty"] is None


# --------------------------------------------------------------------------- #
# 4. The module name is a real identity or None — never garbage
# --------------------------------------------------------------------------- #
def test_a_non_file_entry_point_has_no_module_name(tmp_path):
    """`python -c` reports sys.argv[0] == '-c', which resolves against the cwd,
    lands inside the repo and yields the module name '-c' — garbage that looks
    like an answer."""
    assert ci._module_from_path("-c", tmp_path) is None
    assert ci._module_from_path(None, tmp_path) is None


def test_a_path_outside_the_repo_has_no_module_name(tmp_path):
    outside = tmp_path.parent / "elsewhere.py"
    outside.write_text("x = 1", encoding="utf-8")
    assert ci._module_from_path(str(outside), tmp_path) is None


def test_a_real_module_resolves_to_a_dotted_name(tmp_path):
    pkg = tmp_path / "tools" / "genesis"
    pkg.mkdir(parents=True)
    mod = pkg / "daemon.py"
    mod.write_text("x = 1", encoding="utf-8")
    assert ci._module_from_path(str(mod), tmp_path) == "tools.genesis.daemon"


# --------------------------------------------------------------------------- #
# 5. The fleet reader distinguishes its four answers
# --------------------------------------------------------------------------- #
def test_an_unreadable_registry_is_unmeasurable_not_an_empty_fleet(monkeypatch):
    """"Nothing is running" and "nobody could look" justify opposite actions."""
    import tools.coordination.session_registry as reg

    def _boom(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(reg, "list_active", _boom)
    report = ci.processes()
    assert report["state"] == "unmeasurable"
    assert report["recorded"] is None and report["unknown"] is None, (
        "an unmeasurable fleet reported counts, which read as measured zeros"
    )


def test_no_live_processes_is_distinct_from_unmeasurable(monkeypatch):
    import tools.coordination.session_registry as reg

    monkeypatch.setattr(reg, "list_active", lambda *_a, **_kw: [])
    report = ci.processes()
    assert report["state"] == "no_live_processes"
    assert report["recorded"] == 0


def test_a_process_with_no_recorded_version_is_unknown(monkeypatch):
    """A row predating the migration has no reading. Defaulting it to HEAD would
    assert everything already running is up to date."""
    import tools.coordination.session_registry as reg

    monkeypatch.setattr(reg, "list_active", lambda *_a, **_kw: [
        {"session_id": "a", "pid": 1, "code_version": "sha-1", "module": "m"},
        {"session_id": "b", "pid": 2, "code_version": None, "module": None},
    ])
    report = ci.processes()
    assert report["recorded"] == 1 and report["unknown"] == 1
    states = {p["session_id"]: p["state"] for p in report["processes"]}
    assert states == {"a": ci.RECORDED, "b": ci.UNKNOWN}


def test_the_reader_computes_no_staleness_verdict():
    """Comparing a recorded sha against the tip is autonomy-id-02's business,
    and must be done against the process's own import closure. A verdict here
    would key on the tip generally and mark everything stale hourly."""
    import ast
    import inspect
    import textwrap

    # The CODE, never the prose: this function's docstring says in words that it
    # computes no staleness verdict, so grepping the raw source would fail on the
    # very sentence that documents the invariant.
    tree = ast.parse(textwrap.dedent(inspect.getsource(ci.processes)))
    fn = tree.body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    code = "\n".join(ast.dump(node) for node in body).lower()

    for forbidden in ("stale", "origin/main", "behind"):
        assert forbidden not in code, (
            f"processes() reached for {forbidden!r} — that is id-02's question, "
            f"and it must be answered against the import closure, not the tip"
        )


# --------------------------------------------------------------------------- #
# 6. IS IT ACTUALLY WIRED? — the half that shipped missing
# --------------------------------------------------------------------------- #
# These exist because the first push of this card contained the library, the
# migration and every test above, and NONE of the three callers: the files were
# modified but never `git add`ed, so the commit carried a recorder nothing
# invoked. Every check passed — the suite and the red-first gate both run
# against the WORKING TREE, which had the wiring — so a capability nobody calls
# went green inside the card whose whole subject is capabilities nobody calls.
#
# A test that only exercises `code_identity` cannot see that. These assert the
# CALLERS.

def _as_session(sid: str) -> None:
    import os

    os.environ["ICDEV_SESSION_ID"] = sid
    os.environ["CLAUDE_SESSION_ID"] = sid
    os.environ["ICDEV_AGENT"] = "test"
    try:
        import tools.airgap.hook_compat as hc
        hc._session_id = None
    except Exception:  # noqa: BLE001
        pass


def _migrate_identity_columns() -> bool:
    """Apply migration 20260821024132 to whatever database this test is on.

    Necessary, and the reason is the point: an `agent_sessions` table created
    before this card has the OLD shape, and `CREATE TABLE IF NOT EXISTS` never
    alters it — so a test DB carried over from an earlier run reaches the
    identity-absent path, which is a REAL deployment state rather than a test
    artifact. Applying the migration is what makes this test describe a MIGRATED
    deployment; the pre-migration state is asserted separately below.
    """
    import importlib.util

    from tools.coordination import session_registry as reg

    path = (ROOT / "tools" / "db" / "migrations"
            / "20260821024132_agent_sessions_code_identity" / "up.py")
    if not path.is_file():
        return False
    conn = reg._conn()
    try:
        reg._ensure_table(conn)
        spec = importlib.util.spec_from_file_location("_id_up", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.up(conn)
        conn.commit()
        return True
    finally:
        conn.close()


def test_register_actually_persists_the_code_identity(tmp_path):
    """The wiring, end to end: `register` must WRITE what `boot_identity` reads.

    Without this the recorder is a library nobody calls — and that is not a
    hypothetical, it is what the first push of this card shipped.
    """
    from tools.coordination import session_registry as reg

    # Asserted, never skipped: the migration ships in the same commit as this
    # test, so its absence is a broken change and not a reason to stand the
    # check down. A gated test that skips is an UNMEASURED test.
    assert _migrate_identity_columns(), (
        "migration 20260821024132 is missing — the identity columns cannot exist"
    )

    ci.reset_for_test()
    ci.boot_identity(root=tmp_path, runner=_runner(sha="wired0123456"))
    _as_session("identity-wiring-probe")

    assert reg.register(intent="probe").get("ok"), "the session did not register at all"

    row = next((dict(r) for r in reg.list_active()
                if dict(r).get("session_id") == "identity-wiring-probe"), None)
    assert row is not None, "registered session is not visible"
    assert row.get("code_version") == "wired0123456", (
        "register() did not persist the code identity — the recorder is not wired"
    )
    assert row.get("code_version_source") == "git"


def test_register_still_works_when_the_identity_columns_are_absent(monkeypatch, tmp_path):
    """A deployment where migration 20260821024132 has not run yet.

    `register` swallows its exceptions, so naming absent columns in the INSERT
    would silently stop the session registering AT ALL — trading a missing code
    version for a missing PROCESS, which is strictly worse.
    """
    from tools.coordination import session_registry as reg

    ci.reset_for_test()
    ci.boot_identity(root=tmp_path, runner=_runner(sha="abc"))
    _as_session("pre-migration-probe")
    # Pretend the catalogue reports the OLD shape.
    monkeypatch.setattr(reg, "_live_columns", lambda _conn: {
        "session_id", "agent_type", "pid", "host", "cwd", "started_at",
        "last_heartbeat", "current_intent", "status"})

    assert reg.register(intent="probe").get("ok"), (
        "a pre-migration deployment could no longer register a session"
    )


def test_the_genesis_daemon_registers_its_identity_at_boot():
    """The daemon is the one supervised process that does NOT self-update —
    `kanban_scheduler` and `pr_watcher` both call
    `code_reload.restart_if_code_changed` and it does not — so it is precisely
    the process whose staleness is invisible without a record."""
    from tools.genesis import daemon

    called = {}
    import tools.coordination.session_registry as reg
    original = reg.register
    try:
        reg.register = lambda intent=None: called.setdefault("intent", intent) or {"ok": True}
        daemon._register_process_identity()
    finally:
        reg.register = original

    assert "intent" in called, "the genesis daemon boots without recording its code"


def test_pr_watcher_registers_in_its_poll_loop():
    """Structural, and deliberately so: asserting this behaviourally would mean
    starting a forever-poll. Narrow — it pins the ONE call site, so it cannot
    speak for any other (see rem-hyg-19)."""
    import inspect

    from tools.ci.pr_watcher import PRWatcher

    src = inspect.getsource(PRWatcher.run_daemon)
    assert "session_registry" in src, (
        "pr_watcher polls for days and self-re-execs through os.execv; without a "
        "record, a failed re-exec leaves it serving old code while looking healthy"
    )
