# CUI // SP-CTI
"""End-to-end document lifecycle exercise for the Document Intelligence Canvas.

Exercises the full collaborative lifecycle through the REAL DIC blueprint + real
database, via an in-process Flask test client (so it always runs the current code,
not a possibly-stale long-running dashboard process):

    generate -> manual edit (optimistic concurrency) -> STALE edit rejected (409)
    -> AI regenerate (cross-canvas RAG+KG) -> HITL approve section -> HITL approve version

Run::

    python tools/document_intelligence/e2e_lifecycle.py
    python tools/document_intelligence/e2e_lifecycle.py --collection net_knowledge

Prereq: the acting user (``current_user``) must hold an editor/reviewer/admin role
on the collection (DIC gates mutations via dic_team_access).
"""
from __future__ import annotations

import argparse

STEPS: list[tuple[str, str]] = []


def _ok(cond: bool, msg: str) -> None:
    STEPS.append(("PASS" if cond else "FAIL", msg))
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def _build_app():
    from flask import Flask, g

    from tools.document_intelligence.blueprint import dic_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    # Mirror the no-auth dev dashboard: no injected security context, so
    # _current_user() resolves to 'current_user' (granted admin on the collection).
    # (The 'g' import is kept for parity with the dashboard request shape.)
    _ = g
    app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    return app


