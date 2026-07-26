# CUI // SP-CTI
"""Tests for tools/browser/scope.py — the agent browser's scope controls.

Every test runs with audit disabled and a stub driver: no WebDriver, no DNS,
no database.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.browser import scope


def make_config(**overrides):
    """Deny-by-default config with audit off, plus per-test overrides."""
    base = {"audit_enabled": False}
    base.update(overrides)
    return scope.BrowserScopeConfig(**base)


class StubElement:
    def __init__(self):
        self.typed = []
        self.clicks = 0
        self.cleared = 0

    def send_keys(self, text):
        self.typed.append(text)

    def clear(self):
        self.cleared += 1

    def click(self):
        self.clicks += 1


class StubDriver:
    """Minimal WebDriver stand-in. ``current_url`` tracks navigation."""

    def __init__(self, current_url="about:blank", on_get=None):
        self.current_url = current_url
        self.visited = []
        self.scripts = []
        self._on_get = on_get
        self.page_load_timeout = None
        self.script_timeout = None

    def get(self, url):
        self.visited.append(url)
        self.current_url = self._on_get(url) if self._on_get else url

    def execute_script(self, script, *args):
        self.scripts.append(script)
        return "script-result"

    def set_page_load_timeout(self, seconds):
        self.page_load_timeout = seconds

    def set_script_timeout(self, seconds):
        self.script_timeout = seconds

    def get_screenshot_as_png(self):
        return b"png-bytes"

    @property
    def title(self):
        return "Stub Page"


# ── Defaults are deny-by-default ──────────────────────────────────────────────


def test_default_config_is_loopback_only():
    cfg = scope.BrowserScopeConfig()
    assert set(cfg.allowed_domains) == {"localhost", "127.0.0.1", "::1"}
    assert cfg.allow_non_local is False
    assert cfg.require_egress_guard is True
    assert cfg.audit_enabled is True


def test_missing_config_file_yields_safe_defaults(tmp_path):
    cfg = scope.load_scope_config(path=tmp_path / "does_not_exist.yaml")
    assert cfg.allow_non_local is False
    assert set(cfg.allowed_domains) == {"localhost", "127.0.0.1", "::1"}


def test_shipped_config_is_loopback_only():
    """The committed args/browser_scope.yaml must not widen the default."""
    cfg = scope.load_scope_config(path=ROOT / "args" / "browser_scope.yaml")
    assert cfg.allow_non_local is False
    assert cfg.require_egress_guard is True
    assert set(cfg.allowed_domains) <= {"localhost", "127.0.0.1", "::1"}
    assert cfg.sensitive_placeholders == {}


def test_unknown_override_key_rejected(tmp_path):
    with pytest.raises(ValueError):
        scope.load_scope_config(
            path=tmp_path / "none.yaml", overrides={"allow_everything": True}
        )


def test_to_dict_lists_placeholder_names_not_values():
    cfg = make_config(sensitive_placeholders={"pw": "SOME_ENV_VAR"})
    assert cfg.to_dict()["sensitive_placeholders"] == ["pw"]


# ── Domain allowlist ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5050/",
        "http://127.0.0.1:5050/kanban",
        "https://localhost/secure",
    ],
)
def test_loopback_allowed_by_default(url):
    decision = scope.check_navigation(url, make_config())
    assert decision.allowed, decision.reason
    assert decision.reason == "ok_local"


@pytest.mark.parametrize(
    "url,reason",
    [
        ("https://example.com/", "not_allowlisted"),
        ("https://evil.test/exfil", "not_allowlisted"),
        ("http://169.254.169.254/latest/meta-data/", "not_allowlisted"),
    ],
)
def test_non_allowlisted_hosts_denied(url, reason):
    decision = scope.check_navigation(url, make_config())
    assert decision.allowed is False
    assert decision.reason == reason


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "data:text/html,<script>1</script>",
        "file:///C:/Windows/win.ini",
        "about:config",
        "ftp://localhost/etc",
    ],
)
def test_dangerous_schemes_denied(url):
    decision = scope.check_navigation(url, make_config())
    assert decision.allowed is False
    assert decision.reason in ("scheme_not_allowed", "no_host")


def test_denylist_beats_allowlist():
    cfg = make_config(
        allowed_domains=("localhost", "example.com"),
        denied_domains=("example.com",),
        allow_non_local=True,
    )
    decision = scope.check_navigation("https://app.example.com/", cfg)
    assert decision.allowed is False
    assert decision.reason == "denylisted"


def test_allowlist_suffix_matches_subdomain_only():
    cfg = make_config(
        allowed_domains=("example.com",),
        allow_non_local=True,
        require_egress_guard=False,
    )
    assert scope.check_navigation("https://app.example.com/", cfg).allowed
    # "notexample.com" must NOT match the "example.com" suffix rule
    assert not scope.check_navigation("https://notexample.com/", cfg).allowed


def test_allowlisted_remote_still_denied_without_allow_non_local():
    """Listing a host is necessary but not sufficient — three switches."""
    cfg = make_config(allowed_domains=("localhost", "example.com"))
    decision = scope.check_navigation("https://example.com/", cfg)
    assert decision.allowed is False
    assert decision.reason == "non_local_disabled"


def test_remote_host_must_clear_egress_guard(monkeypatch):
    cfg = make_config(
        allowed_domains=("example.com",),
        allow_non_local=True,
        require_egress_guard=True,
    )
    calls = {}

    def fake_guard(url, guard_cfg, resolver=None):
        calls["url"] = url
        return (False, "denied_ip_range", ["10.0.0.5"])

    # importlib+setattr, not the dotted string form: tools.* and icdev.tools.*
    # are distinct module objects and the string form resolves to the wrong one.
    import importlib

    # oss-filter-03 moved the implementation to tools/http/egress_guard.py, which
    # is what scope.py now imports. Patching the old link_check path would leave
    # the real guard in place and this test would pass for the wrong reason.
    guard_mod = importlib.import_module("tools.http.egress_guard")
    monkeypatch.setattr(guard_mod, "egress_guard", fake_guard, raising=False)
    decision = scope.check_navigation("https://example.com/", cfg)
    assert decision.allowed is False
    assert decision.reason == "egress_guard:denied_ip_range"
    assert decision.ips == ("10.0.0.5",)
    assert calls["url"] == "https://example.com/"


def test_remote_host_allowed_when_egress_guard_clears(monkeypatch):
    cfg = make_config(
        allowed_domains=("example.com",),
        allow_non_local=True,
        require_egress_guard=True,
    )
    import importlib

    link_check = importlib.import_module("tools.doc_modernization.link_check")
    monkeypatch.setattr(
        link_check,
        "egress_guard",
        lambda url, guard_cfg, resolver=None: (True, "ok", ["93.184.216.34"]),
        raising=False,
    )
    decision = scope.check_navigation("https://example.com/", cfg)
    assert decision.allowed is True
    assert decision.reason == "ok_remote"


def test_egress_guard_unavailable_fails_closed(monkeypatch):
    cfg = make_config(
        allowed_domains=("example.com",), allow_non_local=True, require_egress_guard=True
    )
    import builtins

    real_import = builtins.__import__

    def blocked(name, *a, **kw):
        if name == "tools.http.egress_guard":
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    decision = scope.check_navigation("https://example.com/", cfg)
    assert decision.allowed is False
    assert decision.reason == "egress_guard_unavailable"


def test_empty_allowlist_denies_everything():
    cfg = make_config(allowed_domains=())
    assert not scope.check_navigation("http://localhost:5050/", cfg).allowed


# ── Sensitive data ────────────────────────────────────────────────────────────


class AllowBroker:
    def __init__(self):
        self.calls = []

    def request_credential(self, agent_id, function, **kw):
        self.calls.append((agent_id, function))
        return {"fallback": True, "agent_id": agent_id, "function": function}


class DenyBroker:
    def request_credential(self, agent_id, function, **kw):
        return {"error": "agent_untrusted", "trust_level": "low"}


def test_placeholder_resolved_at_the_driver():
    cfg = make_config(sensitive_placeholders={"pw": "TEST_BROWSER_PW"})
    resolver = scope.SensitiveDataResolver(
        cfg, broker=AllowBroker(), env={"TEST_BROWSER_PW": "hunter2"}
    )
    out = resolver.resolve("login <secret>pw</secret> now")
    assert out.secret == "login hunter2 now"
    assert out.redacted == "login <secret>pw</secret> now"
    assert out.placeholders == ("pw",)


def test_resolved_text_repr_never_leaks_the_value():
    cfg = make_config(sensitive_placeholders={"pw": "TEST_BROWSER_PW"})
    resolver = scope.SensitiveDataResolver(
        cfg, broker=AllowBroker(), env={"TEST_BROWSER_PW": "hunter2"}
    )
    out = resolver.resolve("<secret>pw</secret>")
    assert "hunter2" not in repr(out)
    assert "hunter2" not in str(out)
    assert "hunter2" not in f"{out}"


def test_undeclared_placeholder_refused():
    cfg = make_config(sensitive_placeholders={})
    resolver = scope.SensitiveDataResolver(cfg, broker=AllowBroker(), env={})
    with pytest.raises(scope.SecretResolutionError):
        resolver.resolve("<secret>pw</secret>")


def test_unset_env_var_refused():
    cfg = make_config(sensitive_placeholders={"pw": "TEST_BROWSER_PW"})
    resolver = scope.SensitiveDataResolver(cfg, broker=AllowBroker(), env={})
    with pytest.raises(scope.SecretResolutionError):
        resolver.resolve("<secret>pw</secret>")


def test_broker_denial_fails_closed():
    cfg = make_config(sensitive_placeholders={"pw": "TEST_BROWSER_PW"})
    resolver = scope.SensitiveDataResolver(
        cfg, broker=DenyBroker(), env={"TEST_BROWSER_PW": "hunter2"}
    )
    with pytest.raises(scope.SecretResolutionError):
        resolver.resolve("<secret>pw</secret>")


def test_broker_not_consulted_when_no_placeholders():
    broker = AllowBroker()
    resolver = scope.SensitiveDataResolver(make_config(), broker=broker, env={})
    out = resolver.resolve("plain text")
    assert out.secret == "plain text"
    assert broker.calls == []


def test_redact_scrubs_leaked_values():
    cfg = make_config(sensitive_placeholders={"pw": "TEST_BROWSER_PW"})
    resolver = scope.SensitiveDataResolver(
        cfg, broker=AllowBroker(), env={"TEST_BROWSER_PW": "hunter2"}
    )
    assert resolver.redact("page says hunter2 here") == (
        "page says <secret>pw</secret> here"
    )


# ── Budget ────────────────────────────────────────────────────────────────────


def test_action_cap_enforced():
    budget = scope.ActionBudget(max_actions=2)
    budget.consume("a")
    budget.consume("b")
    with pytest.raises(scope.ActionBudgetExceeded):
        budget.consume("c")
    assert budget.remaining_actions == 0


def test_failure_cap_enforced():
    budget = scope.ActionBudget(max_failures=2)
    budget.record_failure("click", "boom")
    with pytest.raises(scope.ActionBudgetExceeded):
        budget.record_failure("click", "boom")


def test_step_timeout_enforced():
    budget = scope.ActionBudget(step_timeout_seconds=0.5)
    budget.check_step_duration("click", 0.4)  # under budget, no raise
    with pytest.raises(scope.StepTimeout):
        budget.check_step_duration("click", 0.9)


def test_budget_from_config():
    cfg = make_config(max_actions_per_run=7, max_failures=2, step_timeout_seconds=3.0)
    budget = scope.ActionBudget.from_config(cfg)
    assert (budget.max_actions, budget.max_failures, budget.step_timeout_seconds) == (
        7,
        2,
        3.0,
    )


# ── GuardedDriver ─────────────────────────────────────────────────────────────


def guarded(driver=None, **cfg_overrides):
    driver = driver or StubDriver()
    return scope.GuardedDriver(driver, config=make_config(**cfg_overrides))


def test_guarded_navigate_allows_loopback():
    drv = StubDriver()
    session = guarded(drv)
    session.navigate("http://localhost:5050/")
    assert drv.visited == ["http://localhost:5050/"]
    assert session.budget.actions_used == 1


def test_guarded_navigate_denies_and_spends_no_budget():
    drv = StubDriver()
    session = guarded(drv)
    with pytest.raises(scope.NavigationDenied):
        session.navigate("https://example.com/")
    assert drv.visited == []
    assert session.budget.actions_used == 0


def test_driver_timeouts_are_applied():
    drv = StubDriver()
    scope.GuardedDriver(
        drv,
        config=make_config(page_load_timeout_seconds=11.0, script_timeout_seconds=5.0),
    )
    assert drv.page_load_timeout == 11.0
    assert drv.script_timeout == 5.0


def test_bypass_attributes_blocked():
    session = guarded()
    for attr in ("get", "execute_script", "set_page_load_timeout"):
        with pytest.raises(scope.ScopeViolation):
            getattr(session, attr)


def test_non_bypass_attributes_pass_through():
    session = guarded()
    assert session.title == "Stub Page"


def test_escape_hatch_is_explicit():
    drv = StubDriver()
    session = guarded(drv)
    session.driver.get("https://anywhere.test/")  # documented, unguarded
    assert drv.visited == ["https://anywhere.test/"]


def test_redirect_out_of_scope_is_caught_and_contained():
    """A page that redirects off-allowlist must not survive the action."""
    drv = StubDriver(on_get=lambda url: "https://evil.test/landing")
    session = guarded(drv)
    with pytest.raises(scope.NavigationDenied) as exc:
        session.navigate("http://localhost:5050/redirector")
    assert "post_action" in exc.value.reason
    assert drv.visited[-1] == "about:blank"  # session parked, not left live


def test_action_cap_stops_the_run():
    drv = StubDriver()
    session = guarded(drv, max_actions_per_run=2)
    session.navigate("http://localhost:5050/a")
    session.navigate("http://localhost:5050/b")
    with pytest.raises(scope.ActionBudgetExceeded):
        session.navigate("http://localhost:5050/c")
    assert len(drv.visited) == 2


def test_failed_action_charges_failure_and_reraises():
    session = guarded(max_failures=2)

    def boom():
        raise RuntimeError("element not interactable")

    with pytest.raises(RuntimeError):
        session.run_action("click", boom)
    assert session.budget.failures == 1
    # second failure hits the cap and converts to a budget violation
    with pytest.raises(scope.ActionBudgetExceeded):
        session.run_action("click", boom)


def test_slow_action_trips_the_step_timeout():
    import time as _time

    session = guarded(step_timeout_seconds=0.01, max_failures=5)
    with pytest.raises(scope.StepTimeout):
        session.run_action("slow", lambda: _time.sleep(0.05))
    assert session.budget.failures == 1


def test_type_text_substitutes_secret_at_the_driver():
    cfg = make_config(sensitive_placeholders={"pw": "TEST_BROWSER_PW"})
    drv = StubDriver()
    session = scope.GuardedDriver(
        drv,
        config=cfg,
        secrets=scope.SensitiveDataResolver(
            cfg, broker=AllowBroker(), env={"TEST_BROWSER_PW": "hunter2"}
        ),
    )
    element = StubElement()
    used = session.type_text(element, "<secret>pw</secret>", clear=True)
    assert element.typed == ["hunter2"]
    assert element.cleared == 1
    assert used == ("pw",)


def test_type_text_audit_payload_carries_no_secret(monkeypatch):
    cfg = make_config(sensitive_placeholders={"pw": "TEST_BROWSER_PW"})
    cfg.audit_enabled = True
    captured = []
    monkeypatch.setattr(
        scope, "audit_browser_action",
        lambda action, outcome, details=None, config=None, run_id=None: captured.append(
            (action, outcome, details)
        ),
    )
    session = scope.GuardedDriver(
        StubDriver(),
        config=cfg,
        secrets=scope.SensitiveDataResolver(
            cfg, broker=AllowBroker(), env={"TEST_BROWSER_PW": "hunter2"}
        ),
    )
    session.type_text(StubElement(), "user <secret>pw</secret>")
    assert captured, "no audit row emitted"
    blob = repr(captured)
    assert "hunter2" not in blob
    assert "<secret>pw</secret>" in blob


def test_every_action_is_audited(monkeypatch):
    captured = []
    monkeypatch.setattr(
        scope, "audit_browser_action",
        lambda action, outcome, details=None, config=None, run_id=None: captured.append(
            (action, outcome)
        ),
    )
    session = guarded(max_actions_per_run=1)
    session.navigate("http://localhost:5050/")
    with pytest.raises(scope.NavigationDenied):
        session.navigate("https://example.com/")
    with pytest.raises(scope.ActionBudgetExceeded):
        session.navigate("http://localhost:5050/again")

    assert ("navigate", "allowed") in captured
    assert ("navigate", "denied") in captured
    assert len([c for c in captured if c[1] == "denied"]) == 2


def test_run_script_is_budgeted_and_audited():
    drv = StubDriver()
    session = guarded(drv)
    assert session.run_script("return 1;") == "script-result"
    assert drv.scripts == ["return 1;"]
    assert session.budget.actions_used == 1


def test_status_reports_budget_and_policy():
    status = guarded().status()
    assert status["budget"]["max_actions"] == 50
    assert status["policy"]["allow_non_local"] is False


def test_browser_event_types_pass_the_audit_trail_check_constraint():
    """The reused event types must exist in BOTH the Python constant and the
    SQL CHECK — the failure mode is a row that silently never lands."""
    from tools.audit import audit_logger

    ddl = (ROOT / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    for event_type in ("agent_task_completed", "agent_task_failed"):
        assert event_type in audit_logger.VALID_EVENT_TYPES
        assert f"'{event_type}'" in ddl


def test_audit_row_lands_in_audit_trail(tmp_path, monkeypatch):
    """End-to-end through the real log_event writer, against a temp DB."""
    import functools
    import json
    import sqlite3

    from tools.audit import audit_logger

    db = tmp_path / "audit.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'agent_task_submitted', 'agent_task_completed', 'agent_task_failed')),
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            affected_files TEXT,
            classification TEXT DEFAULT 'CUI',
            ip_address TEXT,
            session_id TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        audit_logger, "log_event", functools.partial(audit_logger.log_event, db_path=db)
    )
    scope.audit_browser_action(
        "navigate",
        "allowed",
        {"target": "http://localhost:5050/", "duration_ms": 12},
        config=make_config(audit_enabled=True),
        run_id="vv-001",
    )

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT event_type, actor, action, details FROM audit_trail"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    event_type, actor, action, details = rows[0]
    assert event_type == "agent_task_completed"
    assert actor == "browser_agent"
    assert action == "browser.navigate"
    payload = json.loads(details)
    assert payload["outcome"] == "allowed"
    assert payload["run_id"] == "vv-001"


def test_audit_never_raises_on_backend_failure(monkeypatch):
    """A broken audit backend must not break the browser call."""
    import tools.audit.audit_logger as audit_logger

    def explode(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(audit_logger, "log_event", explode)
    scope.audit_browser_action(
        "navigate", "allowed", {"url": "http://localhost:5050/"},
        config=make_config(audit_enabled=True),
    )  # must not raise
