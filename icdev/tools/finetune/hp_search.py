#!/usr/bin/env python3
# CUI // SP-CTI
"""Hyperparameter search orchestrator for ICDEV™ fine-tuning (D-FT-13).

Grid or random search over LoRA/training hyperparameters.  Each trial
creates a training job via training_engine; results are recorded after
evaluation completes.  Designed to be non-blocking — run_next_trial
queues a job, an external scheduler checks completion and calls
record_trial_result.

Usage:
    python tools/finetune/hp_search.py --create --dataset-id ds-123 --json
    python tools/finetune/hp_search.py --create --dataset-id ds-123 --method random --max-trials 5 --json
    python tools/finetune/hp_search.py --run-next --search-id hp-123 --json
    python tools/finetune/hp_search.py --record --trial-id trial-123 --score 0.85 --json
    python tools/finetune/hp_search.py --status --search-id hp-123 --json
    python tools/finetune/hp_search.py --list --json
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ── SQL DDL ──────────────────────────────────────────────────────────

_DDL_SEARCHES = """
CREATE TABLE IF NOT EXISTS ft_hp_searches (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'grid',
    search_space TEXT NOT NULL DEFAULT '{}',
    max_trials INTEGER DEFAULT 9,
    completed_trials INTEGER DEFAULT 0,
    best_trial_id TEXT,
    best_score REAL,
    best_params TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed','cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_DDL_TRIALS = """
CREATE TABLE IF NOT EXISTS ft_hp_trials (
    id TEXT PRIMARY KEY,
    search_id TEXT NOT NULL REFERENCES ft_hp_searches(id),
    trial_number INTEGER NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    training_job_id TEXT,
    eval_score REAL,
    eval_metrics TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed','skipped')),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_DDL_SEARCHES)
    conn.execute(_DDL_TRIALS)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_search_id() -> str:
    return f"hp-{uuid.uuid4().hex[:12]}"


def _new_trial_id() -> str:
    return f"trial-{uuid.uuid4().hex[:12]}"


def _load_config() -> Dict[str, Any]:
    """Load hyperparam_search block from finetune_config.yaml."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("hyperparam_search", {})
        except (ImportError, Exception):
            pass
    return {}


def _generate_grid(search_space: Dict[str, List], max_trials: int) -> List[Dict[str, Any]]:
    """Cartesian product of search_space values, truncated to max_trials."""
    keys = sorted(search_space.keys())
    if not keys:
        return [{}]
    values = [search_space[k] for k in keys]
    combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    return combos[:max_trials]


def _generate_random(
    search_space: Dict[str, List],
    max_trials: int,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """Random sample from search_space values."""
    keys = sorted(search_space.keys())
    if not keys:
        return [{}]
    values = [search_space[k] for k in keys]
    all_combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    rng = random.Random(seed)
    n = min(max_trials, len(all_combos))
    return rng.sample(all_combos, n)


# ── Core Functions ───────────────────────────────────────────────────


def create_search(
    dataset_id: str,
    method: Optional[str] = None,
    search_space: Optional[Dict[str, List]] = None,
    max_trials: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new hyperparameter search with generated trials."""
    cfg = _load_config()

    method = method or cfg.get("method", "grid")
    if method not in ("grid", "random"):
        return {"success": False, "error": f"Invalid method: {method}. Must be 'grid' or 'random'."}

    search_space = search_space or cfg.get(
        "search_space",
        {
            "lora_rank": [8, 16, 32],
            "learning_rate": [0.0001, 0.0002, 0.0005],
        },
    )
    max_trials = max_trials or cfg.get("max_trials", 9)

    # Generate trial parameter sets
    if method == "grid":
        trial_params = _generate_grid(search_space, max_trials)
    else:
        trial_params = _generate_random(search_space, max_trials)

    search_id = _new_search_id()
    now = _now()

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO ft_hp_searches
               (id, dataset_id, method, search_space, max_trials,
                completed_trials, best_params, status, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, 0, '{}', 'pending', %s, %s)""",
            (search_id, dataset_id, method, json.dumps(search_space), len(trial_params), now, now),
        )

        for idx, params in enumerate(trial_params, start=1):
            trial_id = _new_trial_id()
            conn.execute(
                """INSERT INTO ft_hp_trials
                   (id, search_id, trial_number, params, status, created_at)
                   VALUES (%s, %s, %s, %s, 'pending', %s)""",
                (trial_id, search_id, idx, json.dumps(params), now),
            )

        conn.commit()

        return {
            "success": True,
            "search_id": search_id,
            "dataset_id": dataset_id,
            "method": method,
            "trial_count": len(trial_params),
            "max_trials": len(trial_params),
            "search_space": search_space,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def run_next_trial(
    search_id: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Pick the next pending trial and create a training job for it."""
    conn = _get_db(db_path)
    try:
        # Validate search exists
        search = conn.execute("SELECT * FROM ft_hp_searches WHERE id = %s", (search_id,)).fetchone()
        if not search:
            return {"success": False, "error": f"Search not found: {search_id}"}

        if search["status"] in ("completed", "failed", "cancelled"):
            return {"success": False, "error": f"Search already in terminal state: {search['status']}"}

        # Find next pending trial
        trial = conn.execute(
            """SELECT * FROM ft_hp_trials
               WHERE search_id = %s AND status = 'pending'
               ORDER BY trial_number ASC LIMIT 1""",
            (search_id,),
        ).fetchone()

        if not trial:
            return {"success": False, "error": "No pending trials remaining"}

        trial_dict = dict(trial)
        params = json.loads(trial_dict["params"])

        # Create training job via training_engine
        from tools.finetune.training_engine import create_training_job

        job_result = create_training_job(
            dataset_id=search["dataset_id"],
            lora_rank=params.get("lora_rank", 16),
            learning_rate=params.get("learning_rate", 2e-4),
            epochs=params.get("epochs", 3),
            batch_size=params.get("batch_size", 2),
            db_path=Path(db_path) if db_path else None,
        )

        now = _now()
        if job_result.get("success"):
            conn.execute(
                """UPDATE ft_hp_trials
                   SET status = 'running', training_job_id = %s, started_at = %s
                   WHERE id = %s""",
                (job_result["job_id"], now, trial_dict["id"]),
            )
        else:
            conn.execute(
                """UPDATE ft_hp_trials
                   SET status = 'failed', started_at = %s, completed_at = %s
                   WHERE id = %s""",
                (now, now, trial_dict["id"]),
            )

        # Mark search as running if still pending
        if search["status"] == "pending":
            conn.execute(
                "UPDATE ft_hp_searches SET status = 'running', updated_at = %s WHERE id = %s",
                (now, search_id),
            )

        conn.commit()

        return {
            "success": job_result.get("success", False),
            "search_id": search_id,
            "trial_id": trial_dict["id"],
            "trial_number": trial_dict["trial_number"],
            "params": params,
            "training_job_id": job_result.get("job_id"),
            "error": job_result.get("error"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def record_trial_result(
    trial_id: str,
    eval_score: float,
    eval_metrics: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Record evaluation results for a completed trial."""
    conn = _get_db(db_path)
    try:
        trial = conn.execute("SELECT * FROM ft_hp_trials WHERE id = %s", (trial_id,)).fetchone()
        if not trial:
            return {"success": False, "error": f"Trial not found: {trial_id}"}

        trial_dict = dict(trial)
        search_id = trial_dict["search_id"]
        now = _now()

        # Update trial
        conn.execute(
            """UPDATE ft_hp_trials
               SET status = 'completed', eval_score = %s, eval_metrics = %s,
                   completed_at = %s
               WHERE id = %s""",
            (eval_score, json.dumps(eval_metrics or {}), now, trial_id),
        )

        # Update search — increment completed, check for new best
        search = conn.execute("SELECT * FROM ft_hp_searches WHERE id = %s", (search_id,)).fetchone()
        search_dict = dict(search)

        new_completed = search_dict["completed_trials"] + 1
        is_new_best = search_dict["best_score"] is None or eval_score > search_dict["best_score"]

        updates = {
            "completed_trials": new_completed,
            "updated_at": now,
        }

        if is_new_best:
            updates["best_trial_id"] = trial_id
            updates["best_score"] = eval_score
            updates["best_params"] = trial_dict["params"]

        # Check if all trials are done
        total_trials = search_dict["max_trials"]
        pending_or_running = conn.execute(
            """SELECT COUNT(*) FROM ft_hp_trials
               WHERE search_id = %s AND status IN ('pending', 'running')""",
            (search_id,),
        ).fetchone()[0]

        # Current trial just completed, so subtract 1 from pending_or_running
        # (the UPDATE above already changed this trial's status)
        if pending_or_running == 0:
            updates["status"] = "completed"

        set_clause = ", ".join(f"{k} = ?" for k in updates)  # nosec B608 — keys are internal constants
        values = list(updates.values()) + [search_id]
        conn.execute(
            f"UPDATE ft_hp_searches SET {set_clause} WHERE id = %s",  # nosec B608
            values,
        )

        conn.commit()

        return {
            "success": True,
            "trial_id": trial_id,
            "search_id": search_id,
            "eval_score": eval_score,
            "is_new_best": is_new_best,
            "completed_trials": new_completed,
            "total_trials": total_trials,
            "search_status": updates.get("status", search_dict["status"]),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_search_status(
    search_id: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return full search status with all trials, best params, progress."""
    conn = _get_db(db_path)
    try:
        search = conn.execute("SELECT * FROM ft_hp_searches WHERE id = %s", (search_id,)).fetchone()
        if not search:
            return {"success": False, "error": f"Search not found: {search_id}"}

        search_dict = dict(search)
        # Parse JSON fields
        search_dict["search_space"] = json.loads(search_dict.get("search_space") or "{}")
        search_dict["best_params"] = json.loads(search_dict.get("best_params") or "{}")

        trials = conn.execute(
            """SELECT * FROM ft_hp_trials
               WHERE search_id = %s
               ORDER BY trial_number ASC""",
            (search_id,),
        ).fetchall()

        trial_list = []
        for t in trials:
            td = dict(t)
            td["params"] = json.loads(td.get("params") or "{}")
            td["eval_metrics"] = json.loads(td.get("eval_metrics") or "{}")
            trial_list.append(td)

        search_dict["trials"] = trial_list
        search_dict["progress_pct"] = round(
            (search_dict["completed_trials"] / max(search_dict["max_trials"], 1)) * 100, 1
        )

        return {"success": True, "search": search_dict}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def list_searches(
    dataset_id: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """List all HP searches, optionally filtered."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM ft_hp_searches WHERE 1=1"
        params: List[Any] = []
        if dataset_id:
            query += " AND dataset_id = ?"
            params.append(dataset_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()
        searches = []
        for r in rows:
            rd = dict(r)
            rd["search_space"] = json.loads(rd.get("search_space") or "{}")
            rd["best_params"] = json.loads(rd.get("best_params") or "{}")
            searches.append(rd)

        return {"success": True, "searches": searches, "count": len(searches)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Hyperparameter search orchestrator")
    parser.add_argument("--create", action="store_true", help="Create new HP search")
    parser.add_argument("--run-next", action="store_true", help="Run next pending trial")
    parser.add_argument("--record", action="store_true", help="Record trial result")
    parser.add_argument("--status", action="store_true", help="Get search status")
    parser.add_argument("--list", action="store_true", help="List searches")

    parser.add_argument("--dataset-id", type=str, default="")
    parser.add_argument("--search-id", type=str, default="")
    parser.add_argument("--trial-id", type=str, default="")
    parser.add_argument("--method", type=str, default=None, help="grid or random")
    parser.add_argument("--max-trials", type=int, default=None)
    parser.add_argument("--score", type=float, default=None, help="Eval score for --record")
    parser.add_argument("--filter-status", type=str, default=None, help="Filter by status")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.create:
        if not args.dataset_id:
            result = {"success": False, "error": "--dataset-id is required for --create"}
        else:
            result = create_search(
                dataset_id=args.dataset_id,
                method=args.method,
                max_trials=args.max_trials,
            )
    elif args.run_next:
        if not args.search_id:
            result = {"success": False, "error": "--search-id is required for --run-next"}
        else:
            result = run_next_trial(search_id=args.search_id)
    elif args.record:
        if not args.trial_id or args.score is None:
            result = {"success": False, "error": "--trial-id and --score are required for --record"}
        else:
            result = record_trial_result(
                trial_id=args.trial_id,
                eval_score=args.score,
            )
    elif args.status:
        if not args.search_id:
            result = {"success": False, "error": "--search-id is required for --status"}
        else:
            result = get_search_status(search_id=args.search_id)
    elif args.list:
        result = list_searches(
            dataset_id=args.dataset_id or None,
            status=args.filter_status,
        )
    else:
        result = {"success": False, "error": "Specify --create, --run-next, --record, --status, or --list"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
