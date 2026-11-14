# CUI // SP-CTI
"""POST /cortex/api/v1/bom: the route proposes, it never self-approves (icx-cnf-04, PM-CTX-17).

tests/bom/test_cortex_bom.py holds the engine behind the verb by calling the
undecorated function. This file holds the ROUTE a caller actually reaches:
  * no key, no answer (401); a key without ``cortex:bom`` is denied (403);
  * a credibility tier the caller does not declare comes back ``ai_proposed``
    with a rationale: the service proposes, a person's declaration binds;
  * a tier the caller does declare comes back ``set_by: human``;
  * a body with no documents is a 400 naming the defect, not a 500.
"""
from __future__ import annotations

import base64
import io

import openpyxl
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext

SCOPE = "cortex:bom"
PATH = "/cortex/api/v1/bom"


def make_client(*, binding=None, authenticated=True):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        # Mirrors tests/cortex/test_rest_award.py: a service-key caller is role
        # "service" with its binding; an unauthenticated caller has no user.
        if not authenticated:
            return
        if binding is not None:
            g.current_user = {"id": "cortex-svc:compass", "role": "service", "tenant_id": "compass"}
            g.cortex_binding = binding
        else:
            g.current_user = {"id": "u1", "role": "admin"}
        g.security_context = {"tenant_id": "compass", "user_id": "u1", "classification": "CUI"}

    return app.test_client()


def _binding(scopes):
    return {
        "ctx": CortexContext(tenant_id="compass", classification="CUI"),
        "scopes": list(scopes),
        "label": "compass",
        "tenant_id": "compass",
        "classification_ceiling": "CUI",
    }


def _doc(**extra):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    for row in (["Item", "Part Number", "QTY", "Unit Price", "Extended"],
                ["Core switch", "CS-9500-16X", 2, 21000, 42000]):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return {"filename": "bom.xlsx",
            "content_base64": base64.b64encode(buf.getvalue()).decode(), **extra}


def test_the_route_needs_a_caller_and_the_bom_scope():
    assert make_client(authenticated=False).post(PATH, json={"documents": [_doc()]}).status_code == 401
    denied = make_client(binding=_binding(["cortex:search"])).post(PATH, json={"documents": [_doc()]})
    assert denied.status_code == 403


def test_an_undeclared_tier_is_proposed_never_self_approved():
    res = make_client(binding=_binding([SCOPE])).post(PATH, json={"documents": [_doc()]})
    assert res.status_code == 200, res.get_json()
    src = res.get_json()["sources"][0]
    assert src["set_by"] == "ai_proposed"
    assert src["rationale"]


def test_only_the_callers_declaration_binds():
    res = make_client(binding=_binding([SCOPE])).post(PATH, json={"documents": [
        _doc(role="bom_claim", credibility_tier="authoritative")]})
    assert res.status_code == 200, res.get_json()
    src = res.get_json()["sources"][0]
    assert (src["credibility_tier"], src["set_by"]) == ("authoritative", "human")


def test_no_documents_is_a_400_naming_the_defect():
    res = make_client(binding=_binding([SCOPE])).post(PATH, json={"documents": []})
    assert res.status_code == 400
    assert "documents" in str(res.get_json())
