# Agent Information Map

Use this map to find the smallest useful reading set before changing code. Start with the row that matches the task, then follow local imports and tests with `rg`.

## Global Orientation

| Need | Read First | Then Inspect |
| --- | --- | --- |
| Product intent and supported behavior | `README.md` | `docs/how-it-works.md`, `SECURITY.md` |
| Current app workflow boundaries | `docs/app-workflow-map.md` | `app/code/job_agent/web/workflows.py`, `app/code/job_agent/web/source_workflow.py` |
| Why the codebase is split this way | `docs/codebase-size-justification.md` | `app/environment/scripts/code_usage_map.py` |
| Full static usage scan | `python app/environment/scripts/code_usage_map.py --top 30` | Add `--include-tests` when comparing test-only usage |

## Product Areas

| Task Area | Read First | Primary Code | Notes |
| --- | --- | --- | --- |
| Daily run pipeline | `docs/how-it-works.md`, README Architecture | `app/code/job_agent/run_service.py`, `app/code/job_agent/run_store.py`, `app/code/job_agent/digest.py` | Daily runs write normal output and may update seen state depending on options. |
| Source ingestion | `README.md` Source Strategy, `docs/source-registry.md` | `app/code/job_agent/sources.py`, `app/code/job_agent/services/execution_source_service.py`, `user/sources/recruiting-sites.yaml` | Keep adapters bounded and explicit. Generic extraction should warn rather than invent jobs. |
| Source registry and value | `docs/source-registry.md` | `app/code/job_agent/services/source_registry_service.py`, `app/code/job_agent/services/source_health_service.py`, `user/sources/source-registry.yaml` | Registry owns recipe-backed source state; execution config is a projection bridge. |
| Source workflow UI | `docs/app-workflow-map.md`, `docs/source-workflow-state.md` | `app/code/job_agent/web/source_workflow.py`, `app/code/job_agent/web/workflows.py`, `app/code/job_agent/web/routers/sources.py` | Source status, setup steps, readiness, sessions, and next actions should stay aligned through workflow state. |
| Source tests and go-live readiness | `docs/source-registry.md`, `docs/recipe-generation.md` | `app/code/job_agent/services/source_test_service.py`, `app/code/job_agent/services/source_execution_readiness_service.py` | Source tests must not write packages, materials, seen state, digests, or run records. |
| Recipe extraction | `README.md` Job-Board Extraction Recipes, `docs/recipe-preview.md` | `app/code/job_agent/services/job_board_recipe_service.py`, `app/code/job_agent/services/recipes/`, `sources/recipes/` | Recipes are constrained YAML. Preserve single-input, bounded extraction behavior. |
| Recipe calibration | `docs/recipe-calibration.md` | `app/code/job_agent/services/recipe_calibration_service.py`, `app/code/job_agent/browser/playwright_probe.py` | Calibration saves local evidence only. It does not generate, edit, or enable recipes. |
| Recipe generation lifecycle | `docs/recipe-generation.md` | `app/code/job_agent/services/recipe_generation_run_service.py`, `recipe_suggestion_service.py`, `recipe_candidate_service.py`, `recipe_candidate_approval_service.py`, `approved_recipe_adoption_service.py` | Generation, approval, adoption, readiness, and enablement are separate trust gates. |
| Matching and scoring | `docs/matching-and-ai-review.md` | `app/code/job_agent/scoring.py`, `app/code/job_agent/highlights.py`, `app/code/job_agent/web/view_models/match_sandbox.py` | Deterministic scoring drives category and match result. AI review is advisory. |
| AI search review | `docs/matching-and-ai-review.md` | `app/code/job_agent/services/ai_search_service.py`, `app/resources/prompts/evaluate_job_relevance.md`, `app/resources/prompts/score_job_assist.md` | Excluded jobs should stay skipped unless policy explicitly allows review triggers. |
| Material generation | README Optional Claude Setup | `app/code/job_agent/generator.py`, `app/code/job_agent/services/material_service.py`, `app/resources/templates/`, `app/resources/prompts/generate_application.md`, `app/resources/prompts/generate_form_answers.md` | Recruiter-facing output should not expose internal scores or implementation language. |
| Human application examples | `docs/profile-setup.md`, `docs/matching-and-ai-review.md` | `app/code/job_agent/services/application_examples_service.py`, `setup/defaults/profile/application-examples.yaml` | Examples feed application prompts, AI edit context, and review bundles. |
| Profile and setup | `docs/profile-setup.md` | `app/code/job_agent/config.py`, `app/code/job_agent/profile_contract.py`, `app/code/job_agent/services/setup_service.py`, `app/code/job_agent/web/routers/setup.py` | Prefer preserving unrelated YAML and keeping public sample data neutral. |
| CV reference upload and drafts | `docs/profile-setup.md` | `app/code/job_agent/services/cv_reference_service.py`, `app/code/job_agent/services/cv_profile_draft_service.py`, setup routes/templates | Uploaded real CV files belong under ignored `user/uploads/cv/`. |
| Web app shell and routes | README Local Web UI, `docs/app-workflow-map.md` | `app/code/job_agent/web/app.py`, `app/code/job_agent/web/routers/`, `app/code/job_agent/web/templates/`, `app/code/job_agent/web/view_models/` | Keep app creation simple and routers thin. Put shared decisions in services/workflows. |
| Web runtime/background work | README Local Web UI | `app/code/job_agent/web/runtime.py`, `app/code/job_agent/web/routers/health.py`, `app/code/job_agent/web/debug_state.py` | Runtime work should report status through `/api/work-status` and avoid hidden repo-state mutation in tests. |
| Local persistence | README Private Data | `app/code/job_agent/io/`, `app/code/job_agent/store.py`, `app/code/job_agent/run_store.py`, `app/code/job_agent/application_status_store.py`, `app/code/job_agent/token_usage.py` | Use atomic helpers and preserve strict corruption behavior where tests cover it. |
| LLM gateway | README Optional Claude Setup | `app/code/job_agent/llm/`, `app/code/job_agent/services/ai_edit_service.py` | Tests fake LLM calls. Do not require real API keys for product tests. |
| Browser diagnostics | `docs/playwright-setup.md`, `docs/recipe-calibration.md` | `app/code/job_agent/browser/playwright_probe.py`, `app/environment/scripts/check_playwright.py` | Playwright is optional and not required for normal daily runs. |
| CLI commands | README command sections | `app/code/job_agent/cli.py` plus the service it wires | Keep commands thin and rooted so tests can pass temp project roots. |

