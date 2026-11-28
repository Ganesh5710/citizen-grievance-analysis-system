"""
Locust load test script targeting Citizen Grievance Portal HTTP endpoints.
Usage:
  locust -f scripts/load_test.py --headless -u 100 -r 10 --run-time 30s --host http://localhost:8501
"""

from __future__ import annotations

try:
    from locust import HttpUser, between, task
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install locust (`pip install locust`) to execute load tests.") from exc


class CitizenPortalUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(3)
    def view_portal_root(self):
        with self.client.get("/", name="GET / (Portal Root)", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected HTTP {resp.status_code}")

    @task(2)
    def check_health_endpoint(self):
        with self.client.get("/?health=1", name="GET /?health=1", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health HTTP {resp.status_code}")

    @task(1)
    def check_public_ticket_qr_route(self):
        with self.client.get("/?ticket=CIV-00000001", name="GET /?ticket=...", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Ticket route HTTP {resp.status_code}")
