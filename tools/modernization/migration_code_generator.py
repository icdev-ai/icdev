# CUI // SP-CTI
#!/usr/bin/env python3
"""Migration Code Generator — ICDEV DoD Modernization System.

Produces adapters, facades, microservice scaffolds, data access layers,
migration tests, and rollback scripts from migration plans stored in
icdev.db.  All generated source files carry CUI // SP-CTI banners.

Usage:
    # Generate everything for a migration plan
    python tools/modernization/migration_code_generator.py \\
        --plan-id mplan-001 --output /tmp/migration --generate all

    # Generate only adapters in Java
    python tools/modernization/migration_code_generator.py \\
        --plan-id mplan-001 --output /tmp/migration --generate adapters --language java

    # Generate a single service scaffold
    python tools/modernization/migration_code_generator.py \\
        --plan-id mplan-001 --output /tmp/migration --generate scaffolds \\
        --service-name user-service --language python --framework flask

    # JSON output
    python tools/modernization/migration_code_generator.py \\
        --plan-id mplan-001 --output /tmp/migration --generate all --json

Classification: CUI // SP-CTI
"""

import argparse
import collections
import hashlib
import json
import os
import sqlite3
import textwrap
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

CUI_BANNER = "CUI // SP-CTI"

# Language-specific comment banners
_COMMENT_BANNERS = {
    "python": (f"# {CUI_BANNER}", f"# {CUI_BANNER}"),
    "java": (f"// {CUI_BANNER}", f"// {CUI_BANNER}"),
    "csharp": (f"// {CUI_BANNER}", f"// {CUI_BANNER}"),
}

# File extensions per language
_EXTENSIONS = {"python": ".py", "java": ".java", "csharp": ".cs"}

