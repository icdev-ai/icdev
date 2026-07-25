# CUI // SP-CTI
"""Locust load profile for the ICDEV dashboard hot endpoints.

Pure-Python load generator (``pip install locust`` — no npm/Node). Drives the
same ~10 hot GET endpoints that ``tools/testing/perf_benchmark.py`` benchmarks,
under sustained concurrent load, so p50/p95/p99 can be observed *under load*
rather than in isolation.

Run it (against a REAL, running dashboard — never LocalStack / mocks):

    pip install locust
    python tools/dashboard/app.py                 # in another shell
    locust -f tools/testing/perf/locustfile.py --host http://127.0.0.1:5050

    # headless, 20 users, 30s (also what perf_benchmark.py --load invokes):
    locust -f tools/testing/perf/locustfile.py --headless \
        -u 20 -r 5 -t 30s --host http://127.0.0.1:5050

The ``locust`` import is guarded so that merely importing this module (e.g. a
stray test-collection scan) never raises ImportError when locust is absent —
instead a placeholder ``HotEndpointsUser`` is exported and locust CLI usage
prints a clear "install locust" message.
"""
from __future__ import annotations

try:
    from locust import HttpUser, between, task

    _LOCUST_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when locust absent
    _LOCUST_AVAILABLE = False


# Hot endpoints under load. Weighted roughly by real traffic: the board home and
# its backing APIs get hit far more than deep canvas pages.
HOT_GET_ENDPOINTS = [
    ("/", 5),
    ("/health", 5),
    ("/api/kanban/tasks", 5),
    ("/api/projects", 3),
    ("/api/agents", 3),
    ("/api/govcon/opportunities", 2),
    ("/kanban", 2),
    ("/govcon", 1),
    ("/proposals", 1),
    ("/compliance", 1),
]


if _LOCUST_AVAILABLE:

    class HotEndpointsUser(HttpUser):
        """Simulated user hammering the dashboard's hottest read paths."""

        # Think-time between requests: 0.5–2s, a realistic browsing cadence.
        wait_time = between(0.5, 2.0)

        def _get(self, path: str) -> None:
            # name= groups query-string variants; catch_response lets us mark
            # 5xx as failures without raising (load runs are advisory).
            with self.client.get(path, name=path, catch_response=True) as resp:
                if resp.status_code >= 500:
                    resp.failure(f"HTTP {resp.status_code}")
                else:
                    resp.success()

        # Register one weighted task per endpoint by expanding the weight.
        @task(5)
        def home(self) -> None:
            self._get("/")

        @task(5)
        def health(self) -> None:
            self._get("/health")

        @task(5)
        def api_kanban_tasks(self) -> None:
            self._get("/api/kanban/tasks")

        @task(3)
        def api_projects(self) -> None:
            self._get("/api/projects")

        @task(3)
        def api_agents(self) -> None:
            self._get("/api/agents")

        @task(2)
        def api_govcon_opps(self) -> None:
            self._get("/api/govcon/opportunities")

        @task(2)
        def kanban_page(self) -> None:
            self._get("/kanban")

        @task(1)
        def govcon_page(self) -> None:
            self._get("/govcon")

        @task(1)
        def proposals_page(self) -> None:
            self._get("/proposals")

        @task(1)
        def compliance_page(self) -> None:
            self._get("/compliance")

else:  # pragma: no cover - placeholder when locust not installed

    class HotEndpointsUser:  # type: ignore[no-redef]
        """Placeholder — install locust (``pip install locust``) to run load tests."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "locust is not installed. Run: pip install locust"
            )
