# CUI // SP-CTI
"""Genesis Reflex — the CALLER the ATO evidence stack never had (rmf-inert-02).

THE DEFECT THIS EXISTS FOR
--------------------------
Measured on the live board 2026-09-05, every table an ATO package is assembled
from held ZERO rows: ``rmf_workflow_stages``, ``cato_evidence``,
``project_controls``, ``asset_visibility_snapshots``, ``ssp_documents``,
``poam_items``, ``stig_findings``, ``oscal_artifacts``. Eight for eight.

IT IS NOT A MISSING GENERATOR. ``ssp_generator``, ``poam_generator``,
``stig_checker``, ``oscal_generator`` and ``cato_monitor.collect_evidence`` all
exist on main, and rmf-cyc-01 already wired every one of them to
``rmf_stage_recorder`` so that a stage row is a CONSEQUENCE of an artifact
rather than a step somebody remembers. The chain was complete from the producer
down. NOTHING INVOKED THE PRODUCERS. This is the platform's signature bug --
declared, importable, catalogued, consumed by nobody -- and the fix is always a
caller.

WHY A REFLEX, AND NOT A ROUTE OR A CLI
--------------------------------------
rmf-cyc-01's own diagnosis was that ``rmf_workflow_stages`` was a
hand-maintained board, and hand-maintained boards do not get maintained. A route
requires somebody to click it and a CLI requires somebody to remember it; both
reproduce the exact failure mode that emptied these tables in the first place.
RMF Step 7 (``monitor``) is a CADENCE by definition -- "ongoing assessment" is
what continuous ATO means -- so the producer of monitor evidence has to be
something that runs on its own, or it is not monitoring anything.

IT DOES NOT FABRICATE, AND THE REFUSALS ARE THE DESIGN
------------------------------------------------------
Seeding rows to turn the ATO dashboard green is the exact defect rmf-rail-01 and
rmf-zt-01 were written to remove. So every producer here is gated on a PROBE of
its own input substrate, and a producer whose input is absent is REFUSED with
the substrate NAMED -- never run, never approximated:

  stig_checker     input: the project's ``directory_path`` is a real directory.
                   Then the assessment is a genuine static measurement of real
                   source (9 of the 14 webapp-STIG findings carry auto-checks);
                   the other 5 record ``Not_Reviewed``, which is an UNMEASURED
                   control and not a pass.
  poam_generator   input: ``stig_findings`` rows with status='Open'. A POA&M is
                   a derivation of findings; with none there is nothing to
                   derive, and a zero-item POA&M reads downstream as "no
                   weaknesses" -- the "absence scores as a pass" shape
                   rmf-rail-01 names.
  cato_monitor     input: an artifact file THIS cycle actually produced, on
                   disk. Evidence pointing at a file that does not exist is an
                   assertion with nothing to re-derive.
  ssp_generator    input: ``project_controls``. EMPTY on this deployment, so it
  oscal_generator  is NOT RUN. An SSP over zero control implementations is a
                   document asserting that a security plan exists where none
                   does, and it would stamp ``select`` in_progress on the way
                   past. Reported as ``skipped_no_input`` with the substrate
                   named, on every run, so the gap stays visible.

THE cwd TRAP, WHICH WOULD HAVE MADE THIS SILENTLY WORTHLESS
-----------------------------------------------------------
``run_stig_check`` resolves the project directory as ``Path(directory_path)`` --
CWD-RELATIVE -- and when that is not a directory it sets ``can_auto_check =
False`` and records all 14 findings as ``Not_Reviewed`` WITHOUT FAILING. A
daemon started from anywhere but the repo root would therefore write a complete,
successful-looking assessment that measured nothing, and stamp ``assess`` for
it. So this reflex re-derives the producer's OWN expression before calling it,
and when the path resolves under the repo root but not from the current
directory it REFUSES and says so, naming both paths. An assessment that measured
nothing must never be recorded as an assessment.

A CYCLE THAT PRODUCED NOTHING REPORTS ``unmeasured``, NEVER ``ok``.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 24

# The ONE root resolver (xit-decl-03). `Path(__file__).parents[3]` would be a
# hard-coded claim about where this file sits -- true today, silently wrong the
# moment the kernel packages move.
_CONFIG_PATH = repo_root(__file__) / "args" / "genesis_config.yaml"
_NIST_CATALOG = repo_root(__file__) / "context" / "compliance" / "nist_800_53.json"

#: Which NIST SP 800-53 control an artifact this reflex produced is EVIDENCE OF.
#: Declared here, once, rather than spelled at the call site -- the same reason
#: ``rmf_stage_recorder.ARTIFACT_STAGE`` is a mapping and not five literals.
#:
#: These are the controls' OWN definitions, not an inference: CA-5 is titled
#: "Plan of Action and Milestones", CA-2 "Control Assessments", CM-6
#: "Configuration Settings". Every id is VALIDATED against
#: context/compliance/nist_800_53.json before a row is written -- a control id
#: this deployment's catalogue does not carry is refused rather than recorded,
#: because evidence filed against a control that does not exist is worse than no
#: evidence: it counts toward coverage and can never be reviewed.
#:
#: The webapp STIG template carries NO control field on its findings, so a
#: PER-FINDING control mapping would have to be invented. It is not attempted.
#: What is asserted here is only what the ARTIFACT is -- a STIG checklist is an
#: assessment of configuration settings; a POA&M is a POA&M.
ARTIFACT_CONTROLS: dict[str, tuple[str, ...]] = {
    "stig_assessment": ("CA-2", "CM-6"),
    "poam": ("CA-5",),
}

#: Which cATO evidence type each artifact is, from ``cato_monitor.EVIDENCE_TYPES``.
ARTIFACT_EVIDENCE_TYPE: dict[str, str] = {
    "stig_assessment": "scan_result",
    "poam": "artifact",
}

#: Producers this reflex deliberately does NOT run, and the substrate that would
#: have to hold rows first. Reported on EVERY cycle: a gap nothing names is a
#: gap nobody closes.
DEFERRED_PRODUCERS: dict[str, str] = {
    "ssp_generator": "project_controls",
    "oscal_generator": "project_controls",
}

_FALLBACK_CFG: dict[str, Any] = {
    # Empty means EVERY project registered in `projects` whose directory
    # resolves -- a MEASURED selection, not a guess. Deliberately NOT the
    # asset_discovery "empty = refuse" default: that reflex cannot guess a
    # deployment's address space, while this one reads the deployment's own
    # source tree, which it can always find. Shipping the caller for an inert
    # stack in an inert-by-default state would commit the very defect the card
    # was written to close.
    "projects": [],
    "stig_id": "webapp",
    "max_projects_per_cycle": 5,
    "collect_evidence": True,
    "dry_run": False,
    # WHERE THE ARTIFACTS GO, AND WHY IT IS NOT THE PRODUCERS' DEFAULT.
    # Both producers default their output to ``<directory_path>/compliance/`` --
    # for this deployment's own project that is ``tools/compliance/``, INSIDE
    # THE TRACKED SOURCE TREE. A human running the CLI once leaves two files a
    # reviewer notices; a reflex on a 24-hour cadence writes two more every day
    # into a directory the auto-commit hook sweeps, so the platform would be
    # committing its own compliance reports to git forever. Relative to the repo
    # root, and gitignored.
    "artifact_dir": "data/compliance",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config() -> dict[str, Any]:
    cfg = dict(_FALLBACK_CFG)
    try:
        import yaml

        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        block = (data.get("reflexes") or {}).get("ato_evidence_cycle") or {}
        for key in _FALLBACK_CFG:
            if key in block:
                cfg[key] = block[key]
    except Exception as exc:  # pragma: no cover - config read failure
        logger.warning("[ato_evidence_cycle] config read failed, using defaults: %s", exc)
    return cfg


def known_controls() -> set[str]:
    """Control ids this deployment's NIST 800-53 catalogue actually carries.

    An empty set means the catalogue could not be READ, which is not the same as
    a catalogue holding no controls; callers treat it as "cannot validate" and
    refuse rather than writing unvalidated evidence.
    """
    try:
        with open(_NIST_CATALOG, encoding="utf-8") as fh:
            data = json.load(fh)
        return {c["id"] for c in data.get("controls", []) if c.get("id")}
    except Exception as exc:
        logger.warning("[ato_evidence_cycle] NIST catalogue unreadable: %s", exc)
        return set()


def resolve_project_dir(directory_path: str | None) -> dict[str, Any]:
    """Re-derive the precondition ``run_stig_check`` will itself evaluate.

    Returns ``{"usable", "reason", "cwd_path", "repo_path"}``.

    ``usable`` is True only when ``Path(directory_path).is_dir()`` -- the EXACT
    expression the producer evaluates -- is True. Asking a different question
    here (resolving against the repo root, say) would prove something the
    producer does not check, and the producer would then silently degrade to an
    assessment of nothing. When the path is unusable but DOES exist under the
    repo root, that is its own reason, because the repair is "start the daemon
    from the repo root" and not "the project is misconfigured".
    """
    if not directory_path:
        return {
            "usable": False,
            "reason": "project_has_no_directory_path",
            "cwd_path": None,
            "repo_path": None,
        }
    cwd_candidate = Path(directory_path)
    repo_candidate = repo_root(__file__) / directory_path
    if cwd_candidate.is_dir():
        return {
            "usable": True,
            "reason": None,
            "cwd_path": str(cwd_candidate.resolve()),
            "repo_path": None,
        }
    if repo_candidate.is_dir():
        return {
            "usable": False,
            # NAMED, not swallowed: the producer would otherwise have recorded a
            # complete assessment in which every finding was Not_Reviewed.
            "reason": "project_directory_not_resolvable_from_cwd",
            "cwd_path": str(cwd_candidate),
            "repo_path": str(repo_candidate),
        }
    return {
        "usable": False,
        "reason": "project_directory_absent",
        "cwd_path": str(cwd_candidate),
        "repo_path": None,
    }


def artifact_dir(cfg: dict[str, Any], project_id: str) -> Path:
    """Where this cycle's artifacts are written, resolved against the repo root.

    An ABSOLUTE configured path is honoured as given -- a deployment retaining
    compliance artifacts on a mounted evidence store should not have them
    silently re-rooted under the checkout.
    """
    configured = Path(str(cfg.get("artifact_dir") or _FALLBACK_CFG["artifact_dir"]))
    base = configured if configured.is_absolute() else repo_root(__file__) / configured
    return base / project_id


def _timestamped(out_dir: Path, prefix: str, project_id: str, stamp: str) -> str:
    """One file per cycle, named for the cycle that produced it.

    Never overwritten: a compliance artifact is a dated record of what was true
    when it was assessed, and the cato_evidence row that cites it stores the
    sha256 of THAT file. Overwriting in place would leave every historical
    evidence row pointing at a document whose contents had since changed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"{prefix}_{project_id}_{stamp}.md")