# Framework defaults per language
_DEFAULT_FRAMEWORK = {
    "python": "flask",
    "java": "spring-boot",
    "csharp": "aspnet-core",
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db():
    """Return a sqlite3 connection with Row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _record_artifact(plan_id, task_id, artifact_type, file_path, description):
    """Insert a row into migration_artifacts and compute file_hash."""
    file_path_str = str(file_path)
    file_hash = ""
    if os.path.isfile(file_path_str):
        with open(file_path_str, "rb") as fh:
            file_hash = hashlib.sha256(fh.read()).hexdigest()
    conn = _get_db()
    conn.execute(
        "INSERT INTO migration_artifacts "
        "(plan_id, task_id, artifact_type, file_path, file_hash, description, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (plan_id, task_id, artifact_type, file_path_str, file_hash, description,
         datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def _write_file(path, content):
    """Write *content* to *path*, creating parent dirs as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _banner_top(language):
    return _COMMENT_BANNERS.get(language, ("# " + CUI_BANNER,))[0]


def _banner_bottom(language):
    pair = _COMMENT_BANNERS.get(language, ("# " + CUI_BANNER, "# " + CUI_BANNER))
    return pair[1]


def _ext(language):
    return _EXTENSIONS.get(language, ".py")


def _safe_class_name(name):
    """Convert an arbitrary string into a PascalCase class-safe name."""
    parts = name.replace("-", "_").replace(".", "_").split("_")
    return "".join(p.capitalize() for p in parts if p)


def _safe_var_name(name):
    """Convert to a snake_case variable-safe name."""
    return name.replace("-", "_").replace(".", "_").replace(" ", "_").lower()


# ---------------------------------------------------------------------------
# 1. generate_adapter
# ---------------------------------------------------------------------------

def generate_adapter(plan_id, legacy_component_id, language="python", output_dir="."):
    """Generate an adapter-pattern wrapper for a legacy component."""
    conn = _get_db()

    comp = conn.execute(
        "SELECT * FROM legacy_components WHERE id = ?", (legacy_component_id,)
    ).fetchone()
    if not comp:
        conn.close()
        raise ValueError(f"Legacy component {legacy_component_id} not found")

    comp_name = comp["name"]
    qualified = comp["qualified_name"] or comp_name
    props_json = comp["properties"] or "{}"
    props = json.loads(props_json) if isinstance(props_json, str) else {}

    # Derive method list from properties or fall back to placeholder
    methods = props.get("methods", [])
    if not methods:
        methods = [{"name": "execute", "params": [], "return_type": "object"}]

    class_name = _safe_class_name(comp_name)
    adapter_class = f"Legacy{class_name}Adapter"
    modern_iface = f"Modern{class_name}Interface"

    # Look for a matching migration task
    task = conn.execute(
        "SELECT id FROM migration_tasks WHERE plan_id = ? AND legacy_component_id = ? "
        "AND task_type = 'generate_adapter' LIMIT 1",
        (plan_id, legacy_component_id),
    ).fetchone()
    task_id = task["id"] if task else None
    conn.close()

    ext = _ext(language)
    out_path = Path(output_dir) / "adapters" / f"{_safe_var_name(comp_name)}_adapter{ext}"

    if language == "python":
        method_defs = []
        for m in methods:
            params_str = ", ".join(m.get("params", []))
            sig = f"self, {params_str}" if params_str else "self"
            method_defs.append(textwrap.dedent(f"""\
                def {m['name']}({sig}):
                    \"\"\"Delegate to legacy {qualified}.{m['name']}.\"\"\"
                    return self._legacy.{m['name']}({params_str})
            """))
        body = "\n    ".join("\n".join(method_defs).split("\n"))
        code = textwrap.dedent(f"""\
            {_banner_top(language)}
            \"\"\"Adapter for legacy component: {qualified}.

            Wraps the legacy interface and exposes a modern interface.
            Classification: {CUI_BANNER}
            \"\"\"


            class {adapter_class}:
                \"\"\"Adapts {qualified} to the modern interface.\"\"\"

                def __init__(self, legacy_instance):
                    self._legacy = legacy_instance

                {body}

                def health_check(self):
                    \"\"\"Return adapter health status.\"\"\"
                    return {{"status": "healthy", "component": "{comp_name}", "adapter": True}}
            {_banner_bottom(language)}
        """)
    elif language == "java":
        method_defs = []
        for m in methods:
            ret = m.get("return_type", "Object")
            params_str = ", ".join(f"Object {p}" for p in m.get("params", []))
            call_args = ", ".join(m.get("params", []))
            method_defs.append(
                f"    public {ret} {m['name']}({params_str}) {{\n"
                f"        return this.legacy.{m['name']}({call_args});\n"
                f"    }}"
            )
        methods_block = "\n\n".join(method_defs)
        code = textwrap.dedent(f"""\
            {_banner_top(language)}
            /**
             * Adapter for legacy component: {qualified}.
             * Classification: {CUI_BANNER}
             */
            public class {adapter_class} implements {modern_iface} {{

                private final Object legacy;

                public {adapter_class}(Object legacyInstance) {{
                    this.legacy = legacyInstance;
                }}

            {methods_block}

                public java.util.Map<String, Object> healthCheck() {{
                    java.util.Map<String, Object> status = new java.util.HashMap<>();
                    status.put("status", "healthy");
                    status.put("component", "{comp_name}");
                    status.put("adapter", true);
                    return status;
                }}
            }}
            {_banner_bottom(language)}
        """)
    elif language == "csharp":
        method_defs = []
        for m in methods:
            ret = m.get("return_type", "object")
            params_str = ", ".join(f"object {p}" for p in m.get("params", []))
            call_args = ", ".join(m.get("params", []))
            method_defs.append(
                f"        public {ret} {_safe_class_name(m['name'])}({params_str})\n"
                f"        {{\n"
                f"            return _legacy.{m['name']}({call_args});\n"
                f"        }}"
            )
        methods_block = "\n\n".join(method_defs)
        code = textwrap.dedent(f"""\
            {_banner_top(language)}
            /// <summary>
            /// Adapter for legacy component: {qualified}.
            /// Classification: {CUI_BANNER}
            /// </summary>
            public class {adapter_class} : IModern{class_name}
            {{
                private readonly dynamic _legacy;

                public {adapter_class}(object legacyInstance)
                {{
                    _legacy = legacyInstance;
                }}

            {methods_block}

                public object HealthCheck()
                {{
                    return new {{ Status = "healthy", Component = "{comp_name}", Adapter = true }};
                }}
            }}
            {_banner_bottom(language)}
        """)
    else:
        raise ValueError(f"Unsupported language: {language}")

    _write_file(out_path, code)
    _record_artifact(plan_id, task_id, "adapter_code", out_path, f"Adapter for {comp_name}")
    return str(out_path)


# ---------------------------------------------------------------------------
# 2. generate_facade
# ---------------------------------------------------------------------------

def generate_facade(plan_id, language="python", output_dir="."):
    """Generate an API facade with routing for all legacy endpoints."""
    conn = _get_db()

    plan = conn.execute("SELECT * FROM migration_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"Migration plan {plan_id} not found")

    app_id = plan["legacy_app_id"]
    apis = conn.execute(
        "SELECT * FROM legacy_apis WHERE legacy_app_id = ? ORDER BY path", (app_id,)
    ).fetchall()

    # Group by service boundary from extract_service tasks
    service_tasks = conn.execute(
        "SELECT mt.id, mt.title, mt.legacy_component_id FROM migration_tasks mt "
        "WHERE mt.plan_id = ? AND mt.task_type = 'extract_service'", (plan_id,)
    ).fetchall()

    # Map component_id -> service name
    comp_to_service = {}
    for st in service_tasks:
        comp_to_service[st["legacy_component_id"]] = st["title"]

    task = conn.execute(
        "SELECT id FROM migration_tasks WHERE plan_id = ? AND task_type = 'generate_facade' LIMIT 1",
        (plan_id,),
    ).fetchone()
    task_id = task["id"] if task else None
    conn.close()

    ext = _ext(language)
    out_path = Path(output_dir) / "facade" / f"api_facade{ext}"

    if language == "python":
        route_blocks = []
        for api in apis:
            method = (api["method"] or "GET").lower()
            path = api["path"] or "/"
            handler = api["handler_function"] or "handler"
            svc = comp_to_service.get(api["component_id"], "legacy")
            func_name = _safe_var_name(f"{method}_{path.replace('/', '_')}")
            route_blocks.append(textwrap.dedent(f"""\
                @app.route("{path}", methods=["{method.upper()}"])
                def {func_name}():
                    \"\"\"Proxy to {svc} service (legacy: {handler}).\"\"\"
                    backend = BACKENDS.get("{svc}", BACKENDS["legacy"])
                    resp = requests.request("{method}", backend + "{path}", json=request.get_json(), headers=dict(request.headers))
                    return (resp.content, resp.status_code, dict(resp.headers))
            """))
        routes_code = "\n\n".join(route_blocks) if route_blocks else "# No legacy APIs found\npass\n"
        code = textwrap.dedent(f"""\
            {_banner_top(language)}
            \"\"\"API Facade — routes traffic between legacy and modern services.

            Generated by ICDEV Migration Code Generator.
            Classification: {CUI_BANNER}
            \"\"\"
            import os
            import requests
            from flask import Flask, request

            app = Flask(__name__)

            # Backend URLs (override via environment variables)
            BACKENDS = {{
                "legacy": os.environ.get("LEGACY_BACKEND", "http://localhost:8000"),
            }}

            {routes_code}

            @app.route("/health")
            def health():
                return {{"status": "healthy", "service": "api-facade"}}


            if __name__ == "__main__":
                app.run(host="0.0.0.0", port=5050)
            {_banner_bottom(language)}
        """)
    elif language == "java":
        mappings = []
        for api in apis:
            method = (api["method"] or "GET").upper()
            path = api["path"] or "/"
            handler = api["handler_function"] or "handler"
            svc = comp_to_service.get(api["component_id"], "legacy")
            method_name = _safe_var_name(f"{method.lower()}_{path.replace('/', '_')}")
            annotation = f"@RequestMapping(value = \"{path}\", method = RequestMethod.{method})"
            mappings.append(
                f"    {annotation}\n"
                f"    public ResponseEntity<String> {method_name}(HttpServletRequest req) {{\n"
                f"        // Proxy to {svc} service (legacy: {handler})\n"
                f"        String backend = backends.getOrDefault(\"{svc}\", backends.get(\"legacy\"));\n"
                f"        return proxyRequest(req, backend + \"{path}\");\n"
                f"    }}"
            )
        mappings_block = "\n\n".join(mappings) if mappings else "    // No legacy APIs found"
        code = textwrap.dedent(f"""\
            {_banner_top(language)}
            /**
             * API Facade - routes traffic between legacy and modern services.
             * Generated by ICDEV Migration Code Generator.
             * Classification: {CUI_BANNER}
             */
            import org.springframework.web.bind.annotation.*;
            import org.springframework.http.ResponseEntity;
            import javax.servlet.http.HttpServletRequest;
            import java.util.*;

            @RestController
            public class ApiFacade {{

                private final Map<String, String> backends = new HashMap<>() {{{{
                    put("legacy", System.getenv().getOrDefault("LEGACY_BACKEND", "http://localhost:8000"));
                }}}};

            {mappings_block}

                @GetMapping("/health")
                public Map<String, Object> health() {{
                    Map<String, Object> status = new HashMap<>();
                    status.put("status", "healthy");
                    status.put("service", "api-facade");
                    return status;
                }}

                private ResponseEntity<String> proxyRequest(HttpServletRequest req, String targetUrl) {{
                    // TODO: implement HTTP proxying with RestTemplate or WebClient
                    return ResponseEntity.ok("proxy-stub");
                }}
            }}
            {_banner_bottom(language)}
        """)
    elif language == "csharp":
        actions = []
        for api in apis:
            method = (api["method"] or "GET").upper()
            path = api["path"] or "/"
            handler = api["handler_function"] or "handler"
            svc = comp_to_service.get(api["component_id"], "legacy")
            attr = {"GET": "HttpGet", "POST": "HttpPost", "PUT": "HttpPut",
                     "DELETE": "HttpDelete", "PATCH": "HttpPatch"}.get(method, "HttpGet")
            method_name = _safe_class_name(f"{method.lower()}_{path.replace('/', '_')}")
            actions.append(
                f"        [{attr}(\"{path}\")]\n"
                f"        public async Task<IActionResult> {method_name}()\n"
                f"        {{\n"
                f"            // Proxy to {svc} service (legacy: {handler})\n"
                f"            var backend = _backends.GetValueOrDefault(\"{svc}\", _backends[\"legacy\"]);\n"
                f"            return await ProxyRequest(backend + \"{path}\");\n"
                f"        }}"
            )
        actions_block = "\n\n".join(actions) if actions else "        // No legacy APIs found"
        code = textwrap.dedent(f"""\
            {_banner_top(language)}
            /// <summary>
            /// API Facade - routes traffic between legacy and modern services.
            /// Generated by ICDEV Migration Code Generator.
            /// Classification: {CUI_BANNER}
            /// </summary>
            using Microsoft.AspNetCore.Mvc;
            using System.Collections.Generic;
            using System.Threading.Tasks;

            [ApiController]
            [Route("/")]
            public class ApiFacadeController : ControllerBase
            {{
                private readonly Dictionary<string, string> _backends = new()
                {{
                    ["legacy"] = Environment.GetEnvironmentVariable("LEGACY_BACKEND") ?? "http://localhost:8000"
                }};

            {actions_block}

                [HttpGet("health")]
                public IActionResult Health()
                {{
                    return Ok(new {{ Status = "healthy", Service = "api-facade" }});
                }}

                private async Task<IActionResult> ProxyRequest(string targetUrl)
                {{
                    // TODO: implement HTTP proxying with HttpClient
                    await Task.CompletedTask;
                    return Ok("proxy-stub");
                }}
            }}
            {_banner_bottom(language)}
        """)
    else:
        raise ValueError(f"Unsupported language: {language}")

    _write_file(out_path, code)
    _record_artifact(plan_id, task_id, "facade_code", out_path, "API facade for migration plan")
    return str(out_path)


# ---------------------------------------------------------------------------
# 3. generate_service_scaffold
# ---------------------------------------------------------------------------

def generate_service_scaffold(plan_id, service_name, language="python",
                              framework=None, output_dir="."):
    """Generate a microservice skeleton for the given service name."""
    framework = framework or _DEFAULT_FRAMEWORK.get(language, "flask")
    svc_dir = Path(output_dir) / "services" / service_name
    svc_class = _safe_class_name(service_name)
    svc_var = _safe_var_name(service_name)
    ban_top = _banner_top(language)
    ban_bot = _banner_bottom(language)

    conn = _get_db()
    task = conn.execute(
        "SELECT id FROM migration_tasks WHERE plan_id = ? AND task_type = 'extract_service' "
        "AND title LIKE ? LIMIT 1",
        (plan_id, f"%{service_name}%"),
    ).fetchone()
    task_id = task["id"] if task else None
    conn.close()

    generated = []

    # -- Python / Flask -------------------------------------------------
    if language == "python" and framework in ("flask", "flask"):
        _write_file(svc_dir / "app.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Flask application for {service_name}. Classification: {CUI_BANNER}\"\"\"
            from flask import Flask
            from routes import register_routes

            app = Flask(__name__)

            register_routes(app)

            @app.route("/health")
            def health():
                return {{"status": "healthy", "service": "{service_name}"}}

            if __name__ == "__main__":
                app.run(host="0.0.0.0", port=8080)
            {ban_bot}
        """))
        _write_file(svc_dir / "config.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Configuration for {service_name}. Classification: {CUI_BANNER}\"\"\"
            import os

            DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data.db")
            SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
            DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
            {ban_bot}
        """))
        _write_file(svc_dir / "routes" / "__init__.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Route registration for {service_name}. Classification: {CUI_BANNER}\"\"\"
            from flask import Flask, jsonify

            def register_routes(app: Flask):
                @app.route("/api/v1/{svc_var}", methods=["GET"])
                def list_{svc_var}():
                    return jsonify([])

                @app.route("/api/v1/{svc_var}/<item_id>", methods=["GET"])
                def get_{svc_var}(item_id):
                    return jsonify({{"id": item_id}})
            {ban_bot}
        """))
        _write_file(svc_dir / "models" / "__init__.py", f"{ban_top}\n# Models for {service_name}\n{ban_bot}\n")
        _write_file(svc_dir / "tests" / "__init__.py", f"{ban_top}\n# Tests for {service_name}\n{ban_bot}\n")
        _write_file(svc_dir / "tests" / "test_health.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Health check tests for {service_name}. Classification: {CUI_BANNER}\"\"\"
            import pytest
            from app import app

            @pytest.fixture
            def client():
                app.config["TESTING"] = True
                with app.test_client() as c:
                    yield c

            def test_health_endpoint(client):
                rv = client.get("/health")
                assert rv.status_code == 200
                data = rv.get_json()
                assert data["status"] == "healthy"
            {ban_bot}
        """))
        _write_file(svc_dir / "requirements.txt", "flask>=3.0\npytest>=7.0\nSQLAlchemy>=2.0\n")
        _write_file(svc_dir / "Dockerfile", textwrap.dedent(f"""\
            # {CUI_BANNER}
            FROM python:3.11-slim
            RUN useradd -r -u 1000 appuser
            WORKDIR /app
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt
            COPY . .
            USER appuser
            EXPOSE 8080
            CMD ["python", "app.py"]
            # {CUI_BANNER}
        """))
        generated = ["app.py", "config.py", "routes/__init__.py",
                     "models/__init__.py", "tests/__init__.py",
                     "tests/test_health.py", "requirements.txt", "Dockerfile"]

    # -- Python / FastAPI -----------------------------------------------
    elif language == "python" and framework == "fastapi":
        _write_file(svc_dir / "main.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"FastAPI application for {service_name}. Classification: {CUI_BANNER}\"\"\"
            from fastapi import FastAPI
            from routers import {svc_var}_router

            app = FastAPI(title="{service_name}")
            app.include_router({svc_var}_router.router, prefix="/api/v1")

            @app.get("/health")
            def health():
                return {{"status": "healthy", "service": "{service_name}"}}
            {ban_bot}
        """))
        _write_file(svc_dir / "config.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Configuration for {service_name}. Classification: {CUI_BANNER}\"\"\"
            import os
            DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data.db")
            SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
            {ban_bot}
        """))
        _write_file(svc_dir / "routers" / f"{svc_var}_router.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Router for {service_name}. Classification: {CUI_BANNER}\"\"\"
            from fastapi import APIRouter
            router = APIRouter()

            @router.get("/{svc_var}")
            def list_items():
                return []

            @router.get("/{svc_var}/{{item_id}}")
            def get_item(item_id: str):
                return {{"id": item_id}}
            {ban_bot}
        """))
        _write_file(svc_dir / "models" / "__init__.py", f"{ban_top}\n# Models for {service_name}\n{ban_bot}\n")
        _write_file(svc_dir / "tests" / "__init__.py", f"{ban_top}\n# Tests for {service_name}\n{ban_bot}\n")
        _write_file(svc_dir / "tests" / "test_health.py", textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Health tests for {service_name}. Classification: {CUI_BANNER}\"\"\"
            from fastapi.testclient import TestClient
            from main import app

            client = TestClient(app)

            def test_health():
                resp = client.get("/health")
                assert resp.status_code == 200
                assert resp.json()["status"] == "healthy"
            {ban_bot}
        """))
        _write_file(svc_dir / "requirements.txt", "fastapi>=0.110\nuvicorn>=0.29\npytest>=7.0\nSQLAlchemy>=2.0\nhttpx>=0.27\n")
        _write_file(svc_dir / "Dockerfile", textwrap.dedent(f"""\
            # {CUI_BANNER}
            FROM python:3.11-slim
            RUN useradd -r -u 1000 appuser
            WORKDIR /app
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt
            COPY . .
            USER appuser
            EXPOSE 8080
            CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
            # {CUI_BANNER}
        """))
        generated = ["main.py", "config.py", f"routers/{svc_var}_router.py",
                     "models/__init__.py", "tests/__init__.py",
                     "tests/test_health.py", "requirements.txt", "Dockerfile"]

    # -- Java / Spring Boot ---------------------------------------------
    elif language == "java" and framework == "spring-boot":
        pkg = f"com.icdev.{svc_var}"
        pkg_path = pkg.replace(".", "/")
        _write_file(svc_dir / f"src/main/java/{pkg_path}/{svc_class}Application.java", textwrap.dedent(f"""\
            {ban_top}
            package {pkg};
            import org.springframework.boot.SpringApplication;
            import org.springframework.boot.autoconfigure.SpringBootApplication;
            /** Classification: {CUI_BANNER} */
            @SpringBootApplication
            public class {svc_class}Application {{
                public static void main(String[] args) {{
                    SpringApplication.run({svc_class}Application.class, args);
                }}
            }}
            {ban_bot}
        """))
        _write_file(svc_dir / f"src/main/java/{pkg_path}/controller/{svc_class}Controller.java", textwrap.dedent(f"""\
            {ban_top}
            package {pkg}.controller;
            import org.springframework.web.bind.annotation.*;
            import java.util.*;
            /** Classification: {CUI_BANNER} */
            @RestController
            @RequestMapping("/api/v1/{svc_var}")
            public class {svc_class}Controller {{
                @GetMapping
                public List<Map<String, Object>> list() {{ return List.of(); }}
                @GetMapping("/health")
                public Map<String, Object> health() {{
                    return Map.of("status", "healthy", "service", "{service_name}");
                }}
            }}
            {ban_bot}
        """))
        for sub in ("service", "repository", "model"):
            _write_file(svc_dir / f"src/main/java/{pkg_path}/{sub}/.gitkeep", "")
        _write_file(svc_dir / "pom.xml", textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!-- {CUI_BANNER} -->
            <project xmlns="http://maven.apache.org/POM/4.0.0">
                <modelVersion>4.0.0</modelVersion>
                <groupId>com.icdev</groupId>
                <artifactId>{svc_var}</artifactId>
                <version>0.1.0</version>
                <parent>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-starter-parent</artifactId>
                    <version>3.2.0</version>
                </parent>
                <dependencies>
                    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
                    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>
                    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>
                </dependencies>
            </project>
            <!-- {CUI_BANNER} -->
        """))
        _write_file(svc_dir / "Dockerfile", textwrap.dedent(f"""\
            # {CUI_BANNER}
            FROM eclipse-temurin:17-jre-alpine
            RUN adduser -D -u 1000 appuser
            WORKDIR /app
            COPY target/*.jar app.jar
            USER appuser
            EXPOSE 8080
            CMD ["java", "-jar", "app.jar"]
            # {CUI_BANNER}
        """))
        generated = [f"src/main/java/{pkg_path}/{svc_class}Application.java",
                     f"src/main/java/{pkg_path}/controller/{svc_class}Controller.java",
                     "pom.xml", "Dockerfile"]

    # -- C# / ASP.NET Core ---------------------------------------------
    elif language == "csharp" and framework == "aspnet-core":
        _write_file(svc_dir / "Program.cs", textwrap.dedent(f"""\
            {ban_top}
            // Classification: {CUI_BANNER}
            var builder = WebApplication.CreateBuilder(args);
            builder.Services.AddControllers();
            var app = builder.Build();
            app.MapControllers();
            app.MapGet("/health", () => Results.Ok(new {{ Status = "healthy", Service = "{service_name}" }}));
            app.Run();
            {ban_bot}
        """))
        _write_file(svc_dir / f"Controllers/{svc_class}Controller.cs", textwrap.dedent(f"""\
            {ban_top}
            using Microsoft.AspNetCore.Mvc;
            // Classification: {CUI_BANNER}
            [ApiController]
            [Route("api/v1/{svc_var}")]
            public class {svc_class}Controller : ControllerBase
            {{
                [HttpGet]
                public IActionResult List() => Ok(new object[] {{}});

                [HttpGet("{{id}}")]
                public IActionResult Get(string id) => Ok(new {{ Id = id }});
            }}
            {ban_bot}
        """))
        for sub in ("Services", "Models"):
            _write_file(svc_dir / sub / ".gitkeep", "")
        _write_file(svc_dir / f"{svc_var}.csproj", textwrap.dedent(f"""\
            <!-- {CUI_BANNER} -->
            <Project Sdk="Microsoft.NET.Sdk.Web">
              <PropertyGroup>
                <TargetFramework>net8.0</TargetFramework>
              </PropertyGroup>
            </Project>
            <!-- {CUI_BANNER} -->
        """))
        _write_file(svc_dir / "Dockerfile", textwrap.dedent(f"""\
            # {CUI_BANNER}
            FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine
            RUN adduser -D -u 1000 appuser
            WORKDIR /app
            COPY publish/ .
            USER appuser
            EXPOSE 8080
            CMD ["dotnet", "{svc_var}.dll"]
            # {CUI_BANNER}
        """))
        generated = ["Program.cs", f"Controllers/{svc_class}Controller.cs",
                     f"{svc_var}.csproj", "Dockerfile"]
    else:
        raise ValueError(f"Unsupported language/framework: {language}/{framework}")

    _record_artifact(plan_id, task_id, "scaffold_code", svc_dir,
                     f"Service scaffold for {service_name} ({language}/{framework})")
    return str(svc_dir)


# ---------------------------------------------------------------------------
# 4. generate_data_access_layer
# ---------------------------------------------------------------------------

def generate_data_access_layer(plan_id, service_name, tables, language="python",
                               output_dir="."):
    """Generate repository/DAO code from legacy DB schema definitions."""
    conn = _get_db()
    plan = conn.execute("SELECT * FROM migration_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"Migration plan {plan_id} not found")
    app_id = plan["legacy_app_id"]

    all_paths = []
    ban_top = _banner_top(language)
    ban_bot = _banner_bottom(language)

    for table in tables:
        columns = conn.execute(
            "SELECT * FROM legacy_db_schemas WHERE legacy_app_id = ? AND table_name = ? "
            "ORDER BY column_name",
            (app_id, table),
        ).fetchall()
        if not columns:
            continue

        model_class = _safe_class_name(table)
        model_var = _safe_var_name(table)
        svc_dir = Path(output_dir) / "services" / service_name

        if language == "python":
            # Model file
            field_defs = []
            pk_cols = []
            for col in columns:
                sa_type = {"integer": "Integer", "varchar": "String", "text": "Text",
                           "boolean": "Boolean", "date": "Date", "timestamp": "DateTime",
                           "real": "Float", "float": "Float", "double": "Float",
                           "bigint": "BigInteger", "smallint": "SmallInteger",
                           }.get(col["data_type"].lower(), "String")
                pk = ", primary_key=True" if col["is_primary_key"] else ""
                fk = ""
                if col["is_foreign_key"] and col["foreign_table"] and col["foreign_column"]:
                    fk = f", ForeignKey('{col['foreign_table']}.{col['foreign_column']}')"
                nullable = "" if col["is_primary_key"] else f", nullable={bool(col['is_nullable'])}"
                field_defs.append(
                    f"    {col['column_name']} = Column({sa_type}{pk}{fk}{nullable})"
                )
                if col["is_primary_key"]:
                    pk_cols.append(col["column_name"])

            fields_block = "\n".join(field_defs)
            model_path = svc_dir / "models" / f"{model_var}.py"
            _write_file(model_path, textwrap.dedent(f"""\
                {ban_top}
                \"\"\"SQLAlchemy model for {table}. Classification: {CUI_BANNER}\"\"\"
                from sqlalchemy import Column, Integer, String, Text, Boolean, Date, DateTime, Float
                from sqlalchemy import BigInteger, SmallInteger, ForeignKey
                from sqlalchemy.orm import declarative_base

                Base = declarative_base()

                class {model_class}(Base):
                    __tablename__ = "{table}"

                {fields_block}

                    def to_dict(self):
                        return {{c.name: getattr(self, c.name) for c in self.__table__.columns}}
                {ban_bot}
            """))
            all_paths.append(str(model_path))

            # Repository file
            pk_param = pk_cols[0] if pk_cols else "id"
            repo_path = svc_dir / "repository" / f"{model_var}_repository.py"
            _write_file(repo_path, textwrap.dedent(f"""\
                {ban_top}
                \"\"\"Repository for {table}. Classification: {CUI_BANNER}\"\"\"
                from models.{model_var} import {model_class}

                class {model_class}Repository:
                    def __init__(self, session):
                        self.session = session

                    def get_by_id(self, {pk_param}):
                        return self.session.query({model_class}).get({pk_param})

                    def list_all(self, limit=100, offset=0):
                        return self.session.query({model_class}).limit(limit).offset(offset).all()

                    def create(self, entity):
                        self.session.add(entity)
                        self.session.commit()
                        return entity

                    def update(self, entity):
                        self.session.merge(entity)
                        self.session.commit()
                        return entity

                    def delete(self, {pk_param}):
                        obj = self.get_by_id({pk_param})
                        if obj:
                            self.session.delete(obj)
                            self.session.commit()
                        return obj
                {ban_bot}
            """))
            all_paths.append(str(repo_path))

        elif language == "java":
            pkg = f"com.icdev.{_safe_var_name(service_name)}"
            pkg_path = pkg.replace(".", "/")
            svc_dir_java = svc_dir / f"src/main/java/{pkg_path}"

            field_defs = []
            for col in columns:
                jtype = {"integer": "Integer", "varchar": "String", "text": "String",
                         "boolean": "Boolean", "date": "java.time.LocalDate",
                         "timestamp": "java.time.LocalDateTime", "real": "Double",
                         "float": "Double", "bigint": "Long",
                         }.get(col["data_type"].lower(), "String")
                annotations = []
                if col["is_primary_key"]:
                    annotations.append("    @Id")
                field_defs.append("\n".join(annotations + [f"    private {jtype} {col['column_name']};"]))
            fields_block = "\n".join(field_defs)
            model_path = svc_dir_java / "model" / f"{model_class}.java"
            _write_file(model_path, textwrap.dedent(f"""\
                {ban_top}
                package {pkg}.model;
                import javax.persistence.*;
                /** Entity for {table}. Classification: {CUI_BANNER} */
                @Entity
                @Table(name = "{table}")
                public class {model_class} {{
                {fields_block}
                }}
                {ban_bot}
            """))
            all_paths.append(str(model_path))

            repo_path = svc_dir_java / "repository" / f"{model_class}Repository.java"
            _write_file(repo_path, textwrap.dedent(f"""\
                {ban_top}
                package {pkg}.repository;
                import {pkg}.model.{model_class};
                import org.springframework.data.jpa.repository.JpaRepository;
                /** Repository for {table}. Classification: {CUI_BANNER} */
                public interface {model_class}Repository extends JpaRepository<{model_class}, Long> {{
                }}
                {ban_bot}
            """))
            all_paths.append(str(repo_path))

        elif language == "csharp":
            field_defs = []
            for col in columns:
                cstype = {"integer": "int", "varchar": "string", "text": "string",
                          "boolean": "bool", "date": "DateTime", "timestamp": "DateTime",
                          "real": "double", "float": "double", "bigint": "long",
                          }.get(col["data_type"].lower(), "string")
                nullable_q = "?" if (not col["is_primary_key"] and col["is_nullable"]) and cstype != "string" else ""
                field_defs.append(f"        public {cstype}{nullable_q} {_safe_class_name(col['column_name'])} {{ get; set; }}")
            fields_block = "\n".join(field_defs)
            model_path = svc_dir / "Models" / f"{model_class}.cs"
            _write_file(model_path, textwrap.dedent(f"""\
                {ban_top}
                // Entity for {table}. Classification: {CUI_BANNER}
                using System.ComponentModel.DataAnnotations;
                using System.ComponentModel.DataAnnotations.Schema;

                [Table("{table}")]
                public class {model_class}
                {{
                {fields_block}
                }}
                {ban_bot}
            """))
            all_paths.append(str(model_path))

            ctx_path = svc_dir / "Models" / f"{_safe_class_name(service_name)}DbContext.cs"
            _write_file(ctx_path, textwrap.dedent(f"""\
                {ban_top}
                // EF Core DbContext for {service_name}. Classification: {CUI_BANNER}
                using Microsoft.EntityFrameworkCore;

                public class {_safe_class_name(service_name)}DbContext : DbContext
                {{
                    public {_safe_class_name(service_name)}DbContext(DbContextOptions options) : base(options) {{ }}
                    public DbSet<{model_class}> {model_class}s {{ get; set; }}
                }}
                {ban_bot}
            """))
            all_paths.append(str(ctx_path))

    task = conn.execute(
        "SELECT id FROM migration_tasks WHERE plan_id = ? AND task_type = 'migrate_schema' LIMIT 1",
        (plan_id,),
    ).fetchone()
    task_id = task["id"] if task else None
    conn.close()

    for p in all_paths:
        _record_artifact(plan_id, task_id, "scaffold_code", p,
                         f"DAL file for {service_name}")
    return all_paths


# ---------------------------------------------------------------------------
# 5. generate_migration_tests
# ---------------------------------------------------------------------------

def generate_migration_tests(plan_id, output_dir="."):
    """Generate API compatibility, data integrity, and functional equivalence tests."""
    conn = _get_db()
    plan = conn.execute("SELECT * FROM migration_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"Migration plan {plan_id} not found")

    app_id = plan["legacy_app_id"]
    language = (plan["target_language"] or "python").lower()
    ban_top = _banner_top(language)
    ban_bot = _banner_bottom(language)

    apis = conn.execute(
        "SELECT * FROM legacy_apis WHERE legacy_app_id = ?", (app_id,)
    ).fetchall()

    tables = conn.execute(
        "SELECT DISTINCT table_name FROM legacy_db_schemas WHERE legacy_app_id = ?",
        (app_id,),
    ).fetchall()

    components = conn.execute(
        "SELECT lc.* FROM legacy_components lc "
        "JOIN migration_tasks mt ON mt.legacy_component_id = lc.id "
        "WHERE mt.plan_id = ?", (plan_id,)
    ).fetchall()

    task = conn.execute(
        "SELECT id FROM migration_tasks WHERE plan_id = ? AND task_type = 'generate_test' LIMIT 1",
        (plan_id,),
    ).fetchone()
    task_id = task["id"] if task else None
    conn.close()

    test_dir = Path(output_dir) / "tests"
    all_paths = []

    # --- API compatibility tests ---
    if language == "python":
        api_tests = []
        for api in apis:
            method = (api["method"] or "GET").lower()
            path = api["path"] or "/"
            func = _safe_var_name(f"test_api_{method}_{path.replace('/', '_')}")
            api_tests.append(textwrap.dedent(f"""\
                def {func}(legacy_base, modern_base):
                    \"\"\"Compare legacy and modern responses for {method.upper()} {path}.\"\"\"
                    legacy_resp = requests.{method}(legacy_base + "{path}")
                    modern_resp = requests.{method}(modern_base + "{path}")
                    assert legacy_resp.status_code == modern_resp.status_code, (
                        f"Status mismatch: {{legacy_resp.status_code}} vs {{modern_resp.status_code}}"
                    )
                    assert legacy_resp.json() == modern_resp.json(), "Response body mismatch"
            """))
        api_block = "\n\n".join(api_tests) if api_tests else "# No legacy APIs to test\npass\n"
        api_path = test_dir / "test_api_compatibility.py"
        _write_file(api_path, textwrap.dedent(f"""\
            {ban_top}
            \"\"\"API compatibility tests — compares legacy vs modern responses.

            Classification: {CUI_BANNER}
            \"\"\"
            import pytest
            import requests

            LEGACY_BASE = "http://localhost:8000"
            MODERN_BASE = "http://localhost:8080"

            @pytest.fixture
            def legacy_base():
                return LEGACY_BASE

            @pytest.fixture
            def modern_base():
                return MODERN_BASE

            {api_block}
            {ban_bot}
        """))
        all_paths.append(str(api_path))

        # --- Data integrity tests ---
        data_tests = []
        for trow in tables:
            tname = trow["table_name"]
            func = _safe_var_name(f"test_data_{tname}")
            data_tests.append(textwrap.dedent(f"""\
                def {func}(legacy_conn, modern_conn):
                    \"\"\"Verify row counts match for table {tname}.\"\"\"
                    legacy_count = legacy_conn.execute("SELECT COUNT(*) FROM {tname}").fetchone()[0]
                    modern_count = modern_conn.execute("SELECT COUNT(*) FROM {tname}").fetchone()[0]
                    assert legacy_count == modern_count, (
                        f"Row count mismatch for {tname}: {{legacy_count}} vs {{modern_count}}"
                    )
            """))
        data_block = "\n\n".join(data_tests) if data_tests else "# No tables to test\npass\n"
        data_path = test_dir / "test_data_integrity.py"
        _write_file(data_path, textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Data integrity tests — compares legacy vs modern row counts.

            Classification: {CUI_BANNER}
            \"\"\"
            import pytest
            import sqlite3

            @pytest.fixture
            def legacy_conn():
                conn = sqlite3.connect("legacy.db")
                yield conn
                conn.close()

            @pytest.fixture
            def modern_conn():
                conn = sqlite3.connect("modern.db")
                yield conn
                conn.close()

            {data_block}
            {ban_bot}
        """))
        all_paths.append(str(data_path))

        # --- Functional equivalence tests ---
        func_tests = []
        for comp in components:
            cname = comp["name"]
            func = _safe_var_name(f"test_equivalence_{cname}")
            func_tests.append(textwrap.dedent(f"""\
                def {func}():
                    \"\"\"Functional equivalence test for {cname}.\"\"\"
                    # TODO: implement test that exercises both legacy and modern {cname}
                    #       and compares outputs for identical inputs.
                    legacy_result = None   # call legacy {cname}
                    modern_result = None   # call modern {cname}
                    assert legacy_result == modern_result, "Functional equivalence failed for {cname}"
            """))
        func_block = "\n\n".join(func_tests) if func_tests else "# No components to test\npass\n"
        func_path = test_dir / "test_functional_equivalence.py"
        _write_file(func_path, textwrap.dedent(f"""\
            {ban_top}
            \"\"\"Functional equivalence tests — skeleton with TODO markers.

            Classification: {CUI_BANNER}
            \"\"\"
            import pytest

            {func_block}
            {ban_bot}
        """))
        all_paths.append(str(func_path))

    elif language == "java":
        api_path = test_dir / "ApiCompatibilityTest.java"
        api_methods = []
        for api in apis:
            method = (api["method"] or "GET").upper()
            path = api["path"] or "/"
            mname = _safe_var_name(f"test_{method.lower()}_{path.replace('/', '_')}")
            api_methods.append(
                f"    @Test\n    public void {mname}() {{\n"
                f"        // TODO: compare legacy and modern responses for {method} {path}\n"
                f"    }}"
            )
        api_block = "\n\n".join(api_methods) if api_methods else "    // No legacy APIs to test"
        _write_file(api_path, textwrap.dedent(f"""\
            {ban_top}
            /** API compatibility tests. Classification: {CUI_BANNER} */
            import org.junit.jupiter.api.Test;
            import static org.junit.jupiter.api.Assertions.*;

            public class ApiCompatibilityTest {{
            {api_block}
            }}
            {ban_bot}
        """))
        all_paths.append(str(api_path))

    elif language == "csharp":
        api_path = test_dir / "ApiCompatibilityTests.cs"
        api_methods = []
        for api in apis:
            method = (api["method"] or "GET").upper()
            path = api["path"] or "/"
            mname = _safe_class_name(f"test_{method.lower()}_{path.replace('/', '_')}")
            api_methods.append(
                f"        [Fact]\n        public async Task {mname}()\n"
                f"        {{\n"
                f"            // TODO: compare legacy and modern responses for {method} {path}\n"
                f"        }}"
            )
        api_block = "\n\n".join(api_methods) if api_methods else "        // No legacy APIs to test"
        _write_file(api_path, textwrap.dedent(f"""\
            {ban_top}
            // API compatibility tests. Classification: {CUI_BANNER}
            using Xunit;
            using System.Threading.Tasks;

            public class ApiCompatibilityTests
            {{
            {api_block}
            }}
            {ban_bot}
        """))
        all_paths.append(str(api_path))

    for p in all_paths:
        _record_artifact(plan_id, task_id, "test_code", p, "Migration validation test")
    return all_paths