def _j(resp):
    try:
        return resp.get_json() or {}
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="net_knowledge")
    ap.add_argument("--query", default="Production network segmentation and BGP security runbook")
    args = ap.parse_args()
    coll = args.collection
    P = "/document-intelligence"
    print(f"DIC document lifecycle E2E (in-process, collection={coll})\n")

    app = _build_app()
    c = app.test_client()

    # 1) GENERATE -------------------------------------------------------------
    print("1) Generate a grounded, AI-labeled draft (pending_review)…")
    r = c.post(f"{P}/api/generate", json={"query": args.query, "collection_id": coll})
    gen = _j(r)
    version_id = gen.get("version_id")
    _ok(r.status_code == 200 and bool(version_id), f"generate -> {r.status_code}, version={version_id}")
    _ok(gen.get("origin") == "ai_generated" and gen.get("status") == "pending_review",
        f"AI-labeled + HITL-gated (origin={gen.get('origin')}, status={gen.get('status')})")
    _ok(bool(gen.get("context_canvases")),
        f"cross-canvas context used: {gen.get('context_canvases')} "
        f"({len(gen.get('cross_canvas_sources', []))} sources)")
    # The bug this guards against: AI Assist silently produced EMPTY, uncited
    # sections (broken LLM router API). Require real grounded output.
    gen_secs = gen.get("sections", [])
    nonempty = [s for s in gen_secs if (s.get("content") or "").strip()
                and not (s.get("content") or "").startswith("(Abstained")]
    _ok(bool(nonempty),
        f"generated >=1 non-empty, non-abstained section ({len(nonempty)}/{len(gen_secs)})")
    _ok(any(s.get("citation_count", 0) >= 1 for s in nonempty),
        f"generated content carries citations (max={max([s.get('citation_count',0) for s in gen_secs] or [0])})")
    if not version_id:
        return _finish()

    # 2) READ SECTIONS --------------------------------------------------------
    print("2) Read sections (with optimistic-lock rev)…")
    r = c.get(f"{P}/api/versions/{version_id}/sections")
    sections = _j(r).get("sections", [])
    _ok(r.status_code == 200 and bool(sections), f"sections -> {r.status_code}, count={len(sections)}")
    if not sections:
        return _finish()
    section_id = sections[0]["section_id"]
    heading = sections[0]["heading"]
    rev = sections[0].get("rev", 1)
    print(f"     target section: {section_id} '{heading}' rev={rev}")

    # 3) MANUAL EDIT (optimistic concurrency, correct rev) --------------------
    print("3) Manual edit with current rev (should succeed + bump rev)…")
    r = c.post(f"{P}/api/sections/{section_id}/content",
               json={"content": "Manually edited by net-admin: production segments use /24 "
                                "allocations per RFC 1918; eBGP peers filtered per RFC 7454.",
                     "base_rev": rev})
    new_rev = _j(r).get("rev")
    _ok(r.status_code == 200 and new_rev == rev + 1, f"edit -> {r.status_code}, rev {rev} -> {new_rev}")

    # 4) STALE EDIT (same old rev) -> 409 conflict, no clobber ----------------
    print("4) Concurrent stale edit with the OLD rev (should be refused 409)…")
    r = c.post(f"{P}/api/sections/{section_id}/content",
               json={"content": "STALE overwrite that must NOT win.", "base_rev": rev})
    conf = _j(r)
    _ok(r.status_code == 409 and conf.get("status") == "conflict",
        f"stale edit refused -> {r.status_code} (current_rev={conf.get('current_rev')})")
    r = c.get(f"{P}/api/versions/{version_id}/sections")
    cur = next((s for s in _j(r).get("sections", []) if s["section_id"] == section_id), {})
    _ok("Manually edited by net-admin" in (cur.get("content") or ""),
        "first writer's content preserved (no clobber)")

    # 5) AI REGENERATE with cross-canvas context ------------------------------
    print("5) AI-regenerate the section (cross-canvas RAG+KG)…")
    r = c.post(f"{P}/api/generate/section",
               json={"version_id": version_id, "heading": heading, "collection_id": coll})
    regen = _j(r)
    _ok(r.status_code == 200 and regen.get("status") == "pending_review",
        f"regenerate -> {r.status_code}, status={regen.get('status')}, "
        f"cross_canvas={regen.get('cross_canvas_count')}, canvases={regen.get('context_canvases')}")
    # Guard the exact failure the user hit: a silent no-op (empty content + no
    # citations + no abstain signal). Either we produced cited content, OR we
    # abstained HONESTLY with a reason — never silently nothing.
    rc = (regen.get("content") or "").strip()
    if regen.get("abstained"):
        _ok(bool(regen.get("reason")) and rc.startswith("(Abstained"),
            f"abstention is explicit + reasoned (reason={regen.get('reason')})")
    else:
        _ok(bool(rc) and not rc.startswith("(Abstained"),
            f"regenerated non-empty content ({len(rc)} chars)")
        _ok(regen.get("citation_count", 0) >= 1,
            f"regenerated content is cited ({regen.get('citation_count')} citations)")
        # Confirm the new content was actually PERSISTED (the original bug returned
        # a dict but never wrote the DB, so the page reload showed nothing).
        r = c.get(f"{P}/api/versions/{version_id}/sections")
        persisted = next((s for s in _j(r).get("sections", []) if s["heading"] == heading), {})
        _ok((persisted.get("content") or "").strip() == rc and bool(rc),
            "regenerated content persisted to DB (reload would show it)")

    # 6) HITL APPROVE the section ---------------------------------------------
    print("6) HITL: reviewer approves the section…")
    r = c.post(f"{P}/api/sections/{section_id}/approve",
               json={"reviewer": "net-admin", "note": "Verified against RFC corpus + NDC context."})
    _ok(r.status_code == 200 and _j(r).get("status") == "approved",
        f"section approve -> {r.status_code} ({_j(r).get('status')})")

    # 7) HITL APPROVE the version (publish) -----------------------------------
    print("7) HITL: reviewer approves the whole version (publish)…")
    r = c.post(f"{P}/api/review/{version_id}/approve",
               json={"reviewer": "net-admin", "note": "Publish v1.", "type": "version"})
    _ok(r.status_code == 200, f"version approve -> {r.status_code} ({_j(r).get('status')})")

    return _finish()


def _finish() -> int:
    passed = sum(1 for s, _ in STEPS if s == "PASS")
    total = len(STEPS)
    print(f"\nLifecycle E2E: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
