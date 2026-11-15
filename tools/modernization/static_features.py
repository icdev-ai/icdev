# CUI // SP-CTI
"""Report a legacy application's features STATICALLY, from the entry points the analyser already found (ICDEV[OS]
osx-fth-12).

``legacy_analyzer`` records each HTTP entry point it reads from source -- Spring and JAX-RS annotations, Servlet 3
``@WebServlet``, Stripes ``ActionBean`` bindings, ASP.NET MVC attributes and conventional routes -- in ``legacy_apis``,
and on its controller (``legacy_components.properties.entry_points``) how it found each one and how sure it is
(``legacy_analyzer.ENTRY_CONFIDENCE``). This module groups those entry points into features:

* by controller: a class that exposes more than one entry point (a controller, a JAX-RS resource, an action bean)
  is one feature, named for the class without its role suffix (``CartController`` -> "Cart");
* by resource: classes with one entry point each under the same first path segment (servlets under
  ``/servlet/...``) are that segment's feature;
* otherwise the class alone.

Each feature is labelled ``provenance: static`` and ``basis: source`` -- read from source, never seen running, so
never ``observed`` -- with the LOWEST confidence of its entry points. An entry point the analyser recorded no
detection for (a Struts descriptor action, say) counts at ``UNRECORDED``. Nothing is guessed beyond the entry
points: an application with none reports no features (null, unverified in ICDEV[OS]), never an empty list. A
feature is a CANDIDATE: ICDEV[OS] counts it for overlap only once a person confirms the capability it realises.

    python tools/modernization/static_features.py --app-id <legacy app id> [--out features.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TOOL = "icdev-it/static_features"
METHOD = "entry points grouped by controller or resource (deterministic, from source; never run)"
#: An entry point the analyser found without recording how (a Struts descriptor action, an older analysis).
UNRECORDED = 0.5
_ROLE = re.compile(r"(Controller|ActionBean|Action|Servlet|Resource|Endpoint|Api|Handler)$")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def _words(name: str) -> str:
    """``SiteLayout`` -> "Site layout", ``ejb3`` -> "Ejb3"."""
    w = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", re.sub(r"[_\-.]+", " ", name)).strip()
    return (w[:1].upper() + w[1:].lower()) if w else name


def _props(raw) -> dict:
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def entry_points(app_id: str, conn) -> list[dict]:
    """Every entry point of the application, with its controller and how the analyser found it."""
    rows = conn.execute(
        "SELECT a.method, a.path, a.handler_function, c.name, c.qualified_name, c.file_path, c.properties "
        "FROM legacy_apis a LEFT JOIN legacy_components c ON c.id = a.component_id WHERE a.legacy_app_id = %s",
        (app_id,)).fetchall()
    out = []
    for r in rows:
        method = (r["method"] or "ALL").upper()
        found = {(e.get("method"), e.get("path")): e for e in _props(r["properties"]).get("entry_points") or []}
        e = found.get((method, r["path"])) or {}
        # no component: a descriptor action, whose handler is the class it names (``com.acme.LoginAction``)
        cls = r["name"] or (r["handler_function"] or "").rsplit(".", 1)[-1] or None
        out.append({"ref": f"{method} {r['path']}", "path": r["path"], "handler": r["handler_function"],
                    "class": cls, "qualified": r["qualified_name"] or cls, "file": r["file_path"],
                    "detected_by": e.get("detected_by", "unrecorded"),
                    "confidence": float(e["confidence"]) if e.get("confidence") is not None else UNRECORDED})
    return sorted(out, key=lambda x: (x["ref"], x["qualified"] or ""))


def _segment(path: str) -> str | None:
    parts = [p for p in path.split("?")[0].split("/") if p]
    return parts[0] if len(parts) >= 2 else None


def derive(app_id: str, conn) -> list[dict] | None:
    """The application's features, grouped from its entry points; None when it has none."""
    eps = entry_points(app_id, conn)
    if not eps:
        return None
    per_class: dict[str, list[dict]] = {}
    for e in eps:
        per_class.setdefault(e["qualified"] or e["ref"], []).append(e)
    singles: dict[str, int] = {}                 # one-entry classes under each first path segment
    for members in per_class.values():
        if len(members) == 1 and (seg := _segment(members[0]["path"])):
            singles[seg] = singles.get(seg, 0) + 1
    groups: dict[str, dict] = {}
    for key, members in sorted(per_class.items()):
        seg = _segment(members[0]["path"]) if len(members) == 1 else None
        seg = seg if seg and singles.get(seg, 0) > 1 else None
        title = _words(seg) if seg else _words(_ROLE.sub("", members[0]["class"] or "") or members[0]["class"] or key)
        g = groups.setdefault(_slug(title), {"title": title, "members": []})
        g["members"] += members
    out = []
    for fid, g in sorted(groups.items()):
        ms = g["members"]
        out.append({"id": fid, "title": g["title"], "basis": "source", "provenance": "static",
                    "confidence": round(min(m["confidence"] for m in ms), 2),
                    "signatures": {"apis": sorted({m["ref"] for m in ms})},
                    "evidence": sorted({f"source:{m['file']}" if m["file"] else f"api:{m['ref']}" for m in ms})})
    return out


def report(app_id: str, conn) -> dict:
    """What this tool hands the exporter (``apptwin_export.py --static-features``)."""
    feats = derive(app_id, conn)
    eps = entry_points(app_id, conn)
    by: dict[str, int] = {}
    for e in eps:
        by[e["detected_by"]] = by.get(e["detected_by"], 0) + 1
    return {"tool": TOOL, "method": METHOD, "provenance": "static", "app_id": app_id, "features": feats,
            "entry_points": len(eps), "detected_by": dict(sorted(by.items()))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report a legacy application's features from its entry points")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--out", type=Path, help="write the report here (default: stdout)")
    a = ap.parse_args(argv)
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rep = report(a.app_id, conn)
    finally:
        conn.close()
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "out": str(a.out), "features": len(rep["features"] or []),
                          "entry_points": rep["entry_points"]}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
