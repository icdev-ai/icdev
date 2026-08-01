# CUI // SP-CTI
"""Document Modernization Engine — curated standards catalog access layer.

One interface for "what is the approved standard?" regardless of where the
catalog lives:

- ``GenericStoreProvider`` — the central ``docmod_catalog_entries`` table
  (maintained on the canvas-independent /standards-catalog page).
- ``NetworkCatalogAdapter`` — READ-ONLY view over the network canvas's
  team-curated ``nc_hardware_profiles`` (datasheet catalog incl. eol/eos
  dates) and ``nc_device_profiles`` (vendor/platform vocabulary). The network
  team keeps editing their existing pages; nothing here writes to nc_*.
- ``MergedCatalog`` — composes both, labelling each entry's source, and
  degrades gracefully when the network canvas (nc_* tables) is absent.

Domain packs consume only this interface — they never know which backend
served an entry.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CatalogEntry:
    entry_id: str
    domain: str
    product: str
    vendor: str | None = None
    category: str | None = None
    model_family: str | None = None
    version: str | None = None
    status: str = "approved"
    eol_date: str | None = None
    eos_date: str | None = None
    replacement_entry_id: str | None = None
    tags: list[str] = field(default_factory=list)
    provider_source: str = "generic"     # 'generic' | 'network_canvas'
    detail: dict = field(default_factory=dict)

    def citation(self) -> dict:
        """Citation-shaped evidence entry for Verdict/redline gating."""
        return {
            "source": f"catalog:{self.provider_source}:{self.entry_id}",
            "detail": f"{self.vendor or ''} {self.product} [{self.status}]".strip(),
            "date": self.eol_date or "",
        }


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class CatalogProvider:
    """Interface — see MergedCatalog for the composed default."""

    source_label = "generic"

    def get_approved(self, domain: str, category: str | None = None) -> list[CatalogEntry]:
        raise NotImplementedError

    def get_deprecated(self, domain: str) -> list[CatalogEntry]:
        raise NotImplementedError

    def lookup(self, domain: str, label: str, vendor: str | None = None) -> CatalogEntry | None:
        """Match an extracted entity label against catalog entries
        (product exact-normalized first, then model_family prefix)."""
        target = _norm(label)
        if not target:
            return None
        entries = self.get_approved(domain) + self.get_deprecated(domain)
        for e in entries:
            if _norm(e.product) and _norm(e.product) in target or target in _norm(e.product):
                if vendor and e.vendor and _norm(vendor) != _norm(e.vendor):
                    continue
                return e
        for e in entries:
            fam = _norm(e.model_family)
            if fam and (target.startswith(fam) or fam in target):
                return e
        return None


class GenericStoreProvider(CatalogProvider):
    """Central docmod_catalog_entries store (migration 257)."""

    source_label = "generic"

    def __init__(self, conn_factory=None):
        if conn_factory is None:
            from tools.db.storage import get_connection
            conn_factory = get_connection
        self._connect = conn_factory

    def _rows(self, sql: str, params: tuple) -> list[CatalogEntry]:
        conn = self._connect()
        try:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        except Exception as exc:
            logger.warning("docmod catalog: generic store query failed: %s", exc)
            rows = []
        finally:
            try:
                conn.close()
            except Exception:
                pass
        out = []
        for r in rows:
            try:
                tags = json.loads(r.get("tags_json") or "[]")
            except Exception:
                tags = []
            out.append(CatalogEntry(
                entry_id=r["entry_id"], domain=r["domain"], product=r["product"],
                vendor=r.get("vendor"), category=r.get("category"),
                model_family=r.get("model_family"), version=r.get("version"),
                status=r.get("status") or "approved", eol_date=r.get("eol_date"),
                eos_date=r.get("eos_date"),
                replacement_entry_id=r.get("replacement_entry_id"),
                tags=tags, provider_source=self.source_label,
            ))
        return out

    def get_approved(self, domain: str, category: str | None = None) -> list[CatalogEntry]:
        if category:
            return self._rows(
                "SELECT * FROM docmod_catalog_entries WHERE domain=%s AND status='approved' AND category=%s",
                (domain, category),
            )
        return self._rows(
            "SELECT * FROM docmod_catalog_entries WHERE domain=%s AND status='approved'",
            (domain,),
        )

    def get_deprecated(self, domain: str) -> list[CatalogEntry]:
        return self._rows(
            "SELECT * FROM docmod_catalog_entries WHERE domain=%s AND status IN ('deprecated','retired')",
            (domain,),
        )


class NetworkCatalogAdapter(CatalogProvider):
    """READ-ONLY adapter over nc_hardware_profiles / nc_device_profiles.

    Serves only the 'network_hardware' domain. Absence of the nc_* tables
    (network canvas disabled) yields empty results, never an error.
    """

    source_label = "network_canvas"
    domain = "network_hardware"

    def __init__(self, conn_factory=None):
        if conn_factory is None:
            # nc_* tables have no tenant_id/classification columns — the global
            # RLS predicate from get_connection() raises UndefinedColumn under
            # an active dashboard security context (canonical canvas gotcha).
            from tools.db.storage import get_canvas_connection
            conn_factory = get_canvas_connection
        self._connect = conn_factory

    def _hardware_rows(self) -> list[CatalogEntry]:
        conn = self._connect()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, vendor, model, model_family, device_type, "
                "eol_date, eos_date, tags, is_builtin FROM nc_hardware_profiles"
            ).fetchall()]
        except Exception:
            rows = []  # network canvas not installed — graceful
        finally:
            try:
                conn.close()
            except Exception:
                pass
        out = []
        for r in rows:
            try:
                tags = json.loads(r.get("tags") or "[]")
            except Exception:
                tags = []
            # A profile past EOS is treated as deprecated in catalog terms.
            status = "approved"
            eos = r.get("eos_date") or ""
            eol = r.get("eol_date") or ""
            today = _now()[:10]
            if eol and eol[:10] <= today:
                status = "retired"
            elif eos and eos[:10] <= today:
                status = "deprecated"
            out.append(CatalogEntry(
                entry_id=str(r["id"]), domain=self.domain, product=r.get("model") or "",
                vendor=r.get("vendor"), category=r.get("device_type"),
                model_family=r.get("model_family"), status=status,
                eol_date=r.get("eol_date"), eos_date=r.get("eos_date"),
                tags=tags, provider_source=self.source_label,
            ))
        return out

    def vocabulary(self) -> list[tuple[str, str]]:
        """(vendor, platform/model) vocabulary for extraction seeding —
        nc_device_profiles plus hardware models."""
        conn = self._connect()
        vocab: list[tuple[str, str]] = []
        try:
            for r in conn.execute("SELECT vendor, platform FROM nc_device_profiles").fetchall():
                d = dict(r)
                vocab.append((d.get("vendor") or "", d.get("platform") or ""))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        vocab.extend((e.vendor or "", e.product) for e in self._hardware_rows())
        return [v for v in vocab if v[1]]

    def get_approved(self, domain: str, category: str | None = None) -> list[CatalogEntry]:
        if domain != self.domain:
            return []
        rows = [e for e in self._hardware_rows() if e.status == "approved"]
        if category:
            rows = [e for e in rows if _norm(e.category) == _norm(category)]
        return rows

    def get_deprecated(self, domain: str) -> list[CatalogEntry]:
        if domain != self.domain:
            return []
        return [e for e in self._hardware_rows() if e.status in ("deprecated", "retired")]


class MergedCatalog(CatalogProvider):
    """Generic store + adapters, each entry labelled with its source."""

    def __init__(self, providers: list[CatalogProvider] | None = None):
        if providers is None:
            providers = [GenericStoreProvider(), NetworkCatalogAdapter()]
        self.providers = providers

    def get_approved(self, domain: str, category: str | None = None) -> list[CatalogEntry]:
        out: list[CatalogEntry] = []
        for p in self.providers:
            out.extend(p.get_approved(domain, category))
        return out

    def get_deprecated(self, domain: str) -> list[CatalogEntry]:
        out: list[CatalogEntry] = []
        for p in self.providers:
            out.extend(p.get_deprecated(domain))
        return out


def propose_from_defacto(record: dict, actor: str = "docmod", conn=None) -> str:
    """Create a draft catalog entry from a de-facto-learner record
    (source='promoted_from_defacto'), leaving status for team approval.
    Returns the entry_id. Appends a docmod_catalog_audit row."""
    from tools.db.storage import get_connection

    own = conn is None
    if own:
        conn = get_connection()
    entry_id = f"cat-{uuid.uuid4().hex[:10]}"
    now = _now()
    try:
        conn.execute(
            """INSERT INTO docmod_catalog_entries
               (entry_id, domain, category, vendor, product, version, status,
                source, created_by, created_at, tenant_id, classification)
               VALUES (%s,%s,%s,%s,%s,%s,'approved','promoted_from_defacto',%s,%s,%s,%s)
               ON CONFLICT (domain, category, vendor, product, version) DO NOTHING""",
            (
                # ''-coalesced uniqueness columns — SQL NULLs never collide in
                # the UNIQUE constraint, which would allow duplicate proposals.
                entry_id, record["domain"], record.get("category") or "general",
                record.get("vendor") or "", record["product"],
                record.get("version") or "",
                actor, now, record.get("tenant_id"), record.get("classification"),
            ),
        )
        # Audit columns per migration 257: id / event_type / details / recorded_at.
        conn.execute(
            """INSERT INTO docmod_catalog_audit
               (id, entry_id, event_type, actor, details, recorded_at, tenant_id, classification)
               VALUES (%s,%s,'promote',%s,%s,%s,%s,%s)""",
            (
                f"aud-{uuid.uuid4().hex[:10]}", entry_id, actor,
                json.dumps(record, default=str), now,
                record.get("tenant_id"), record.get("classification"),
            ),
        )
        conn.commit()
    finally:
        if own:
            conn.close()
    return entry_id
