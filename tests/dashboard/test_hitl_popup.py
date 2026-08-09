# CUI // SP-CTI
"""The HITL popup: the one signal that means "the pipeline stopped".

Everything the pipeline CAN recover is retried automatically and reported in the
Autonomous Recovery panel. This popup exists for the residue — work where 2
rebases and 5 resume cycles are spent — because that class waits forever, and the
operator works from Home, not from /monitoring where the buttons live.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PARTIAL = REPO / "tools" / "dashboard" / "templates" / "_hitl_popup.html"
HOME = REPO / "tools" / "dashboard" / "templates" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PARTIAL.read_text(encoding="utf-8")


def test_the_popup_is_included_on_home():
    """A partial nobody includes is invisible — the whole point is Home."""
    assert '{% include "_hitl_popup.html" %}' in HOME.read_text(encoding="utf-8")


def test_it_polls_the_hitl_endpoint_not_the_generic_one(html):
    """/api/notifications counts EVERY firing alert. Mixing informational alerts
    into this popup is how the one urgent signal stops being read."""
    assert "/api/hitl/pending" in html
    assert "/api/notifications" not in html


def test_dismiss_is_per_alert_and_per_session(html):
    """A permanent dismissal turns the stop-the-line signal into something people
    mute once and never see again."""
    assert "sessionStorage" in html
    assert "localStorage" not in html, "localStorage would outlive the incident"
    assert "hitl-dismissed:" in html


def test_task_ids_are_rendered_as_text_not_markup(html):
    """Alert text is data. It reaches this template from a task id and a PR url,
    so it must not be interpolated as HTML."""
    assert "textContent" in html


def test_a_polling_failure_cannot_break_home(html):
    assert ".catch(" in html


def test_it_links_to_where_the_buttons_are(html):
    """Seeing it is half the job; the remediation buttons live on /monitoring."""
    assert "/monitoring" in html

def test_os_notification_fires_once_per_alert(html):
    """Re-notifying every 60s for the same stuck task is how a notification
    becomes noise and gets permission-revoked — taking the genuinely new ones
    with it."""
    assert "var notified = {}" in html
    assert "!notified[i.id]" in html


def test_permission_is_requested_on_a_real_alert_not_on_page_load(html):
    """A prompt that appears for no reason gets denied, and a denial is sticky."""
    assert "requestPermission" in html
    assert "ensurePermission(items)" in html


def test_the_os_notification_persists_until_acknowledged(html):
    """This is a stop-the-line signal, not an FYI."""
    assert "requireInteraction: true" in html


def test_dismissing_the_banner_does_not_silence_the_os_notification(html):
    """Dismissing the in-page popup silences the banner, not the incident."""
    assert "ensurePermission(items)" in html, "must be passed the UNFILTERED list"
    assert "ensurePermission(fresh)" not in html


def test_clicking_the_notification_lands_where_the_buttons_are(html):
    assert "n.onclick" in html and "/monitoring#firing-alerts" in html
