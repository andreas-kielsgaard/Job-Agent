# Source Registry

The source registry is the first version of a separate Sources area in the app. It treats sources as places jobs can come from, with review status, recipe health, notes, and source-specific value signals from saved packages.

This is separate from Setup:

- Setup/customization is global app and user configuration.
- Sources are job-origin objects: manual intake, local YAML files, generic HTML sources, and recipe-backed experiments.

## Registry File

The registry lives at:

```text
sources/source-registry.yaml
```

It is a review/configuration layer. It does not replace `sources/recruiting-sites.yaml`, and daily runs still use the existing daily-run source config.

Each entry can include:

- `id`
- `name`
- `kind`
- `status`
- `url`
- `recipe_path`
- `added_at`
- `enabled`
- `notes`
- `tags`

## Current Seeded Sources

- Manual Intake
- Sample Jobs
- Eursap Jobs experimental recipe
- Whitehall Resources SAP Contract Jobs experimental recipe
- Montreal Associates experimental recipe

Eursap and Whitehall are marked as live-calibrated experimental recipe sources based on saved local calibration artifacts. Montreal remains partial/rendered experimental because the broad result set includes non-SAP roles.

## Preview Boundary

Recipe preview is the manual trust gate before any later source integration. A recipe-backed source can link to `/recipe-preview`, but this does not enable the source in daily runs.

## Recipe-Backed Execution

Daily runs now have an opt-in `recipe_html` source type in the existing `sources/recruiting-sites.yaml` execution config. This is a technical bridge only; no experimental recipe sources are enabled by default, and the source registry is still not the daily-run source of truth.

Example shape:

```yaml
sources:
  - name: Eursap Jobs
    source_id: eursap-jobs
    type: recipe_html
    url: https://eursap.eu/jobs
    recipe_path: sources/recipes/experimental/eursap-jobs.yaml
    enabled: false
```

Use recipe preview and saved source health before manually enabling a recipe-backed source. When a `recipe_html` source is eventually enabled in `sources/recruiting-sites.yaml`, it loads the configured constrained YAML recipe, extracts jobs from the configured single URL, and returns normal jobs to the existing daily-run pipeline. It does not add pagination or broader site traversal.

Recipe-backed jobs can carry `source_id` into package indexes. Future registry-to-execution work should use this stable id instead of relying on source names or URLs.

## Source Health

Source health is stored separately at:

```text
sources/source-health.yaml
```

Health records are created only when a recipe preview/test is explicitly associated with a source id. For example:

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/eursap-jobs.yaml output/recipe-calibration/20260508-081750-eursap-eu-jobs/page.html --base-url https://eursap.eu/jobs --source-id eursap-jobs
```

The web recipe preview page can also save health when opened from a source detail page.

Health answers: "Does extraction appear to work?" It is based on the latest manual preview/test result:

- last preview time
- input used
- mode used
- extracted job count
- useful titles
- generic labels
- unique URLs
- average description length
- warnings

Health does not answer: "Is this source valuable?" Source value/performance is kept separate and uses historical match/application outcomes from saved package indexes.

Health statuses are intentionally simple:

- `untested`: no saved preview result
- `good`: jobs were extracted, titles were useful, and URLs looked distinct
- `warning`: jobs were extracted but warnings, generic labels, or duplicate/weak URL signals appeared
- `failing`: preview failed or extracted no jobs

Saving source health does not enable a source and does not change daily-run behavior.

## Source Value

Source value answers: "Has this source produced useful jobs?" It is derived from package indexes already written under `output/` plus application review status where available.

The Sources area shows:

- total saved jobs matched to the source
- strong, exploratory, weak, and excluded match counts
- applied, not interesting, and unreviewed counts
- average and best match score
- best recent match title/link when available
- last seen run id
- a simple value status

Value statuses are advisory:

- `no_data`: no saved packages matched this source yet
- `promising`: at least one strong/exploratory match, or a clearly high score
- `mixed`: jobs exist, but the signal is not clearly strong or clearly low value
- `low_value`: jobs exist, but they are mostly weak, excluded, or marked not interesting

For experimental recipe sources that are not connected to daily runs, `no_data` is expected. They can have good extraction health from preview while still having no source value history.

Matching is intentionally conservative. It uses stable `source_id` when package indexes provide it, then exact source names where available, manual-intake labels for manual postings, and matching source URL domains/paths. Older package indexes may still rely on inferred source identity.

Source value does not enable a source and does not change daily-run behavior.

## Not Implemented Yet

- Registry-to-daily-run enablement
- Automatic recipe enabling
- Recruiter/contact tracking
- Pagination or broader site traversal
- Application submission or form filling
