from __future__ import annotations

from pathlib import Path

from job_agent.config import ROOT
from job_agent.io.atomic import atomic_write_bytes, atomic_write_text


ALLOWED_CV_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


class CvReferenceService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.target_dir = root / "profile" / "files"

    def get_cv_reference(self) -> dict[str, str]:
        for path in sorted(self.target_dir.glob("reference-cv.*")) if self.target_dir.exists() else []:
            if path.suffix.lower() in ALLOWED_CV_SUFFIXES:
                extracted = self.target_dir / "reference-cv-extracted.txt"
                return {
                    "filename": path.name,
                    "path": str(path),
                    "url": f"/profile-files/{path.name}",
                    "extracted_path": str(extracted) if extracted.exists() else "",
                    "extracted_text": extracted.read_text(encoding="utf-8") if extracted.exists() else "",
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
        text = self.extract_cv_text(target)
        if text:
            atomic_write_text(self.target_dir / "reference-cv-extracted.txt", text, encoding="utf-8")
        if extract_to_canonical and text:
            atomic_write_text(self.root / "profile" / "canonical-cv.md", text.strip() + "\n", encoding="utf-8")
        return target

    def resolve_profile_file(self, filename: str) -> Path:
        target_dir = self.target_dir.resolve()
        path = (target_dir / filename).resolve()
        if not str(path).startswith(str(target_dir)) or not path.exists():
            raise FileNotFoundError(filename)
        return path

    @staticmethod
    def extract_cv_text(path: Path) -> str:
        suffix = path.suffix.lower()
        try:
            if suffix in {".txt", ".md"}:
                return path.read_text(encoding="utf-8", errors="ignore")
            if suffix == ".pdf":
                from pypdf import PdfReader

                reader = PdfReader(str(path))
                return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            if suffix == ".docx":
                from docx import Document

                document = Document(str(path))
                return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()).strip()
        except Exception as exc:
            return f"[Text extraction failed for {path.name}: {exc}]"
        return ""
