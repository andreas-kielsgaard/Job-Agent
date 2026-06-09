from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.digest import write_job_package
from job_agent.models import GeneratedPackage, Job, MatchResult
from job_agent.services.material_service import MaterialService, MaterialUpdate
from job_agent.services.package_index_service import PackageIndexService
from job_agent.llm import DEFAULT_CLAUDE_MODEL
from job_agent.services.setup_service import SetupService


class ServiceBoundaryTests(unittest.TestCase):
    def test_package_index_lists_and_refreshes_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_package(root)
            status_store = ApplicationStatusStore(root)
            status_store.ensure_for_job(
                stable_id="stable-1",
                fuzzy_key="fuzzy-1",
                title="SAP ABAP Consultant",
                company="Recruiter",
                source="Sample",
                url="https://example.com",
                application_url="https://example.com/apply",
            )

            service = PackageIndexService(root)
            packages = service.list_packages("run-1")
            self.assertEqual(len(packages), 1)
            self.assertEqual(service.find_package("stable-1")["title"], "SAP ABAP Consultant")

            status_store.update_status("stable-1", "interesting")
            service.refresh_package_status("stable-1", "interesting")
            self.assertEqual(service.find_package("stable-1")["application_status"], "interesting")

    def test_material_service_saves_existing_materials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_package(root)

            MaterialService(root).save_job_materials(
                "stable-1",
                MaterialUpdate(
                    cv="new cv", application="new app", form_answers="new forms", match_analysis="new analysis"
                ),
            )

            files = PackageIndexService(root).read_package_files(PackageIndexService(root).find_package("stable-1"))
            self.assertEqual(files["cv"], "new cv")
            self.assertEqual(files["application"], "new app")
            self.assertEqual(files["form_answers"], "new forms")
            self.assertEqual(files["match_analysis"], "new analysis")

    def test_setup_service_preserves_api_key_when_blank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("ANTHROPIC_API_KEY=secret\nOLD_VALUE=keep\n", encoding="utf-8")

            SetupService(root).save_env_settings("", DEFAULT_CLAUDE_MODEL, True)

            env = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("ANTHROPIC_API_KEY=secret", env)
            self.assertIn("OLD_VALUE=keep", env)
            self.assertIn("CLAUDE_USE_BY_DEFAULT=true", env)

    @staticmethod
    def _write_package(root: Path) -> None:
        write_job_package(
            Job(title="SAP ABAP Consultant", company="Recruiter", url="https://example.com"),
            MatchResult(total_score=82, category="strong", recommended_angle="Lead with ABAP"),
            GeneratedPackage("cv", "app", "forms", "analysis", [], []),
            date(2026, 5, 6),
            root=root,
            run_id="run-1",
            stable_id="stable-1",
            fuzzy_key="fuzzy-1",
            state="new",
        )


if __name__ == "__main__":
    unittest.main()
