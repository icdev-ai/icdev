# CUI // SP-CTI
"""Scorecards against adopted standards, delivery evidence, and a conformance bundle per repo (icx-coe-03).

The third card of the Engineering CoE programme. icx-coe-01 gave ICDEV a standards register with a lifecycle
(``tools/idp/standards.py``), icx-coe-02 exemptions that expire and ADRs as records (``tools/idp/exemptions.py``,
``tools/idp/adrs.py``). This module scores them, per repo, and hands ICDEV[OS] one bundle per repo
(``icos-conformance/v1``, schema ``context/icos/conformance.schema.json``) that its enterprise twin imports
(osx-coe-01). It REUSES that machinery and adds none of its own:

**Scorecards against ADOPTED standards** (:func:`standard_results`). For each repo declared in
``args/coe_conformance.yaml``, every standard in the register is listed with its lifecycle state, and only a
standard IN FORCE that APPLIES to the repo is scored:

* a ``scorecard_rule`` check is scored by machine: the existing IDP scorecard (``tools/idp/scorecard.py``) is
  evaluated with the live exemption log (icx-coe-02) applied, and the rule passes only when every service it
  applies to passes it or is waived by a live exemption. An EXPIRED exemption FAILS it, named;
* a ``manual_evidence`` check is ``needs_evidence``: a person reads the named evidence. It is never passed;
* a standard not in force (proposed, reviewed, refused, returned, deprecated) is ``not_scored``, only listed;
* a standard in force that the repo's declaration does not name is ``not_applicable``;
* a check that cannot be evaluated (a rule that applies to no service, a scorecard that will not load) is
  ``unverified``, never passed.

**Delivery evidence** (``tools/idp/dora.py``): the DORA four keys and AI-assisted rework from the repo's
declared sources, each metric with its window, sample size and source digests; no data is unverified.

**The bundle** (:func:`build`): standards and their status, the scorecard results with their evidence, the
exemptions that touch the repo (with expiry), the delivery metrics, the ADRs linked to the repo's standards,
the producer, version and time. ``content_digest`` is SHA-256 over the canonical JSON of the bundle without
``content_digest``, ``publication`` and ``produced_at`` (:func:`content_digest`): ICDEV[OS] recomputes it.
Validated against the draft schema (pinned by SHA-256 here) before it is returned.

**Publishing is a person's decision** (``decl-wft-coe-conformance-publish``). A bundle leaving the factory is
evidence ICDEV[OS] will judge the CoE by, so someone attests it: anyone (or an agent) PREPARES a bundle, which
records it, at its digest, in the append-only, hash-chained ``coe_conformance_events``; a person who did not
prepare it PUBLISHES it or refuses, with a reason, bound to that digest. Only then does the bundle carry its
``publication`` (who, the step, when, the digest), the way an apptwin bundle carries an IV&V only when an
independent person signed it at its digest (``tools/modernization/survivor_ivv.py``). Nothing publishes itself.

CLI: ``tools/idp/conformance_export.py``.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess  # nosec B404 — git show, fixed arguments, never a shell
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

import yaml  # noqa: E402

from icdev.core.paths import repo_root  # noqa: E402
from tools.idp import dora  # noqa: E402

BASE_DIR = repo_root(__file__)
CONFIG_RELPATH = "args/coe_conformance.yaml"
SCHEMA = "icos-conformance/v1"
SCHEMA_RELPATH = "context/icos/conformance.schema.json"
#: The digest of the DRAFT schema (to be adopted into the ICOS contract by osx-coe-01, then re-pinned). A
#: changed file is refused, never silently used.
SCHEMA_SHA256 = "7776e0252f5fef90519cde392f2214689686603e8410c7f3cad886a9804e9762"
PRODUCER = "icdev-it/conformance_export"
CLASSIFICATION = "CUI // SP-CTI"
TABLE = "coe_conformance_events"
GENESIS = "0" * 64

TEMPLATE = "decl-wft-coe-conformance-publish"
SURFACE = "idp.idp_api_conformance_publish"

EVENTS = ("prepared", "published", "refused")
ACTOR_KINDS = ("person", "agent", "bootstrap")
STATUSES = ("pass", "fail", "needs_evidence", "unverified", "not_scored", "not_applicable")

_COLS = ("event_id", "repo", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
         "content_digest", "bundle_json", "detail_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in _COLS if c != "row_hash")


class ConformanceRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


canonical = dora.canonical
digest = dora.digest


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _as_of(value: Any) -> str:
    """An as-of date (YYYY-MM-DD), today when none is given; anything else is refused, never guessed at."""
    text = _text(value) or _today()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$", text)
    try:
        if m is None:
            raise ValueError(text)
        return date.fromisoformat(m.group(1)).isoformat()
    except ValueError:
        raise ConformanceRefused(400, "as_of", f"as_of must be a date, YYYY-MM-DD, not {text!r}") from None


# ── configuration ────────────────────────────────────────────────────────────


def load_config(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else BASE_DIR / CONFIG_RELPATH
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg.get("repos"), dict) or not cfg["repos"]:
        raise ValueError(f"{CONFIG_RELPATH} declares no repos")
    raw = p.read_bytes()
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    return cfg


def repo_config(repo: str, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    key = _text(repo).lower()
    if key not in cfg["repos"]:
        raise ConformanceRefused(404, "unknown_repo",
                                 f"no repo {repo!r}: one of {', '.join(sorted(cfg['repos']))}")
    return {"key": key, **cfg["repos"][key]}


def applies(standard_id: str, patterns: Iterable[str]) -> bool:
    """Does a repo's declaration (ids, or prefixes ending in ``*``) name this standard?"""
    return any(fnmatch.fnmatchcase(standard_id, str(p)) for p in patterns or ())


