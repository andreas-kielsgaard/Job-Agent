# App Workflow Map

The web app coordinates long-running setup and execution through `AppWorkflowHandler`.
Routes should use this handler as the controller boundary and avoid rebuilding cross-flow state from individual services.

## Areas

- `ProfileWorkflowHandler`: owns profile setup view state, CV reference state, profile draft state, and profile auto-configuration actions.
- `SourceWorkflowHandler`: owns source lifecycle state, source overview cards, setup steps, session status, source-test execution, indexing readiness, detail-review launch, and daily-run projection handoffs.
- `RecipeWorkflowHandler`: owns recipe generation runs, source-test clues passed into recipe regeneration, calibration capture, and candidate approval/adoption/rejection.
- `ExecutorWorkflowHandler`: owns dashboard/run views, daily-run launch, run status payloads, and run logs.

## Required Handoffs

- Profile to executor: daily runs and match scoring depend on the active profile contract.
- Profile to recipe: generated reading plans and source verification should use profile context only through explicit workflow evidence, not hidden route state.
- Source to recipe: failed source tests pass canonical `source_test_insight.generation_clues` into recipe regeneration.
- Recipe to source: approved/adopted candidates update the selected source reading plan and daily-run projection through source workflow state.
- Source to executor: indexing, detail review, source runs, and daily-run inclusion must pass through source readiness checks.

## Controller Rule

Routes can format HTTP responses, redirects, and templates. Decisions about source readiness, session verification, recipe-generation clues, profile draft state, or run state belong in one of the workflow handlers. This keeps setup guide steps, widgets, source detail pages, recipe regeneration, and executor pages aligned on the same state.
