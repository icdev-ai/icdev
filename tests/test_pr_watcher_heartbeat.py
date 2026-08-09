# CUI // SP-CTI
"""kax-obs-01: the pr_watcher daemon heartbeat must land in its own log file.

Root cause pinned here: ``get_logger(__name__)`` at module scope resolves to
``"__main__"`` whenever the module is *executed* rather than imported.  Every
ICDEV daemon is executed that way (``tools/genesis/launcher.py`` runs
``python tools/ci/pr_watcher.py --daemon``), so the ``iteration=`` heartbeat was
written to a shared ``.logs/__main__.ndjson`` bucket while the watcher's own
``.logs/tools.ci.pr_watcher.ndjson`` stayed frozen at two 2026-08-02 WARNINGs
(those two came from the one path that *imports* the module).

That mattered operationally rather than cosmetically: silence in the watcher's
log was read twice during the 2026-08-08 triage as "the daemon is wedged", when
it was in fact looping correctly.  Silence must mean stopped.

Two layers:
  * ``TestCanonicalComponent`` — the unit behaviour of the fix.
  * ``TestDaemonHeartbeatLandsInLog`` — the real script, run the way the
    launcher runs it, proving an ``iteration=`` line is appended.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import icdev.tools.logging.icdev_logger as logger_mod  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Unit: the __main__ sentinel resolves to a dotted module path
# ────────────────────────────────────────────────────────────────────────────


class _FakeMain:
    def __init__(self, file: str | None):
        if file is not None:
            self.__file__ = file


def _make_package(root: Path, *parts: str) -> Path:
    """Create ``root/parts.../`` with an ``__init__.py`` at every level."""
    directory = root
    for part in parts:
        directory = directory / part
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    return directory


class TestCanonicalComponent:
    def test_non_main_component_is_untouched(self):
        assert logger_mod._canonical_component("tools.ci.pr_watcher") == \
            "tools.ci.pr_watcher"
        assert logger_mod._canonical_component("kanban_scheduler") == \
            "kanban_scheduler"

    def test_main_resolves_to_dotted_package_path(self, monkeypatch, tmp_path):
        pkg = _make_package(tmp_path, "tools", "ci")
        script = pkg / "pr_watcher.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setitem(sys.modules, "__main__", _FakeMain(str(script)))
        assert logger_mod._canonical_component("__main__") == "tools.ci.pr_watcher"

    def test_main_outside_a_package_falls_back_to_the_stem(
        self, monkeypatch, tmp_path
    ):
        script = tmp_path / "standalone_script.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setitem(sys.modules, "__main__", _FakeMain(str(script)))
        assert logger_mod._canonical_component("__main__") == "standalone_script"

    def test_main_without_a_file_keeps_the_sentinel(self, monkeypatch):
        # `python -c ...` and the REPL have no __main__.__file__; there is
        # nothing to resolve and the old behaviour must be preserved.
        monkeypatch.setitem(sys.modules, "__main__", _FakeMain(None))
        assert logger_mod._canonical_component("__main__") == "__main__"

    def test_get_logger_writes_under_the_resolved_name(self, monkeypatch, tmp_path):
        log_dir = tmp_path / "logs"
        pkg = _make_package(tmp_path, "tools", "ci")
        script = pkg / "pr_watcher.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setitem(sys.modules, "__main__", _FakeMain(str(script)))

        logger_mod.invalidate_cache()
        monkeypatch.setattr(logger_mod, "_CONFIG_CACHE", {
            "global_level": "INFO",
            "log_dir": str(log_dir),
            "rotation": {"when": "midnight", "retention_days": 7,
                         "max_bytes": 1_048_576},
            "component_overrides": {},
        })
        try:
            log = logger_mod.get_logger("__main__")
            log.info("pr_watcher: iteration=1 checked=0 actions=0")
            for handler in log.handlers:
                handler.flush()

            resolved = log_dir / "tools.ci.pr_watcher.ndjson"
            assert resolved.exists(), "heartbeat did not land in the per-component file"
            record = json.loads(resolved.read_text(encoding="utf-8").splitlines()[0])
            assert record["component"] == "tools.ci.pr_watcher"
            assert "iteration=1" in record["message"]
            assert not (log_dir / "__main__.ndjson").exists()
        finally:
            for handler in list(logger_mod.get_logger("__main__").handlers):
                handler.close()
            logger_mod.invalidate_cache()


# ────────────────────────────────────────────────────────────────────────────
# Integration: the real daemon, launched the way launcher.py launches it
# ────────────────────────────────────────────────────────────────────────────


class TestDaemonHeartbeatLandsInLog:
    """Runs ``python tools/ci/pr_watcher.py --daemon`` — the exact form used by
    ``tools/genesis/launcher.py::_start_pr_watcher`` — in an isolated cwd.

    ``log_dir`` in args/logging_config.yaml is relative (``.logs``), so cwd
    alone is enough to redirect the write; nothing touches the real repo logs.
    """

    def test_iteration_line_is_appended(self, tmp_path):
        config = tmp_path / "pr_watcher_config.yaml"
        # Disable the two poll steps that shell out to `gh`; the heartbeat is
        # what is under test, not PR discovery.
        config.write_text(
            "link_prs_on_poll: false\nsibling_conflict_check: false\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["ICDEV_STORAGE_BACKEND"] = "sqlite"
        env["ICDEV_DB_PATH"] = str(tmp_path / "empty.db")
        # ICDEV_DATABASE_URL outranks ICDEV_DB_PATH — a leaked one would drag
        # the subprocess onto a real Postgres.
        env.pop("ICDEV_DATABASE_URL", None)
        env.pop("ICDEV_PG_DATABASE", None)

        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "ci" / "pr_watcher.py"),
             "--daemon", "--interval", "1", "--max-iterations", "1",
             "--dry-run", "--config", str(config)],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=280,
        )
        assert proc.returncode == 0, f"daemon exited {proc.returncode}: {proc.stderr}"

        log_file = tmp_path / ".logs" / "tools.ci.pr_watcher.ndjson"
        assert log_file.exists(), (
            "pr_watcher wrote no per-component log at all.\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )

        records = [
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        heartbeats = [
            r for r in records
            if r["level"] == "INFO" and "iteration=" in r["message"]
        ]
        assert heartbeats, (
            "no iteration= heartbeat in the watcher's own log; records were "
            f"{[r['message'] for r in records]}"
        )
        assert heartbeats[0]["component"] == "tools.ci.pr_watcher"

        # The regression itself: nothing may fall through to the shared bucket.
        assert not (tmp_path / ".logs" / "__main__.ndjson").exists(), (
            "heartbeat still routed to the shared __main__ bucket"
        )
