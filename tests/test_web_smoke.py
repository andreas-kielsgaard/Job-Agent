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


if __name__ == "__main__":
    unittest.main()
