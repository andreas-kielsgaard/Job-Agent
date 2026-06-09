from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertEqual(service.resolve_profile_file("reference-cv.md").name, "reference-cv.md")

    def test_rejects_unsupported_extension_and_blocks_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CvReferenceService(root)
            with self.assertRaises(ValueError):
                service.store_reference_cv("cv.exe", b"nope", extract_to_canonical=False)
            with self.assertRaises(FileNotFoundError):
                service.resolve_profile_file("../secret.txt")

    def test_failed_extraction_is_error_not_profile_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CvReferenceService(root)
            with patch.object(
                CvReferenceService,
                "extract_cv_text_with_error",
                return_value=("", "Text extraction failed for reference-cv.pdf: broken"),
            ):
                service.store_reference_cv("cv.pdf", b"%PDF-1.4 broken", extract_to_canonical=True)

            reference = service.get_cv_reference()
            self.assertEqual(reference["extracted_text"], "")
            self.assertIn("broken", reference["extraction_error"])
            self.assertFalse((root / "profile" / "canonical-cv.md").exists())


if __name__ == "__main__":
    unittest.main()
