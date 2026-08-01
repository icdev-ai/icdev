# CUI // SP-CTI
"""CCC — LOA (Letter of Authorization) workflow and document generator."""
from __future__ import annotations

from datetime import datetime, date, timedelta, timezone


def _next_loa_number(conn) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m")
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM ccc_loa_requests WHERE loa_number LIKE %s",
            (f"LOA-{ts}-%",),
        ).fetchone()
    except Exception:
        row = conn.execute(
            "SELECT COUNT(*) FROM ccc_loa_requests WHERE loa_number LIKE %s",
            (f"LOA-{ts}-%",),
        ).fetchone()
    seq = (row[0] if row else 0) + 1
    return f"LOA-{ts}-{seq:04d}"


def create_loa_request(conn, data: dict) -> dict:
    """Insert a new LOA request and return the record."""
    loa_number = _next_loa_number(conn)
    fields = {
        "loa_number":       loa_number,
        "circuit_id":       data.get("circuit_id"),
        "xc_id":            data.get("xc_id"),
        "facility":         data.get("facility", ""),
        "rack_a":           data.get("rack_a", ""),
        "rack_z":           data.get("rack_z", ""),
        "patch_panel_a":    data.get("patch_panel_a", ""),
        "patch_panel_z":    data.get("patch_panel_z", ""),
        "requester_name":   data.get("requester_name", ""),
        "requester_email":  data.get("requester_email", ""),
        "requester_company": data.get("requester_company", ""),
        "status":           "draft",
        "valid_days":       data.get("valid_days", 30),
        "notes":            data.get("notes", ""),
    }
    cols = ", ".join(fields.keys())
    # NB: the list form is required. ", ".join("%s" * n) builds the STRING
    # "%s%s%s..." and join() then iterates its characters, yielding
    # "%, s, %, s, ..." — which is what the old except-branch here did.
    placeholders = ", ".join(["%s"] * len(fields))
    conn.execute(f"INSERT INTO ccc_loa_requests ({cols}) VALUES ({placeholders})", tuple(fields.values()))
    conn.commit()
    return {"status": "created", "loa_number": loa_number}


def generate_loa_text(conn, loa_id: int) -> str:
    """Generate a plain-text LOA document from a request record."""
    try:
        row = conn.execute("SELECT * FROM ccc_loa_requests WHERE id=%s", (loa_id,)).fetchone()
    except Exception:
        row = conn.execute("SELECT * FROM ccc_loa_requests WHERE id=%s", (loa_id,)).fetchone()
    if not row:
        return "LOA not found."
    r = dict(row)
    valid_until = (date.today() + timedelta(days=int(r.get("valid_days", 30)))).isoformat()

    return f"""LETTER OF AUTHORIZATION (LOA)
{r['loa_number']} — {r['facility'].upper()} Facility
Generated: {date.today().isoformat()}  |  Valid Until: {valid_until}
Classification: CUI

AUTHORIZED PARTY
  Company:   {r['requester_company']}
  Contact:   {r['requester_name']}
  Email:     {r['requester_email']}

CROSS-CONNECT AUTHORIZATION
  Facility:         {r['facility'].upper()}
  Rack A (Near):    {r.get('rack_a', 'TBD')}
  Patch Panel A:    {r.get('patch_panel_a', 'TBD')}
  Rack Z (Far):     {r.get('rack_z', 'TBD')}
  Patch Panel Z:    {r.get('patch_panel_z', 'TBD')}

AUTHORIZATION
This LOA authorizes the above-named party to install cross-connect cabling
between the specified demarcation points at the {r['facility'].upper()} facility.
All work must comply with facility standards and be completed by {valid_until}.

Notes: {r.get('notes', 'None')}

Authorized by: ________________________________  Date: ___________
Title: NOC Manager / Network Engineering
"""