def sources_dir(cfg: dict) -> Path:
    env = _text(os.environ.get("ICDEV_COE_SOURCES_DIR"))
    p = Path(env or cfg.get("sources_dir") or "data/coe/sources").expanduser()
    return p if p.is_absolute() else BASE_DIR / p


def _live_git_allowed() -> bool:
    return _text(os.environ.get("ICDEV_COE_LIVE_GIT") or "1") not in ("0", "false", "no")


def git_path(rc: dict) -> Path:
    g = rc.get("git") or {}
    env = _text(os.environ.get(_text(g.get("path_env")))) if g.get("path_env") else ""
    p = Path(env or g.get("path") or ".").expanduser()
    return p if p.is_absolute() else BASE_DIR / p


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_digests(path: Path, ref: str, files: Iterable[str]) -> dict:
    """sha256 of each file as committed at ``ref`` (``git show``), or None when it is not there."""
    out = {}
    for f in files or ():
        try:
            raw = subprocess.run(["git", "-C", str(path), "show", f"{ref}:{f}"],  # nosec B603 B607
                                 capture_output=True, timeout=60, check=False)
            out[f] = hashlib.sha256(raw.stdout).hexdigest() if raw.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            out[f] = None
    return out


def read_sources(repo: str, as_of: str, *, cfg: Optional[dict] = None) -> dict:
    """The repo's three delivery sources for the window ending ``as_of``: ``{git, ci, board, missing, read}``.

    A snapshot under the sources directory wins (``<repo>.git.json``, ``<repo>.ci.json``,
    ``<repo>.board.json``); git is otherwise read live from the declared checkout. CI and the board are never
    read live. A source that cannot be read is missing, with why.
    """
    cfg = cfg or load_config()
    rc = repo_config(repo, cfg)
    defs = dora.definitions(cfg.get("delivery") or {})
    win = dora.window(as_of, defs["window_days"])
    d = sources_dir(cfg)
    out: dict[str, Any] = {"git": None, "ci": None, "board": None, "missing": {}, "read": {}}
    snap = d / f"{rc['key']}.git.json"
    if snap.is_file():
        try:
            out["git"] = dora.check_git(_load_json(snap))
            out["read"]["git"] = "snapshot"
        except (ValueError, dora.SourceError) as exc:
            out["missing"]["git"] = f"the git snapshot {snap.name} is unreadable: {exc}"
    elif _live_git_allowed():
        path = git_path(rc)
        ref = _text((rc.get("git") or {}).get("ref")) or "origin/main"
        try:
            g = dora.read_git(path, ref, since=win["start"], until=win["end"], defs=defs, repo=rc["key"])
            if rc.get("pinned_files"):
                g["files"] = _file_digests(path, g["head"], rc["pinned_files"])
            out["git"] = g
            out["read"]["git"] = "live"
        except dora.SourceError as exc:
            out["missing"]["git"] = f"{rc['name']} is not readable here ({exc}); set {(rc.get('git') or {}).get('path_env')}"
    else:
        out["missing"]["git"] = f"no git snapshot {snap.name}, and live git reads are off (ICDEV_COE_LIVE_GIT=0)"
    for kind, norm in (("ci", lambda doc: dora.normalise_ci(doc, repo=rc["key"],
                                                            workflow=_text(rc.get("ci_workflow")))),
                       ("board", dora.normalise_board)):
        f = d / f"{rc['key']}.{kind}.json"
        if not f.is_file():
            out["missing"][kind] = f"no {kind} snapshot {f.name} under {d.name}/ (passed in, never fetched live)"
            continue
        try:
            out[kind] = norm(_load_json(f))
            out["read"][kind] = "snapshot"
        except (ValueError, dora.SourceError) as exc:
            out["missing"][kind] = f"the {kind} snapshot {f.name} is unreadable: {exc}"
    return out


# ── scorecards against adopted standards ─────────────────────────────────────


def _check_of(entry: dict) -> dict:
    chk = entry.get("check") or {}
    if "scorecard_rule" in chk:
        r = chk["scorecard_rule"] or {}
        return {"kind": "scorecard_rule", "scorecard": _text(r.get("scorecard")), "rule": _text(r.get("rule"))}
    if "manual_evidence" in chk:
        m = chk["manual_evidence"] or {}
        return {"kind": "manual_evidence", "name": _text(m.get("name")),
                "procedure": _text(m.get("procedure")),
                **({"produced_by": _text(m.get("produced_by"))} if m.get("produced_by") else {})}
    return {"kind": "none"}


