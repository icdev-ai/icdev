# CUI // SP-CTI
"""Second Brain (/me) canvas blueprint — profile, objectives, relationships, challenges, briefing, integrations."""
from __future__ import annotations

import hmac
import os
import secrets

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, session, url_for

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


def _authenticated_user_id() -> str | None:
    """Return the authenticated user id, or None if the request is unauthenticated.

    Never returns a shared 'default' identity — the Second Brain stores per-user
    personal data (profile, relationships, integration tokens); a shared fallback
    would collapse every unauthenticated caller into one identity (cnr-me-01).
    """
    return getattr(g, "user_id", None) or session.get("user_id")


@second_brain_bp.before_request
def _second_brain_auth_gate():
    """Fail-closed login guard for every /me route.

    Adopts the Security Canvas ``sc_login_required`` pattern: an authenticated
    ICDEV dashboard session (or an explicit test/API-key opt-in) is required.
    Unauthenticated API / JSON / mutating requests get a JSON 401; unauthenticated
    page loads redirect to /login. There is no shared-identity fallback.
    """
    if _authenticated_user_id():
        return None

    # ICDEV_AUTH_BYPASS: explicit test-only opt-in.
    if os.environ.get("ICDEV_AUTH_BYPASS"):
        session["user_id"] = "e2e-bypass"
        return None

    # ICDEV_DASHBOARD_API_KEY: presented-key semantics — authenticate ONLY if the
    # request actually presents the key (constant-time compared). Merely having the
    # env var set does NOT auto-authenticate.
    api_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
    if api_key:
        presented = request.headers.get("X-ICDEV-API-Key", "")
        if not presented:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                presented = auth_header[len("Bearer "):].strip()
        if presented and hmac.compare_digest(presented, api_key):
            session["user_id"] = "api-key"
            return None

    if (
        request.is_json
        or request.path.startswith("/me/api/")
        or request.method in ("DELETE", "POST", "PUT")
    ):
        return jsonify({"error": "Authentication required"}), 401
    return redirect("/login")


def _user_id() -> str:
    """Current authenticated user identifier.

    The before_request gate guarantees an authenticated identity for every /me
    route, so this never returns a shared 'default'. Aborts 401 defensively if
    called outside the gated request path with no session.
    """
    uid = _authenticated_user_id()
    if not uid:
        abort(401)
    return uid


def _tenant_id() -> str:
    return getattr(g, "tenant_id", None) or "default"


