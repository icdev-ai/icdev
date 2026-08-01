#!/usr/bin/env python3
# CUI // SP-CTI
"""shx-test-05 — Zero-Trust fail-closed COMBINED verification sweep.

This is an INDEPENDENT posture verification pass (fresh eyes) over the
zero-trust safety work landed by shx-safe-01/02/03. It deliberately does NOT
duplicate the per-module unit tests (tests/test_zt_stub_gate.py,
tests/test_attack_path_twin_predicates.py). Instead it drives each subsystem
through its HIGHEST-LEVEL public entry point and asserts the whole posture is
fail-closed when the stub opt-in flag is absent, and dev-permissive when it is
set:

  * device trust        -> verify_device_posture()            (real CrowdStrike stub)
  * device compliance   -> scan_device()                      (drives verify_device_posture)
  * PDP access decision -> evaluate_access()                  (top-level, adapter-selected)
  * attack path twin    -> query_attack_paths()               (IQE query surface)

Plus:
  * flag-value robustness ("0"/"false"/""/garbage -> deny), driven through a
    real entry point rather than only stub_allowed();
  * a static single-reader scan asserting stub_gate.stub_allowed() is the ONLY
    reader of ICDEV_ZT_ALLOW_STUB in tools/security/ + tools/security_canvas/,
    so the fail-open/closed semantics cannot fork across modules.

NIST 800-53: AC-3, AC-4, IA-3, SA-9, ZTA Pillars 2 & 5
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

import tools.security.stub_gate as _gate
import tools.security.device_trust as _dt
import tools.security.pdp_client as _pdp
import tools.security_canvas.device_compliance_scanner as _dcs
import tools.security_canvas.attack_path_twin as _apt
from tools.db.storage import StorageConnection


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Every sweep case starts from a clean cache + a known-clean flag env.

    Caches in device_trust / pdp_client persist across calls and are keyed by
    fingerprint / request; toggling the ZT flag between cases would otherwise
    read back a stale decision. Individual tests re-set the flag as needed.
    """
    _dt._cache.clear()
    _pdp.clear_cache()
    monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
    yield
    _dt._cache.clear()
    _pdp.clear_cache()


def _configure_unreachable_vendor(monkeypatch):
    """Give the CrowdStrike adapter credentials so verify_device_posture()
    reaches the (unreachable-live-API) stub that reports status 'unknown',
    and require device trust so the check is actually enforced."""
    monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")
    monkeypatch.setenv("ICDEV_CROWDSTRIKE_BASE_URL", "https://falcon.unreachable.mil")
    monkeypatch.setenv("ICDEV_CROWDSTRIKE_CLIENT_ID", "cid")
    monkeypatch.setenv("ICDEV_CROWDSTRIKE_CLIENT_SECRET", "csec")


def _fresh_mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return StorageConnection(conn, "sqlite")


def _sample_graph() -> dict:
    """Minimal SDC graph that yields at least one entry->target replay path.

    internet -> web(server) -> db(database). 'web' becomes an entry point
    (reachable from the internet boundary); 'db' is a high-value target.
    """
    return {
        "nodes": [
            {"id": "boundary-internet", "type": "boundary-internet", "label": "Internet"},
            {"id": "asset-web", "type": "asset-server", "label": "Web Tier"},
            {"id": "asset-db", "type": "asset-database", "label": "Records DB"},
        ],
        "edges": [
            {"source": "boundary-internet", "target": "asset-web"},
            {"source": "asset-web", "target": "asset-db"},
        ],
        "boundaries": [],
    }


# ---------------------------------------------------------------------------
# 1. Combined FAIL-CLOSED posture with the flag UNSET
# ---------------------------------------------------------------------------


