#!/usr/bin/env python3
# CUI // SP-CTI
"""
Strategos Research Bridge — routes Industry Research Engine output into Strategos.

Pulls signals, challenges, and dossiers from a research session and fans them
out to four Strategos targets:

  research_signals  (high-relevance, maritime keywords)
        └─► sg_ghost_signals        (domain awareness feed)

  research_signals  (all high-relevance)
        └─► sg_hitl_items           (analyst HITL queue)

  research_challenges  (top-scored)
        └─► sg_pir_requirements     (PIR / CCIR / EEI queue)

  research_dossiers    (completed dossier)
        └─► sg_intelligence_briefs  (Intel Briefs page)

Usage:
  # Bridge an existing session
  python -m tools.strategos.research_bridge --session rsess-1c5c6b47f611 --json

  # Create a new vert-strategos session and bridge immediately
  python -m tools.strategos.research_bridge --run --json

  # Dry-run: print what would be written without touching the DB
  python -m tools.strategos.research_bridge --session rsess-1c5c6b47f611 --dry-run

  # List bridgeable sessions
  python -m tools.strategos.research_bridge --list
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

# ── Routing config (mirrors strategos_routing in vertical JSON) ────────────
GHOST_KEYWORDS = {
    "ais", "vessel", "mmsi", "maritime", "ship", "spoofing",
    "dark", "gnss", "subsurface", "usv", "tanker", "frigate",
    "strait", "chokepoint", "naval", "fleet",
}
GHOST_MIN_RELEVANCE    = 0.6
HITL_MIN_RELEVANCE     = 0.65
PIR_MIN_SCORE          = 0.5
HITL_MAX               = 50   # cap per bridge run
PIR_MAX                = 20
GHOST_MAX              = 30

# AIS/maritime signal_types to assign when creating ghost signals
def _ghost_signal_type(title: str, body: str) -> str:
    text = (title + " " + (body or "")).lower()
    if any(k in text for k in ("spoof", "fake ais", "ais manipulation")):
        return "ais_spoofing"
    if any(k in text for k in ("dark vessel", "ais dark", "go dark", "turned off ais")):
        return "ais_dark"
    if any(k in text for k in ("gnss", "gps spoof", "gps denial", "navigation attack")):
        return "gnss_spoofing"
    if any(k in text for k in ("rf anomaly", "rf emission", "spectrum", "jamming", "jammer")):
        return "rf_anomaly"
    if any(k in text for k in ("subsurface", "submarine", "underwater")):
        return "subsurface_contact"
    if any(k in text for k in ("usv", "unmanned surface", "drone vessel")):
        return "usv_contact"
    if any(k in text for k in ("emcon", "emission control")):
        return "emcon_break"
    return "intelligence_signal"


def _challenge_pir_type(title: str, description: str) -> str:
    text = (title + " " + (description or "")).lower()
    if any(k in text for k in ("supply", "logistics", "interdiction", "chain")):
        return "CCIR"
    if any(k in text for k in ("electronic", "ew ", "spectrum", "jamming", "radar")):
        return "EEI"
    if any(k in text for k in ("information", "cognitive", "influence", "iw ")):
        return "EEI"
    return "PIR"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _score_to_priority(score: float) -> int:
    """Map composite score to integer priority (1=critical, 2=high, 3=medium, 4=low)."""
    if score >= 0.75:
        return 1
    if score >= 0.6:
        return 2
    if score >= 0.5:
        return 3
    return 4


def _is_maritime_signal(title: str, body: str, keywords: Optional[str]) -> bool:
    text = (title + " " + (body or "") + " " + (keywords or "")).lower()
    return any(kw in text for kw in GHOST_KEYWORDS)


# ── Load data from research DB ─────────────────────────────────────────────

def _load_session(conn, session_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, name, vertical_id, vertical_name, status, pipeline_stage, "
        "signal_count, challenge_count, dossier_id "
        "FROM research_sessions WHERE id = %s",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def _load_signals(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, source_type, title, body, keywords, classification "
        "FROM research_signals WHERE session_id = %s "
        "ORDER BY id DESC LIMIT 500",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_challenges(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, description, composite_score, severity, keywords "
        "FROM research_challenges WHERE session_id = %s "
        "AND composite_score >= %s "
        "ORDER BY composite_score DESC LIMIT 100",
        (session_id, PIR_MIN_SCORE),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_dossier(conn, dossier_id: Optional[str], session_id: str) -> Optional[dict]:
    if dossier_id:
        row = conn.execute(
            "SELECT id, title, content, executive_summary, overall_opportunity_score, generated_at "
            "FROM research_dossiers WHERE id = %s",
            (dossier_id,),
        ).fetchone()
        if row:
            return dict(row)
    # fallback: find any dossier for this session
    row = conn.execute(
        "SELECT id, title, content, executive_summary, overall_opportunity_score, generated_at "
        "FROM research_dossiers WHERE session_id = %s "
        "ORDER BY generated_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


# ── Write to Strategos ─────────────────────────────────────────────────────

def _write_ghost_signals(conn, signals: list[dict], session: dict, dry_run: bool) -> list[dict]:
    written = []
    for sig in signals:
        if not _is_maritime_signal(sig["title"], sig.get("body", ""), sig.get("keywords")):
            continue
        if len(written) >= GHOST_MAX:
            break
        entry = {
            "id":          str(uuid.uuid4()),
            "signal_type": _ghost_signal_type(sig["title"], sig.get("body", "")),
            "source":      f"research:{session['vertical_id']}:{sig.get('source_type','unknown')}",
            "detected_at": _now(),
            "confidence":  0.65,
            "behavior_profile_json": json.dumps({
                "research_signal_id": sig["id"],
                "session_id":         session["id"],
                "title":              sig["title"],
                "keywords":           sig.get("keywords"),
            }),
        }
        if not dry_run:
            conn.execute(
                "INSERT INTO sg_ghost_signals "
                "(id, signal_type, source, detected_at, confidence, behavior_profile_json) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (entry["id"], entry["signal_type"], entry["source"],
                 entry["detected_at"], entry["confidence"], entry["behavior_profile_json"]),
            )
        written.append(entry)
    return written


def _write_hitl_items(conn, signals: list[dict], session: dict, dry_run: bool) -> list[dict]:
    written = []
    for sig in signals:
        if len(written) >= HITL_MAX:
            break
        payload = json.dumps({
            "title":       sig["title"],
            "body":        (sig.get("body") or "")[:500],
            "source_type": sig.get("source_type"),
            "session_id":  session["id"],
            "vertical":    session["vertical_name"],
        })
        entry = {
            "id":        str(uuid.uuid4()),
            "item_type": "research_signal",
            "ref_id":    sig["id"],
            "payload":   payload,
            "status":    "pending",
            "created_at": _now(),
        }
        if not dry_run:
            conn.execute(
                "INSERT INTO sg_hitl_items (id, item_type, ref_id, payload, status, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (entry["id"], entry["item_type"], entry["ref_id"],
                 entry["payload"], entry["status"], entry["created_at"]),
            )
        written.append(entry)
    return written


def _write_pir_requirements(conn, challenges: list[dict], session: dict, dry_run: bool) -> list[dict]:
    written = []
    for ch in challenges:
        if len(written) >= PIR_MAX:
            break
        pir_type = _challenge_pir_type(ch["title"], ch.get("description", ""))
        priority = _score_to_priority(ch["composite_score"])
        entry = {
            "id":                  str(uuid.uuid4()),
            "pir_type":            pir_type,
            "topic":               ch["title"][:200],
            "description":         (ch.get("description") or "")[:1000] +
                                   f"\n\n[Source: research session {session['id']}, "
                                   f"vertical {session['vertical_name']}, "
                                   f"score {ch['composite_score']:.2f}]",
            "collection_priority": priority,
            "status":              "active",
            "created_at":          _now(),
            "updated_at":          _now(),
        }
        if not dry_run:
            conn.execute(
                "INSERT INTO sg_pir_requirements "
                "(id, pir_type, topic, description, collection_priority, status, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (entry["id"], entry["pir_type"], entry["topic"], entry["description"],
                 entry["collection_priority"], entry["status"],
                 entry["created_at"], entry["updated_at"]),
            )
        written.append(entry)
    return written


def _write_intelligence_brief(conn, dossier: dict, session: dict, dry_run: bool) -> Optional[dict]:
    title = dossier.get("title") or f"Research Dossier — {session['vertical_name']}"
    exec_summary = dossier.get("executive_summary") or ""
    content = dossier.get("content") or ""

    content_md = f"""# {title}

