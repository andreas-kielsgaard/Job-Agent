from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from job_agent.web.app import app
from job_agent.run_store import RunOptions


class WebTests(unittest.TestCase):
    def test_dashboard_loads(self) -> None:
        client = TestClient(app)
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Overview", response.text)
        self.assertIn("Perform daily run", response.text)

    def test_setup_loads_friendly_sections(self) -> None:
        client = TestClient(app)
        response = client.get("/setup")
        self.assertEqual(response.status_code, 200)
        self.assertIn("CV Reference Upload", response.text)
        self.assertIn("Template Variables", response.text)
        self.assertIn("Highest performance", response.text)
        self.assertIn("Minimum digest score", response.text)

    def test_dashboard_has_material_generation_option(self) -> None:
        client = TestClient(app)
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Generate CV/application materials", response.text)

    def test_jobs_and_stats_pages_load(self) -> None:
        client = TestClient(app)
        self.assertEqual(client.get("/jobs").status_code, 200)
        stats = client.get("/stats")
        self.assertEqual(stats.status_code, 200)
        self.assertIn("Stats", stats.text)
        self.assertEqual(client.get("/runs?view=test").status_code, 200)
        self.assertEqual(client.get("/runs?view=archived").status_code, 200)
        self.assertEqual(client.get("/runs?view=deleted").status_code, 200)

    def test_jobs_multi_filters_load(self) -> None:
        client = TestClient(app)
        response = client.get("/jobs?app_status=interesting&app_status=not_interesting&category=strong&category=exploratory")
        self.assertEqual(response.status_code, 200)
        self.assertIn("clickable-row", response.text)

    def test_run_options_include_material_generation_flag(self) -> None:
        options = RunOptions()
        self.assertTrue(options.generate_materials)

    def test_ai_edit_context_endpoint(self) -> None:
        client = TestClient(app)
        response = client.get("/api/ai-edit/context", params={"field_id": "profile.skills", "button_id": "setup.skills"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("blocks", data)
        self.assertIn("selected_blocks", data)


if __name__ == "__main__":
    unittest.main()