def _count(conn, table: str, project_id: str | None = None) -> int | None:
    """Row count, or None when the table cannot be read.

    None is NEVER 0: a table a migration never created holds an UNKNOWN number
    of rows, and reporting zero for it sends a reader to the writer when the
    repair is a migration.
    """
    try:
        from tools.db.storage import table_exists

        if not table_exists(conn, table):
            return None
        if project_id:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = %s", (project_id,)
            ).fetchone()
        else:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(dict(row)["n"]) if hasattr(row, "keys") else int(row[0])
    except Exception:
        return None


def open_stig_findings(conn, project_id: str) -> int | None:
    """Count findings a POA&M could actually be derived from.

    The same predicate ``poam_generator._get_stig_findings`` uses -- status
    'Open'. Counting every row instead would run the generator against an
    assessment whose findings were all Not_Reviewed and produce an empty POA&M,
    which reads downstream as "assessed, no weaknesses".
    """
    try:
        from tools.db.storage import table_exists

        if not table_exists(conn, "stig_findings"):
            return None
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM stig_findings WHERE project_id = %s AND status = 'Open'",
            (project_id,),
        ).fetchone()
        return int(dict(row)["n"]) if hasattr(row, "keys") else int(row[0])
    except Exception:
        return None


def select_projects(conn, declared: list[str] | None, limit: int) -> list[dict]:
    """Projects this cycle will consider, with their directory verdict attached.

    A DECLARED list is honoured verbatim -- including a project whose directory
    does not resolve, which becomes a NAMED refusal rather than a silent
    omission. An empty list means every registered project, which is a measured
    selection of what the board holds and not a guess about it.
    """
    try:
        if declared:
            marks = ", ".join(["%s"] * len(declared))
            rows = conn.execute(
                f"SELECT id, name, directory_path FROM projects WHERE id IN ({marks}) ORDER BY id",
                tuple(declared),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, directory_path FROM projects ORDER BY id"
            ).fetchall()
    except Exception as exc:
        logger.warning("[ato_evidence_cycle] project read failed: %s", exc)
        return []
    out = []
    for row in rows[: max(0, int(limit))]:
        rec = dict(row)
        rec["dir"] = resolve_project_dir(rec.get("directory_path"))
        out.append(rec)
    return out