def _err(exc: Exception, **extra):
    """Return a generic 500 JSON payload; log the real detail server-side.

    cnr-me-04(d): never leak raw str(exc) (stack/internal detail) to clients.
    *extra* preserves per-endpoint response keys (e.g. canvases=[], results=[]).
    """
    logger.warning("[second_brain] error in %s: %s", getattr(request, "endpoint", "?"), exc)
    payload = {"ok": False, "error": "Internal server error"}
    payload.update(extra)
    return jsonify(payload), 500


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
    import json as _json
    from tools.second_brain.profile import get_profile
    from tools.second_brain.role_advisor import infer_persona
    from tools.second_brain.constants import ORG_INDUSTRIES, ORG_SIZES, SENIORITY_LABELS
    profile = get_profile(_user_id(), _tenant_id()) or {}
    try:
        profile["delivery_channels_list"] = _json.loads(profile.get("delivery_channels") or '["dashboard"]')
    except Exception:
        profile["delivery_channels_list"] = ["dashboard"]
    inferred = infer_persona(profile.get("title", ""))
    return render_template(
        "second_brain/profile.html",
        profile=profile,
        seniority_labels=SENIORITY_LABELS,
        industries=ORG_INDUSTRIES,
        org_sizes=ORG_SIZES,
        inferred_persona=inferred,
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
    try:
        from tools.second_brain.relationship_health import get_relationship_health_map
        health_data = {r["id"]: r["health"] for r in get_relationship_health_map(uid, tid)}
    except Exception:
        health_data = {}
    return render_template(
        "second_brain/customers.html",
        customers=relationships,
        relationship_labels=RELATIONSHIP_LABELS,
        relationship_types=RELATIONSHIP_TYPES,
        customer_type_icons=CUSTOMER_TYPE_ICONS,
        customer_type_descriptions=CUSTOMER_TYPE_DESCRIPTIONS,
        team_mission=profile.get("team_mission", ""),
        health_data=health_data,
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
        from tools.second_brain.profile import save_full_profile, upsert_profile
        # Handle flat data from profile page form (no nested "profile" key)
        if "profile" not in data:
            upsert_profile(_user_id(), _tenant_id(), **{
                k: v for k, v in data.items()
                if k in {"full_name","work_email","title","seniority_tier","org_name",
                         "org_industry","org_size","timezone","work_start","work_end",
                         "briefing_time","delivery_channels","comm_style","team_mission","focus_block"}
            })
            from tools.second_brain.profile import get_profile
            return jsonify({"ok": True, "profile": get_profile(_user_id(), _tenant_id())})
        result = save_full_profile(_user_id(), data, _tenant_id())
        return jsonify({"ok": True, "profile": result})
    except Exception as exc:
        logger.warning("[second_brain] save_profile error: %s", exc)
        return _err(exc)


@second_brain_bp.route("/api/second-brain/profile/infer-role")
def api_infer_role():
    title = request.args.get("title", "").strip()
    from tools.second_brain.role_advisor import infer_persona
    persona = infer_persona(title)
    return jsonify({"ok": True, "persona": {
        "persona": persona.get("persona"),
        "display_name": persona.get("display_name", ""),
        "canvases": persona.get("canvases", []),
        "digest_topics": persona.get("digest_topics", [])[:4],
    }})


@second_brain_bp.route("/api/second-brain/profile/summarize", methods=["POST"])
def api_summarize_profile():
    try:
        from tools.second_brain.profile import generate_profile_summary
        summary = generate_profile_summary(_user_id(), _tenant_id())
        return jsonify({"ok": True, "summary": summary})
    except Exception as exc:
        return _err(exc)


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
        return _err(exc)


@second_brain_bp.route("/api/second-brain/briefing/today", methods=["GET"])
def api_todays_briefing():
    from tools.second_brain.briefing import get_todays_briefing
    briefing = get_todays_briefing(_user_id(), _tenant_id())
    return jsonify(briefing or {})


@second_brain_bp.route("/api/second-brain/briefing/deliver", methods=["POST"])
def api_briefing_deliver():
    """Manually trigger delivery of today's briefing to all configured channels."""
    from datetime import date
    from tools.second_brain.briefing import deliver_briefing, get_todays_briefing
    today_brief = get_todays_briefing(_user_id(), _tenant_id())
    if not today_brief:
        return jsonify({"ok": False, "error": "No briefing for today. Generate one first."}), 404
    status = deliver_briefing(_user_id(), date.today().isoformat(), _tenant_id())
    return jsonify({"ok": True, "delivery_status": status})


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
        return _err(exc)


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
        return _err(exc)


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
        return _err(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Proactive advisor API — manual triggers for autonomous features
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/second-brain/proactive/tomorrow-prep", methods=["POST"])
def api_tomorrow_prep():
    """Manually trigger tomorrow-prep generation."""
    try:
        from tools.second_brain.proactive_advisor import generate_tomorrow_prep
        prep = generate_tomorrow_prep(_user_id(), _tenant_id())
        return jsonify({"ok": True, "prep": prep})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/proactive/weekly-digest", methods=["POST"])
def api_weekly_digest():
    """Manually trigger weekly architecture digest generation."""
    try:
        from tools.second_brain.proactive_advisor import generate_weekly_architecture_digest
        digest = generate_weekly_architecture_digest(_user_id(), _tenant_id())
        return jsonify({"ok": True, "digest": digest})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/proactive/stalled-work", methods=["GET"])
def api_stalled_work():
    """Return objectives with no forward motion in 5+ days."""
    try:
        from tools.second_brain.proactive_advisor import scan_stalled_objectives
        items = scan_stalled_objectives(_user_id(), _tenant_id())
        return jsonify({"ok": True, "stalled": items})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/proactive/design-review", methods=["POST"])
def api_design_review():
    """Customer-aware design review. POST body: design_context dict."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.second_brain.proactive_advisor import customer_aware_review
        findings = customer_aware_review(data, _user_id(), _tenant_id())
        return jsonify({"ok": True, "findings": findings})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/proactive/relevant-canvases", methods=["GET"])
def api_relevant_canvases():
    """Return canvas keys in the user's role affinity list."""
    try:
        from tools.second_brain.role_advisor import get_relevant_canvases
        canvases = get_relevant_canvases(_user_id(), _tenant_id())
        return jsonify({"ok": True, "canvases": canvases})
    except Exception as exc:
        return _err(exc, canvases=[])


@second_brain_bp.route("/api/second-brain/proactive/expectation-fields", methods=["GET"])
def api_expectation_fields():
    """Return role-appropriate expectation field definitions for the customer form."""
    try:
        from tools.second_brain.role_advisor import infer_persona, _get_user_title
        title = _get_user_title(_user_id(), _tenant_id())
        persona = infer_persona(title)
        return jsonify({
            "ok": True,
            "fields": persona.get("expectation_fields", []),
            "persona": persona.get("display_name", ""),
        })
    except Exception as exc:
        return _err(exc, fields=[])


@second_brain_bp.route("/api/second-brain/challenges/mitigate", methods=["POST"])
def api_challenge_mitigate():
    """Generate context-aware AI mitigations for the user's active challenges."""
    try:
        from tools.second_brain.proactive_advisor import generate_challenge_mitigations
        result = generate_challenge_mitigations(_user_id(), _tenant_id())
        return jsonify({"ok": True, "mitigations": result})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/objectives/sync", methods=["POST"])
def api_objectives_sync():
    """Manually trigger objective progress sync from kanban + git."""
    try:
        from tools.second_brain.objective_tracker import sync_objective_progress
        updated = sync_objective_progress(_user_id(), _tenant_id())
        return jsonify({"ok": True, "updated": updated, "count": len(updated)})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/proactive/commitment-alerts", methods=["GET"])
def api_commitment_alerts():
    """Return commitment date alerts across all customer relationships."""
    try:
        from tools.second_brain.proactive_advisor import generate_commitment_alerts
        alerts = generate_commitment_alerts(_user_id(), _tenant_id())
        return jsonify({"ok": True, "alerts": alerts, "count": len(alerts)})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/proactive/meeting-preps", methods=["POST"])
def api_meeting_preps():
    """Manually trigger meeting prep card generation for upcoming meetings."""
    try:
        from tools.second_brain.proactive_advisor import generate_meeting_preps
        cards = generate_meeting_preps(_user_id(), _tenant_id())
        return jsonify({"ok": True, "cards": cards, "count": len(cards)})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/second-brain/proactive/launch-meeting-coworker", methods=["POST"])
def api_launch_meeting_coworker():
    """Spawn an ACE coworker pre-loaded with meeting prep context."""
    data = request.get_json(force=True, silent=True) or {}
    customer_name = data.get("customer_name", "")
    meeting_title = data.get("meeting_title", "")
    bullets = data.get("bullets", "")
    customer_type = data.get("customer_type", "")

    directive = (
        f"I have a meeting coming up: '{meeting_title}' with {customer_name} ({customer_type}).\n"
        f"Here is what was prepared:\n{bullets}\n\n"
        f"Help me prepare further. Ask me what I need help with — "
        f"draft talking points, anticipate their questions, or review the agenda."
    )

    try:
        from tools.ace.controller import ACEController
        instance_id = ACEController.get_instance().launch(
            problem_text=directive,
            trigger_source="second_brain_meeting_prep",
            trigger_ref=f"meeting:{meeting_title[:40]}",
            user_id=_user_id(),
        )
        return jsonify({"ok": True, "instance_id": instance_id, "redirect": f"/coworker/{instance_id}"})
    except Exception:
        import urllib.parse
        msg = urllib.parse.quote(directive[:500])
        return jsonify({"ok": True, "instance_id": None, "redirect": f"/coworker?prefill={msg}"})


# ─────────────────────────────────────────────────────────────────────────────
# Customer interaction history API
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/second-brain/customers/<relationship_id>/interactions", methods=["GET"])
def api_get_interactions(relationship_id: str):
    from tools.second_brain.interactions import get_interactions
    items = get_interactions(relationship_id, _user_id(), _tenant_id())
    return jsonify({"ok": True, "interactions": items})


@second_brain_bp.route("/api/second-brain/customers/<relationship_id>/interactions", methods=["POST"])
def api_log_interaction(relationship_id: str):
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.second_brain.interactions import log_interaction
        result = log_interaction(
            relationship_id=relationship_id,
            user_id=_user_id(),
            title=data.get("title", "Interaction"),
            notes=data.get("notes", ""),
            action_items=data.get("action_items", []),
            follow_up_date=data.get("follow_up_date"),
            interaction_type=data.get("interaction_type", "meeting"),
            interaction_date=data.get("interaction_date"),
            tenant_id=_tenant_id(),
        )
        return jsonify({"ok": True, "interaction": result})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route(
    "/api/second-brain/customers/<relationship_id>/interactions/<interaction_id>",
    methods=["DELETE"],
)
def api_delete_interaction(relationship_id: str, interaction_id: str):
    from tools.second_brain.interactions import delete_interaction
    ok = delete_interaction(interaction_id, _user_id(), _tenant_id())
    return jsonify({"ok": ok})


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
        return _err(exc, results=[])


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Personal Knowledge Base (Personal RAG)
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/learn", methods=["GET"])
def page_learn():
    from tools.second_brain.personal_rag import get_items
    items = get_items(_user_id(), _tenant_id())
    return render_template("second_brain/learn.html", items=items)


@second_brain_bp.route("/api/second-brain/learn/url", methods=["POST"])
def api_learn_url():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    from tools.second_brain.personal_rag import queue_url
    result = queue_url(_user_id(), url, data.get("title", ""), _tenant_id())
    return jsonify({"ok": True, **result})


@second_brain_bp.route("/api/second-brain/learn/text", methods=["POST"])
def api_learn_text():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    from tools.second_brain.personal_rag import queue_text
    result = queue_text(_user_id(), text, data.get("title", "Note"), data.get("tags", []), _tenant_id())
    return jsonify({"ok": True, **result})


@second_brain_bp.route("/api/second-brain/learn/items", methods=["GET"])
def api_learn_items():
    from tools.second_brain.personal_rag import get_items
    return jsonify({"ok": True, "items": get_items(_user_id(), _tenant_id())})


@second_brain_bp.route("/api/second-brain/learn/items/<item_id>", methods=["DELETE"])
def api_learn_delete(item_id: str):
    from tools.second_brain.personal_rag import delete_item
    ok = delete_item(item_id, _user_id(), _tenant_id())
    return jsonify({"ok": ok})


@second_brain_bp.route("/api/second-brain/learn/search", methods=["GET"])
def api_learn_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": False, "error": "q required"}), 400
    from tools.second_brain.personal_rag import search_personal_rag
    return jsonify({"ok": True, "results": search_personal_rag(q, _user_id(), _tenant_id())})


# ─────────────────────────────────────────────────────────────────────────────
# Slides audience tailoring
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/second-brain/slides/audience-framing", methods=["POST"])
def api_slides_audience_framing():
    """Return audience-tailored framing for a slide deck topic."""
    data = request.get_json(force=True, silent=True) or {}
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400
    from tools.second_brain.slides_tailor import get_audience_framing
    result = get_audience_framing(topic, _user_id(), _tenant_id())
    return jsonify({"ok": True, **result})


# ─────────────────────────────────────────────────────────────────────────────
# DIC personalisation test endpoint
# ─────────────────────────────────────────────────────────────────────────────


@second_brain_bp.route("/api/second-brain/dic/personalise", methods=["GET"])
def api_dic_personalise():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"ok": False, "error": "q required"}), 400
    try:
        from tools.document_intelligence.search import search_documents
        raw = search_documents(q, limit=10)
    except Exception:
        raw = []
    from tools.second_brain.dic_personaliser import personalise_dic_results
    ranked = personalise_dic_results(raw, _user_id(), _tenant_id())
    return jsonify({"ok": True, "results": ranked[:10]})


@second_brain_bp.route("/api/second-brain/relationships/health", methods=["GET"])
def api_relationship_health():
    """Return all relationships with health scores."""
    try:
        from tools.second_brain.relationship_health import get_relationship_health_map
        return jsonify({"ok": True, "relationships": get_relationship_health_map(_user_id(), _tenant_id())})
    except Exception as exc:
        return _err(exc)


@second_brain_bp.route("/api/integrations/google/contacts", methods=["GET"])
def api_google_contacts():
    """Return Gmail/People contacts for wizard relationship import."""
    try:
        from tools.second_brain.integrations import get_decrypted_token
        token = get_decrypted_token(_user_id(), "gcal", _tenant_id())
        if not token:
            return jsonify({"ok": False, "error": "Google not connected"}), 400
        from tools.second_brain.connectors.google import GoogleConnector
        # Use today's calendar attendees as a proxy for frequent contacts
        connector = GoogleConnector()
        items = connector.get_todays_items(_user_id())
        contacts: list[dict] = []
        seen: set[str] = set()
        for ev in items:
            for email in ev.get("attendees", []):
                if email and email not in seen:
                    seen.add(email)
                    contacts.append({"email": email, "name": email.split("@")[0], "org": ""})
        return jsonify({"ok": True, "contacts": contacts[:20]})
    except Exception as exc:
        return _err(exc)


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
        if service == "msgraph":
            # Microsoft Graph connector — lightweight wrapper
            from tools.second_brain.connectors import microsoft as _ms
            return _ms  # module acts as connector (no class needed)
    except Exception as exc:
        logger.debug("[second_brain] connector load failed for %s: %s", service, exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# M365 OAuth routes (dedicated — msgraph needs special scope/tenant handling)
# ─────────────────────────────────────────────────────────────────────────────

@second_brain_bp.route("/api/integrations/oauth/start/msgraph")
def oauth_start_msgraph():
    import secrets
    state = secrets.token_hex(16)
    session["oauth_state_msgraph"] = state
    redirect_uri = request.host_url.rstrip("/") + url_for("second_brain.oauth_callback_msgraph")
    from tools.second_brain.connectors.microsoft import get_oauth_url
    return redirect(get_oauth_url(redirect_uri, state))


@second_brain_bp.route("/api/integrations/oauth/callback/msgraph")
def oauth_callback_msgraph():
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code or state != session.pop("oauth_state_msgraph", None):
        return render_template("errors/400.html", message="M365 OAuth state mismatch"), 400
    redirect_uri = request.host_url.rstrip("/") + url_for("second_brain.oauth_callback_msgraph")
    try:
        from tools.second_brain.connectors.microsoft import exchange_code, get_user_profile
        tokens = exchange_code(code, redirect_uri)
        access_tok = tokens.get("access_token", "")
        ms_profile = get_user_profile(access_tok)
        meta = {
            "ms_user_id": ms_profile.get("id", ""),
            "ms_email": ms_profile.get("mail") or ms_profile.get("userPrincipalName", ""),
            "ms_name": ms_profile.get("displayName", ""),
        }
        # cnr-me-03: persist via the canvas connection (same DB every other
        # integration read/write uses) with encrypted tokens — no more split-brain.
        from tools.second_brain.integrations import save_integration
        save_integration(
            user_id=_user_id(),
            service="msgraph",
            access_token=access_tok,
            refresh_token=tokens.get("refresh_token", ""),
            token_expiry=str(tokens.get("expires_in", "")),
            metadata=meta,
            tenant_id=_tenant_id(),
        )
        return redirect(url_for("second_brain.integrations_page") + "?connected=msgraph")
    except Exception as exc:
        logger.warning("[second_brain] M365 OAuth callback error: %s", exc)
        return render_template("errors/500.html", message=f"M365 OAuth failed: {exc}"), 500


# ── Weekly Retrospective ──────────────────────────────────────────────────────

@second_brain_bp.route("/retro")
def page_retro():
    from tools.second_brain.retro import get_latest_retro
    retro = get_latest_retro(_user_id(), _tenant_id())
    return render_template("second_brain/retro.html", retro=retro)


@second_brain_bp.route("/api/second-brain/retro/generate", methods=["POST"])
def api_retro_generate():
    from tools.second_brain.retro import generate_weekly_retro
    retro = generate_weekly_retro(_user_id(), _tenant_id())
    return jsonify({"ok": True, "retro": retro})


@second_brain_bp.route("/api/second-brain/retro/export-slides", methods=["POST"])
def api_retro_export_slides():
    """Generate and return a PPTX of the latest weekly retrospective."""
    import io
    from flask import send_file
    from tools.second_brain.retro import export_retro_as_slides, generate_weekly_retro, get_latest_retro
    retro = get_latest_retro(_user_id(), _tenant_id())
    if not retro:
        retro = generate_weekly_retro(_user_id(), _tenant_id())
    if not retro:
        return jsonify({"ok": False, "error": "No retro data available"}), 404
    pptx_bytes = export_retro_as_slides(retro, _user_id(), _tenant_id())
    if not pptx_bytes:
        return jsonify({"ok": False, "error": "PPTX generation failed — slides builder may not be configured"}), 500
    week = retro.get("week_ending", "retro")
    return send_file(
        io.BytesIO(pptx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        as_attachment=True,
        download_name=f"retro-{week}.pptx",
    )


# ── Unified Search ────────────────────────────────────────────────────────────

@second_brain_bp.route("/search")
def page_search():
    q = request.args.get("q", "").strip()
    results = {}
    if q:
        from tools.second_brain.search import unified_search
        results = unified_search(q, _user_id(), _tenant_id())
    return render_template("second_brain/search.html", q=q, results=results)


@second_brain_bp.route("/api/second-brain/search")
def api_second_brain_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": False, "error": "q required"}), 400
    from tools.second_brain.search import unified_search
    return jsonify({"ok": True, "query": q, "results": unified_search(q, _user_id(), _tenant_id())})
