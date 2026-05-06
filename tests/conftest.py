from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("External network/API call attempted during test")

    monkeypatch.setattr("requests.get", blocked, raising=False)


@pytest.fixture(autouse=True)
def no_claude_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    for relative in [
        "profile.example",
        "profile",
        "sources",
        "jobs/raw",
        "templates",
        "prompts",
        "output",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def minimal_profile(project_root: Path) -> Path:
    profile = project_root / "profile"
    (profile / "contact.yaml").write_text(
        "contact:\n"
        "  name: Andreas Kielsgaard\n"
        "  title: SAP ABAP / RAP Consultant\n"
        "  email: andreas.kielsgaard@gmail.com\n"
        "  phone: '+45 2883 0550'\n"
        "  linkedin: https://www.linkedin.com/in/andreaskielsgaard/\n"
        "  location: Denmark\n",
        encoding="utf-8",
    )
    (profile / "preferences.yaml").write_text(
        "availability:\n"
        "  available_from: immediate\n"
        "  logistics: Needs a couple of weeks for relocation logistics.\n"
        "location_policy:\n"
        "  current_base: Denmark\n"
        "  onsite_roles: Will relocate for onsite roles.\n"
        "  preferred_regions:\n"
        "    - Denmark\n"
        "    - Sweden\n"
        "thresholds:\n"
        "  minimum_digest_score: 45\n",
        encoding="utf-8",
    )
    (profile / "skills.yaml").write_text(
        "experience_level:\n"
        "  sap_experience: 6+ years\n"
        "skills:\n"
        "  strongest:\n"
        "    - SAP ABAP\n"
        "    - RAP\n"
        "    - CDS Views\n"
        "    - OData / SAP Gateway\n"
        "    - Debugging\n"
        "  caveats:\n"
        "    fiori: Backend/Gateway experience for Fiori-related applications; not a pure UI5 expert.\n"
        "    project_management: Coordination experience, not formal PM ownership.\n",
        encoding="utf-8",
    )
    (profile / "experience.yaml").write_text(
        "experience:\n"
        "  - company: LEGO\n"
        "    role: SAP ABAP Consultant\n"
        "    highlights:\n"
        "      - Built backend and OData functionality for mobile QM solution.\n"
        "    keywords:\n"
        "      - ABAP\n"
        "      - OData\n"
        "      - QM\n",
        encoding="utf-8",
    )
    (profile / "canonical-cv.md").write_text("Canonical CV text\n", encoding="utf-8")
    (profile / "writing-style.md").write_text("Direct consultant tone.\n", encoding="utf-8")
    return profile


@pytest.fixture
def template_project(project_root: Path, minimal_profile: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    for template in (repo_root / "templates").glob("*.j2"):
        shutil.copy2(template, project_root / "templates" / template.name)
    return project_root


@pytest.fixture
def local_yaml_source_project(template_project: Path) -> Path:
    jobs_path = template_project / "jobs" / "raw" / "sample_jobs.yaml"
    jobs_path.write_text(
        "jobs:\n"
        "  - title: SAP ABAP RAP Consultant\n"
        "    company: Example Recruiter\n"
        "    source: Sample Jobs\n"
        "    url: https://example.com/job\n"
        "    location: Copenhagen\n"
        "    remote: Hybrid\n"
        "    posted_date: 2026-05-06\n"
        "    description: Strong ABAP RAP CDS OData Gateway S/4HANA contract role.\n",
        encoding="utf-8",
    )
    (template_project / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n  - name: Local Sample\n    type: local_yaml\n    path: jobs/raw/sample_jobs.yaml\n",
        encoding="utf-8",
    )
    return template_project