def _collect_artifact_evidence(
    project_id: str,
    artifact_kind: str,
    artifact_path: str,
    *,
    db_path: str | None,
    catalogue: set[str],
) -> dict[str, Any]:
    """File a produced artifact as cATO evidence against its declared controls.

    Refuses on three grounds, each its own reason, because each sends a reader
    somewhere different: the file is not on disk (the producer's output moved or
    was never written), the catalogue could not be read (cannot validate), or
    the control id is not in it (the mapping and the catalogue disagree).
    """
    out: dict[str, Any] = {"collected": [], "refusals": []}
    path = Path(artifact_path)
    if not path.is_file():
        out["refusals"].append(
            {
                "producer": "cato_monitor",
                "artifact": artifact_kind,
                "reason": "artifact_file_absent",
                "detail": str(path),
            }
        )
        return out
    if not catalogue:
        out["refusals"].append(
            {
                "producer": "cato_monitor",
                "artifact": artifact_kind,
                "reason": "nist_catalogue_unreadable",
                "detail": str(_NIST_CATALOG),
            }
        )
        return out

    from tools.compliance.cato_monitor import collect_evidence

    evidence_type = ARTIFACT_EVIDENCE_TYPE.get(artifact_kind, "artifact")
    for control_id in ARTIFACT_CONTROLS.get(artifact_kind, ()):
        if control_id not in catalogue:
            out["refusals"].append(
                {
                    "producer": "cato_monitor",
                    "artifact": artifact_kind,
                    "reason": "control_not_in_catalogue",
                    "detail": control_id,
                }
            )
            continue
        try:
            rec = collect_evidence(
                project_id,
                control_id,
                evidence_type,
                f"icdev_{artifact_kind}",
                evidence_path=str(path),
                # The reflex's own cadence, declared rather than assumed: the
                # freshness window this evidence is judged against must be the
                # window it is actually refreshed on, or `is_fresh` describes a
                # schedule nothing runs.
                automation_frequency="daily",
                db_path=db_path,
            )
            out["collected"].append(
                {
                    "control_id": control_id,
                    "evidence_id": rec.get("evidence_id"),
                    "evidence_type": evidence_type,
                    "evidence_hash": rec.get("evidence_hash"),
                    "expires_at": rec.get("expires_at"),
                    "artifact": str(path),
                }
            )
        except Exception as exc:
            out["refusals"].append(
                {
                    "producer": "cato_monitor",
                    "artifact": artifact_kind,
                    "reason": f"error: {exc}",
                    "detail": control_id,
                }
            )
    return out