## Common Search Patterns

| Question | Command |
| --- | --- |
| Which tests mention a service? | `rg "ServiceName|function_name" tests` |
| Which routes render a template? | `rg "TemplateResponse|template_name.html" app/code/job_agent/web` |
| Which code writes a file? | `rg "write_json|write_yaml|atomic_write|write_text|write_bytes" app/code/job_agent` |
| Which code touches source execution? | `rg "ExecutionSourceService|source-go-live|enable_source|recruiting-sites" app/code/job_agent tests docs` |
| Which code depends on a prompt or template? | `rg "prompt_name|template_name" app/code/job_agent tests app/resources/prompts app/resources/templates` |

## State And Side Effects

| File/Folder | Meaning | Agent Caution |
| --- | --- | --- |
| `setup/defaults/profile/` | Public neutral sample profile | Safe to edit when changing setup defaults or sample schema. |
| `user/profile/` | Private real profile | Ignored. Do not read or write unless explicitly asked. |
| `user/sources/source-registry.yaml` | Source review and selected recipe owner | Mutating this changes source setup state. Tests should use temp roots. |
| `user/sources/recruiting-sites.yaml` | Daily-run execution config | Viewing pages should not change this. Enablement is explicit and guarded. |
| `user/sources/source-health.yaml` | Saved recipe preview health | Health is not execution readiness. |
| `user/sources/source-execution-readiness.yaml` | Saved source-test readiness | Readiness writes metadata only, not run outputs. |
| `runtime/output/` | Generated packages, digests, logs, calibration artifacts | Ignored. Product tests should not mutate repo output. |
| `runtime/jobs/seen_jobs.json` | Seen-state store | Ignored. Source tests must not update it. |
| `runtime/jobs/application_status.json` | User review/application statuses | Ignored. Treat as private local state. |

## Dependency Hints

- Core install: `pip install -r app/environment/requirements.txt`
- Dev checks: `pip install -r app/environment/requirements-dev.txt`
- Optional rendered diagnostics: `pip install -r app/environment/requirements-playwright.txt` then `python -m playwright install chromium`
- The default `pytest` config excludes `tests/exploratory/`.

