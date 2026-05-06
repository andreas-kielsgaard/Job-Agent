from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from job_agent.web.app import create_app


class WebSmokeTests(unittest.TestCase):
    def test_app_creation_and_health(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_route_smoke_and_missing_resources(self) -> None:
        client = TestClient(create_app())
        for path in ["/", "/runs", "/jobs", "/stats", "/setup"]:
            self.assertEqual(client.get(path).status_code, 200)
        self.assertEqual(client.get("/runs/nonexistent").status_code, 404)
        self.assertEqual(client.get("/jobs/nonexistent").status_code, 404)

    def test_invalid_bulk_actions_return_400(self) -> None:
        client = TestClient(create_app())
        self.assertEqual(
            client.post("/api/runs/bulk", data={"run_ids": ["x"], "action": "explode"}).status_code,
            400,
        )
        self.assertEqual(
            client.post("/api/jobs/bulk-status", data={"job_ids": ["x"], "status": "maybe"}).status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