def _process_project(
    project: dict,
    cfg: dict[str, Any],
    *,
    db_path: str | None,
    catalogue: set[str],
) -> dict[str, Any]:
    """Run the producers whose inputs are present, for ONE project."""
    project_id = project["id"]
    out_dir = artifact_dir(cfg, project_id)
    report: dict[str, Any] = {
        "project_id": project_id,
        "name": project.get("name"),
        "directory": project.get("directory_path"),
        "directory_verdict": project["dir"],
        "artifacts": [],
        "evidence": [],
        "refusals": [],
        "skipped_no_input": [],
    }

    # ── Producers with no input on this deployment, named every run ─────────
    from tools.db.storage import get_connection

    conn = get_connection(db_path=db_path) if db_path else get_connection()
    try:
        controls_rows = _count(conn, "project_controls", project_id)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    for producer, substrate in DEFERRED_PRODUCERS.items():
        if not controls_rows:
            report["skipped_no_input"].append(
                {
                    "producer": producer,
                    "substrate": substrate,
                    # None (unreadable) and 0 (readable, empty) send a reader to
                    # different fixes and are never merged.
                    "substrate_rows": controls_rows,
                    "reason": (
                        "substrate_unreadable" if controls_rows is None else "substrate_empty"
                    ),
                }
            )

    # ── assess: the STIG assessment ─────────────────────────────────────────
    stig_path: str | None = None
    verdict = project["dir"]
    if not verdict["usable"]:
        report["refusals"].append(
            {
                "producer": "stig_checker",
                "reason": verdict["reason"],
                "detail": verdict.get("repo_path") or verdict.get("cwd_path"),
            }
        )
    elif cfg["dry_run"]:
        report["refusals"].append({"producer": "stig_checker", "reason": "dry_run"})
    else:
        try:
            from tools.compliance.stig_checker import run_stig_check

            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            res = run_stig_check(
                project_id,
                stig_id=str(cfg["stig_id"]),
                output_path=_timestamped(
                    out_dir, f"stig_{cfg['stig_id']}", project_id, stamp
                ),
                db_path=db_path,
            )
            stig_path = res.get("output_file")
            summary = res.get("summary") or {}
            assessed = sum(sum(v.values()) for v in summary.values())
            reviewed = sum(
                cnt for v in summary.values() for st, cnt in v.items() if st != "Not_Reviewed"
            )
            report["artifacts"].append(
                {
                    "producer": "stig_checker",
                    "artifact_kind": "stig_assessment",
                    "stage": "assess",
                    "path": stig_path,
                    "findings_assessed": assessed,
                    # The honest denominator: how many of the findings a probe
                    # actually reached. `Not_Reviewed` is UNMEASURED, not a
                    # pass (rmf-zt-01), so it is excluded from `findings_measured`
                    # rather than counted as a clean result.
                    "findings_measured": reviewed,
                    "summary": summary,
                }
            )
        except Exception as exc:
            report["refusals"].append(
                {"producer": "stig_checker", "reason": f"error: {exc}"}
            )

    # ── assess: the POA&M, derived from what the assessment actually found ──
    conn = get_connection(db_path=db_path) if db_path else get_connection()
    try:
        open_findings = open_stig_findings(conn, project_id)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    poam_path: str | None = None
    if open_findings is None:
        report["skipped_no_input"].append(
            {
                "producer": "poam_generator",
                "substrate": "stig_findings",
                "substrate_rows": None,
                "reason": "substrate_unreadable",
            }
        )
    elif open_findings == 0:
        report["skipped_no_input"].append(
            {
                "producer": "poam_generator",
                "substrate": "stig_findings",
                "substrate_rows": 0,
                # A POA&M with no items would be filed as an artifact and read
                # downstream as "assessed, no weaknesses" -- the shape
                # rmf-rail-01 names, where an ABSENCE scores as a pass.
                "reason": "no_open_findings_to_derive_from",
            }
        )
    elif cfg["dry_run"]:
        report["refusals"].append({"producer": "poam_generator", "reason": "dry_run"})
    else:
        try:
            from tools.compliance.poam_generator import generate_poam

            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            poam_path = str(
                generate_poam(
                    project_id,
                    output_path=_timestamped(out_dir, "poam", project_id, stamp),
                    db_path=db_path,
                )
            )
            report["artifacts"].append(
                {
                    "producer": "poam_generator",
                    "artifact_kind": "poam",
                    "stage": "assess",
                    "path": poam_path,
                    "derived_from_open_findings": open_findings,
                }
            )
        except Exception as exc:
            report["refusals"].append(
                {"producer": "poam_generator", "reason": f"error: {exc}"}
            )

    # ── monitor: file what was produced as cATO evidence ────────────────────
    if cfg["collect_evidence"] and not cfg["dry_run"]:
        for kind, path in (("stig_assessment", stig_path), ("poam", poam_path)):
            if not path:
                continue
            res = _collect_artifact_evidence(
                project_id, kind, path, db_path=db_path, catalogue=catalogue
            )
            report["evidence"].extend(res["collected"])
            report["refusals"].extend(res["refusals"])

    return report