def generate_loa_for_facility(conn, loa_id: int, facility_type: str) -> str:
    """Facility-aware LOA document.

    'equinix': adds IBX code, MMR/MRR designation, LOA-CFA header, Smart Hands section.
    'megaport': adds Megaport UID, diversity designation, VXC details.
    Falls back to generate_loa_text() for 'generic' or unknown facility types.
    """
    facility_type = (facility_type or "generic").lower()
    if facility_type not in ("equinix", "megaport"):
        return generate_loa_text(conn, loa_id)

    try:
        row = conn.execute("SELECT * FROM ccc_loa_requests WHERE id=%s", (loa_id,)).fetchone()
    except Exception:
        row = conn.execute("SELECT * FROM ccc_loa_requests WHERE id=%s", (loa_id,)).fetchone()
    if not row:
        return "LOA not found."

    r = dict(row)
    valid_until = (date.today() + timedelta(days=int(r.get("valid_days", 30)))).isoformat()
    notes = r.get("notes", "")

    # Parse optional JSON extras from notes field
    import json as _json
    extras: dict = {}
    try:
        extras = _json.loads(notes) if notes.startswith("{") else {}
    except Exception:
        pass

    if facility_type == "equinix":
        ibx_code = extras.get("ibx_code", r.get("facility_id", "TBD"))
        mmr = extras.get("mmr", "TBD")
        smart_hands = extras.get("smart_hands", False)
        sh_section = (
            "\nSMART HANDS AUTHORIZATION\n"
            "  This LOA also authorizes Equinix Smart Hands to perform cable\n"
            "  installation on behalf of the requester. Smart Hands task must\n"
            "  reference this LOA number.\n"
        ) if smart_hands else ""

        return f"""LETTER OF AUTHORIZATION — LOA-CFA (Cross-Facility Authorization)
{r['loa_number']} — Equinix IBX: {ibx_code}
Generated: {date.today().isoformat()}  |  Valid Until: {valid_until}
Classification: CUI

AUTHORIZED PARTY
  Company:     {r['requester_company']}
  Contact:     {r['requester_name']}
  Email:       {r['requester_email']}

CROSS-CONNECT AUTHORIZATION
  Facility:         EQUINIX {ibx_code}
  MMR/MRR:          {mmr}
  Rack A (Near):    {r.get('rack_a', 'TBD')}
  Patch Panel A:    {r.get('patch_panel_a', 'TBD')}
  Rack Z (Far):     {r.get('rack_z', 'TBD')}
  Patch Panel Z:    {r.get('patch_panel_z', 'TBD')}
{sh_section}
AUTHORIZATION
This LOA-CFA authorizes the above-named party to install cross-connect cabling
between the specified demarcation points at Equinix IBX {ibx_code}.
All work must comply with Equinix IBX standards (ECP-CX) and be completed
by {valid_until}. Requester is responsible for coordinating with Equinix IBX Ops.

Notes: {notes or 'None'}

Authorized by: ________________________________  Date: ___________
Title: NOC Manager / Network Engineering
"""

    # megaport
    megaport_uid = extras.get("megaport_uid", r.get("facility_id", "TBD"))
    diverse_port = extras.get("diverse_port", "")
    vxc_details = extras.get("vxc_details", "")
    diversity_section = (
        f"\n  Diversity Port:   {diverse_port}"
    ) if diverse_port else ""
    vxc_section = (
        f"\nVXC DETAILS\n  {vxc_details}\n"
    ) if vxc_details else ""

    return f"""LETTER OF AUTHORIZATION
{r['loa_number']} — Megaport Port UID: {megaport_uid}
Generated: {date.today().isoformat()}  |  Valid Until: {valid_until}
Classification: CUI

AUTHORIZED PARTY
  Company:     {r['requester_company']}
  Contact:     {r['requester_name']}
  Email:       {r['requester_email']}

CROSS-CONNECT AUTHORIZATION
  Facility:         Megaport
  Port UID:         {megaport_uid}{diversity_section}
  Rack A (Near):    {r.get('rack_a', 'TBD')}
  Patch Panel A:    {r.get('patch_panel_a', 'TBD')}
  Rack Z (Far):     {r.get('rack_z', 'TBD')}
  Patch Panel Z:    {r.get('patch_panel_z', 'TBD')}
{vxc_section}
AUTHORIZATION
This LOA authorizes the above-named party to provision a Megaport cross-connect
(VXC) on Port UID {megaport_uid}, valid until {valid_until}.

Notes: {notes or 'None'}

Authorized by: ________________________________  Date: ___________
Title: NOC Manager / Network Engineering
"""
