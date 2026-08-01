# CUI // SP-CTI
"""MITRE ATT&CK ingestor for the ODC pipeline.

Loads MITRE Enterprise ATT&CK technique data and upserts into
odc_mitre_techniques (append-only — existing technique_ids are skipped).

Two sources are supported, selected by the ``source`` argument:

  * ``source="local"`` (default) — seed FROM the in-repo single-source-of-truth
    code catalog ``tools.observability_canvas.mitre_catalog.MITRE_CATALOG``.
    Deterministic, offline, and drift-free: the DB table can never diverge from
    the curated code constant that every other ODC module already derives from.
  * ``source="stix"`` — download the external MITRE STIX 2.x enterprise bundle
    (``force_download=True``) or fall back to the local mirror at
    ``context/mitre/enterprise.json`` (tactics[] hierarchy). Use this to pull the
    full external ATT&CK matrix rather than the curated subset.

Reconciliation note (obx-cov-04): PR #473 made ``mitre_catalog.py`` the single
technique source of truth (a code constant). The historical local-catalog mode
here read ``context/mitre/enterprise.json`` directly, which could drift from that
constant. Local mode now sources FROM ``mitre_catalog.MITRE_CATALOG`` so the two
can never disagree; the STIX/enterprise.json path is reserved for the external
full-matrix ingest.

Public API:
  ingest(catalog_path=None, force_download=False, source="local") -> dict
      Returns {"ingested": N, "skipped": N, "errors": []}
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = get_logger("icdev.mitre_ingestor")

_DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "context" / "mitre" / "enterprise.json"
_STIX_URL = (
    "https://raw.githubusercontent.com/mitre/cti/master/"
    "enterprise-attack/enterprise-attack.json"
)


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------

def _load_stix(catalog_path: Optional[Path], force_download: bool) -> dict:
    """Return raw MITRE data dict — local file or network download."""
    local = catalog_path or _DEFAULT_CATALOG

    if not force_download and local.exists():
        with open(local, encoding="utf-8") as fh:
            return json.load(fh)

    try:
        import urllib.request

        with urllib.request.urlopen(_STIX_URL, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("STIX download failed (%s); falling back to local catalog", exc)
        if local.exists():
            with open(local, encoding="utf-8") as fh:
                return json.load(fh)
        raise RuntimeError(
            f"No MITRE catalog at {local} and download failed: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_stix_bundle(bundle: dict) -> list[dict]:
    """Parse STIX 2.x bundle → list of technique dicts."""
    objects = bundle.get("objects", [])

    # Build phase_name → canonical tactic shortname mapping from x-mitre-tactic objects
    tactic_short: dict[str, str] = {}
    for obj in objects:
        if obj.get("type") == "x-mitre-tactic":
            short = obj.get("x_mitre_shortname", "")
            name = obj.get("name", "")
            if short and name:
                tactic_short[name.lower()] = short

    techniques: list[dict] = []
    for obj in objects:
        if obj.get("type") != "attack-pattern":
            continue
        if obj.get("x_mitre_deprecated") or obj.get("revoked"):
            continue

        ext_refs = obj.get("external_references", [])
        tid = next(
            (r["external_id"] for r in ext_refs if r.get("source_name") == "mitre-attack"),
            "",
        )
        if not tid.startswith("T"):
            continue

        phases = [
            p["phase_name"]
            for p in obj.get("kill_chain_phases", [])
            if p.get("kill_chain_name") == "mitre-attack"
        ]
        tactic = ",".join(phases)

        techniques.append(
            {
                "technique_id": tid,
                "name": obj.get("name", ""),
                "tactic": tactic,
            }
        )

    return techniques


def _parse_mitre_catalog() -> list[dict]:
    """Derive the technique list from the single-source-of-truth code catalog.

    ``tools.observability_canvas.mitre_catalog.MITRE_CATALOG`` is the canonical
    ODC technique catalog (PR #473). Sourcing local-mode ingest from it — rather
    than from ``context/mitre/enterprise.json`` — guarantees the persisted
    ``odc_mitre_techniques`` rows never drift from the code constant that the
    coverage twin and both Sigma generators already derive from.
    """
    from tools.observability_canvas.mitre_catalog import MITRE_CATALOG, primary_tactic

    techniques: list[dict] = []
    for tid, entry in MITRE_CATALOG.items():
        techniques.append(
            {
                "technique_id": tid,
                "name": entry.get("name", ""),
                "tactic": primary_tactic(tid),
            }
        )
    return techniques


def _parse_local_catalog(catalog: dict) -> list[dict]:
    """Parse the local enterprise.json tactic hierarchy → technique list."""
    techniques: list[dict] = []

    for tactic in catalog.get("tactics", []):
        tactic_short = tactic.get("short_name", tactic.get("id", ""))

        for tech in tactic.get("techniques", []):
            techniques.append(
                {
                    "technique_id": tech["id"],
                    "name": tech["name"],
                    "tactic": tactic_short,
                }
            )
            for sub in tech.get("sub_techniques", []):
                techniques.append(
                    {
                        "technique_id": sub["id"],
                        "name": sub["name"],
                        "tactic": tactic_short,
                    }
                )

    return techniques


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest(
    catalog_path: Optional[Path] = None,
    force_download: bool = False,
    source: str = "local",
) -> dict:
    """Ingest MITRE ATT&CK techniques into odc_mitre_techniques.

    Existing rows (by technique_id) are skipped (append-only semantics).
    sigma_template is pre-generated using tools.observability.sigma_generator.

    Args:
        catalog_path:   Optional path to a STIX/enterprise.json file (source="stix").
        force_download: Fetch the external STIX bundle (source="stix" only).
        source:         "local" (default) — seed from the mitre_catalog code
                        constant; "stix" — external STIX / enterprise.json matrix.

    Returns:
        {"ingested": int, "skipped": int, "errors": list[str]}
    """
    from tools.observability_canvas.db.init_db import get_connection, init_db

    # odc_mitre_techniques is owned by init_db.SCHEMA. Ensure it exists so the
    # ingestor is self-sufficient from the CLI (not only when the ODC blueprint
    # has already run init_db at startup).
    try:
        init_db()
    except Exception as exc:
        logger.warning("init_db() failed before MITRE ingest: %s", exc)

    source = (source or "local").strip().lower()
    if source == "local":
        techniques = _parse_mitre_catalog()
    else:
        try:
            bundle = _load_stix(catalog_path, force_download)
        except Exception as exc:
            logger.error("Failed to load MITRE catalog: %s", exc)
            return {"ingested": 0, "skipped": 0, "errors": [str(exc)]}

        techniques = (
            _parse_stix_bundle(bundle) if "objects" in bundle else _parse_local_catalog(bundle)
        )

    ingested = 0
    skipped = 0
    errors: list[str] = []

    conn = get_connection()
    try:
        for tech in techniques:
            tid = tech["technique_id"]
            try:
                existing = conn.execute(
                    "SELECT id FROM odc_mitre_techniques WHERE technique_id = %s",
                    (tid,),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                sigma_tpl = ""
                try:
                    from tools.observability.sigma_generator import generate_sigma

                    sigma_tpl = generate_sigma(tid)
                except Exception:
                    pass

                conn.execute(
                    "INSERT INTO odc_mitre_techniques "
                    "(id, technique_id, name, tactic, sigma_template, ingested_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        tid,
                        tech["name"],
                        tech["tactic"],
                        sigma_tpl,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                ingested += 1
            except Exception as exc:
                errors.append(f"{tid}: {exc}")

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "MITRE ingest complete: %d ingested, %d skipped, %d errors",
        ingested,
        skipped,
        len(errors),
    )
    return {"ingested": ingested, "skipped": skipped, "errors": errors}


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    # Default source is the in-repo mitre_catalog constant. --stix / --download
    # switch to the external STIX / enterprise.json matrix.
    _source = "stix" if ("--stix" in sys.argv or "--download" in sys.argv) else "local"
    result = ingest(source=_source, force_download="--download" in sys.argv)
    if "--json" in sys.argv:
        import json as _json
        print(_json.dumps(result, indent=2))
    else:
        print(f"Ingested: {result['ingested']}  Skipped: {result['skipped']}  Errors: {len(result['errors'])}")
        for err in result["errors"]:
            print(f"  ERROR: {err}")
