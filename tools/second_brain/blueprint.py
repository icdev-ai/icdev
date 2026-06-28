# CUI // SP-CTI
"""Second Brain (/me) canvas blueprint — profile, objectives, relationships, challenges, briefing, integrations."""
from __future__ import annotations

import json
import os
import secrets

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

second_brain_bp = Blueprint(
    "second_brain",
    __name__,
    template_folder="../../tools/dashboard/templates",
    url_prefix="/me",
)

_OAUTH_SERVICES = {"gcal", "gmail", "slack"}
_PAT_SERVICES = {"github", "gitlab", "jira", "linear", "notion"}


def _user_id() -> str:
    """Current user identifier — falls back to 'default' if auth not configured."""
    return getattr(g, "user_id", None) or session.get("user_id") or "default"


def _tenant_id() -> str:
    return getattr(g, "tenant_id", None) or "default"


# ─────────────────────────────────────────────────────────────────────────────
# Page routes
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/")
def index():
    from tools.second_brain.briefing import get_todays_briefing
    from tools.second_brain.profile import get_profile, get_objectives, get_relationships, get_challenges
    uid, tid = _user_id(), _tenant_id()
    briefing = get_todays_briefing(uid, tid) or {}
    profile = get_profile(uid, tid) or {}
    objectives = get_objectives(uid, tid)
    customers = get_relationships(uid, tid)
    challenges = get_challenges(uid, tid)
    return render_template(
        "second_brain/index.html",
        briefing=briefing,
        profile=profile,
        objectives=objectives,
        customers=customers,
        challenges=challenges,
    )


@second_brain_bp.route("/profile")
def profile_page():
    from tools.second_brain.profile import get_profile
    from tools.second_brain.constants import ORG_INDUSTRIES, ORG_SIZES, SENIORITY_LABELS
    profile = get_profile(_user_id(), _tenant_id()) or {}
    return render_template(
        "second_brain/profile.html",
        profile=profile,
        seniority_labels=SENIORITY_LABELS,
        industries=ORG_INDUSTRIES,
        org_sizes=ORG_SIZES,
    )


@second_brain_bp.route("/objectives")
def objectives_page():
    from tools.second_brain.profile import get_objectives
    objectives = get_objectives(_user_id(), _tenant_id())
    return render_template("second_brain/objectives.html", objectives=objectives)


@second_brain_bp.route("/customers")
def customers_page():
    from tools.second_brain.profile import get_relationships, get_profile
    from tools.second_brain.constants import (
        RELATIONSHIP_LABELS, RELATIONSHIP_TYPES,
        CUSTOMER_TYPE_ICONS, CUSTOMER_TYPE_DESCRIPTIONS,
    )
    uid, tid = _user_id(), _tenant_id()
    relationships = get_relationships(uid, tid)
    profile = get_profile(uid, tid) or {}
    return render_template(
        "second_brain/customers.html",
        customers=relationships,
        relationship_labels=RELATIONSHIP_LABELS,
        relationship_types=RELATIONSHIP_TYPES,
        customer_type_icons=CUSTOMER_TYPE_ICONS,
        customer_type_descriptions=CUSTOMER_TYPE_DESCRIPTIONS,
        team_mission=profile.get("team_mission", ""),
    )


@second_brain_bp.route("/relationships")
def relationships_page():
    return redirect("/me/customers", code=301)


@second_brain_bp.route("/challenges")
def challenges_page():
    from tools.second_brain.profile import get_challenges
    from tools.second_brain.constants import CHALLENGE_KEYS, CHALLENGE_LABELS, CHALLENGE_DESCRIPTIONS
    challenges = get_challenges(_user_id(), _tenant_id())
    return render_template(
        "second_brain/challenges.html",
        challenges=challenges,
        challenge_keys=CHALLENGE_KEYS,
        challenge_labels=CHALLENGE_LABELS,
        challenge_descriptions=CHALLENGE_DESCRIPTIONS,
    )


@second_brain_bp.route("/briefing/today")
def briefing_page():
    from tools.second_brain.briefing import get_todays_briefing, mark_briefing_opened
    briefing = get_todays_briefing(_user_id(), _tenant_id()) or {}
    mark_briefing_opened(_user_id(), _tenant_id())
    return render_template("second_brain/briefing.html", briefing=briefing)


