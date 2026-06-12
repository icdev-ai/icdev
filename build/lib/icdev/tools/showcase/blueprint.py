# CUI // SP-CTI
"""AI Canvas Demo Runner — Flask Blueprint.

Routes:
  GET  /demo-runner/                Demo control panel
  POST /demo-runner/api/run         Execute demo scenarios → stores run, returns JSON
  GET  /demo-runner/api/run-stream  SSE live stream — fires events per act as they complete
  GET  /demo-runner/api/runs        List run history
  POST /demo-runner/api/iqe-query   IQE natural-language query
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import importlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context
from tools.security.canvas_access import check_access as _canvas_check_access

_ROOT = Path(__file__).resolve().parents[2]
logger = get_logger(__name__)

demo_runner_bp = Blueprint(
    "demo_runner",
    __name__,
    url_prefix="/demo-runner",
    template_folder="../../tools/dashboard/templates",
)

@demo_runner_bp.before_request
def _check_canvas_access():
    """G-02: DENY-ALL canvas access gate. Requires explicit grant in canvas_access_grants."""
    try:
        from flask import g, abort, request as _req
        # Skip health/status utility endpoints
        if _req.path.endswith(("/health", "/status", "/ping")):
            return
        user = getattr(g, "current_user", None) or {}
        user_id = str(user.get("id", "") or user.get("user_id", "") or "")
        tenant_id = str(getattr(g, "tenant_id", None) or user.get("tenant_id", "") or "")
        if not user_id or not tenant_id:
            abort(403)
        if not _canvas_check_access(user_id, tenant_id, "showcase"):
            abort(403)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("canvas_access check error: %s", exc)
        abort(403)


_INIT_DONE = False

_SCENARIO_META = {
    1: {
        "title": "AI Observatory", "short": "Obs", "color": "#a78bfa",
        "hook": "Governance visibility",
        "narrate_start": "Querying AI decision log across 8 canvases — checking confabulation flags and HITL queue…",
        "narrate_pass":  "Decision audit complete. Confabulation flags surfaced. Full trace visibility confirmed.",
        "narrate_fail":  "Observatory query returned unexpected results — check canvas_ai_decisions.",
    },
    2: {
        "title": "AADC Design", "short": "AADC", "color": "#60a5fa",
        "hook": "HITL + rights-impacting",
        "narrate_start": "Loading 8 agentic AI designs — checking ATO readiness and rights-impacting classification…",
        "narrate_pass":  "ATO pipeline verified. Rights-impacting designs flagged for CAIO review per OMB M-25-21 §4.",
        "narrate_fail":  "AADC query failed — check aadc_designs schema.",
    },
    3: {
        "title": "AIMC Models", "short": "AIMC", "color": "#34d399",
        "hook": "IL5 air-gap + ROI",
        "narrate_start": "Loading AI/ML model designs — verifying IL5 air-gap boundary and DoD RAI compliance…",
        "narrate_pass":  "IL filter confirmed. SIGINT NLP air-gap enforced. FAR/DFARS Q&A ROI: 29x vs GS-12.",
        "narrate_fail":  "AIMC query failed — check aiml_designs schema.",
    },
    4: {
        "title": "AAC Modernization", "short": "AAC", "color": "#f59e0b",
        "hook": "Legacy system scan",
        "narrate_start": "Scanning 5 legacy systems (GCSS-Army, SIEM, DFAS, JTRS, HR) for AI augmentation opportunities…",
        "narrate_pass":  "72 opportunities identified. SIEM composite 0.85 — IOC embedding pilot saves 10 days.",
        "narrate_fail":  "AAC scan query failed — check aac_scans and aac_opportunities.",
    },
    5: {
        "title": "Cross-Canvas", "short": "Gov", "color": "#f87171",
        "hook": "OMB M-25-21 inventory",
        "narrate_start": "Running cross-canvas governance sweep — rights-impacting inventory and low-confidence HITL queue…",
        "narrate_pass":  "OMB M-25-21 inventory complete. Rights-impacting designs identified. CAIO queue populated.",
        "narrate_fail":  "Cross-canvas sweep failed — check shared icdev.db connection.",
    },
}


# ── DB setup ──────────────────────────────────────────────────────────────────

def _ensure_db() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        from tools.db.storage import get_canvas_connection
        conn = get_canvas_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS showcase_demo_runs (
                run_id      TEXT PRIMARY KEY,
                audience    TEXT NOT NULL DEFAULT 'exec',
                scenarios_json TEXT NOT NULL DEFAULT '[]',
                status      TEXT NOT NULL DEFAULT 'running',
                result_json TEXT,
                scenarios_passed INTEGER DEFAULT 0,
                scenarios_total  INTEGER DEFAULT 0,
                elapsed_ms  INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        _INIT_DONE = True
    except Exception as exc:
        logger.warning("demo_runner: DB init error: %s", exc)


@demo_runner_bp.before_request
def _init():
    _ensure_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_recent_runs(limit: int = 20) -> list[dict]:
    try:
        from tools.db.storage import get_canvas_connection
        conn = get_canvas_connection()
        rows = conn.execute(
            "SELECT run_id, audience, scenarios_json, status, scenarios_passed, "
            "scenarios_total, elapsed_ms, created_at "
            "FROM showcase_demo_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        result = []
        for row in rows:
            d = dict(row) if hasattr(row, "keys") else {
                "run_id": row[0], "audience": row[1], "scenarios_json": row[2],
                "status": row[3], "scenarios_passed": row[4],
                "scenarios_total": row[5], "elapsed_ms": row[6], "created_at": row[7],
            }
            try:
                d["scenarios_list"] = json.loads(d.get("scenarios_json") or "[]")
            except (ValueError, TypeError):
                d["scenarios_list"] = []
            result.append(d)
        return result
    except Exception as exc:
        logger.warning("demo_runner: run history error: %s", exc)
        return []


def _store_run(run_id: str, audience: str, scenarios, status: str,
               result: dict, passed: int, total: int, elapsed_ms: int) -> None:
    try:
        from tools.db.storage import get_canvas_connection
        conn = get_canvas_connection()
        conn.execute(
            "INSERT INTO showcase_demo_runs "
            "(run_id, audience, scenarios_json, status, result_json, "
            "scenarios_passed, scenarios_total, elapsed_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, audience, json.dumps(scenarios),
             status, json.dumps(result, default=str),
             passed, total, elapsed_ms,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("demo_runner: store run error: %s", exc)


def _do_run(audience: str, scenarios: list[int] | None) -> dict:
    """Import and call run_all_scenarios() directly with output suppressed."""
    runner = importlib.import_module("tools.showcase.ai_canvas_demo_runner")
    old_mode = runner._JSON_MODE
    runner._JSON_MODE = True
    try:
        return runner.run_all_scenarios(scenarios, audience=audience)
    finally:
        runner._JSON_MODE = old_mode


# ── Routes ────────────────────────────────────────────────────────────────────

@demo_runner_bp.route("/")
def index():
    runs = _get_recent_runs(15)
    last_run = runs[0] if runs else None
    last_result = {}
    if last_run and last_run.get("result_json"):
        try:
            last_result = json.loads(last_run["result_json"])
        except (ValueError, TypeError):
            pass
    return render_template(
        "demo_runner/page.html",
        runs=runs,
        last_run=last_run,
        last_result=last_result,
        scenario_meta=_SCENARIO_META,
        iqe_canvas="demo_runner",
        iqe_api_route="/demo-runner/api/iqe-query",
        iqe_title="Demo Runner IQE",
        iqe_examples=[
            {"label": "Recent runs", "query": "show recent demo runs"},
            {"label": "Exec audience", "query": "show exec audience runs"},
            {"label": "Failed runs",   "query": "show failed runs"},
        ],
    )


@demo_runner_bp.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True, silent=True) or {}
    audience = (data.get("audience") or "exec").lower()
    raw_scenarios = data.get("scenarios")

    if not raw_scenarios or raw_scenarios == "all":
        scenarios: list[int] | None = None
    else:
        try:
            scenarios = [int(s) for s in raw_scenarios if 1 <= int(s) <= 5]
        except (ValueError, TypeError):
            scenarios = None

    run_id = str(uuid.uuid4())
    t0 = time.monotonic()
    result: dict = {}
    status = "error"

    try:
        result = _do_run(audience, scenarios)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        passed = sum(1 for v in result.values() if isinstance(v, dict) and v.get("status") == "pass")
        total = len(result)
        status = "pass" if total > 0 and passed == total else "partial" if passed > 0 else "fail"
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        result = {"error": str(exc)}
        passed = 0
        total = 0
        status = "error"
        logger.exception("demo_runner: run error: %s", exc)

    _store_run(run_id, audience, scenarios or "all", status, result, passed, total, elapsed_ms)

    return jsonify({
        "run_id": run_id,
        "status": status,
        "results": result,
        "scenarios_passed": passed,
        "scenarios_total": total,
        "elapsed_ms": elapsed_ms,
        "scenario_meta": _SCENARIO_META,
    })


@demo_runner_bp.route("/api/runs")
def api_runs():
    limit = min(int(request.args.get("limit", 20)), 100)
    return jsonify(_get_recent_runs(limit))


@demo_runner_bp.route("/api/run-stream")
def api_run_stream():
    """SSE endpoint — yields one event per act as it completes."""
    audience = (request.args.get("audience") or "exec").lower()
    scenarios_param = (request.args.get("scenarios") or "all").strip()

    if scenarios_param == "all":
        to_run = [1, 2, 3, 4, 5]
    else:
        try:
            to_run = [int(s) for s in scenarios_param.split(",") if s.strip().isdigit()
                      and 1 <= int(s.strip()) <= 5]
        except (ValueError, TypeError):
            to_run = [1, 2, 3, 4, 5]
    if not to_run:
        to_run = [1, 2, 3, 4, 5]

    run_id = str(uuid.uuid4())

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, default=str)}\n\n"

    def generate():
        runner = importlib.import_module("tools.showcase.ai_canvas_demo_runner")
        old_mode = runner._JSON_MODE
        runner._JSON_MODE = True
        dispatch = {
            1: runner.scenario_1, 2: runner.scenario_2, 3: runner.scenario_3,
            4: runner.scenario_4, 5: runner.scenario_5,
        }
        t0 = time.monotonic()
        results: dict = {}

        yield _sse({"type": "init", "run_id": run_id, "total": len(to_run)})

        for idx, num in enumerate(to_run):
            meta = _SCENARIO_META[num]
            yield _sse({
                "type": "start", "num": num,
                "title": meta["title"], "hook": meta["hook"],
                "color": meta["color"], "narrate": meta["narrate_start"],
                "index": idx + 1, "total": len(to_run),
            })

            t_act = time.monotonic()
            fn = dispatch.get(num)
            try:
                result = fn()
                elapsed = int((time.monotonic() - t_act) * 1000)
                results[f"s{num}"] = result
                status = result.get("status", "pass")
                narrate = meta["narrate_pass"] if status == "pass" else meta["narrate_fail"]
                yield _sse({
                    "type": "result", "num": num, "status": status,
                    "result": result, "elapsed_ms": elapsed, "narrate": narrate,
                })
            except Exception as exc:
                elapsed = int((time.monotonic() - t_act) * 1000)
                results[f"s{num}"] = {"status": "error", "error": str(exc)}
                yield _sse({
                    "type": "result", "num": num, "status": "error",
                    "result": {"status": "error", "error": str(exc)},
                    "elapsed_ms": elapsed, "narrate": meta["narrate_fail"],
                })

        runner._JSON_MODE = old_mode
        total_ms = int((time.monotonic() - t0) * 1000)
        passed = sum(1 for v in results.values()
                     if isinstance(v, dict) and v.get("status") == "pass")
        total = len(results)
        run_status = ("pass" if total > 0 and passed == total
                      else "partial" if passed > 0 else "fail")
        _store_run(run_id, audience, to_run, run_status, results, passed, total, total_ms)
        yield _sse({
            "type": "complete", "run_id": run_id,
            "passed": passed, "total": total,
            "elapsed_ms": total_ms, "status": run_status,
            "results": results,
        })

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@demo_runner_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        importlib.import_module("tools.iqe.adapters.demo_runner")
    except Exception:
        pass

    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import parse as _parse
    from tools.iqe.executor import execute_query

    collections = ["demo_runner.runs", "demo_runner.scenarios", "demo_runner.results"]
    nl_result = nl_to_iqe(question, collections)
    iqe_str = nl_result.get("iqe", "")
    explanation = nl_result.get("explanation", "")

    rows: list = []
    try:
        ast = _parse(iqe_str)
        rows = execute_query(ast, conn=None)
    except Exception:
        rows = []

    return jsonify({
        "ok": True,
        "canvas": "demo_runner",
        "iqe": iqe_str,
        "explanation": explanation,
        "rows": rows,
        "row_count": len(rows),
    })
