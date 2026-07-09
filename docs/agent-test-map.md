# Agent Test Map

Use this file to choose focused verification from the files you touched. Prefer `python app/environment/scripts/test_handler.py ...` when an agent is running tests; pass the targeted test files shown below as handler arguments.

## Baseline Commands

| Situation | Command |
| --- | --- |
| Targeted iteration | `python app/environment/scripts/test_handler.py tests/test_scoring.py` |
| Profile a target | `python app/environment/scripts/test_handler.py --profile tests/test_scoring.py` |
| Fast product suite | `python app/environment/scripts/test_handler.py --fast` |
| Full non-exploratory suite | `python app/environment/scripts/test_handler.py --full` |
| Normal local quality gate | `.\app\environment\scripts\check.ps1` |
| Release/audit quality gate | `.\app\environment\scripts\check-release.ps1` |
| Lint only | `python -m ruff check .` |
| Format check only | `python -m ruff format --check .` |
| Coverage report | `python app/environment/scripts/test_handler.py --coverage` |
| Repo-state mutation audit | `python app/environment/scripts/test_handler.py --repo-state-audit tests/test_web_smoke.py` |

The default pytest options in `pyproject.toml` run `tests/` quietly and exclude `exploratory` tests. Product tests use temp roots and block external network/API calls through `requests` entry points.

The fast suite is the normal handoff gate. It excludes tests marked `slow` or `browser`, but still includes core web smoke coverage for app creation, health/work-status routes, basic local UI route rendering, static icons, setup/dashboard/jobs/runs route smoke, and cheap guard routes. Broad source workflows, recipe-generation flows, source-test lifecycle checks, CV/LLM-style setup flows, and deeper form-posting scenarios stay in `--full`.

Route broad runs through `python app/environment/scripts/test_handler.py`. The handler enables `[pytest-progress]` lines as each test file starts and finishes. It also writes `.pytest-progress/latest.txt` and `.pytest-progress/latest.json`; after a timeout, inspect those files to see passed files, failed files, the current file/test, and the remaining files before rerunning work. Raw `python -m pytest` avoids that progress-file overhead unless `--job-agent-progress` or `JOB_AGENT_PYTEST_PROGRESS=1` is set.

Repo-state mutation auditing is opt-in through `--repo-state-audit`, `JOB_AGENT_REPO_STATE_AUDIT=1`, or the `mutation_audit` marker. Use it for release/audit checks and for tests that might accidentally touch ignored `user/` or `runtime/` state. Normal product tests should still write through temp project roots.

## Touched Files To Tests