def _evaluate(scorecard_key: str, as_of: str, cache: dict) -> dict:
    """One IDP scorecard, graded as the portal grades it: the live collection, the platform's exemption log."""
    if scorecard_key not in cache:
        from tools.db.storage import get_connection
        from tools.idp import scorecard

        conn = get_connection()
        try:
            cache[scorecard_key] = scorecard.evaluate(scorecard.load_scorecard(scorecard_key), conn=None,
                                                      today=as_of, exemptions_conn=conn)
        finally:
            conn.close()
    return cache[scorecard_key]


def _rule_result(std_id: str, chk: dict, report: dict) -> dict:
    """How one scorecard rule landed across every service it applies to."""
    rule = chk["rule"]
    applicable, passing, failing, exempt, expired = [], [], [], [], []
    for res in report.get("results") or []:
        for o in res.get("rules") or res.get("outcomes") or []:
            if o.get("identifier") != rule:
                continue
            st = o.get("status")
            if st == "not_applicable":
                continue
            applicable.append(res["entity"])
            ex = o.get("exemption") or {}
            if st == "pass":
                passing.append(res["entity"])
            elif st == "exempt":
                exempt.append({"component": res["entity"], "exemption_key": ex.get("exemption_key") or "",
                               "expires_on": ex.get("expires") or ex.get("expires_on") or ""})
            else:
                failing.append(res["entity"])
                if ex.get("expired"):
                    expired.append({"component": res["entity"], "exemption_key": ex.get("exemption_key") or "",
                                    "expires_on": ex.get("expires") or ex.get("expires_on") or ""})
    seen = {e["exemption_key"] for e in expired}
    for ex in (report.get("exemptions") or {}).get("expired") or []:
        if ex.get("identifier") in (rule, "*") and (ex.get("exemption_key") or "") not in seen:
            expired.append({"component": ex.get("entity") or "", "exemption_key": ex.get("exemption_key") or "",
                            "expires_on": ex.get("expires") or ""})
    rr = next((r for r in report.get("rules") or [] if r.get("identifier") == rule), {})
    if not applicable:
        status, why = "unverified", f"rule {rule} applies to no service: nothing was measured"
    elif expired:
        status = "fail"
        why = "an exemption expired: " + ", ".join(
            f"{e['exemption_key'] or e['component']} on {e['expires_on']}" for e in expired[:3])
    elif failing:
        status, why = "fail", f"{len(failing)} of {len(applicable)} services fail {rule}"
    elif not passing:
        status, why = "unverified", f"every service is exempt from {rule}: none passed by machine"
    else:
        status = "pass"
        why = f"{len(passing)} of {len(applicable)} services pass {rule} by machine" + (
            f", {len(exempt)} exempt under a live exemption" if exempt else "")
    return {
        "standard_id": std_id, "scorecard": chk["scorecard"], "rule": rule, "status": status, "why": why,
        "services": {"applicable": len(applicable), "passing": len(passing), "failing": len(failing),
                     "exempt": len(exempt), "expired": len(expired)},
        "failing": sorted(failing), "exempt": sorted(exempt, key=lambda e: e["component"]),
        "expired": sorted(expired, key=lambda e: (e["component"], e["exemption_key"])),
        "evidence": {"collection": report.get("collection"), "adapter_module": report.get("adapter_module"),
                     "expression": rr.get("expression") or "", "source_path": report.get("source_path"),
                     "evaluated_on": report.get("evaluated_on"),
                     "exemption_gate": (report.get("exemptions") or {}).get("gate")},
    }


