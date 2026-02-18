# CUI // SP-CTI
"""ICDEV Architecture Extractor — reverse-engineering extraction tool.

Extracts call graphs, component diagrams, data flows, service boundaries,
database schemas, and architecture summaries from legacy applications
analyzed by legacy_analyzer.py.

Usage:
    python tools/modernization/architecture_extractor.py --app-id ID --extract summary
    python tools/modernization/architecture_extractor.py --app-id ID --extract call-graph --json
    python tools/modernization/architecture_extractor.py --app-id ID --extract db-schema --source-path /path

Classification: CUI // SP-CTI
"""
import argparse, collections, datetime, json, math, os, re, sqlite3, uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CALL_GRAPH_DEP_TYPES = ("method_call", "import", "inheritance", "injection")
COMPLEXITY_THRESHOLDS = {"low": 10, "medium": 20, "high": 50}
LAYER_ORDER = ["api_endpoint", "controller", "servlet", "service", "ejb",
               "repository", "model", "entity", "stored_procedure", "trigger",
               "function", "util", "config", "migration"]
DB_LAYER_TYPES = {"repository", "model", "entity", "stored_procedure", "trigger", "migration"}
GENERIC_WORDS = {"service", "controller", "repository", "model", "impl", "base",
                 "abstract", "interface", "test", "util", "helper", "manager",
                 "handler", "the", "class", "module", "component", "config"}
_FT = {"String": "VARCHAR", "Text": "TEXT", "Integer": "INTEGER", "Float": "FLOAT",
       "Boolean": "BOOLEAN", "DateTime": "TIMESTAMP", "Date": "DATE", "BigInteger": "BIGINT",
       "CharField": "VARCHAR", "TextField": "TEXT", "IntegerField": "INTEGER",
       "FloatField": "FLOAT", "BooleanField": "BOOLEAN", "DateTimeField": "TIMESTAMP",
       "DateField": "DATE", "AutoField": "INTEGER", "DecimalField": "NUMERIC",
       "UUIDField": "UUID", "ForeignKey": "INTEGER", "int": "INTEGER", "long": "BIGINT",
       "Long": "BIGINT", "Double": "DOUBLE", "boolean": "BOOLEAN",
       "LocalDate": "DATE", "LocalDateTime": "TIMESTAMP", "BigDecimal": "NUMERIC"}

# -- Compiled regex patterns --------------------------------------------------
_RE = {
    "sa_table": re.compile(r'__tablename__\s*=\s*["\'](\w+)["\']'),
    "sa_col": re.compile(r'(\w+)\s*=\s*(?:db\.)?Column\(\s*(?:db\.)?(\w+)'),
    "sa_fk": re.compile(r"ForeignKey\(\s*['\"](\w+)\.(\w+)['\"]\s*\)"),
    "sa_cls": re.compile(r'class\s+(\w+)\s*\('),
    "dj_cls": re.compile(r'class\s+(\w+)\s*\(.*?models\.Model.*?\)'),
    "dj_fld": re.compile(r'(\w+)\s*=\s*models\.(\w+Field)\('),
    "dj_fk": re.compile(r"(\w+)\s*=\s*models\.ForeignKey\(\s*['\"]?(\w+)['\"]?"),
    "dj_meta": re.compile(r'db_table\s*=\s*["\'](\w+)["\']'),
    "hb_ent": re.compile(r'@Entity'), "hb_tbl": re.compile(r'@Table\s*\(\s*name\s*=\s*"(\w+)"'),
    "hb_col": re.compile(r'@Column\s*\(.*?name\s*=\s*"(\w+)".*?\)\s*(?:private|protected|public)\s+(\w+)\s+(\w+)'),
    "hb_fld": re.compile(r'(?:private|protected|public)\s+(\w+)\s+(\w+)\s*;'),
    "hb_id": re.compile(r'@Id'), "hb_cls": re.compile(r'public\s+class\s+(\w+)'),
    "hb_m2o": re.compile(r'@ManyToOne.*?(?:private|protected|public)\s+(\w+)\s+(\w+)', re.DOTALL),
    "hb_o2m": re.compile(r'@OneToMany.*?(?:private|protected|public)\s+\w+<(\w+)>\s+(\w+)', re.DOTALL),
    "ef_dbs": re.compile(r'DbSet<(\w+)>\s+(\w+)'),
    "ef_key": re.compile(r'\[Key\]'), "ef_fk": re.compile(r'\[ForeignKey\("(\w+)"\)\]'),
    "ef_tbl": re.compile(r'\[Table\("(\w+)"\)\]'), "ef_cls": re.compile(r'public\s+class\s+(\w+)'),
    "ef_prop": re.compile(r'public\s+(\w+\??)\s+(\w+)\s*\{\s*get;\s*set;\s*\}'),
    "ef_h1": re.compile(r'\.HasOne<(\w+)>\('), "ef_hm": re.compile(r'\.HasMany<(\w+)>\('),
    "sql_ct": re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?', re.I),
    "sql_cd": re.compile(r'^\s+[`"\[]?(\w+)[`"\]]?\s+([\w()]+)', re.M),
    "sql_pki": re.compile(r'(\w+)\s+\w+.*?PRIMARY\s+KEY', re.I),
    "sql_pkc": re.compile(r'PRIMARY\s+KEY\s*\(([^)]+)\)', re.I),
    "sql_fkc": re.compile(r'FOREIGN\s+KEY\s*\(\s*[`"\[]?(\w+)[`"\]]?\s*\)\s*REFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(\s*[`"\[]?(\w+)[`"\]]?\s*\)', re.I),
    "sql_ac": re.compile(r'ALTER\s+TABLE\s+[`"\[]?(\w+)[`"\]]?\s+ADD\s+(?:COLUMN\s+)?[`"\[]?(\w+)[`"\]]?\s+([\w()]+)', re.I),
    "sql_afk": re.compile(r'ALTER\s+TABLE\s+[`"\[]?(\w+)[`"\]]?\s+ADD\s+.*?FOREIGN\s+KEY\s*\(\s*[`"\[]?(\w+)[`"\]]?\s*\)\s*REFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(\s*[`"\[]?(\w+)[`"\]]?\s*\)', re.I),
}

