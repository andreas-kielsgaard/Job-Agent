from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_agent.config import ROOT
from job_agent.paths import resolve_project_path
from job_agent.run_service import run_daily_agent
from job_agent.run_store import RunOptions, RunRecord, RunStore
from job_agent.services.source_listing_index_service import SourceListingIndexService
from job_agent.web.work_widgets import WorkStatusWidgetHandler


@dataclass
class SourceIndexTask:
    task_id: str
    source_id: str
    source_name: str
    status: str = "pending"
    page_explored_count: int = 0
    page_total: int = 0
    jobs_found: int = 0
    pagination_duplicate_page_count: int = 0
    pagination_strategy: str = ""
    message: str = ""
    started_at: str = ""
    finished_at: str = ""
    error_message: str = ""


@dataclass
class ProfileDraftTask:
    task_id: str
    title: str = "Drafting profile from CV"
    status: str = "pending"
    stage: str = "Preparing"
    message: str = ""
    progress_percent: int = 8
    started_at: str = ""
    finished_at: str = ""
    error_message: str = ""
    draft_id: str = ""
    href: str = ""


@dataclass
class SourceSessionCaptureTask:
    task_id: str
    source_id: str
    source_name: str
    source_url: str
    session_scope: str = ""
    storage_state_path: str = ""
    expires_at: str = ""
    status: str = "pending"
    title: str = ""
    stage: str = "Preparing"
    message: str = ""
    progress_percent: int = 8
    started_at: str = ""
    finished_at: str = ""
    error_message: str = ""
    href: str = ""