def standard_results(repo: str, as_of: str, *, cfg: Optional[dict] = None,
                     register: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
    """``(standards, scorecards)`` for one repo: every standard listed, only adopted ones that apply scored."""
    from tools.idp import standards

    cfg = cfg or load_config()
    rc = repo_config(repo, cfg)
    view = register if register is not None else standards.register_view()
    cache: dict = {}
    listed, scored = [], []
    for s in view.get("standards") or []:
        try:
            entry = standards.standard_view(s["id"])["entry"]
        except standards.StandardRefused:
            entry = {}
        chk = _check_of(entry)
        row = {"id": s["id"], "title": s.get("title") or "", "owner": s.get("owner") or "",
               "state": s.get("state") or "", "version": s.get("in_force") or s.get("latest_version") or "",
               "in_force": bool(s.get("in_force")), "adopted_by_kind": s.get("adopted_kind") or None,
               "applies": applies(s["id"], rc.get("standards") or []), "check": chk,
               "href": f"/idp/standards#{s['id']}"}
        if not row["in_force"]:
            row.update(status="not_scored", why=f"{row['state'] or 'not recorded'}: not adopted, so listed, not scored")
        elif not row["applies"]:
            row.update(status="not_applicable", why=f"not declared for {rc['name']} ({CONFIG_RELPATH})")
        elif not (s.get("check") or {}).get("resolved", True):
            row.update(status="unverified", why=f"its check no longer resolves: {(s.get('check') or {}).get('why', '')}")
        elif chk["kind"] == "manual_evidence":
            row.update(status="needs_evidence", why=f"a person reads the evidence: {chk['name']}")
        elif chk["kind"] == "scorecard_rule":
            try:
                res = _rule_result(s["id"], chk, _evaluate(chk["scorecard"], as_of, cache))
            except Exception as exc:  # noqa: BLE001 — a scorecard that will not grade is unverified, never passed
                res = {"standard_id": s["id"], "scorecard": chk["scorecard"], "rule": chk["rule"],
                       "status": "unverified", "why": f"the scorecard could not be graded: {exc}",
                       "services": {"applicable": 0, "passing": 0, "failing": 0, "exempt": 0, "expired": 0},
                       "failing": [], "exempt": [], "expired": [], "evidence": {}}
            scored.append(res)
            row.update(status=res["status"], why=res["why"])
        else:
            row.update(status="unverified", why="it names no check")
        listed.append(row)
    return listed, scored


def exemption_rows(repo: str, as_of: str, scorecards: Iterable[str], standard_ids: Iterable[str],
                   *, cfg: Optional[dict] = None) -> list[dict]:
    """The exemptions that touch this repo, each with its expiry state as of ``as_of``."""
    from tools.idp import exemptions

    cfg = cfg or load_config()
    rc = repo_config(repo, cfg)
    cards, stds = set(scorecards), set(standard_ids)
    view = exemptions.exemptions_view(today=as_of)
    out = []
    for x in view.get("exemptions") or []:
        mine = (x.get("scorecard") in cards and x.get("scope") != "repo") or (
            x.get("scope") == "repo" and x.get("scope_ref") == rc.get("exemption_repo")) or bool(
            stds & set(x.get("standard_ids") or []))
        if not mine:
            continue
        out.append({k: x.get(k) for k in ("exemption_key", "ref", "scorecard", "rule", "component", "scope",
                                           "scope_ref", "standard_ids", "state", "expires_on", "requested_by",
                                           "approved_by")} | {"href": "/idp/exemptions"})
    return sorted(out, key=lambda e: e["exemption_key"])


def adr_summary(standard_ids: Iterable[str]) -> dict:
    """The ADRs linked to the repo's applicable standards, and every ADR's status count."""
    from tools.idp import adrs

    stds = set(standard_ids)
    view = adrs.adrs_view()
    linked = [{"id": a["id"], "title": a["title"], "status": a["status"],
               "standards": sorted(set(a.get("standards") or []) & stds), "href": f"/idp/adrs#{a['id']}"}
              for a in view.get("adrs") or [] if stds & set(a.get("standards") or [])]
    return {"total": view.get("total", 0), "by_status": dict(sorted((view.get("counts") or {}).items())),
            "linked": linked, "href": "/idp/adrs"}


def pinned_files(rc: dict, git: Optional[dict], register: dict) -> list[dict]:
    """For a repo whose files standards pin by sha256 (ICDEV[OS]'s args/): each file's digest at the ref read."""
    from tools.idp import standards

    want = list(rc.get("pinned_files") or [])
    if not want:
        return []
    pins: dict[str, list[dict]] = {f: [] for f in want}
    for s in register.get("standards") or []:
        try:
            entry = standards.standard_view(s["id"])["entry"]
        except standards.StandardRefused:
            continue
        for src in entry.get("sources") or []:
            if isinstance(src, dict) and src.get("path") in pins and src.get("repo"):
                pins[src["path"]].append({"standard_id": s["id"], "commit": src.get("commit"),
                                          "sha256": src.get("sha256")})
    files = (git or {}).get("files") or {}
    out = []
    for f in want:
        now = files.get(f)
        out.append({"path": f, "sha256": now, "pins": pins[f],
                    "drifted": bool(now) and any(p["sha256"] != now for p in pins[f]),
                    **({"why": "not read: no git history of the repo here"} if now is None else {})})
    return out


# ── the bundle ───────────────────────────────────────────────────────────────


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("icdev")
    except Exception:  # noqa: BLE001 — an unpackaged checkout still says what it is
        return "unpackaged"


def content_digest(bundle: dict) -> str:
    """SHA-256 over the canonical JSON (keys sorted, no whitespace) of the bundle WITHOUT ``content_digest``,
    ``publication`` and ``produced_at``: what was assessed, not when the file was written or who published it.
    ICDEV[OS] recomputes it."""
    return digest({k: v for k, v in bundle.items() if k not in ("content_digest", "publication", "produced_at")})


def schema() -> dict:
    raw = (BASE_DIR / SCHEMA_RELPATH).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != SCHEMA_SHA256:
        raise ConformanceRefused(500, "schema", f"{SCHEMA_RELPATH} is not the pinned draft (sha256 {got}, "
                                                f"pinned {SCHEMA_SHA256})")
    return json.loads(raw)


def validate(bundle: dict) -> list[str]:
    """The bundle's errors against the draft schema, then its own digest. Empty: ICDEV[OS] would take it."""
    import jsonschema

    v = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())
    errors = sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
                    for e in v.iter_errors(bundle))
    if not errors and bundle.get("content_digest") != content_digest(bundle):
        errors.append("content_digest is not the digest of the bundle's content")
    pub = bundle.get("publication")
    if not errors and pub and pub.get("content_digest") != bundle["content_digest"]:
        errors.append("the publication was taken at another digest")
    return errors