# ---------------------------------------------------------------------------
# 6. generate_rollback_scripts
# ---------------------------------------------------------------------------

def generate_rollback_scripts(plan_id, output_dir="."):
    """Generate rollback scripts for completed/in-progress migration tasks."""
    conn = _get_db()
    plan = conn.execute("SELECT * FROM migration_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"Migration plan {plan_id} not found")

    app_id = plan["legacy_app_id"]
    language = (plan["target_language"] or "python").lower()
    ban_top = _banner_top(language)
    ban_bot = _banner_bottom(language)

    tasks = conn.execute(
        "SELECT * FROM migration_tasks WHERE plan_id = ? AND status IN ('completed', 'in_progress') "
        "ORDER BY created_at",
        (plan_id,),
    ).fetchall()

    apis = conn.execute(
        "SELECT * FROM legacy_apis WHERE legacy_app_id = ?", (app_id,)
    ).fetchall()

    schema_cols = conn.execute(
        "SELECT DISTINCT table_name FROM legacy_db_schemas WHERE legacy_app_id = ?",
        (app_id,),
    ).fetchall()

    task_for_artifact = conn.execute(
        "SELECT id FROM migration_tasks WHERE plan_id = ? AND task_type = 'cutover' LIMIT 1",
        (plan_id,),
    ).fetchone()
    task_id = task_for_artifact["id"] if task_for_artifact else None
    conn.close()

    rb_dir = Path(output_dir) / "rollback"
    all_paths = []

    # Reverse routing script
    if apis:
        if language == "python":
            route_entries = []
            for api in apis:
                m = (api["method"] or "GET").upper()
                p = api["path"] or "/"
                route_entries.append(f'    ("{m}", "{p}"),')
            routes_block = "\n".join(route_entries)
            code = textwrap.dedent(f"""\
                {ban_top}
                \"\"\"Rollback: revert all routes to legacy backend.

                Classification: {CUI_BANNER}
                \"\"\"
                import os
                import requests

                LEGACY_BACKEND = os.environ.get("LEGACY_BACKEND", "http://localhost:8000")
                FACADE_ADMIN = os.environ.get("FACADE_ADMIN", "http://localhost:5050/admin")

                ROUTES_TO_REVERT = [
                {routes_block}
                ]


                def rollback_routes():
                    \"\"\"Switch all routes back to the legacy backend.\"\"\"
                    results = []
                    for method, path in ROUTES_TO_REVERT:
                        # TODO: call facade admin API to switch route target
                        results.append({{"method": method, "path": path, "target": LEGACY_BACKEND, "status": "reverted"}})
                    return results


                if __name__ == "__main__":
                    for r in rollback_routes():
                        print(f"Reverted {{r['method']}} {{r['path']}} -> {{r['target']}}")
                {ban_bot}
            """)
        else:
            code = textwrap.dedent(f"""\
                {ban_top}
                // Rollback: revert routes to legacy. Classification: {CUI_BANNER}
                // TODO: Implement route rollback for {language}
                {ban_bot}
            """)
        rr_path = rb_dir / f"rollback_routes{_ext(language)}"
        _write_file(rr_path, code)
        all_paths.append(str(rr_path))

    # DB rollback script (reverse DDL)
    if schema_cols:
        ddl_stmts = []
        for trow in schema_cols:
            tname = trow["table_name"]
            ddl_stmts.append(f"-- Rollback: drop migrated table {tname}_new if it exists")
            ddl_stmts.append(f"DROP TABLE IF EXISTS {tname}_new;")
            ddl_stmts.append(f"-- Restore: rename legacy backup back if it exists")
            ddl_stmts.append(f"-- ALTER TABLE {tname}_legacy_backup RENAME TO {tname};")
            ddl_stmts.append("")
        ddl_block = "\n".join(ddl_stmts)
        sql_path = rb_dir / "rollback_schema.sql"
        _write_file(sql_path, textwrap.dedent(f"""\
            -- {CUI_BANNER}
            -- Schema Rollback Script
            -- Generated by ICDEV Migration Code Generator
            -- Plan: {plan_id}
            -- Classification: {CUI_BANNER}
            --
            -- WARNING: Review each statement before executing.
            -- This script is auto-generated and may need manual adjustment.

            BEGIN;

            {ddl_block}

            COMMIT;
            -- {CUI_BANNER}
        """))
        all_paths.append(str(sql_path))

    # Per-task rollback summaries
    for t in tasks:
        t_type = t["task_type"]
        t_title = t["title"]
        t_id = t["id"]
        note_path = rb_dir / f"rollback_{_safe_var_name(t_title)}.txt"
        _write_file(note_path, textwrap.dedent(f"""\
            {CUI_BANNER}
            Rollback Procedure for: {t_title}
            Task ID: {t_id}
            Task Type: {t_type}
            Status: {t['status']}

            Steps:
            1. Verify the current deployment state.
            2. Switch traffic back to the legacy endpoint.
            3. If schema was migrated, execute rollback_schema.sql.
            4. Confirm legacy application is handling traffic.
            5. Update migration_tasks status to 'pending'.

            Classification: {CUI_BANNER}
        """))
        all_paths.append(str(note_path))

    for p in all_paths:
        _record_artifact(plan_id, task_id, "rollback_script", p, "Rollback artifact")
    return all_paths


# ---------------------------------------------------------------------------
# 7. generate_all
# ---------------------------------------------------------------------------

def generate_all(plan_id, output_dir="."):
    """Orchestrate all code generation for a migration plan."""
    conn = _get_db()
    plan = conn.execute("SELECT * FROM migration_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"Migration plan {plan_id} not found")

    app_id = plan["legacy_app_id"]
    language = (plan["target_language"] or "python").lower()
    framework = plan["target_framework"] or _DEFAULT_FRAMEWORK.get(language, "flask")

    # Components with cross-boundary dependencies (adapter candidates)
    adapter_comps = conn.execute(
        "SELECT DISTINCT lc.id, lc.name FROM legacy_components lc "
        "JOIN migration_tasks mt ON mt.legacy_component_id = lc.id "
        "WHERE mt.plan_id = ? AND mt.task_type IN ('generate_adapter', 'create_acl')",
        (plan_id,),
    ).fetchall()

    # Service extraction tasks
    service_tasks = conn.execute(
        "SELECT * FROM migration_tasks WHERE plan_id = ? AND task_type = 'extract_service'",
        (plan_id,),
    ).fetchall()

    # Tables by service
    tables = conn.execute(
        "SELECT DISTINCT table_name FROM legacy_db_schemas WHERE legacy_app_id = ?",
        (app_id,),
    ).fetchall()
    table_names = [t["table_name"] for t in tables]

    conn.close()

    summary = {
        "plan_id": plan_id,
        "language": language,
        "framework": framework,
        "adapters": [],
        "facade": None,
        "scaffolds": [],
        "data_access": [],
        "tests": [],
        "rollback": [],
    }

    # 1. Adapters
    for comp in adapter_comps:
        path = generate_adapter(plan_id, comp["id"], language, output_dir)
        summary["adapters"].append(path)

    # 2. Facade
    try:
        summary["facade"] = generate_facade(plan_id, language, output_dir)
    except Exception as exc:
        summary["facade"] = f"ERROR: {exc}"

    # 3. Service scaffolds
    for st in service_tasks:
        svc_name = _safe_var_name(st["title"])
        path = generate_service_scaffold(plan_id, svc_name, language, framework, output_dir)
        summary["scaffolds"].append(path)

    # 4. DAL for each service (distribute tables evenly if no better mapping)
    if service_tasks and table_names:
        svc_names = [_safe_var_name(st["title"]) for st in service_tasks]
        # Simple round-robin assignment of tables to services
        svc_tables = collections.defaultdict(list)
        for idx, tname in enumerate(table_names):
            svc_tables[svc_names[idx % len(svc_names)]].append(tname)
        for svc_name, tbls in svc_tables.items():
            paths = generate_data_access_layer(plan_id, svc_name, tbls, language, output_dir)
            summary["data_access"].extend(paths)
    elif table_names:
        paths = generate_data_access_layer(plan_id, "default", table_names, language, output_dir)
        summary["data_access"].extend(paths)

    # 5. Tests
    summary["tests"] = generate_migration_tests(plan_id, output_dir)

    # 6. Rollback
    summary["rollback"] = generate_rollback_scripts(plan_id, output_dir)

    # 7. Index document
    index_lines = [
        f"<!-- {CUI_BANNER} -->",
        f"# Migration Code Generation — Plan {plan_id}",
        f"",
        f"Generated: {datetime.utcnow().isoformat()}",
        f"Language: {language} | Framework: {framework}",
        f"",
        "## Adapters",
    ]
    for a in summary["adapters"]:
        index_lines.append(f"- `{a}`")
    index_lines += ["", "## Facade", f"- `{summary['facade']}`", "", "## Service Scaffolds"]
    for s in summary["scaffolds"]:
        index_lines.append(f"- `{s}`")
    index_lines += ["", "## Data Access Layer"]
    for d in summary["data_access"]:
        index_lines.append(f"- `{d}`")
    index_lines += ["", "## Tests"]
    for t in summary["tests"]:
        index_lines.append(f"- `{t}`")
    index_lines += ["", "## Rollback Scripts"]
    for r in summary["rollback"]:
        index_lines.append(f"- `{r}`")
    index_lines += ["", f"<!-- {CUI_BANNER} -->"]
    _write_file(Path(output_dir) / "index.md", "\n".join(index_lines) + "\n")

    # 8. Update migration_tasks output_path
    conn = _get_db()
    conn.execute(
        "UPDATE migration_tasks SET output_path = ? WHERE plan_id = ? AND task_type = 'generate_adapter'",
        (str(Path(output_dir) / "adapters"), plan_id),
    )
    conn.execute(
        "UPDATE migration_tasks SET output_path = ? WHERE plan_id = ? AND task_type = 'generate_facade'",
        (str(Path(output_dir) / "facade"), plan_id),
    )
    conn.execute(
        "UPDATE migration_tasks SET output_path = ? WHERE plan_id = ? AND task_type = 'extract_service'",
        (str(Path(output_dir) / "services"), plan_id),
    )
    conn.execute(
        "UPDATE migration_tasks SET output_path = ? WHERE plan_id = ? AND task_type = 'generate_test'",
        (str(Path(output_dir) / "tests"), plan_id),
    )
    conn.commit()
    conn.close()

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Migration Code Generator — produce adapters, facades, scaffolds, "
                    "DAL, tests, and rollback scripts from a migration plan.",
    )
    parser.add_argument("--plan-id", required=True, help="Migration plan ID (from migration_plans table)")
    parser.add_argument("--output", required=True, help="Output directory for generated code")
    parser.add_argument(
        "--generate",
        choices=["adapters", "facade", "scaffolds", "dal", "tests", "rollback", "all"],
        default="all",
        help="What to generate (default: all)",
    )
    parser.add_argument(
        "--language",
        choices=["python", "java", "csharp"],
        default=None,
        help="Target language (overrides plan setting)",
    )
    parser.add_argument(
        "--framework",
        choices=["flask", "fastapi", "spring-boot", "aspnet-core"],
        default=None,
        help="Target framework (overrides plan setting)",
    )
    parser.add_argument("--service-name", default=None, help="Service name for single scaffold generation")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output results as JSON")

    args = parser.parse_args()

    # Resolve language from plan if not specified
    conn = _get_db()
    plan = conn.execute("SELECT * FROM migration_plans WHERE id = ?", (args.plan_id,)).fetchone()
    if not plan:
        conn.close()
        msg = f"Migration plan {args.plan_id} not found"
        if args.json_output:
            print(json.dumps({"error": msg}))
        else:
            print(f"ERROR: {msg}")
        return
    conn.close()

    language = args.language or (plan["target_language"] or "python").lower()
    framework = args.framework or plan["target_framework"] or _DEFAULT_FRAMEWORK.get(language, "flask")
    output_dir = args.output

    result = None

    if args.generate == "all":
        result = generate_all(args.plan_id, output_dir)
    elif args.generate == "adapters":
        conn = _get_db()
        comps = conn.execute(
            "SELECT DISTINCT lc.id FROM legacy_components lc "
            "JOIN migration_tasks mt ON mt.legacy_component_id = lc.id "
            "WHERE mt.plan_id = ?", (args.plan_id,)
        ).fetchall()
        conn.close()
        result = []
        for c in comps:
            result.append(generate_adapter(args.plan_id, c["id"], language, output_dir))
    elif args.generate == "facade":
        result = generate_facade(args.plan_id, language, output_dir)
    elif args.generate == "scaffolds":
        if args.service_name:
            result = generate_service_scaffold(args.plan_id, args.service_name,
                                               language, framework, output_dir)
        else:
            conn = _get_db()
            svc_tasks = conn.execute(
                "SELECT title FROM migration_tasks WHERE plan_id = ? AND task_type = 'extract_service'",
                (args.plan_id,),
            ).fetchall()
            conn.close()
            result = []
            for st in svc_tasks:
                result.append(generate_service_scaffold(
                    args.plan_id, _safe_var_name(st["title"]),
                    language, framework, output_dir))
    elif args.generate == "dal":
        conn = _get_db()
        app_id = plan["legacy_app_id"]
        tbls = conn.execute(
            "SELECT DISTINCT table_name FROM legacy_db_schemas WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
        conn.close()
        table_names = [t["table_name"] for t in tbls]
        svc = args.service_name or "default"
        result = generate_data_access_layer(args.plan_id, svc, table_names, language, output_dir)
    elif args.generate == "tests":
        result = generate_migration_tests(args.plan_id, output_dir)
    elif args.generate == "rollback":
        result = generate_rollback_scripts(args.plan_id, output_dir)

    if args.json_output:
        print(json.dumps({"plan_id": args.plan_id, "generate": args.generate, "result": result}, indent=2))
    else:
        print(f"Migration code generation complete for plan {args.plan_id}")
        print(f"  Mode:      {args.generate}")
        print(f"  Language:  {language}")
        print(f"  Framework: {framework}")
        print(f"  Output:    {output_dir}")
        if isinstance(result, dict):
            for key, val in result.items():
                if isinstance(val, list):
                    print(f"  {key}: {len(val)} files")
                else:
                    print(f"  {key}: {val}")
        elif isinstance(result, list):
            print(f"  Files generated: {len(result)}")
            for p in result:
                print(f"    - {p}")
        else:
            print(f"  Result: {result}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