class WebRuntime:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.index_executor = ThreadPoolExecutor(max_workers=3)
        self.session_executor = ThreadPoolExecutor(max_workers=3)
        self._index_tasks: dict[str, SourceIndexTask] = {}
        self._index_lock = threading.Lock()
        self._profile_tasks: dict[str, ProfileDraftTask] = {}
        self._profile_lock = threading.Lock()
        self._session_tasks: dict[str, SourceSessionCaptureTask] = {}
        self._session_lock = threading.Lock()
        self.last_request_at = time.time()
        self.idle_monitor_started = False
        self.app_version = ""

    def mark_activity(self) -> None:
        self.last_request_at = time.time()

    def startup(self) -> None:
        self.app_version = compute_app_version(self.root)
        store = RunStore(self.root)
        try:
            store.recover_stale_runs()
        except ValueError:
            store.recover_corrupt_registry()
        if self.idle_monitor_started:
            return
        self.idle_monitor_started = True
        seconds = int(os.getenv("JOB_AGENT_IDLE_SHUTDOWN_SECONDS", "0") or "0")
        if seconds <= 0:
            return
        thread = threading.Thread(target=self._idle_shutdown_loop, args=(seconds,), daemon=True)
        thread.start()

    def health_payload(self) -> dict[str, str]:
        active_run = self.active_run()
        return {
            "status": "ok",
            "time": datetime.now(UTC).isoformat(),
            "app_version": self.app_version or compute_app_version(self.root),
            "root": str(self.root.resolve()),
            "active_run_id": active_run.run_id if active_run else "",
            "active_run_status": active_run.status if active_run else "",
        }

    def launch_daily_run(self, options: RunOptions) -> RunRecord:
        store = RunStore(self.root)
        record = store.create_run(options)
        self.executor.submit(run_daily_agent, options, None, self.root, record.run_id)
        return record

    def launch_source_detail_run(
        self,
        source_id: str,
        *,
        include_disabled_source: bool = False,
        append_to_today: bool = True,
    ) -> RunRecord:
        store = RunStore(self.root)
        record = self._latest_daily_run_today(store) if append_to_today else None
        options = RunOptions(
            include_seen=False,
            include_weak=False,
            mark_seen=True,
            generate_materials=False,
            use_llm=False,
            ai_enhanced_search=False,
            detail_extraction_limit=None,
            append_to_daily_run=bool(record),
        )
        append_to_existing = record is not None
        if record is None:
            record = store.create_run(options)
        self.executor.submit(
            run_daily_agent,
            options,
            None,
            self.root,
            record.run_id,
            source_id,
            include_disabled_source,
            append_to_existing,
        )
        return record

    def launch_source_listing_index(self, source_id: str, source_name: str = "") -> SourceIndexTask:
        task = SourceIndexTask(
            task_id=f"idx-{uuid4().hex[:10]}",
            source_id=source_id,
            source_name=source_name or source_id,
            status="pending",
            started_at=utc_now(),
            message="Waiting to start listing index.",
        )
        with self._index_lock:
            self._index_tasks[task.task_id] = task
        self.index_executor.submit(self._run_source_listing_index, task.task_id)
        return task

    def active_work_payload(self) -> dict[str, Any]:
        profile_tasks = self._active_profile_tasks()
        snapshot = {
            "active_run": self.active_run(),
            "index_tasks": self._active_index_sources(),
            "session_tasks": self._active_session_sources(),
            "profile_tasks": profile_tasks,
            "persisted_profile_tasks": self._active_persisted_profile_drafts(profile_tasks),
        }
        return WorkStatusWidgetHandler(self.root).active_work_payload(snapshot)

    def launch_source_session_capture(
        self,
        *,
        source_id: str,
        source_name: str,
        source_url: str,
        session_scope: str,
        storage_state_path: str,
        expires_at: str = "",
    ) -> SourceSessionCaptureTask:
        superseded_at = utc_now()
        task = SourceSessionCaptureTask(
            task_id=f"session-{uuid4().hex[:10]}",
            source_id=source_id,
            source_name=source_name or source_id,
            source_url=source_url,
            session_scope=session_scope,
            storage_state_path=storage_state_path,
            expires_at=expires_at,
            status="pending",
            title=f"Connecting {source_name or source_id}",
            stage="Preparing browser",
            message="Opening a browser window for this source.",
            progress_percent=10,
            started_at=utc_now(),
            href=f"/sources/{source_id}/session",
        )
        with self._session_lock:
            for existing in self._session_tasks.values():
                if (
                    existing.source_id == source_id
                    and existing.status in {"pending", "running"}
                    and not existing.finished_at
                ):
                    existing.status = "failed"
                    existing.stage = "Superseded"
                    existing.message = "A newer sign-in browser was opened for this source."
                    existing.progress_percent = 100
                    existing.error_message = "Superseded by a newer session capture."
                    existing.finished_at = superseded_at
            self._session_tasks[task.task_id] = task
        self.session_executor.submit(self._run_source_session_capture, task.task_id)
        return task

    def start_profile_draft_task(self, task_id: str = "", title: str = "Drafting profile from CV") -> ProfileDraftTask:
        task = ProfileDraftTask(
            task_id=task_id or f"profile-{uuid4().hex[:10]}",
            title=title,
            status="running",
            stage="Preparing",
            message="Starting CV profile draft.",
            progress_percent=8,
            started_at=utc_now(),
        )
        with self._profile_lock:
            self._profile_tasks[task.task_id] = task
        self._persist_profile_task(task)
        return task

    def update_profile_draft_task(self, task_id: str, **updates: Any) -> None:
        with self._profile_lock:
            task = self._profile_tasks.get(task_id)
            if not task:
                return
            for key, value in updates.items():
                setattr(task, key, value)
            self._persist_profile_task(task)

    def finish_profile_draft_task(
        self,
        task_id: str,
        *,
        status: str = "completed",
        message: str = "Profile draft is ready.",
        error_message: str = "",
        draft_id: str = "",
        href: str = "",
    ) -> None:
        self.update_profile_draft_task(
            task_id,
            status=status,
            stage="Finished" if status == "completed" else "Stopped",
            message=message,
            progress_percent=100 if status == "completed" else 100,
            error_message=error_message,
            finished_at=utc_now(),
            draft_id=draft_id,
            href=href,
        )

    def active_run(self) -> RunRecord | None:
        store = RunStore(self.root)
        try:
            runs = store.list_runs()
        except ValueError:
            store.recover_corrupt_registry()
            runs = []
        return next((run for run in runs if run.status in {"pending", "running"}), None)

    def _run_source_listing_index(self, task_id: str) -> None:
        self._update_index_task(task_id, status="running", message="Starting listing index.")

        def progress(event: dict[str, Any]) -> None:
            phase = str(event.get("phase") or "Indexing")
            detail = str(event.get("detail") or "")
            updates: dict[str, Any] = {
                "status": "running",
                "message": f"{phase}: {detail}" if detail else phase,
            }
            if _int(event.get("page_explored_count")):
                updates["page_explored_count"] = _int(event.get("page_explored_count"))
            if _int(event.get("page_total")):
                updates["page_total"] = _int(event.get("page_total"))
            if _int(event.get("jobs_found")):
                updates["jobs_found"] = _int(event.get("jobs_found"))
            self._update_index_task(task_id, **updates)

        try:
            with self._index_lock:
                task = self._index_tasks[task_id]
                source_id = task.source_id
            result = SourceListingIndexService(self.root).index(source_id, progress_callback=progress)
            with self._index_lock:
                current = self._index_tasks[task_id]
                pages_explored = current.page_explored_count
                page_total = current.page_total
            self._update_index_task(
                task_id,
                status=result.status,
                page_explored_count=max(result.page_explored_count, pages_explored, 1),
                page_total=max(result.page_total, page_total, pages_explored, 1),
                jobs_found=result.job_count,
                pagination_duplicate_page_count=result.pagination_duplicate_page_count,
                pagination_strategy=result.pagination_strategy,
                message=result.summary,
                finished_at=utc_now(),
            )
        except Exception as exc:
            self._update_index_task(
                task_id,
                status="failed",
                message=f"Listing index failed: {exc}",
                error_message=str(exc),
                finished_at=utc_now(),
            )

    def _update_index_task(self, task_id: str, **updates: Any) -> None:
        with self._index_lock:
            task = self._index_tasks.get(task_id)
            if not task:
                return
            for key, value in updates.items():
                setattr(task, key, value)

    def _active_index_sources(self) -> list[dict[str, Any]]:
        now = time.time()
        items = []
        stale_ids = []
        with self._index_lock:
            for task_id, task in self._index_tasks.items():
                if task.finished_at:
                    try:
                        finished = datetime.fromisoformat(task.finished_at).timestamp()
                    except ValueError:
                        finished = now
                    if now - finished > 20:
                        stale_ids.append(task_id)
                        continue
                payload = asdict(task)
                payload.update(
                    {
                        "kind": "index",
                        "title": f"Indexing {task.source_name}",
                        "status": task.status,
                    }
                )
                items.append(payload)
            for task_id in stale_ids:
                self._index_tasks.pop(task_id, None)
        return items

    def _run_source_session_capture(self, task_id: str) -> None:
        if not self._is_current_session_capture(task_id):
            return
        self._update_session_task(
            task_id,
            status="running",
            stage="Launching browser",
            message="Starting a sign-in browser window for this source. This can take a moment.",
            progress_percent=18,
        )
        try:
            with self._session_lock:
                task = self._session_tasks[task_id]
            self._capture_source_session_to_file(task)
            if not self._is_current_session_capture(task_id, task.source_id):
                return
            from job_agent.services.source_session_service import SourceSessionService

            SourceSessionService(self.root).record_storage_state(
                task.source_id,
                session_scope=task.session_scope,
                storage_state_path=task.storage_state_path,
                expires_at=task.expires_at,
            )
            self._update_session_task(
                task_id,
                status="completed",
                stage="Session saved",
                message="Browser session saved. Verify it before daily runs rely on it.",
                progress_percent=100,
                finished_at=utc_now(),
            )
        except Exception as exc:
            if not self._is_current_session_capture(task_id):
                return
            self._update_session_task(
                task_id,
                status="failed",
                stage="Capture failed",
                message=f"Browser session capture failed: {exc}",
                progress_percent=100,
                error_message=str(exc),
                finished_at=utc_now(),
            )

    def _capture_source_session_to_file(self, task: SourceSessionCaptureTask) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Browser session capture requires Playwright to be installed.") from exc

        target_path = Path(task.storage_state_path)
        if not target_path.is_absolute():
            target_path = resolve_project_path(self.root, target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        close_event = threading.Event()
        timeout_seconds = int(os.getenv("JOB_AGENT_SESSION_CAPTURE_TIMEOUT_SECONDS", "900") or "900")
        autosave_interval_seconds = 0.5
        last_save_at = 0.0
        last_save_error = ""

        def saved_state_exists() -> bool:
            try:
                return target_path.exists() and target_path.stat().st_size > 0
            except OSError:
                return False

        try:
            with sync_playwright() as playwright:
                self._update_session_task(
                    task.task_id,
                    status="running",
                    stage="Launching browser",
                    message="Starting Chromium for source sign-in.",
                    progress_percent=22,
                )
                browser = playwright.chromium.launch(headless=False)
                browser.on("disconnected", lambda *args: close_event.set())
                try:
                    context = browser.new_context()
                    page = context.new_page()
                    self._update_session_task(
                        task.task_id,
                        status="running",
                        stage="Loading sign-in page",
                        message="Browser is open; loading the source page before you sign in.",
                        progress_percent=28,
                    )

                    def attach_close_listener(open_page) -> None:
                        open_page.on("close", lambda *args: close_event.set())

                    def live_pages() -> list[Any]:
                        return [open_page for open_page in context.pages if not open_page.is_closed()]

                    attach_close_listener(page)
                    context.on("page", attach_close_listener)
                    page.goto(task.source_url, wait_until="domcontentloaded", timeout=30_000)
                    self._update_session_task(
                        task.task_id,
                        status="running",
                        stage="Browser ready",
                        message="Sign in in the opened browser window, then close that window to save the session.",
                        progress_percent=35,
                    )
                    context.storage_state(path=str(target_path))
                    last_save_at = time.time()
                    while True:
                        if close_event.wait(0.5):
                            if not live_pages():
                                break
                            close_event.clear()
                        if not browser.is_connected() or not live_pages():
                            break
                        now = time.time()
                        if now - last_save_at >= autosave_interval_seconds:
                            try:
                                context.storage_state(path=str(target_path))
                                last_save_at = now
                                last_save_error = ""
                            except PlaywrightError as exc:
                                last_save_error = str(exc)
                        if (
                            timeout_seconds > 0
                            and time.time() - datetime.fromisoformat(task.started_at).timestamp() > timeout_seconds
                        ):
                            break
                    if browser.is_connected():
                        try:
                            context.storage_state(path=str(target_path))
                        except PlaywrightError as exc:
                            last_save_error = str(exc)
                    if not saved_state_exists():
                        raise RuntimeError(
                            "The sign-in browser closed before the session could be saved."
                            + (f" Last browser error: {last_save_error}" if last_save_error else "")
                        )
                    if browser.is_connected():
                        context.close()
                        browser.close()
                except Exception:
                    if browser.is_connected():
                        browser.close()
                    raise
        except PlaywrightError as exc:
            raise RuntimeError(str(exc)) from exc

    def _is_current_session_capture(self, task_id: str, source_id: str = "") -> bool:
        with self._session_lock:
            task = self._session_tasks.get(task_id)
            if not task or task.finished_at:
                return False
            if source_id and task.source_id != source_id:
                return False
            return task.status in {"pending", "running"}

    def _update_session_task(self, task_id: str, **updates: Any) -> None:
        with self._session_lock:
            task = self._session_tasks.get(task_id)
            if not task:
                return
            for key, value in updates.items():
                setattr(task, key, value)

    def _active_session_sources(self) -> list[dict[str, Any]]:
        now = time.time()
        items = []
        stale_ids = []
        with self._session_lock:
            for task_id, task in self._session_tasks.items():
                if task.finished_at:
                    try:
                        finished = datetime.fromisoformat(task.finished_at).timestamp()
                    except ValueError:
                        finished = now
                    if now - finished > 30:
                        stale_ids.append(task_id)
                        continue
                payload = asdict(task)
                payload.update(
                    {
                        "kind": "session",
                        "source_name": task.source_name,
                        "status": task.status,
                    }
                )
                items.append(payload)
            for task_id in stale_ids:
                self._session_tasks.pop(task_id, None)
        return items

    def _active_profile_tasks(self) -> list[dict[str, Any]]:
        now = time.time()
        items = []
        stale_ids = []
        with self._profile_lock:
            for task_id, task in self._profile_tasks.items():
                if task.finished_at:
                    try:
                        finished = datetime.fromisoformat(task.finished_at).timestamp()
                    except ValueError:
                        finished = now
                    keep_until_reviewed = bool(task.draft_id and self._has_active_profile_draft(task.draft_id))
                    if task.draft_id and not keep_until_reviewed:
                        stale_ids.append(task_id)
                        continue
                    if not keep_until_reviewed and now - finished > 20:
                        stale_ids.append(task_id)
                        continue
                payload = asdict(task)
                payload.update(
                    {
                        "kind": "profile",
                        "source_name": "Profile",
                    }
                )
                items.append(payload)
            for task_id in stale_ids:
                self._profile_tasks.pop(task_id, None)
        return items

    def _active_persisted_profile_drafts(self, profile_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing_task_ids = {str(item.get("task_id") or "") for item in profile_tasks}
        existing_draft_ids = {str(item.get("draft_id") or "") for item in profile_tasks}
        try:
            from job_agent.services.cv_profile_draft_service import CvProfileDraftService

            service = CvProfileDraftService(self.root)
            task = service.active_task()
            if task and str(task.get("task_id") or "") not in existing_task_ids:
                return [task]
            item = service.active_work_item()
        except Exception:
            return []
        if not item or str(item.get("draft_id") or "") in existing_draft_ids:
            return []
        return [item]

    def _has_active_profile_draft(self, draft_id: str) -> bool:
        try:
            from job_agent.services.cv_profile_draft_service import CvProfileDraftService

            return CvProfileDraftService(self.root).has_active_draft(draft_id)
        except Exception:
            return False

    def _persist_profile_task(self, task: ProfileDraftTask) -> None:
        try:
            from job_agent.services.cv_profile_draft_service import CvProfileDraftService

            CvProfileDraftService(self.root).save_task(asdict(task))
        except Exception:
            return

    def _latest_daily_run_today(self, store: RunStore) -> RunRecord | None:
        today = str(date.today())
        for run in store.list_runs(include_tests=False):
            if run.visibility != "active" or not run.started_at.startswith(today):
                continue
            if run.status in {"completed", "pending", "running"}:
                return run
        return None

    def has_active_run(self) -> bool:
        try:
            return self.active_run() is not None
        except Exception:
            return True

    def _idle_shutdown_loop(self, seconds: int) -> None:
        while True:
            time.sleep(10)
            if time.time() - self.last_request_at < seconds:
                continue
            if self.has_active_run():
                continue
            # Intentional local-launcher shutdown path. The double-click Windows launcher starts a private
            # localhost server for one user; if the browser has been idle and no agent run is active, a hard
            # process exit avoids leaving a background server behind. This is not intended for hosted use.
            os._exit(0)


def compute_app_version(root: Path) -> str:
    hasher = hashlib.sha256()
    patterns = [
        "job_agent/**/*.py",
        "app/code/job_agent/**/*.py",
        "job_agent/web/templates/**/*.html",
        "app/code/job_agent/web/templates/**/*.html",
        "job_agent/web/static/**/*",
        "app/code/job_agent/web/static/**/*",
        "templates/**/*.j2",
        "app/resources/templates/**/*.j2",
        "prompts/**/*.md",
        "app/resources/prompts/**/*.md",
        "requirements.txt",
        "app/environment/requirements.txt",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


runtime = WebRuntime(ROOT)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
