from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from job_agent.config import ROOT
from job_agent.web import dependencies
from job_agent.web.app import create_app
from job_agent.web.runtime import runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_STATE_PATHS = ("runtime", "user")
PYTEST_PROGRESS_DIR = REPO_ROOT / ".pytest-progress"
PYTEST_PROGRESS_JSON = PYTEST_PROGRESS_DIR / "latest.json"
PYTEST_PROGRESS_TEXT = PYTEST_PROGRESS_DIR / "latest.txt"
SLOW_TEST_FILES = {
    "tests/test_job_board_recipe_service.py",
    "tests/test_run_service.py",
    "tests/test_web_recipe_generation.py",
    "tests/test_web_smoke.py",
}


@dataclass
class _TestFileProgress:
    total: int = 0
    completed: int = 0
    failed: bool = False
    failed_items: list[str] = field(default_factory=list)


class _PytestProgressReporter:
    def __init__(self, config: pytest.Config, items: list[pytest.Item]) -> None:
        self._terminal = config.pluginmanager.get_plugin("terminalreporter")
        self._started_at = datetime.now(UTC).isoformat()
        self._file_order: list[str] = []
        self._file_index: dict[str, int] = {}
        self._files: dict[str, _TestFileProgress] = {}
        self._file_started_at: dict[str, float] = {}
        self._completed_files: list[str] = []
        self._failed_files: list[str] = []
        self._current_file: str | None = None
        self._current_test: str | None = None

        for item in items:
            file_path = _display_test_path(item.path)
            if file_path not in self._files:
                self._file_index[file_path] = len(self._file_order) + 1
                self._file_order.append(file_path)
                self._files[file_path] = _TestFileProgress()
            self._files[file_path].total += 1

        self._write_progress("collected")

    def start_test(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        file_path = _display_test_path(location[0])
        if self._current_file != file_path:
            self._current_file = file_path
            self._file_started_at[file_path] = time.monotonic()
            self._line(f"[pytest-progress] RUN {self._file_index[file_path]}/{len(self._file_order)} {file_path}")
        self._current_test = nodeid
        self._write_progress("running")

    def record_report(self, report: pytest.TestReport) -> None:
        file_path = _nodeid_file_path(report.nodeid)
        file_progress = self._files.get(file_path)
        if file_progress is None:
            return

        if report.failed:
            file_progress.failed = True
            if report.nodeid not in file_progress.failed_items:
                file_progress.failed_items.append(report.nodeid)

        if report.when != "teardown":
            return

        file_progress.completed += 1
        if file_progress.completed >= file_progress.total:
            self._finish_file(file_path, file_progress)

    def finish(self, exitstatus: int | pytest.ExitCode) -> None:
        unfinished = self._unfinished_files()
        if unfinished:
            current = self._current_file or unfinished[0]
            self._line(f"[pytest-progress] STOPPED while running {current}")
            status = "stopped"
        elif self._failed_files:
            self._line(
                f"[pytest-progress] FAIL {len(self._failed_files)} test file(s); {len(self._completed_files)} passed"
            )
            status = "failed"
        elif exitstatus == pytest.ExitCode.OK:
            self._line(f"[pytest-progress] PASS all {len(self._completed_files)} test files")
            status = "finished"
        else:
            status = "finished"
        self._write_progress(status)

    def _finish_file(self, file_path: str, file_progress: _TestFileProgress) -> None:
        duration = time.monotonic() - self._file_started_at.get(file_path, time.monotonic())
        completed_count = len(self._completed_files) + len(self._failed_files) + 1
        outcome = "FAIL" if file_progress.failed else "PASS"
        self._line(
            f"[pytest-progress] {outcome} {completed_count}/{len(self._file_order)} "
            f"{file_path} ({file_progress.completed} tests, {duration:.1f}s)"
        )

        if file_progress.failed:
            self._failed_files.append(file_path)
        else:
            self._completed_files.append(file_path)
        if self._current_file == file_path:
            self._current_file = None
            self._current_test = None
        self._write_progress(outcome.lower())

    def _unfinished_files(self) -> list[str]:
        done = set(self._completed_files) | set(self._failed_files)
        return [file_path for file_path in self._file_order if file_path not in done]

    def _line(self, message: str) -> None:
        if self._terminal is not None:
            self._terminal.write_line(message)

    def _write_progress(self, status: str) -> None:
        remaining_files = self._unfinished_files()
        payload: dict[str, Any] = {
            "status": status,
            "started_at": self._started_at,
            "updated_at": datetime.now(UTC).isoformat(),
            "current_file": self._current_file,
            "current_test": self._current_test,
            "total_files": len(self._file_order),
            "passed_files": self._completed_files,
            "failed_files": self._failed_files,
            "remaining_files": remaining_files,
            "progress_note": ("If pytest is interrupted, rerun failed/current files first, then remaining files."),
        }
        text_lines = [
            f"status: {payload['status']}",
            f"updated_at: {payload['updated_at']}",
            f"current_file: {payload['current_file'] or ''}",
            f"current_test: {payload['current_test'] or ''}",
            f"passed_files: {len(self._completed_files)}/{len(self._file_order)}",
            "passed:",
            *[f"  - {file_path}" for file_path in self._completed_files],
            "failed:",
            *[f"  - {file_path}" for file_path in self._failed_files],
            "remaining:",
            *[f"  - {file_path}" for file_path in remaining_files],
        ]

        with suppress(OSError):
            PYTEST_PROGRESS_DIR.mkdir(exist_ok=True)
            _atomic_write_text(PYTEST_PROGRESS_JSON, json.dumps(payload, indent=2) + "\n")
            _atomic_write_text(PYTEST_PROGRESS_TEXT, "\n".join(text_lines) + "\n")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--job-agent-progress",
        action="store_true",
        help="Write Job Agent pytest progress breadcrumbs under .pytest-progress/.",
    )
    parser.addoption(
        "--repo-state-audit",
        action="store_true",
        help="Audit product tests for accidental mutation of repo user/ and runtime/ state.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        file_path = _display_test_path(item.path)
        if file_path.startswith("tests/test_web"):
            item.add_marker(pytest.mark.web)
        if file_path.endswith("_service.py"):
            item.add_marker(pytest.mark.service)
        if file_path in SLOW_TEST_FILES:
            item.add_marker(pytest.mark.slow)


def pytest_collection_finish(session: pytest.Session) -> None:
    global _PYTEST_PROGRESS_REPORTER
    if not _progress_enabled(session.config):
        _PYTEST_PROGRESS_REPORTER = None
        return
    _PYTEST_PROGRESS_REPORTER = _PytestProgressReporter(session.config, session.items)


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    progress = _get_progress_reporter()
    if progress is not None:
        progress.start_test(nodeid, location)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    progress = _get_progress_reporter()
    if progress is not None:
        progress.record_report(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    if _PYTEST_PROGRESS_REPORTER is not None:
        _PYTEST_PROGRESS_REPORTER.finish(exitstatus)


def _get_progress_reporter() -> _PytestProgressReporter | None:
    return _PYTEST_PROGRESS_REPORTER


def _progress_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("--job-agent-progress") or _truthy_env("JOB_AGENT_PYTEST_PROGRESS"))


def _repo_state_audit_enabled(request: pytest.FixtureRequest) -> bool:
    return bool(
        request.config.getoption("--repo-state-audit")
        or request.node.get_closest_marker("mutation_audit")
        or _truthy_env("JOB_AGENT_REPO_STATE_AUDIT")
    )


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.lower() in {"1", "true", "yes", "on"}


def _display_test_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _nodeid_file_path(nodeid: str) -> str:
    return Path(nodeid.split("::", 1)[0]).as_posix()


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    finally:
        with suppress(OSError):
            temp_path.unlink()


_PYTEST_PROGRESS_REPORTER: _PytestProgressReporter | None = None


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("External network/API call attempted during test")

    monkeypatch.setattr("requests.get", blocked, raising=False)
    monkeypatch.setattr("requests.post", blocked, raising=False)
    monkeypatch.setattr("requests.put", blocked, raising=False)
    monkeypatch.setattr("requests.patch", blocked, raising=False)
    monkeypatch.setattr("requests.delete", blocked, raising=False)
    monkeypatch.setattr("requests.head", blocked, raising=False)
    monkeypatch.setattr("requests.options", blocked, raising=False)
    monkeypatch.setattr("requests.request", blocked, raising=False)
    monkeypatch.setattr("requests.sessions.Session.request", blocked, raising=False)


@pytest.fixture(autouse=True)
def no_claude_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def product_tests_do_not_mutate_repo_state(request: pytest.FixtureRequest):
    if request.node.get_closest_marker("exploratory") or not _repo_state_audit_enabled(request):
        yield
        return
    before = _snapshot_app_state()
    yield
    after = _snapshot_app_state()
    changed = _changed_paths(before, after)
    assert not changed, (
        "Repo state audit failed. Product tests must use project_root/tmp_path and leave repo app state untouched. "
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
    for template in (repo_root / "app" / "resources" / "templates").glob("*.j2"):
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


def _snapshot_app_state() -> dict[str, tuple[str, int, int]]:
    snapshot: dict[str, tuple[str, int, int]] = {}
    for relative in APP_STATE_PATHS:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        for item in sorted(path.rglob("*")):
            item_relative = item.relative_to(REPO_ROOT).as_posix()
            if item.is_dir():
                snapshot[item_relative] = ("dir", 0, item.stat().st_mtime_ns)
            elif item.is_file():
                stat = item.stat()
                snapshot[item_relative] = ("file", stat.st_size, stat.st_mtime_ns)
    return snapshot


def _changed_paths(
    before: dict[str, tuple[str, int, int]],
    after: dict[str, tuple[str, int, int]],
) -> list[str]:
    paths = sorted(set(before) | set(after))
    return [path for path in paths if before.get(path) != after.get(path)]