@second_brain_bp.route("/integrations")
def integrations_page():
    from tools.second_brain.integrations import list_integrations
    from tools.second_brain.constants import INTEGRATION_SERVICES
    integrations = {i["service"]: i for i in list_integrations(_user_id(), _tenant_id())}
    return render_template(
        "second_brain/integrations.html",
        integrations=integrations,
        all_services=INTEGRATION_SERVICES,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Profile API
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/second-brain/profile", methods=["POST"])
def api_save_profile():
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.second_brain.profile import save_full_profile
        result = save_full_profile(_user_id(), data, _tenant_id())
        return jsonify({"ok": True, "profile": result})
    except Exception as exc:
        logger.warning("[second_brain] save_profile error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@second_brain_bp.route("/api/second-brain/profile/summarize", methods=["POST"])
def api_summarize_profile():
    try:
        from tools.second_brain.profile import generate_profile_summary
        summary = generate_profile_summary(_user_id(), _tenant_id())
        return jsonify({"ok": True, "summary": summary})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@second_brain_bp.route("/api/second-brain/profile", methods=["GET"])
def api_get_profile():
    from tools.second_brain.profile import get_full_profile
    profile = get_full_profile(_user_id(), _tenant_id())
    return jsonify(profile or {})


# ─────────────────────────────────────────────────────────────────────────────
# Briefing API
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/second-brain/briefing/generate", methods=["POST"])
def api_generate_briefing():
    data = request.get_json(force=True, silent=True) or {}
    briefing_date = data.get("date")
    try:
        from tools.second_brain.briefing import deliver_briefing, generate_briefing
        content = generate_briefing(_user_id(), briefing_date, _tenant_id())
        delivery = deliver_briefing(_user_id(), briefing_date, _tenant_id())
        return jsonify({"ok": True, "briefing": content, "delivery": delivery})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@second_brain_bp.route("/api/second-brain/briefing/today", methods=["GET"])
def api_todays_briefing():
    from tools.second_brain.briefing import get_todays_briefing
    briefing = get_todays_briefing(_user_id(), _tenant_id())
    return jsonify(briefing or {})


# ─────────────────────────────────────────────────────────────────────────────
# Integration API — PAT/API key verification + OAuth
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/integrations/status", methods=["GET"])
def api_integrations_status():
    from tools.second_brain.integrations import list_integrations
    return jsonify(list_integrations(_user_id(), _tenant_id()))


@second_brain_bp.route("/api/integrations/verify/<service>", methods=["POST"])
def api_verify_integration(service: str):
    data = request.get_json(force=True, silent=True) or {}
    try:
        connector = _get_connector(service)
        if connector is None:
            return jsonify({"ok": False, "error": "unknown service"}), 400
        ok = connector.verify(data)
        if ok:
            # Auto-save on successful verify (for PAT services)
            if service in _PAT_SERVICES:
                from tools.second_brain.integrations import save_integration
                save_integration(
                    user_id=_user_id(),
                    service=service,
                    access_token=data.get("access_token") or data.get("pat") or data.get("api_key", ""),
                    metadata={k: v for k, v in data.items() if k not in ("access_token", "pat", "api_key")},
                    tenant_id=_tenant_id(),
                )
        return jsonify({"ok": ok})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@second_brain_bp.route("/api/integrations/oauth/start/<service>", methods=["GET"])
def api_oauth_start(service: str):
    if service not in _OAUTH_SERVICES:
        return jsonify({"error": "not an OAuth service"}), 400
    state = secrets.token_urlsafe(16)
    session[f"oauth_state_{service}"] = state
    redirect_uri = url_for("second_brain.api_oauth_callback", service=service, _external=True)
    connector = _get_connector(service)
    if connector is None:
        return jsonify({"error": "connector unavailable"}), 500
    auth_url = connector.get_oauth_authorize_url(state=state, redirect_uri=redirect_uri)
    return redirect(auth_url)


@second_brain_bp.route("/api/integrations/oauth/callback/<service>", methods=["GET"])
def api_oauth_callback(service: str):
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    expected_state = session.pop(f"oauth_state_{service}", None)
    if not code or state != expected_state:
        return render_template("errors/400.html", message="OAuth state mismatch"), 400
    try:
        redirect_uri = url_for("second_brain.api_oauth_callback", service=service, _external=True)
        connector = _get_connector(service)
        token_data = connector.exchange_code(code, redirect_uri)
        access_token = token_data.get("access_token") or ""
        refresh_token = token_data.get("refresh_token") or ""
        expiry = str(token_data.get("expires_in", "")) or ""
        meta = {k: v for k, v in token_data.items() if k not in ("access_token", "refresh_token", "expires_in")}
        from tools.second_brain.integrations import save_integration
        save_integration(
            user_id=_user_id(),
            service=service,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=expiry,
            metadata=meta,
            tenant_id=_tenant_id(),
        )
        return redirect(url_for("second_brain.integrations_page") + f"?connected={service}")
    except Exception as exc:
        logger.warning("[second_brain] OAuth callback error %s: %s", service, exc)
        return render_template("errors/500.html", message=f"OAuth failed: {exc}"), 500


@second_brain_bp.route("/api/integrations/sync/<service>", methods=["POST"])
def api_sync_integration(service: str):
    try:
        connector = _get_connector(service)
        if connector is None:
            return jsonify({"ok": False, "error": "unknown service"}), 400
        result = connector.sync_to_context(_user_id())
        from tools.second_brain.integrations import update_sync_time
        update_sync_time(_user_id(), service, _tenant_id())
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@second_brain_bp.route("/api/integrations/<service>", methods=["DELETE"])
def api_revoke_integration(service: str):
    from tools.second_brain.integrations import revoke_integration
    ok = revoke_integration(_user_id(), service, _tenant_id())
    return jsonify({"ok": ok})


@second_brain_bp.route("/api/integrations/calendar/contacts", methods=["GET"])
def api_calendar_contacts():
    try:
        from tools.second_brain.connectors.google import GoogleConnector
        items = GoogleConnector().get_todays_items(_user_id())
        attendees: set[str] = set()
        for ev in items:
            for a in ev.get("attendees", []):
                if a:
                    attendees.add(a)
        return jsonify(list(attendees)[:20])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# IQE endpoint
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    data = request.get_json(force=True, silent=True) or {}
    query = data.get("query", "")
    collection = data.get("collection", "second_brain.profile")
    try:
        from tools.iqe.adapters.second_brain import query as iqe_query
        results = iqe_query(query, collection, _user_id(), _tenant_id())
        return jsonify({"ok": True, "results": results})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "results": []}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


def _get_connector(service: str):
    try:
        if service in ("gcal", "gmail"):
            from tools.second_brain.connectors.google import GoogleConnector
            return GoogleConnector()
        if service == "slack":
            from tools.second_brain.connectors.slack import SlackConnector
            return SlackConnector()
        if service in ("github", "gitlab"):
            from tools.second_brain.connectors.github import GitHubConnector
            return GitHubConnector()
        if service in ("jira", "linear"):
            from tools.second_brain.connectors.jira import JiraConnector
            return JiraConnector()
    except Exception as exc:
        logger.debug("[second_brain] connector load failed for %s: %s", service, exc)
    return None