def _finalise(result: dict[str, Any]) -> dict[str, Any]:
    """Set the verdict, and refuse to call a cycle that produced nothing ``ok``."""
    if result["status"] == "error":
        return result
    produced = int(result["artifacts_produced"] or 0)
    if produced == 0:
        # UNMEASURABLE is never folded into a clean report. A cycle that
        # refused everything looks identical to a healthy one unless it says so,
        # and that is how a capability stays dead behind a green dashboard.
        result["status"] = "unmeasured"
    result["metric_value"] = float(produced)
    return result


def run(ctx: dict[str, Any], trust: Any = None) -> dict[str, Any]:
    """Take every eligible project through the RMF stages its inputs support.

    The daemon dispatches reflexes as ``fn(config, trust)`` -- the second
    positional argument is the TrustKernel, NOT a DB connection.

    ctx keys (each overrides the same key under ``reflexes.ato_evidence_cycle``
    in args/genesis_config.yaml):
        projects                project ids. Empty => every registered project.
        stig_id                 STIG template id (default 'webapp').
        max_projects_per_cycle  bound on how many projects one cycle touches.
        collect_evidence        file produced artifacts as cATO evidence.
        dry_run                 probe and report; run no producer, write nothing.
        db_path                 database override (tests).
    """
    cfg = _load_config()
    for key in _FALLBACK_CFG:
        if key in ctx:
            cfg[key] = ctx[key]
    cfg["dry_run"] = bool(cfg.get("dry_run", False))
    cfg["collect_evidence"] = bool(cfg.get("collect_evidence", True))
    db_path = ctx.get("db_path")

    result: dict[str, Any] = {
        # THE DAEMON'S CONTRACT, and it is easy to get silently wrong: a reflex
        # returning no `success` key is scored a FAILURE on every cycle forever
        # (tools/daemon/base.py::classify_failure). `success` means "this cycle
        # completed" and is set False only by the error path -- a refusal and an
        # `unmeasured` verdict are the reflex WORKING, and scoring them as
        # failures would put a correctly-behaving reflex into the circuit
        # breaker on a deployment that has simply not registered a project yet.
        "success": True,
        "metric_value": 0.0,
        "cadence_hours": CADENCE_HOURS,
        "status": "ok",
        "dry_run": cfg["dry_run"],
        "projects_considered": 0,
        "projects_processed": 0,
        "artifacts_produced": 0,
        "evidence_collected": 0,
        "projects": [],
        # Every refusal is NAMED. A reflex that declines to act and reports
        # nothing about why is indistinguishable from one that is not running.
        "refusals": [],
        "skipped_no_input": [],
        "substrate_before": {},
        "substrate_after": {},
        "errors": [],
        "ran_at": _now(),
    }

    tables = (
        "rmf_workflow_stages",
        "stig_findings",
        "poam_items",
        "cato_evidence",
        "project_controls",
        "ssp_documents",
        "oscal_artifacts",
    )

    try:
        from tools.db.storage import get_connection

        conn = get_connection(db_path=db_path) if db_path else get_connection()
        try:
            result["substrate_before"] = {t: _count(conn, t) for t in tables}
            projects = select_projects(
                conn,
                list(cfg.get("projects") or []),
                int(cfg.get("max_projects_per_cycle") or 5),
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

        result["projects_considered"] = len(projects)
        if not projects:
            result["refusals"].append(
                {"producer": None, "reason": "no_projects_registered"}
            )

        catalogue = known_controls()
        for project in projects:
            report = _process_project(project, cfg, db_path=db_path, catalogue=catalogue)
            result["projects"].append(report)
            result["refusals"].extend(report["refusals"])
            result["skipped_no_input"].extend(report["skipped_no_input"])
            result["artifacts_produced"] += len(report["artifacts"])
            result["evidence_collected"] += len(report["evidence"])
            if report["artifacts"]:
                result["projects_processed"] += 1

        conn = get_connection(db_path=db_path) if db_path else get_connection()
        try:
            result["substrate_after"] = {t: _count(conn, t) for t in tables}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    except Exception as exc:
        logger.error("[ato_evidence_cycle] cycle failed: %s", exc)
        result["status"] = "error"
        result["success"] = False
        result["errors"].append(str(exc))

    return _finalise(result)


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as
    # the GenesisDaemon. override=True: a pip-installed ICDEV in site-packages
    # may already have loaded a different checkout's .env at import. Repo root
    # via __file__, not cwd.
    import argparse

    try:
        from dotenv import load_dotenv as _load_dotenv

        _load_dotenv(repo_root(__file__) / ".env", override=True)
    except ImportError:
        pass

    _parser = argparse.ArgumentParser(
        description="Run one ATO evidence cycle (rmf-inert-02)"
    )
    _parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Probe every precondition and report; run no producer, write nothing",
    )
    _parser.add_argument("--project", action="append", dest="projects")
    _parser.add_argument("--db", dest="db_path")
    _args = _parser.parse_args()

    _ctx: dict[str, Any] = {}
    if _args.dry_run:
        _ctx["dry_run"] = True
    if _args.projects:
        _ctx["projects"] = _args.projects
    if _args.db_path:
        _ctx["db_path"] = _args.db_path
    print(json.dumps(run(_ctx), indent=2, default=str))
