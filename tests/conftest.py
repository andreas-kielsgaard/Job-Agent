from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.config import ROOT
from job_agent.web import dependencies
from job_agent.web.app import create_app
from job_agent.web.runtime import runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_STATE_PATHS = ("jobs", "sources", "output")


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("External network/API call attempted during test")

    monkeypatch.setattr("requests.get", blocked, raising=False)


@pytest.fixture(autouse=True)
def no_claude_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def product_tests_do_not_mutate_repo_state(request: pytest.FixtureRequest):
    if request.node.get_closest_marker("exploratory"):
        yield
        return
    before = _snapshot_app_state()
    yield
    after = _snapshot_app_state()
    changed = _changed_paths(before, after)
    assert not changed, (
        "Product tests must use project_root/tmp_path and leave repo app state untouched. "
        f"Mutated paths: {', '.join(changed[:12])}"
    )


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
def client(project_root: Path, minimal_profile: Path):
    _set_app_root(project_root)
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        _set_app_root(ROOT)


def _set_app_root(root: Path) -> None:
    dependencies._current_root = root
    runtime.root = root


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
        "  minimum_digest_score: 45\n"
        "match_engine:\n"
        "  remote_policy: slight_preference\n"
        "  permanent_policy: penalize\n"
        "  permanent_penalty: -25\n"
        "  technical_cap: 55\n"
        "  module_cap: 25\n"
        "  technical_keyword_groups:\n"
        "    - label: ABAP core\n"
        "      terms: [abap, sap abap, abap oo]\n"
        "      score: 22\n"
        "      mode: bonus\n"
        "    - label: RAP\n"
        "      terms: [rap, restful application programming]\n"
        "      score: 12\n"
        "      mode: bonus\n"
        "    - label: CDS\n"
        "      terms: [cds, cds views]\n"
        "      score: 10\n"
        "      mode: bonus\n"
        "    - label: OData / Gateway\n"
        "      terms: [odata, gateway, sap gateway]\n"
        "      score: 10\n"
        "      mode: bonus\n"
        "  module_keyword_groups:\n"
        "    - label: QM\n"
        "      terms: [qm, quality management]\n"
        "      score: 7\n"
        "      mode: bonus\n"
        "  contract_keyword_groups:\n"
        "    - label: Contract / freelance\n"
        "      terms: [contract, freelance]\n"
        "      score: 8\n"
        "      mode: bonus\n",
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


def _snapshot_app_state() -> dict[str, tuple[str, int, str]]:
    snapshot: dict[str, tuple[str, int, str]] = {}
    for relative in APP_STATE_PATHS:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        for item in sorted(path.rglob("*")):
            item_relative = item.relative_to(REPO_ROOT).as_posix()
            if item.is_dir():
                snapshot[item_relative] = ("dir", 0, "")
            elif item.is_file():
                digest = hashlib.sha256(item.read_bytes()).hexdigest()
                snapshot[item_relative] = ("file", item.stat().st_size, digest)
    return snapshot


def _changed_paths(
    before: dict[str, tuple[str, int, str]],
    after: dict[str, tuple[str, int, str]],
) -> list[str]:
    paths = sorted(set(before) | set(after))
    return [path for path in paths if before.get(path) != after.get(path)]
