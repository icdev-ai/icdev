# CUI // SP-CTI
"""icx-cnf-08 — pages call routes by endpoint id, and the e2e run proves the writes.

The planted audit cases (a built path is not credited, a hand-written ``/api/`` path
fails ``check()``, a stale route table fails, the e2e gate fails on a write no page
sent) live with the rest of the audit's teeth in tests/test_feature_conformance_audit.py.
This file holds what the card changed around it:

  * the shipped record: no audited file hand-writes an ``/api/`` path, every write
    the snapshot credits to a page was seen sent by a page, and the committed e2e
    record claims nothing the snapshot does not credit;
  * base.html loads the route table before any page script (not deferred);
  * the e2e-only roles directory is honoured only under ``tests/`` and never
    overrides a shipped role;
  * the suggested-team card: chat returns a message's metadata, and chat.js hands
    an action card to the shared renderer whose buttons confirm or decline it.
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
fa = importlib.import_module("tools.conformance.feature_audit")


# ── the shipped record ───────────────────────────────────────────────────────


def test_no_audited_file_hand_writes_an_api_path():
    snapshot = fa.load_snapshot(REPO)
    ids = snapshot["route_ids"]
    assert ids["hand_written"] == []
    assert ids["unknown_ids"] == []
    assert ids["routes_gen"]["current"] is True
    # The derived set is the pages' own files, not an empty list that passes trivially.
    assert {"tools/dashboard/templates/coworker/index.html",
            "tools/dashboard/templates/cortex/pmo.html",
            "tools/dashboard/templates/includes/_chat_action_card.html"} <= set(ids["audited_files"])
    for rel in ids["audited_files"]:
        assert "/api/" not in (REPO / rel).read_text(encoding="utf-8"), rel


def test_every_credited_write_was_seen_sent_by_a_page():
    snapshot = fa.load_snapshot(REPO)
    writes = [r for f in snapshot["features"] for r in f["cells"]["gui"]["routes"]
              if r["write"] and r["by_id"]]
    assert len(writes) >= 35
    assert [r["endpoint"] for r in writes if not r["observed"]] == []
    assert snapshot["summary"]["writes_observed"] == snapshot["summary"]["writes_by_id"]


def test_the_committed_record_claims_only_audited_routes():
    record = json.loads((REPO / fa.OBSERVED_RELPATH).read_text(encoding="utf-8"))
    snapshot = fa.load_snapshot(REPO)
    audited = {r["endpoint"] for f in snapshot["features"] for r in f["cells"]["gui"]["routes"]}
    assert record["schema"] == "icdev-observed-calls/v1"
    assert {v["endpoint"] for v in record["routes"].values()} <= audited
    # The gate over the committed record itself holds (a run that saw exactly it).
    assert fa.e2e_gate(snapshot, [REPO / fa.OBSERVED_RELPATH])["ok"]


def test_every_page_of_an_audited_feature_names_its_routes_by_id():
    """Spot-check the migration: the old literal paths are gone, the ids are there."""
    pages = {
        "coworker/index.html": "icdevRoute('ace_api.api_launch')",
        "coworker/roles.html": "icdevFetch('ace_api.api_profiles_delete'",
        "coworker/_dispatch_panels.html": "icdevRoute('ace_api.api_auto_dispatch_grant')",
        "cortex/verbs.html": "icdevRoute('cortex.api_v1_resolve')",
        "cortex/index.html": "url_for('cortex.api_iqe_query')",
        "document_intelligence/explorer.html":
            "icdevRoute('dic.api_explorer_cortex_finding_adjudicate'",
    }
    for rel, needle in pages.items():
        text = (REPO / "tools/dashboard/templates" / rel).read_text(encoding="utf-8")
        assert needle in text, rel


# ── the route table in base.html ─────────────────────────────────────────────


def test_base_html_loads_the_route_table_before_page_scripts():
    text = (REPO / "tools/dashboard/templates/base.html").read_text(encoding="utf-8")
    tag = re.search(r"<script[^>]*js/routes\.gen\.js[^>]*>", text)
    assert tag, "base.html does not load routes.gen.js"
    assert "defer" not in tag.group(0) and "async" not in tag.group(0)
    assert text.index(tag.group(0)) < text.index("{% block content %}")


def test_the_route_table_is_mirrored():
    rel = "tools/dashboard/static/js/routes.gen.js"
    assert (REPO / rel).read_bytes() == (REPO / "icdev" / rel).read_bytes()


# ── the e2e-only roles directory ─────────────────────────────────────────────


def _loader(monkeypatch, value: str | None):
    rl = importlib.import_module("tools.ace.role_loader")
    if value is None:
        monkeypatch.delenv(rl._E2E_ROLES_ENV, raising=False)
    else:
        monkeypatch.setenv(rl._E2E_ROLES_ENV, value)
    return rl.RoleLoader(hot_reload=False)


def test_the_e2e_role_is_added_only_from_under_tests(monkeypatch, tmp_path):
    e2e = REPO / "tests/e2e/fixtures/ace_roles"
    shipped = {r.role_id for r in _loader(monkeypatch, None).list_roles()}
    assert "cnf08_e2e_auto_role" not in shipped
    with_e2e = {r.role_id for r in _loader(monkeypatch, str(e2e)).list_roles()}
    assert with_e2e == shipped | {"cnf08_e2e_auto_role"}
    # Outside tests/ the variable is refused: a deployment cannot add roles through it.
    (tmp_path / "x.yaml").write_text((e2e / "cnf08_e2e_auto_role.yaml").read_text())
    assert {r.role_id for r in _loader(monkeypatch, str(tmp_path)).list_roles()} == shipped


def test_the_e2e_role_never_overrides_a_shipped_role(monkeypatch, tmp_path):
    """A test role that reuses a shipped role's id is ignored: the shipped one wins."""
    import yaml

    rl = importlib.import_module("tools.ace.role_loader")
    first = _loader(monkeypatch, None).list_roles()[0]
    e2e = tmp_path / "tests" / "roles"
    e2e.mkdir(parents=True)
    impostor = yaml.safe_load((REPO / "tests/e2e/fixtures/ace_roles/cnf08_e2e_auto_role.yaml")
                              .read_text(encoding="utf-8"))
    impostor.update(role_id=first.role_id, description="cnf08 impostor")
    (e2e / "impostor.yaml").write_text(yaml.safe_dump(impostor), encoding="utf-8")
    # The guard reads <repo>/tests from get_data_path; point it at tmp_path for this test.
    monkeypatch.setattr(rl, "get_data_path", lambda name: tmp_path / name)
    loader = _loader(monkeypatch, str(e2e))
    assert loader.get_role(first.role_id).description == first.description


