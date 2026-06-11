from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests.helpers import write_sample_package

from job_agent.io.json_store import read_json, write_json
from job_agent.services.package_index_service import PackageIndexService


class PackageIndexServiceTests(unittest.TestCase):
    def test_lists_filters_finds_reads_and_updates_package_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_sample_package(root, run_id="run-1", stable_id="stable-1")
            write_sample_package(root, run_id="run-2", stable_id="stable-2", title="SAP RAP Consultant")
            service = PackageIndexService(root)

            self.assertEqual(len(service.list_packages("run-1")), 1)
            self.assertEqual(len(service.list_packages()), 2)
            package = service.find_package("stable-1")
            self.assertEqual(package["title"], "SAP ABAP Consultant")
            self.assertEqual(service.read_package_files(package)["cv"], "cv")

            service.refresh_package_status("stable-1", "interesting")
            self.assertEqual(read_json(Path(paths["index"]), {})["application_status"], "interesting")
            service.mark_package_materials_generated(package, False)
            updated_index = read_json(Path(paths["index"]), {})
            self.assertFalse(updated_index["materials_generated"])
            self.assertEqual(updated_index["material_status"], "missing")
            self.assertEqual(service.validate_package(package), [])
            self.assertEqual(service.infer_package_date(package).isoformat(), "2026-05-06")

            Path(package["paths"]["job"]).unlink()
            self.assertEqual(service.validate_package(package), ["job"])

    def test_unique_jobs_keeps_latest_run_for_same_stable_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sample_package(root, run_id="run-1", stable_id="stable-1", run_date=date(2026, 5, 6))
            write_sample_package(root, run_id="run-9", stable_id="stable-1", run_date=date(2026, 5, 7))

            unique = PackageIndexService(root).list_unique_jobs()
            self.assertEqual(len(unique), 1)
            self.assertEqual(unique[0]["run_id"], "run-9")

    def test_missing_material_status_is_inferred_from_materials_generated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_sample_package(root, stable_id="stable-1")
            index = read_json(Path(paths["index"]), {})
            index.pop("material_status", None)
            index["materials_generated"] = False
            write_json(Path(paths["index"]), index)

            package = PackageIndexService(root).find_package("stable-1")

            self.assertEqual(package["material_status"], "missing")

    def test_service_has_no_presentation_or_review_bundle_responsibility(self) -> None:
        self.assertFalse(hasattr(PackageIndexService, "build_review_bundle"))
        self.assertFalse(hasattr(PackageIndexService, "markdown_to_html"))


if __name__ == "__main__":
    unittest.main()
