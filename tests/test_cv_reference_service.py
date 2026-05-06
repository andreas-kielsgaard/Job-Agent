from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_agent.services.cv_reference_service import CvReferenceService


class CvReferenceServiceTests(unittest.TestCase):
    def test_stores_text_cv_extracts_text_and_updates_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CvReferenceService(root)
            service.store_reference_cv("cv.md", b"# CV\nABAP and RAP\n", extract_to_canonical=True)

            reference = service.get_cv_reference()
            self.assertEqual(reference["filename"], "reference-cv.md")
            self.assertIn("ABAP and RAP", reference["extracted_text"])
            self.assertIn("ABAP and RAP", (root / "profile" / "canonical-cv.md").read_text(encoding="utf-8"))

    def test_rejects_unsupported_extension_and_blocks_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CvReferenceService(root)
            with self.assertRaises(ValueError):
                service.store_reference_cv("cv.exe", b"nope", extract_to_canonical=False)
            with self.assertRaises(FileNotFoundError):
                service.resolve_profile_file("../secret.txt")


if __name__ == "__main__":
    unittest.main()