**Source:** Industry Research Engine — {session['vertical_name']}
**Session:** `{session['id']}`
**Generated:** {dossier.get('generated_at', _now())}
**Classification:** CUI // SP-CTI

---

## Executive Summary

{exec_summary}

---

## Full Analysis

{content}
"""
    score = float(dossier.get("overall_opportunity_score") or 0.5)
    entry = {
        "id":               str(uuid.uuid4()),
        "brief_type":       "assessment",
        "title":            title[:300],
        "content_md":       content_md,
        "sio_confidence":   min(score, 1.0),
        "analyst_reviewed": 0,
        "created_at":       _now(),
    }
    if not dry_run:
        conn.execute(
            "INSERT INTO sg_intelligence_briefs "
            "(id, brief_type, title, content_md, sio_confidence, analyst_reviewed, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (entry["id"], entry["brief_type"], entry["title"], entry["content_md"],
             entry["sio_confidence"], entry["analyst_reviewed"], entry["created_at"]),
        )
    return entry


# ── Main bridge function ───────────────────────────────────────────────────

def bridge(session_id: str, dry_run: bool = False) -> dict:
    """
    Bridge a research session into Strategos.
    Returns a summary dict of what was written to each target.
    """
    from tools.db.storage import get_connection
    conn = get_connection()
    result = {
        "session_id":    session_id,
        "dry_run":       dry_run,
        "ghost_signals": [],
        "hitl_items":    [],
        "pir_requirements": [],
        "intelligence_brief": None,
        "errors":        [],
    }
    try:
        session = _load_session(conn, session_id)
        if not session:
            result["errors"].append(f"Session not found: {session_id}")
            return result

        signals    = _load_signals(conn, session_id)
        challenges = _load_challenges(conn, session_id)
        dossier    = _load_dossier(conn, session.get("dossier_id"), session_id)

        # Route signals → ghost signals (maritime-keyword filtered)
        ghost_sigs = _write_ghost_signals(conn, signals, session, dry_run)
        result["ghost_signals"] = [
            {"id": g["id"], "signal_type": g["signal_type"], "source": g["source"]}
            for g in ghost_sigs
        ]

        # Route high-relevance signals → HITL queue
        hitl_items = _write_hitl_items(conn, signals, session, dry_run)
        result["hitl_items"] = [{"id": h["id"], "title": json.loads(h["payload"])["title"][:60]}
                                 for h in hitl_items]

        # Route top challenges → PIR requirements
        pir_reqs = _write_pir_requirements(conn, challenges, session, dry_run)
        result["pir_requirements"] = [
            {"id": p["id"], "pir_type": p["pir_type"], "topic": p["topic"][:60],
             "priority": p["collection_priority"]}
            for p in pir_reqs
        ]

        # Route dossier → intelligence brief
        if dossier:
            brief = _write_intelligence_brief(conn, dossier, session, dry_run)
            result["intelligence_brief"] = {
                "id":    brief["id"],
                "title": brief["title"],
                "score": brief["sio_confidence"],
            }

        if not dry_run:
            conn.commit()

    except Exception as exc:
        result["errors"].append(str(exc))
        if not dry_run:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        conn.close()

    return result


def _run_new_session() -> str:
    """Create and run a new vert-strategos research session, return session_id."""
    import subprocess
    import sys

    from tools.compat.subprocess_utils import runnable_module

    # `-m tools.…` cannot resolve in a child process: the tools → icdev.tools
    # alias is a sys.modules entry in the PARENT, and this call site sets no
    # PYTHONPATH. In a pip-installed environment all three subprocesses below
    # died with ModuleNotFoundError — the first two silently (check=False,
    # capture_output=True), so the failure surfaced only as the JSONDecodeError
    # raised on session_manager's empty stdout.
    # Load the vertical first
    subprocess.run(
        [sys.executable, "-m", runnable_module("tools.research.vertical_loader"),
         "--load", "--slug", "strategos"],
        check=False, capture_output=True,
    )
    # Create session — session_manager looks up by slug, not id
    r = subprocess.run(
        [sys.executable, "-m", runnable_module("tools.research.session_manager"),
         "--create", "--vertical", "strategos",
         "--name", "Strategos Intelligence Research",
         "--json"],
        capture_output=True, text=True,
    )
    try:
        sess = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"session_manager returned non-JSON: {r.stdout!r}\nstderr: {r.stderr!r}")
    if "error" in sess:
        raise RuntimeError(f"session_manager error: {sess['error']}\nstdout: {r.stdout!r}")
    session_id = sess.get("id") or sess.get("session_id")
    # Run the full pipeline
    subprocess.run(
        [sys.executable, "-m", runnable_module("tools.research.research_engine"),
         "--run", "--session", session_id, "--json"],
        check=False,
    )
    return session_id


def _list_bridgeable(limit: int = 20) -> None:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, vertical_id, status, signal_count, challenge_count, dossier_id "
            "FROM research_sessions "
            "WHERE status IN ('dossier_ready','reviewed','scanning','synthesizing') "
            "ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        print(f"{'Session ID':25s}  {'Vertical':25s}  {'Status':15s}  {'Signals':>7}  {'Challenges':>10}  Dossier")
        print("-" * 100)
        for r in rows:
            d = dict(r)
            has_dossier = "✓" if d.get("dossier_id") else "—"
            print(f"{d['id']:25s}  {(d['vertical_id'] or ''):25s}  "
                  f"{d['status']:15s}  {d['signal_count']:>7}  "
                  f"{d['challenge_count']:>10}  {has_dossier}")
    finally:
        conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="Strategos Research Bridge")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--session",  metavar="SESSION_ID",
                     help="Bridge an existing research session")
    grp.add_argument("--run",      action="store_true",
                     help="Create + run a new vert-strategos session, then bridge")
    grp.add_argument("--list",     action="store_true",
                     help="List bridgeable sessions")
    ap.add_argument("--dry-run",   action="store_true",
                    help="Print what would be written without touching DB")
    ap.add_argument("--json",      action="store_true",
                    help="Output results as JSON")
    args = ap.parse_args(argv)

    if args.list:
        _list_bridgeable()
        return

    session_id = args.session
    if args.run:
        print("[bridge] Creating and running vert-strategos session…", flush=True)
        session_id = _run_new_session()
        print(f"[bridge] Session: {session_id}", flush=True)

    result = bridge(session_id, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        dr = " (DRY RUN)" if args.dry_run else ""
        print(f"\nStrategos Research Bridge{dr}")
        print(f"  Session:            {result['session_id']}")
        print(f"  Ghost signals:      {len(result['ghost_signals'])}")
        print(f"  HITL items:         {len(result['hitl_items'])}")
        print(f"  PIR requirements:   {len(result['pir_requirements'])}")
        brief = result.get('intelligence_brief')
        print(f"  Intelligence brief: {'✓ ' + brief['title'][:50] if brief else '—'}")
        if result["errors"]:
            print("  Errors:")
            for e in result["errors"]:
                print(f"    ! {e}")

    if result["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
