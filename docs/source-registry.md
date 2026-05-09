# Source Registry

The source registry is the first version of a separate Sources area in the app. It treats sources as places jobs can come from, with review status, recipe health, notes, and eventually source-specific performance.

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

## Not Implemented Yet

- Recipe-backed daily-run execution
- Automatic recipe enabling
- Historical source-quality scoring beyond rough package-derived placeholders
- Recruiter/contact tracking
- Pagination or broader site traversal
- Application submission or form filling
