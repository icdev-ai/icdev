# CUI // SP-CTI
"""Unit tests for the swallowed-persistence detector and gate (swp-swallow-01).

The card that commissioned this work observed that `except Exception: pass`
around an INSERT is why a batch of defects survived: the write fails forever
and nothing reports it. These tests pin both directions — the gate must fail
when such a block is reintroduced, and must stay quiet for the best-effort
patterns that are legitimately fine.
"""
import sys
import textwrap
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write(tmp_path: Path, body: str, name: str = "mod.py") -> Path:
    """Drop a module under a fake tools/ tree and return the file."""
    pkg = tmp_path / "tools" / "sample"
    pkg.mkdir(parents=True, exist_ok=True)
    target = pkg / name
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    return target


SWALLOWED = """
    def record(conn, name):
        try:
            conn.execute("INSERT INTO audit_trail (name) VALUES (?)", (name,))
            conn.commit()
        except Exception:
            pass
    """


class TestFindSites:
    """The shared detector — the gate and the fixer both import this."""

    def test_flags_except_exception_pass_over_insert(self, tmp_path):
        from tools.refactor.swallowed_persistence import find_sites

        _write(tmp_path, SWALLOWED)
        sites = find_sites([tmp_path / "tools"], tmp_path)

        assert len(sites) == 1
        assert sites[0].table == "audit_trail"
        assert sites[0].func_name == "record"

    def test_flags_bare_except_pass(self, tmp_path):
        from tools.refactor.swallowed_persistence import find_sites

        _write(
            tmp_path,
            """
            def record(conn):
                try:
                    conn.execute("INSERT INTO events (a) VALUES (1)")
                except:  # noqa: E722
                    pass
            """,
        )
        assert len(find_sites([tmp_path / "tools"], tmp_path)) == 1

    def test_ignores_handler_that_logs(self, tmp_path):
        """The prescribed fix must not itself trip the gate."""
        from tools.refactor.swallowed_persistence import find_sites

        _write(
            tmp_path,
            """
            def record(conn, name):
                try:
                    conn.execute("INSERT INTO audit_trail (n) VALUES (?)", (name,))
                except Exception as exc:
                    logger.warning("best-effort INSERT failed: %s", exc)
            """,
        )
        assert find_sites([tmp_path / "tools"], tmp_path) == []

    def test_ignores_narrow_handler(self, tmp_path):
        from tools.refactor.swallowed_persistence import find_sites

        _write(
            tmp_path,
            """
            def record(conn):
                try:
                    conn.execute("INSERT INTO events (a) VALUES (1)")
                except sqlite3.IntegrityError:
                    pass
            """,
        )
        assert find_sites([tmp_path / "tools"], tmp_path) == []

    def test_ignores_swallow_without_a_write(self, tmp_path):
        """Silence only matters when something was supposed to persist."""
        from tools.refactor.swallowed_persistence import find_sites

        _write(
            tmp_path,
            """
            def peek(conn):
                try:
                    conn.execute("SELECT 1 FROM events")
                except Exception:
                    pass
            """,
        )
        assert find_sites([tmp_path / "tools"], tmp_path) == []

    def test_skips_migrations_and_tests(self, tmp_path):
        from tools.refactor.swallowed_persistence import find_sites

        for sub in ("migrations", "tests"):
            d = tmp_path / "tools" / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / "m.py").write_text(textwrap.dedent(SWALLOWED), encoding="utf-8")

        assert find_sites([tmp_path / "tools"], tmp_path) == []

    def test_reads_through_a_utf8_bom(self, tmp_path):
        """A BOM makes ast.parse raise — that is how sites escaped earlier gates."""
        from tools.refactor.swallowed_persistence import find_sites

        target = _write(tmp_path, SWALLOWED, name="bom.py")
        target.write_bytes(b"\xef\xbb\xbf" + target.read_bytes())

        assert len(find_sites([tmp_path / "tools"], tmp_path)) == 1


class TestCheckSwallowedPersistence:
    """The coherence gate that fails the build on reintroduction."""

    def test_fails_when_reintroduced(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as mod

        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        _write(tmp_path, SWALLOWED)

        result = mod.check_swallowed_persistence()

        assert result.status == "fail"
        assert "audit_trail" in " ".join(result.extra)
        assert "logger.warning" in result.message

    def test_passes_on_clean_tree(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as mod

        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        _write(
            tmp_path,
            """
            def record(conn, name):
                try:
                    conn.execute("INSERT INTO audit_trail (n) VALUES (?)", (name,))
                except Exception as exc:
                    logger.warning("best-effort INSERT failed: %s", exc)
            """,
        )

        assert mod.check_swallowed_persistence().status == "pass"

    def test_registered_in_check_registry(self):
        """An unregistered check never runs, so it never gates anything."""
        from tools.workflow.coherence_checker import CHECK_REGISTRY

        assert "swallowed_persistence" in CHECK_REGISTRY

    def test_runs_in_the_fast_tier(self):
        """The per-task gate is the fast tier — a full-tier-only check
        would not block the build that reintroduces the pattern."""
        from tools.workflow.coherence_checker import select_checks

        assert "swallowed_persistence" in select_checks("fast", [])

    def test_missing_detector_fails_rather_than_passes(self, monkeypatch):
        """A gate that silently passes when its detector is gone is the
        exact failure mode this card exists to eliminate."""
        import builtins

        from tools.workflow import coherence_checker as mod

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "tools.refactor.swallowed_persistence":
                raise ImportError("simulated missing detector")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        result = mod.check_swallowed_persistence()

        assert result.status == "fail"
        assert "cannot import" in result.message

    def test_real_tree_is_clean(self):
        """The whole point of the card: zero sites remain in tools/."""
        from tools.workflow.coherence_checker import check_swallowed_persistence

        assert check_swallowed_persistence().status == "pass"