def test_no_shipped_role_opts_into_auto():
    import yaml

    for path in sorted((REPO / "args/ace/roles").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert not (data.get("auto_dispatch") or {}).get("eligible_topics"), path.name


# ── the suggested-team card ──────────────────────────────────────────────────


class _Conn:
    """sqlite with the storage layer's ``%s`` placeholders."""

    def __init__(self, conn: sqlite3.Connection):
        self._c = conn

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def close(self):
        pass


def test_chat_returns_a_message_metadata_so_the_card_can_render(monkeypatch):
    cm = importlib.import_module("tools.dashboard.chat_manager")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE chat_messages (context_id TEXT, turn_number INTEGER, role TEXT, "
               "content TEXT, content_type TEXT, metadata TEXT, is_compressed INTEGER, "
               "compression_tier TEXT, classification TEXT, created_at TEXT)")
    card = {"card": "sme_team_suggestion", "suggestion_id": "sug-cnf08"}
    db.execute("INSERT INTO chat_messages VALUES ('ctx-1', 1, 'system', 'fallback', "
               "'action_card', ?, 0, NULL, 'CUI', 'now')", (json.dumps(card),))
    db.commit()
    monkeypatch.setattr(cm, "get_connection", lambda **_: _Conn(db))
    messages = cm.ChatManager.get_messages(object.__new__(cm.ChatManager), "ctx-1")
    assert messages[0]["content_type"] == "action_card"
    assert json.loads(messages[0]["metadata"]) == card
    db.close()


def test_chat_js_hands_action_cards_to_the_shared_renderer():
    js = (REPO / "tools/dashboard/static/js/chat.js").read_text(encoding="utf-8")
    assert "ct === 'action_card' && window.__icdevChatCards" in js
    assert "window.__icdevChatCards.render(cardMeta, holder)" in js
    # The card is shown as soon as it is posted, not after the model replies.
    assert "|| change.type === 'action_card'" in js
    card = (REPO / "tools/dashboard/templates/includes/_chat_action_card.html").read_text(
        encoding="utf-8")
    assert "icdevRoute('ace_api.api_suggestion_confirm'" in card
    assert "icdevRoute('ace_api.api_suggestion_decline'" in card


@pytest.mark.parametrize("rel", [
    "tools/dashboard/static/js/chat.js",
    "tools/dashboard/chat_manager.py",
    "tools/ace/role_loader.py",
    "tools/conformance/feature_audit.py",
    "tools/dashboard/routes_gen.py",
])
def test_changed_modules_are_mirrored(rel):
    assert (REPO / rel).read_bytes() == (REPO / "icdev" / rel).read_bytes(), rel
