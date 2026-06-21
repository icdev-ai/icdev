"""Data Mesh integration layer — Domains, Products, Contracts, Governance, CSP."""
# data_mesh.py is shadowed by this package directory. Load it explicitly and
# re-export its public API so callers of `tools.data_canvas.data_mesh` still work.
import importlib.util as _ilu
import pathlib as _pl
import sys as _sys

_flat = _pl.Path(__file__).parent.parent / "data_mesh.py"
if _flat.exists():
    _KEY = "tools.data_canvas._data_mesh_flat"
    if _KEY not in _sys.modules:
        _spec = _ilu.spec_from_file_location(_KEY, _flat)
        _mod = _ilu.module_from_spec(_spec)
        _sys.modules[_KEY] = _mod
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    else:
        _mod = _sys.modules[_KEY]

    create_domain = _mod.create_domain  # noqa: F401
    list_domains = _mod.list_domains  # noqa: F401
    get_domain = _mod.get_domain  # noqa: F401
    create_data_product = _mod.create_data_product  # noqa: F401
    list_products = _mod.list_products  # noqa: F401
    assess_domain_maturity = _mod.assess_domain_maturity  # noqa: F401

# list_contracts queries dm_contracts directly (not in the flat module)
from tools.data_canvas.db.init_db import get_connection as _gc  # noqa: E402


def list_contracts(product_id: str | None = None) -> list:
    try:
        conn = _gc()
        if product_id:
            rows = conn.execute(
                "SELECT * FROM dm_contracts WHERE product_id=? ORDER BY title",
                (product_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM dm_contracts ORDER BY title").fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"error": str(exc)}]


# Re-export governance sub-module surface
from tools.data_canvas.data_mesh.governance_engine import (  # noqa: E402,F401
    compute_governance_score,
    list_policies,
    create_policy,
    check_access,
)
from tools.data_canvas.data_mesh.lineage_emitter import (  # noqa: E402,F401
    emit_lineage_event,
    emit_data_product_lineage,
)
