# CUI // SP-CTI
"""MITRE ATT&CK STIX ingestor for the ODC pipeline.

Downloads (or loads locally) the MITRE Enterprise ATT&CK data, parses
technique objects, and upserts into odc_mitre_techniques.

Supports two input formats:
  1. STIX 2.x bundle (objects array with attack-pattern type)
  2. Local enterprise.json format used by this codebase (tactics[] hierarchy)

The STIX bundle is attempted first when force_download=True, then falls back
to the local catalog at context/mitre/enterprise.json.

Public API:
  ingest(catalog_path=None, force_download=False) -> dict
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
) -> dict:
    """Ingest MITRE ATT&CK techniques into odc_mitre_techniques.

    Existing rows (by technique_id) are skipped (append-only semantics).
    sigma_template is pre-generated using tools.observability.sigma_generator.

    Returns:
        {"ingested": int, "skipped": int, "errors": list[str]}
    """
    from tools.observability_canvas.db.init_db import get_connection

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
    result = ingest(force_download="--download" in sys.argv)
    if "--json" in sys.argv:
        import json as _json
        print(_json.dumps(result, indent=2))
    else:
        print(f"Ingested: {result['ingested']}  Skipped: {result['skipped']}  Errors: {len(result['errors'])}")
        for err in result["errors"]:
            print(f"  ERROR: {err}")
