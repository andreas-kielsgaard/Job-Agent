# Source Workflow State

The source setup flow is coordinated by `SourceWorkflowHandler`.
Controllers should use this handler instead of recomputing source state in templates, route handlers, or recipe flows.

## Inputs

- `source`: registry entry from `sources/source-registry.yaml`.
- `execution_entry`: daily-run projection from `sources/recruiting-sites.yaml`.
- `generation_status`: generated or approved reading-plan candidates.
- `readiness`: latest safe source-test readiness and capability checks.
- `session_status`: connected, unverified, expired, or missing source session.
- `recipe_explanation`: selected reading-plan capabilities.
- `index`: listing index state for the source.
- `detail`: detail-review coverage for indexed or historical jobs.

## Derived States

- `lifecycle`: `setup` or `implemented`.
- `status`: top-level source status, badge, blockers, and primary action.
- `setup_steps`: ordered setup guide steps and actions.
- `source_test_insight`: canonical diagnosis for failed or passed safe source tests.
- `safe_test_action`: the action shown in the Safe Source Test panel.
- `setup_complete`: true only when all setup steps are complete.

## Diagnosis Priority

1. Stale test after reading-plan change: rerun safe source test.
2. Source access/session failure: connect or refresh a session only when no usable session exists; otherwise rerun the safe source test with the connected session.
3. Pagination failure: rebuild reading plan with pagination evidence.
4. Other failed capability: review the failing capability.
5. No failures: source test passed.

## Controller Rule

Any controller that adds a source, prepares execution, tests a source, verifies a session, rebuilds a recipe, adopts a recipe candidate, indexes listings, or investigates details should either:

- build a `SourceWorkflowState`, or
- call a compatibility wrapper backed by `SourceWorkflowHandler`.

This keeps the top status panel, setup guide, safe-test panel, source-test result, recipe regeneration flow, and progress widgets aligned on the same next action. Session verification is not a separate setup step after testing; the safe source test is the verification mechanism.
