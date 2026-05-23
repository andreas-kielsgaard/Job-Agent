# Codebase Size Justification

This project is about 28.5k tracked lines across application code, tests, docs, fixtures, recipes, and configuration. The size is larger than a small script because the app is no longer only "fetch jobs and write files"; it now includes source setup, recipe generation and review, guarded source execution, progress/debug views, package generation, scoring, and persistence.

## Current Shape

Approximate tracked line counts at the time of this report:

| Area | Files | Lines | Why it exists |
| --- | ---: | ---: | --- |
| `job_agent/` | 105 | 19.3k | Runtime app, services, web UI, adapters, CLI, scoring, generation, persistence. |
| `tests/` | 59 | 7.5k | Regression coverage for recipe extraction, source workflow, daily runs, UI routes, and generation flows. |
| `sources/` | 9 | 0.5k | Seed source registry and recipe YAML. |
| `docs/` and root docs | 8 | 0.7k | Operating model, workflow boundaries, and maintenance notes. |
| Sample/profile/prompt/script files | 13 | 0.4k | Default user profile, prompts, sample jobs, and helper script. |

Within `job_agent/`, the biggest areas are:

| Area | Lines | Justification |
| --- | ---: | --- |
| `job_agent/services/` | 8.4k | The domain layer: recipe parsing/extraction, source registry, source tests, generation runs, candidates, health/readiness, single-source runs, materials, stats, and storage adapters. This is where most rules live so web routes and CLI commands remain thin. |
| `job_agent/web/` | 7.3k | HTML templates, routers, and view models for setup, sources, previews, generated plans, runs, jobs, and review screens. The app has become interactive enough that explicit UI state is preferable to hidden command-line behavior. |
| `job_agent/cli.py` | 0.9k | Backward-compatible operational commands. This is large enough to be a future split candidate, but it is still mostly command wiring. |
| `job_agent/sources.py` | 0.6k | Adapter bridge for manual/local/API/recipe sources. This should stay contained because ethical access and source-specific behavior need one obvious boundary. |

## Why The Quantity Is Mostly Earned

The app has several real domains that cannot safely collapse into one script:

- Job discovery and execution: sources can be manual, local, API-like, generic HTML, or recipe-backed, and daily runs must handle enabled state, seen state, run records, packages, and warnings.
- Ethical source access: recipe previews, source tests, and daily runs intentionally have different limits and side effects. Keeping those boundaries explicit costs code, but prevents accidentally turning review actions into bulk scraping.
- Recipe generation and review: generating a reading plan is not the same as trusting it. Candidate storage, approval, adoption, source health, and source-test readiness are separate because each answers a different user question.
- Extraction observability: compatibility checks, recipe previews, source tests, and daily runs need explainable steps, count explanations, detail-page evidence, pagination proof, field coverage, and debug records.
- User-facing workflow: the Sources area now guides a human from "I added a site" through "the app can read it" to "include it in daily checks." That requires view models, templates, route state, and guardrails.
- Regression safety: the test suite is intentionally substantial because source lifecycle behavior is easy to regress. The 7.5k test lines are a healthy fraction for this type of app.

## Where The Size Is Still Carrying Debt

Some of the quantity is transitional and should keep being reduced:

- `source_execution_readiness_service.py` still uses persisted `dry_run_*` field names for compatibility. User-facing language now says "source test", but storage migration can eventually retire the old names.
- Compatibility routes and CLI still keep `dry-run-source` and `/dry-run` aliases so old links/scripts do not break. They should remain thin compatibility wrappers only.
- `job_agent/cli.py` is becoming a command hub. Splitting command groups into modules would make future cleanup easier.
- `tests/test_web_smoke.py` is broad. It catches useful integration regressions, but some scenarios should move into narrower route/service tests over time.
- Web templates and services have been improved, but the source workflow still has several adjacent screens. Further consolidation should focus on fewer user-facing steps, not just fewer files.

## Conclusion

The codebase size is justified by the app's current responsibility: it is an end-to-end job-search assistant with guarded automation, explainable extraction, recipe learning, review workflows, and persistent run/package outputs. The right cleanup direction is not to shrink it into a scraper-shaped script, but to keep narrowing duplicate concepts, preserve clear service boundaries, and retire compatibility aliases once the newer source-test and registry-owned execution model has fully settled.
