from __future__ import annotations

from pathlib import Path

from job_agent.config import ROOT
from job_agent.io.atomic import atomic_write_bytes, atomic_write_text
from job_agent.paths import cv_upload_dir, profile_dir

ALLOWED_CV_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


class CvReferenceService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.target_dir = cv_upload_dir(root)

    def get_cv_reference(self) -> dict[str, str]:
        for path in sorted(self.target_dir.glob("reference-cv.*")) if self.target_dir.exists() else []:
            if path.suffix.lower() in ALLOWED_CV_SUFFIXES:
                extracted = self.target_dir / "reference-cv-extracted.txt"
                error = self.target_dir / "reference-cv-extraction-error.txt"
                return {
                    "filename": path.name,
                    "path": str(path),
                    "url": f"/profile-files/{path.name}",
                    "suffix": path.suffix.lower(),
                    "is_pdf": path.suffix.lower() == ".pdf",
                    "extracted_path": str(extracted) if extracted.exists() else "",
                    "extracted_text": extracted.read_text(encoding="utf-8") if extracted.exists() else "",
                    "extraction_error": error.read_text(encoding="utf-8") if error.exists() else "",
                }
        return {}

    def store_reference_cv(self, filename: str, content: bytes, extract_to_canonical: bool) -> Path | None:
        if not filename:
            return None
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_CV_SUFFIXES:
            raise ValueError("Supported CV reference formats: PDF, DOCX, TXT, MD")
        target = self.target_dir / f"reference-cv{suffix}"
        atomic_write_bytes(target, content)
        text, error = self.extract_cv_text_with_error(target)
        extracted_path = self.target_dir / "reference-cv-extracted.txt"
        error_path = self.target_dir / "reference-cv-extraction-error.txt"
        if text:
            atomic_write_text(extracted_path, text, encoding="utf-8")
            error_path.unlink(missing_ok=True)
        else:
            extracted_path.unlink(missing_ok=True)
        if error:
            atomic_write_text(error_path, error, encoding="utf-8")
        elif not text:
            error_path.unlink(missing_ok=True)
        if extract_to_canonical and text:
            atomic_write_text(profile_dir(self.root) / "canonical-cv.md", text.strip() + "\n", encoding="utf-8")
        return target

    def resolve_profile_file(self, filename: str) -> Path:
        target_dir = self.target_dir.resolve()
        path = (target_dir / filename).resolve()
        try:
            path.relative_to(target_dir)
        except ValueError as exc:
            raise FileNotFoundError(filename) from exc
        if not path.exists():
            raise FileNotFoundError(filename)
        return path

    @staticmethod
    def extract_cv_text(path: Path) -> str:
        text, _error = CvReferenceService.extract_cv_text_with_error(path)
        return text

    @staticmethod
    def extract_cv_text_with_error(path: Path) -> tuple[str, str]:
        suffix = path.suffix.lower()
        try:
            if suffix in {".txt", ".md"}:
                return path.read_text(encoding="utf-8", errors="ignore"), ""
            if suffix == ".pdf":
                from pypdf import PdfReader

                reader = PdfReader(str(path))
                return "\n".join(page.extract_text() or "" for page in reader.pages).strip(), ""
            if suffix == ".docx":
                from docx import Document

                document = Document(str(path))
                return "\n".join(
                    paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()
                ).strip(), ""
        except Exception as exc:
            return "", f"Text extraction failed for {path.name}: {exc}"
        return "", ""