# -- Helpers -------------------------------------------------------------------
def _get_db() -> sqlite3.Connection:
    """Return sqlite3 connection to ICDEV database with WAL mode and Row factory."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"ICDEV database not found at {DB_PATH}. Run 'python tools/db/init_icdev_db.py' first.")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _complexity_bucket(v: float) -> str:
    if v <= 10: return "low"
    if v <= 20: return "medium"
    if v <= 50: return "high"
    return "very_high"

# -- 1. Call Graph Extraction --------------------------------------------------
def extract_call_graph(app_id: str) -> dict:
    """Build directed graph from legacy_dependencies (method_call/import/inheritance/injection).
    Returns {nodes: [{id, name, type, loc, complexity}], edges: [{source, target, type, weight}]}."""
    conn = _get_db()
    try:
        ph = ",".join("?" for _ in CALL_GRAPH_DEP_TYPES)
        edge_rows = conn.execute(
            f"SELECT source_component_id, target_component_id, dependency_type, weight "
            f"FROM legacy_dependencies WHERE legacy_app_id=? AND dependency_type IN ({ph}) "
            f"AND target_component_id IS NOT NULL", (app_id, *CALL_GRAPH_DEP_TYPES)).fetchall()
        cids = set()
        for r in edge_rows: cids.add(r["source_component_id"]); cids.add(r["target_component_id"])
        nodes = []
        if cids:
            iph = ",".join("?" for _ in cids)
            for c in conn.execute(f"SELECT id,name,component_type,loc,cyclomatic_complexity FROM legacy_components WHERE id IN ({iph})", list(cids)).fetchall():
                nodes.append({"id": c["id"], "name": c["name"], "type": c["component_type"], "loc": c["loc"] or 0, "complexity": c["cyclomatic_complexity"] or 0.0})
        edges = [{"source": r["source_component_id"], "target": r["target_component_id"], "type": r["dependency_type"], "weight": r["weight"] or 1.0} for r in edge_rows]
        return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()

# -- 2. Component Diagram Extraction -------------------------------------------
def extract_component_diagram(app_id: str) -> dict:
    """Group components by package (parsed from qualified_name) with internal/external dep counts.
    Returns {packages: [...], inter_package_edges: [...]}."""
    conn = _get_db()
    try:
        comps = conn.execute("SELECT id,name,component_type,qualified_name FROM legacy_components WHERE legacy_app_id=?", (app_id,)).fetchall()
        deps = conn.execute("SELECT source_component_id,target_component_id FROM legacy_dependencies WHERE legacy_app_id=? AND target_component_id IS NOT NULL", (app_id,)).fetchall()
        c2p, pkg_comps = {}, collections.defaultdict(list)
        for c in comps:
            qn = c["qualified_name"] or c["name"] or ""
            parts = qn.rsplit(".", 1)
            pkg = parts[0] if len(parts) > 1 else "(default)"
            c2p[c["id"]] = pkg
            pkg_comps[pkg].append({"id": c["id"], "name": c["name"], "type": c["component_type"]})
        pi, pe, ipe = collections.Counter(), collections.Counter(), collections.Counter()
        for d in deps:
            sp, tp = c2p.get(d["source_component_id"]), c2p.get(d["target_component_id"])
            if not sp or not tp: continue
            if sp == tp: pi[sp] += 1
            else: pe[sp] += 1; ipe[(sp, tp)] += 1
        packages = [{"name": p, "components": pkg_comps[p], "internal_deps": pi.get(p, 0), "external_deps": pe.get(p, 0)} for p in sorted(pkg_comps)]
        inter = [{"source_pkg": s, "target_pkg": t, "count": c} for (s, t), c in sorted(ipe.items(), key=lambda x: -x[1])]
        return {"packages": packages, "inter_package_edges": inter}
    finally:
        conn.close()

# -- 3. Data Flow Extraction ---------------------------------------------------
def extract_data_flow(app_id: str) -> dict:
    """Trace data from API endpoints through component layers to database.
    Returns {flows: [{api_endpoint, method, path, chain: [...], reaches_database: bool}]}."""
    conn = _get_db()
    try:
        apis = conn.execute("SELECT id,component_id,method,path FROM legacy_apis WHERE legacy_app_id=?", (app_id,)).fetchall()
        cm = {c["id"]: dict(c) for c in conn.execute("SELECT id,name,component_type FROM legacy_components WHERE legacy_app_id=?", (app_id,)).fetchall()}
        adj = collections.defaultdict(set)
        for d in conn.execute("SELECT source_component_id,target_component_id FROM legacy_dependencies WHERE legacy_app_id=? AND target_component_id IS NOT NULL", (app_id,)).fetchall():
            adj[d["source_component_id"]].add(d["target_component_id"])
        lr = {t: i for i, t in enumerate(LAYER_ORDER)}
        flows = []
        for api in apis:
            sid = api["component_id"]
            if not sid or sid not in cm:
                flows.append({"api_endpoint": api["id"], "method": api["method"], "path": api["path"], "chain": [], "reaches_database": False}); continue
            visited, queue, chain, rdb = set(), collections.deque([sid]), [], False
            while queue:
                cid = queue.popleft()
                if cid in visited: continue
                visited.add(cid)
                c = cm.get(cid)
                if not c: continue
                chain.append({"component_name": c["name"], "component_type": c["component_type"]})
                if c["component_type"] in DB_LAYER_TYPES: rdb = True
                for nb in adj.get(cid, []):
                    if nb not in visited: queue.append(nb)
            chain.sort(key=lambda x: lr.get(x["component_type"], 999))
            flows.append({"api_endpoint": api["id"], "method": api["method"], "path": api["path"], "chain": chain, "reaches_database": rdb})
        return {"flows": flows}
    finally:
        conn.close()

# -- 4. Service Boundary Detection (Louvain-like) -----------------------------
def _suggest_service_name(mids: list, ci: dict) -> str:
    """Auto-generate service name from dominant component types and common name words."""
    if not mids: return "unknown-service"
    tc, nw = collections.Counter(), collections.Counter()
    for m in mids:
        info = ci.get(m, {})
        tc[info.get("type", "unknown")] += 1
        for w in re.findall(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z]|$)', info.get("name", "")):
            if w.lower() not in GENERIC_WORDS: nw[w.lower()] += 1
    dt = tc.most_common(1)[0][0]
    dh = nw.most_common(1)[0][0] if nw else ""
    return f"{dh}-{dt}-service" if dh else f"{dt}-service"

def detect_service_boundaries(app_id: str) -> dict:
    """Community detection via Louvain-like modularity optimization (stdlib only).
    Returns {communities: [{id, name, components, cohesion, coupling, suggested_service_name}], modularity_score}."""
    conn = _get_db()
    try:
        comps = conn.execute("SELECT id,name,component_type,coupling_score,cohesion_score FROM legacy_components WHERE legacy_app_id=?", (app_id,)).fetchall()
        deps = conn.execute("SELECT source_component_id,target_component_id,weight FROM legacy_dependencies WHERE legacy_app_id=? AND target_component_id IS NOT NULL", (app_id,)).fetchall()
        if not comps: return {"communities": [], "modularity_score": 0.0}
        ci = {c["id"]: {"name": c["name"], "type": c["component_type"], "coupling": c["coupling_score"] or 0.0, "cohesion": c["cohesion_score"] or 0.0} for c in comps}
        nids = list(ci.keys()); nidx = {v: i for i, v in enumerate(nids)}; n = len(nids)
        adj = collections.defaultdict(lambda: collections.defaultdict(float)); tw = 0.0
        for d in deps:
            s, t, w = d["source_component_id"], d["target_component_id"], d["weight"] or 1.0
            if s not in nidx or t not in nidx: continue
            i, j = nidx[s], nidx[t]
            adj[i][j] += w; adj[j][i] += w; tw += w
        if tw == 0:
            out = [{"id": str(uuid.uuid4()), "name": _suggest_service_name([nid], ci), "components": [nid],
                    "cohesion": ci[nid]["cohesion"], "coupling": ci[nid]["coupling"],
                    "suggested_service_name": _suggest_service_name([nid], ci)} for nid in nids]
            return {"communities": out, "modularity_score": 0.0}
        m2 = 2.0 * tw
        deg = [sum(adj[i].values()) for i in range(n)]
        comm = list(range(n))
        for _ in range(50):
            improved = False
            for i in range(n):
                cc = comm[i]; ce = collections.defaultdict(float)
                for j, w in adj[i].items(): ce[comm[j]] += w
                ki = deg[i]; st = collections.defaultdict(float)
                for j2 in range(n): st[comm[j2]] += deg[j2]
                best_d, best_c = 0.0, cc
                for tc2, ki_t in ce.items():
                    if tc2 == cc: continue
                    delta = (ki_t / tw - st[tc2] * ki / (m2 * tw)) - (ce.get(cc, 0.0) / tw - (st[cc] - ki) * ki / (m2 * tw))
                    if delta > best_d: best_d = delta; best_c = tc2
                if best_c != cc: comm[i] = best_c; improved = True
            if not improved: break
        cm2 = collections.defaultdict(list)
        for i, c in enumerate(comm): cm2[c].append(nids[i])
        mod = 0.0
        for i in range(n):
            for j, w in adj[i].items():
                if comm[i] == comm[j]: mod += w - (deg[i] * deg[j] / m2)
        mod /= m2
        out = []
        for _, mids in sorted(cm2.items()):
            ch = [ci[m]["cohesion"] for m in mids]; cp = [ci[m]["coupling"] for m in mids]
            out.append({"id": str(uuid.uuid4()), "name": _suggest_service_name(mids, ci),
                        "components": mids, "cohesion": round(sum(ch)/len(ch), 4) if ch else 0.0,
                        "coupling": round(sum(cp)/len(cp), 4) if cp else 0.0,
                        "suggested_service_name": _suggest_service_name(mids, ci)})
        return {"communities": out, "modularity_score": round(mod, 4)}
    finally:
        conn.close()

# -- 5. Database Schema Extraction ---------------------------------------------
def _detect_db_type(source: Path, framework: str) -> str:
    """Heuristic DB type detection from config files and framework name."""
    for pat in ("application.properties", "application.yml", "settings.py",
                "database.yml", "appsettings.json", "persistence.xml"):
        for f in source.rglob(pat):
            try:
                lo = f.read_text(encoding="utf-8", errors="ignore").lower()
                for kw, db in [("postgresql", "postgresql"), ("postgres", "postgresql"),
                               ("mysql", "mysql"), ("oracle", "oracle"),
                               ("sqlserver", "mssql"), ("mssql", "mssql"),
                               ("sqlite", "sqlite"), ("h2", "h2"), ("db2", "db2")]:
                    if kw in lo: return db
            except OSError: continue
    for kw, db in [("django", "postgresql"), ("spring", "postgresql"),
                   ("hibernate", "postgresql"), ("entity", "mssql"), (".net", "mssql")]:
        if kw in framework: return db
    return "postgresql"

def _parse_sqlalchemy(src: Path, tbls: dict, rels: list):
    for pf in src.rglob("*.py"):
        try: content = pf.read_text(encoding="utf-8", errors="ignore")
        except OSError: continue
        if "Column(" not in content: continue
        ct = None
        for ln in content.splitlines():
            cm = _RE["sa_cls"].search(ln)
            if cm: ct = None
            tm = _RE["sa_table"].search(ln)
            if tm: ct = tm.group(1); tbls.setdefault(ct, {"columns": []})
            if ct:
                col = _RE["sa_col"].search(ln)
                if col:
                    cn, tr = col.group(1), col.group(2)
                    pk = "primary_key=True" in ln or "primary_key = True" in ln
                    entry = {"name": cn, "type": _FT.get(tr, tr), "pk": pk}
                    fk = _RE["sa_fk"].search(ln)
                    if fk:
                        entry["fk_table"], entry["fk_col"] = fk.group(1), fk.group(2)
                        rels.append({"from_table": ct, "from_col": cn, "to_table": fk.group(1), "to_col": fk.group(2)})
                    tbls[ct]["columns"].append(entry)

def _parse_django(src: Path, tbls: dict, rels: list):
    for pf in src.rglob("*.py"):
        try: content = pf.read_text(encoding="utf-8", errors="ignore")
        except OSError: continue
        if "models.Model" not in content: continue
        ct = None
        for ln in content.splitlines():
            cm = _RE["dj_cls"].search(ln)
            if cm: ct = cm.group(1).lower(); tbls.setdefault(ct, {"columns": []})
            mm = _RE["dj_meta"].search(ln)
            if mm and ct:
                old = ct; ct = mm.group(1)
                if old in tbls and old != ct: tbls[ct] = tbls.pop(old)
            if ct:
                fk = _RE["dj_fk"].search(ln)
                if fk:
                    cn, rt = fk.group(1), fk.group(2).lower()
                    tbls[ct]["columns"].append({"name": f"{cn}_id", "type": "INTEGER", "pk": False, "fk_table": rt, "fk_col": "id"})
                    rels.append({"from_table": ct, "from_col": f"{cn}_id", "to_table": rt, "to_col": "id"}); continue
                fm = _RE["dj_fld"].search(ln)
                if fm:
                    tbls[ct]["columns"].append({"name": fm.group(1), "type": _FT.get(fm.group(2), fm.group(2)), "pk": "primary_key=True" in ln})

def _parse_hibernate(src: Path, tbls: dict, rels: list):
    for jf in list(src.rglob("*.java")) + list(src.rglob("*.kt")):
        try: content = jf.read_text(encoding="utf-8", errors="ignore")
        except OSError: continue
        if "@Entity" not in content: continue
        cm = _RE["hb_cls"].search(content)
        if not cm: continue
        cn = cm.group(1)
        tm = _RE["hb_tbl"].search(content)
        tn = tm.group(1) if tm else cn.lower()
        tbls.setdefault(tn, {"columns": []})
        idpos = [m.start() for m in _RE["hb_id"].finditer(content)]
        for col in _RE["hb_col"].finditer(content):
            pk = any(abs(p - col.start()) < 100 and p < col.start() for p in idpos)
            tbls[tn]["columns"].append({"name": col.group(1), "type": _FT.get(col.group(2), col.group(2)), "pk": pk})
        if not tbls[tn]["columns"]:
            for fm in _RE["hb_fld"].finditer(content):
                if fm.group(1) in ("class", "interface", "void", "return", "static"): continue
                pk = any(abs(p - fm.start()) < 80 and p < fm.start() for p in idpos)
                tbls[tn]["columns"].append({"name": fm.group(2), "type": _FT.get(fm.group(1), fm.group(1)), "pk": pk})
        for m in _RE["hb_m2o"].finditer(content):
            rels.append({"from_table": tn, "from_col": f"{m.group(2)}_id", "to_table": m.group(1).lower(), "to_col": "id"})
        for m in _RE["hb_o2m"].finditer(content):
            rels.append({"from_table": m.group(1).lower(), "from_col": f"{tn}_id", "to_table": tn, "to_col": "id"})

def _parse_ef(src: Path, tbls: dict, rels: list):
    for cf in src.rglob("*.cs"):
        try: content = cf.read_text(encoding="utf-8", errors="ignore")
        except OSError: continue
        for m in _RE["ef_dbs"].finditer(content):
            tbls.setdefault(m.group(1).lower(), {"columns": []})
        cm = _RE["ef_cls"].search(content)
        if not cm: continue
        cn = cm.group(1)
        ta = _RE["ef_tbl"].search(content)
        tn = ta.group(1) if ta else cn.lower()
        props = list(_RE["ef_prop"].finditer(content))
        if not props: continue
        tbls.setdefault(tn, {"columns": []})
        kpos = [m.start() for m in _RE["ef_key"].finditer(content)]
        fkas = [(m.start(), m.group(1)) for m in _RE["ef_fk"].finditer(content)]
        for p in props:
            pt, pn = p.group(1).rstrip("?"), p.group(2)
            pk = any(abs(k - p.start()) < 80 and k < p.start() for k in kpos)
            entry = {"name": pn, "type": _FT.get(pt, pt), "pk": pk}
            for fp, fn in fkas:
                if abs(fp - p.start()) < 100 and fp < p.start():
                    entry["fk_table"], entry["fk_col"] = fn.lower(), "Id"
                    rels.append({"from_table": tn, "from_col": pn, "to_table": fn.lower(), "to_col": "Id"}); break
            tbls[tn]["columns"].append(entry)
        for m in _RE["ef_h1"].finditer(content):
            rels.append({"from_table": tn, "from_col": f"{m.group(1).lower()}_id", "to_table": m.group(1).lower(), "to_col": "Id"})
        for m in _RE["ef_hm"].finditer(content):
            rels.append({"from_table": m.group(1).lower(), "from_col": f"{tn}_id", "to_table": tn, "to_col": "Id"})

def _parse_sql(src: Path, tbls: dict, rels: list):
    skip = {"primary", "foreign", "unique", "constraint", "check", "index", "key", "create", "alter"}
    for sf in src.rglob("*.sql"):
        try: content = sf.read_text(encoding="utf-8", errors="ignore")
        except OSError: continue
        for stmt in re.split(r';\s*\n', content):
            s = stmt.strip()
            if not s: continue
            ct = _RE["sql_ct"].search(s)
            if ct:
                tn = ct.group(1).lower(); tbls.setdefault(tn, {"columns": []})
                pks = set()
                for m in _RE["sql_pki"].finditer(s): pks.add(m.group(1).lower())
                for m in _RE["sql_pkc"].finditer(s):
                    for c in m.group(1).split(","): pks.add(c.strip().strip('`"[] ').lower())
                ps, pe = s.find("("), s.rfind(")")
                if ps >= 0 and pe > ps:
                    for cm in _RE["sql_cd"].finditer(s[ps+1:pe]):
                        cn = cm.group(1).lower()
                        if cn in skip: continue
                        tbls[tn]["columns"].append({"name": cn, "type": cm.group(2).upper(), "pk": cn in pks})
                for fm in _RE["sql_fkc"].finditer(s):
                    fc, ft, tc2 = fm.group(1).lower(), fm.group(2).lower(), fm.group(3).lower()
                    for col in tbls[tn]["columns"]:
                        if col["name"] == fc: col["fk_table"] = ft; col["fk_col"] = tc2
                    rels.append({"from_table": tn, "from_col": fc, "to_table": ft, "to_col": tc2})
            for m in _RE["sql_ac"].finditer(s):
                t2 = m.group(1).lower(); tbls.setdefault(t2, {"columns": []})
                tbls[t2]["columns"].append({"name": m.group(2).lower(), "type": m.group(3).upper(), "pk": False})
            for m in _RE["sql_afk"].finditer(s):
                rels.append({"from_table": m.group(1).lower(), "from_col": m.group(2).lower(), "to_table": m.group(3).lower(), "to_col": m.group(4).lower()})

def extract_database_schema(app_id: str, source_path: str) -> dict:
    """Parse ORM models (SQLAlchemy/Django/Hibernate/JPA/EF) and SQL migrations.
    Stores discovered schema in legacy_db_schemas. Returns {tables, relationships, db_type, totals}."""
    src = Path(source_path)
    if not src.exists(): raise FileNotFoundError(f"Source path not found: {source_path}")
    conn = _get_db()
    try:
        ar = conn.execute("SELECT primary_language,framework FROM legacy_applications WHERE id=?", (app_id,)).fetchone()
        if not ar: raise ValueError(f"Application {app_id} not found in database.")
        lang, fw = (ar["primary_language"] or "").lower(), (ar["framework"] or "").lower()
        tbls, rels = {}, []
        if lang == "python":
            (_parse_django if "django" in fw else _parse_sqlalchemy)(src, tbls, rels)
        elif lang in ("java", "kotlin"): _parse_hibernate(src, tbls, rels)
        elif lang in ("csharp", "c#"): _parse_ef(src, tbls, rels)
        _parse_sql(src, tbls, rels)
        dbt = _detect_db_type(src, fw)
        for tn, ti in tbls.items():
            for col in ti.get("columns", []):
                try:
                    conn.execute("INSERT OR IGNORE INTO legacy_db_schemas (id,legacy_app_id,db_type,schema_name,table_name,column_name,data_type,is_primary_key,is_foreign_key,foreign_table,foreign_column) VALUES (?,?,?,'public',?,?,?,?,?,?,?)",
                                 (str(uuid.uuid4()), app_id, dbt, tn, col["name"], col["type"], 1 if col.get("pk") else 0, 1 if col.get("fk_table") else 0, col.get("fk_table"), col.get("fk_col")))
                except sqlite3.IntegrityError: pass
        conn.commit()
        out_t = [{"name": t, "columns": tbls[t].get("columns", [])} for t in sorted(tbls)]
        seen, ur = set(), []
        for r in rels:
            k = (r["from_table"], r["from_col"], r["to_table"], r["to_col"])
            if k not in seen: seen.add(k); ur.append(r)
        return {"tables": out_t, "relationships": ur, "db_type": dbt, "total_tables": len(out_t),
                "total_columns": sum(len(t["columns"]) for t in out_t), "total_relationships": len(ur)}
    finally:
        conn.close()

# -- 6. Architecture Summary ---------------------------------------------------
def _detect_style(tc: collections.Counter, total: int) -> str:
    """Detect architecture style from component type distribution."""
    if total == 0: return "unknown"
    has = lambda t: tc.get(t, 0) > 0
    if has("ejb") and has("servlet"): return "j2ee"
    if has("controller") and has("model") and has("view"):
        return "mvc-layered" if has("service") and has("repository") else "mvc"
    if has("controller") and has("service") and has("repository"): return "layered"
    if has("service") and has("api_endpoint") and tc.get("service", 0) >= 3: return "microservice-candidate"
    if has("controller") and (has("model") or has("entity")): return "mvc"
    if has("service") and has("repository"): return "layered"
    return "monolith"

def generate_architecture_summary(app_id: str) -> dict:
    """Aggregate all analysis into a comprehensive architecture summary.
    Returns summary dict with app info, counts, complexity distribution, coupling analysis, and style."""
    conn = _get_db()
    try:
        ar = conn.execute("SELECT name,primary_language,framework,loc_total,complexity_score,maintainability_index FROM legacy_applications WHERE id=?", (app_id,)).fetchone()
        if not ar: raise ValueError(f"Application {app_id} not found in database.")
        comps = conn.execute("SELECT component_type,cyclomatic_complexity,coupling_score FROM legacy_components WHERE legacy_app_id=?", (app_id,)).fetchall()
        dc = conn.execute("SELECT COUNT(*) as cnt FROM legacy_dependencies WHERE legacy_app_id=?", (app_id,)).fetchone()["cnt"]
        ac = conn.execute("SELECT COUNT(*) as cnt FROM legacy_apis WHERE legacy_app_id=?", (app_id,)).fetchone()["cnt"]
        tc2 = conn.execute("SELECT COUNT(DISTINCT table_name) as cnt FROM legacy_db_schemas WHERE legacy_app_id=?", (app_id,)).fetchone()["cnt"]
        hs = [{"name": h["name"], "coupling_score": h["coupling_score"]} for h in conn.execute("SELECT name,coupling_score FROM legacy_components WHERE legacy_app_id=? ORDER BY coupling_score DESC LIMIT 5", (app_id,)).fetchall()]
    finally:
        conn.close()
    cg = extract_call_graph(app_id); cd = extract_component_diagram(app_id); sb = detect_service_boundaries(app_id)
    cxd = {"low": 0, "medium": 0, "high": 0, "very_high": 0}; cvs = []
    tc = collections.Counter()
    for c in comps:
        cxd[_complexity_bucket(c["cyclomatic_complexity"] or 0)] += 1
        cvs.append(c["coupling_score"] or 0.0); tc[c["component_type"]] += 1
    return {
        "app_name": ar["name"], "language": ar["primary_language"], "framework": ar["framework"],
        "total_loc": ar["loc_total"] or 0, "total_components": len(comps), "total_dependencies": dc,
        "total_apis": ac, "total_tables": tc2, "packages": len(cd["packages"]),
        "suggested_services": len(sb["communities"]), "modularity_score": sb["modularity_score"],
        "complexity_distribution": cxd,
        "coupling_analysis": {"avg": round(sum(cvs)/len(cvs), 4) if cvs else 0.0,
                              "max": round(max(cvs), 4) if cvs else 0.0, "hotspots": hs},
        "architecture_style": _detect_style(tc, len(comps)),
        "call_graph_nodes": len(cg["nodes"]), "call_graph_edges": len(cg["edges"])}

# -- Text Formatters (ASCII diagrams) -----------------------------------------
def _fmt_cg(d):
    ls = ["CUI // SP-CTI", "", "=" * 70, "  CALL GRAPH", "=" * 70, "",
          f"  Nodes: {len(d['nodes'])}    Edges: {len(d['edges'])}", ""]
    if d["nodes"]:
        ls += ["  COMPONENTS:", "  " + "-" * 66,
               f"  {'ID':<12} {'Name':<25} {'Type':<15} {'LOC':>6} {'Cmplx':>6}", "  " + "-" * 66]
        for n in sorted(d["nodes"], key=lambda x: x["name"]):
            ls.append(f"  {n['id'][:10]:<12} {n['name'][:23]:<25} {n['type']:<15} {n['loc']:>6} {n['complexity']:>6.1f}")
        ls.append("")
    if d["edges"]:
        nm = {n["id"]: n["name"] for n in d["nodes"]}
        by_s = collections.defaultdict(list)
        for e in d["edges"]: by_s[e["source"]].append(e)
        ls += ["  DEPENDENCIES:", "  " + "-" * 66]
        for sid, es in sorted(by_s.items(), key=lambda x: nm.get(x[0], "")):
            for e in es:
                ar = "-->" if e["type"] == "method_call" else "..>"
                ls.append(f"  [{nm.get(sid, sid[:10])[:20]}] {ar} [{nm.get(e['target'], e['target'][:10])[:20]}]  ({e['type']}, w={e['weight']:.1f})")
        ls.append("")
    ls += ["=" * 70, "CUI // SP-CTI"]; return "\n".join(ls)

def _fmt_cd(d):
    ls = ["CUI // SP-CTI", "", "=" * 70, "  COMPONENT DIAGRAM", "=" * 70, "",
          f"  Packages: {len(d['packages'])}    Inter-package edges: {len(d['inter_package_edges'])}", ""]
    for p in d["packages"]:
        w = max(42, len(p["name"]) + 6)
        ls += ["  +" + "-" * w + "+", f"  | {p['name']:<{w-2}} |", "  +" + "-" * w + "+"]
        for c in p["components"]:
            ls.append(f"  |  [{c['type'][:12]}] {c['name'][:w-20]:<{w-18}}|")
        ls += [f"  | int:{p['internal_deps']} ext:{p['external_deps']}" + " " * max(0, w - 18 - len(str(p['internal_deps'])) - len(str(p['external_deps']))) + "|",
               "  +" + "-" * w + "+", ""]
    if d["inter_package_edges"]:
        ls += ["  INTER-PACKAGE DEPS:", "  " + "-" * 50]
        for e in d["inter_package_edges"]:
            ls.append(f"  {e['source_pkg'][:20]:<22} --> {e['target_pkg'][:20]:<22} (x{e['count']})")
        ls.append("")
    ls += ["=" * 70, "CUI // SP-CTI"]; return "\n".join(ls)

def _fmt_df(d):
    ls = ["CUI // SP-CTI", "", "=" * 70, "  DATA FLOW ANALYSIS", "=" * 70, "",
          f"  API Endpoints Traced: {len(d['flows'])}", ""]
    for f in d["flows"]:
        db = " [DB]" if f["reaches_database"] else ""
        ls.append(f"  {f['method'] or 'ANY'} {f['path']}{db}")
        if f["chain"]:
            for i, s in enumerate(f["chain"]):
                cn = "|-->" if i < len(f["chain"]) - 1 else "`-->"
                ls.append(f"    {cn} [{s['component_type']}] {s['component_name']}")
        else: ls.append("    (no component chain traced)")
        ls.append("")
    ls += ["=" * 70, "CUI // SP-CTI"]; return "\n".join(ls)

def _fmt_sb(d):
    ls = ["CUI // SP-CTI", "", "=" * 70, "  SERVICE BOUNDARY DETECTION", "=" * 70, "",
          f"  Modularity Score (Q): {d['modularity_score']:.4f}",
          f"  Suggested Services:   {len(d['communities'])}", ""]
    for i, c in enumerate(d["communities"], 1):
        ls += [f"  Service {i}: {c['suggested_service_name']}",
               f"    Cohesion: {c['cohesion']:.4f}  Coupling: {c['coupling']:.4f}",
               f"    Components ({len(c['components'])}):" ]
        for cid in c["components"][:10]: ls.append(f"      - {cid}")
        if len(c["components"]) > 10: ls.append(f"      ... and {len(c['components'])-10} more")
        ls.append("")
    ls += ["=" * 70, "CUI // SP-CTI"]; return "\n".join(ls)

def _fmt_ds(d):
    ls = ["CUI // SP-CTI", "", "=" * 70, "  DATABASE SCHEMA", "=" * 70, "",
          f"  DB Type: {d.get('db_type','unknown')}",
          f"  Tables: {d['total_tables']}  Columns: {d['total_columns']}  Relationships: {d['total_relationships']}", ""]
    for t in d["tables"]:
        w = max(45, len(t["name"]) + 10)
        ls += ["  +" + "=" * w + "+", f"  | {t['name'].upper():<{w-2}} |", "  +" + "-" * w + "+"]
        for c in t["columns"]:
            pk = " [PK]" if c.get("pk") else ""
            fk = f" -> {c['fk_table']}.{c.get('fk_col','?')}" if c.get("fk_table") else ""
            ls.append(f"  | {c['name']} : {c['type']}{pk}{fk}" + " " * max(0, w - 4 - len(c['name']) - len(c['type']) - len(pk) - len(fk)) + " |")
        ls += ["  +" + "=" * w + "+", ""]
    if d["relationships"]:
        ls += ["  FOREIGN KEY RELATIONSHIPS:", "  " + "-" * 50]
        for r in d["relationships"]: ls.append(f"  {r['from_table']}.{r['from_col']} --> {r['to_table']}.{r['to_col']}")
        ls.append("")
    ls += ["=" * 70, "CUI // SP-CTI"]; return "\n".join(ls)

def _fmt_sm(d):
    ls = ["CUI // SP-CTI", "", "=" * 70, "  ARCHITECTURE SUMMARY", "=" * 70, "",
          f"  Application:   {d['app_name']}", f"  Language:       {d['language']}",
          f"  Framework:      {d['framework']}", f"  Architecture:   {d['architecture_style']}", "",
          "  METRICS:",
          f"    Total LOC:          {d['total_loc']:>8,}", f"    Components:         {d['total_components']:>8,}",
          f"    Dependencies:       {d['total_dependencies']:>8,}", f"    API Endpoints:      {d['total_apis']:>8,}",
          f"    Database Tables:    {d['total_tables']:>8,}", f"    Packages:           {d['packages']:>8,}",
          f"    Suggested Services: {d['suggested_services']:>8,}",
          f"    Modularity (Q):     {d['modularity_score']:>8.4f}", "", "  COMPLEXITY DISTRIBUTION:"]
    cd = d["complexity_distribution"]; tot = sum(cd.values()) or 1
    for b in ("low", "medium", "high", "very_high"):
        c = cd[b]; pct = (c / tot) * 100
        ls.append(f"    {b:<10} {c:>5} ({pct:>5.1f}%) {'#' * int(pct / 2)}")
    ca = d["coupling_analysis"]
    ls += ["", "  COUPLING ANALYSIS:", f"    Average: {ca['avg']:.4f}    Maximum: {ca['max']:.4f}"]
    if ca["hotspots"]:
        ls.append("    Hotspots:")
        for h in ca["hotspots"]: ls.append(f"      - {h['name']}: {h['coupling_score']:.4f}")
    ls += ["", "  CALL GRAPH:", f"    Nodes: {d['call_graph_nodes']}    Edges: {d['call_graph_edges']}",
           "", "=" * 70, "CUI // SP-CTI"]
    return "\n".join(ls)

# -- CLI -----------------------------------------------------------------------
_FMTS = {"call-graph": _fmt_cg, "component-diagram": _fmt_cd, "data-flow": _fmt_df,
         "service-boundaries": _fmt_sb, "db-schema": _fmt_ds, "summary": _fmt_sm}
_EXT = {"call-graph": lambda a: extract_call_graph(a.app_id),
        "component-diagram": lambda a: extract_component_diagram(a.app_id),
        "data-flow": lambda a: extract_data_flow(a.app_id),
        "service-boundaries": lambda a: detect_service_boundaries(a.app_id),
        "db-schema": lambda a: extract_database_schema(a.app_id, a.source_path),
        "summary": lambda a: generate_architecture_summary(a.app_id)}

def main():
    """CLI entry point for the ICDEV Architecture Extractor."""
    ap = argparse.ArgumentParser(description="CUI // SP-CTI -- ICDEV Architecture Extractor",
                                 epilog="Classification: CUI // SP-CTI")
    ap.add_argument("--app-id", required=True, help="Legacy application ID to analyze.")
    ap.add_argument("--extract", required=True, choices=list(_EXT.keys()), help="Extraction type.")
    ap.add_argument("--source-path", help="Source code path (required for db-schema).")
    ap.add_argument("--json", action="store_true", default=False, help="Output as JSON.")
    ap.add_argument("--output-dir", help="Directory to write output files to.")
    args = ap.parse_args()
    if args.extract == "db-schema" and not args.source_path:
        ap.error("--source-path is required for db-schema extraction.")
    try:
        result = _EXT[args.extract](args)
    except FileNotFoundError as e:
        print(f"ERROR: {e}"); raise SystemExit(1)
    except ValueError as e:
        print(f"ERROR: {e}"); raise SystemExit(1)
    except sqlite3.Error as e:
        print(f"DATABASE ERROR: {e}"); raise SystemExit(1)
    output = json.dumps(result, indent=2, default=str) if args.json else _FMTS[args.extract](result)
    if args.output_dir:
        od = Path(args.output_dir); od.mkdir(parents=True, exist_ok=True)
        ext = ".json" if args.json else ".txt"
        op = od / f"architecture_{args.extract.replace('-', '_')}{ext}"
        op.write_text(output, encoding="utf-8"); print(f"Output written to: {op}")
    else:
        print(output)

if __name__ == "__main__":
    main()
# CUI // SP-CTI
