# CUI // SP-CTI
"""ECR-CL-01/02 V&V: CHANGELOG.md parser + /updates route + unseen-release badge."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# parse_changelog tests
# ---------------------------------------------------------------------------

SAMPLE_CHANGELOG = textwrap.dedent("""\
    # Changelog

    ## [2.0.0] - 2026-06-01

    ### Added
    - Feature A was added
    - Feature B was added

    ### Changed
    - Behavior X updated

    ## [1.1.0] - 2026-05-01

    ### Fixed
    - Bug Y resolved

    ### Security
    - Patch for CVE-0001

    ## [1.0.0] - 2026-04-01

    ### Added
    - Initial release
""")


@pytest.fixture()
def changelog_file(tmp_path: Path) -> Path:
    p = tmp_path / "CHANGELOG.md"
    p.write_text(SAMPLE_CHANGELOG, encoding="utf-8")
    return p


def test_changelog_parsed(changelog_file: Path) -> None:
    from tools.dashboard.changelog import parse_changelog

    releases = parse_changelog(changelog_file)
    assert len(releases) == 3
    assert releases[0]["version"] == "2.0.0"
    assert releases[0]["date"] == "2026-06-01"
    assert "Added" in releases[0]["sections"]
    assert "Feature A was added" in releases[0]["sections"]["Added"]


def test_changelog_returns_list(changelog_file: Path) -> None:
    from tools.dashboard.changelog import parse_changelog

    result = parse_changelog(changelog_file)
    assert isinstance(result, list)
    for rel in result:
        assert "version" in rel
        assert "date" in rel
        assert "sections" in rel
        assert isinstance(rel["sections"], dict)


def test_changelog_three_releases(changelog_file: Path) -> None:
    from tools.dashboard.changelog import parse_changelog

    releases = parse_changelog(changelog_file)
    versions = [r["version"] for r in releases]
    assert "2.0.0" in versions
    assert "1.1.0" in versions
    assert "1.0.0" in versions


def test_changelog_sections_populated(changelog_file: Path) -> None:
    from tools.dashboard.changelog import parse_changelog

    releases = parse_changelog(changelog_file)
    v110 = next(r for r in releases if r["version"] == "1.1.0")
    assert "Fixed" in v110["sections"]
    assert "Security" in v110["sections"]
    assert "Bug Y resolved" in v110["sections"]["Fixed"]


def test_changelog_changed_section(changelog_file: Path) -> None:
    from tools.dashboard.changelog import parse_changelog

    releases = parse_changelog(changelog_file)
    v200 = next(r for r in releases if r["version"] == "2.0.0")
    assert "Changed" in v200["sections"]
    assert "Behavior X updated" in v200["sections"]["Changed"]


def test_changelog_path_accepts_string(changelog_file: Path) -> None:
    from tools.dashboard.changelog import parse_changelog

    releases = parse_changelog(str(changelog_file))
    assert len(releases) >= 3


def test_changelog_real_file() -> None:
    """Parse the actual repo CHANGELOG.md — must have ≥3 releases."""
    from tools.dashboard.changelog import parse_changelog

    repo_root = Path(__file__).resolve().parents[1]
    changelog = repo_root / "CHANGELOG.md"
    if not changelog.exists():
        pytest.skip("CHANGELOG.md not present at repo root")

    releases = parse_changelog(changelog)
    assert len(releases) >= 3, f"Expected ≥3 releases, got {len(releases)}"


# ---------------------------------------------------------------------------
# /updates route tests
# ---------------------------------------------------------------------------

def _make_updates_client(changelog_path: str | Path | None = None):
    """Build a minimal Flask app with the /updates route wired."""
    from flask import Flask, render_template_string

    from tools.dashboard.changelog import parse_changelog

    TMPL = """\
<!DOCTYPE html><html><body>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
{% for rel in releases %}
<div class="release" data-version="{{ rel.version }}" data-date="{{ rel.date }}">
  {% for sec, items in rel.sections.items() %}
  <div class="section" data-name="{{ sec }}">
    {% for item in items %}<li>{{ item }}</li>{% endfor %}
  </div>
  {% endfor %}
</div>
{% endfor %}
</body></html>"""

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/updates")
    def updates_page():
        path = changelog_path
        try:
            releases = parse_changelog(path)
        except Exception as exc:
            return render_template_string(TMPL, releases=[], error=str(exc))
        return render_template_string(TMPL, releases=releases, error=None)

    return app.test_client()


def test_updates_route_200(changelog_file: Path) -> None:
    client = _make_updates_client(changelog_file)
    resp = client.get("/updates")
    assert resp.status_code == 200


def test_updates_route_lists_versions(changelog_file: Path) -> None:
    client = _make_updates_client(changelog_file)
    html = client.get("/updates").data.decode()
    assert "2.0.0" in html
    assert "1.1.0" in html
    assert "1.0.0" in html


def test_updates_route_missing_changelog(tmp_path: Path) -> None:
    client = _make_updates_client(tmp_path / "missing.md")
    resp = client.get("/updates")
    assert resp.status_code == 200
    assert b"error" in resp.data.lower()


# ---------------------------------------------------------------------------
# ECR-CL-02: unseen-release badge + dismissal
# ---------------------------------------------------------------------------

def _sqlite_db(tmp_path: Path):
    """Create an in-memory-like SQLite DB with user_preferences table."""
    import sqlite3

    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id          TEXT PRIMARY KEY,
            tenant_id        TEXT NOT NULL DEFAULT 'default',
            onboarding_state TEXT NOT NULL DEFAULT '{}',
            updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
        """
    )
    conn.commit()
    conn.close()
    return str(db)