class TestFailClosedFlagUnset:
    def test_device_posture_unreachable_vendor_not_trusted(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        _configure_unreachable_vendor(monkeypatch)

        result = _dt.verify_device_posture("fp-sweep-deny")

        assert result.trusted is False
        assert result.status == "unknown"
        assert "fail closed" in result.reason.lower()

    def test_compliance_scan_unknown_posture_fails_closed(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        _configure_unreachable_vendor(monkeypatch)
        monkeypatch.setattr(_dcs, "get_connection", _fresh_mem_conn)

        # NOTE: verify_device_posture is NOT stubbed here — scan_device drives
        # the real device-trust entry point end to end (its own higher-level
        # exercise, distinct from the per-module unit test).
        result = _dcs.scan_device("host-sweep-unknown.example.mil")

        assert result["overall_pass"] is False
        assert result["compliance_score"] == 0.0
        assert result["health_score"] == 0.0
        assert any("UNKNOWN" in g for g in result["gaps"])

    def test_pdp_disa_icam_denies_via_top_level(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "disa_icam")

        decision = _pdp.evaluate_access({"user_id": "u1"}, "res-a", "read")

        assert decision.permit is False
        assert decision.adapter == "disa_icam"

    def test_pdp_zscaler_zpa_denies_via_top_level(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "zscaler_zpa")

        decision = _pdp.evaluate_access({"user_id": "u1"}, "res-b", "read")

        assert decision.permit is False
        assert decision.adapter == "zscaler_zpa"

    def test_attack_path_unknown_predicate_returns_empty(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        graph = _sample_graph()

        # Baseline: the query engine can return paths.
        baseline = _apt.query_attack_paths(graph, "foreach path in attack_paths select all")
        assert baseline["count"] >= 1, "sample graph should yield at least one path"
        assert baseline["errors"] == []

        # Unknown where-clause predicate -> fail-closed -> zero paths, not all.
        filtered = _apt.query_attack_paths(
            graph,
            "foreach path in attack_paths where bogus_unknown_predicate select all",
        )
        assert filtered["count"] == 0
        assert filtered["results"] == []


# ---------------------------------------------------------------------------
# 2. Combined DEV-RESTORED posture with ICDEV_ZT_ALLOW_STUB=1
# ---------------------------------------------------------------------------


class TestDevRestoredFlagSet:
    def test_device_posture_trusted_under_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        _configure_unreachable_vendor(monkeypatch)

        result = _dt.verify_device_posture("fp-sweep-permit")

        assert result.trusted is True
        assert result.status == "unknown"
        assert "ICDEV_ZT_ALLOW_STUB" in result.reason

    def test_compliance_scan_passes_under_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        _configure_unreachable_vendor(monkeypatch)
        monkeypatch.setattr(_dcs, "get_connection", _fresh_mem_conn)

        result = _dcs.scan_device("host-sweep-dev.example.mil")

        assert result["overall_pass"] is True
        assert result["health_score"] == 0.75
        assert not any("UNKNOWN" in g for g in result["gaps"])

    def test_pdp_stub_adapters_permit_under_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")

        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "disa_icam")
        _pdp.clear_cache()
        disa = _pdp.evaluate_access({"user_id": "u1"}, "res-c", "read")
        assert disa.permit is True

        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "zscaler_zpa")
        _pdp.clear_cache()
        zpa = _pdp.evaluate_access({"user_id": "u1"}, "res-d", "read")
        assert zpa.permit is True

    def test_attack_path_fail_closed_is_orthogonal_to_flag(self, monkeypatch):
        """The attack-path unknown-predicate guard is a SEPARATE safety
        mechanism (shx-safe-01) — it is NOT gated on ICDEV_ZT_ALLOW_STUB and
        must stay fail-closed even in dev/CI with the flag set."""
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        graph = _sample_graph()

        filtered = _apt.query_attack_paths(
            graph,
            "foreach path in attack_paths where still_bogus select all",
        )
        assert filtered["count"] == 0
        assert filtered["results"] == []


# ---------------------------------------------------------------------------
# 3. Flag-value robustness: only explicit truthy opts in; everything else denies
# ---------------------------------------------------------------------------


class TestFlagValueRobustness:
    _CLOSED_VALUES = ["0", "false", "FALSE", "no", "off", "", "  ", "garbage", "2", "enable"]

    @pytest.mark.parametrize("value", _CLOSED_VALUES)
    def test_stub_allowed_treats_non_truthy_as_closed(self, monkeypatch, value):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", value)
        assert _gate.stub_allowed() is False

    @pytest.mark.parametrize("value", ["0", "false", "", "garbage"])
    def test_pdp_denies_for_non_truthy_flag_end_to_end(self, monkeypatch, value):
        """Drive the closed values through a real entry point, not just the
        predicate helper, so a divergent truthiness check would be caught."""
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", value)
        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "disa_icam")
        _pdp.clear_cache()

        decision = _pdp.evaluate_access({"user_id": "u1"}, f"res-{value or 'blank'}", "read")
        assert decision.permit is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "  YES  "])
    def test_stub_allowed_truthy_opts_in(self, monkeypatch, value):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", value)
        assert _gate.stub_allowed() is True


# ---------------------------------------------------------------------------
# 4. Static single-reader scan — stub_gate is the ONLY reader of the flag
# ---------------------------------------------------------------------------

# Matches an environment-variable READ (not a comment / reason-string mention).
_ENV_READ_RE = re.compile(r"os\.environ|os\.getenv|environ\.get|environ\[")
_FLAG_TOKENS = ("ICDEV_ZT_ALLOW_STUB", "STUB_ENV_VAR")


def _scan_dirs() -> list[Path]:
    import tools.security as _sec
    import tools.security_canvas as _seccanvas

    return [
        Path(_sec.__file__).resolve().parent,
        Path(_seccanvas.__file__).resolve().parent,
    ]


class TestSingleReaderInvariant:
    def test_stub_gate_is_the_only_env_reader(self):
        stub_gate_path = Path(_gate.__file__).resolve()
        violations: list[str] = []

        for base in _scan_dirs():
            for py in base.rglob("*.py"):
                if py.resolve() == stub_gate_path:
                    continue
                for lineno, line in enumerate(
                    py.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if not _ENV_READ_RE.search(line):
                        continue
                    if any(tok in line for tok in _FLAG_TOKENS):
                        violations.append(f"{py}:{lineno}: {line.strip()}")

        assert not violations, (
            "ICDEV_ZT_ALLOW_STUB must be read ONLY via stub_gate.stub_allowed(); "
            "direct reads found (semantics would fork):\n" + "\n".join(violations)
        )

    def test_scan_is_not_vacuous(self):
        """Guard the invariant test itself: stub_gate MUST actually contain an
        env read of the flag, otherwise the single-reader scan proves nothing."""
        src = Path(_gate.__file__).resolve().read_text(encoding="utf-8")
        env_read_lines = [ln for ln in src.splitlines() if _ENV_READ_RE.search(ln)]
        assert env_read_lines, "stub_gate.py should perform the env read"
        assert any(
            "STUB_ENV_VAR" in ln or "ICDEV_ZT_ALLOW_STUB" in ln for ln in env_read_lines
        ), "stub_gate.py env read should reference the flag"