def _summary(stds: list[dict], metrics: dict) -> dict:
    by: dict[str, int] = {s: 0 for s in STATUSES}
    for s in stds:
        by[s["status"]] = by.get(s["status"], 0) + 1
    return {"standards": by, "metrics_measured": sum(1 for m in metrics.values() if m["status"] == "measured"),
            "metrics_unverified": sorted(k for k, m in metrics.items() if m["status"] != "measured")}


def build(repo: str, *, as_of: Optional[str] = None, at: Optional[str] = None, cfg: Optional[dict] = None,
          sources: Optional[dict] = None, check: bool = True) -> dict:
    """One repo's ``icos-conformance/v1`` bundle as of ``as_of`` (YYYY-MM-DD), unpublished.

    Deterministic given its inputs: the register and exemption log, the repo tree's scorecard facts, the
    delivery sources and ``as_of``. ``at`` is ``produced_at`` (outside the digest).
    """
    from tools.idp import standards

    cfg = cfg or load_config()
    rc = repo_config(repo, cfg)
    as_of = _as_of(as_of)
    src = sources if sources is not None else read_sources(rc["key"], as_of, cfg=cfg)
    register = standards.register_view()
    stds, cards = standard_results(rc["key"], as_of, cfg=cfg, register=register)
    applicable = [s["id"] for s in stds if s["applies"]]
    defs = dora.definitions(cfg.get("delivery") or {})
    delivery = dora.compute(git=src.get("git"), ci=src.get("ci"), board=src.get("board"), as_of=as_of,
                            defs=defs, missing=src.get("missing"))
    git = src.get("git") or {}
    bundle = {
        "schema": SCHEMA,
        "producer": {"tool": PRODUCER, "version": _version()},
        "produced_at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "classification": CLASSIFICATION,
        "repo": {"key": rc["key"], "name": rc["name"], "remote": rc.get("remote") or "",
                 "ref": git.get("ref") or (rc.get("git") or {}).get("ref") or "", "head": git.get("head"),
                 "read": dict(sorted((src.get("read") or {}).items()))},
        "as_of": as_of,
        "config": {"path": CONFIG_RELPATH, "sha256": cfg.get("_sha256")},
        "register": {"path": standards.REGISTER_RELPATH, "sha256": (register.get("register") or {}).get("sha256"),
                     "chain_intact": bool((register.get("chain") or {}).get("intact")),
                     "ledger_rows": (register.get("chain") or {}).get("rows")},
        "standards": stds,
        "scorecards": cards,
        "exemptions": exemption_rows(rc["key"], as_of, {c["scorecard"] for c in cards}, applicable, cfg=cfg),
        "delivery": delivery,
        "adrs": adr_summary(applicable),
        "pinned_files": pinned_files(rc, src.get("git"), register),
        "summary": _summary(stds, delivery["metrics"]),
        "publication": None,
    }
    bundle["content_digest"] = content_digest(bundle)
    if check:
        errors = validate(bundle)
        if errors:
            raise ConformanceRefused(500, "contract", "the bundle does not meet the icos-conformance/v1 draft: "
                                                      + "; ".join(errors[:10]))
    return bundle


# ── the ledger: prepared, then a person publishes ────────────────────────────