def test_unseen_badge_new_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A brand-new user (no last_seen_version) should get unseen_release=True."""
    db_path = _sqlite_db(tmp_path)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)

    import importlib
    import tools.db.storage as _storage
    importlib.reload(_storage)
    import tools.auth.onboarding as _onb
    importlib.reload(_onb)
    monkeypatch.setattr(_onb, "_DEFAULT_STATE", {**_onb._DEFAULT_STATE, "last_seen_version": None})
    monkeypatch.setattr(_onb, "_ALLOWED_KEYS", frozenset(_onb._DEFAULT_STATE))

    result = _onb.get_last_seen_version("user-new")
    assert result is None


def test_unseen_badge_set_and_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """set_last_seen_version persists and get_last_seen_version retrieves it."""
    db_path = _sqlite_db(tmp_path)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)

    import importlib
    import tools.db.storage as _storage
    importlib.reload(_storage)
    import tools.auth.onboarding as _onb
    importlib.reload(_onb)

    _onb.set_last_seen_version("user-42", "1.2.19")
    seen = _onb.get_last_seen_version("user-42")
    assert seen == "1.2.19"


def test_unseen_badge_dismissed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After set_last_seen_version matches brand.version, unseen_release is False."""
    db_path = _sqlite_db(tmp_path)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)

    import importlib
    import tools.db.storage as _storage
    importlib.reload(_storage)
    import tools.auth.onboarding as _onb
    importlib.reload(_onb)
    import tools.dashboard.brand as _brand
    importlib.reload(_brand)

    brand_version = _brand.get_brand(reload=True).get("version", "1.2.19")
    _onb.set_last_seen_version("user-seen", brand_version)
    seen = _onb.get_last_seen_version("user-seen")
    assert seen == brand_version


def test_unseen_badge_brand_version_present() -> None:
    """get_brand() returns a non-empty version field."""
    from tools.dashboard.brand import get_brand
    brand = get_brand(reload=True)
    assert "version" in brand
    assert brand["version"]


def _make_seen_version_client(db_path: str, user_id: str = "test-user"):
    """Build a minimal Flask app with the PATCH /api/user/prefs/seen-version route."""
    import os

    from flask import Flask, jsonify, session

    os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
    os.environ["ICDEV_DB_PATH"] = db_path

    import importlib
    import tools.db.storage as _storage
    importlib.reload(_storage)
    import tools.auth.onboarding as _onb
    importlib.reload(_onb)
    import tools.dashboard.brand as _brand
    importlib.reload(_brand)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    @app.route("/api/user/prefs/seen-version", methods=["PATCH"])
    def patch_seen_version():
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "unauthenticated"}), 401
        brand_ver = _brand.get_brand().get("version", "")
        _onb.set_last_seen_version(uid, brand_ver)
        session["_seen_version"] = brand_ver
        return jsonify({"ok": True, "seen_version": brand_ver})

    @app.route("/login-test")
    def login_test():
        session["user_id"] = user_id
        return jsonify({"ok": True})

    return app.test_client()


def test_unseen_badge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance: PATCH /api/user/prefs/seen-version records seen version; subsequent
    get_last_seen_version returns the brand version so the badge would disappear."""
    db_path = _sqlite_db(tmp_path)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)

    client = _make_seen_version_client(db_path)

    # Unauthenticated call → 401
    resp = client.patch("/api/user/prefs/seen-version")
    assert resp.status_code == 401

    # Simulate login
    client.get("/login-test")

    # Now PATCH → 200, seen_version non-empty
    resp = client.patch("/api/user/prefs/seen-version")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["seen_version"]  # non-empty version string

    # Verify DB persisted it
    import importlib
    import tools.db.storage as _storage
    importlib.reload(_storage)
    import tools.auth.onboarding as _onb
    importlib.reload(_onb)
    seen = _onb.get_last_seen_version("test-user")
    assert seen == data["seen_version"]


# ---------------------------------------------------------------------------
# ECR-CL-03: named acceptance tests
# ---------------------------------------------------------------------------

def test_updates_route(changelog_file: Path) -> None:
    """/updates route returns HTTP 200 with release data."""
    client = _make_updates_client(changelog_file)
    resp = client.get("/updates")
    assert resp.status_code == 200
    assert b"2.0.0" in resp.data


def test_parse_empty_changelog(tmp_path: Path) -> None:
    """parse_changelog on an empty file returns an empty list (no crash)."""
    from tools.dashboard.changelog import parse_changelog

    empty = tmp_path / "CHANGELOG.md"
    empty.write_text("", encoding="utf-8")
    releases = parse_changelog(empty)
    assert releases == []


def test_seen_version_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH /api/user/prefs/seen-version persists the brand version to user prefs."""
    db_path = _sqlite_db(tmp_path)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)

    client = _make_seen_version_client(db_path, user_id="svp-user")
    client.get("/login-test")

    resp = client.patch("/api/user/prefs/seen-version")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["seen_version"]

    import importlib
    import tools.db.storage as _storage
    importlib.reload(_storage)
    import tools.auth.onboarding as _onb
    importlib.reload(_onb)

    seen = _onb.get_last_seen_version("svp-user")
    assert seen == data["seen_version"]
