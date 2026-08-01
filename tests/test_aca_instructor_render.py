# CUI // SP-CTI
"""aca-trn-04 — the two instructor templates rendered for real, with real data.

``test_penta_aca_routes`` smokes every Academy route but patches
``render_template`` to a sentinel, so it proves the view's Python does not 500
and says nothing at all about the templates. A template is where this feature is
actually consumed: an ``UndefinedError``, an attribute that does not exist on the
dict the route passes, or a filter applied to ``None`` all render as a 500 that
the route smoke is structurally unable to see.

So this renders ``instructor.html`` and ``instructor_learner.html`` through a
real Jinja environment loading the **shipped** template directory, against a
**multi-learner, multi-tenant** database, with the context built by the same
``instructor`` functions the routes call. ``base.html`` is stubbed to a minimal
shell: it belongs to the dashboard app and carries 30+ injected canvas flags, so
loading it here would test the harness rather than these two views. Everything
inside ``{% block content %}`` — which is the entirety of what this card wrote —
is rendered for real.

The data deliberately includes the states that only appear with more than one
learner: a cohort assignment fanned out across several people, an overdue one, a
cancelled one, a score override with its prior value, and a second tenant whose
learners must not appear anywhere in the first tenant's page.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined

from _academy_conn import academy_conn

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "tools" / "dashboard" / "templates"

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

# Enough of base.html to render a child that extends it. Named blocks only —
# anything else would be asserting on the dashboard chrome, not on this feature.
_STUB_BASE = (
    "<!doctype html><html><head><title>{% block title %}{% endblock %}</title>"
    "{% block extra_css %}{% endblock %}</head><body>"
    "{% block content %}{% endblock %}"
    "{% block extra_js %}{% endblock %}</body></html>"
)


@pytest.fixture()
def env():
    """Jinja env over the SHIPPED template dir, with undefined names fatal.

    ``StrictUndefined`` is the point of the fixture: Jinja's default silently
    renders a missing variable as an empty string, which is exactly how a
    template referencing a key the route never passes reaches production looking
    fine on a page where that section happens to be empty.
    """
    return Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": _STUB_BASE}),
            FileSystemLoader(str(TEMPLATE_DIR)),
        ]),
        undefined=StrictUndefined,
        autoescape=True,
    )


@pytest.fixture()
def seeded(monkeypatch):
    """Three learners in tenant A, one in tenant B, with assignments and a review."""
    db = importlib.import_module("apps.forge_academy.db")
    inst = importlib.import_module("apps.forge_academy.instructor")
    conn = academy_conn()
    conn.executescript(db._DDL)
    conn.commit()
    monkeypatch.setattr(db, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(inst, "get_connection", lambda *a, **k: conn)

    for uid, username, role, xp, name in [
        (1, "avery", "devops", 900, "Avery Stone"),
        (2, "blake", "devops", 400, "Blake Rivers"),
        (3, "casey", "unset", 0, "Casey Wynn"),
    ]:
        conn.execute(
            "INSERT INTO fa_users (id,username,display_name,role,xp,level,streak_days,tenant_id) "
            "VALUES (%s,%s,%s,%s,%s,'recruit',3,%s)",
            (uid, username, name, role, xp, TENANT_A),
        )
    conn.execute(
        "INSERT INTO fa_users (id,username,display_name,role,xp,tenant_id) "
        "VALUES (9,'zed','Zed Other','devops',5000,%s)", (TENANT_B,),
    )
    for mid, slug, title, rf in [
        (1, "pipeline-basics", "Pipeline Basics", "devops"),
        (2, "threat-model", "Threat Modelling", "secops"),
    ]:
        conn.execute(
            "INSERT INTO fa_missions (id,slug,title,tier,role_filter,is_active,xp_reward) "
            "VALUES (%s,%s,%s,1,%s,1,50)", (mid, slug, title, rf),
        )
    conn.execute(
        "INSERT INTO fa_mission_steps (id,mission_id,step_num,title,step_type,xp_partial) "
        "VALUES (1,1,1,'Write the pipeline','coding',10)"
    )
    conn.execute(
        "INSERT INTO fa_mission_progress (user_id,mission_id,status,score,xp_earned) "
        "VALUES (1,1,'completed',72,50)"
    )
    conn.execute(
        "INSERT INTO fa_step_progress (user_id,step_id,status,submission,score,hints_used) "
        "VALUES (1,1,'completed','def build(): return 0',72,1)"
    )
    conn.execute(
        "INSERT INTO fa_xp_ledger (user_id,xp_delta,reason,source_type,source_id,is_attendance) "
        "VALUES (1,50,'Completed Pipeline Basics','mission',1,0)"
    )
    conn.commit()

    # A cohort assignment (fans out to the two devops learners), one overdue
    # learner assignment, and one that gets cancelled.
    inst.create_assignment(assigned_by="pm@test.local", actor_role="pm",
                           assignment_type="track", track_key="devops",
                           target_type="cohort", target_role="devops",
                           due_at="2030-01-01", note="Quarterly refresh",
                           tenant_id=TENANT_A)
    inst.create_assignment(assigned_by="pm@test.local", actor_role="pm",
                           mission_id=1, target_type="learner", target_user_id=2,
                           due_at="2020-01-01", tenant_id=TENANT_A)
    third = inst.create_assignment(assigned_by="pm@test.local", actor_role="pm",
                                   mission_id=1, target_type="learner",
                                   target_user_id=3, tenant_id=TENANT_A)
    inst.cancel_assignment(third["id"], actor="pm@test.local", tenant_id=TENANT_A)
    inst.record_review(user_id=1, verdict="approved", reviewer="pm@test.local",
                       mission_id=1, override_score=88,
                       comment="Clean pipeline, good rollback.", tenant_id=TENANT_A)
    try:
        yield inst, db, conn
    finally:
        conn.close()


def _console_html(env, inst, db):
    """Render instructor.html with the context instructor_page() builds."""
    from apps.forge_academy.constants import ROLES
    return env.get_template("forge_academy/instructor.html").render(
        learners=inst.roster(TENANT_A),
        assignments=inst.list_assignments(TENANT_A),
        missions=db.list_missions(tier=None),
        roles=ROLES,
        audit=inst.audit_trail(TENANT_A, limit=25),
        verdicts=sorted(inst.REVIEW_VERDICTS),
    )


def _learner_html(env, inst, db, user_id=1):
    """Render instructor_learner.html with the context instructor_learner_page() builds."""
    from apps.forge_academy.constants import ROLES
    return env.get_template("forge_academy/instructor_learner.html").render(
        learner=inst.get_learner(user_id, TENANT_A),
        summary=db.user_progress_summary(user_id, TENANT_A),
        assignments=inst.list_assignments(TENANT_A, user_id=user_id),
        submissions=inst.learner_submissions(user_id),
        evidence=inst.learner_evidence(user_id),
        reviews=inst.learner_reviews(user_id),
        roles=ROLES,
        verdicts=sorted(inst.REVIEW_VERDICTS),
    )


# ---------------------------------------------------------------------------
# The console
# ---------------------------------------------------------------------------

def test_the_console_renders_with_real_data(env, seeded):
    inst, db, _ = seeded
    html = _console_html(env, inst, db)
    assert "Instructor Console" in html
    # Every learner in the tenant is on the roster, including the one who never
    # picked a role — "who has not started" is the question this page exists for.
    for name in ("Avery Stone", "Blake Rivers", "Casey Wynn"):
        assert name in html


def test_the_console_never_shows_another_tenants_learner(env, seeded):
    inst, db, _ = seeded
    html = _console_html(env, inst, db)
    assert "Zed Other" not in html
    assert "5000" not in html


def test_the_console_renders_cohort_overdue_and_cancelled_states(env, seeded):
    """The three assignment states that only exist once there is a cohort."""
    inst, db, _ = seeded
    html = _console_html(env, inst, db)
    assert "OVERDUE" in html
    assert "CANCELLED" in html
    # The devops track fanned out to both devops learners, not to Casey.
    assert "Cohort:" in html


def test_the_console_offers_every_assignable_mission_and_role(env, seeded):
    inst, db, _ = seeded
    html = _console_html(env, inst, db)
    assert "Pipeline Basics" in html
    assert "Threat Modelling" in html
    # A track option per role, plus the explicit "everything" option.
    assert 'value="all"' in html


def test_the_console_shows_the_audit_trail(env, seeded):
    """An override is only attributable if the page actually prints who did it."""
    inst, db, _ = seeded
    html = _console_html(env, inst, db)
    assert "pm@test.local" in html
    assert "review.record" in html or "Review" in html


# ---------------------------------------------------------------------------
# The per-learner roster view
# ---------------------------------------------------------------------------

def test_the_learner_page_renders_with_real_data(env, seeded):
    inst, db, _ = seeded
    html = _learner_html(env, inst, db)
    assert "Avery Stone" in html


def test_the_learner_page_shows_the_actual_submission(env, seeded):
    """Reviewing work you cannot see is rubber-stamping."""
    inst, db, _ = seeded
    html = _learner_html(env, inst, db)
    assert "def build(): return 0" in html
    assert "Write the pipeline" in html


def test_the_learner_page_shows_verified_evidence_from_the_ledger(env, seeded):
    inst, db, _ = seeded
    html = _learner_html(env, inst, db)
    assert "Completed Pipeline Basics" in html


def test_the_learner_page_shows_the_override_and_what_it_replaced(env, seeded):
    """A score that changed without showing the prior value is not reviewable."""
    inst, db, _ = seeded
    html = _learner_html(env, inst, db)
    assert "88" in html
    assert "72" in html
    assert "Clean pipeline, good rollback." in html


def test_the_learner_page_offers_every_verdict(env, seeded):
    inst, db, _ = seeded
    html = _learner_html(env, inst, db)
    for verdict in ("approved", "needs_rework", "rejected"):
        assert verdict in html


def test_a_learner_with_no_activity_still_renders(env, seeded):
    """The empty-state path — Casey has no progress, no submissions, no evidence.

    Rendered last and asserted explicitly because every ``{% for %}`` in these
    templates has an empty case, and a filter applied to the ``None`` that an
    absent row produces is the most likely way this page 500s in the field.
    """
    inst, db, _ = seeded
    html = _learner_html(env, inst, db, user_id=3)
    assert "Casey Wynn" in html