def _connection():
    """The platform database without the row-security predicate (the ledger has no label column: it is the
    platform's own engineering governance, as coe_standard_events is)."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def read_events(conn=None, repo: Optional[str] = None) -> list[dict]:
    own = conn is None
    conn = conn or _connection()
    try:
        sql = f"SELECT {', '.join(_COLS)} FROM {TABLE}"  # nosec B608 — fixed table and columns
        params: tuple = ()
        if repo:
            sql += " WHERE repo = %s"
            params = (repo,)
        rows = conn.execute(sql + " ORDER BY created_at, event_id", params).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Each repo's rows in hash-chain order, plus ``_problems`` (a row that does not verify or is off its chain)."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["repo"]), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for repo, items in sorted(by.items()):
        nxt = {str(r["prev_hash"]): r for r in items}
        ordered, prev = [], GENESIS
        while prev in nxt:
            r = nxt.pop(prev)
            ordered.append(r)
            prev = str(r["row_hash"])
        for r in items:
            if _row_hash(r) != r["row_hash"]:
                out["_problems"].append({"event_id": r["event_id"], "repo": repo,
                                         "problem": "row hash does not match its content"})
            elif r["event"] == "prepared":
                try:
                    if content_digest(json.loads(r["bundle_json"])) != r["content_digest"]:
                        raise ValueError
                except (ValueError, TypeError):
                    out["_problems"].append({"event_id": r["event_id"], "repo": repo,
                                             "problem": "the bundle does not match its digest"})
        for r in nxt.values():
            out["_problems"].append({"event_id": r["event_id"], "repo": repo,
                                     "problem": "not on the repo's hash chain"})
        out[repo] = ordered
    return out


def _detail(row: dict) -> dict:
    try:
        return json.loads(row.get("detail_json") or "{}")
    except ValueError:
        return {}


def replay(ordered: list[dict]) -> dict:
    """One repo's ledger state: the latest prepared bundle, whether a person decided it, the latest published."""
    prepared = published = None
    history = []
    for r in ordered:
        det = _detail(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")} | {"detail": det}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None}
        elif prepared and det.get("prepared_event") == prepared["event_id"]:
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"]}
    return {"prepared": prepared, "published": published, "history": history}


def _state(repo: str, conn) -> tuple[dict, list[dict], list[dict]]:
    rows = read_events(conn, repo)
    ch = chain(rows)
    return replay(ch.get(repo, [])), ch["_problems"], ch.get(repo, [])


def _head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def _append(conn, *, repo: str, event: str, actor: str, actor_kind: str, content: str, prev_hash: str,
            bundle: Optional[dict] = None, actor_role: str = "", step_id: str = "", reason: str = "",
            detail: Optional[dict] = None) -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    row = {"event_id": f"coec-{uuid.uuid4().hex[:16]}", "repo": repo, "event": event, "actor": actor,
           "actor_kind": actor_kind, "actor_role": actor_role or "", "step_id": step_id or "",
           "reason": reason or "", "content_digest": content,
           "bundle_json": canonical(bundle) if bundle is not None else "",
           "detail_json": canonical(detail or {}), "prev_hash": prev_hash, "created_at": _now()}
    row["row_hash"] = _row_hash(row)
    try:
        conn.execute(f"INSERT INTO {TABLE} ({', '.join(_COLS)}) VALUES ({', '.join(['%s'] * len(_COLS))})",
                     tuple(row[c] for c in _COLS))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        except Exception:  # noqa: BLE001
            pass
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise ConformanceRefused(409, "concurrent", f"{repo}'s bundle changed while you were deciding: reload it") from exc
        raise ConformanceRefused(503, "store_unavailable",
                                 f"the conformance ledger could not be written, so nothing changed ({exc})") from exc
    return row


def _actor_kind(actor: dict) -> str:
    if _text(actor.get("id")).startswith("bootstrap:"):
        return "bootstrap"
    if actor.get("role") == "service" or _text(actor.get("id")).startswith("agent:"):
        return "agent"
    return "person"


def prepare(repo: str, actor: Optional[dict], *, as_of: Optional[str] = None, note: str = "", conn=None,
            bundle: Optional[dict] = None) -> dict:
    """Build the repo's bundle and record it, at its digest, as PREPARED. Decides nothing.

    Anyone signed in, or an agent (recorded as one). A person who did not prepare it publishes it or refuses.
    """
    if not isinstance(actor, dict) or not actor.get("id"):
        raise ConformanceRefused(401, "no_actor", "preparing a bundle needs a signed-in person or a named agent")
    rc = repo_config(repo)
    b = bundle if bundle is not None else build(rc["key"], as_of=as_of)
    own = conn is None
    conn = conn or _connection()
    try:
        _, problems, ordered = _state(rc["key"], conn)
        if problems:
            raise ConformanceRefused(409, "chain_broken", f"{rc['key']}'s ledger does not verify: {problems[0]['problem']}")
        row = _append(conn, repo=rc["key"], event="prepared", actor=_text(actor["id"]),
                      actor_kind=_actor_kind(actor), actor_role=_text(actor.get("role")),
                      content=b["content_digest"], bundle=b, reason=note,
                      detail={"as_of": b["as_of"], "summary": b["summary"]}, prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    return {"repo": rc["key"], "event_id": row["event_id"], "content_digest": b["content_digest"],
            "prepared_by": row["actor"], "actor_kind": row["actor_kind"], "as_of": b["as_of"],
            "summary": b["summary"], "template": TEMPLATE,
            "next": "a person who did not prepare it publishes it or refuses, on /idp/conformance"}


def builders(state: dict) -> dict[str, str]:
    """Who may not publish the prepared bundle (``separate_from: builders``): whoever prepared it."""
    p = state.get("prepared") or {}
    who = _text(p.get("actor"))
    return {who: "prepared this bundle"} if who else {}


def publish(repo: str, *, decision: str, reason: str, read_digest: str, actor: Optional[dict], conn=None) -> dict:
    """A person who did not prepare it publishes the repo's prepared bundle to ICDEV[OS], or refuses it.

    The person's step of ``decl-wft-coe-conformance-publish``, with a reason, bound to the digest of the
    prepared bundle they read. A published bundle carries its ``publication``; a refused one never leaves.
    """
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise ConformanceRefused(401, "no_person", "publishing is a signed-in person's declared step on /idp/conformance")
    rc = repo_config(repo)
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _state(rc["key"], conn)
        if problems:
            raise ConformanceRefused(409, "chain_broken", f"{rc['key']}'s ledger does not verify: {problems[0]['problem']}")
        p = st["prepared"]
        if p is None:
            raise ConformanceRefused(409, "nothing_prepared", f"no bundle of {rc['key']} is prepared: prepare one first")
        if p.get("decided"):
            raise ConformanceRefused(409, "already_decided",
                                     f"the prepared bundle {p['content_digest'][:12]} was already {p['decided']['event']}: prepare a new one")
        if _text(read_digest) != p["content_digest"]:
            raise ConformanceRefused(409, "digest", f"the prepared bundle is {p['content_digest'][:12]}, not what you read (reload it)")
        dec = _text(decision).lower()
        try:
            taken = declared.take_step(
                TEMPLATE, surface=SURFACE, decision=dec, subject=f"conformance:{rc['key']}",
                reason=_text(reason), actor=actor, separated_from=builders(st),
                payload={"repo": rc["key"], "content_digest": p["content_digest"], "prepared_event": p["event_id"],
                         "as_of": (p.get("detail") or {}).get("as_of")})
        except declared.StepRefused as exc:
            raise ConformanceRefused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        row = _append(conn, repo=rc["key"], event=event, actor=taken["decided_by"], actor_kind="person",
                      actor_role=_text(actor.get("role")), step_id=taken["step_id"], reason=_text(reason),
                      content=p["content_digest"], detail={"prepared_event": p["event_id"], "decision": dec},
                      prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    out = {"repo": rc["key"], "event": event, "event_id": row["event_id"], "content_digest": p["content_digest"],
           "step": taken, "decided_by": taken["decided_by"], "prepared_by": p["actor"]}
    if event == "published":
        out["bundle"] = published_bundle(rc["key"])
    return out


def published_bundle(repo: str, conn=None) -> Optional[dict]:
    """The repo's newest PUBLISHED bundle, as prepared, with its ``publication``; None when none is."""
    rc = repo_config(repo)
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _state(rc["key"], conn)
    finally:
        if own:
            conn.close()
    pub = st["published"]
    if pub is None or problems:
        return None
    prep = next((r for r in ordered if r["event_id"] == pub["prepared_event"]), None)
    if prep is None:
        return None
    b = json.loads(prep["bundle_json"])
    b["publication"] = {"signed_by": pub["actor"], "role": pub["actor_role"] or None, "step_id": pub["step_id"],
                        "decided_at": dora._iso(dora._utc(pub["created_at"])), "template": TEMPLATE,
                        "content_digest": pub["content_digest"], "prepared_by": pub["prepared_by"],
                        "event_id": pub["event_id"]}
    return b


# ── reading ──────────────────────────────────────────────────────────────────


def ledger_view(repo: str, conn=None) -> dict:
    rc = repo_config(repo)
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, _ = _state(rc["key"], conn)
    finally:
        if own:
            conn.close()
    p = st["prepared"]
    return {"prepared": p, "published": st["published"], "history": st["history"],
            "builders": builders(st), "chain": {"intact": not problems, "problems": problems},
            "next": ["publish"] if p and not p.get("decided") else ["prepare"], "template": TEMPLATE}


def repos_view(conn=None) -> dict:
    """Every declared repo and its ledger state. Reads the ledger only; computes no bundle."""
    cfg = load_config()
    rows = read_events(conn)
    ch = chain(rows)
    out = []
    for key, rc in cfg["repos"].items():
        st = replay(ch.get(key, []))
        out.append({"key": key, "name": rc["name"], "remote": rc.get("remote"), "standards": rc.get("standards") or [],
                    "prepared": st["prepared"], "published": st["published"]})
    return {"repos": out, "chain": {"intact": not ch["_problems"], "problems": ch["_problems"], "rows": len(rows)},
            "config": {"path": CONFIG_RELPATH, "sha256": cfg["_sha256"]}, "template": TEMPLATE,
            "schema": SCHEMA, "schema_path": SCHEMA_RELPATH}


def repo_view(repo: str, *, as_of: Optional[str] = None, conn=None) -> dict:
    """The page's view of one repo: its bundle as it would be prepared now, and its ledger state."""
    b = build(repo, as_of=as_of)
    return {"bundle": b, "ledger": ledger_view(repo, conn)}


# ── twin ─────────────────────────────────────────────────────────────────────


def twin_evidence() -> dict:
    """What the governance twin reads: each repo's ledger state and its newest published bundle's facts."""
    ev: dict[str, Any] = {"repos": None, "errors": {}}
    try:
        cfg = load_config()
        rows = read_events()
        ch = chain(rows)
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["conformance"] = str(exc)
        return ev
    items = []
    for key in sorted(cfg["repos"]):
        ordered = ch.get(key, [])
        st = replay(ordered)
        pub = st["published"]
        facts = None
        if pub is not None:
            prep = next((r for r in ordered if r["event_id"] == pub["prepared_event"]), None)
            try:
                b = json.loads(prep["bundle_json"]) if prep else {}
            except ValueError:
                b = {}
            facts = {"as_of": b.get("as_of"),
                     "failing": sorted(s["id"] for s in b.get("standards") or [] if s.get("status") == "fail"),
                     "expired": sorted({e["exemption_key"] for c in b.get("scorecards") or []
                                        for e in c.get("expired") or [] if e.get("exemption_key")}),
                     "unverified_metrics": sorted(k for k, m in ((b.get("delivery") or {}).get("metrics") or {}).items()
                                                  if m.get("status") != "measured")}
        items.append({"repo": key, "prepared": st["prepared"] and {
            k: st["prepared"][k] for k in ("event_id", "actor", "created_at", "content_digest")} | {
            "decided": bool(st["prepared"].get("decided"))},
            "published": pub and {k: pub[k] for k in ("event_id", "actor", "prepared_by", "created_at",
                                                      "content_digest")},
            "facts": facts})
    ev["repos"] = items
    ev["problems"] = ch["_problems"]
    ev["policy"] = cfg.get("publish") or {}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)`` for the conformance bundles. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    repos = ev.get("repos")
    checks = [check("coe.conformance.ledger", "Conformance bundle ledger (coe_conformance_events)",
                    repos is not None, errors.get("conformance") or "ledger not read")]
    findings: list[dict] = []
    pol = {**(ev.get("policy") or {}), **(policy or {})}
    stale = float(pol.get("stale_days") or 14)
    waiting = float(pol.get("waiting_warn_days") or 7)
    for p in ev.get("problems") or []:
        findings.append(finding("coe.conformance.chain_broken", "critical", f"{p['repo']}: {p['problem']}",
                                thread_id=f"coe_conformance:{p['event_id']}",
                                recommendation="The bundle history cannot be trusted; the ledger is append-only, so "
                                               "find who wrote the row and treat it as an incident."))
    published = 0
    for r in repos or []:
        pub, prep, facts = r.get("published"), r.get("prepared"), r.get("facts") or {}
        # An unreported repo is unverified, never passed.
        checks.append(check(f"coe.conformance.{r['repo']}", f"A published conformance bundle for {r['repo']}",
                            pub is not None, "no bundle of this repo has been published: unverified"))
        if prep and not prep.get("decided"):
            age = age_hours(prep.get("created_at"), observed_at)
            if age is not None and age > waiting * 24:
                findings.append(finding("coe.conformance.awaiting_publish", "low",
                                        f"{r['repo']}: a prepared bundle has waited {age / 24:.0f} days for a person",
                                        thread_id=f"coe_conformance:{prep['event_id']}",
                                        recommendation="A person who did not prepare it publishes it or refuses."))
        if pub is None:
            continue
        published += 1
        thread = f"coe_conformance:{pub['event_id']}"
        if pub.get("actor") and pub.get("actor") == pub.get("prepared_by"):
            findings.append(finding("coe.conformance.self_published", "critical",
                                    f"{r['repo']}: the bundle was published by the person who prepared it",
                                    thread_id=thread, recommendation="Treat it as unpublished; a person who did "
                                                                     "not prepare it publishes a new one."))
        age = age_hours(pub.get("created_at"), observed_at)
        if age is not None and age > stale * 24:
            findings.append(finding("coe.conformance.stale", "medium",
                                    f"{r['repo']}: the newest published bundle is {age / 24:.0f} days old",
                                    thread_id=thread, recommendation="Prepare and publish a current bundle."))
        for sid in facts.get("failing") or []:
            findings.append(finding("coe.conformance.standard_failing", "high",
                                    f"{r['repo']}: {sid} fails in the published bundle (as of {facts.get('as_of')})",
                                    thread_id=thread, recommendation="Fix the failing services, or a person grants "
                                                                     "an exemption that expires."))
        for key in facts.get("expired") or []:
            findings.append(finding("coe.conformance.expired_exemption", "high",
                                    f"{r['repo']}: the published bundle carries an expired exemption {key}",
                                    thread_id=thread, recommendation="A person renews or revokes it on /idp/exemptions."))
        for k in facts.get("unverified_metrics") or []:
            findings.append(finding("coe.conformance.metric_unverified", "low",
                                    f"{r['repo']}: delivery metric {k} is unverified in the published bundle",
                                    thread_id=thread, recommendation="Pass in the source it needs (a CI snapshot, "
                                                                     "the board export) and publish again."))
    summary = {"conformance_repos": len(repos or []), "conformance_published": published}
    return checks, findings, summary