| Touched Area | Run These Tests First |
| --- | --- |
| `app/code/job_agent/scoring.py`, scoring policy in `setup/defaults/profile/preferences.yaml`, `docs/matching-and-ai-review.md` | `python -m pytest tests/test_scoring.py tests/test_highlights.py tests/test_profile_contract.py` |
| `app/code/job_agent/highlights.py` | `python -m pytest tests/test_highlights.py tests/test_scoring.py` |
| `app/code/job_agent/models.py` | `python -m pytest tests/test_scoring.py tests/test_sources.py tests/test_run_service.py tests/test_generation_caveats.py` |
| `app/code/job_agent/config.py`, profile loading, `setup/defaults/profile/*` | `python -m pytest tests/test_profile_contract.py tests/test_setup_service.py tests/test_generation_caveats.py tests/test_web_smoke.py` |
| `app/code/job_agent/generator.py`, `app/resources/templates/*.j2`, `app/resources/prompts/generate_application.md`, `app/resources/prompts/generate_form_answers.md` | `python -m pytest tests/test_generation_caveats.py tests/test_material_service.py tests/test_templates.py tests/test_pipeline.py` |
| `app/code/job_agent/services/application_examples_service.py`, `setup/defaults/profile/application-examples.yaml` | `python -m pytest tests/test_generation_caveats.py tests/test_review_bundle_service.py tests/test_setup_service.py` |
| `app/code/job_agent/services/ai_search_service.py`, `app/resources/prompts/evaluate_job_relevance.md`, `app/resources/prompts/score_job_assist.md` | `python -m pytest tests/test_ai_search_service.py tests/test_run_service.py tests/test_manual_posting_service.py` |
| `app/code/job_agent/llm/*`, `app/code/job_agent/services/ai_edit_service.py` | `python -m pytest tests/test_llm_service.py tests/test_ai_search_service.py tests/test_generation_caveats.py tests/test_web.py` |
| `app/code/job_agent/run_service.py`, daily run options, package/run event flow | `python -m pytest tests/test_run_service.py tests/test_run_state.py tests/test_pipeline.py tests/test_sources.py tests/test_ai_search_service.py` |
| `app/code/job_agent/run_store.py` | `python -m pytest tests/test_run_store.py tests/test_run_state.py tests/test_web.py tests/test_web_smoke.py` |
| `app/code/job_agent/store.py`, `app/resources/jobs/raw/sample_jobs.yaml` | `python -m pytest tests/test_job_store.py tests/test_store.py tests/test_sources.py tests/test_pipeline.py` |
| `app/code/job_agent/application_status_store.py` | `python -m pytest tests/test_application_status_store.py tests/test_web.py tests/test_web_smoke.py` |
| `app/code/job_agent/application_store.py`, `app/code/job_agent/email_store.py`, `app/code/job_agent/services/application_*`, `app/code/job_agent/services/email_*`, `app/code/job_agent/services/gmail_email_provider.py`, application routes/templates | `python app/environment/scripts/test_handler.py tests/test_application_tracking.py tests/test_applications_web.py tests/test_gmail_sync_service.py tests/test_setup_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/io/*` | `python -m pytest tests/test_job_store.py tests/test_run_store.py tests/test_setup_service.py tests/test_application_status_store.py` |
| `app/code/job_agent/digest.py`, output package indexes | `python -m pytest tests/test_package_index_service.py tests/test_pipeline.py tests/test_templates.py tests/test_material_service.py` |
| `app/code/job_agent/services/material_service.py` | `python -m pytest tests/test_material_service.py tests/test_generation_caveats.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/review_bundle_service.py` | `python -m pytest tests/test_review_bundle_service.py tests/test_generation_caveats.py tests/test_web.py` |
| `app/code/job_agent/sources.py`, `sources/recruiting-sites.yaml` | `python -m pytest tests/test_sources.py tests/test_source_test_service.py tests/test_single_source_run_service.py tests/test_pipeline.py` |
| `app/code/job_agent/services/execution_source_service.py` | `python -m pytest tests/test_execution_source_service.py tests/test_source_execution_readiness_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/source_registry_service.py`, `sources/source-registry.yaml` | `python -m pytest tests/test_source_registry_service.py tests/test_source_status_view_model.py tests/test_web_smoke.py tests/test_web_workflows.py` |
| `app/code/job_agent/services/source_health_service.py`, `sources/source-health.yaml` | `python -m pytest tests/test_source_health_service.py tests/test_source_execution_readiness_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/source_execution_readiness_service.py`, `sources/source-execution-readiness.yaml` | `python -m pytest tests/test_source_execution_readiness_service.py tests/test_web_source_go_live_readiness.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/source_test_service.py`, `app/code/job_agent/services/source_test_log_service.py` | `python -m pytest tests/test_source_test_service.py tests/test_web_smoke.py tests/test_web_workflows.py` |
| `app/code/job_agent/services/source_session_service.py` | `python -m pytest tests/test_source_session_service.py tests/test_web_runtime.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/source_listing_index_store.py`, `app/code/job_agent/services/source_listing_index_service.py` | `python -m pytest tests/test_source_listing_index_service.py tests/test_web.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/job_board_recipe_service.py`, `app/code/job_agent/services/recipes/*`, recipe YAML in `sources/recipes/` | `python -m pytest tests/test_job_board_recipe_service.py tests/test_job_board_check_service.py tests/test_sources.py tests/test_source_test_service.py` |
| `app/code/job_agent/services/recipe_preview_service.py`, preview routes/templates | `python -m pytest tests/test_job_board_recipe_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/recipe_calibration_service.py` | `python -m pytest tests/test_recipe_calibration_service.py tests/test_recipe_suggestion_service.py tests/test_web_recipe_generation.py` |
| `app/code/job_agent/services/recipe_suggestion_service.py` | `python -m pytest tests/test_recipe_suggestion_service.py tests/test_recipe_generation_workflow.py tests/test_recipe_candidate_service.py` |
| `app/code/job_agent/services/recipe_generation_run_service.py`, generation status | `python -m pytest tests/test_recipe_generation_workflow.py tests/test_web_recipe_generation.py tests/test_web_workflows.py` |
| `app/code/job_agent/services/recipe_candidate_service.py`, `recipe_candidate_policy.py` | `python -m pytest tests/test_recipe_candidate_service.py tests/test_recipe_generation_workflow.py tests/test_web_recipe_generation.py` |
| `app/code/job_agent/services/recipe_candidate_approval_service.py` | `python -m pytest tests/test_recipe_candidate_approval_service.py tests/test_web_recipe_candidate_approval.py tests/test_approved_recipe_adoption_service.py` |
| `app/code/job_agent/services/approved_recipe_adoption_service.py` | `python -m pytest tests/test_approved_recipe_adoption_service.py tests/test_web_approved_recipe_adoption.py tests/test_web_source_go_live_readiness.py` |
| `app/code/job_agent/services/recipe_artifact_service.py` | `python -m pytest tests/test_recipe_artifact_service.py tests/test_web_recipe_generation.py` |
| `app/code/job_agent/services/single_source_run_service.py` | `python -m pytest tests/test_single_source_run_service.py tests/test_source_execution_readiness_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/setup_service.py`, setup profile writes | `python -m pytest tests/test_setup_service.py tests/test_profile_contract.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/cv_reference_service.py` | `python -m pytest tests/test_cv_reference_service.py tests/test_setup_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/cv_profile_draft_service.py` | `python -m pytest tests/test_setup_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/services/manual_posting_service.py`, posting intake UI | `python -m pytest tests/test_manual_posting_service.py tests/test_web_smoke.py tests/test_run_service.py` |
| `app/code/job_agent/services/package_index_service.py` | `python -m pytest tests/test_package_index_service.py tests/test_review_bundle_service.py tests/test_web.py` |
| `app/code/job_agent/services/stats_service.py` | `python -m pytest tests/test_stats_service.py tests/test_web.py` |
| `app/code/job_agent/web/app.py`, router registration, middleware/startup | `python -m pytest tests/test_web_smoke.py tests/test_web_runtime.py tests/test_web.py` |
| `app/code/job_agent/web/workflows.py`, `app/code/job_agent/web/source_workflow.py` | `python -m pytest tests/test_web_workflows.py tests/test_web_smoke.py tests/test_web_source_go_live_readiness.py tests/test_web_recipe_generation.py` |
| `app/code/job_agent/web/routers/dashboard.py`, dashboard template/view models | `python -m pytest tests/test_web.py tests/test_web_smoke.py` |
| `app/code/job_agent/web/routers/runs.py`, run templates/view models | `python -m pytest tests/test_web.py tests/test_web_smoke.py tests/test_run_view_models.py` |
| `app/code/job_agent/web/routers/jobs.py`, jobs templates/view models | `python -m pytest tests/test_web.py tests/test_web_smoke.py tests/test_run_view_models.py tests/test_material_service.py` |
| `app/code/job_agent/web/routers/sources.py`, source templates | `python -m pytest tests/test_web_smoke.py tests/test_web_workflows.py tests/test_web_source_go_live_readiness.py tests/test_web_recipe_generation.py` |
| `app/code/job_agent/web/routers/setup.py`, `app/code/job_agent/web/templates/setup.html` | `python -m pytest tests/test_setup_service.py tests/test_web_smoke.py tests/test_profile_contract.py` |
| `app/code/job_agent/web/routers/recipe_editor.py`, `recipe_editor.html` | `python -m pytest tests/test_web_recipe_editor.py tests/test_job_board_recipe_service.py` |
| `app/code/job_agent/web/routers/recipe_preview.py`, `recipe_preview.html` | `python -m pytest tests/test_web_smoke.py tests/test_job_board_recipe_service.py` |
| `app/code/job_agent/web/routers/compatibility.py` | `python -m pytest tests/test_job_board_check_service.py tests/test_web_smoke.py` |
| `app/code/job_agent/web/routers/files.py` | `python -m pytest tests/test_web_smoke.py tests/test_cv_reference_service.py` |
| `app/code/job_agent/web/formatting.py` | `python -m pytest tests/test_web_formatting.py tests/test_web.py` |
| `app/code/job_agent/web/runtime.py`, background launch/status code | `python -m pytest tests/test_web_runtime.py tests/test_web_smoke.py` |
| `app/code/job_agent/web/static/app.css`, templates only | `python -m pytest tests/test_web.py tests/test_web_smoke.py` |
| `app/code/job_agent/cli.py` | Run the service tests for the command plus CLI-covered tests: `python -m pytest tests/test_job_board_recipe_service.py tests/test_source_test_service.py tests/test_recipe_candidate_service.py tests/test_recipe_candidate_approval_service.py tests/test_approved_recipe_adoption_service.py` |
| `app/environment/scripts/check.ps1`, `app/environment/scripts/check-release.ps1`, `pyproject.toml`, dependency files | `python app/environment/scripts/test_handler.py --fast` and `python -m ruff check .` |
| Docs only | No automated tests required unless docs changed commands or schemas. For command docs, run the documented command against fixtures when practical. |

## Cross-Cutting Escalation

Run `python app/environment/scripts/test_handler.py --full` when a change touches more than one layer, for example service plus route plus template, or source recipe plus execution readiness.

Run `.\app\environment\scripts\check.ps1` before normal handoff. Run `.\app\environment\scripts\check-release.ps1` before release-ready handoff or when coverage and repo-state mutation protection need to be exercised together.

Run optional Playwright checks only for rendered browser diagnostics:

```powershell
pip install -r app/environment/requirements-playwright.txt
python -m playwright install chromium
python app/environment/scripts/check_playwright.py
```

## Exploratory Tests

Exploratory tests are excluded from the default suite:

```powershell
python -m pytest tests/exploratory -m exploratory
```

Use them only when the task explicitly involves saved real-source recipe probes or live-source exploration. They are not a normal product verification gate.

